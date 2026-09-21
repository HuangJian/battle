"""--rotate-seed 配对旗标（2026-09-20，x20-clutch 配对比较要求）。

resolve_rotate_seed 三级优先级（显式 > 继承 > 抖动）+ 同 rotateSeed ⇒
同一种子流（配对比较 / McNemar 前提）。纯函数，无 torch。
"""
from __future__ import annotations

import types

from rl.course import build_pairs, resolve_rotate_seed


def test_explicit_beats_inherited() -> None:
    v, src = resolve_rotate_seed(7, 12345, 999, 1700000000)
    assert (v, src) == (12345, "explicit")


def test_explicit_zero_is_valid() -> None:
    # 0 是合法覆盖值，不得按缺失处理。
    v, src = resolve_rotate_seed(7, 0, 999, 1700000000)
    assert (v, src) == (0, "explicit")


def test_inherited_preserved_without_override() -> None:
    v, src = resolve_rotate_seed(7, None, 999, 1700000000)
    assert (v, src) == (999, "inherited")


def test_jitter_matches_legacy_formula() -> None:
    # 无覆盖无继承 = 旧抖动公式逐字节不变（固定 now 下可断言）。
    v, src = resolve_rotate_seed(7, None, None, 1700000000)
    assert (v, src) == ((7 * 1009 + 1 + 1700000000) % 2**32, "jitter")


def _x20_args() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        stages="2000-2003",
        seeds="0-3",
        seed_rotate=150,
        seeds_per_stage=10,
        rotate_stages=0,
        curriculum_stages="",
        curriculum_start=4,
        curriculum_every=8,
        curriculum_grow=4,
    )


def test_same_rotate_seed_same_stream() -> None:
    a = _x20_args()
    p1 = build_pairs(a, 76, 424242)
    p2 = build_pairs(a, 76, 424242)
    assert p1 == p2 and len(p1) == 4 * 150


def test_different_rotate_seed_different_stream() -> None:
    a = _x20_args()
    assert build_pairs(a, 76, 424243) != build_pairs(a, 76, 424242)
