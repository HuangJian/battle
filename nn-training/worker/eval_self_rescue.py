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
  回场即重置该腿的软失败预算；真故障（`nodeFailStreak`）仍整轮停派——回场不拆节点护栏；
* **槽位动态扩容**（2026-11-03）：派发时的 `eval_slot_cap(concurrency, inflight)` 只是一张
  **快照**——rollout 退场后节点 inflight 下降，本轮按最新 ping 把空出来的槽补成线程。
  旧实现整窗钉在 floor=1：z5-safe-base it25 四个节点只开 4 条线程，700 局 1838s 只跑完 589。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import common.distribution
from worker.eval_yield import (
    SOFT_COMEBACK_MAX,
    eval_slot_cap,
    local_fallback_midround,
    soft_comeback_backoff_sec,
    soft_comeback_due,
)

#: 动态扩容的重 ping 间隔（秒）；`0` = 关闭。判据是「rollout 退场后补线程」而不是墙钟：
#: 测试把它压到毫秒级，走的仍是同一条 `_grow_slots` 路。
EVAL_SLOT_GROW_SEC = 15.0
#: 扩容 ping 的每节点预算（与派发时 `statusTimeoutSec` 的缺省同值）。
EVAL_GROW_PING_TIMEOUT_SEC = 3.0


def _capacity_pending(nodes_ok: list[dict[str, Any]]) -> bool:
    """还有节点「派发时让过位（`c<baseC`）」⇒ 它的份额只是被 rollout/PPO 占着，值得等。

    只看已入场节点：`nodes_ok` 为空（无节点 / 权重全失败）时无路可扩，仍按 workers-gone 收工。
    """
    return any(
        int(nd.get("c") or 0) < int(nd.get("baseC") or nd.get("c") or 1) for nd in nodes_ok
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
        #: 已按「权重就绪」孵过的节点（`on_alive` 与 POST 收工后的补齐两条路会撞车，
        #: 整轮每节点只孵一次；扩容/回场走 `spawn_node`，不受此集合限制）。
        self._spawned_nodes: set[str] = set()

    # —— 孵化 ——

    def track(self, fn: Callable[..., Any], *a: Any, name: str = "") -> threading.Thread:
        """孵化消费线程并计入 live_workers（线程必须先计数再启动：主线程在
        spawn 完才看计数，晚计数会让收工判断误以为「消费线程全退」）。

        计数走 `self.lock`：2026-11-03 起 `on_alive` 让**权重 POST 线程**也来孵化
        （边下发边开采），两个节点同时下发成功时 `+= 1` 不能丢。
        """
        with self.lock:
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
        """孵化一条节点腿的 N 条消费者线程（首次派发 / 停派后回场 / 扩容共用）。"""
        nid = str(nd["id"])
        for _ in range(int(nd["c"])):
            t = self.track(self.worker, nd, name=f"eval-{nid}")
            with self.lock:
                self.threads.append(t)
                self.node_threads.setdefault(nid, []).append(t)

    def spawn_node_once(self, nd: dict) -> None:
        """权重就绪即开采（2026-11-03 流水线）：整轮每节点**只孵一次**。

        `on_alive` 在权重 POST 的线程池里回调、POST 收工后主线程还会补一遍剩余节点
        （失败/超时的那些不在 `nodes_ok` 里）——两条路可能同时到同一节点，故用集合去重。
        `c<=0` 的节点不孵化（节点被 rollout 占满），等 `_grow_slots` 补。
        """
        nid = str(nd["id"])
        if int(nd.get("c") or 0) <= 0:
            return
        with self.lock:
            if nid in self._spawned_nodes:
                return
            self._spawned_nodes.add(nid)
        self.spawn_node(nd)

    def _grow_slots(
        self,
        nodes_ok: list[dict[str, Any]],
        streaks: dict[str, int],
        soft_streaks: dict[str, int],
        queue_len: int,
    ) -> None:
        """rollout 退场 ⇒ 重 ping，把空出来的槽补成本轮线程（2026-11-03）。

        只补**派发时让过位**的节点（`c < baseC`）：没让位 = 本来就满额，重 ping
        纯属多余（也让未发生拥堵的轮次完全不付这笔开销）。`ping.inflight` 含我们
        自己的在飞局 ⇒ 先减掉本腿活线程，剩下的才是 rollout / 别的轮占的份额，
        再走与派发时同一个 `eval_slot_cap` 判据。ping 拿不到就这拍不扩，不抛。
        """
        yielded = [
            nd for nd in nodes_ok if int(nd.get("c") or 0) < int(nd.get("baseC") or nd.get("c") or 1)
        ]
        if not yielded:
            return
        try:
            pings = common.distribution.ping_nodes_parallel(
                yielded, timeout=EVAL_GROW_PING_TIMEOUT_SEC
            )
        except Exception:
            return
        for nd, ping in zip(yielded, pings, strict=True):
            nid = str(nd["id"])
            if ping is None or not ping.get("evalSupport"):
                continue
            with self.lock:
                if streaks.get(nid, 0) >= self.fail_streak_max:
                    continue
                if soft_streaks.get(nid, 0) >= self.soft_streak_max:
                    continue
                alive_n = sum(1 for t in self.node_threads.get(nid, ()) if t.is_alive())
            base_c = int(nd.get("baseC") or nd.get("c") or 1)
            others = max(0, int(ping.get("inflight") or 0) - alive_n)
            want = eval_slot_cap(base_c, others)
            add = want - alive_n
            if add <= 0:
                continue
            self.spawn_node({**nd, "c": add})
            self._log(
                f"[eval] it{self.it}: node {nid} 扩容 +{add} → {want} 线程"
                f"（inflight={ping.get('inflight')}，队列还剩 {queue_len} 局）"
            )

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
        ③ 节点**让过位**（`c<baseC`）但当下被 rollout/PPO 占满 ⇒ 留在窗口里等扩容
           节拍（2026-11-03：eval 排队吃间隙，节点空出来就补线程；等不到由窗口收工）；
        ④ 都无路 ⇒ 返回，`workers-gone` 仍是**会到来**的状态（不把收工换成等窗口到期）。
        另有吞吐路径：每 `EVAL_SLOT_GROW_SEC` 重 ping，把 rollout 退场空出的槽补上。
        """
        grow_every = float(EVAL_SLOT_GROW_SEC)
        grow_at = time.time() + grow_every if grow_every > 0 else float("inf")
        while not all_done.is_set() and time.time() < deadline:
            all_done.wait(0.2)
            with self.lock:
                idle = self.live_workers[0] <= 0
                has_work = bool(pending) or bool(inflight)
                queue_len = len(pending)
            now = time.time()
            if grow_every > 0 and has_work and now >= grow_at:
                grow_at = now + grow_every
                self._grow_slots(nodes_ok, streaks, soft_streaks, queue_len)
            if not idle:
                continue
            if not has_work:
                return
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
            if _capacity_pending(nodes_ok):
                # 节点让过位、只是当下被占满：别把「等间隙」记成 workers-gone。
                continue
            return

    def report_comebacks(self) -> None:
        """收工时把回场次数打一行（一次都没回场就不打）。"""
        if self.comebacks:
            self._log(
                f"[eval] it{self.it}: 停派后回场 {sum(self.comebacks.values())} 次"
                f"（{', '.join(f'{k}×{v}' for k, v in sorted(self.comebacks.items()))}）"
            )
