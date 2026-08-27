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
import time
from typing import Any, Dict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM, MOVE_DIM, FIRE_DIM
from student_model import PPOStudent
from weights_io import load_weights_json, save_weights_json


# ---------------- hyper-params (CLI-overridable) ----------------
# R6（2026-08-25 训练质量审计）：it1–it68 未收敛（winRate ~10% 水平、value 预测量级
# ~0.03-0.09 vs 回报 0.1-0.3 → GAE 优势被噪声主导、policy loss≈0）。两处收紧：
#   GAMMA 0.99 → 0.995：决策间隔 K=10 下有效时域从 ~100 决策(16.7s) 拉长到 ~200
#     决策(33s)——守家/拦截是长时域行为，需要更远的信用回溯；
#   VF_COEF 0.5 → 1.0：价值头训练强度翻倍，缩小 value loss 与 policy loss 的量级差，
#     让 baseline 脱离噪声、给策略梯度注入真实优势信号。
GAMMA = 0.995
LAM = 0.95
CLIP_EPS = 0.2
VF_COEF = 1.0
ENT_COEF = 0.01
LR = 3e-4
MAX_GRAD_NORM = 1.0
MASK_DIM = MOVE_DIM + FIRE_DIM  # 7 (v2: item head removed)

# Observability cadences (pure logging; never touches RNG or numerics).
LOAD_LOG_EVERY = 128   # shard-loading progress lines
HB_SEC = 60.0          # PPO update heartbeat interval



