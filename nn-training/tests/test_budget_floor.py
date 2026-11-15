"""`tests/helpers/budget.sanity_floor` 的口径（存量基线类门禁的扫描入口自检地板）。

这条地板要回答的问题只有一个：**AST 还数得到东西吗**（walk 坏了 / 扫描面写错 / 包改名）。
它与「存量多少」无关，所以不能写死绝对值 —— 那样每次合法清理都得回填一个无判据可依的数字
（2026-11-15 一天里改了三回）。这里钉住派生式的三条性质：随基线走、永不是恒等式、空基线为 0。
"""

from __future__ import annotations

from tests.helpers.budget import SANITY_RATIO, sanity_floor


def test_floor_is_nine_tenths_of_the_baseline() -> None:
    assert SANITY_RATIO == 0.9
    assert sanity_floor({"a": 100, "b": 50}) == 135


def test_floor_is_strictly_below_the_baseline() -> None:
    """不是恒等式：少掉一成的扫描面仍然红（否则这条自检永远绿）。"""
    for baseline in ({"a": 1}, {"a": 10}, {"a": 100}, {"a": 100, "b": 1}):
        total = sum(baseline.values())
        assert 0 <= sanity_floor(baseline) < total


def test_floor_follows_the_baseline_down() -> None:
    """清理让基线变小 ⇒ 地板自动跟着变小，不需要任何人工回填。"""
    before = {"x": 40, "y": 10}  # 50
    after = {"x": 40}  # 40
    assert sanity_floor(after) < sanity_floor(before)
    assert sanity_floor(after) == 36


def test_floor_of_an_empty_baseline_is_zero() -> None:
    """空基线由各门禁自己的「锚文件非零」断言兜住，地板本身不该 pretend 有存量。"""
    assert sanity_floor({}) == 0
