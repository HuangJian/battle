"""
npy IO for the NN training pipeline.

The TS exporter (`src/nn/npy.ts`) hand-writes standard NumPy 1.0 `.npy` files
(no external dependency). This module reads them with `numpy.load` and also
provides a synthetic writer used only by `smoke_test.py`.

Standard `.npy` (v1.0) layout (must match `src/nn/npy.ts`):
    magic  \x93NUMPY          (6 bytes)
    version 0x01 0x00          (2 bytes)
    header_len uint16 LE       (2 bytes)
    header  dict repr + spaces, 64-byte aligned
    raw bytes (C order)
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import numpy as np

# Per-shard file names produced by the TS exporter.
SHARD_FILES = {
    "obs": "obs.npy",  # uint8  (N, 14, 26, 26)
    "scalars": "scalars.npy",  # float32 (N, 19)
    "actions": "actions.npy",  # uint8  (N, 2)  [move, fire] (v2: item 头删除)
    "masks": "masks.npy",  # uint8  (N, 7) [move5, fire2], 1=valid
    "conditions": "conditions.npy",  # uint8 (N,) decision condition
}
MANIFEST_FILE = "manifest.json"


def read_npy(path: str) -> np.ndarray:
    """Read a standard .npy file written by either numpy or the TS writer."""
    return np.load(path, allow_pickle=False)


OPTIONAL_FILES = {"returns": "returns.npy"}  # v2: M3 value MC 预置（可选）


def load_shard(shard_dir: str) -> dict[str, np.ndarray]:
    """Load one exported replay shard into a dict of numpy arrays."""
    out: dict[str, np.ndarray] = {}
    for key, fname in SHARD_FILES.items():
        p = os.path.join(shard_dir, fname)
        if not os.path.exists(p):
            raise FileNotFoundError(f"missing shard file {p}")
        out[key] = read_npy(p)
    for key, fname in OPTIONAL_FILES.items():
        p = os.path.join(shard_dir, fname)
        if os.path.exists(p):
            out[key] = read_npy(p)
    return out


def scan_shards(data_dir: str) -> list[str]:
    """Find all shard directories under `data_dir` (each contains obs.npy)."""
    shards: list[str] = []
    for root, _dirs, files in os.walk(data_dir):
        if SHARD_FILES["obs"] in files:
            shards.append(root)
    return sorted(shards)


def load_dataset(data_dir: str) -> dict[str, np.ndarray]:
    """Concatenate every shard under `data_dir` into one big sample dict."""
    shards = scan_shards(data_dir)
    if not shards:
        raise FileNotFoundError(f"no shards (obs.npy) found under {data_dir}")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in SHARD_FILES}
    opt_parts: dict[str, list[np.ndarray]] = {k: [] for k in OPTIONAL_FILES}
    for s in shards:
        d = load_shard(s)
        for k in SHARD_FILES:
            parts[k].append(d[k])
        for k in OPTIONAL_FILES:
            if k in d:
                opt_parts[k].append(d[k])
    out = {k: np.concatenate(v, axis=0) for k, v in parts.items()}
    for k, v in opt_parts.items():
        if v:
            # 可选文件部分 shard 缺失 → 拼接后以 NaN 补齐（与 dataset.py 的 n/a 语义一致）
            n = out["obs"].shape[0]
            full = np.full((n,), np.nan, dtype=np.float32)
            off = 0
            for arr in v:
                full[off : off + arr.shape[0]] = arr
                off += arr.shape[0]
            out[k] = full
    return out


def save_shard(shard_dir: str, arrays: dict[str, np.ndarray], manifest: dict[str, Any]) -> None:
    """Write a shard (numpy) — used by smoke_test to synthesize data."""
    os.makedirs(shard_dir, exist_ok=True)
    for key, fname in SHARD_FILES.items():
        np.save(
            os.path.join(shard_dir, fname),
            arrays[key].astype(
                np.uint8 if key in ("obs", "actions", "masks", "conditions") else np.float32
            ),
        )
    with open(os.path.join(shard_dir, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
