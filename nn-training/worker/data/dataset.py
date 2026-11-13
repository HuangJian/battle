"""
Dataset + online augmentation (mirrorX) for behavior-cloning training.

mirrorX (plan §NN-M1, nn2 N5 / nn3 N5) is a left-right reflection that MUST keep
the input/output pair self-consistent:
  * obs grid is flipped on the width axis ([:, :, ::-1]);
  * direction-encoded channels (self / enemy-*/bullet) have their dirIdx
    swapped  left<->right  (value = (hi<<3) | (flippedDirIdx+1));
  * the move LABEL is flipped left<->right;
  * scalar relative-direction x-components flip sign (SCALAR_X_INDICES);
  * scalars/obs are otherwise unchanged (y, distance, terrain types, etc.).
Flipping the grid but NOT the labels (or vice-versa) is explicitly forbidden —
it produces a contradictory (input, target) pair.
"""



from __future__ import annotations

# 仓库根探测（B4，2026-09-02；2026-09-30 修）：包已安装（pip install -e .）或
# script-dir/cwd 在 nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入
# sys.path——不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正
# import，避免探针导入产生 F401。
# ⚠ 本文件搬到 `worker/<pkg>/` 后上溯层数 +1，且探针名随刀 2 的 `schema` → `common`
# 更正：算错一层会插进 `worker/`，于是 `worker/cmd.py` 遮蔽 stdlib `cmd`（torch 的
# `import pdb` 当场炸）——「兜底路径」写错从来不是小事。
import importlib.util as _ilu

if _ilu.find_spec("common") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import random
from typing import TYPE_CHECKING

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, random_split

from common.schema import FIRE_DIM, MOVE_DIM

if TYPE_CHECKING:
    from numpy.typing import DTypeLike

    from worker.data.npyio import ArrayField

# mirrorX 的**纯 numpy**实现已抽到 data/mirror.py（2026-09-26：让只测镜像自洽的用例
# 不必把 torch 拖进测试路径——本模块因 NNDataset/_AugWrapper 继承 Dataset 必须顶层
# import torch）。这里再导出；本模块内与外部 `from data.dataset import mirror_x` 一行不改。
from worker.data.mirror import (  # noqa: F401  (re-export：既有调用点不变)
    _MOVE_FLIP,
    BULLET_MIRROR_LUT,
    ENEMY_MIRROR_LUT,
    _flip_direction,
    mirror_x,
)

# shard 级切分的**纯 numpy**实现（2026-09-26，item 6e）：本模块顶层必须 import torch，
# 切分判据没必要连坐 —— 切分逻辑住 data/shard_split.py（免 torch），这里只调它。
from worker.data.shard_split import plan_shard_split, should_split_by_shards


def _field(data: dict[str, ArrayField], key: str, dtype: DTypeLike) -> ArrayField:
    """按目标 dtype 取字段；**dtype 已对就不复制**（2026-11-13）。

    旧实现一律 `.astype(...)`：lazy 的 mmap 分片视图会被整份物化（9.8 GB 语料
    直接 OOM），eager 的 uint8 obs 也要白复制一份。这里只在 dtype 真的不同
    （legacy 的 uint8 actions → int64）时才走 `np.asarray`。
    """
    arr = data[key]
    if getattr(arr, "dtype", None) == np.dtype(dtype):
        return arr
    return np.asarray(arr, dtype=dtype)


