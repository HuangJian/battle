"""common/node_report.py — 节点侧观测（磁盘阈值 / 水位行 / 超额认购判据）+ 同一 node_id 的归属卫兵。

为什么在这里（2026-11-14，plan/node-throughput-and-keepalive P3；LOC 预算 <1000 行）：
这些是「把 `/v1/status` 的字段变成一行人人能读的日志」的纯逻辑，与传输/监护无关；
住在 `remote/rollout_node.py` 会把那个文件顶过预算，也不利于单测（不用起监护循环）。

2026-11-18 追加 `reclaim_node_id`（同一 `node_id` 只许一个节点在轮询，cell 重跑**先清场**）：
它同样属于「节点侧自己的记账」，但**不是纯函数**（会递信号）—— 放在这里仍是因为
`rollout_node.py` 顶在 1000 行预算上，而不是因为它够纯。

口径（AGENTS §9 观测面）：
* 阈值一律用**生效值**（CFG 覆盖后的 `diskFloorMB/diskWarnMB`），不写死常量；
* `free` 不可知时如实说不可知，不编数字；
* 告警靠**档位切换**触发（首拍只记基线），否则每次重启都弹一行。
"""

from __future__ import annotations

import os
import re
import signal
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from common.distribution_limits import node_slots, node_slots_note
from common.instance_lock import proc_cmdline
from common.pid_probe import pid_alive
from common.proc import run_capture

DISK_FLOOR_MB_DEFAULT = 2048
DISK_WARN_MB_DEFAULT = 4096
DISK_HYST_MB = 512
DISK_FLOOR_MIN, DISK_FLOOR_MAX = 512, 65536
#: 超额认购告警的节流（与产出摘要同一个 10 分钟节拍）。
LATENCY_WARN_EVERY_SEC = 600.0


def node_online(nid: str, node: dict, ping: dict) -> tuple[int, str]:
    """在线节点的一行：生效槽位（`node_slots`）+ config/workers/cpus（超额派工提示）。

    槽位口径与派工共用同一个函数（日志里数与真派出去的数不可能不一致）。
    """
    slots = (node.get("concurrency"), ping.get("cpus"), ping.get("workers"))
    c_n = node_slots(*slots)
    return c_n, (
        f"[dist] node {nid}: online, concurrency={c_n}"
        f"{node_slots_note(*slots)}{admission_note(ping)}"
    )


def admission_note(ping: dict) -> str:
    """`/v1/ping` 的准入计数（2026-11-15）：池被压时的排队/超时**事实**。

    旧 agent 无 `admission` 字段 ⇒ 空串（不编数字，也不每轮刷 8 行「不可知」）。
    `active/workers` 是瞬时值；`peakWaiting/queueFull/timedOut` 是进程累计值
    （相邻轮相减 = 本轮新增；503 归因靠的就是这个增量）。
    """
    adm = ping.get("admission")
    if not isinstance(adm, dict):
        return ""

    def num(key: str) -> str:
        v = adm.get(key)
        return str(v) if isinstance(v, int) and not isinstance(v, bool) else "?"

    active = num("active")
    workers = ping.get("workers")
    if active != "?" and isinstance(workers, int):
        active = f"{active}/{workers}"
    return (
        f" · 准入 active={active} waiting={num('waiting')}"
        f" peakWaiting={num('peakWaiting')} queueFull={num('queueFull')}"
        f" timedOut={num('timedOut')}"
    )


def disk_thresholds(cfg: dict) -> tuple[int, int]:
    """`CFG.disk_floor_mb` / `disk_warn_mb` → 生效阈值（默认 2048 / 4096）。

    只给 floor 不填 warn ⇒ `max(floor + 512, floor × 2)`（floor=2048 时正好是缺省的 4096）。
    """
    raw_floor = cfg.get("disk_floor_mb")
    raw_warn = cfg.get("disk_warn_mb")
    floor = int(raw_floor) if raw_floor not in (None, "") else DISK_FLOOR_MB_DEFAULT
    warn = int(raw_warn) if raw_warn not in (None, "") else max(floor + DISK_HYST_MB, floor * 2)
    return floor, warn


def validate_disk_thresholds(floor: int, warn: int) -> str:
    """范围校验（返回空串 = 合法；非空 = 人类可读的 FATAL 理由）。**不静默夹取**。"""
    if not (DISK_FLOOR_MIN <= floor <= DISK_FLOOR_MAX):
        return f"disk_floor_mb 需在 [{DISK_FLOOR_MIN}, {DISK_FLOOR_MAX}]，收到 {floor}"
    if warn < floor + DISK_HYST_MB:
        return f"disk_warn_mb 需 ≥ floor+{DISK_HYST_MB}（floor={floor}），收到 {warn}"
    return ""


