"""eval 尾段竞速副本上限（`worker/eval_arbiter.MAX_COPIES_PER_TASK`）。

旧行为：`pending` 清空后，每个空闲线程都可以对任意在飞局领一份副本；副本数
随空槽数增长。48 个空槽对着 1 局残局，会把它完整重跑 47 次。

新判据：同一任务最多 2 份在飞（主副本 + 1 份竞速）。`replica_candidates` 与
`claim_replica` 同判据——前者选靶，后者在选中后二次校验（候选集与领取之间
可能有别的线程先领走一份）。
"""

from __future__ import annotations

import threading
import time

from worker.eval_arbiter import MAX_COPIES_PER_TASK, EvalRound, RoundClock

TASK = (1, 7)


def _round(copies: int) -> EvalRound:
    """最小可用的 EvalRound：只装 `replica_candidates` / `claim_replica` 要读的字段。"""
    rnd = object.__new__(EvalRound)
    rnd.closed = False
    rnd.stop_grants = False
    rnd.settled = threading.Event()
    rnd.clock = RoundClock(60.0, 10.0)
    rnd.clock.arm(time.time(), paused=False)
    rnd.nodes = {"n1": {}, "n2": {}}
    rnd.node_caps = {"n1": 1, "n2": 1}
    rnd.node_inflight = {}
    rnd.fail_streak_max = 3
    rnd.soft_streak_max = 5
    rnd.streaks = {}
    rnd.soft_streaks = {}
    rnd.inflight = {TASK: copies}
    rnd.inflight_nodes = {TASK: {"n1"}}
    rnd.attempts = {}
    rnd.leg_tries = {}
    rnd._grant_seq = 0
    rnd._lock = threading.Lock()
    rnd._log = lambda _m: None
    return rnd


def test_replica_candidates_stop_at_the_cap() -> None:
    now = time.time()
    at_cap = _round(MAX_COPIES_PER_TASK)
    assert at_cap.replica_candidates("n2", now) == [], (
        "已经到副本上限的任务不得再被选为竞速靶"
    )

    below_cap = _round(MAX_COPIES_PER_TASK - 1)
    assert below_cap.replica_candidates("n2", now) == [TASK]


def test_claim_replica_checks_the_cap_again() -> None:
    """候选列表与领取之间有窗口：claim 必须二次校验，不能只信 candidates。"""
    now = time.time()
    rnd = _round(MAX_COPIES_PER_TASK)
    assert rnd.claim_replica("n2", TASK, now) is None

    rnd.inflight[TASK] = MAX_COPIES_PER_TASK - 1
    grant = rnd.claim_replica("n2", TASK, now)
    assert grant is not None and grant.replica is True
    assert rnd.inflight[TASK] == MAX_COPIES_PER_TASK
