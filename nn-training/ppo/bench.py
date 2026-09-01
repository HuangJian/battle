"""ppo-bench.py — PPO 计算基准（吞吐 plan T1/T2）。

不重新 rollout：直接复用已有回合 shards（如 tmp/s3-cap2/it3 的 obs/act/... ），
只重跑 chunk_episodes + ppo_update 计时——OMP/mb 的差异全部落在 PPO 计算段。

用法（必须经 start-training.sh 保证 venv/torch；OMP 由 --torch-threads 控制）：
  bash nn-training/start-training.sh --torch-threads 12 --script ppo-bench.py \
      --shards tmp/s3-cap2/it3 --weights tmp/s3-cap2/weights.json --mb 512 --epochs 4

输出：json 一行（chunks / ppo_sec / chunk_time / kl / entropy），追加 tmp/thru-bench.jsonl。
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import torch

import ppo.engine as ppo_mod
from data.weights_io import load_state_into


def collect_shard_dirs(root: str):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if "obs.npy" in files and "done.npy" in files:
            out.append(dirpath)
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True, help="traj 目录（如 tmp/s3-cap2/it3）")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    dirs = collect_shard_dirs(args.shards)
    if not dirs:
        print(f"[ppo-bench] no shards under {args.shards}")
        sys.exit(1)
    eps = []
    for d in dirs:
        try:
            ep = ppo_mod.load_episode_from_shard(d)
        except Exception as e:
            print(f"[ppo-bench] skip bad shard {d}: {e}")
            continue
        if ep is not None:
            eps.append(ep)
    if not eps:
        print("[ppo-bench] no valid episodes")
        sys.exit(1)
    # adv 归一化（与 rl/stream._load_wave 同口径，PPO 主循环一致）
    all_adv = np.concatenate([ep["adv"] for ep in eps])
    mean, std = all_adv.mean(), all_adv.std() + 1e-8
    for ep in eps:
        ep["adv"] = ((ep["adv"] - mean) / std).astype(np.float32)
    print(f"[ppo-bench] episodes={len(eps)} shards={len(dirs)} mb={args.mb} epochs={args.epochs}")

    model = ppo_mod.build_ppo(args.weights)
    load_state_into(model, args.weights)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    t0 = time.time()
    chunks = ppo_mod.chunk_episodes(eps, args.mb)
    t_chunk = time.time() - t0
    agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, args.device)
    ppo_sec = round(time.time() - t0 - t_chunk, 1)  # 纯 PPO 计算（不含 chunk 组装）
    out = {
        "event": "ppo_bench",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "shards": args.shards,
        "episodes": len(eps),
        "chunks": len(chunks),
        "chunk_assemble_sec": round(t_chunk, 1),
        "ppo_sec": ppo_sec,
        "chunk_time": round(ppo_sec / max(1, len(chunks)), 3),
        # ppo_update 返回聚合 dict（非 stats list）
        "kl": round(float(agg.get("kl", 0.0)), 4),
        "entropy": round(float(agg.get("entropy", 0.0)), 4),
        "mb": args.mb,
        "epochs": args.epochs,
        "lr": args.lr,
    }
    line = json.dumps(out, ensure_ascii=False)
    print(f"[ppo-bench] {line}")
    os.makedirs("tmp", exist_ok=True)
    with open("tmp/thru-bench.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
