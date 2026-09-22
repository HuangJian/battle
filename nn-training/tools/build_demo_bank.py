#!/usr/bin/env python3
"""Build a single demo bank npz from human BC shards (one-time, reproducible).

Reads nn-training/data/human-x20-corpus/shard_*/{obs,scalars,actions,masks}.npy,
concatenates, writes demo_bank.npz (compressed) + prints sha256.

Output keys: obs (N,16,26,26) u1, scalars (N,30) f4, actions (N,2) i8 [move,fire],
masks (N,7) u1 [move5, fire2]. Same encodings as rollout chunks (ObsEncoder v3).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np

NN_ROOT = Path(__file__).resolve().parent.parent  # nn-training/ (script lives in tools/)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(NN_ROOT / "data" / "human-x20-corpus"))
    ap.add_argument("--out", default=str(NN_ROOT / "data" / "human-x20-corpus" / "demo_bank.npz"))
    args = ap.parse_args()

    shards = sorted(Path(args.corpus).glob("shard_*"))
    if not shards:
        raise SystemExit(f"no shards in {args.corpus}")
    obs_l, sc_l, ac_l, m_l = [], [], [], []
    total = 0
    for d in shards:
        o = np.load(d / "obs.npy")
        s = np.load(d / "scalars.npy")
        a = np.load(d / "actions.npy")
        m = np.load(d / "masks.npy")
        assert o.shape[0] == s.shape[0] == a.shape[0] == m.shape[0], d
        assert tuple(o.shape[1:]) == (16, 26, 26), (d, o.shape)
        obs_l.append(o)
        sc_l.append(s.astype(np.float32))
        ac_l.append(a.astype(np.int64))
        m_l.append(m)
        total += o.shape[0]
    obs = np.concatenate(obs_l).astype(np.uint8)
    sc = np.concatenate(sc_l).astype(np.float32)
    ac = np.concatenate(ac_l).astype(np.int64)
    mm = np.concatenate(m_l).astype(np.uint8)
    assert ac.shape[1] == 2 and mm.shape[1] == 7
    np.savez_compressed(args.out, obs=obs, scalars=sc, actions=ac, masks=mm)
    sha = hashlib.sha256(Path(args.out).read_bytes()).hexdigest()
    mb = Path(args.out).stat().st_size / 1e6
    print(f"[demo-bank] shards={len(shards)} N={total} out={args.out} {mb:.1f}MB sha={sha}")
    print(f"[demo-bank] obs{obs.shape} scalars{sc.shape} actions{ac.shape} masks{mm.shape}")


if __name__ == "__main__":
    main()
