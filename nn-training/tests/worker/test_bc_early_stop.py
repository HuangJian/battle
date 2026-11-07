"""B3：BC 早停判据（滑动平均 val_loss，不用单点新低）。

单点 val_loss 的抖动由 fire 头噪声主导（report §2：与 fire_acc 相关 −0.965），
「新低」≈ 噪声抽签。`early_stop_epoch` 是纯函数，这里钉住它的语义：
  · 0 = 关；
  · 单调下降 ⇒ 不停；
  · 平盘 patience 个 epoch ⇒ 停；
  · 一次真实改善会把计时重置；
  · 小于 min_delta 的抖动不算改善（否则平盘永不停）。
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from worker.train.bc import early_stop_epoch, select_best_epoch, smoothed_val_loss


def test_patience_zero_never_stops() -> None:
    assert early_stop_epoch([1.0] * 20, 0) is None


def test_monotone_improvement_never_stops() -> None:
    losses = [2.4 - 0.01 * i for i in range(30)]
    assert early_stop_epoch(losses, 3) is None


def test_plateau_stops_after_patience() -> None:
    # 第 1 个是最佳；之后平盘 ⇒ 第 1+patience 个 epoch 停。
    losses = [1.0] * 12
    assert early_stop_epoch(losses, 3) == 4


def test_real_improvement_resets_the_clock() -> None:
    # 1..5 缓降，6..12 平盘：最佳平滑值出现在 5，停 = 5+patience。
    losses = [2.0, 1.9, 1.8, 1.7, 1.6] + [1.6] * 7
    stop = early_stop_epoch(losses, 2)
    assert stop is not None and stop >= 7


def test_sub_delta_jitter_is_not_improvement() -> None:
    # 每步只降 1e-6（< min_delta）：视为平盘，仍会早停。
    losses = [1.0 - 1e-6 * i for i in range(20)]
    assert early_stop_epoch(losses, 3) is not None


def test_smoothed_uses_recent_window_only() -> None:
    assert smoothed_val_loss([9.0, 1.0, 2.0, 3.0], window=3) == pytest.approx(2.0)
    assert smoothed_val_loss([1.0, 2.0], window=5) == pytest.approx(1.5)


def test_select_best_epoch_needs_a_full_window() -> None:
    # 窗口未满 ⇒ None（调用方交最后一轮，不退回单点 min）。
    assert select_best_epoch([1.0, 0.5, 0.4, 0.3], window=5) is None
    assert select_best_epoch([1.0, 0.5, 0.4, 0.3, 0.2], window=5) == 5


def test_select_best_epoch_ignores_single_point_spike() -> None:
    # 单点最低在第 4 轮，但第 6 轮是尖峰：平滑窗口把交付点留在 5（原始 argmin 会选 4）。
    losses = [3.2, 3.1, 3.0, 2.9, 3.0, 9.0]
    assert min(range(len(losses)), key=losses.__getitem__) + 1 == 4
    assert select_best_epoch(losses, window=5) == 5


def test_select_best_epoch_is_not_raw_argmin() -> None:
    # 原始最低在第 1 轮；平滑最低在第 5 轮 ⇒ 交付点跟着平滑曲线走。
    losses = [1.0, 2.0, 2.0, 2.0, 2.0, 1.9]
    assert min(range(len(losses)), key=losses.__getitem__) + 1 == 1
    assert select_best_epoch(losses, window=5) == 5
