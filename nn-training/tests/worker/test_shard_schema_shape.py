"""A9（obs-v5）——shard 装载的**形状判据**：旧 schema（9 维 extra / 30 维 scalars）响亮拒收。

plan/obs-v5-vector-gap.plan.md §3-S6：`load_shard_fields` 原先只查 dtype ⇒ 9 维 `extra.npy`
会被当 37 维静默读进来，错位到训练完才发作。本文件钉住三件事：
  ① 正确形状：零拷贝装载，值不变；
  ② 旧 9 维 extra / 旧 30 维 scalars / 旧 14 通道 obs ⇒ `ValueError`（不是静默）；
  ③ 判据与 `common.schema` 同源（改 schema 而不改判据 ⇒ 这里红）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM
from worker.ppo.np_core import (
    _RL_SHARD_OPTIONAL,
    _RL_SHARD_SHAPES,
    _RL_SHARD_SPEC,
    load_shard_fields,
)


def _write_shard(
    tmp_path: Path,
    *,
    n: int = 4,
    obs_ch: int = OBS_CHANNELS,
    scalars: int = SCALAR_DIM,
    extra: int = POLICY_EXTRA_DIM,
) -> Path:
    """最小可装载 shard：三张被形状判据盯着的张量 + 其余占位（形状不限）。"""
    rng = np.random.default_rng(7)
    np.save(tmp_path / "obs.npy", rng.integers(0, 255, size=(n, obs_ch, BOARD, BOARD), dtype=np.uint8))
    np.save(tmp_path / "scalars.npy", rng.random((n, scalars)).astype(np.float32))
    np.save(tmp_path / "extra.npy", rng.random((n, extra)).astype(np.float32))
    for name, arr in (
        ("a_move.npy", np.zeros(n, dtype=np.int64)),
        ("a_fire.npy", np.zeros(n, dtype=np.int64)),
        ("lp_move.npy", np.zeros(n, dtype=np.float32)),
        ("lp_fire.npy", np.zeros(n, dtype=np.float32)),
        ("value.npy", np.zeros(n, dtype=np.float32)),
        ("done.npy", np.zeros(n, dtype=np.int64)),
        ("mask.npy", np.zeros((n, 7), dtype=np.int64)),
        ("metrics.npy", np.zeros((n + 1, 21), dtype=np.float64)),
    ):
        np.save(tmp_path / name, arr)
    return tmp_path


def test_load_shard_fields_accepts_current_schema(tmp_path: Path) -> None:
    """① 当前 schema：装载成功且读数逐位保留（形状判据不误伤）。"""
    _write_shard(tmp_path)
    # B1 v6（2026-10-20）：`_RL_SHARD_SPEC` 现在含 v6 才有的 `a_hold`/`lp_hold`，
    # 本 fixture 写的是 legacy shard（没有这两个文件）⇒ 必须按生产口径传 `optional`。
    d = load_shard_fields(
        str(tmp_path), _RL_SHARD_SPEC, _RL_SHARD_SHAPES, optional=set(_RL_SHARD_OPTIONAL)
    )
    assert d.get("a_hold") is None  # legacy shard：整键省略，不是填 0
    assert d["obs"].shape == (4, OBS_CHANNELS, BOARD, BOARD)
    assert d["scalars"].shape == (4, SCALAR_DIM)
    assert d["extra"].shape == (4, POLICY_EXTRA_DIM)
    assert d["obs"].dtype == np.uint8 and d["extra"].dtype == np.float32


@pytest.mark.parametrize(
    "key, legacy, current",
    [
        ("extra", 9, POLICY_EXTRA_DIM),  # v4 的 POLICY_EXTRA(9)：A9 的主角
        ("scalars", 30, SCALAR_DIM),  # v4 的 SCALAR_DIM(30)
        ("obs_ch", 14, OBS_CHANNELS),  # v3 的 14 通道
    ],
)
def test_load_shard_fields_refuses_legacy_schema(
    tmp_path: Path, key: str, legacy: int, current: int
) -> None:
    """② 旧 schema ⇒ 响亮 ValueError；只把那一维改回 schema 值 ⇒ 能装载。

    判据用**行为**（抛 / 不抛）而不是异常文本（AGENTS §0.4：新用例禁子串断言），
    而且这样更能证明拒绝的**归因**就是那一维。
    """
    _write_shard(tmp_path, **{key: legacy})
    with pytest.raises(ValueError):
        load_shard_fields(
            str(tmp_path), _RL_SHARD_SPEC, _RL_SHARD_SHAPES, optional=set(_RL_SHARD_OPTIONAL)
        )
    _write_shard(tmp_path, **{key: current})
    d = load_shard_fields(
        str(tmp_path), _RL_SHARD_SPEC, _RL_SHARD_SHAPES, optional=set(_RL_SHARD_OPTIONAL)
    )
    assert d["obs"].shape[0] == 4


def test_shape_criteria_are_schema_sourced() -> None:
    """③ 判据不是写死的数字：改 schema 常量会同时改判据（单一事实来源）。"""
    assert _RL_SHARD_SHAPES["extra"] == (POLICY_EXTRA_DIM,)
    assert _RL_SHARD_SHAPES["scalars"] == (SCALAR_DIM,)
    assert _RL_SHARD_SHAPES["obs"] == (OBS_CHANNELS, BOARD, BOARD)
    assert _RL_SHARD_SHAPES["extra"][0] > 9 and _RL_SHARD_SHAPES["scalars"][0] > 30
