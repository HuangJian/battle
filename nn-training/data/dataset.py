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

# 仓库根探测（B4，2026-09-02）：包已安装（pip install -e .）或 script-dir/cwd 在
# nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入 sys.path——
# 不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正 import，
# 避免探针导入产生 F401。
import importlib.util as _ilu

if _ilu.find_spec("schema") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, random_split

from schema import (
    CH,
    DIRECTION_CHANNELS,
    FIRE_DIM,
    MOVE_DIM,
    SCALAR_X_INDICES,
)

_MOVE_FLIP = np.array([0, 1, 2, 4, 3], dtype=np.int64)  # none,up,down,left<->right


def _flip_direction(channel: np.ndarray, is_bullet: bool) -> np.ndarray:
    """左右翻转方向编码 `(hi<<3)|dirIdx+1`；bullet 通道按敌我区分编码。

    编码（src/nn/obs-encoder.ts，dirIdx 顺序见 schema.DIR_INDEX up/down/left/right）：
      坦克/敌车：val = (hi << 3) | (d + 1)      低 3 位 ∈ 1..4（hi = star/tier）
      敌弹     ：val = d + 1                    低 3 位 ∈ 1..4
      玩家子弹 ：val = d + 1 + 4                低 3 位 ∈ 5..7，**right=8 → slot=0**

    旧实现只翻转 d∈{2,3}（`(col&7)-1`），玩家子弹低 3 位 ∈ {5,6,7,0} 恒不命中，
    且 right=8 的 slot=0 被解成 d=-1 原样保留——玩家子弹方向**从不翻转**，镜像后
    obs 与 move 标签自相矛盾（plan/python-refactor.md P0-2，2026-09-02 修复）。

    修复：按 slot 解出方向 d（slot=0 → d=3 即 right），翻转 left↔right 后
    按通道语义重编码；`mirror(mirror(x)) == x` 对全部 8 方向 × 敌我成立
    （tests/test_dataset_mirror.py 逐例断言）。
    """
    col = channel.astype(np.int32)
    slot = col & 7
    d = (slot - 1) & 3  # slot∈1..4→0..3；5..7→0..2；0(player right)→3
    newd = np.where(d == 2, 3, np.where(d == 3, 2, d))
    if is_bullet:
        # 敌弹 hi=0；玩家 right=8 的 bit3 是 d+1+4 的溢出伪影，重编码时用加法还原
        player = (slot >= 5) | (slot == 0)
        val = np.where(player, newd + 1 + 4, newd + 1)
        return np.where(col > 0, val, 0).astype(np.uint8)
    hi = (col >> 3) & 0x1F
    return np.where(col > 0, (hi << 3) | (newd + 1), 0).astype(np.uint8)


def mirror_x(obs: np.ndarray, scalars: np.ndarray, move_label: int):
    """Return (obs', scalars', move_label') for a left-right reflection."""
    obs = obs.copy()
    obs = obs[:, :, ::-1].copy()  # flip width (copy -> positive strides for torch collate)
    for ch in DIRECTION_CHANNELS:
        obs[ch] = _flip_direction(obs[ch], is_bullet=(ch == CH["bullet"]))
    scalars = scalars.copy()
    for i in SCALAR_X_INDICES:
        scalars[i] = -scalars[i]
    return obs, scalars, int(_MOVE_FLIP[move_label])


class NNDataset(Dataset):
    """Wraps a concatenated sample dict; applies mirrorX on training draws.

    v2: actions = (N,2) [move, fire]；masks = (N,7) [move5, fire2]（item 头删除）。
    """

    def __init__(
        self,
        data: dict[str, np.ndarray],
        augment: bool = False,
        mirror_p: float = 0.5,
        rng: np.random.Generator | None = None,
    ):
        self.obs = data["obs"].astype(np.uint8)
        self.scalars = data["scalars"].astype(np.float32)
        self.actions = data["actions"].astype(np.int64)  # (N,2) move,fire
        self.masks = data["masks"].astype(np.float32)  # (N,7)
        self.conditions = data["conditions"].astype(np.int64)
        # v2: returns.npy（M3 value 头 MC 预置）可选——不存在时 n/a
        self.returns = (
            data["returns"].astype(np.float32)
            if "returns" in data
            else np.full(self.actions.shape[0], np.nan, dtype=np.float32)
        )
        self.augment = augment
        self.mirror_p = mirror_p
        self.rng = rng or np.random.default_rng(0)
        assert self.obs.shape[0] == self.scalars.shape[0] == self.actions.shape[0]

    def __len__(self) -> int:
        return int(self.obs.shape[0])

    def __getitem__(self, idx: int):
        obs = self.obs[idx]
        sc = self.scalars[idx]
        mv = int(self.actions[idx, 0])
        fr = int(self.actions[idx, 1])
        mask = self.masks[idx]
        ret = self.returns[idx]
        if self.augment and self.rng.random() < self.mirror_p:
            obs, sc, mv = mirror_x(obs, sc, mv)
        # Torch expects (C,H,W); obs is (C,H,W) already.
        return (
            obs,
            sc,
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
):
    """Build train/val DataLoaders from a directory of npy shards."""
    from data.npyio import load_dataset

    data = load_dataset(data_dir)
    full = NNDataset(data, augment=False)
    n = len(full)
    n_val = int(n * val_split)
    n_tr = n - n_val
    gen = torch.Generator().manual_seed(seed)
    train_sub, val_ds = random_split(full, [n_tr, n_val], generator=gen)
    # Re-wrap so augmentation only applies to the training split.
    train_ds = _AugWrapper(data, list(train_sub.indices), mirror_p, seed)
    return (
        DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers),
        DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=num_workers),
        {"train": n_tr, "val": n_val, "total": n},
    )


class _AugWrapper(Dataset):
    """Thin wrapper exposing NNDataset.__getitem__ with augmentation on a split."""

    def __init__(self, data: dict, indices: list[int], mirror_p: float, seed: int):
        self.inner = NNDataset(
            data, augment=True, mirror_p=mirror_p, rng=np.random.default_rng(seed)
        )
        self.indices = list(indices)

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, i):
        return self.inner[self.indices[i]]
