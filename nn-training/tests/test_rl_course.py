"""rl/course.py — pure-function tests (no torch, no bun).

Determinism invariants + curriculum progression rules.
"""
from __future__ import annotations

from rl.course import build_pairs, parse_range, curriculum_active_count

from tests.conftest import bp_args


def test_parse_range() -> None:
    assert parse_range("0-3") == [0, 1, 2, 3]
    assert parse_range("0,2,5") == [0, 2, 5]
    assert parse_range("0-1,4") == [0, 1, 4]
    assert parse_range("7") == [7]


def test_build_pairs_deterministic() -> None:
    a = build_pairs(bp_args(3), 60, 1787503550)
    b = build_pairs(bp_args(3), 60, 1787503550)
    assert a == b and len(a) == 105  # 35 * 3 seeds


def test_build_pairs_differs_by_it() -> None:
    a = build_pairs(bp_args(3), 60, 1787503550)
    c = build_pairs(bp_args(3), 61, 1787503550)
    assert a != c


def test_build_pairs_sps_overlap() -> None:
    # sps 4->3 only changes per-stage seed window overlap by exactly 6 pairs.
    old = build_pairs(bp_args(4), 60, 1787503550)
    new = build_pairs(bp_args(3), 60, 1787503550)
    inter = set(old) & set(new)
    assert len(inter) == 6, f"expected overlap 6, got {len(inter)}"


def test_curriculum_progression() -> None:
    # start=4, grow=4, every=8: it1 -> 4, it8 -> 4, it9 -> 8, it17 -> 12, caps at total=35.
    assert curriculum_active_count(35, 1, 4, 8, 4) == 4
    assert curriculum_active_count(35, 8, 4, 8, 4) == 4
    assert curriculum_active_count(35, 9, 4, 8, 4) == 8
    assert curriculum_active_count(35, 17, 4, 8, 4) == 12


def test_curriculum_frozen() -> None:
    # every<=0 -> never grow.
    for it in (1, 5, 50):
        assert curriculum_active_count(35, it, 4, 0, 4) == 4
        assert curriculum_active_count(35, it, 4, -1, 4) == 4
