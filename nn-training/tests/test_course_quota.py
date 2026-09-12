"""test_course_quota.py — P4-W1：本机并发配额按课程热读（纯函数）。

plan: `plan/multi-course-parallel-training.md`（P4-W1 / §3.4 / C1）。

覆盖：
- `courses.<课>.workers` 覆盖课程声明值，并产生 DoD 断言的响亮行；
- `courses.<课>.local_slots` 优先于 `rl.local_slots`；无课程/无 `courses` 块回退；
- 0 是合法值（语义 = 关闭该课本机直跑）；
- 值未变化时不产生响亮行（不刷屏）。
"""

from __future__ import annotations

import sys
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from rl.config import resolve_course_quota


def _cfg(rl: dict | None = None, courses: dict | None = None) -> dict:
    return {"rl": rl or {}, "courses": courses or {}}


def test_course_workers_override_is_loud() -> None:
    """课程声明 8、机器切分 4 → 响亮行（DoD 断言的确切形状）。"""
    w, ls, loud = resolve_course_quota(
        _cfg({"workers": 8, "local_slots": 10}, {"s-dodge": {"workers": 4}}),
        "s-dodge",
        8,
        10,
    )
    assert (w, ls) == (4, 10)
    assert loud == "[quota] workers 8 -> 4 (multi-course split)"


def test_course_local_slots_preferred_over_rl() -> None:
    w, ls, loud = resolve_course_quota(
        _cfg({"local_slots": 10}, {"a": {"local_slots": 3}}), "a", 8, 10
    )
    assert (w, ls, loud) == (8, 3, None)


def test_rl_local_slots_fallback_without_courses_block() -> None:
    w, ls, loud = resolve_course_quota(_cfg({"local_slots": 6}), "", 8, 10)
    assert (w, ls, loud) == (8, 6, None)


def test_unknown_course_falls_back_to_rl() -> None:
    w, ls, _ = resolve_course_quota(
        _cfg({"local_slots": 7}, {"a": {"local_slots": 2, "workers": 2}}), "b", 8, 10
    )
    assert (w, ls) == (8, 7)


def test_zero_is_a_legal_quota() -> None:
    """0 = 关闭该课本机直跑（不参与 eff），必须被照常接受。"""
    w, ls, _ = resolve_course_quota(_cfg({}, {"a": {"workers": 0, "local_slots": 0}}), "a", 4, 5)
    assert (w, ls) == (0, 0)


def test_same_value_is_not_loud() -> None:
    """值未变化时不产生响亮行（每轮热读，避免刷屏）。"""
    _, _, loud = resolve_course_quota(_cfg({}, {"a": {"workers": 4}}), "a", 4, 0)
    assert loud is None


def test_missing_dist_cfg_is_noop() -> None:
    assert resolve_course_quota(None, "", 8, 10) == (8, 10, None)