def log(msg: str) -> None:
    """Timestamped log line (matches run_rl.log format)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

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

    # copy=False：dtype 已符合时零拷贝——obs 每轮 ~550MB，无谓 astype 拷贝纯烧内存带宽。
    return {
        "obs": npy("obs.npy").astype(np.uint8, copy=False),
        "scalars": npy("scalars.npy").astype(np.float32, copy=False),
        "a_move": npy("a_move.npy").astype(np.int64, copy=False),
        "a_fire": npy("a_fire.npy").astype(np.int64, copy=False),
        "lp_move": npy("lp_move.npy").astype(np.float32, copy=False),
        "lp_fire": npy("lp_fire.npy").astype(np.float32, copy=False),
        "value": npy("value.npy").astype(np.float32, copy=False),
        "reward": npy("reward.npy").astype(np.float32, copy=False),
        "done": npy("done.npy").astype(np.int64, copy=False),
        "mask": npy("mask.npy").astype(np.int64, copy=False),
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


def load_episode_from_shard(dirpath: str, gamma: float = GAMMA, lam: float = LAM) -> dict | None:
    """流式 backend 接口（rl/stream.py）：单个 shard → 可训练 episode（adv/ret 未归一）。

    ppo_intent.load_episode_from_shard 同签名——run_rollout_stream 以 backend 参数
    复用同一套流式基础设施（工程化共享，勿在 stream 内复制第二份加载逻辑）。
    """
    d = load_shard(dirpath)
    N = d["obs"].shape[0]
    if N == 0:
        return None
    adv, ret = compute_gae(d["reward"], d["value"], d["done"], gamma, lam)
    return {
        "obs": d["obs"],
        "scalars": d["scalars"],
        "a_move": d["a_move"],
        "a_fire": d["a_fire"],
        "lp_move": d["lp_move"],
        "lp_fire": d["lp_fire"],
        "value": d["value"],
        "adv": adv.astype(np.float32),
        "ret": ret.astype(np.float32),
        "mask": d["mask"],
    }


def load_episodes(data_root: str, gamma: float = GAMMA, lam: float = LAM) -> list[dict]:
    """Discover trajectory shards under `data_root`, compute per-episode GAE,
    and normalize advantages across the whole batch. Shared by this CLI's
    update mode and the run_rl.py loop."""
    shards = discover_rl_shards(data_root)
    if not shards:
        raise SystemExit(f"[ppo] no RL shards found under {data_root}")
    log(f"[ppo] loaded {len(shards)} trajectory shards from {data_root}")

    episodes: list[dict] = []
    t_load = time.time()
    for k, sd in enumerate(shards):
        if k > 0 and k % LOAD_LOG_EVERY == 0:
            log(f"[ppo] loading shards {k}/{len(shards)} ({time.time() - t_load:.0f}s)")
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
                "lp_move": d["lp_move"],
                "lp_fire": d["lp_fire"],
                "value": d["value"],
                "adv": adv.astype(np.float32),
                "ret": ret.astype(np.float32),
                "mask": d["mask"],
            }
        )

    log(f"[ppo] shard IO + GAE done for {len(episodes)} episodes "
        f"({time.time() - t_load:.0f}s)")
    all_adv = np.concatenate([e["adv"] for e in episodes])
    mean, std = all_adv.mean(), all_adv.std() + 1e-8
    for e in episodes:
        e["adv"] = (e["adv"] - mean) / std
    return episodes


# ---------------- PPO update ----------------
def _pack_np_state() -> list:
    """numpy MT19937 全局状态 → JSON 可序列化（续跑需精确重建 epoch 乱序）。"""
    s = np.random.get_state()
    return [s[0], s[1].tolist(), s[2], s[3], s[4]]


def _unpack_np_state(packed: list) -> None:
    np.random.set_state((packed[0], np.asarray(packed[1], dtype=np.uint32),
                         packed[2], packed[3], packed[4]))


def _ppo_save(ckpt_path: str, model, opt, epochs_done: int) -> None:
    """epoch 粒度 checkpoint：model+optimizer 状态 + 已完成 epoch 数 + numpy RNG。
    恢复粒度 = 一个 epoch（从最近 checkpoint 续，重跑该 epoch 的梯度步，秒级）。"""
    os.makedirs(ckpt_path, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(ckpt_path, "model.pt"))
    torch.save(opt.state_dict(), os.path.join(ckpt_path, "opt.pt"))
    with open(os.path.join(ckpt_path, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"epochs_done": epochs_done, "rng": _pack_np_state()}, f)


def _ppo_load(ckpt_path: str | None, model, opt) -> int:
    """返回已完成 epoch 数（0=无 checkpoint / 无法加载）。加载 model/opt + 恢复 RNG。"""
    if not ckpt_path:
        return 0
    sp = os.path.join(ckpt_path, "state.json")
    mp = os.path.join(ckpt_path, "model.pt")
    op = os.path.join(ckpt_path, "opt.pt")
    if not all(os.path.exists(p) for p in (sp, mp, op)):
        return 0
    with open(sp, encoding="utf-8") as f:
        st = json.load(f)
    model.load_state_dict(torch.load(mp, map_location="cpu"))
    opt.load_state_dict(torch.load(op, map_location="cpu"))
    _unpack_np_state(st["rng"])
    return int(st.get("epochs_done", 0))


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


def ppo_update(model, opt, chunks, epochs, device, ckpt_path: str | None = None):
    """chunks: list of minibatch dicts (obs (B,14,26,26) / scalars (B,24) / ...).

    ckpt_path: 非空则每 epoch 落盘 checkpoint（model/opt/epochs_done/numpy RNG），
    并支持断点续跑（重启后从已完成 epoch 数继续，minibatch 乱序精确复现）。
    """
    model.train()
    clip = CLIP_EPS
    stats = []
    # Convert numpy -> torch ONCE per chunk (not once per epoch): identical
    # values, ~epochs× less conversion overhead.
    tensored = [
        {k: torch.from_numpy(v).to(device) for k, v in c.items()} for c in chunks
    ]
    total_steps = len(tensored) * epochs
    log(f"[ppo] update start: {len(tensored)} chunks x {epochs} epochs "
        f"(~{total_steps} grad steps)")
    t0 = time.time()
    last_hb = t0
    start_epoch = _ppo_load(ckpt_path, model, opt)
    if start_epoch:
        log(f"[ppo] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done "
            f"(continuing remaining {epochs - start_epoch})")
    for ep in range(start_epoch, epochs):
        perm = np.random.permutation(len(tensored))
        n_ep_start = len(stats)
        for j, i in enumerate(perm):
            e = tensored[int(i)]
            obs = e["obs"]
            sc = e["scalars"]
            a_move = e["a_move"]
            a_fire = e["a_fire"]
            lp_move = e["lp_move"]
            lp_fire = e["lp_fire"]
            adv = e["adv"]
            ret = e["ret"]
            mask = e["mask"]  # (T, 7)

            mv, fr, val = model(obs, sc)
            move_logp = masked_logsoftmax(mv, mask[:, :MOVE_DIM])
            fire_logp = masked_logsoftmax(fr, mask[:, MOVE_DIM:MOVE_DIM + FIRE_DIM])

            lp_new = (
                cat_logprob(a_move, move_logp)
                + cat_logprob(a_fire, fire_logp)
            )
            lp_old = lp_move + lp_fire

            ratio = torch.exp(lp_new - lp_old)
            surr1 = ratio * adv
            surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
            policy_loss = -torch.min(surr1, surr2).mean()

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = cat_entropy(move_logp) + cat_entropy(fire_logp)

            loss = policy_loss + VF_COEF * value_loss - ENT_COEF * entropy

            opt.zero_grad()
            loss.backward()
            gn = nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
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
                    "gnorm": float(gn),
                }
            )
            # Heartbeat: pure-print progress/health line; wall-clock only.
            now = time.time()
            if now - last_hb >= HB_SEC:
                last_hb = now
                recent = stats[-32:]
                n_r = len(recent)
                done_steps = ep * len(tensored) + j + 1
                elapsed = now - t0
                eta = elapsed / done_steps * (total_steps - done_steps)
                log(
                    f"[ppo] ep {ep + 1}/{epochs} chunk {j + 1}/{len(tensored)} "
                    f"step {done_steps}/{total_steps} "
                    f"elapsed={elapsed:.0f}s eta~{eta:.0f}s "
                    f"kl={sum(s['kl'] for s in recent) / n_r:.4f} "
                    f"entropy={sum(s['entropy'] for s in recent) / n_r:.4f} "
                    f"policy={sum(s['policy'] for s in recent) / n_r:.4f} "
                    f"value={sum(s['value'] for s in recent) / n_r:.4f} "
                    f"gnorm={sum(s['gnorm'] for s in recent) / n_r:.3f}"
                )
        if ckpt_path:
            _ppo_save(ckpt_path, model, opt, ep + 1)
        ep_stats = stats[n_ep_start:]
        n_e = max(1, len(ep_stats))
        log(f"[ppo] epoch {ep + 1}/{epochs} done ({time.time() - t0:.0f}s total, "
            f"{len(ep_stats)} chunks)"
            + (", ckpt saved" if ckpt_path else "")
            + f": kl={sum(s['kl'] for s in ep_stats) / n_e:.4f} "
              f"entropy={sum(s['entropy'] for s in ep_stats) / n_e:.4f} "
              f"policy={sum(s['policy'] for s in ep_stats) / n_e:.4f} "
              f"value={sum(s['value'] for s in ep_stats) / n_e:.4f} "
              f"gnorm={sum(s['gnorm'] for s in ep_stats) / n_e:.3f}")
    # aggregate
    if not stats:
        # 断点续跑"剩余 0 epoch"路径（checkpoint 已完成）：无梯度步可跑，
        # 返回零聚合——此前 stats[0] 直接 IndexError 让整轮重试空转。
        log("[ppo] checkpoint already complete — 0 grad steps, returning zero aggregate")
        return {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0,
                "gnorm": 0.0, "mean_ret": 0.0}
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
    ap.add_argument("--mb", type=int, default=512,
                    help="minibatch size (transitions per update step)")
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--gamma", type=float, default=GAMMA)
    ap.add_argument("--lam", type=float, default=LAM)
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument("--seed", type=int, default=7, help="numpy seed for minibatch shuffling")
    ap.add_argument("--threads", type=int, default=8,
                    help="torch intra-op threads; 0 keeps the launcher default "
                         "(OMP_NUM_THREADS). 8 = physical cores on the dev box — "
                         "avoids HT contention + OMP sync overhead on this small model.")
    args = ap.parse_args()

    np.random.seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    try:
        # Denormal floats can slow small-model CPU convs by large factors;
        # flushing them is numerically negligible (~0 values) and often much
        # faster. Not supported on every platform -> best effort.
        torch.set_flush_denormal(True)
    except (RuntimeError, AttributeError):
        pass
    device = torch.device(args.device)

    # ---- init mode ----
    if args.init_from and not args.data:
        model = build_ppo(args.init_from)
        # warm-start policy heads from BC (value head stays random)
        from weights_io import load_state_into

        load_state_into(model, args.init_from)
        save_weights_json(model, args.out)
        log(f"[ppo] init RL weights (BC policy + random value) -> {args.out}")
        log(f"[ppo] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    # ---- update mode ----
    assert args.resume and args.data, "--resume and --data required in update mode"
    model = build_ppo(args.resume)
    from weights_io import load_state_into

    load_state_into(model, args.resume)
    model.to(device)

    episodes = load_episodes(args.data, args.gamma, args.lam)
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    log(f"[ppo] total transition steps={total_steps}")

    chunks = chunk_episodes(episodes, args.mb)
    log(f"[ppo] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    agg = ppo_update(model, opt, chunks, args.epochs, device)

    model.to("cpu")
    save_weights_json(model, args.out)
    log(
        f"[ppo] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} "
        f"mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )


if __name__ == "__main__":
    main()
