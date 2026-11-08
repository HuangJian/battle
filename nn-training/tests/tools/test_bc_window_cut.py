"""`tools/bc_window_cut.py` 的契约（plan/w2-window-split.plan.md §2/§5）。

钉的是四件事：只留 pickup 窗口、`metrics` 末行终局保留、`manifest.nSamples` 更新、
非 shard/行数不符/空局面 的拒绝与跳过。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from tools.bc_window_cut import (
    METRICS,
    OPTIONAL_FILES,
    REQUIRED_FILES,
    cut_one,
    find_shards,
)

D = 90


def _write_shard(root: Path, name: str, pickup: list[float]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    n = len(pickup)
    m = np.zeros((n + 1, D), dtype=np.float32)
    m[:n, 40] = np.asarray(pickup, dtype=np.float32)
    m[n, 0] = 123.0  # 终局行哨兵
    np.save(d / METRICS, m)
    np.save(d / "obs.npy", np.zeros((n, 17, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", np.zeros((n, 46), dtype=np.float32))
    np.save(d / "extra.npy", np.zeros((n, 57), dtype=np.float32))
    for f in ("a_move", "a_fire", "a_hold", "lp_move", "lp_fire", "lp_hold", "done", "returns", "value"):
        np.save(d / f"{f}.npy", np.zeros((n,), dtype=np.float32))
    np.save(d / "mask.npy", np.zeros((n, 9), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps({"stage": 16, "seed": 0, "nSamples": n, "outcome": "stage_clear", "ticks": 999}),
        encoding="utf-8",
    )
    return d


def test_cut_keeps_only_pickup_window(tmp_path: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed0", [9, 3, 0, -1, 6, 7])
    dst = tmp_path / "out" / "rl_s16_seed0"
    n, k = cut_one(str(src), str(dst), max_dist=6, min_steps=1)
    assert (n, k) == (6, 3)  # 3/0/6 入选；9 太远、-1 不可达、7 太远

    for f in REQUIRED_FILES + OPTIONAL_FILES:
        assert np.load(dst / f).shape[0] == 3, f
    m = np.load(dst / METRICS)
    assert m.shape == (4, D)
    assert m[-1, 0] == 123.0, "终局行必须保留"
    assert list(m[:3, 40]) == [3.0, 0.0, 6.0]

    man = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    assert man["nSamples"] == 3
    assert man["outcome"] == "stage_clear" and man["ticks"] == 999
    assert man["window_cut"] == {"by": "pickupDist", "max_dist": 6, "from": 6, "to": 3}


def test_game_without_pickup_is_not_written(tmp_path: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed1", [9, 9, -1])
    dst = tmp_path / "out" / "rl_s16_seed1"
    n, k = cut_one(str(src), str(dst), max_dist=6, min_steps=1)
    assert (n, k) == (3, 0)
    assert not dst.exists()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed2", [1, 2])
    dst = tmp_path / "out" / "rl_s16_seed2"
    n, k = cut_one(str(src), str(dst), max_dist=6, min_steps=1, dry_run=True)
    assert (n, k) == (2, 2)
    assert not dst.exists()


def test_find_shards_requires_full_v6_shape(tmp_path: Path) -> None:
    _write_shard(tmp_path / "in", "rl_s16_seed0", [1])
    bad = tmp_path / "in" / "not_a_shard"
    bad.mkdir()
    np.save(bad / "obs.npy", np.zeros((1, 17, 26, 26), dtype=np.uint8))
    got = find_shards(str(tmp_path / "in"), ("obs.npy", METRICS, "manifest.json"))
    assert got == [str(tmp_path / "in" / "rl_s16_seed0")]


def test_bc_collect_shard_without_returns_is_accepted(tmp_path: Path) -> None:
    """BC 采集课不写 `returns.npy` / `value.npy`：缺这两个也必须能裁。

    2026-11-08 w2-pick-collect 现场：工具把 returns 当必需文件 ⇒ 200 个 shard 全部
    FileNotFoundError。BC 语料（`value_coef 0`）本来就不需要 returns。
    """
    src = _write_shard(tmp_path / "in", "rl_s16_seed4", [1, 9, 2])
    (src / "returns.npy").unlink()
    (src / "value.npy").unlink()
    dst = tmp_path / "out" / "rl_s16_seed4"
    n, k = cut_one(str(src), str(dst), max_dist=6, min_steps=1)
    assert (n, k) == (3, 2)
    for f in REQUIRED_FILES:
        assert np.load(dst / f).shape[0] == 2, f
    for f in OPTIONAL_FILES:
        assert not (dst / f).exists(), f"{f} 缺失时不该凭空生成"


def test_row_mismatch_is_loud(tmp_path: Path) -> None:
    src = _write_shard(tmp_path / "in", "rl_s16_seed3", [1, 1])
    np.save(src / "obs.npy", np.zeros((1, 17, 26, 26), dtype=np.uint8))  # 少一行
    with pytest.raises(ValueError, match="行数"):
        cut_one(str(src), str(tmp_path / "out" / "x"), max_dist=6, min_steps=1)
