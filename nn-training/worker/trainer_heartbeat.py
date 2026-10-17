"""trainer_heartbeat — 共享 trainer（`run_rl_cluster.py --serve`）的心跳（plan/single-resident-trainer §2）。

## 为什么需要

S1 之后 serve 是**唯一**常驻写者；而旧判据（`pidAlive`）判不出「卡死」——单线程
`while True: sup.step()`（`trainer/loop_serve.py`）一旦卡在某一步里，进程还在、健康检查照绿，
而所有课程一步都不前进。心跳让「卡死」可判：控制台 `stack/specs.ts::trainerServeSpec.healthy`
读这个文件，`ts` 陈旧 ⇒ 判不健康（**消费面**= 告警坞一条 err + 人工「停止→启动」，见 plan §2.2）。

## 为什么用**线程**写，而不是「每拍写一次」（评审 P0-2）

一次 `sup.step()` 之内可以**合法**阻塞很久：`join_precollect_child` 在本进程里轮询到
deadline=+3600s、PPO 让位、引擎冷启动。写点只挂在「每拍」上就覆盖不到这些等待 ⇒ 一个
**健康但正在等外部**的 serve 会被判死（而根据 §2.2，判死会变成告警/重启）。

独立写线程按固定周期写「主线程最后一次刷新过的快照」⇒ **任何**长阻塞都被自然覆盖，并把不变式
写成常量：

    最长合法无心跳间隔 ≈ HEARTBEAT_SEC —— 必须 << STALE（控制台判死窗口 300s）

（先例：`worker/eval_heartbeat.py` 就是评估板的同款线程。）

## 落点与字段

`tmp/trainer-heartbeat.json`（`NN_TRAINER_HEARTBEAT` 覆盖路径，读侧 `BCITY_TRAINER_HEARTBEAT`；
`NN_TRAINER_HEARTBEAT=0` = 关掉写入）。原子写（`common/fs.py::atomic_write_json`）——
控制台每拍读它，半写文件会读到坏 JSON。

    {"pid": 9792, "ts": 1792…（epoch 秒）, "steps": 4413, "waiting": 3,
     "courses": {"y20-far": {"state": "WAIT_EXTERNAL", "rounds_done": 137, "next_it": 138}}}

**不写进** `dashboard/data/evalboard/runner_state.json`（那是评估板的另一个事实）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.log import log

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 写线程周期（秒）。**不变式：HEARTBEAT_SEC << 控制台判死窗口 STALE=300s**（评审 P0-2 的护栏 4）。
HEARTBEAT_SEC = 5.0


def heartbeat_enabled() -> bool:
    """`NN_TRAINER_HEARTBEAT` 是否允许写（`0` = 关；其余 = 开）。"""
    return (os.environ.get("NN_TRAINER_HEARTBEAT") or "1") not in ("0", "off", "false")


def heartbeat_path() -> str:
    """心跳文件路径（`NN_TRAINER_HEARTBEAT` 可覆盖；`0` 在 `heartbeat_enabled` 里处理）。"""
    raw = os.environ.get("NN_TRAINER_HEARTBEAT") or ""
    if raw and raw not in ("0", "off", "false"):
        return raw
    return str(REPO_ROOT / "tmp" / "trainer-heartbeat.json")


def write_heartbeat(payload: dict, *, path: str | None = None, log_fn=None) -> None:
    """原子写一次心跳。IO 失败**只告警不抛**——心跳绝不打断训练主链（与评估板心跳同规）。"""
    from common.fs import atomic_write_json

    say = log_fn or log
    try:
        atomic_write_json(path or heartbeat_path(), payload)
    except OSError as e:
        say(f"[heartbeat] WARN: 写心跳失败（{e}）——控制台会看不到这一拍（不影响训练）")


class HeartbeatPublisher:
    """心跳发布器：主线程 `update(**fields)` 刷快照，写线程按周期落盘。

    线程只负责「把最后一次刷新的快照 + 当前时刻」写出去 ⇒ 主线程卡在任何一步里都不影响
    心跳新鲜度（这正是评审 P0-2 要的形状）。
    """

    def __init__(
        self,
        *,
        path: str | None = None,
        interval: float = HEARTBEAT_SEC,
        now: Callable[[], float] = time.time,
        log_fn: Callable[[str], None] = log,
        enabled: bool | None = None,
    ) -> None:
        self._path = path
        self._interval = float(interval)
        self._now = now
        self._log = log_fn
        self._enabled = heartbeat_enabled() if enabled is None else bool(enabled)
        self._lock = threading.Lock()
        self._fields: dict[str, Any] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_write = 0.0
        self.writes = 0  # 观测用（用例断言节流/写入次数）

    # ---- 主线程侧 ----

    def update(self, **fields: Any) -> None:
        """刷新快照（廉价：一次 dict update；每拍调用无压力）。"""
        with self._lock:
            self._fields.update(fields)

    # ---- 写侧 ----

    def payload(self) -> dict:
        """本次要写的完整负载（pid/ts 每次现取；其余来自快照）。"""
        with self._lock:
            body = dict(self._fields)
        return {"pid": os.getpid(), "ts": float(self._now()), **body}

    def tick(self, *, force: bool = False) -> bool:
        """写一次（`force` 或距上次写 ≥ interval 才真的落盘）。返回是否写了。"""
        if not self._enabled:
            return False
        now = float(self._now())
        if not force and (now - self._last_write) < self._interval:
            return False
        self._last_write = now
        write_heartbeat(self.payload(), path=self._path, log_fn=self._log)
        self.writes += 1
        return True

    def start(self) -> None:
        """起写线程（幂等）。第一拍立刻写（否则启动后的宽限期内控制台只能靠宽限判据）。"""
        if not self._enabled or self._thread is not None:
            return
        self.tick(force=True)

        def _loop() -> None:
            while not self._stop.wait(self._interval):
                try:
                    self.tick()
                except Exception as e:  # 写线程绝不许把主进程带下去
                    self._log(f"[heartbeat] WARN: 写线程异常（{e}）")

        t = threading.Thread(target=_loop, name="trainer-heartbeat", daemon=True)
        self._thread = t
        t.start()
        self._log(
            f"[heartbeat] 启动：{self._path or heartbeat_path()}（每 {self._interval:g}s；"
            f"不变式 最长合法无心跳间隔 ≈{self._interval:g}s << 判死窗口 300s）"
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
            self._thread = None


def read_heartbeat(path: str | None = None) -> dict | None:
    """读心跳（控制台之外的第二读面：用例/诊断）。坏文件/缺失 ⇒ None。"""
    p = path or heartbeat_path()
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
