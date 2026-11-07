"""`tools/p0_auc_probe.py` 的契约（plan/classic35-path.plan.md §5 C4）。

钉住：
  · 标签只用 `outcome + end_tick`，非基地毁局全负；
  · 消融切片精确对应 `POLICY_EXTRA[25:37]`；
  · 训练抽样保留全部正步、负步比例、每局权重归一；
  · fold 按局分层且不重叠；AUC / AP / 判决映射可复算。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_p0_auc_probe_under_test", NN_ROOT / "tools" / "p0_auc_probe.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load()


def _game(
    stage: int, seed: int, n: int = 100, positives: int = 0, window: int = 50
) -> Any:
    labels = {w: np.zeros(n, dtype=bool) for w in C.WINDOWS}
    if positives:
        labels[window][-positives:] = True
    return C.GameRows(
        stage=stage,
        seed=seed,
        x=np.arange(n * 185, dtype=np.float32).reshape(n, 185) / 1000.0,
        value=np.zeros(n, dtype=np.float32),
        labels=labels,
    )


def test_labels_for_uses_end_tick_window_only() -> None:
    got = C.labels_for("base_destroyed", np.array([0, 100, 150, 200]), 200.0)
    assert got[50].tolist() == [False, False, True, True]
    assert got[100].tolist() == [False, True, True, True]
    assert got[200].tolist() == [True, True, True, True]
    other = C.labels_for("stage_clear", np.array([0, 100, 150, 200]), 200.0)
    assert not any(y.any() for y in other.values())


def test_threat_slice_maps_to_extra_tail() -> None:
    sl = C.threat_slice(185)
    assert (sl.start, sl.stop) == (128 + 25, 128 + 37)
    assert sl.stop - sl.start == 12


def test_sample_episode_keeps_all_positive_and_normalizes_per_game() -> None:
    g = _game(1, 1, n=100, positives=5)
    x, y, w = C.sample_episode(g, 50, 0.03, np.random.default_rng(0))
    assert int(y.sum()) == 5
    assert len(x) == 5 + 3
    assert abs(float(w.sum()) - 1.0) < 1e-6
    assert np.allclose(x[:5], g.x[-5:])


def test_sample_episode_ablation_zeroes_12_columns() -> None:
    g = _game(1, 1, n=100, positives=5)
    sl = C.threat_slice(g.x.shape[1])
    x, _y, _w = C.sample_episode(g, 50, 0.03, np.random.default_rng(0), ablate=sl)
    assert np.all(x[:, sl] == 0.0)


def test_make_folds_is_grouped_and_stratified() -> None:
    games = [_game(1, i, positives=3 if i < 5 else 0) for i in range(10)]
    folds = C.make_folds(games, 5, seed=7)
    assert sorted(folds.tolist()) == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    for k in range(5):
        members = [i for i, f in enumerate(folds) if f == k]
        assert len(members) == 2
        assert any(i < 5 for i in members)
        assert any(i >= 5 for i in members)


def test_auc_and_average_precision_known_values() -> None:
    scores = np.array([0.1, 0.2, 0.8, 0.9])
    labels = np.array([0, 0, 1, 1], dtype=bool)
    assert C.auc_score(scores, labels) == 1.0
    assert C.average_precision(scores, labels) == 1.0
    scores2 = np.array([0.9, 0.8, 0.7])
    labels2 = np.array([1, 0, 1], dtype=bool)
    assert abs(C.average_precision(scores2, labels2) - (1.0 + 2.0 / 3.0) / 2.0) < 1e-12
    assert np.isnan(C.auc_score(scores2, np.zeros(3, dtype=bool)))


def test_fit_logistic_learns_separable_direction() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(300, 4))
    y = (x[:, 0] + x[:, 1] > 0).astype(bool)
    probe = C.fit_logistic(x, y, np.ones(len(y)), max_iter=80, seed=1)
    assert C.auc_score(probe.predict(x), y) > 0.9


def test_verdict_mapping() -> None:
    assert C.probe_verdict(0.90, 0.70) == "signal_in_base_12d"
    assert C.probe_verdict(0.90, 0.88) == "redundant_signal"
    assert C.probe_verdict(0.55, 0.50) == "no_linear_signal"
    assert C.critic_verdict(0.90, 0.87) == "critic_ranks_near_probe"
    assert C.critic_verdict(0.90, 0.55) == "critic_underuses_signal"
