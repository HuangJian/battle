"""remote/lifecycle.py — worker 生命周期上发腿（2026-10-31，纯观测、失败即丢）。

**动机（kaggle 事故 2026-10-31）**：那台 2×T4 的 notebook 在 `09:51:42` 领走 job 之后 18 秒
被整机收走——云侧日志里**一个字都没有**（不是崩溃 traceback：是进程连收尸的机会都没有），
本机这边当时也看不到（notebook 日志由浏览器脚本抓 cell 输出上报，人不在电脑前就一条不传）。
事后只能靠 hub 的请求 cadence 反推死亡时刻。

本模块把「云机自己知道、而 hub 不知道」的四件事补上一条记录：

| 事件 | 何时 | 为什么值得一条 |
|---|---|---|
| 启动 | `worker_loop` 起来（身份/设备已解析） | 「这台机器当时是什么配置/显存/磁盘」是事后唯一能对账的起点 |
| 读点 | 每 `reading_sec`（缺省 15min，只在 job 边界发） | 被杀前最后一次读数 = 有没有 OOM / 磁盘满的前兆 |
| SIGTERM/SIGINT | 收到信号（Kaggle「停止会话」/ ctrl-C） | 区分「有序收工」与「被 SIGKILL」——**没有这条就是后者** |
| 收线 | 循环退出（空闲超时 / once / hub 停算 / 异常） | 收线原因 + 处理了几个 job |

## 三条硬约束（与 `remote/error_report.py` 同规）

1. **不阻塞训练**：读点走 daemon 线程（`report_error`）；只有**启动/收线/信号**用同步发送
   （≤2s，一个会话各一次）——因为进程马上就要没了，异步线程活不到发完；
2. **失败即丢**：连不上/超时/非 2xx 一律吞掉、不重试、不抛（观测面坏掉不该拖垮训练）；
3. **不进任何判据**：本模块只往 hub 的 `/admin/worker-error` ring 里塞一条记录
   （控制台 `/admin/errors` 读面呈现），不碰派发/租约/游标。

⚠ **不是唯一现场**：hub 侧还有一条独立的「静默哨兵」（`hub/worker_pulse.py`）。两条腿刻意
不共享判据——本模块证明「它自己说了什么」，哨兵证明「它什么时候不再说话」。SIGKILL 时前者
必然沉默，只剩后者。
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote.error_report import error_payload, post_error, report_error

#: 上报体里的 `component`：控制台按它把「云机自报的生命周期」与 `ppo-worker` 的失败分开归类。
LIFECYCLE_COMPONENT = "worker-lifecycle"
#: 读点间隔（秒）：只做「被杀前最后一眼」，15min 足够——它不是监控，是遗言。
LIFECYCLE_READING_SEC = 900.0


def _rss_gb() -> float | None:
    """本进程 RSS（GB）；Linux 读 `/proc/self/status` 的 `VmRSS`，其它平台返回 None（不编数）。

    为什么不用 `os.sysconf`：它在 Windows 上**不存在**（typeshed 也按平台分开声明），
    而 `getattr(os, "sysconf")` 会被 ruff B009 判成「和直接取属性一样」，两边打架。
    `/proc/self/status` 是纯文本，一行 `VmRSS:  <kB>`，没有平台分支。
    """
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024.0**2
    except Exception:
        pass
    return None


def readings(work_dir: Path | str, device: str = "") -> dict[str, Any]:
    """结构化读数：`{"device", "gpu":[{"index","free_gb","total_gb"}], "disk":[…], "rss_gb"}`。

    单独留一份结构化形态（而不是只产字符串）：用例判的是**有没有读到 / 读到几个**，不是
    「日志措辞对不对」——措辞那层在 `readings_line()`，它只有一个读者（`detail`）。

    torch **只读 `sys.modules`**、绝不在本模块 import：启动那一刻（还没领过 job）就不该为
    一条观测付 2s 的 import 代价，而那时显存本来也还没有占用——读不到就记 `[]`。
    """
    out: dict[str, Any] = {
        "device": device,
        "torch_loaded": sys.modules.get("torch") is not None,
        "gpu": [],
        "disk": [],
        "rss_gb": None,
    }
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                for i in range(int(torch.cuda.device_count())):
                    free, total = torch.cuda.mem_get_info(i)
                    out["gpu"].append(
                        {"index": i, "free_gb": round(free / 1024.0**3, 1),
                         "total_gb": round(total / 1024.0**3, 1)}
                    )
        except Exception:  # 驱动/NVML 抽风不该让一条读数变成异常
            pass
    seen: set[Any] = set()
    for label, path in (("work", Path(work_dir)), ("kaggle-working", Path("/kaggle/working"))):
        try:
            if not path.exists():
                continue
            dev = os.stat(str(path)).st_dev  # 同一挂载点只报一次（Kaggle 上 /tmp 与 /kaggle 常不同盘）
            if dev in seen:
                continue
            seen.add(dev)
            usage = shutil.disk_usage(str(path))
            out["disk"].append(
                {"label": label, "free_gb": round(usage.free / 1024.0**3, 1),
                 "total_gb": round(usage.total / 1024.0**3, 1)}
            )
        except Exception:
            continue
    rss = _rss_gb()
    if rss is not None:
        out["rss_gb"] = round(rss, 1)
    return out


def readings_line(work_dir: Path | str, device: str = "") -> str:
    """一句话读数（`detail` 的那一段）：显存 / 磁盘 / RSS / device。"""
    r = readings(work_dir, device)
    parts = [f"cuda{g['index']} 空闲 {g['free_gb']}GB/{g['total_gb']}GB" for g in r["gpu"]]
    if not r["gpu"]:
        parts.append("显存=未加载" if not r["torch_loaded"] else "显存=n/a（无 CUDA）")
    parts += [f"磁盘 {d['label']} 空闲 {d['free_gb']}GB/{d['total_gb']}GB" for d in r["disk"]]
    if r["rss_gb"] is not None:
        parts.append(f"rss {r['rss_gb']}GB")
    if device:
        parts.append(f"device={device}")
    return " · ".join(parts)


class Lifecycle:
    """一个 worker 会话的生命周期上报器（一次构造、四个调用点，见模块 docstring 的表）。

    调用点（`remote/worker.py`）：`start()` → 每轮 `tick()` → 退出路径 `why()` → `finally` 里
    `close(done)`。构造它**不发任何请求**（无 hub 地址/无 token 时全静默——本地预演、
    单测、离线无 hub 的场景本来就该没有这条腿）。
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        worker_id: str,
        *,
        work_dir: Path | str,
        device: str = "",
        role: str = "",
        once: bool = False,
        reading_sec: float = LIFECYCLE_READING_SEC,
        log: Callable[[str], None] | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._base = base_url
        self._token = token
        self._wid = worker_id
        self._work_dir = Path(work_dir)
        self._device = device
        self._role = role
        self._once = once
        self._reading_sec = float(reading_sec)
        self._log = log
        self._now = now_fn
        self._last_reading = 0.0
        self._why = ""
        self._sig: int = 0
        self._prev_handlers: dict[int, Any] = {}

    # ---- 上报 ----
    def _send(self, reason: str, *, detail: str = "", severity: str = "warn", block: bool) -> bool:
        if not self._base or not self._token:
            return False
        if block:
            payload = error_payload(
                worker=self._wid,
                reason=reason,
                component=LIFECYCLE_COMPONENT,
                detail=detail,
                severity=severity,
            )
            ok = post_error(self._base, self._token, payload)
            if not ok and self._log is not None:
                self._log(f"生命周期上报未送达（观测腿，失败即丢）：{reason[:80]}")
            return ok
        report_error(
            self._base,
            self._token,
            worker_id=self._wid,
            reason=reason,
            component=LIFECYCLE_COMPONENT,
            detail=detail,
            severity=severity,
            log=self._log,
        )
        return True

    def _detail(self, extra: str = "") -> str:
        bits = [
            f"role={self._role or '-'}",
            f"once={self._once}",
            f"工作目录={self._work_dir}",
            readings_line(self._work_dir, self._device),
        ]
        if extra:
            bits.append(extra)
        return " · ".join(bits)

    # ---- 四个调用点 ----
    def start(self) -> None:
        """装信号 + 报「启动」（同步：会话开始时 hub 不在也能立刻知道，且不留悬空线程）。"""
        self._install_signals()
        self._last_reading = self._now()
        self._send("worker 启动", detail=self._detail(), block=True)

    def why(self, reason: str) -> None:
        """记下退出原因（`break`/`return` 之前调；后写的覆盖先写的）。"""
        self._why = reason

    def tick(self) -> None:
        """轮到 job 边界时调一次：到点才发读点（异步、失败即丢，绝不占关键路径）。"""
        if self._reading_sec <= 0:
            return
        now = self._now()
        if now - self._last_reading < self._reading_sec:
            return
        self._last_reading = now
        self._send("读点：显存/磁盘", detail=self._detail(), block=False)

    def close(self, done: int = 0) -> None:
        """报「收线」（同步）：原因 + 处理数 + 最后一次读数。每条退出路径都会经过它。"""
        self._restore_signals()
        why = self._why or "未标注（异常/中断/热替换）"
        self._send(
            f"worker 收线（{why}）",
            detail=self._detail(f"done={done}"),
            block=True,
        )

    # ---- 信号（SIGTERM/SIGINT：有序收工 vs 被 SIGKILL 的分界）----
    def _install_signals(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError, AttributeError):
                # 非主线程 / 平台不支持 ⇒ 静默降级（观测腿不该改变进程的信号语义）
                self._prev_handlers.pop(sig, None)

    def _restore_signals(self) -> None:
        for sig, handler in list(self._prev_handlers.items()):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, AttributeError):
                pass
        self._prev_handlers.clear()

    def _on_signal(self, signum: int, frame: Any) -> None:
        if self._sig:
            return  # 第二发不重复上报
        self._sig = signum
        name = getattr(signal.Signals(signum), "name", str(signum))
        self._why = f"收到 {name}"
        # ★ 同步发：进程马上就要没了，daemon 线程活不到发完（这是本模块唯一必须阻塞的场合）
        self._send(f"worker 收到 {name}（开始收工）", detail=self._detail(), severity="err", block=True)
        if signum == getattr(signal, "SIGINT", None):
            # 交回 Python 的默认处理 ⇒ KeyboardInterrupt ⇒ 既有 finally（回传收尾）照跑
            signal.default_int_handler(signum, frame)
            return
        # SIGTERM：恢复默认处置再自投一发 —— 语义与「没人处理」完全一致（rc=143），
        # 但顺序变成「先上报、后死」，而不是「先死、什么都没留下」。
        try:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        except Exception:
            pass