def disk_line(status: dict | None) -> str:
    """磁盘水位一行（字段源 = agent `diskReport()`，ping/status 同源同拍）。"""
    if not isinstance(status, dict):
        return "磁盘 free=不可知（agent 未应答）"
    free = status.get("diskFreeMB")
    if not isinstance(free, int):
        return "磁盘 free=不可知（旧 agent 无磁盘字段）"
    floor = status.get("diskFloorMB")
    if not isinstance(floor, int):
        return f"磁盘 free={free}MB（旧 agent 无阈值字段）"
    warn = status.get("diskWarnMB")
    level = status.get("diskLevel")
    gap = free - floor
    gap_txt = f"距熔断 {gap}MB" if gap >= 0 else f"已破熔断 {-gap}MB"
    return (
        f"磁盘 free={free}MB（warn<{warn} / 熔断<{floor}）"
        f" · 档位={level if level is not None else '不可知'}"
        f" · 拒收={'是' if gap < 0 else '否'} · {gap_txt}"
    )


def startup_disk(cfg: dict) -> tuple[tuple[int, int], str]:
    """启动期一次性：生效阈值 + 校验理由（空串 = 合法）。

    校验不过 ⇒ 调用方**停下来**（`FATAL` + 非 0 返回），不静默夹取。
    """
    floor, warn = disk_thresholds(cfg)
    return (floor, warn), validate_disk_thresholds(floor, warn)


def agent_disk_args(cfg: dict) -> list[str]:
    """传给 `sampler-agent` 的磁盘阈值参数（总是显式传生效值，便于控制台对账）。"""
    floor, warn = disk_thresholds(cfg)
    return ["--disk-floor-mb", str(floor), "--disk-warn-mb", str(warn)]


def output_line(status: dict | None) -> str:
    """把 `/v1/status` 的产出摘要拼成**一行**（空/旧 agent 如实说不可知）。

    分母、窗口、分列全在 agent 侧算好（`outputLine`）——这里不重算任何数字：
    notebook 日志与 `sampler-agent.log` 必须逐字相同，否则两个日志对账时先要怀疑口径。
    """
    if not isinstance(status, dict):
        return "近30min产出 不可知（agent 未应答）"
    line = status.get("outputLine")
    if isinstance(line, str) and line.strip():
        return f"近30min产出 {line.strip()}"
    # 旧 agent（没随本仓同步升级）不能编一个「窗口」出来：累计数冒充 30 分钟窗口是静默错数。
    return "近30min产出 不可知（旧 agent 无 outputLine）"


def over_subscribed(ms: int, base_ms: int) -> bool:
    """超额认购疑点：`/v1/ping` 延迟 ≥1s 且 > 1.5× 基准（无基准只看 1s）。"""
    if ms < 1000:
        return False
    return ms > base_ms * 1.5 if base_ms > 0 else True


def note_ping_latency(ms: int, base_ms: int, state: dict, log: Callable[[str], None]) -> None:
    """超额认购即时行（靠 10 分钟节流，不逐拍刷屏）。"""
    if not over_subscribed(ms, base_ms):
        return
    now = time.monotonic()  # 节流是「隔了多久」，墙钟跳变不该让它提前/推后
    if now - float(state.get("warned_at") or 0.0) < LATENCY_WARN_EVERY_SEC:
        return
    state["warned_at"] = now
    log(
        f"★ workers 超额认购：ping 基准 {base_ms}ms → 现值 {ms}ms"
        f"（回退：CFG.workers = 物理核数 重跑 cell）"
    )


def note_disk_tick(ping: dict | None, state: dict, log: Callable[[str], None]) -> None:
    """档位切换 / 正在拒收的**即时行**（复用 60s 的 `/v1/ping`，零额外请求）。

    首拍只记基线（否则每次重启/重连都弹一行）；例外：首拍就在拒收 ⇒ 当场可见。
    旧 agent 无磁盘字段 ⇒ 静默跳过（不把「老代码」读成「磁盘坏了」）。
    """
    if not isinstance(ping, dict):
        return
    free = ping.get("diskFreeMB")
    level = ping.get("diskLevel")
    if free is None and level is None:
        return
    floor = ping.get("diskFloorMB")
    rejecting = isinstance(free, int) and isinstance(floor, int) and free < floor
    prev_level = state.get("level")
    prev_reject = bool(state.get("rejecting"))
    state["level"] = level
    state["rejecting"] = rejecting
    if prev_level is None:
        if rejecting:
            log("★ " + disk_line(ping))
        return
    if level != prev_level or rejecting != prev_reject:
        log("★ " + disk_line(ping))


