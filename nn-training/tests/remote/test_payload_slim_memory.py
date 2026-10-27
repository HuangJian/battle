"""`ShardSlim` 的**内存源**入口（2026-10-27 payload 零落盘）。

不变量：同一个 shard，用**盘源**（`member_bytes` / `shard_extra`）与**内存源**
（`member_bytes_of` / `shard_extra_of`）走出来的字节必须**完全相同** —— 否则 dispatch 腿
（HTTP 回传、不落盘）与 local 兜底腿（bun 直写盘）会得到两份不同的 payload。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.reward_library import METRICS_DIM, RewardSpec
from remote.payload_slim import F16_FILES, ShardSlim

N = 5  # samples
FNAME = "rl_s2000_seed1"


def _npy(arr: np.ndarray) -> bytes:
    import io

    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _shard_bytes() -> dict[str, bytes]:
    rng = np.random.default_rng(7)
    files = {
        "obs.npy": _npy(rng.integers(0, 2, size=(N, 16, 26, 26), dtype=np.uint8)),
        "scalars.npy": _npy(rng.random((N, 46), dtype=np.float32)),
        "extra.npy": _npy(rng.random((N, 37), dtype=np.float32)),
        "lp_move.npy": _npy(rng.random(N, dtype=np.float32)),
        "lp_fire.npy": _npy(rng.random(N, dtype=np.float32)),
        "metrics.npy": _npy(rng.random((N + 1, METRICS_DIM), dtype=np.float64)),
    }
    files["manifest.json"] = json.dumps(
        {"stage": 2000, "seed": 1, "nSamples": N, "outcome": "lives_exhausted", "score": 0.0}
    ).encode()
    return files


def _slim() -> ShardSlim:
    spec = RewardSpec(
        formula="wKill*kills + wWin*where(clearTick>=0, 1, 0)",
        params={"wKill": 3.0, "wWin": 48.0},
        terminal={"timeout": -35.0, "lives_exhausted": -35.0},
        scheme="toy",
    )
    return ShardSlim(reward_spec=spec, it=7, metrics_version=12, formula_hash="fh")


def test_memory_source_matches_disk_source(tmp_path: Path) -> None:
    files = _shard_bytes()
    d = tmp_path / FNAME
    d.mkdir()
    for name, data in files.items():
        (d / name).write_bytes(data)

    slim = _slim()
    # 盘源
    disk = {n: slim.member_bytes(d, n) for n in sorted(files)}
    disk_extra = slim.shard_extra(d)
    # 内存源（同一个 slim 实例：`_kind` 缓存按 shard 名，两条路必须得到同一判定）
    mem = {n: slim.member_bytes_of(FNAME, files, n) for n in sorted(files)}
    mem_extra = slim.shard_extra_of(FNAME, files)

    assert disk == mem, "盘源与内存源逐成员结果必须相同"
    assert disk_extra == mem_extra, "extra 成员（reward.npy / reward_meta.json）必须相同"

    # metrics 被丢弃、f16 字段被替换、其余原样 —— 三结局都覆盖到
    from common.payload import DROP_MEMBER

    assert disk["metrics.npy"] is DROP_MEMBER
    for n in sorted(F16_FILES):
        if n in files:
            assert isinstance(disk[n], bytes), f"{n} 应被替换成 f16 字节"
    assert disk["obs.npy"] is None, "obs 不在 f16 名单 ⇒ 原样"
    assert disk_extra.get("reward.npy") is not None, "reward.npy 必须由 `shard_extra` 产出"


def test_member_bytes_of_rejects_missing_member(tmp_path: Path) -> None:
    files = _shard_bytes()
    del files["scalars.npy"]
    # `_f16_bytes` 走 `src.array()` ⇒ 缺 key 时报「不可解析」（带成员名），语义足够定位。
    with pytest.raises(Exception, match=r"scalars\.npy"):
        _slim().member_bytes_of(FNAME, files, "scalars.npy")
