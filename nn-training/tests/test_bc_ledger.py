"""rl/bc_ledger.py —— BC 轮账本的读/写面（R3-4；BC 指针与 RL **不同源**）。

钉的是「指针语义只有一份」这件事：单进程 supervisor 与 BC 编排器都必须从
`bc_round_completed` 读「下一轮是几」，而不是从 RL 的 `iteration` 事件推断。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.bc_ledger import (
    JOB_DONE_EVENT,
    ROUND_DONE_EVENT,
    append_ledger,
    bc_progress,
    completed_jobs,
    completed_rounds,
    inflight_jobs,
    read_events,
)


def _write(path: Path, events: list[object]) -> Path:
    lines: list[str] = []
    for e in events:
        lines.append(e if isinstance(e, str) else json.dumps(e))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_append_ledger_creates_parents_and_appends(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "training_log.jsonl"
    append_ledger(p, {"event": ROUND_DONE_EVENT, "it": 1})
    append_ledger(p, {"event": ROUND_DONE_EVENT, "it": 2})
    assert p.read_text(encoding="utf-8").count("\n") == 2
    assert [e["it"] for e in read_events(p)] == [1, 2]


def test_read_events_skips_junk_and_half_written_lines(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "j.jsonl",
        [{"event": "a"}, "not json", "", '{"event": "b", '],
    )
    assert [e["event"] for e in read_events(p)] == ["a"]


def test_read_events_of_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_events(tmp_path / "nope.jsonl") == []


def test_completed_rounds_counts_only_bc_round_completed(tmp_path: Path) -> None:
    """RL 的 `iteration` 事件**不算** BC 的一轮完成（混进判据就是跳轮）。"""
    p = _write(
        tmp_path / "l.jsonl",
        [
            {"event": "iteration", "iter": 9},
            {"event": ROUND_DONE_EVENT, "it": 1},
            {"event": ROUND_DONE_EVENT, "it": "bad"},  # 坏值不炸
            {"event": ROUND_DONE_EVENT},
            "not json",
        ],
    )
    assert completed_rounds(p) == {1}
    assert completed_rounds(tmp_path / "missing.jsonl") == set()


def test_completed_jobs_only_counts_job_completed(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "l.jsonl",
        [
            {"event": "job_pending", "job_id": "a"},
            {"event": JOB_DONE_EVENT, "job_id": "b"},
            {"event": JOB_DONE_EVENT, "job_id": 7},  # 非字符串忽略
        ],
    )
    assert completed_jobs(p) == {"b"}


def test_inflight_jobs_is_pending_minus_terminal(tmp_path: Path) -> None:
    """在飞 = `job_pending ∖ (job_completed ∪ job_cancelled)`：作废也算终局。

    只排除收口，会让被打扫掉的滞后 job 永远显示在飞（控制台于是永久等一个不会回来的结果）。
    """
    p = _write(
        tmp_path / "l.jsonl",
        [
            {"event": "job_pending", "job_id": "a", "it": 2},
            {"event": "job_completed", "job_id": "a"},
            {"event": "job_pending", "job_id": "b", "it": 1},
            {"event": "job_cancelled", "job_id": "b"},
            {"event": "job_pending", "job_id": "c", "it": 3},
        ],
    )
    assert [r["jid"] for r in inflight_jobs(p)] == ["c"]
    assert inflight_jobs(tmp_path / "missing.jsonl") == []


def test_inflight_jobs_keeps_jobs_with_unusable_it(tmp_path: Path) -> None:
    """`it` 缺失/非整数记 0 但**保留**——藏起一个真在跑的 job 比显示一条来历不明更坏。"""
    p = _write(
        tmp_path / "l.jsonl",
        [
            {"event": "job_pending", "job_id": "no-it"},
            {"event": "job_pending", "job_id": "bad-it", "it": "x"},
            {"event": "job_pending", "job_id": 7, "it": 1},
        ],
    )
    assert [(r["jid"], r["it"]) for r in inflight_jobs(p)] == [("bad-it", 0), ("no-it", 0)]


def test_inflight_jobs_is_sorted_by_round_then_jid(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "l.jsonl",
        [
            {"event": "job_pending", "job_id": "z", "it": 2},
            {"event": "job_pending", "job_id": "y", "it": 1},
            {"event": "job_pending", "job_id": "x", "it": 2},
        ],
    )
    assert [r["jid"] for r in inflight_jobs(p)] == ["y", "x", "z"]


def test_progress_without_iters_is_max_done_plus_one(tmp_path: Path) -> None:
    """轮数未知（只读 CLI 不为一个数字 import 课程配置）⇒ 指针 = 最大已完成 + 1。"""
    p = _write(tmp_path / "l.jsonl", [{"event": ROUND_DONE_EVENT, "it": 3}])
    prog = bc_progress(p, 0)
    assert (prog.next_it, prog.done, prog.iters) == (4, 1, 0)
    assert prog.finished is False  # 算不出的判据不得当成「跑完」


def test_progress_with_iters_returns_the_first_missing_round(tmp_path: Path) -> None:
    """轮数已知 ⇒ 指针 = 第一个**缺**的轮（有空档时不是「最大 + 1」——那会静默跳轮）。"""
    p = _write(
        tmp_path / "l.jsonl",
        [{"event": ROUND_DONE_EVENT, "it": 1}, {"event": ROUND_DONE_EVENT, "it": 3}],
    )
    assert bc_progress(p, 3).next_it == 2
    assert bc_progress(p, 3).completed == frozenset({1, 3})


def test_progress_finished_only_when_all_rounds_done(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "l.jsonl",
        [{"event": ROUND_DONE_EVENT, "it": 1}, {"event": ROUND_DONE_EVENT, "it": 2}],
    )
    prog = bc_progress(p, 2)
    assert prog.finished is True and prog.next_it == 3


def test_progress_of_empty_ledger_starts_at_one(tmp_path: Path) -> None:
    prog = bc_progress(tmp_path / "missing.jsonl", 4)
    assert (prog.next_it, prog.done, prog.finished) == (1, 0, False)