def monitor_tick(
    ping: dict | None, ms: int, base_ms: int, state: dict, log: Callable[[str], None]
) -> None:
    """60s 探活后的两件事：磁盘档位即时行 + 超额认购监控（同一个 state）。"""
    note_disk_tick(ping, state, log)
    note_ping_latency(ms, base_ms, state, log)


def workers_selfcheck_line(workers: int, slot_note: str, base_ms: int) -> str:
    """启动期一行：worker 数与空载 ping 基准（1s 阈值靠它才有意义）。"""
    return (
        f"workers 自检: workers={workers}（{slot_note}） ping={base_ms}ms"
        f" — 持续 >1.5×且≥1s 即超额认购（回退：CFG.workers = 物理核数）"
    )


# ────────────────────────── 同一 node_id 的重复进程（2026-11-18 DSW）──────────────────────────

#: 收掉上一个节点进程的宽限（秒）：SIGINT 之后它还要 unregister + 收 bun agent/cloudflared，
#: 这段是给它走完 `finally` 的预算；到点还活着才升级 SIGKILL（那一步会留下孤儿子进程）。
RECLAIM_GRACE_SEC = 8.0
#: 等对方退出时的轮询间隔（秒）。
RECLAIM_POLL_SEC = 0.2


def node_id_pidfile(node_id: str) -> Path:
    """同一 `node_id` 的「谁在轮询」记在临时目录（不进仓库、不进 CFG）。"""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", node_id)[:64] or "node"
    return Path(tempfile.gettempdir()) / f"battle-rollout-{safe}.pid"


