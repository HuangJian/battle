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

import numpy as np
import torch
from schema import (
    DIRECTION_CHANNELS,
    FIRE_DIM,
    MASK_DIM,
    MOVE_DIM,
    SCALAR_X_INDICES,
)
from torch.utils.data import DataLoader, Dataset, random_split

_MOVE_FLIP = np.array([0, 1, 2, 4, 3], dtype=np.int64)  # none,up,down,left<->right


def mirror_x(obs: np.ndarray, scalars: np.ndarray, move_label: int):
    """Return (obs', scalars', move_label') for a left-right reflection."""
    obs = obs.copy()
    obs = obs[:, :, ::-1].copy()  # flip width (copy -> positive strides for torch collate)
    for ch in DIRECTION_CHANNELS:
        col = obs[ch].astype(np.int32)
        mask = col > 0
        hi = (col >> 3) & 0x1F
        d = (col & 7) - 1  # -1..3
        newd = np.where(d == 2, 3, np.where(d == 3, 2, d))
        col = np.where(mask, (hi << 3) | (newd + 1), 0).astype(np.uint8)
        obs[ch] = col
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
        return self.obs.shape[0]

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
    from npyio import load_dataset

    data = load_dataset(data_dir)
    full = NNDataset(data, augment=False)
    n = len(full)
    n_val = int(n * val_split)
    n_tr = n - n_val
    gen = torch.Generator().manual_seed(seed)
    train_ds, val_ds = random_split(full, [n_tr, n_val], generator=gen)
    # Re-wrap so augmentation only applies to the training split.
    train_ds = _AugWrapper(data, train_ds.indices, mirror_p, seed)
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
