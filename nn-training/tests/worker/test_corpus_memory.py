"""大语料装载的内存回归（2026-11-13，g2-classic35 OOM）。

钉四件事（纯 numpy，不拖 torch）：
  1. `load_dataset(lazy=True)` 的 obs/scalars/extra 是 `ShardArray`（逐 shard mmap），
     不是整份 concatenate；单帧/切片内容与 eager 逐字节一致；
  2. 选择 `shard_ids` 仍是一份真实 ndarray（切分判据不受 lazy 影响）；
  3. `plan_corpus_memory` 只 stat + mmap 头部就能报出 shard/样本/字节；
  4. `check_corpus_memory` 在分配**之前**按可用内存拒绝（eager 装不下 / lazy 能过），
     错误信息带处置，而不是等内核重启 notebook。
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pytest

from worker.data.npyio import (
    CorpusMemoryPlan,
    ShardArray,
    check_corpus_memory,
    load_dataset,
    plan_corpus_memory,
)

N = 8
CH, BOARD = 17, 26
SCALAR_DIM, EXTRA_DIM = 46, 57


def _write_v6_shard(root: Path, name: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "obs.npy", rng.integers(0, 256, (N, CH, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((N, SCALAR_DIM)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((N, EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4, N, dtype=np.uint8))
    np.save(d / "a_fire.npy", rng.integers(0, 3, N, dtype=np.uint8))
    np.save(d / "a_hold.npy", rng.integers(0, 2, N, dtype=np.uint8))
    np.save(d / "mask.npy", np.ones((N, 9), dtype=np.uint8))
    (d / "manifest.json").write_text(json.dumps({"samples": N}), encoding="utf-8")


@pytest.fixture()
def v6_corpus(tmp_path: Path) -> Path:
    for i in range(2):
        _write_v6_shard(tmp_path, f"rl_s2000_seed{i}", 100 + i)
    return tmp_path


def test_lazy_load_returns_shard_views_with_same_values(v6_corpus: Path) -> None:
    eager = load_dataset(str(v6_corpus), action_v6=True)
    lazy = load_dataset(str(v6_corpus), action_v6=True, lazy=True)

    for key in ("obs", "scalars", "extra"):
        lazy_field = lazy[key]
        assert isinstance(lazy_field, ShardArray), f"{key} 应走 mmap 分片视图"
        assert not isinstance(eager[key], ShardArray), f"eager 的 {key} 应保持 ndarray"
        eager_arr = np.asarray(eager[key])
        assert lazy_field.shape == eager_arr.shape
        assert lazy_field.dtype == eager_arr.dtype
    # 跨 shard 边界 + 负下标 + 切片：索引语义与 ndarray 一致
    lazy_obs = lazy["obs"]
    eager_obs = np.asarray(eager["obs"])
    assert isinstance(lazy_obs, ShardArray)
    for i in (0, N - 1, N, 2 * N - 1, -1):
        assert np.array_equal(lazy_obs[i], eager_obs[i])
    assert np.array_equal(lazy_obs[::3], eager_obs[::3])
    lazy_sc = lazy["scalars"]
    assert isinstance(lazy_sc, ShardArray)
    assert np.array_equal(
        lazy_sc[[1, N + 2]], np.asarray(eager["scalars"])[[1, N + 2]]
    )

    # 小数组仍整份拼好（下游按列/切片读的语义不变成逐帧）
    for key in ("actions", "masks", "hold", "conditions", "shard_ids"):
        lazy_small = lazy[key]
        assert isinstance(lazy_small, np.ndarray), f"{key} 不应 lazy"
        assert np.array_equal(lazy_small, np.asarray(eager[key]))
    shard_ids = lazy["shard_ids"]
    assert isinstance(shard_ids, np.ndarray)
    assert shard_ids.tolist() == [0] * N + [1] * N


def test_plan_corpus_memory_counts_without_materializing(v6_corpus: Path) -> None:
    plan = plan_corpus_memory(str(v6_corpus))
    assert plan.shards == 2
    assert plan.samples == 2 * N
    assert plan.obs_bytes >= 2 * N * CH * BOARD * BOARD
    assert plan.other_bytes > 0
    assert plan.total_bytes == plan.obs_bytes + plan.other_bytes
    assert plan.eager_peak_bytes > 2 * plan.total_bytes
    assert plan.lazy_peak_bytes() > 0


def test_memory_preflight_refuses_before_allocating(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = CorpusMemoryPlan(
        data_dir="synthetic",
        shards=2,
        samples=1000,
        obs_bytes=2 << 30,
        other_bytes=1 << 29,
    )
    with pytest.raises(MemoryError, match="内存预检失败"):
        check_corpus_memory(plan, lazy=False, available=4 << 30)
    # 同样 4 GiB 可用：mmap 路径能过（工作集远小于整份 2×）
    assert check_corpus_memory(plan, lazy=True, available=4 << 30) == 4 << 30
    # 可用内存探测不到 ⇒ 不编造拒绝（monkeypatch 探针，避免依赖本机读数）
    monkeypatch.setattr("worker.data.npyio.available_memory_bytes", lambda: None)
    assert check_corpus_memory(plan, lazy=False) is None


def test_shard_array_pickles_by_reopening_files_without_payload(v6_corpus: Path) -> None:
    """DataLoader worker（spawn）会把 dataset pickle 过去：必须只带路径，不带 9.4 GiB。"""
    data = load_dataset(str(v6_corpus), action_v6=True, lazy=True)
    obs = data["obs"]
    assert isinstance(obs, ShardArray)
    blob = pickle.dumps(obs)
    # 16 帧 × 17×26×26 = 184 KiB；路径 pickle 应远小于 10 KiB
    assert len(blob) < 10_000, f"ShardArray pickle 了 {len(blob)} 字节（疑似把 mmap 内容序列化）"
    clone = pickle.loads(blob)
    assert isinstance(clone, ShardArray)
    assert clone.shape == obs.shape
    assert np.array_equal(clone[N + 1], obs[N + 1])


def test_shard_array_rejects_mismatched_parts() -> None:
    with pytest.raises(ValueError, match="形状"):
        ShardArray(
            [np.zeros((2, 3), dtype=np.uint8), np.zeros((2, 4), dtype=np.uint8)],
            name="bad",
        )
    with pytest.raises(ValueError, match="dtype"):
        ShardArray(
            [np.zeros((2, 3), dtype=np.uint8), np.zeros((2, 3), dtype=np.float32)],
            name="bad-dtype",
        )
