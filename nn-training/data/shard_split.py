"""data/shard_split.py — shard 级 train/val 切分的**纯 numpy**实现（2026-09-26，item 6e）。

## 为什么单独一个模块

`data/dataset.py` 必须在模块顶层 `import torch`（`NNDataset` / `_AugWrapper` 要在类定义期
继承 `Dataset`），于是「切分本身对不对」的用例——val 取整 shard 不泄漏、单 shard 语料必须
回退样本级、shard_id 与样本一一对应——全都被 DataLoader 连坐进 torch 测试路径。切分只依赖
numpy，P2-6d 的三条性质都能在纯 numpy 下钉住；`make_loaders` 只在最后一步把下标交给
`_AugWrapper` / `Subset` / `DataLoader`。

## 顺序来源不变（逐字节等价的硬条件）

抽哪些 shard 进 val 完全由调用方给的 `perm` 决定——`make_loaders` 里仍是
`torch.randperm(shard_ids.max() + 1, generator=gen)`，`gen = torch.Generator().manual_seed(seed)`
的消费顺序一字未改。本模块**不自己造随机数**：同一个 perm 必然得到同一组 val shard，
所以既有语料的切分结果与拆分前逐字节相同（`tests/test_shard_plan.py` 用固定 perm 把这条钉住）。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

#: shard 级切分至少要几个 shard。单 shard 走 shard 级会把**全部**样本划进 val ⇒ train=0
#: ⇒ DataLoader `num_samples=0` 崩溃（2026-09-13 bc 云端冒烟实测）⇒ 必须回退样本级切分。
MIN_SHARDS_FOR_SPLIT = 2


def should_split_by_shards(shard_ids: np.ndarray | None) -> bool:
    """语料是否带**可用**的 shard 元数据（≥ `MIN_SHARDS_FOR_SPLIT` 个 shard）。

    旧语料没有 `shard_ids` 键（`None`）⇒ False。
    """
    if shard_ids is None:
        return False
    return int(shard_ids.max()) + 1 >= MIN_SHARDS_FOR_SPLIT


def shard_sizes(shard_ids: np.ndarray) -> list[int]:
    """每个 shard 的样本数（下标 = shard id，假定 id 是 0..n-1 连续）。"""
    n_shards = int(shard_ids.max()) + 1
    return [int((shard_ids == s).sum()) for s in range(n_shards)]


def val_shards_for(sizes: Sequence[int], n_val: int, perm: Sequence[int]) -> list[int]:
    """按 `perm` 顺序取整 shard 进 val，累计样本数 ≥ `n_val` 即停。

    注意判停点的形状（与 P2-6d 原实现逐字一致）：**先取一个再判**——`val_shards` 非空是
    判停的前提，所以哪怕第一个 shard 就超过 `n_val`，val 也至少有一个 shard（否则 val 为空）。
    """
    val_shards: list[int] = []
    acc = 0
    for s in perm:
        if val_shards and acc >= n_val:
            break
        val_shards.append(s)
        acc += sizes[s]
    return val_shards


def plan_shard_split(
    shard_ids: np.ndarray, n_val: int, perm: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    """整 shard 切分 → `(train 下标, val 下标)`（按原样本顺序的 `flatnonzero`）。

    两侧都非空由调用方保证：只有 `should_split_by_shards(shard_ids)` 为真时才该走这里。
    """
    val_mask = np.isin(shard_ids, val_shards_for(shard_sizes(shard_ids), n_val, perm))
    return np.flatnonzero(~val_mask), np.flatnonzero(val_mask)
