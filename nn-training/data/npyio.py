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
from typing import Any

import numpy as np

# Per-shard file names produced by the TS exporter.
SHARD_FILES = {
    "obs": "obs.npy",  # uint8  (N, OBS_CHANNELS, BOARD, BOARD) — v3: (N, 16, 26, 26)
    "scalars": "scalars.npy",  # float32 (N, SCALAR_DIM) — v3: (N, 30)
    "actions": "actions.npy",  # uint8  (N, 2)  [move, fire] (v2: item 头删除)
    "masks": "masks.npy",  # uint8  (N, 7) [move5, fire2], 1=valid
    "conditions": "conditions.npy",  # uint8 (N,) decision condition
}
MANIFEST_FILE = "manifest.json"


def read_npy(path: str) -> np.ndarray:
    """Read a standard .npy file written by either numpy or the TS writer."""
    # np.load 存根返回 Any（mmap/NpzFile 多态），显式收窄为本函数契约的 ndarray。
    arr: np.ndarray = np.load(path, allow_pickle=False)
    return arr


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


def verify_shard_schema(shard_dir: str, arrays: dict[str, np.ndarray]) -> None:
    """语料身份校验（v3，obs spec §3.4-7）：形状 + shard manifest 指纹。

    major bump 后旧语料**全部作废**（权重同理）。两道判据：
      · 形状（最硬，无 manifest 也判）：obs 必须 (N, OBS_CHANNELS, BOARD, BOARD)、
        scalars 必须 (N, SCALAR_DIM)；
      · 指纹（manifest 有 `schemaFingerprint` 时判）：与当前 schema 常量不符即错。
    manifest 缺指纹（v2 及更早产物）不豁免形状判据——形状不符同样 raise。
    """
    from schema import BOARD, OBS_CHANNELS, SCALAR_DIM, SCHEMA_FINGERPRINT

    obs = arrays.get("obs")
    if obs is not None and (obs.ndim != 4 or tuple(obs.shape[1:]) != (OBS_CHANNELS, BOARD, BOARD)):
        raise ValueError(
            f"[npyio] {shard_dir}: obs 形状 {tuple(obs.shape)} 与当前 schema 不符"
            f"（期望 (N, {OBS_CHANNELS}, {BOARD}, {BOARD})）——该 shard 来自另一个"
            f"schema major，语料已作废，请重新导出（major bump ⇒ 旧 shard 全废）"
        )
    sc = arrays.get("scalars")
    if sc is not None and (sc.ndim != 2 or sc.shape[1] != SCALAR_DIM):
        raise ValueError(
            f"[npyio] {shard_dir}: scalars 形状 {tuple(sc.shape)} 与当前 schema 不符"
            f"（期望 (N, {SCALAR_DIM})）——请重新导出该 shard"
        )
    mpath = os.path.join(shard_dir, MANIFEST_FILE)
    if not os.path.exists(mpath):
        return
    try:
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict):
        return
    fp = manifest.get("schemaFingerprint")
    if isinstance(fp, str) and fp and fp != SCHEMA_FINGERPRINT:
        raise ValueError(
            f"[npyio] {shard_dir}: manifest 指纹 {fp} != 当前 schema {SCHEMA_FINGERPRINT}"
            "——该 shard 来自另一个 schema 版本，语料已作废，请重新导出"
        )


def scan_shards(data_dir: str) -> list[str]:
    """Find all shard directories under `data_dir` (each contains obs.npy)."""
    shards: list[str] = []
    for root, _dirs, files in os.walk(data_dir):
        if SHARD_FILES["obs"] in files:
            shards.append(root)
    return sorted(shards)


def load_dataset(data_dir: str) -> dict[str, np.ndarray]:
    """Concatenate every shard under `data_dir` into one big sample dict.

    P2-6d（2026-09-02）：额外产出 **"shard_ids"**（(N,) int64，每样本所属 shard
    索引）——让 dataset 层能做 **shard 级 train/val 切分**，杜绝"同局相邻帧跨集
    泄漏 → val 虚高"。旧调用方只读已知键，不受影响。

    v3（obs-schema-v3.plan.md v4.0 §3.4-7）：逐个 shard 跑 **语料身份校验**
    （`verify_shard_schema`）——v2 的 14ch/19sc shard 混进 v3 语料会静默错位成
    一张「半张脸」的观测（major bump 全旧权重失效 ⇒ 同理旧语料全废），必须先
    炸在加载处而不是等训练曲线发疯。
    """
    shards = scan_shards(data_dir)
    if not shards:
        raise FileNotFoundError(f"no shards (obs.npy) found under {data_dir}")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in SHARD_FILES}
    opt_parts: dict[str, list[np.ndarray]] = {k: [] for k in OPTIONAL_FILES}
    for s in shards:
        d = load_shard(s)
        verify_shard_schema(s, d)
        for k in SHARD_FILES:
            parts[k].append(d[k])
        for k in OPTIONAL_FILES:
            if k in d:
                opt_parts[k].append(d[k])
    out = {k: np.concatenate(v, axis=0) for k, v in parts.items()}
    out["shard_ids"] = np.repeat(
        np.arange(len(shards), dtype=np.int64), [v.shape[0] for v in parts["obs"]]
    )
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
