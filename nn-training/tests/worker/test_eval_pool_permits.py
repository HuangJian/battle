"""B1：eval 池按许可缩容 + claim 扣还节点 `c`（2026-11-10）。

旧行为：`EvalExecutors._ensure` 只增不缩；`EvalRound.claim_unique` 只看
`participates`，不看节点当前允许的并发 `c`。节点曾按 96 槽开池、后来 inflight
升高/课程并发调小，旧线程仍在，解冻后按 96 条抢任务 ⇒ 过订 + 503。

本文件钉住两条新判据：
* 池子目标按 owner（一轮 eval）取 max，缩容只置退出信号；
* claim 先扣本节点许可，settle 时归还；无许可时任务留在队列。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import common.distribution
from worker.eval_arbiter import EvalRound, RoundClock
from worker.eval_exec import EvalExecutors
from worker.eval_self_rescue import grow_pools


def _wait_until(pred: Any, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)  # sleep-ok: 轮询步长
    return bool(pred())


class _HoldArbiter:
    """不领牌，只等退出信号；用来稳定观察池线程数而不依赖真实仲裁器。"""

    def take(self, nid: str, stop: threading.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            time.sleep(0.005)  # sleep-ok: 轮询步长
        return None


def _round(cap: int) -> EvalRound:
    """最小可用的 EvalRound：只装 claim / settle 要读的字段。"""
    rnd = object.__new__(EvalRound)
    rnd.closed = False
    rnd.stop_grants = False
    rnd.settled = threading.Event()
    rnd.clock = RoundClock(60.0, 10.0)
    rnd.clock.arm(time.time(), paused=False)
    rnd.nodes = {"n1": {"id": "n1", "c": cap}}
    rnd.node_caps = {"n1": cap}
    rnd.node_inflight = {}
    rnd.fail_streak_max = 3
    rnd.soft_streak_max = 5
    rnd.streaks = {}
    rnd.soft_streaks = {}
    rnd.pending = deque([(0, 1), (0, 2), (0, 3)])
    rnd.redispatched = set()
    rnd.retry_at = {}
    rnd.inflight = {}
    rnd.inflight_nodes = {}
    rnd.attempts = {}
    rnd.leg_tries = {}
    rnd._grant_seq = 0
    rnd._done_grants = set()
    rnd._lock = threading.Lock()
    rnd._log = lambda _m: None
    rnd.local_slots = 0
    rnd.local_stopped = threading.Event()
    rnd.local_gate = None
    rnd.reserved = 0
    rnd.attempts_max = 1
    rnd.attempts_transient_max = 1
    rnd._fail_recorder = lambda *_a, **_k: None
    rnd.meta_path = Path("unused")
    rnd.it = 1
    rnd.seen = set()
    rnd.writers = 0
    return rnd


def test_pool_shrinks_when_want_drops() -> None:
    ex = EvalExecutors(arbiter=_HoldArbiter())
    nd = {"id": "n1", "c": 4}
    assert ex.ensure_node(nd, 4) == 4
    assert _wait_until(lambda: ex.live("n1") == 4)

    assert ex.ensure_node({**nd, "c": 2}, 2) == 0
    assert _wait_until(lambda: ex.live("n1") <= 2), "缩容后多余的池线程必须退出"
    ex.close_pools()


def test_pool_uses_max_owner_want_and_releases() -> None:
    ex = EvalExecutors(arbiter=_HoldArbiter())
    a, b = object(), object()
    nd = {"id": "n1", "c": 4}
    assert ex.ensure_node(nd, 4, owner=a) == 4
    assert ex.ensure_node(nd, 2, owner=b) == 0
    assert _wait_until(lambda: ex.live("n1") == 4), "另一轮仍要 4 槽时不得被缩到 2"

    ex.release_owner(a)
    assert _wait_until(lambda: ex.live("n1") <= 2)
    ex.release_owner(b)
    assert _wait_until(lambda: ex.live("n1") == 0)
    ex.close_pools()


def test_claim_unique_stops_at_node_cap_and_settle_returns_permit() -> None:
    now = time.time()
    rnd = _round(2)
    g1 = rnd.claim_unique("n1", now)
    g2 = rnd.claim_unique("n1", now)
    assert g1 is not None and g2 is not None
    assert rnd.node_inflight["n1"] == 2
    assert rnd.claim_unique("n1", now) is None
    assert rnd.pending, "无许可时任务必须留在队列"

    assert rnd.settle_task(g1, ok=True) is True
    assert rnd.node_inflight["n1"] == 1
    g3 = rnd.claim_unique("n1", now)
    assert g3 is not None and g3.task == (0, 3), "结算归还许可后应继续取队首"


def test_claim_replica_also_respects_node_cap() -> None:
    now = time.time()
    rnd = _round(1)
    rnd.nodes["n2"] = {"id": "n2", "c": 1}
    rnd.node_caps["n2"] = 1
    first = rnd.claim_unique("n1", now)
    assert first is not None
    rnd.node_inflight["n2"] = 1
    assert rnd.replica_candidates("n2", now) == []
    assert rnd.claim_replica("n2", first.task, now) is None

    rnd.node_inflight["n2"] = 0
    replica = rnd.claim_replica("n2", first.task, now)
    assert replica is not None and replica.replica is True


class _GrowRnd:
    def __init__(self) -> None:
        self.it = 1
        self.caps: list[tuple[str, int]] = []

    def yielded_nodes(self) -> list[dict[str, Any]]:
        return [{"id": "n1", "url": "http://n1", "key": "", "c": 0, "baseC": 4}]

    def node_routable(self, nid: str, _now: float) -> bool:
        return nid == "n1"

    def pending_len(self) -> int:
        return 0

    def set_node_cap(self, nid: str, cap: int) -> None:
        self.caps.append((nid, cap))

    def log(self, _msg: str) -> None:
        pass


class _GrowExecs:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, Any]] = []

    def active(self, _nid: str) -> int:
        return 4

    def ensure_node(self, nd: dict, want: int, owner: Any = None) -> int:
        self.calls.append((int(nd["c"]), want, owner))
        return 0


def test_grow_pools_trims_when_live_slots_drop(monkeypatch) -> None:
    """ping 看到别的腿占走 3 槽 ⇒ want 从 4 降到 1，ensure 必须收到 1（缩容）。"""
    rnd: Any = _GrowRnd()
    execs: Any = _GrowExecs()
    monkeypatch.setattr(
        common.distribution,
        "ping_nodes_parallel",
        lambda nodes, timeout: [{"evalSupport": True, "inflight": 7}],
    )
    assert grow_pools(rnd, execs) == 0
    assert rnd.caps == [("n1", 1)]
    assert execs.calls == [(1, 1, rnd)]
