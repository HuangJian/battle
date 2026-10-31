"""hub/worker_pulse.py — worker 静默哨兵（纯观测，2026-10-31）。

**动机（kaggle 事故 2026-10-31）**：那台云机的 notebook 进程在 09:51:58 之后再没有一条请求，
而 hub 侧**什么都没说**——要么 worker 自己崩了、要么整机被云侧收走，事后只能靠翻
`hub-server.out` 的 cadence（claim/result 的节奏）反推「它死在 09:51:42 那份 claim 之后 18 秒」。
而同一时间段的 notebook 日志是**浏览器脚本**抓 cell 输出上报的，人不在电脑前就一条不传
（`userscript/src/notebook-companion/app/ship-loop.ts`）⇒ 唯一的现场记录正好在案发窗口断掉。

本模块补的就是那条断掉的腿：**hub 是唯一知道「谁还活着」的地方**——worker 每 2s 轮一次
`/jobs/{id}/status`、每轮 peek 一次，只要进程还在，请求就不会停。

## 口径（三条都是「只观测」的同一条纪律）

1. **只打点、不裁决**：`touch()` 只记 `(last, touches, first)`，**绝不进任何派发/租约/游标判据**
   （与 `_worker_prefetch` / `_worker_errors` / `_offline_disks` 同规：进程内、重启即清）。
2. **报一条就够**：同一段静默只报一次（`alarmed` 位），下次 `touch()` 才复位并补一条「恢复」——
   恢复行带着**静默时长**，那是事后校准死亡时刻的唯一读数。
3. **只在「本 episod 有轮询节奏」时才报**：`touches >= min_touches` **且**这几次请求落在
   `arm_span_sec` 内才武装。push 模式的 worker（hub 主动推，它只在回结果时出现，间隔几分钟）
   与一次性的冒烟请求天生没有节奏，不武装就不会被误报——而真轮询的 worker 空闲 5s 一拍，
   十秒内就够 3 次。

阈值为什么 120s：worker 空闲轮询 5s、跑 job 时 `/status` 2s 一拍（`remote/job_round.py`），
而最长的单点阻塞是「下载 2.2MB payload + 解 60 个 shard」（实测 10s + 3s）⇒ 120s = 20 倍余量，
真静默只有一种解释：进程/整机没了。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Any

#: 静默阈值（秒）：超过它 + 本 episod 有轮询节奏 ⇒ 报一条。
WORKER_PULSE_SILENCE_SEC = 120.0
#: 武装门槛：本 episod 至少这么多次请求（挡住一次性冒烟请求/纯 push worker 的误报）。
WORKER_PULSE_MIN_TOUCHES = 3
#: 武装窗口（秒）：这 `min_touches` 次必须落在这么短的跨度里 = **轮询节奏**。
#: 为什么还要这一条：push 模式的 worker 只在「回结果」时出现（间隔几分钟），三次回传凑够
#: `min_touches` 却完全没有轮询节奏——按次数武装会把它误报成「死了」。而真轮询的 worker
#: （空闲 5s / 跑 job 时 `/status` 2s 一拍）十秒内就够 3 次。
WORKER_PULSE_ARM_SPAN_SEC = 60.0
#: 忘记窗口（秒）：超过它没再出现的 worker 直接丢弃（不让表随进程寿命无限长）。
WORKER_PULSE_FORGET_SEC = 6 * 3600.0
#: 扫描节拍（秒）：`hub/boot.py` 的后台线程按它调 `sweep()`。
WORKER_PULSE_SWEEP_SEC = 5.0


class _Rec:
    """一个 worker 的打点账（`__slots__` 只为一件事：这张表按 worker 数增长，字段别乱长）。"""

    __slots__ = ("alarmed", "first", "last", "touches")

    def __init__(self, now: float) -> None:
        self.last = now
        self.first = now
        self.touches = 1
        self.alarmed = False


class WorkerPulse:
    """worker 静默检测器（进程级、线程安全、可注入时钟）。

    `now_fn` 注入是为了用例（与 hub 其它观测面同规：`_now` 可被测试替换）；生产恒为 `time.time`。
    """

    def __init__(
        self,
        *,
        silence_sec: float = WORKER_PULSE_SILENCE_SEC,
        min_touches: int = WORKER_PULSE_MIN_TOUCHES,
        arm_span_sec: float = WORKER_PULSE_ARM_SPAN_SEC,
        forget_sec: float = WORKER_PULSE_FORGET_SEC,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._silence = float(silence_sec)
        self._min_touches = max(1, int(min_touches))
        self._arm_span = float(arm_span_sec)
        self._forget = float(forget_sec)
        self._now = now_fn
        self._state: dict[str, _Rec] = {}
        self._pending: list[dict[str, Any]] = []
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        """`silence_sec <= 0` = 关闭（`--worker-silence-sec 0`，误报逃生口）。"""
        return self._silence > 0.0

    def touch(self, worker_id: str) -> None:
        """登记一次请求（`WORKER_ID_HEADER` 有值才调）。O(1)、持锁一次、无 IO。

        中间**断过**（gap > 阈值）= 新 episod：`touches` 重新计数；若上一段报过警，这里补一条
        「恢复」事件（进 `_pending`，由 `sweep()` 统一发出——请求路径上不做任何上报）。
        """
        wid = (worker_id or "").strip()
        if not wid or not self.enabled:
            return
        now = self._now()
        with self._lock:
            rec = self._state.get(wid)
            if rec is None:
                self._state[wid] = _Rec(now)
                return
            gap = now - rec.last
            if gap > self._silence:
                if rec.alarmed:
                    self._pending.append({"kind": "recover", "worker": wid, "silent": gap})
                self._state[wid] = _Rec(now)
                return
            if now - rec.first > self._arm_span:
                # 这几次请求拉得太开 = 不是轮询节奏（push worker 逐份回结果）⇒ 重开 episod
                self._state[wid] = _Rec(now)
                return
            rec.touches += 1
            rec.last = now
            rec.alarmed = False

    def sweep(self) -> list[dict[str, Any]]:
        """扫一拍：返回本拍要上发的事件（`recover` 在前，`silence` 在后）。

        事件形状（`hub/boot.py` 原样喂 `note_worker_error`）：

          `{"kind": "silence", "worker", "silent", "last", "touches"}`
          `{"kind": "recover", "worker", "silent"}`
        """
        if not self.enabled:
            return []
        now = self._now()
        with self._lock:
            out = self._pending
            self._pending = []
            for wid, rec in list(self._state.items()):
                quiet = now - rec.last
                if quiet > self._forget:
                    del self._state[wid]
                    continue
                if rec.alarmed or quiet < self._silence or rec.touches < self._min_touches:
                    continue
                rec.alarmed = True
                out.append(
                    {
                        "kind": "silence",
                        "worker": wid,
                        "silent": quiet,
                        "last": rec.last,
                        "touches": rec.touches,
                    }
                )
        return out

    def reset(self) -> None:
        """清空（**仅供用例**隔离夹具：这是进程级表，跨用例会串味）。"""
        with self._lock:
            self._state.clear()
            self._pending.clear()

    def readout(self) -> dict[str, dict[str, float | int | bool]]:
        """打点表的只读快照：`{worker: {last, touches, alarmed}}`（诊断/用例）。

        为什么要有它而不是让调用方摸 `_state`：这张表是**进程级**的，用例在断言「请求真的
        打进来了」时不该同时依赖内部字段名（那是把测试钉在实现上）。
        """
        with self._lock:
            return {
                wid: {"last": rec.last, "touches": rec.touches, "alarmed": rec.alarmed}
                for wid, rec in self._state.items()
            }


def error_body(ev: dict[str, Any], *, silence_sec: float = WORKER_PULSE_SILENCE_SEC) -> dict[str, Any]:
    """事件 → `note_worker_error` 的上报体（**纯函数**，用例直接喂它）。

    字段对齐 `hub/queue_observe.py::note_worker_error` 的归一（`worker`/`reason` 必填、
    `severity` 只认 err/warn）；`component="hub-watchdog"` 让控制台把它与云机自报的
    `ppo-worker` 分开归类——「云机说它错了」与「hub 觉得它没了」是两件事。
    """
    kind = str(ev.get("kind") or "")
    wid = str(ev.get("worker") or "")
    silent = float(ev.get("silent") or 0.0)
    if kind == "silence":
        last = float(ev.get("last") or 0.0)
        hhmmss = time.strftime("%H:%M:%S", time.localtime(last)) if last else "?"
        return {
            "worker": wid,
            "component": "hub-watchdog",
            "severity": "err",
            "reason": (
                f"worker 静默 {silent:.0f}s（末次请求 {hhmmss}）——"
                f"≥{silence_sec:.0f}s 无任何请求，云侧会话可能已终止/被杀"
            ),
            "detail": (
                f"本 episod 共 {int(ev.get('touches') or 0)} 次请求；"
                "hub 只观测（不进派发/租约判据）；恢复后会补一条 recover"
            ),
        }
    return {
        "worker": wid,
        "component": "hub-watchdog",
        "severity": "warn",
        "reason": f"worker 恢复（静默 {silent:.0f}s 后重新出现）",
        "detail": "上一段静默的时长即案发窗口；若期间无人手工重启，按「云侧被杀」排查",
    }
