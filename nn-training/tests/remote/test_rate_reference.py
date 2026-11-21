"""`remote/wire.RateReference`：坏签判据的参照速率（P0-5 对象化，2026-11-21）。

旧形状是两个 `global` 重绑的模块标量（`_BEST_RATE` 参照 + `_stale_samples` 迟滞计数）。
语义没变，但每一半各自成立与否现在都能单独钉住：

* 采样门槛（小 body 不参与判据）；
* **迟滞**：单个慢样本不覆盖更好值，连续 `stale_samples` 个起才衰减；
* **恢复瞬时**：一个更好的样本立刻把参照抬回新高、迟滞清零（不惩罚恢复）；
* 地板：`max(WIRE_MIN_RATE, 参照/4)`，只在「明显偏离近期水平」时动手。
"""

from __future__ import annotations

import pytest

from remote.wire import (
    WIRE_MIN_RATE,
    WIRE_RATE_DECAY,
    WIRE_RATE_SAMPLE_MIN_BYTES,
    WIRE_RATE_STALE_SAMPLES,
    RateReference,
)


def test_small_bodies_never_move_the_reference() -> None:
    """采样门槛：小于 `WIRE_RATE_SAMPLE_MIN_BYTES` 的 body 连迟滞计数都不该动。"""
    ref = RateReference()
    ref.sample(10_000_000.0, WIRE_RATE_SAMPLE_MIN_BYTES - 1)
    assert ref.level() == 0.0
    ref.sample(1.0, WIRE_RATE_SAMPLE_MIN_BYTES)  # 第一个真样本：抬到 1.0
    assert ref.level() == 1.0
    ref.sample(0.5, WIRE_RATE_SAMPLE_MIN_BYTES - 1)  # 小 body 的慢样本不该启动衰减
    for _ in range(WIRE_RATE_STALE_SAMPLES):
        ref.sample(0.5, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() < 1.0, "连续慢样本到达迟滞后仍应衰减"


def test_single_slow_sample_does_not_overwrite_a_better_one() -> None:
    """迟滞：一个更慢的样本不动参照（真机抖动下这是「白扔 256KB 重抽」的来源）。"""
    ref = RateReference()
    ref.sample(500.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    for _ in range(WIRE_RATE_STALE_SAMPLES - 1):
        ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == 500.0
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)  # 第 stale_samples 个：开始衰减
    assert ref.level() == pytest.approx(500.0 * WIRE_RATE_DECAY)


def test_decay_multiples_but_never_below_the_sample_itself() -> None:
    """每个额外的慢样本乘一次 `decay`，下限是样本速率本身（不跌到 0 或负数）。"""
    ref = RateReference()
    ref.sample(400.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == pytest.approx(200.0)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == pytest.approx(100.0)  # 200 * 0.5 == 样本本身
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == pytest.approx(100.0), "再衰减也不能低于本次样本速率"


def test_recovery_is_instant_and_resets_the_stale_counter() -> None:
    """恢复不惩罚：一个更好的样本立刻回到新高，且迟滞重新从头数。"""
    ref = RateReference()
    ref.sample(500.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() < 500.0
    ref.sample(700.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == 700.0
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES * 4)  # 迟滞已清零 ⇒ 单个慢样本不动
    assert ref.level() == 700.0


def test_floor_is_a_quarter_of_the_reference_and_never_below_the_absolute_min() -> None:
    ref = RateReference()
    assert ref.floor() == WIRE_MIN_RATE, "没采过样时地板就是绝对地板"
    ref.sample(WIRE_MIN_RATE * 8.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.floor() == pytest.approx(WIRE_MIN_RATE * 2.0)
    ref.clear_samples()
    assert ref.floor() == WIRE_MIN_RATE


def test_clear_samples_resets_the_counter_too() -> None:
    """清场必须两个都清：只清参照会把「迟滞已数了几个」带进下一次判据。"""
    ref = RateReference()
    ref.sample(500.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)  # 迟滞 1
    ref.clear_samples()
    assert ref.level() == 0.0
    ref.sample(600.0, WIRE_RATE_SAMPLE_MIN_BYTES)  # 迟到样本若还在，下一个样本就会被当「慢」
    ref.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert ref.level() == 600.0, "清场后迟滞计数必须归零"


def test_window_parameters_are_data_not_module_constants() -> None:
    """迟滞与衰减是构造参数（用例可以要「每个慢样本都衰减」/「永不衰减」）。"""
    impatient = RateReference(stale_samples=1, decay=0.0)
    impatient.sample(500.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    impatient.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert impatient.level() == pytest.approx(100.0)  # 衰减到 0 ⇒ 落到样本速率

    frozen = RateReference(stale_samples=1, decay=1.0)
    frozen.sample(500.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    frozen.sample(100.0, WIRE_RATE_SAMPLE_MIN_BYTES)
    assert frozen.level() == pytest.approx(500.0), "decay=1.0 = 永不衰减（只升）"
