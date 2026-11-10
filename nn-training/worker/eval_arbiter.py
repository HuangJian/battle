"""任务级 eval 仲裁：多轮共享同一批节点槽位，按优先级发**下一局**（2026-11-04）。

用户口径（plan/cloud-pool-cross-round.plan.md）：

1. 一轮 eval A 在跑时又来一轮 B ⇒ A/B **平等**分派，不排队等 A 收工；
2. 所有 eval 局都已派出仍有空闲 worker ⇒ 多副本竞速，跨轮按 `(stage, seed)` 平铺；
3. rollout 启动 ⇒ **在飞 eval 局不动**，只冻结新 eval 投递，直到 rollout 收尾；
4. 同一时刻多课程/多轮的 rollout 与 eval 在同一批节点上并行，分派端决定下一局是谁。

2026-11-04（§113）：冻结按**课程**作用域（`freeze(course_fp)`）。旧实现冻结整个进程
⇒ Supervisor 里两门课交替采集时（z7-maze-v2/kill 各有 91 轮采集），任一课采集都把
另一门课的 eval 一起停掉，实测冻结覆盖 75% 墙钟。`freeze(None)` 仍是进程级冻结
（手工 `evalA` / 无课程身份的旧路径）。

旧实现是「每轮一条轮级闸 + 每条腿一组线程」：闸串行轮次（并发 2），轮 A 的线程又
只看得见 A 的队列 ⇒ B 的局在 A 收尾前拿不到算力，A 尾部的算力只能拿去跑自己的竞速
副本。本模块把**队列与线程解耦**：轮只登记 `(stage, seed)` 与自己的上下文，线程从
`EvalArbiter.take(node)` 领 `Grant`；同一个 Grant 永远带着**拥有该任务的轮**的上下文
（权重 / iterId / course_fp / 快照 / 超时），所以线程可以执行任意轮的局。

判据仍只有一份：让位/份额在 `worker.eval_yield`，重试配额在 `worker.eval_local`，
轮中自救在 `worker.eval_self_rescue`。本模块只做状态机与仲裁。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

from worker.eval_yield import (
    hold_for_local,
    local_release_due,
    requeue_backoff_sec,
)

#: 本机槽位的伪节点 id（与旧派发器的线程名 / 账本口径一致）。
LOCAL_NODE = "local"
#: 没有可领任务时的轮询步长（秒）。竞速/退避都用它做上限，不忙等。
TAKE_TICK_SEC = 0.2
#: 无任何轮登记时，线程再等这么久才退出——只为躲开「上一轮刚收工、下一轮刚登记」
#: 的窗口，避免线程一退就要重新孵化（也避免单测里跨用例的池抖动）。
IDLE_EXIT_SEC = 0.5
#: 同一任务最多几份在飞副本（主副本 + 至多 1 份竞速）。
#: 竞速是尾延迟手段：副本数应随**剩余工作量**增长，而不是随空槽数增长。
#: 旧实现让 `pending` 空后的每个空闲线程都复制任意在飞局，48 个空槽对 1 局
#: 残局可以产生 47 份完整重跑；这里把上界钉死在 2。
MAX_COPIES_PER_TASK = 2


class RoundClock:
    """一轮 eval 的窗口钟（冻结感知）。

    rollout 活跃期钟必须停：否则 eval 每轮醒来都被判「窗口过期」，rollout 一结束
    整批 eval 已经死了。权重 POST 期间钟还没 arm（窗口从权重就绪起算，2026-11-03），
    此时只有 `registered_at + window + post_budget` 的临时上限，同样冻结感知。
    """

    __slots__ = (
        "paused_at",
        "paused_total",
        "post_budget",
        "registered_at",
        "started",
        "window",
    )

    def __init__(self, window: float, post_budget: float) -> None:
        self.window = max(0.0, float(window))
        self.post_budget = max(0.0, float(post_budget))
        self.registered_at = 0.0
        self.started = 0.0
        self.paused_total = 0.0
        self.paused_at = 0.0

    def register(self, now: float) -> None:
        self.registered_at = now

    def arm(self, now: float, *, paused: bool) -> None:
        """权重就绪：窗口从现在起算一个完整 `window`。"""
        self.started = now
        self.paused_total = 0.0
        self.paused_at = now if paused else 0.0

    def pause(self, now: float) -> None:
        if not self.paused_at:
            self.paused_at = now

    def resume(self, now: float) -> None:
        if self.paused_at:
            self.paused_total += now - self.paused_at
            self.paused_at = 0.0

    @property
    def paused(self) -> bool:
        return self.paused_at > 0.0

    def _clock_now(self, now: float) -> float:
        return self.paused_at or now

    def elapsed(self, now: float) -> float:
        if not self.started:
            return 0.0
        return max(0.0, self._clock_now(now) - self.started - self.paused_total)

    def remaining(self, now: float) -> float:
        if not self.started:
            if not self.registered_at:
                return self.window + self.post_budget
            spent = self._clock_now(now) - self.registered_at - self.paused_total
            return max(0.0, self.window + self.post_budget - spent)
        return max(0.0, self.window - self.elapsed(now))

    def expired(self, now: float) -> bool:
        return self.remaining(now) <= 0.0

    def deadline(self, now: float) -> float:
        return now + self.remaining(now)


@dataclass(frozen=True)
class Grant:
    """一次发牌：`round` 拥有任务上下文，`node` 是执行者（`LOCAL_NODE` = 本机）。

    `gid` 是轮内单调递增的唯一号：`finish` 用**它**做幂等键。别用 `id(grant)`——
    CPython 会在对象回收后复用地址，几张牌先后领取就会撞号，`finish` 误判「已结算」
    ⇒ 任务既不回队也不落账（2026-11-04 首次接线实测：4 局丢 3 局）。
    """

    round: EvalRound
    task: tuple[int, int]
    node: str
    replica: bool
    leg: int
    started: float
    gid: int = 0


class EvalRound:
    """一轮 eval 的任务表 + 上下文（线程无关；线程只借它的上下文执行 Grant）。"""

    def __init__(
        self,
        *,
        seq: int,
        it: int,
        iter_id: str,
        eval_iter_id: str,
        wver: str,
        key16: str,
        course_fp: str,
        args: Any,
        bun: str,
        eval_dir: Path,
        eval_jsonl: Path,
        meta_path: Path,
        task_timeout: float,
        window: float,
        post_budget: float,
        snapshot_path: str | None,
        local_slots: int,
        fallback_only: bool,
        reserved: int,
        local_gate: threading.Event | None,
        fail_streak_max: int,
        soft_streak_max: int,
        req_scope: str,
        total: int,
        pairs: list[tuple[int, int]],
        todo: list[tuple[int, int]],
        weights_kind: str,
        weights_bytes: bytes | None,
        attempts_max: int,
        attempts_transient_max: int,
        record_retry_max: int,
        reuse_node: str,
        row_builder: Any,
        append_row: Any,
        meta_ok: Any,
        record_failed_meta: Any,
        fail_recorder: Any,
        local_fn: Any,
        abort_scope: Any,
        log: Any,
    ) -> None:
        self.seq = seq
        self.it = it
        self.iter_id = iter_id
        self.eval_iter_id = eval_iter_id
        self.wver = wver
        self.key16 = key16
        self.course_fp = course_fp
        self.args = args
        self.bun = bun
        self.eval_dir = eval_dir
        self.eval_jsonl = eval_jsonl
        self.meta_path = meta_path
        self.task_timeout = task_timeout
        self.clock = RoundClock(window, post_budget)
        self.post_budget = post_budget
        self.snapshot_path = snapshot_path
        self.local_slots = local_slots
        self.fallback_only = fallback_only
        self.reserved = reserved
        self.local_gate = local_gate
        self.fail_streak_max = fail_streak_max
        self.soft_streak_max = soft_streak_max
        self.req_scope = req_scope
        self.total = total
        self.pairs = pairs
        self.pending: deque[tuple[int, int]] = deque(todo)
        self.weights_kind = weights_kind
        self.weights_bytes = weights_bytes
        self.attempts_max = attempts_max
        self.attempts_transient_max = attempts_transient_max
        self.record_retry_max = record_retry_max
        self.reuse_node = reuse_node
        self._row_builder = row_builder
        self._append_row = append_row
        self._meta_ok = meta_ok
        self._record_failed_meta = record_failed_meta
        self._fail_recorder = fail_recorder
        self.local_fn = local_fn
        self._abort_scope = abort_scope
        self._log = log

        self._lock = threading.Lock()
        self.nodes: dict[str, dict[str, Any]] = {}
        # 每节点许可（c）与本轮在该节点的在飞数：claim 时扣、settle 时还。
        # 池线程数只是物理载体；没有许可账时，历史大池会在下轮解冻后按旧槽数抢任务。
        self.node_caps: dict[str, int] = {LOCAL_NODE: max(0, int(local_slots))}
        self.node_inflight: dict[str, int] = {}
        self.remote_ready = False
        self.attempts: dict[tuple[int, int], int] = {}
        self.leg_tries: dict[tuple[tuple[int, int], str], int] = {}
        self.retry_at: dict[tuple[int, int], float] = {}
        self.seen: set[tuple[int, int]] = set()
        self.landed: set[tuple[int, int]] = set()
        self.inflight: dict[tuple[int, int], int] = {}
        self.inflight_nodes: dict[tuple[int, int], set[str]] = {}
        self.streaks: dict[str, int] = {}
        self.soft_streaks: dict[str, int] = {}
        self.redispatched: set[tuple[int, int]] = set()
        self.local_stopped = threading.Event()
        self.local_spawned = False
        self.comebacks: dict[str, int] = {}
        self.comeback_at: dict[str, float] = {}
        self.wins = [0]
        self.cleared_total = [0]
        self.outcomes: dict[str, int] = {}
        self.node_games: dict[str, int] = {}
        self.writers = 0
        self.settled = threading.Event()
        self.closed = False
        self.stop_grants = False
        #: 已 `finish` 过的 Grant（`Grant.gid`）：执行层兜底与正常路径都调 finish，二次调用
        #: 不得再计一次 streak / 再回队一次（重复结算即重复扣预算）。
        self._done_grants: set[int] = set()
        self._grant_seq = 0

    # —— 上下文 / 时钟 ——

    def log(self, msg: str) -> None:
        self._log(msg)

    def register_clock(self, now: float) -> None:
        with self._lock:
            self.clock.register(now)

    def arm_window(self, now: float, *, paused: bool) -> None:
        with self._lock:
            self.clock.arm(now, paused=paused)

    def deadline(self, now: float | None = None) -> float:
        with self._lock:
            return self.clock.deadline(time.time() if now is None else now)

    def remaining(self, now: float) -> float:
        with self._lock:
            return self.clock.remaining(now)

    def expired(self, now: float) -> bool:
        with self._lock:
            return self.clock.expired(now)

    def pause_clock(self, now: float) -> None:
        with self._lock:
            self.clock.pause(now)

    def resume_clock(self, now: float) -> None:
        with self._lock:
            self.clock.resume(now)

    def activate(self, nd: dict[str, Any]) -> None:
        nid = str(nd["id"])
        with self._lock:
            self.nodes[nid] = dict(nd)
            self.node_caps[nid] = max(0, int(nd.get("c") or 0))

    def set_remote_ready(self, nodes_ok: list[dict[str, Any]]) -> None:
        """POST 收工：只保留权重真正就绪的节点，本机兜底从此可用。"""
        with self._lock:
            keep = {str(nd["id"]) for nd in nodes_ok}
            for nid in list(self.nodes):
                if nid not in keep:
                    self.nodes.pop(nid, None)
            self.remote_ready = True

    def node_info(self, nid: str) -> dict[str, Any] | None:
        with self._lock:
            nd = self.nodes.get(nid)
            return dict(nd) if nd is not None else None

    def node_ids(self) -> list[str]:
        with self._lock:
            return list(self.nodes)

    def set_node_cap(self, nid: str, cap: int) -> None:
        """更新节点许可（扩容/让位后与池目标同源）。"""
        with self._lock:
            self.node_caps[nid] = max(0, int(cap))

    def _node_cap_locked(self, nid: str) -> int:
        if nid == LOCAL_NODE:
            return max(0, int(self.local_slots))
        if nid in self.node_caps:
            return self.node_caps[nid]
        # 旧测试/手工构造的 nodes 没有 c：按 1 槽处理（生产节点必带 c）。
        return max(0, int((self.nodes.get(nid) or {}).get("c") or 1))

    def _has_node_capacity_locked(self, nid: str) -> bool:
        return self.node_inflight.get(nid, 0) < self._node_cap_locked(nid)

    # —— 参与判据（仲裁器调用；读锁） ——

    def _node_routable_locked(self, nid: str) -> bool:
        return (
            nid in self.nodes
            and self.streaks.get(nid, 0) < self.fail_streak_max
            and self.soft_streaks.get(nid, 0) < self.soft_streak_max
        )

    def _remote_alive_locked(self) -> bool:
        return any(self._node_routable_locked(nid) for nid in self.nodes)

    def local_ok_locked(self, now: float) -> bool:
        if self.local_stopped.is_set() or self.snapshot_path is None or self.local_slots <= 0:
            return False
        if not self.fallback_only:
            return self.local_gate is None or self.local_gate.is_set()
        # 兜底模式：POST 收工后，只有**没有任何可路由远端**时本机才接盘。
        return self.remote_ready and not self._remote_alive_locked()

    def _participates_locked(self, nid: str, now: float) -> bool:
        if self.closed or self.stop_grants or self.settled.is_set():
            return False
        if self.clock.expired(now):
            return False
        if nid == LOCAL_NODE:
            return self.local_ok_locked(now)
        return self._node_routable_locked(nid)

    def participates(self, nid: str, now: float) -> bool:
        with self._lock:
            return self._participates_locked(nid, now)

    def wants(self, nid: str, now: float) -> bool:
        """这一轮是否**还需要** `nid` 这个执行者（池线程据此决定等还是退出）。

        与 `participates` 只差一处：份额模式下 gate 还没开的本机也**算需要**——旧派发器的
        本机线程就是一直等到 gate 放行（或窗口到期）才退出；只看 `participates` 会让线程在
        放行前 0.5s 就退光，而 `release_local_gate_if_starved` 之后没人再孵化。
        """
        with self._lock:
            if self.closed or self.stop_grants or self.settled.is_set():
                return False
            if self.clock.expired(now):
                return False
            if nid == LOCAL_NODE:
                if (
                    self.local_stopped.is_set()
                    or self.snapshot_path is None
                    or self.local_slots <= 0
                ):
                    return False
                if not self.fallback_only:
                    return True
                return self.remote_ready and not self._remote_alive_locked()
            return self._node_routable_locked(nid)

    def node_routable(self, nid: str, now: float) -> bool:
        with self._lock:
            return (
                not self.closed
                and not self.stop_grants
                and not self.settled.is_set()
                and not self.clock.expired(now)
                and self._node_routable_locked(nid)
            )

    def local_ok(self, now: float) -> bool:
        with self._lock:
            return self.local_ok_locked(now)

    def has_work(self) -> bool:
        with self._lock:
            return bool(self.pending) or bool(self.inflight)

    def pending_len(self) -> int:
        with self._lock:
            return len(self.pending)

    def inflight_len(self) -> int:
        with self._lock:
            return len(self.inflight)

    # —— 发牌 ——

    def _node_route_alive_locked(self) -> bool:
        return any(self._node_routable_locked(nid) for nid in self.nodes)

    def _take_ready_locked(self, for_local: bool) -> tuple[int, int] | None:
        if not self.pending:
            return None
        now = time.time()
        node_live = self._node_route_alive_locked()
        for _ in range(len(self.pending)):
            task = self.pending.popleft()
            if for_local and task in self.redispatched and node_live:
                self.pending.append(task)
                continue
            due = self.retry_at.get(task)
            if due is None or due <= now:
                self.retry_at.pop(task, None)
                return task
            self.pending.append(task)
        return None

    def _claim_locked(self, task: tuple[int, int], nid: str, *, replica: bool) -> Grant:
        if not replica:
            self.attempts[task] = self.attempts.get(task, 0) + 1
        key = (task, nid)
        leg = self.leg_tries.get(key, 0) + 1
        self.leg_tries[key] = leg
        self.inflight[task] = self.inflight.get(task, 0) + 1
        self.inflight_nodes.setdefault(task, set()).add(nid)
        self.node_inflight[nid] = self.node_inflight.get(nid, 0) + 1
        self._grant_seq += 1
        return Grant(
            round=self,
            task=task,
            node=nid,
            replica=replica,
            leg=leg,
            started=time.time(),
            gid=self._grant_seq,
        )

    def claim_unique(self, nid: str, now: float) -> Grant | None:
        with self._lock:
            if not self._participates_locked(nid, now):
                return None
            if not self._has_node_capacity_locked(nid):
                return None
            if nid != LOCAL_NODE:
                gate_ok = self.local_gate is not None and self.local_gate.is_set()
                if hold_for_local(
                    len(self.pending),
                    self.reserved,
                    gate_ok,
                    local_release_due(now, self.clock.deadline(now)),
                ):
                    return None
            task = self._take_ready_locked(for_local=nid == LOCAL_NODE)
            if task is None:
                return None
            return self._claim_locked(task, nid, replica=False)

    def replica_candidates(self, nid: str, now: float) -> list[tuple[int, int]]:
        with self._lock:
            if not self._participates_locked(nid, now):
                return []
            if not self._has_node_capacity_locked(nid):
                return []
            return [
                task
                for task in self.inflight
                if nid not in self.inflight_nodes.get(task, set())
                and self.inflight.get(task, 0) < MAX_COPIES_PER_TASK
            ]

    def claim_replica(self, nid: str, task: tuple[int, int], now: float) -> Grant | None:
        with self._lock:
            if not self._participates_locked(nid, now):
                return None
            if (
                not self._has_node_capacity_locked(nid)
                or task not in self.inflight
                or nid in self.inflight_nodes.get(task, set())
                or self.inflight.get(task, 0) >= MAX_COPIES_PER_TASK
            ):
                return None
            grant = self._claim_locked(task, nid, replica=True)
        self._log(f"[eval] tail-race s{task[0]}/seed{task[1]} node={nid} — race lane")
        return grant

    # —— 结算 ——

    def _release_node_locked(self, nid: str) -> None:
        n = self.node_inflight.get(nid, 0)
        if n <= 0:
            return
        if n <= 1:
            self.node_inflight.pop(nid, None)
        else:
            self.node_inflight[nid] = n - 1

    def _pop_inflight_locked(self, task: tuple[int, int], nid: str) -> None:
        n = self.inflight.get(task, 0)
        if n <= 0:
            return
        self._release_node_locked(nid)
        if n <= 1:
            self.inflight.pop(task, None)
            self.inflight_nodes.pop(task, None)
        else:
            self.inflight[task] = n - 1
            self.inflight_nodes.get(task, set()).discard(nid)

    def _clear_inflight_locked(self, task: tuple[int, int]) -> None:
        for nid in self.inflight_nodes.get(task, ()):
            self._release_node_locked(nid)
        self.inflight.pop(task, None)
        self.inflight_nodes.pop(task, None)

    def _stop_node_locked(self, nid: str) -> None:
        self._log(
            f"[eval] node {nid}: 连续 {self.soft_streak_max} 次瞬时失败"
            f"（背压/瞬断，非节点故障）— 本轮停派"
        )

    def _requeue_locked(self, task: tuple[int, int], transient: bool, leg: int) -> None:
        if transient:
            self.retry_at[task] = time.time() + requeue_backoff_sec(leg)
        self.pending.append(task)

    def settle_task(
        self,
        grant: Grant,
        *,
        ok: bool,
        manifest: dict[str, Any] | None = None,
        err: str = "",
        transient: bool = False,
        task_lost: bool = False,
        err_errno: str = "",
        msg_head: str = "",
        wall_sec: float | None = None,
        node_alive: bool = False,
    ) -> bool:
        """一局收尾。返回 True = 调用方应落盘（`record_with_retry`）。"""
        task = grant.task
        nid = grant.node
        manifest = manifest or {}
        with self._lock:
            if grant.gid in self._done_grants:
                return False
            self._done_grants.add(grant.gid)
            if ok:
                if self.closed or self.settled.is_set() or task in self.seen:
                    self._pop_inflight_locked(task, nid)
                    why = "收工后返回" if (self.closed or self.settled.is_set()) else "dup settle"
                    self._log(
                        f"[eval] {why} s{task[0]}/seed{task[1]} node={nid} — dropped"
                    )
                    return False
                self.seen.add(task)
                self.writers += 1
                self._clear_inflight_locked(task)
                self.streaks[nid] = 0
                self.soft_streaks[nid] = 0
                return True
            if self.closed:
                self._pop_inflight_locked(task, nid)
                return False
            if grant.replica:
                self._pop_inflight_locked(task, nid)
                return False
            if transient:
                self.soft_streaks[nid] = self.soft_streaks.get(nid, 0) + 1
                if self.soft_streaks[nid] == self.soft_streak_max:
                    if nid == LOCAL_NODE:
                        self.local_stopped.set()
                        self._log(
                            f"[eval] local: 连续 {self.soft_streak_max} 次瞬时失败"
                            "（背压/瞬断，非节点故障）— 本轮停派"
                        )
                    else:
                        self._stop_node_locked(nid)
            elif nid != LOCAL_NODE:
                self.streaks[nid] = self.streaks.get(nid, 0) + 1
            self._pop_inflight_locked(task, nid)
            leg = grant.leg
            if nid == LOCAL_NODE:
                if transient:
                    if leg < self.attempts_transient_max and task not in self.seen:
                        self._requeue_locked(task, True, leg)
                        self._log(
                            f"[eval] s{task[0]}/seed{task[1]} failed ({err})"
                            " — requeued（瞬断/背压，不计节点故障）"
                        )
                    elif task not in self.seen:
                        self._record_failed_locked(task, nid, err, err_errno, msg_head)
                        self._log(
                            f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err})"
                            " — dropped（瞬断预算用尽）"
                        )
                elif task not in self.seen and node_alive:
                    self.redispatched.add(task)
                    self.pending.appendleft(task)
                    self._log(
                        f"[eval] s{task[0]}/seed{task[1]} failed ({err})"
                        f" — 改派给在线节点（本机第 {leg} 次）"
                    )
                elif leg < self.attempts_max and task not in self.seen:
                    self._requeue_locked(task, False, leg)
                    self._log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                elif task not in self.seen:
                    self._record_failed_locked(task, nid, err, err_errno, msg_head)
                    self._log(
                        f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err}) — dropped"
                    )
                return False
            retry = leg < self.attempts_max or (
                transient and leg < self.attempts_transient_max
            )
            if retry and task not in self.seen:
                self._requeue_locked(task, transient, leg)
                suffix = (
                    "（任务丢失·节点重启，已回队、不计故障）"
                    if task_lost
                    else "（瞬断/背压，不计节点故障）"
                    if transient
                    else ""
                )
                self._log(
                    f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued{suffix}"
                )
            elif task not in self.seen:
                self._fail_recorder(self.meta_path, self.it, task, nid, err)
                self._log(
                    f"[eval] s{task[0]}/seed{task[1]} failed {leg}x ({err}) — dropped"
                )
            return False

    def finish_write(self) -> None:
        with self._lock:
            self.writers = max(0, self.writers - 1)

    def _record_failed_locked(
        self,
        task: tuple[int, int],
        nid: str,
        err: str,
        err_errno: str,
        msg_head: str,
    ) -> None:
        if nid == LOCAL_NODE:
            self._fail_recorder(
                self.meta_path, self.it, task, nid, err, err_errno, msg_head
            )
        else:
            self._fail_recorder(self.meta_path, self.it, task, nid, err)

    def _record(
        self,
        grant: Grant,
        manifest: dict[str, Any],
        wall_sec: float | None,
        row: dict[str, Any] | None,
    ) -> None:
        task = grant.task
        nid = grant.node
        if row is None:
            row = self._row_builder(
                manifest,
                it=self.it,
                key16=self.key16,
                task=task,
                node=nid,
                wall_sec=wall_sec,
            )
        win = int(row["win"])
        cleared = int(row["cleared"])
        self._append_row(self.eval_jsonl, row)
        try:
            self._meta_ok(nid, manifest, task, wall_sec, win)
        except Exception as e:
            self._log(
                f"[eval] WARN record meta 写入失败（忽略）"
                f": {type(e).__name__}: {str(e)[:160]}"
            )
        with self._lock:
            self.wins[0] += win
            self.cleared_total[0] += cleared
            self.node_games[nid] = self.node_games.get(nid, 0) + 1
            oc = str(manifest.get("outcome"))
            self.outcomes[oc] = self.outcomes.get(oc, 0) + 1

    def record_with_retry(
        self,
        grant: Grant,
        manifest: dict[str, Any],
        wall_sec: float | None,
        row: dict[str, Any] | None = None,
    ) -> bool:
        task = grant.task
        last_err: Exception | None = None
        for attempt in range(1, 2 + self.record_retry_max):
            try:
                self._record(grant, manifest, wall_sec, row)
                break
            except Exception as e:
                last_err = e
                self._log(
                    f"[eval] s{task[0]}/seed{task[1]} 落盘失败"
                    f"（attempt {attempt}/{1 + self.record_retry_max}）"
                    f": {type(e).__name__}: {str(e)[:160]}"
                )
        else:
            err_txt = (
                f"{type(last_err).__name__}: {str(last_err)[:160]}"
                if last_err is not None
                else "unknown"
            )
            self._record_failed_meta(
                grant.node, task, f"record-failed: {err_txt}"
            )
            self._log(
                f"[eval] s{task[0]}/seed{task[1]} 落盘失败 ×{1 + self.record_retry_max}"
                f" —— dropped（本局计入缺口，收官 drain 可补）"
            )
            return False
        with self._lock:
            self.landed.add(task)
            complete = len(self.landed) >= self.total
        if complete:
            self.settle_complete()
        return True

    def land_reused(self, reused: dict[tuple[int, int], dict[str, Any]]) -> None:
        for task, row in reused.items():
            with self._lock:
                self.seen.add(task)
                self._grant_seq += 1
                gid = self._grant_seq
            grant = Grant(
                round=self,
                task=task,
                node=self.reuse_node,
                replica=False,
                leg=0,
                started=0.0,
                gid=gid,
            )
            self.record_with_retry(grant, {}, None, row=row)

    def settle_complete(self) -> None:
        if self.settled.is_set():
            return
        self.settled.set()
        self._abort_scope()

    def landed_count(self) -> int:
        with self._lock:
            return len(self.landed)

    def writers_pending(self) -> int:
        with self._lock:
            return int(self.writers)

    # —— 收工 ——

    def stop(self) -> None:
        with self._lock:
            self.stop_grants = True

    def close(self) -> dict[str, Any]:
        with self._lock:
            self.closed = True
            self.stop_grants = True
            never_pairs = {p for p in self.pending if self.attempts.get(p, 0) == 0}
            stats = {
                "left_pending": len(self.pending),
                "inflight_n": len(self.inflight),
                "never_pairs": never_pairs,
                "landed": set(self.landed),
                "claimed": set(self.seen),
                "writers": self.writers,
                "soft_streaks": dict(self.soft_streaks),
                "local_stopped": self.local_stopped.is_set(),
                "settled": self.settled.is_set(),
            }
        self.settled.set()
        return stats

    def report_comebacks(self) -> None:
        with self._lock:
            if not self.comebacks:
                return
            detail = ", ".join(f"{k}×{v}" for k, v in sorted(self.comebacks.items()))
            total = sum(self.comebacks.values())
        self._log(f"[eval] it{self.it}: 停派后回场 {total} 次（{detail}）")

    def stopped_nodes(self) -> set[str]:
        with self._lock:
            out = {
                k for k, v in self.soft_streaks.items() if v >= self.soft_streak_max
            }
            if self.local_stopped.is_set():
                out.add(LOCAL_NODE)
            return out

    # —— 轮中自救用的小账本（判据仍在 eval_yield） ——

    def yielded_nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(nd)
                for nid, nd in self.nodes.items()
                if int(nd.get("c") or 0)
                < int(nd.get("baseC") or nd.get("c") or 1)
                and self._node_routable_locked(nid)
            ]

    def comeback_state(self, nid: str, now: float) -> tuple[bool, bool, int, float | None, float]:
        """(真停派, 软停派, 已回场次数, 回场到期时刻(未排= None), 窗口截止)。

        判据（`soft_comeback_due` / `SOFT_COMEBACK_MAX`）留在 `eval_self_rescue`；
        这里只把状态一次性读出来，避免自救循环逐字段加锁。
        """
        with self._lock:
            nd = self.nodes.get(nid)
            if nd is None:
                return True, False, 0, None, now
            hard = self.streaks.get(nid, 0) >= self.fail_streak_max
            soft = self.soft_streaks.get(nid, 0) >= self.soft_streak_max
            back_n = self.comebacks.get(nid, 0)
            due_at = self.comeback_at.get(nid)
            deadline = self.clock.deadline(now)
        return hard, soft, back_n, due_at, deadline

    def revive_node(self, nid: str, next_due: float) -> int:
        with self._lock:
            back_n = self.comebacks.get(nid, 0)
            self.soft_streaks[nid] = 0
            self.comebacks[nid] = back_n + 1
            self.comeback_at[nid] = next_due
        return back_n + 1

    def schedule_comeback(self, nid: str, at: float) -> None:
        with self._lock:
            self.comeback_at[nid] = at

    def mark_local_spawned(self) -> None:
        with self._lock:
            self.local_spawned = True

    def mark_remote_ready(self) -> None:
        with self._lock:
            self.remote_ready = True


class EvalArbiter:
    """进程级仲裁：所有轮共享一个发牌循环（每节点槽位由全局线程池持有）。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._rounds: list[EvalRound] = []
        self._rr = 0
        self._rep = 0
        self._grant_ids = count(1)
        self._frozen = 0
        self._frozen_courses: dict[str, int] = {}
        self._shutdown = False

    # —— 登记 / 冻结 ——

    def _frozen_for_locked(self, course_fp: str) -> bool:
        return self._frozen > 0 or self._frozen_courses.get(course_fp, 0) > 0

    def register(self, rnd: EvalRound) -> None:
        with self._cond:
            rnd.register_clock(time.time())
            if self._frozen_for_locked(rnd.course_fp):
                rnd.pause_clock(time.time())
            self._rounds.append(rnd)
            self._cond.notify_all()

    def unregister(self, rnd: EvalRound) -> None:
        with self._cond:
            try:
                self._rounds.remove(rnd)
            except ValueError:
                pass
            # 游标是**绝对**的：这里不能按当前轮数归一化。第三轮收工时把
            # `_rr` 压回 `[0, n)`，随后新轮登记（列表又变长）就会让刚发过牌
            # 的轮从 0 再抢一次 —— 2026-11-04 pygate 复现的 A,A,B,B。
            # 取模只发生在 `_unique_grant` / `_replica_grant` 的读取处。
            if not self._rounds:
                self._rr = self._rep = 0
            self._cond.notify_all()

    def next_seq(self) -> int:
        # `count` 的 `next()` 在 GIL 下原子：发牌序号只是路径标签，不必进条件锁。
        return next(self._grant_ids)

    def freeze(self, course_fp: str | None = None) -> None:
        """冻结新 grant：`course_fp=None` = 进程级；给了值 = 只冻该课（§113）。"""
        with self._cond:
            now = time.time()
            if course_fp is None:
                self._frozen += 1
                if self._frozen == 1:
                    for rnd in self._rounds:
                        rnd.pause_clock(now)
            else:
                n = self._frozen_courses.get(course_fp, 0) + 1
                self._frozen_courses[course_fp] = n
                if n == 1:
                    for rnd in self._rounds:
                        if rnd.course_fp == course_fp:
                            rnd.pause_clock(now)
            self._cond.notify_all()

    def thaw(self, course_fp: str | None = None) -> None:
        with self._cond:
            now = time.time()
            if course_fp is None:
                if self._frozen <= 0:
                    return
                self._frozen -= 1
                if self._frozen == 0:
                    for rnd in self._rounds:
                        if not self._frozen_for_locked(rnd.course_fp):
                            rnd.resume_clock(now)
            else:
                n = self._frozen_courses.get(course_fp, 0)
                if n <= 0:
                    return
                if n == 1:
                    self._frozen_courses.pop(course_fp, None)
                else:
                    self._frozen_courses[course_fp] = n - 1
                if not self._frozen_for_locked(course_fp):
                    for rnd in self._rounds:
                        if rnd.course_fp == course_fp:
                            rnd.resume_clock(now)
            self._cond.notify_all()

    def wait_for_thaw(self, course_fp: str) -> bool:
        """冻结期阻塞调用方（eval 派发线程），解冻/仲裁器 shutdown 后返回。

        B2（2026-11-10）：eval 冻结期间不再 POST 权重。窗口钟在 `register()` 时已暂停，
        这里只等本课解冻；进程 shutdown 返回 False，调用方放弃本轮。
        """
        with self._cond:
            while self._frozen_for_locked(course_fp) and not self._shutdown:
                self._cond.wait(TAKE_TICK_SEC)
            return not self._shutdown

    def frozen(self) -> bool:
        """进程级冻结（`freeze()` 无参）。按课冻结走 `frozen_for(course_fp)`。"""
        with self._cond:
            return self._frozen > 0

    def frozen_for(self, course_fp: str) -> bool:
        with self._cond:
            return self._frozen_for_locked(course_fp)

    def active_for(self, course_fp: str) -> int:
        """该课仍登记在仲裁器里的轮数（在跑或尚未收工）。"""
        with self._cond:
            return sum(1 for rnd in self._rounds if rnd.course_fp == course_fp)

    def reset_for_tests(self) -> None:
        with self._cond:
            self._shutdown = True
            self._rounds.clear()
            self._frozen = 0
            self._frozen_courses.clear()
            self._cond.notify_all()

    # —— 发牌 ——

    def take(self, nid: str, stop: threading.Event | None = None) -> Grant | None:
        """给 `nid` 发下一局：唯一任务按轮轮转 → 跨轮竞速副本 → 等待。

        返回 None = 本进程再没有轮需要这个节点的线程（调用方退出）。冻结期间**阻塞**
        而不是返回 None：rollout 结束后同一个线程继续服务，不重新孵化。冻结按课程
        过滤（§113）：A 课采集中只挡住 A 课的轮，B 课照常领牌。
        """
        idle_since: float | None = None
        while True:
            if stop is not None and stop.is_set():
                return None
            with self._cond:
                if self._shutdown or (stop is not None and stop.is_set()):
                    return None
                now = time.time()
                if any(r.wants(nid, now) for r in self._rounds):
                    idle_since = None
                    rounds = [
                        r for r in self._rounds if not self._frozen_for_locked(r.course_fp)
                    ]
                    grant = self._unique_grant(rounds, nid, now)
                    if grant is None:
                        grant = self._replica_grant(rounds, nid, now)
                    if grant is not None:
                        return grant
                    self._cond.wait(TAKE_TICK_SEC)
                    continue
                if idle_since is None:
                    idle_since = now
                elif now - idle_since >= IDLE_EXIT_SEC:
                    return None
                self._cond.wait(TAKE_TICK_SEC)

    def _unique_grant(
        self, rounds: list[EvalRound], nid: str, now: float
    ) -> Grant | None:
        n = len(rounds)
        for k in range(n):
            idx = (self._rr + k) % n
            grant = rounds[idx].claim_unique(nid, now)
            if grant is not None:
                # 游标是**绝对**的：`% n` 归一化会让「A 单独跑时 n=1」把游标永远压回 0，
                # B 中途登记后仍从 A 起扫 ⇒ A 一直抢到牌（用户口径 1 的公平性回归）。
                self._rr += k + 1
                return grant
        return None

    def _replica_grant(
        self, rounds: list[EvalRound], nid: str, now: float
    ) -> Grant | None:
        cands: list[tuple[int, int, int, EvalRound, tuple[int, int]]] = []
        for rnd in rounds:
            for task in rnd.replica_candidates(nid, now):
                cands.append((task[0], task[1], rnd.seq, rnd, task))
        if not cands:
            return None
        cands.sort(key=lambda it: (it[0], it[1], it[2]))
        total = len(cands)
        for k in range(total):
            stage, seed, _seq, rnd, task = cands[(self._rep + k) % total]
            grant = rnd.claim_replica(nid, task, now)
            if grant is not None:
                # 同 `_unique_grant`：绝对游标，候选集会随在飞局增减，`% total` 归一化
                # 会在 total 变化时把「下一位」重置回 0（同一张候选反复中选）。
                self._rep += k + 1
                return grant
        return None


_ARBITER = EvalArbiter()


def eval_arbiter() -> EvalArbiter:
    return _ARBITER


def reset_eval_arbiter() -> EvalArbiter:
    global _ARBITER
    _ARBITER.reset_for_tests()
    _ARBITER = EvalArbiter()
    return _ARBITER