class NNDataset(Dataset):
    """Wraps a concatenated sample dict; applies mirrorX on training draws.

    v2: actions = (N,2) [move, fire]；masks = (N,7) [move5, fire2]（item 头删除）。
    """

    def __init__(
        self,
        data: dict[str, ArrayField],
        augment: bool = False,
        mirror_p: float = 0.5,
        rng: np.random.Generator | None = None,
        action_v6: bool = False,
    ):
        #: v6 三头（move4/fire3/hold2）。语料由 `npyio._load_dataset_v6` 产出，多一个
        #: `hold` 键；legacy 语料没有它 ⇒ 只有 v6 分支读。
        self.action_v6 = bool(action_v6)
        # 2026-11-13：`_field` 在 dtype 已匹配时**原样保留** mmap 分片视图，绝不整份复制。
        self.obs = _field(data, "obs", np.uint8)
        self.scalars = _field(data, "scalars", np.float32)
        # v4（plan/policy-spatial-head.plan.md S0-b）：POLICY_EXTRA(9) 随样本走；
        # 旧 shard（无该键）在 verify_shard_schema/调用方处已拦——这里缺失即 KeyError（响亮）。
        self.extra = _field(data, "extra", np.float32)  # (N,9)
        self.actions = _field(data, "actions", np.int64)  # (N,2) move,fire
        self.masks = _field(data, "masks", np.float32)  # legacy (N,7) / v6 (N,9)
        #: v6 第三头标签；legacy 语料无该键 ⇒ 全 0 占位（只被 v6 分支读）
        self.hold = (
            _field(data, "hold", np.int64)
            if self.action_v6 and "hold" in data
            else np.zeros(self.actions.shape[0], dtype=np.int64)
        )
        self.conditions = _field(data, "conditions", np.int64)
        # v2: returns.npy（M3 value 头 MC 预置）可选——不存在时 n/a
        self.returns = (
            _field(data, "returns", np.float32)
            if "returns" in data
            else np.full(self.actions.shape[0], np.nan, dtype=np.float32)
        )
        self.augment = augment
        # v6 三头同样做 mirrorX（2026-11-05，B2）：`mirror_x` 早有 4 类表
        # （`_MOVE_FLIP_V6`，up/down/left/right 只换 left↔right）；此前这里强制
        # `mirror_p=0` 是因为没把 `action_v6` 传进镜像函数，legacy 5 类表会把 v6 的
        # move 映到 4（`Target 4 is out of bounds`）。现在开关与表同源，增强恢复。
        self.mirror_p = mirror_p
        self.rng = rng or np.random.default_rng(0)
        assert self.obs.shape[0] == self.scalars.shape[0] == self.actions.shape[0]

    def __len__(self) -> int:
        return int(self.obs.shape[0])

    def __getitem__(self, idx: int):
        obs = self.obs[idx]
        sc = self.scalars[idx]
        ex = self.extra[idx]
        mv = int(self.actions[idx, 0])
        fr = int(self.actions[idx, 1])
        mask = self.masks[idx]
        ret = self.returns[idx]
        if self.augment and self.rng.random() < self.mirror_p:
            # extra 与 obs/scalars 同一抽签、同一次镜像（自洽不可分家）。
            obs, sc, mv, ex = mirror_x(obs, sc, mv, ex, action_v6=self.action_v6)
        # Torch expects (C,H,W); obs is (C,H,W) already.
        if self.action_v6:
            # v6 三头：move 4 / fire 3 / hold 2（掩码 9 位，见 `obs-encoder.computeMasks`）。
            # 元组顺序 = (obs, scalars, extra, move, fire, hold, mask_move, mask_fire, mask_hold, ret)
            return (
                obs,
                sc,
                ex,
                mv,
                fr,
                int(self.hold[idx]),
                mask[:4],
                mask[4:7],
                mask[7:9],
                ret,
            )
        return (
            obs,
            sc,
            ex,
            mv,
            fr,
            mask[:MOVE_DIM],
            mask[MOVE_DIM : MOVE_DIM + FIRE_DIM],
            ret,
        )


