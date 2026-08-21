"""
ppo.py — Clipped PPO for the Battle-City student (承接 P1.5 蒸馏 → RL).

Consumes trajectory shards written by `tools/sim/export-rl-rollout.ts`
(per-episode npy: obs/scalars/a_*/lp_*/value/reward/done/mask) and runs
clipped PPO with a shared trunk (warm-started from the DAgger BC checkpoint)
+ a value head.

Two modes:
  --init-from BC_WEIGHTS --out RL_WEIGHTS
      Build PPOStudent, warm-start policy heads from the BC checkpoint
      (value head stays random), save RL weights. Used once before the loop.
  --resume RL_WEIGHTS --data TRAJ_DIR --out RL_WEIGHTS --epochs K
      Load RL weights (policy+value), run K PPO epochs over the collected
      trajectories, overwrite RL weights. Called once per RL iteration.

Weight format is the canonical JSON+base64 (`weights_io.save_weights_json`),
so the TS runtime reloads it byte-for-byte.

Usage (via start-training.{sh,ps1} which provides the venv + torch):
  python ppo.py --init-from tmp/student-weights-dagger/weights.json \
      --out tmp/rl-weights/weights.json
  python ppo.py --resume tmp/rl-weights/weights.json \
      --data tmp/rl-traj/it1 --out tmp/rl-weights/weights.json --epochs 4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from typing import Any, Dict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM, MOVE_DIM, FIRE_DIM, ITEM_DIM
from student_model import PPOStudent
from weights_io import load_weights_json, save_weights_json


# ---------------- hyper-params (CLI-overridable) ----------------
GAMMA = 0.99
LAM = 0.95
CLIP_EPS = 0.2
VF_COEF = 0.5
ENT_COEF = 0.01
LR = 3e-4
MAX_GRAD_NORM = 1.0
MASK_DIM = MOVE_DIM + FIRE_DIM + ITEM_DIM


def build_ppo(weights_path: str | None) -> PPOStudent:
    h = d = head_hidden = None
    if weights_path and os.path.exists(weights_path):
        meta, _ = load_weights_json(weights_path)
        a = meta.get("arch", {})
        h = a.get("h", 64)
        d = a.get("d", 8)
        head_hidden = a.get("head_hidden", 128)
    return PPOStudent(h=h or 64, d=d or 8, head_hidden=head_hidden or 128)


# ---------------- trajectory loading ----------------
def discover_rl_shards(root: str) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if "reward.npy" in files and "obs.npy" in files:
            out.append(dirpath)
    return sorted(out)


def load_shard(dirpath: str) -> Dict[str, np.ndarray]:
    def npy(name: str) -> np.ndarray:
        return np.load(os.path.join(dirpath, name))

    return {
        "obs": npy("obs.npy").astype(np.uint8),
        "scalars": npy("scalars.npy").astype(np.float32),
        "a_move": npy("a_move.npy").astype(np.int64),
        "a_fire": npy("a_fire.npy").astype(np.int64),
        "a_item": npy("a_item.npy").astype(np.int64),
        "lp_move": npy("lp_move.npy").astype(np.float32),
        "lp_fire": npy("lp_fire.npy").astype(np.float32),
        "lp_item": npy("lp_item.npy").astype(np.float32),
        "value": npy("value.npy").astype(np.float32),
        "reward": npy("reward.npy").astype(np.float32),
        "done": npy("done.npy").astype(np.int64),
        "mask": npy("mask.npy").astype(np.int64),
    }


def compute_gae(rewards, values, dones, gamma, lam):
    """Per-episode GAE. rewards[t]=r_{t+1}, values[t]=V(s_t)."""
    N = len(rewards)
    adv = np.zeros(N, dtype=np.float32)
    last = 0.0
    for t in reversed(range(N)):
        non_term = 1.0 - float(dones[t])
        next_value = 0.0 if t + 1 >= N else float(values[t + 1])
        delta = float(rewards[t]) + gamma * next_value - float(values[t])
        last = delta + gamma * lam * non_term * last
        adv[t] = last
    ret = adv + np.asarray(values, dtype=np.float32)
    return adv, ret


# ---------------- policy helpers ----------------
def masked_logsoftmax(logits: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    # mask: 1 valid, 0 invalid → push invalid to -inf
    big = torch.tensor(1e9, device=logits.device, dtype=logits.dtype)
    m = mask.to(logits.dtype)
    return F.log_softmax(logits + (1.0 - m) * (-big), dim=-1)


def cat_logprob(action: torch.Tensor, logp: torch.Tensor) -> torch.Tensor:
    return logp.gather(1, action.unsqueeze(1)).squeeze(1)


def cat_entropy(logp: torch.Tensor) -> torch.Tensor:
    return -(logp.exp() * logp).sum(dim=-1).mean()


def load_episodes(data_root: str, gamma: float = GAMMA, lam: float = LAM) -> list[dict]:
    """Discover trajectory shards under `data_root`, compute per-episode GAE,
    and normalize advantages across the whole batch. Shared by this CLI's
    update mode and the run_rl.py loop."""
    shards = discover_rl_shards(data_root)
    if not shards:
        raise SystemExit(f"[ppo] no RL shards found under {data_root}")
    print(f"[ppo] loaded {len(shards)} trajectory shards from {data_root}")

    episodes: list[dict] = []
    for sd in shards:
        d = load_shard(sd)
        N = d["obs"].shape[0]
        if N == 0:
            continue
        adv, ret = compute_gae(d["reward"], d["value"], d["done"], gamma, lam)
        episodes.append(
            {
                "obs": d["obs"],
                "scalars": d["scalars"],
                "a_move": d["a_move"],
                "a_fire": d["a_fire"],
                "a_item": d["a_item"],
                "lp_move": d["lp_move"],
                "lp_fire": d["lp_fire"],
                "lp_item": d["lp_item"],
                "value": d["value"],
                "adv": adv.astype(np.float32),
                "ret": ret.astype(np.float32),
                "mask": d["mask"],
            }
        )

    all_adv = np.concatenate([e["adv"] for e in episodes])
    mean, std = all_adv.mean(), all_adv.std() + 1e-8
    for e in episodes:
        e["adv"] = (e["adv"] - mean) / std
    return episodes


# ---------------- PPO update ----------------
def chunk_episodes(episodes: list[dict], mb: int) -> list[dict]:
    """Split per-episode dicts into fixed-size minibatch chunks (last chunk ragged).

    GAE is computed per-episode BEFORE chunking; chunks are only an update-
    granularity unit (bounds activation memory, adds gradient steps).
    """
    out: list[dict] = []
    for e in episodes:
        n = e["obs"].shape[0]
        for s in range(0, n, mb):
            out.append({k: v[s:s + mb] for k, v in e.items()})
    return out


def ppo_update(model, opt, chunks, epochs, device):
    """chunks: list of minibatch dicts (obs (B,14,26,26) / scalars (B,24) / ...)."""
    model.train()
    clip = CLIP_EPS
    stats = []
    for _ in range(epochs):
        perm = np.random.permutation(len(chunks))
        for i in perm:
            e = chunks[int(i)]
            obs = torch.from_numpy(e["obs"]).to(device)
            sc = torch.from_numpy(e["scalars"]).to(device)
            a_move = torch.from_numpy(e["a_move"]).to(device)
            a_fire = torch.from_numpy(e["a_fire"]).to(device)
            a_item = torch.from_numpy(e["a_item"]).to(device)
            lp_move = torch.from_numpy(e["lp_move"]).to(device)
            lp_fire = torch.from_numpy(e["lp_fire"]).to(device)
            lp_item = torch.from_numpy(e["lp_item"]).to(device)
            adv = torch.from_numpy(e["adv"]).to(device)
            ret = torch.from_numpy(e["ret"]).to(device)
            mask = torch.from_numpy(e["mask"]).to(device)  # (T, 10)

            mv, fr, it, val = model(obs, sc)
            move_logp = masked_logsoftmax(mv, mask[:, :MOVE_DIM])
            fire_logp = masked_logsoftmax(fr, mask[:, MOVE_DIM:MOVE_DIM + FIRE_DIM])
            item_logp = masked_logsoftmax(it, mask[:, MOVE_DIM + FIRE_DIM:])

            lp_new = (
                cat_logprob(a_move, move_logp)
                + cat_logprob(a_fire, fire_logp)
                + cat_logprob(a_item, item_logp)
            )
            lp_old = lp_move + lp_fire + lp_item

            ratio = torch.exp(lp_new - lp_old)
            surr1 = ratio * adv
            surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
            policy_loss = -torch.min(surr1, surr2).mean()

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = cat_entropy(move_logp) + cat_entropy(fire_logp) + cat_entropy(item_logp)

            loss = policy_loss + VF_COEF * value_loss - ENT_COEF * entropy

            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            opt.step()

            with torch.no_grad():
                approx_kl = ((lp_old - lp_new) ** 2).mean().item()
            stats.append(
                {
                    "policy": float(policy_loss.item()),
                    "value": float(value_loss.item()),
                    "entropy": float(entropy.item()),
                    "kl": float(approx_kl),
                    "mean_ret": float(ret.mean().item()),
                    "mean_adv": float(adv.mean().item()),
                }
            )
    # aggregate
    n = len(stats)
    agg = {k: sum(s[k] for s in stats) / n for k in stats[0]}
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-from", type=str, default=None, help="BC weights to warm-start from (init mode)")
    ap.add_argument("--resume", type=str, default=None, help="RL weights to resume (update mode)")
    ap.add_argument("--data", type=str, default=None, help="trajectory shard root (update mode)")
    ap.add_argument("--out", type=str, required=True, help="output weights path")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=256, help="minibatch size (transitions per update step)")
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--gamma", type=float, default=GAMMA)
    ap.add_argument("--lam", type=float, default=LAM)
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument("--seed", type=int, default=7, help="numpy seed for minibatch shuffling")
    args = ap.parse_args()

    np.random.seed(args.seed)
    device = torch.device(args.device)

    # ---- init mode ----
    if args.init_from and not args.data:
        model = build_ppo(args.init_from)
        # warm-start policy heads from BC (value head stays random)
        from weights_io import load_state_into

        load_state_into(model, args.init_from)
        save_weights_json(model, args.out)
        print(f"[ppo] init RL weights (BC policy + random value) -> {args.out}")
        print(f"[ppo] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    # ---- update mode ----
    assert args.resume and args.data, "--resume and --data required in update mode"
    model = build_ppo(args.resume)
    from weights_io import load_state_into

    load_state_into(model, args.resume)
    model.to(device)

    episodes = load_episodes(args.data, args.gamma, args.lam)
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    print(f"[ppo] total transition steps={total_steps}")

    chunks = chunk_episodes(episodes, args.mb)
    print(f"[ppo] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    agg = ppo_update(model, opt, chunks, args.epochs, device)

    model.to("cpu")
    save_weights_json(model, args.out)
    print(
        f"[ppo] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} "
        f"mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )


if __name__ == "__main__":
    main()
