"""`tools/p0_thcost.py` 的算术契约（plan/classic35-path.plan.md §5 C2）。

钉住的事：
  · 每局总成本取 metrics **末行** idx85–88 的和，不是逐行求和；
  · 版本 / 列宽 / 负值 / 非有限值都响亮拒绝；
  · 分位按局统计，bootstrap 固定种子可复算；
  · `w@18 = 18 / p95`，p95=0 时不给假单价。
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
        "_p0_thcost_under_test", NN_ROOT / "tools" / "p0_thcost.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load()


def _write_shard(
    root: Path,
    stage: int,
    seed: int,
    axes: tuple[float, float, float, float],
    *,
    version: int = C.METRICS_VERSION,
    width: int = C.METRICS_DIM,
) -> Path:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True)
    metrics = np.zeros((4, width), dtype=np.float64)
    metrics[0, list(C.THCOST_INDICES)] = [999.0, 999.0, 999.0, 999.0]
    metrics[-1, list(C.THCOST_INDICES)] = axes
    np.save(d / "metrics.npy", metrics)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "seed": seed,
                "outcome": "stage_clear",
                "metrics_version": version,
            }
        ),
        encoding="utf-8",
    )
    return d


def test_load_shard_uses_final_row_not_sum(tmp_path: Path) -> None:
    d = _write_shard(tmp_path, 16, 0, (1.0, 2.0, 3.0, 4.0))
    g = C.load_shard(str(d))
    assert (g.stage, g.seed, g.decisions) == (16, 0, 3)
    assert g.axes == pytest.approx((1.0, 2.0, 3.0, 4.0))
    assert g.total == pytest.approx(10.0)


def test_load_shard_rejects_version_width_negative_and_nan(tmp_path: Path) -> None:
    d = _write_shard(tmp_path, 16, 0, (1.0, 2.0, 3.0, 4.0), version=C.METRICS_VERSION - 1)
    with pytest.raises(ValueError, match="metrics_version"):
        C.load_shard(str(d))

    d = _write_shard(tmp_path, 16, 1, (1.0, 2.0, 3.0, 4.0), width=C.METRICS_DIM - 1)
    with pytest.raises(ValueError, match="metrics 应为"):
        C.load_shard(str(d))

    d = _write_shard(tmp_path, 16, 2, (1.0, -2.0, 3.0, 4.0))
    with pytest.raises(ValueError, match="负值"):
        C.load_shard(str(d))

    d = _write_shard(tmp_path, 16, 3, (1.0, float("nan"), 3.0, 4.0))
    with pytest.raises(ValueError, match="非有限"):
        C.load_shard(str(d))


def test_summarize_quantiles_and_zero_frac() -> None:
    st = C.summarize(np.array([0.0, 10.0, 20.0, 30.0]))
    assert st["n"] == 4
    assert st["p50"] == pytest.approx(15.0)
    assert st["p95"] == pytest.approx(28.5)
    assert st["zero_frac"] == pytest.approx(0.25)


def test_bootstrap_is_deterministic() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
    a = C.bootstrap_ci(x, reps=200, seed=7)
    b = C.bootstrap_ci(x, reps=200, seed=7)
    assert a == b
    assert a["p95"] is not None


def test_build_report_implied_w_and_stage_split() -> None:
    games = [
        C.CostGame(16, 0, 10, 100.0, (25.0, 25.0, 25.0, 25.0)),
        C.CostGame(16, 1, 10, 300.0, (75.0, 75.0, 75.0, 75.0)),
        C.CostGame(22, 0, 20, 0.0, (0.0, 0.0, 0.0, 0.0)),
    ]
    res = C.build_report(games, root="tmp/fake", n_boot=50, boot_seed=3)
    assert res["shards"]["games"] == 3
    assert res["per_stage"]["16"]["summary"]["p95"] == pytest.approx(290.0)
    assert res["per_stage"]["16"]["dose"]["implied_w"] == pytest.approx(18.0 / 290.0)
    assert res["per_stage"]["22"]["dose"]["implied_w"] is None
    md = C.render_markdown(res)
    assert "# C2" in md
    assert "pooled" in md
    assert "w@18" in md
    assert "分关 16:2" in md
    assert "{'16'" not in md
