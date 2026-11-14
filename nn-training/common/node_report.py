"""common/node_report.py — 节点侧观测的纯函数（磁盘阈值 / 水位行 / 超额认购判据）。

为什么在这里（2026-11-14，plan/node-throughput-and-keepalive P3；LOC 预算 <1000 行）：
这些是「把 `/v1/status` 的字段变成一行人人能读的日志」的纯逻辑，与传输/监护无关；
住在 `remote/rollout_node.py` 会把那个文件顶过预算，也不利于单测（不用起监护循环）。

口径（AGENTS §9 观测面）：
* 阈值一律用**生效值**（CFG 覆盖后的 `diskFloorMB/diskWarnMB`），不写死常量；
* `free` 不可知时如实说不可知，不编数字；
* 告警靠**档位切换**触发（首拍只记基线），否则每次重启都弹一行。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from common.distribution_limits import node_slots, node_slots_note

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
    return c_n, f"[dist] node {nid}: online, concurrency={c_n}" + node_slots_note(*slots)


def disk_thresholds(cfg: dict) -> tuple[int, int]:
    """`CFG.disk_floor_mb` / `disk_warn_mb` → 生效阈值（默认 2048 / 4096）。

    只给 floor 不填 warn ⇒ `max(floor + 512, floor × 2)`（floor=2048 时正好是缺省的 4096）。
    """
    raw_floor = cfg.get("disk_floor_mb")
    raw_warn = cfg.get("disk_warn_mb")
    floor = int(raw_floor) if raw_floor not in (None, "") else DISK_FLOOR_MB_DEFAULT
    warn = (
        int(raw_warn)
        if raw_warn not in (None, "")
        else max(floor + DISK_HYST_MB, floor * 2)
    )
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
    now = time.time()
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
