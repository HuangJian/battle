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
import os
import time
from typing import Dict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import MOVE_DIM, FIRE_DIM
from student_model import PPOStudent
from weights_io import load_weights_json, save_weights_json
# 共享 PPO 基础设施（ppo_common.py；行为与旧内联实现逐字节一致，见其模块 doc）。
from ppo_common import (  # noqa: E402
    log,
    masked_logsoftmax,
    cat_logprob,
    cat_entropy,
    compute_gae,
    discover_shards,
    load_shard_fields,
    _pack_np_state,
    _unpack_np_state,
    _ppo_save,
    _ppo_load,
    chunk_episodes,
    load_episodes_common,
)


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
# RL shard 字段表：{key: (filename, dtype)} —— 与旧 load_shard 逐字段一致（copy=False 零拷贝）。
_RL_SHARD_SPEC = {
    "obs": ("obs.npy", np.uint8),
    "scalars": ("scalars.npy", np.float32),
    "a_move": ("a_move.npy", np.int64),
    "a_fire": ("a_fire.npy", np.int64),
    "lp_move": ("lp_move.npy", np.float32),
    "lp_fire": ("lp_fire.npy", np.float32),
    "value": ("value.npy", np.float32),
    "reward": ("reward.npy", np.float32),
    "done": ("done.npy", np.int64),
    "mask": ("mask.npy", np.int64),
}


def discover_rl_shards(root: str) -> list[str]:
    return discover_shards(root, ("reward.npy", "obs.npy"))


def load_shard(dirpath: str) -> Dict[str, np.ndarray]:
    return load_shard_fields(dirpath, _RL_SHARD_SPEC)


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
    return load_episodes_common(
        data_root,
        label="ppo",
        shard_kind="RL",
        need_files=("reward.npy", "obs.npy"),
        shard_loader=load_shard,
        gae=lambda d: compute_gae(d["reward"], d["value"], d["done"], gamma, lam),
        gae_name="GAE",
        normalize_ret=False,
    )


# ---------------- PPO update ----------------
# checkpoint 原语（_pack_np_state/_unpack_np_state/_ppo_save/_ppo_load）与
# chunk_episodes 由 ppo_common 提供（re-export 见顶部 import），行为逐字节一致。


def ppo_update(model, opt, chunks, epochs, device, ckpt_path: str | None = None,
               on_epoch_done=None):
    """chunks: list of minibatch dicts (obs (B,14,26,26) / scalars (B,24) / ...).

    ckpt_path: 非空则每 epoch 落盘 checkpoint（model/opt/epochs_done/numpy RNG），
    并支持断点续跑（重启后从已完成 epoch 数继续，minibatch 乱序精确复现）。
    on_epoch_done(ep_done, model): 每个 epoch 完成后同步回调（双缓冲提前预采的
    触发点——stream 在 epoch3 完成时把 θ_{N,e3} 存盘 spawn 首波预采，PPO 继续
    最后一个 epoch；模型在此处即当前 epoch 训练完的状态）。
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
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
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


# rl/stream.py 的 backend 契约要求模块暴露 update(...)（intent 的 ppo_intent 已有）；
# 普通训练器的流式路径（run_rl.py --stream 1）此前从未被拉通——补此别名。
# 签名与 ppo_update 完全一致（ckpt_path 经 update_kwargs 透传）。
update = ppo_update


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
