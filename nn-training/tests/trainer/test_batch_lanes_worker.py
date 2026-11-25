"""`_UnitLanes.worker` 四段的行为守卫（engineering §346，2026-11-25）。

入口只剩调度；判据拆在 `_lane_claim` / `_lane_fetch` / `_lane_accept` / `_lane_fail`。
这里钉四件事：

1. 熔断先于领活；领活只在窗口内发生，attempts 只在那里 +1；
2. 竞速副本不消耗 attempt（`setdefault` 而非 `+1`）；
3. 胜者只落盘一次，second copy 走 dup、不写账；settled 满即置 `all_done` 并断连；
4. 瞬断只重排（退避、attempt 回退），硬失败累 streak 并在上限熔断。

不读源码，全部走真实方法 + 手装状态（`__new__`，同 `test_batch_lanes_split` 的 `_lanes`）。
"""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest

import trainer.batch_lanes as bl


def _lanes() -> bl._UnitLanes:
    lanes = bl._UnitLanes.__new__(bl._UnitLanes)
    lanes.lock = threading.Lock()
    lanes.streaks = {}
    lanes.fail_streak_max = 2
    lanes.pending = deque()
    lanes.attempts = {}
    lanes.inflight = {}
    lanes.inflight_nodes = {}
    lanes.seen = set()
    lanes.writers = [0]
    lanes.dup_settles = [0]
    lanes.all_done = threading.Event()
    lanes.total = 2
    lanes.busy_tries = {}
    lanes.busy_retry_limit = 2
    lanes.busy_backoff_sec = 0.5
    lanes.node_soft_fails = {}
    lanes.node_hard_fails = {}
    lanes.recover_ping_sec = 20.0
    lanes.in_flight = [0]
    lanes.trace = False
    lanes.req_scope = "test"
    lanes.deadline = time.time() + 60
    return lanes


def test_claim_stops_before_touching_a_tripped_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    lanes = _lanes()
    lanes.streaks["n1"] = lanes.fail_streak_max
    lane: dict = {}
    pick = lanes._lane_claim("n1", lane)
    assert pick.stop is True and pick.task is None
    assert lane["tripped"] is True
    # 本机槽位不参与节点熔断（lane["tripped"] 只给 supervise 看）
    lanes.streaks["local"] = lanes.fail_streak_max
    local: dict = {}
    pick = lanes._lane_claim("local", local)
    assert pick.stop is True and local == {}


def test_claim_counts_an_attempt_only_for_a_real_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl._UnitLanes, "window_open", lambda self: True)
    lanes = _lanes()
    lanes.pending.append((0, 1))
    pick = lanes._lane_claim("n1", {})
    assert pick.task == (0, 1) and pick.stop is False and pick.race_copy is False
    assert lanes.attempts == {(0, 1): 1}
    assert lanes.inflight == {(0, 1): 1}
    assert lanes.inflight_nodes == {(0, 1): {"n1"}}

    clone = _lanes()
    clone.pending.append((0, 2))
    pick = clone._lane_claim("local", {})
    assert pick.task == (0, 2) and pick.race_copy is False


def test_race_copy_does_not_consume_an_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl._UnitLanes, "window_open", lambda self: True)
    lanes = _lanes()
    lanes.inflight = {(0, 1): 1}
    lanes.inflight_nodes = {(0, 1): {"n0"}}
    pick = lanes._lane_claim("n1", {})
    assert pick.task == (0, 1) and pick.race_copy is True
    assert lanes.inflight[(0, 1)] == 2
    assert lanes.inflight_nodes[(0, 1)] == {"n0", "n1"}
    assert lanes.attempts == {(0, 1): 1}, "竞速副本只能 setdefault，不能再 +1"


def test_accept_records_the_winner_once_and_dedups_the_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, tuple[int, int]]] = []
    monkeypatch.setattr(
        bl._UnitLanes,
        "record",
        lambda self, manifest, nd_id, task: recorded.append((nd_id, task)),
    )
    lanes = _lanes()
    task = (0, 1)
    manifest = {"win": 1}
    lanes.inflight = {task: 2}
    lanes.inflight_nodes = {task: {"n1", "n2"}}
    accepted = lanes._lane_accept("n1", task, manifest, 0.0)
    assert accepted is True
    assert recorded == [("n1", task)]
    assert lanes.seen == {task} and lanes.writers[0] == 1

    # 第二份（竞速败者）不再落盘，只记 dup
    accepted = lanes._lane_accept("n2", task, manifest, 0.0)
    assert accepted is True
    assert recorded == [("n1", task)]
    assert lanes.dup_settles[0] == 1
    assert lanes.inflight == {}


def test_accept_marks_all_done_and_aborts_on_the_last_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    def _abort(scope: str) -> int:
        closed.append(scope)
        return 3

    monkeypatch.setattr(bl.common.distribution, "abort_active_requests", _abort)
    monkeypatch.setattr(bl._UnitLanes, "record", lambda self, manifest, nd_id, task: None)
    lanes = _lanes()
    lanes.total = 1
    task = (0, 1)
    accepted = lanes._lane_accept("n1", task, {"win": 1}, 0.0)
    assert accepted is True
    assert lanes.all_done.is_set() is True
    assert closed == ["test"]


def test_transient_failure_requeues_without_burning_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(bl, "log", logs.append)
    lanes = _lanes()
    task = (0, 1)
    lanes.streaks["n1"] = 1
    lanes.inflight = {task: 1}
    lanes.inflight_nodes = {task: {"n1"}}
    lanes.attempts[task] = 3
    sleep_for = lanes._lane_fail("n1", {}, task, "503 busy", True, 3)
    assert sleep_for == 0.5
    assert list(lanes.pending) == [task]
    assert lanes.attempts[task] == 2, "背压要回退 popleft 时累加的那一次"
    assert lanes.busy_tries == {task: 1}
    assert lanes.node_soft_fails == {"n1": 1}
    assert lanes.streaks == {"n1": 1}, "瞬断不算节点失败，不累 streak"
    assert len(logs) == 1
    if "背压" not in logs[0]:
        raise AssertionError(logs[0])


def test_hard_failure_trips_the_lane_at_the_streak_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(bl, "log", logs.append)
    lanes = _lanes()
    task = (0, 1)
    lanes.streaks["n1"] = 1
    lane: dict = {}
    lanes.inflight = {task: 1}
    lanes.inflight_nodes = {task: {"n1"}}
    sleep_for = lanes._lane_fail("n1", lane, task, "boom", False, bl.EVAL_TASK_ATTEMPTS)
    assert sleep_for == 0.0
    assert lanes.streaks == {"n1": 2}
    assert lanes.node_hard_fails == {"n1": 1}
    assert lane["tripped"] is True
    assert list(lanes.pending) == [], "attempt 已满 ⇒ 不重排"
    if not any("连续 2 次真失败" in m for m in logs):
        raise AssertionError(logs)
