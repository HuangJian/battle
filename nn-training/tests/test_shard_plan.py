"""tests/test_shard_plan.py —— shard 级切分判据（**免 torch**，plan/python-refactor.md P2-6d）。

2026-09-26（item 6e）：这些性质原本只在 `tests/test_shard_split.py` 里经**真 DataLoader**验，
而 `make_loaders` 为了造 DataLoader 必须 import torch ⇒ 判据被连坐。切分本身只依赖 numpy，
已抽到 `data/shard_split.py`（免 torch），本文件用**合成 shard_ids** 把三条性质钉死：

  1. shard 是切分的最小单位——同一个 shard 的样本绝不同时出现在 train 与 val；
  2. 两侧样本数都是「每 shard 帧数」的整数倍（样本级切分会立刻破坏这条）；
  3. 单 shard / 无 shard_ids 的语料必须**不**走 shard 级（否则 val 吞掉全部样本 ⇒ train=0）。

另有两条把「顺序来源」钉住：抽哪些 shard 进 val 只由调用方给的 `perm` 决定（`make_loaders`
里是 `torch.randperm(..., generator=gen)`）⇒ 同 perm 同结果，且本模块不自己造随机数。

接线锚（`make_loaders` 真的调到本模块、旧语料真的回退样本级切分）仍在
`tests/test_shard_split.py`（那边要真 DataLoader，故保留 torch）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from schema import OBS_CHANNELS, SCALAR_DIM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.npyio import save_shard
from data.shard_split import (
    MIN_SHARDS_FOR_SPLIT,
    plan_shard_split,
    shard_sizes,
    should_split_by_shards,
    val_shards_for,
)

FRAMES = 40


def _corpus(n_shards: int = 6, frames: int = FRAMES) -> np.ndarray:
    """合成 shard_ids：每 shard 恰好 `frames` 帧，按 id 升序连续（与 load_dataset 产出同形）。"""
    return np.repeat(np.arange(n_shards), frames)


def test_should_split_by_shards_needs_at_least_two_shards() -> None:
    """无 shard_ids（旧语料）与单 shard 微语料都必须回退样本级切分。"""
    assert should_split_by_shards(None) is False
    assert should_split_by_shards(np.zeros(181, dtype=np.int64)) is False  # 单 shard
    assert MIN_SHARDS_FOR_SPLIT == 2
    assert should_split_by_shards(np.repeat(np.arange(2), 10)) is True


def test_shard_sizes_counts_every_shard() -> None:
    assert shard_sizes(_corpus(6)) == [FRAMES] * 6
    assert shard_sizes(np.array([0, 0, 0, 1, 2, 2], dtype=np.int64)) == [3, 1, 2]


def test_plan_never_mixes_a_shard_across_train_and_val() -> None:
    """P2-6d 核心断言：val 里任一 shard 的样本绝不出现在 train。"""
    shard_ids = _corpus(6)
    n_val = 60  # = int(240 * 0.25)
    tr_idx, val_idx = plan_shard_split(shard_ids, n_val, [3, 0, 5, 1, 4, 2])

    assert set(tr_idx.tolist()).isdisjoint(val_idx.tolist())
    assert sorted(tr_idx.tolist() + val_idx.tolist()) == list(range(240))
    # 整数性：整 shard 切分下两侧都是 frames 的倍数（样本级切分会破坏这条）
    assert len(tr_idx) % FRAMES == 0 and len(val_idx) % FRAMES == 0
    # 判停形状：先取一个（40 < 60），再取一个（80 ≥ 60）停 ⇒ 恰好两个 shard
    assert set(shard_ids[val_idx].tolist()) == {3, 0}
    assert len(val_idx) == 2 * FRAMES


def test_val_keeps_at_least_one_shard_even_when_it_overshoots() -> None:
    """`n_val` 小于一个 shard 时也得有 val（否则 `val_shards` 空 ⇒ 全进 train）。"""
    shard_ids = _corpus(6)
    tr_idx, val_idx = plan_shard_split(shard_ids, 0, [4, 1, 0, 2, 3, 5])
    assert len(val_idx) == FRAMES
    assert len(tr_idx) == 5 * FRAMES
    assert set(shard_ids[val_idx].tolist()) == {4}


def test_perm_order_decides_which_shards_go_to_val() -> None:
    """切分顺序**只**来自 `perm`（生产里是 `torch.randperm(..., generator=gen)`）。

    ⇒ 同一份语料换 perm 会换出不同 val（说明没有第二个随机源）；同一 perm 必然同结果
    （说明 `plan_shard_split` 自身确定性）。
    """
    shard_ids = _corpus(6)
    a = plan_shard_split(shard_ids, 60, [0, 1, 2, 3, 4, 5])
    b = plan_shard_split(shard_ids, 60, [5, 4, 3, 2, 1, 0])
    c = plan_shard_split(shard_ids, 60, [0, 1, 2, 3, 4, 5])
    assert set(shard_ids[a[1]].tolist()) == {0, 1}
    assert set(shard_ids[b[1]].tolist()) == {5, 4}
    np.testing.assert_array_equal(a[0], c[0])
    np.testing.assert_array_equal(a[1], c[1])


def test_val_shards_for_stops_at_the_first_shard_reaching_the_budget() -> None:
    """判停口径逐字保留原实现：`if val_shards and acc >= n_val: break`——
    即「拿够了（acc ≥ n_val）就停」，且只在这一轮**之后**才判 ⇒ 累计恰好到 n_val 时
    不再多取一个，差 1 则必须再取一个。这条边界是原来内联在 make_loaders 里的形状，
    抽出来后才有人盯着它。"""
    sizes = [10, 10, 10]
    assert val_shards_for(sizes, 0, [0, 1, 2]) == [0]  # 至少一个 shard
    assert val_shards_for(sizes, 10, [0, 1, 2]) == [0]  # acc 恰好到期 ⇒ 停
    assert val_shards_for(sizes, 11, [0, 1, 2]) == [0, 1]  # 差 1 ⇒ 再取一个
    assert val_shards_for(sizes, 25, [2, 1, 0]) == [2, 1, 0]


# ------------------------------------------------ 生产侧产出与合成器对账（load_dataset）

def test_shard_ids_from_load_dataset_match_the_synthetic_shape(tmp_path: Path) -> None:
    """真的 `load_dataset` 产出的 shard_ids 必须与上面 `_corpus` 同形，并通过同一组判据。

    2026-09-26（item 9）：自 `tests/test_shard_split.py` 分家（原用例名
    `test_shard_ids_are_contiguous_and_complete`）。它只吃 `data/npyio`（numpy），却在那边
    因 `make_loaders` 要真 DataLoader 而整文件连坐 torch。上面几条用的是**合成** shard_ids，
    这一条把合成物锚在真产出上——否则纯函数守的是一个可能不存在于生产的形状。
    """
    rng = np.random.default_rng(42)
    for s in range(6):
        d = tmp_path / f"shard{s}"
        d.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {
            "obs": rng.integers(0, 256, (FRAMES, OBS_CHANNELS, 26, 26), dtype=np.uint8),
            "scalars": rng.standard_normal((FRAMES, SCALAR_DIM)).astype(np.float32),
            "actions": rng.integers(0, 5, (FRAMES, 2), dtype=np.int64),
            "masks": np.ones((FRAMES, 7), dtype=np.float32),
            "conditions": np.zeros(FRAMES, dtype=np.int64),
        }
        save_shard(str(d), arrays, {"stage": s, "seed": s})

    from data.npyio import load_dataset

    data = load_dataset(str(tmp_path))
    shard_ids = data["shard_ids"]
    assert shard_ids.shape[0] == data["obs"].shape[0]
    np.testing.assert_array_equal(shard_ids, _corpus(6))
    # 真产出必须过同一组判据（合成器 ↔ 生产 的对账）
    assert shard_sizes(shard_ids) == [FRAMES] * 6
    assert should_split_by_shards(shard_ids) is True
