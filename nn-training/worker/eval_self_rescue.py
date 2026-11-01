"""一轮干净 eval 的**消费线程 + 轮中自救**（2026-11-01，z3-p2002ctl it25/it50 的 786/285 局缺口）。

为什么单独成模块：`trainer/eval_dispatch.py` 贴着 LOC 护栏的天花板（`tests/test_python_loc_budget.py`
单文件 < 1000 行）——自救逻辑放回去就超预算。拆法按 `plan/nn-training-refactor.md §5.7` 的
「独立所有者 + 独立触发条件」：本类独占的是**本轮消费线程的孵化，以及消费者归零那一刻的救援路径**；
派发器仍持有 `threads` 列表（收工行与 join 都读它）、判据公式仍在 `worker/eval_yield.py`。
家在 `worker/`（训练栈）而不是 `trainer/`（只放编排，`tests/test_layering.py` 按集合相等钉住）。

现场：三节点 `503 {"error":"busy"}` 打满 `nodeSoftFailStreak` → 那几条腿的线程全部 `return`，
主循环 `live_workers<=0` 直接 break ⇒ 800 局里 786 局判死，而窗口还剩 1776s。等一等集群就空出来了，
缺的只是「谁去再取一次」：

* **轮中本机兜底**：兜底模式的冻结快照在派发前就建好了，节点腿轮中退光时本机接盘一次；
* **停派后回场**：软失败（503 busy / 瞬断）停派的节点按退避（20s → 40s，共 2 次）重取，
  回场即重置该腿的软失败预算；真故障（`nodeFailStreak`）仍整轮停派——回场不拆节点护栏。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from worker.eval_yield import (
    SOFT_COMEBACK_MAX,
    local_fallback_midround,
    soft_comeback_backoff_sec,
    soft_comeback_due,
)


class EvalConsumers:
    """本轮 eval 的消费线程 + 轮中自救账本（本机是否已孵 / 回场次数 / 回场时刻 / 各腿线程）。"""

    def __init__(
        self,
        *,
        lock: threading.Lock,
        live_workers: list[int],
        threads: list[threading.Thread],
        local_worker: Callable[..., Any],
        worker: Callable[..., Any],
        local_slots: int,
        fallback_only: bool,
        fail_streak_max: int,
        soft_streak_max: int,
        log: Callable[[str], None],
        it: int,
    ) -> None:
        self.lock = lock
        self.live_workers = live_workers
        self.threads = threads
        self.local_worker = local_worker
        self.worker = worker
        self.local_slots = local_slots
        self.fallback_only = fallback_only
        self.fail_streak_max = fail_streak_max
        self.soft_streak_max = soft_streak_max
        self._log = log
        self.it = it
        #: 本机槽位是否已孵化（整轮只孵一次：重复孵化会把本机核数越滚越多）。
        self.local_spawned = False
        #: 停派后回场账本：node id → 已回场次数 / 下次可回场时刻。**只对软失败开放**
        #: （`soft_streaks`）；真故障（`streaks`）仍整轮停派——那是机器/权重真坏。
        self.comebacks: dict[str, int] = {}
        self.comeback_at: dict[str, float] = {}
        #: 每条节点腿孵化过的线程（判「这条腿是否已退光」；`live_workers` 只是总数）。
        self.node_threads: dict[str, list[threading.Thread]] = {}

    # —— 孵化 ——

    def track(self, fn: Callable[..., Any], *a: Any, name: str = "") -> threading.Thread:
        """孵化消费线程并计入 live_workers（线程必须先计数再启动：主线程在
        spawn 完才看计数，晚计数会让收工判断误以为「消费线程全退」）。"""
        self.live_workers[0] += 1

        def _body() -> None:
            try:
                fn(*a)
            finally:
                with self.lock:
                    self.live_workers[0] -= 1

        t = threading.Thread(target=_body, daemon=True, name=name)
        t.start()
        return t

    def spawn_local(self, reason: str, *, snapshot_ready: bool) -> None:
        """孵化本机槽位（整轮**只孵一次**）：兜底模式派发前 / 轮中两条入口共用。"""
        if self.local_spawned or not snapshot_ready or self.local_slots <= 0:
            return
        self.local_spawned = True
        self._spawn_slots()
        self._log(f"[eval] it{self.it}: local fallback ×{self.local_slots}（{reason}）")

    def spawn_heads(self) -> None:
        """份额模式：本机槽位先开工（不置 `local_spawned`——那条只给兜底模式用）。"""
        self._spawn_slots()

    def _spawn_slots(self) -> None:
        for _ in range(self.local_slots):
            self.threads.append(self.track(self.local_worker, name="eval-local"))

    def spawn_node(self, nd: dict) -> None:
        """孵化一条节点腿的 N 条消费者线程（首次派发与停派后回场共用）。"""
        nid = str(nd["id"])
        for _ in range(int(nd["c"])):
            self.threads.append(self.track(self.worker, nd, name=f"eval-{nid}"))
            self.node_threads.setdefault(nid, []).append(self.threads[-1])

    # —— 轮中自救 ——

    def supervise(
        self,
        *,
        all_done: threading.Event,
        pending: deque[tuple[int, int]],
        inflight: dict[tuple[int, int], int],
        nodes_ok: list[dict[str, Any]],
        streaks: dict[str, int],
        soft_streaks: dict[str, int],
        deadline: float,
        snapshot_ready: bool,
    ) -> None:
        """消费者归零但**队列还有活** ⇒ 自救；无路可走才返回（由派发器收工）。

        旧实现在这里直接 break：一次 503 busy 拥堵就把整轮判死。三种走法：
        ① 兜底模式本机接盘一次（本轮没孵过 + 冻结快照在 + 槽位>0）；
        ② 软失败停派的节点退避到点后回场（回场 = 新一轮软失败预算）；
        ③ 都无路 ⇒ 返回，`workers-gone` 仍是**会到来**的状态（不把收工换成等窗口到期）。
        """
        while not all_done.is_set() and time.time() < deadline:
            all_done.wait(0.2)
            with self.lock:
                idle = self.live_workers[0] <= 0
                has_work = bool(pending) or bool(inflight)
                queue_len = len(pending)
            if not idle:
                continue
            if not has_work:
                return
            now = time.time()
            spawned_local = local_fallback_midround(
                self.fallback_only, self.local_spawned, snapshot_ready, self.local_slots, has_work
            )
            if spawned_local:
                self.spawn_local(
                    f"节点消费线程轮中归零，队列还剩 {queue_len} 局", snapshot_ready=snapshot_ready
                )
            revived = 0
            future_revival = False
            for nd in nodes_ok:
                nid = str(nd["id"])
                if any(t.is_alive() for t in self.node_threads.get(nid, ())):
                    continue
                with self.lock:
                    hard_stop = streaks.get(nid, 0) >= self.fail_streak_max
                    soft_stop = soft_streaks.get(nid, 0) >= self.soft_streak_max
                    # 别叫 `done`：派发器里那个 `done` 是 `eval_done_keys()` 的已评集。
                    back_n = self.comebacks.get(nid, 0)
                if hard_stop or not soft_stop or back_n >= SOFT_COMEBACK_MAX:
                    continue
                due_at = self.comeback_at.setdefault(nid, now + soft_comeback_backoff_sec(back_n))
                if not soft_comeback_due(back_n, now, due_at, deadline):
                    future_revival = future_revival or due_at < deadline
                    continue
                with self.lock:
                    soft_streaks[nid] = 0  # 回场 = 新一轮软失败预算
                    self.comebacks[nid] = back_n + 1
                self.comeback_at[nid] = now + soft_comeback_backoff_sec(back_n + 1)
                self.spawn_node(nd)
                revived += 1
                self._log(
                    f"[eval] it{self.it}: node {nid} 停派后回场"
                    f"（第 {back_n + 1}/{SOFT_COMEBACK_MAX} 次；队列还剩 {queue_len} 局）"
                )
            if revived or spawned_local or future_revival:
                continue
            return

    def report_comebacks(self) -> None:
        """收工时把回场次数打一行（一次都没回场就不打）。"""
        if self.comebacks:
            self._log(
                f"[eval] it{self.it}: 停派后回场 {sum(self.comebacks.values())} 次"
                f"（{', '.join(f'{k}×{v}' for k, v in sorted(self.comebacks.items()))}）"
            )
