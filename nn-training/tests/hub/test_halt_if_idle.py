"""hub 的「干完才停」：BC 课收官时还有别的活，就先不下发停机达令（2026-11-12 用户口径）。

用户口径：一个 BC 课训练结束后，hub 要先判断**是否仍有其它 BC 课程或 PPO job 待领**，
都没有时才给云机下发停机命令；`halt_if_idle` 被挡住时只置门闩，等下一次空闲取活重评。

本文件钉三件事：① 还有待领/在飞 job ⇒ 暂缓（`deferred`）且**不**置停机；
② 还有别的已开课未收官课 ⇒ 暂缓；③ 门闩在活清完后由 `maybe_halt_when_idle` 收口。
"""

from __future__ import annotations

import json
from pathlib import Path

from hub.queue import _HubQueue
from hub.store import _JobStore


def _hub(tmp_path: Path, courses: tuple[str, ...] = ("a", "b")) -> _HubQueue:
    stores = {
        c: _JobStore(tmp_path / c / "remote-jobs", tmp_path / c / "training_log.jsonl")
        for c in courses
    }
    return _HubQueue(stores, order=list(courses))


def _ledger(tmp_path: Path, course: str, events: list[dict]) -> None:
    p = tmp_path / course / "training_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _enabled(tmp_path: Path, course: str) -> None:
    (tmp_path / course).mkdir(parents=True, exist_ok=True)
    (tmp_path / course / "training-enabled.txt").write_text("1", encoding="utf-8")


def test_nothing_left_halts_immediately(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    ok, reason = hub.halt_if_idle("a")
    assert ok is True, reason
    assert hub.all_halted() is True


def test_pending_job_defers_and_latch_fires_when_cleared(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    st = hub._stores["b"]
    original = st.claimable_job_ids
    st.claimable_job_ids = lambda: ["j1"]  # type: ignore[method-assign]
    ok, reason = hub.halt_if_idle("a")
    assert ok is False and reason == "b 还有 1 个待领 job"
    assert hub.all_halted() is False
    # 活干完（PPO job 被领走并回传）⇒ 下一次空闲取活时门闩收口
    st.claimable_job_ids = original  # type: ignore[method-assign]
    assert hub.maybe_halt_when_idle() is True
    assert hub.all_halted() is True


def test_other_enabled_unfinished_course_defers(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _enabled(tmp_path, "b")
    _ledger(tmp_path, "b", [{"event": "run_start", "epochs": 20}])
    ok, reason = hub.halt_if_idle("a")
    assert ok is False and reason == "b 已开课且未收官"
    # b 收官（run_complete 在 run_start 之后）⇒ 不再挡
    _ledger(
        tmp_path,
        "b",
        [{"event": "run_start", "epochs": 20}, {"event": "run_complete", "it": 1}],
    )
    assert hub.halt_if_idle("a")[0] is True


def test_requesting_course_is_excluded(tmp_path: Path) -> None:
    """本课自己的 run_complete 还没写（BC 收官调用早于落账）——不得挡住自己的停机申请。"""
    hub = _hub(tmp_path, courses=("a",))
    _enabled(tmp_path, "a")
    _ledger(tmp_path, "a", [{"event": "run_start", "epochs": 20}])
    assert hub.halt_if_idle("a")[0] is True
