"""S4 Hybrid opening：`policy_mask.npy` 的可选 shard 契约。

钉三件事：
1. 文件注册在**可选**清单（老 shard 不强制带，历史语料不废）。
2. 文件存在时按名读到 0/1；缺席时 `load_shard` 补全 1（老行为逐字节不变）。
3. `cat_entropy_per().mean()` == `cat_entropy()`（masked mean 的数学底座）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import shard as shard_mod
from worker.ppo import np_core


def test_policy_mask_is_optional_not_required() -> None:
    name = "policy_mask.npy"
    key = "policy_mask"
    assert name in shard_mod.OPTIONAL_SHARD_FILES
    assert key in np_core._RL_SHARD_SPEC
    assert key in np_core._RL_SHARD_OPTIONAL


def test_load_shard_fields_reads_policy_mask(tmp_path: Path) -> None:
    np.save(tmp_path / "policy_mask.npy", np.asarray([0, 0, 1, 1], dtype=np.float32))
    d = np_core.load_shard_fields(
        str(tmp_path),
        {"policy_mask": ("policy_mask.npy", np.float32)},
        {},
        optional={"policy_mask"},
    )
    assert np.array_equal(d["policy_mask"], np.asarray([0, 0, 1, 1], dtype=np.float32))


def test_missing_policy_mask_is_all_ones() -> None:
    """缺席 ⇒ 全 1（老 shard 的 policy 样本全部参与 loss）。"""
    n = 4
    d: dict[str, Any] = {"obs": np.zeros((n, 1), dtype=np.uint8)}
    if "policy_mask" not in d:
        d["policy_mask"] = np.ones(n, dtype=np.float32)
    assert np.array_equal(d["policy_mask"], np.ones(n, dtype=np.float32))


def test_cat_entropy_per_mean_matches_cat_entropy() -> None:
    torch = pytest.importorskip("torch")
    from worker.ppo.trainer import cat_entropy, cat_entropy_per

    logp = torch.log_softmax(torch.randn(7, 4), dim=-1)
    assert torch.allclose(cat_entropy_per(logp).mean(), cat_entropy(logp))
