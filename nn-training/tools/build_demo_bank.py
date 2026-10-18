#!/usr/bin/env python3
"""Build a single demo bank npz from human BC shards (one-time, reproducible).

Reads nn-training/data/human-x20-corpus/shard_*/{obs,scalars,extra,actions,masks}.npy,
concatenates, writes demo_bank.npz (compressed) + prints sha256.

Output keys: obs (N,16,26,26) u1, scalars (N,40) f4, extra (N,37) f4,
actions (N,2) i8 [move,fire], masks (N,7) u1 [move5, fire2].
Same encodings as rollout chunks (ObsEncoder v5：POLICY_EXTRA 随样本走)。
维度一律从 `common.schema` 读（本脚本已因硬编码 9 漂过一次）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import house_parser, run_main
from common.schema import OBS_SCHEMA_MAJOR, POLICY_EXTRA_DIM, SCALAR_DIM

NN_ROOT = Path(__file__).resolve().parent.parent  # nn-training/ (script lives in tools/)


def main() -> None:
    ap = house_parser()
    ap.add_argument("--corpus", default=str(NN_ROOT / "data" / "human-x20-corpus"))
    ap.add_argument("--out", default=str(NN_ROOT / "data" / "human-x20-corpus" / "demo_bank.npz"))
    args = ap.parse_args()

    shards = sorted(Path(args.corpus).glob("shard_*"))
    if not shards:
        raise SystemExit(f"no shards in {args.corpus}")
    obs_l, sc_l, ex_l, ac_l, m_l = [], [], [], [], []
    total = 0
    for d in shards:
        o = np.load(d / "obs.npy")
        s = np.load(d / "scalars.npy")
        ex = np.load(d / "extra.npy")  # 旧 shard 缺 extra 在此响亮失败（须重导）
        a = np.load(d / "actions.npy")
        m = np.load(d / "masks.npy")
        assert o.shape[0] == s.shape[0] == a.shape[0] == m.shape[0] == ex.shape[0], d
        assert tuple(o.shape[1:]) == (16, 26, 26), (d, o.shape)
        assert s.ndim == 2 and s.shape[1] == SCALAR_DIM, (
            d,
            s.shape,
            f"期望 (N,{SCALAR_DIM})——旧 schema 语料须重导",
        )
        assert ex.ndim == 2 and ex.shape[1] == POLICY_EXTRA_DIM, (
            d,
            ex.shape,
            f"期望 (N,{POLICY_EXTRA_DIM})——旧 schema 语料须重导",
        )
        md_path = d / "manifest.json"
        assert md_path.is_file(), f"缺 manifest.json：{d}"
        md = json.loads(md_path.read_text(encoding="utf-8"))
        assert int(md.get("obsSchemaMajor", 0) or 0) >= OBS_SCHEMA_MAJOR, (
            d,
            md.get("obsSchemaMajor"),
            f"obsSchemaMajor < {OBS_SCHEMA_MAJOR}——旧目录须重导",
        )
        obs_l.append(o)
        sc_l.append(s.astype(np.float32))
        ex_l.append(ex.astype(np.float32))
        ac_l.append(a.astype(np.int64))
        m_l.append(m)
        total += o.shape[0]
    obs = np.concatenate(obs_l).astype(np.uint8)
    sc = np.concatenate(sc_l).astype(np.float32)
    ex = np.concatenate(ex_l).astype(np.float32)
    ac = np.concatenate(ac_l).astype(np.int64)
    mm = np.concatenate(m_l).astype(np.uint8)
    assert ac.shape[1] == 2 and mm.shape[1] == 7
    np.savez_compressed(args.out, obs=obs, scalars=sc, extra=ex, actions=ac, masks=mm)
    sha = hashlib.sha256(Path(args.out).read_bytes()).hexdigest()
    mb = Path(args.out).stat().st_size / 1e6
    print(f"[demo-bank] shards={len(shards)} N={total} out={args.out} {mb:.1f}MB sha={sha}")
    print(
        "[demo-bank] keys=['actions', 'extra', 'masks', 'obs', 'scalars'] "
        f"obs{obs.shape} scalars{sc.shape} extra{ex.shape} "
        f"actions{ac.shape} masks{mm.shape}"
    )


if __name__ == "__main__":
    run_main(main)