def proc_start_stamp(pid: int) -> str:
    """进程启动戳（Linux `/proc/<pid>/stat` 第 22 字段）—— 用来排除 pid 复用。

    pid 会被系统回收：光凭「pid 相同」就给对方递信号，最坏是打断一个无关的训练 / kernel。
    启动戳在同一台机器上单调、同一个 pid 不可能两次拿到同一个值。
    读不到（非 Linux / 已退出 / 无权限）⇒ 空串（`proc_identity` 会去回落命令行）。
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # comm 字段带括号且可能含空格 ⇒ 从**最后一个** `)` 之后切；切出的第 0 项是 state（第 3 字段）。
    tail = raw.rsplit(")", 1)[-1].split()
    return tail[19] if len(tail) > 19 else ""


def proc_identity(pid: int) -> str:
    """进程身份指纹（pidfile 里存的就是它）。空串 = 身份不可核验 ⇒ 调用方 fail-closed。

    Linux 取启动戳（快、不起子进程）；没有 `/proc` 的平台（Windows / macOS）回落命令行
    —— 复用 `common.instance_lock.proc_cmdline` 那唯一一份实现（Windows 走 wmic、新机回落
    PowerShell CIM；macOS 无 `/proc` ⇒ 读不到就返回空）。
    """
    stamp = proc_start_stamp(pid)
    if stamp:
        return f"stamp:{stamp}"
    line = proc_cmdline(pid)
    return f"cmd:{line}" if line else ""


def write_node_owner(path: Path, pid: int, ident: str) -> None:
    """把「谁在轮询这个 node」记账（`pid|身份`）。写不下不影响启动。"""
    try:
        path.write_text(f"{pid}|{ident}", encoding="utf-8")
    except OSError:
        pass


def read_node_owner(path: Path) -> tuple[int, str]:
    """pidfile → `(pid, 身份)`；残缺/不可读 → `(0, "")`。兼容裸 pid 旧格式（身份为空）。"""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0, ""
    pid_txt, _, ident = raw.partition("|")
    try:
        return int(pid_txt.strip() or "0"), ident.strip()
    except ValueError:
        return 0, ""


def reclaim_signal(pid: int, *, interrupt: bool) -> bool:
    """给 `pid` 递终止信号（True = 信号已发出）。

    POSIX：`interrupt` → `SIGINT`，否则 `SIGKILL`。**先 SIGINT 不是客气**：对方的
    `except KeyboardInterrupt` 会走 `finally`（unregister + 收 bun agent/cloudflared），
    而 SIGTERM/SIGKILL 没有这个待遇 —— 会留下占着 `agent_port` 的孤儿，新实例起来就用不了。
    Windows：没有可靠的 SIGINT（`CTRL_C_EVENT` 要求同控制台）⇒ 直接 `taskkill /F /T`
    （`/T` 连子进程一起；走 `run_capture` 免得无控制台的父进程闪一块黑窗）。
    """
    if os.name == "nt":
        if interrupt:
            return False
        try:
            run_capture(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=10)
        except OSError:
            return False  # taskkill 不在 PATH / 无权限：调用方会看到「还没退出」
        return True
    try:
        # `SIGKILL` 在 Windows 的 typeshed 里不存在（本分支只在 POSIX 跑，getattr 只为过静态检查）。
        os.kill(
            pid, signal.SIGINT if interrupt else int(getattr(signal, "SIGKILL", signal.SIGTERM))
        )
    except OSError:
        return False
    return True


def reclaim_kill(pid: int, log: Callable[[str], None]) -> int:
    """收掉 `pid`（先 SIGINT 等它自己收线，超时再 SIGKILL），返回真收掉的 pid（0 = 没收掉）。"""
    log(
        f"⚠ 同一 node_id 的上一个节点还活着（pid={pid}）—— 先收掉它再起新的"
        "（cell 重跑必须清场：两个消费者会抢同一个 hub 队列，日志也混成两份）"
    )
    # Windows 上「interrupt」这一步没有对应信号（返回 False）⇒ 别白等 8s，直接上 taskkill。
    waited = reclaim_signal(pid, interrupt=True)
    deadline = time.monotonic() + (RECLAIM_GRACE_SEC if waited else 0.0)
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(RECLAIM_POLL_SEC)
    hard = False
    if pid_alive(pid):
        reclaim_signal(pid, interrupt=False)
        hard = True
        time.sleep(RECLAIM_POLL_SEC)
    if pid_alive(pid):
        log(f"  pid={pid} 仍未退出 —— 它守的子进程可能已成孤儿；手动 kill 后重跑本 cell")
        return 0
    # 收线方式不同，后果不同 —— 日志不许含糊（SIGINT 走 finally 会自己收子进程，SIGKILL 不会）。
    log(
        f"  pid={pid} 已收线"
        + (
            "（SIGKILL：它守的 bun agent / cloudflared 可能已成孤儿，留意 agent_port 占用）"
            if hard
            else "（SIGINT 走完 finally，bun agent / cloudflared 已随它收掉）"
        )
    )
    return pid


def reclaim_node_id(node_id: str, log: Callable[[str], None]) -> int:
    """**先清场**：收掉同一 `node_id` 上遗留的上一个节点进程，再把本进程登记为 owner。

    返回被收掉的 pid（`0` = 没有遗留 / 没法确认身份）。用户口径（2026-11-18）：重复执行
    rollout cell 必须杀干净上一次的进程再起新的 —— 上一次实测（DSW）旧进程没随 notebook
    重启退出，两个进程同时轮询同一个 node，日志里新旧两种格式交替出现，看起来像「代码
    没生效」，实际是两份进程在说话。

    **身份核验**：pidfile 记 `pid|身份`（Linux 启动戳 / 其它平台命令行，见 `proc_identity`）。
    对不上就不动（pid 被系统复用给了别人 —— 给陌生进程递 SIGINT 最坏能打断一个无关的
    kernel / 训练）。旧格式裸 pid（身份为空）同样只告警不动：没有身份就不该有信号；
    本次启动会把它重写成新格式，下一次重跑即生效。

    判活一律用 `common.pid_probe.pid_alive`（唯一实现）：`os.kill(pid, 0)` 在 Windows 上是
    `TerminateProcess`，会真把对方打死 —— `tests/common/test_pid_probe_windows_safe.py` 守着它。
    """
    path = node_id_pidfile(node_id)
    me = os.getpid()
    other, stored = read_node_owner(path)
    killed = 0
    if other and other != me and pid_alive(other):
        now = proc_identity(other)
        if stored and now and stored == now:
            killed = reclaim_kill(other, log)
        else:
            log(
                f"⚠ 同一 node_id 的 pidfile 指向 pid={other}，但身份核验不过"
                f"（存={stored or '空'} 现={now or '读不到'}）—— 判为 pid 复用，不动它"
                + ("；旧格式 pidfile 无从核验，本次请手动 kill 后重跑" if not stored else "")
            )
    write_node_owner(path, me, proc_identity(me))
    return killed