def make_loaders(
    data_dir: str,
    batch_size: int = 256,
    val_split: float = 0.1,
    mirror_p: float = 0.5,
    seed: int = 1234,
    num_workers: int = 0,
    action_v6: bool = False,
    lazy: bool = True,
):
    """Build train/val DataLoaders from a directory of npy shards.

    可复现性（P2-3，2026-09-02）：train DataLoader 的 shuffle 使用**独立
    generator**（seed+1），不再消费 torch 全局 RNG——否则调用方在训练前迭代
    loader（如 _majority_baseline）会改变训练批次顺序。num_workers>0 时
    worker_init_fn 在（Windows spawn 的）worker 进程内播种，增强可复现。

    **shard 级切分（P2-6d，2026-09-02）**：语料带 shard_ids 时按**整 shard**切分
    train/val（同局相邻帧不再跨集 → val 不再虚高）。无 shard_ids 的旧语料回退
    样本级 random_split。
    """
    from worker.data.npyio import load_dataset

    def _worker_init(wid: int) -> None:
        # Windows spawn：worker 是全新进程，必须重新播种（torch/numpy/random 全种）
        np.random.seed(seed + 1000 + wid)
        random.seed(seed + 2000 + wid)
        torch.manual_seed(seed + 3000 + wid)

    # 2026-11-13：训练默认 lazy 装载（逐 shard mmap），否则 9.8 GB 语料会在
    # `np.concatenate` 处吃掉 ~2× 内存。显式 `lazy=False` 保留旧的整份载入语义。
    data = load_dataset(
        data_dir,
        action_v6=action_v6,
        lazy=lazy,
        batch_size=batch_size,
        num_workers=num_workers,
    )
    full = NNDataset(data, augment=False, action_v6=action_v6)
    n = len(full)
    n_val = int(n * val_split)
    gen = torch.Generator().manual_seed(seed)
    shard_ids = data.get("shard_ids")
    if shard_ids is not None and not isinstance(shard_ids, np.ndarray):
        raise TypeError("[dataset] shard_ids 必须是整份 ndarray（不走 lazy 分片视图）")
    if should_split_by_shards(shard_ids):
        # P2-6d：shard 级切分——val 取整 shard，样本数累计 ≥ n_val 即停。
        # 抽 shard 的顺序仍是 torch 的 randperm（gen 的消费顺序不变 ⇒ 切分结果逐字节不变），
        # 切分逻辑本身在 data/shard_split.py（免 torch，见那里的 docstring）。
        assert shard_ids is not None  # should_split_by_shards 已排掉 None
        tr_idx, val_idx = plan_shard_split(
            shard_ids,
            n_val,
            torch.randperm(int(shard_ids.max()) + 1, generator=gen).tolist(),
        )
        train_ds = _AugWrapper(data, tr_idx.tolist(), mirror_p, seed, action_v6)
        val_ds = torch.utils.data.Subset(full, val_idx.tolist())
        # sizes 用**实际**切分大小（shard 级切分后 val 是整 shard，可能略超 n_val）
        sizes = {"train": len(train_ds), "val": len(val_ds), "total": n}
    else:
        # 旧语料（无 shard 元数据）或**单 shard 微语料**：样本级 random_split。
        # 单 shard 走 shard 级切分会把全部样本划进 val（train=0 → DataLoader
        # num_samples=0 崩溃，2026-09-13 bc 冒烟实测）；样本级对 1 shard 是唯一
        # 能给出非空 train 的切法。
        if n < 2:
            raise ValueError(f"语料样本数 {n} < 2——无法切分 train/val（{data_dir}）")
        n_val = max(0, min(n - 1, n_val))
        train_sub, val_ds = random_split(full, [n - n_val, n_val], generator=gen)
        train_ds = _AugWrapper(data, list(train_sub.indices), mirror_p, seed, action_v6)
        sizes = {"train": n - n_val, "val": n_val, "total": n}
    return (
        DataLoader(
            train_ds,
            batch_size=batch_size,
            shuffle=True,
            num_workers=num_workers,
            generator=torch.Generator().manual_seed(seed + 1),  # P2-3：独立于全局 RNG
            worker_init_fn=_worker_init,
        ),
        DataLoader(
            val_ds,
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            worker_init_fn=_worker_init,
        ),
        sizes,
    )


class _AugWrapper(Dataset):
    """Thin wrapper exposing NNDataset.__getitem__ with augmentation on a split."""

    def __init__(
        self,
        data: dict[str, ArrayField],
        indices: list[int],
        mirror_p: float,
        seed: int,
        action_v6: bool = False,
    ):
        self.inner = NNDataset(
            data,
            augment=True,
            mirror_p=mirror_p,
            rng=np.random.default_rng(seed),
            action_v6=action_v6,
        )
        self.indices = list(indices)

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, i):
        return self.inner[self.indices[i]]
