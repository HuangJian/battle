"""tests/common/test_stats.py —— `common.stats` 的口径守卫（plan/nn-training-reuse-and-deps.plan.md §S2）。

这一族只有一个函数，但它的**边界**就是全部内容：`bool` 与「非数值」是两条独立的剔除条件，
少一条就会让 `"capped": true` 之类字段变成 `1.0` 悄悄混进分位/均值（账本里的静默漂移）。
第二条用例钉住**收敛本身**：两个调用方必须是同一个对象（转发成了副本 = 口径又开始漂）。
"""

from __future__ import annotations

from common.stats import num_or_none


def test_rejects_bool_even_though_it_is_an_int_subclass() -> None:
    """`bool` 是 `int` 的子类 —— 必须显式剔除（`isinstance(True, int) is True`）。"""
    assert num_or_none(True) is None
    assert num_or_none(False) is None


def test_passes_numbers_through_as_float() -> None:
    assert num_or_none(3) == 3.0
    assert isinstance(num_or_none(3), float)
    assert num_or_none(-2.5) == -2.5
    assert num_or_none(0) == 0.0


def test_rejects_non_numeric_and_none() -> None:
    assert num_or_none("3") is None
    assert num_or_none(None) is None
    assert num_or_none([1]) is None
    assert num_or_none({"v": 1}) is None


def test_non_finite_is_still_a_number() -> None:
    """NaN/±inf 属数值 ⇒ **原样透传**（非有限判定是调用方的事，见 `loop_guards_trip._is_nonfinite`）。"""
    nan = float("nan")
    assert num_or_none(nan) != num_or_none(nan)
    assert num_or_none(float("inf")) == float("inf")


def test_both_consumers_share_the_single_implementation() -> None:
    """两个调用方指向**同一个对象**（薄转发名，不是各自一份副本）。"""
    from worker import loop_guards_trip, train_ledger

    assert loop_guards_trip._num_or_none is num_or_none
    assert train_ledger._num is num_or_none
