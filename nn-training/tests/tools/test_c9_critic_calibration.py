"""`tools/c9_critic_calibration.py` 的算术契约（plan/classic35-path.plan.md §5 C9）。

钉住的事：
  · MC return 是 `r_t + γ(1-done_t)G_{t+1}`，末步不 bootstrap；
  · OLS `G ~ 1 + V` 的斜率/截距/R² 与已知直线一致；
  · 校准器读 `value/metrics/done/manifest`，奖励由注入的 RewardFn 现算；
  · bootstrap 按局 cluster（固定种子可复算），不把相关决策步当独立样本。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_c9_critic_calibration_under_test", NN_ROOT / "tools" / "c9_critic_calibration.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load()


def test_mc_returns_discounts_until_done() -> None:
    # G0 = 1 + .5*(2 + .5*3) = 2.75；G1 = 2 + .5*3 = 3.5；G2 = 3
    got = C.mc_returns(np.array([1.0, 2.0, 3.0]), np.array([0, 0, 1]), 0.5)
    assert got == pytest.approx([2.75, 3.5, 3.0])


def test_mc_returns_stops_at_early_terminal() -> None:
    got = C.mc_returns(np.array([1.0, 100.0, 100.0]), np.array([0, 1, 1]), 0.9)
    # t0 不是 terminal ⇒ 仍可向后 bootstrap 一步（t1 自己不再往后）
    assert got == pytest.approx([91.0, 100.0, 100.0])


def test_fit_arrays_recovers_known_line() -> None:
    st = C.fit_arrays(np.array([1.0, 2.0, 3.0]), np.array([2.0, 4.0, 6.0]))
    assert st["slope"] == pytest.approx(2.0)
    assert st["intercept"] == pytest.approx(0.0)
    assert st["r2"] == pytest.approx(1.0)
    assert st["pearson"] == pytest.approx(1.0)


def test_fit_arrays_rejects_zero_variance() -> None:
    with pytest.raises(ValueError, match="方差"):
        C.fit_arrays(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0]))


def _write_shard(
    root: Path, stage: int, seed: int, values: list[float], dones: list[int]
) -> Path:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True)
    n = len(values)
    np.save(d / "value.npy", np.asarray(values, dtype=np.float32))
    np.save(d / "done.npy", np.asarray(dones, dtype=np.int64))
    # 列宽不重要：测试注入的 reward_fn 直接返回已知奖励。
    np.save(d / "metrics.npy", np.zeros((n + 1, 3), dtype=np.float64))
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "seed": seed,
                "outcome": "stage_clear",
                "score": 1.0,
                "metrics_version": C.METRICS_VERSION,
            }
        ),
        encoding="utf-8",
    )
    return d


def test_load_game_uses_metrics_reward_and_value(tmp_path: Path) -> None:
    d = _write_shard(tmp_path, 16, 3, [10.0, 20.0, 30.0], [0, 0, 1])
    seen: list[tuple[Any, ...]] = []

    def reward_fn(metrics: np.ndarray, outcome: str, score: float, it: int) -> np.ndarray:
        seen.append((metrics.shape, outcome, score, it))
        return np.array([1.0, 2.0, 3.0])

    g = C.load_game(str(d), reward_fn, gamma=0.5, it=233)
    assert (g.stage, g.seed, g.n) == (16, 3, 3)
    assert g.values == pytest.approx([10.0, 20.0, 30.0])
    assert g.returns == pytest.approx([2.75, 3.5, 3.0])
    assert seen == [((4, 3), "stage_clear", 1.0, 233)]


def test_load_game_rejects_metrics_version_mismatch(tmp_path: Path) -> None:
    d = _write_shard(tmp_path, 16, 0, [1.0], [1])
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["metrics_version"] = C.METRICS_VERSION - 1
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="metrics_version"):
        C.load_game(str(d), lambda *_: np.array([1.0]), gamma=0.5, it=233)


def test_build_report_pooled_and_per_stage() -> None:
    # stage 16: V [1,2] -> G [2,4]（slope=2）；stage 22: V [3,4] -> G [3,4]（slope=1）
    games = [
        C.Game(16, 0, np.array([1.0, 2.0]), np.array([2.0, 4.0])),
        C.Game(16, 1, np.array([1.0, 2.0]), np.array([2.0, 4.0])),
        C.Game(22, 0, np.array([3.0, 4.0]), np.array([3.0, 4.0])),
    ]
    res = C.build_report(
        games,
        course_name="z10-maze",
        it=233,
        gamma=0.998,
        reward_identity="deadbeef",
        stages_expected=(16, 22, 3, 6),
        n_boot=50,
        boot_seed=7,
    )
    assert res["pooled"]["n_games"] == 3
    assert res["pooled"]["n_decisions"] == 6
    assert res["shards"]["missing_expected_stages"] == [3, 6]
    assert res["per_stage"]["16"]["pooled"]["slope"] == pytest.approx(2.0)
    assert res["per_stage"]["22"]["pooled"]["slope"] == pytest.approx(1.0)
    assert res["pooled"]["ci95"]["slope"] is not None
    # game-level：3 个局均值 (1.5,3)/(1.5,3)/(3.5,3.5) ⇒ 斜率 0.25
    assert res["pooled"]["game_level"]["slope"] == pytest.approx(0.25)


def test_bootstrap_is_deterministic_and_clustered() -> None:
    games = [
        C.Game(16, i, np.array([1.0, 2.0]), np.array([2.0, 4.0])) for i in range(10)
    ]
    a = C._bootstrap_ci(games, reps=100, seed=123)
    b = C._bootstrap_ci(games, reps=100, seed=123)
    assert a == b
    assert a["slope"] == pytest.approx([2.0, 2.0])


def test_write_returns_writes_float32_target(tmp_path: Path) -> None:
    d = _write_shard(tmp_path, 16, 0, [10.0, 20.0, 30.0], [0, 0, 1])
    g = C.load_game(str(d), lambda *_: np.array([1.0, 2.0, 3.0]), gamma=0.5, it=233)
    p = C.write_returns(str(d), g)
    arr = np.load(p)
    assert arr.dtype == np.float32
    assert arr == pytest.approx([2.75, 3.5, 3.0])


def test_values_from_weights_uses_saved_checkpoint(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    from worker.data.weights_io import save_weights_json
    from worker.models.student import PPOStudent

    d = _write_shard(tmp_path, 16, 0, [10.0, 20.0, 30.0], [0, 0, 1])
    rng = np.random.default_rng(11)
    np.save(d / "obs.npy", rng.integers(0, 256, (3, 17, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((3, 46)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((3, 57)).astype(np.float32))
    torch.manual_seed(3)
    model = PPOStudent(action_v6=False, policy_extra=False)
    w = tmp_path / "value-only.json"
    save_weights_json(model, str(w), extra_meta={"arch": model.arch()})
    loaded = C.load_value_model(str(w))
    vals = C.values_from_weights(str(d), loaded, batch=2)
    assert vals.shape == (3,)
    assert np.isfinite(vals).all()


def test_find_shards_requires_value_metrics_done(tmp_path: Path) -> None:
    _write_shard(tmp_path, 16, 0, [1.0], [1])
    (tmp_path / "not_a_shard").mkdir()
    (tmp_path / "not_a_shard" / "obs.npy").write_bytes(b"x")
    assert C.find_shards(str(tmp_path), C.SHARD_FILES) == [
        str(tmp_path / "rl_s16_seed0")
    ]


def test_render_markdown_contains_tables() -> None:
    games = [C.Game(16, 0, np.array([1.0, 2.0]), np.array([2.0, 4.0]))]
    res = C.build_report(
        games,
        course_name="z10-maze",
        it=233,
        gamma=0.998,
        reward_identity="deadbeef",
        stages_expected=(16,),
        n_boot=10,
        boot_seed=7,
    )
    md = C.render_markdown(res)
    assert "# C9" in md
    assert "game-level" in md
    assert "stage 16" in md
