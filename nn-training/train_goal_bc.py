"""
train_goal_bc.py — Goal-Space BC（反事实软目标蒸馏，plan/Goal-Space-Policy-Rebuild.md
§11.5 / §9.4.3 / 任务卡 T6→T9a/T9 的 warm-start 语料）。

监督形式（§11.5 定案）：
  候选集 C（|C| = K≈12，每候选 (s_i, k_i)）：
    p_i ∝ exp((s_i − λ·k_i) / τ)   if i ∈ C
    p_i = 0                        otherwise
  loss = CE(softmax(goal_logits 676), p)      # 全 676 维稀疏 CE（非 K 路 —— 未评估格
                                              # 的 logit 被压低，与 argmax(z+mask) 一致）
  λ / τ 是**训练时超参**（shard 只存 (s_i, k_i)，扫参无需重标注 —— §9.4.3）。

辅助头：engage CE（shard 的 engage 标签 = §8.3.2 反事实判据）。

shard（export-counterfactual-goals.ts 产出）：
  obs (N,14,26,26) u1 | scalars (N,19) f4 | inject (N,9) f4
  cand_cell (N,K) u2（padding 65535）| cand_k (N,K) u2 | cand_s (N,K) f4 | engage (N) u1

流程（经统一启动器）：
  ./start-training.sh --script train_goal_bc.py --data tmp/cf-goals-pilot \
      --out tmp/goal-bc/weights.json --epochs 20 --lambda 0.5 --tau 1.0
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import torch
import torch.nn.functional as F
from goal_net import GoalNet, export_goal_weights, load_goal_weights
from ppo_common import discover_shards, log

UNREACH = 65535
LOAD_LOG_EVERY = 128
HB_SEC = 30.0


def load_cf_shard(dirpath: str, window: int) -> dict:
    """多窗口 shard（cand_s_w{W}.npy）；旧单窗口文件 cand_s.npy 作为回退。"""
    s_name = (
        f"cand_s_w{window}.npy"
        if os.path.exists(os.path.join(dirpath, f"cand_s_w{window}.npy"))
        else "cand_s.npy"
    )
    e_name = (
        f"engage_w{window}.npy"
        if os.path.exists(os.path.join(dirpath, f"engage_w{window}.npy"))
        else "engage.npy"
    )
    d = {
        "obs": np.load(os.path.join(dirpath, "obs.npy")),
        "scalars": np.load(os.path.join(dirpath, "scalars.npy")),
        "inject": np.load(os.path.join(dirpath, "inject.npy")),
        "cand_cell": np.load(os.path.join(dirpath, "cand_cell.npy")).astype(np.int64),
        "cand_k": np.load(os.path.join(dirpath, "cand_k.npy")).astype(np.float32),
        "cand_s": np.load(os.path.join(dirpath, s_name)).astype(np.float32),
        "engage": np.load(os.path.join(dirpath, e_name)).astype(np.int64),
    }
    return d


def build_soft_target(cand_s, cand_k, cand_cell, lam: float, tau: float, fine: int = 676):
    """§11.5/§9.4.3：p_i ∝ exp((s_i − λ·k_i)/τ)，padding(UNREACH) 排除。

    返回 (N,676) 稀疏软目标（行和 = 1）。全 padding 行（无候选）返回 None 占位。
    """
    N, K = cand_s.shape
    target = np.zeros((N, fine), dtype=np.float32)
    valid_any = np.zeros(N, dtype=bool)
    for i in range(N):
        logits = np.empty(K, dtype=np.float64)
        ok = False
        for j in range(K):
            if cand_cell[i, j] == UNREACH:
                logits[j] = -np.inf
                continue
            logits[j] = (cand_s[i, j] - lam * cand_k[i, j]) / tau
            ok = True
        if not ok:
            continue  # 行保持全 0（无有效候选；训练时跳过）
        m = logits.max()
        e = np.exp(logits - m)
        e[~np.isfinite(logits)] = 0.0
        e /= e.sum()
        valid_any[i] = True
        for j in range(K):
            if cand_cell[i, j] != UNREACH and e[j] > 0:
                target[i, cand_cell[i, j]] = e[j]
    return target, valid_any


def load_episodes(data_root: str, window: int) -> list[dict]:
    shards = discover_shards(data_root, ("obs.npy", "cand_cell.npy"))
    if not shards:
        raise SystemExit(f"[goal-bc] no cf shards found under {data_root}")
    log(f"[goal-bc] loaded {len(shards)} cf shards from {data_root}")
    episodes = []
    t0 = time.time()
    for k, sd in enumerate(shards):
        if k > 0 and k % LOAD_LOG_EVERY == 0:
            log(f"[goal-bc] loading shards {k}/{len(shards)} ({time.time() - t0:.0f}s)")
        d = load_cf_shard(sd, window)
        N = d["obs"].shape[0]
        if N == 0:
            continue
        episodes.append(d)
    total = sum(e["obs"].shape[0] for e in episodes)
    log(f"[goal-bc] {len(episodes)} shards, {total} decision points ({time.time() - t0:.0f}s)")
    return episodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=str, required=True, help="cf-goals shard root")
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument(
        "--init-from", type=str, default=None, help="迁移主干（如 intent BC 权重；可选）"
    )
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--mb", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument(
        "--lambda", dest="lam", type=float, default=0.5, help="carve 代价系数 λ（§9.4.2）"
    )
    ap.add_argument("--tau", type=float, default=1.0, help="软目标温度 τ（§11.5）")
    ap.add_argument("--window", type=int, default=240, help="H 扫描选出的窗口档（§11.8）")
    ap.add_argument("--engage-coef", type=float, default=0.3, help="engage 辅助 CE 权重")
    ap.add_argument(
        "--long-weight",
        type=float,
        default=1.0,
        help="长承诺样本（inject duration ≥0.5）的损失加权（§8.1.1 评审 a1 OOD 缓解#2）",
    )
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    device = torch.device(args.device)

    episodes = load_episodes(args.data, args.window)

    model = GoalNet()
    if args.init_from and os.path.exists(args.init_from):
        # 迁移主干 + 保留 goal/engage 头（同构网络直接全载）
        load_goal_weights(model, args.init_from)
        log(f"[goal-bc] init-from {args.init_from}")
    model.to(device)

    # 预构建软目标（λ/τ 变更需重跑本脚本；目标构建 < 1s/万点）
    target_chunks = []
    weight_chunks = []
    n_long = 0
    for e in episodes:
        tgt, valid = build_soft_target(e["cand_s"], e["cand_k"], e["cand_cell"], args.lam, args.tau)
        target_chunks.append((tgt, valid))
        # 来源加权：长承诺（duration ≥0.5，replan240 档）样本权重上浮（§8.1.1 a1 缓解）。
        dur = e["inject"][:, 2]
        w = np.where(dur >= 0.5, args.long_weight, 1.0).astype(np.float32)
        n_long += int((dur >= 0.5).sum())
        weight_chunks.append(w)
    n_valid = sum(int(v.sum()) for _, v in target_chunks)
    log(
        f"[goal-bc] soft targets built (λ={args.lam} τ={args.tau}) valid={n_valid} "
        f"long={n_long} ({100.0 * n_long / max(1, n_valid):.1f}%)"
    )

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    model.train()
    all_losses = []
    t0 = time.time()
    last_hb = t0
    for ep in range(args.epochs):
        order = np.random.permutation(len(episodes))
        ep_losses = []
        for bi in order:
            e = episodes[bi]
            tgt, valid = target_chunks[bi]
            obs = torch.from_numpy(e["obs"]).to(device)
            sc = torch.from_numpy(e["scalars"]).to(device)
            inj = torch.from_numpy(e["inject"]).to(device)
            tgtT = torch.from_numpy(tgt).to(device)
            validT = torch.from_numpy(valid).to(device)
            wT = torch.from_numpy(weight_chunks[bi]).to(device)
            eng = torch.from_numpy(e["engage"]).to(device)

            sp, h = model.spatial(obs, sc)
            goal_logits = model.goal_conv(sp).flatten(1)  # (n,676)
            hi = torch.cat([h, inj], dim=1)
            engage_logits = model.engage_head(hi)

            logp = F.log_softmax(goal_logits, dim=-1)
            ce = -(tgtT * logp).sum(dim=-1)  # 软目标 CE（全 676 维稀疏）
            ce = (ce * validT * wT).sum() / torch.clamp((validT * wT).sum(), min=1.0)
            engage_loss = F.cross_entropy(engage_logits, eng)
            loss = ce + args.engage_coef * engage_loss

            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            ep_losses.append(float(loss.item()))
        mean_loss = sum(ep_losses) / max(1, len(ep_losses))
        all_losses.extend(ep_losses)
        now = time.time()
        if now - last_hb >= HB_SEC:
            last_hb = now
            log(f"[goal-bc] epoch {ep + 1}/{args.epochs} loss={mean_loss:.4f} ({now - t0:.0f}s)")
        else:
            print(f"[goal-bc] epoch {ep + 1}/{args.epochs} loss={mean_loss:.4f}")
    # 结算目标分布的诊断（argmax 与 God-AI 选择的重合、峰值分布）
    log(f"[goal-bc] done: final loss={sum(all_losses[-100:]) / max(1, len(all_losses[-100:])):.4f}")

    model.to("cpu")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    export_goal_weights(model, args.out)
    log(f"[goal-bc] weights -> {args.out}")
    # 元数据（λ/τ 随权重归档，T9a 检查可溯）
    meta_path = args.out + ".meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "lambda": args.lam,
                "tau": args.tau,
                "epochs": args.epochs,
                "engageCoef": args.engage_coef,
                "data": args.data,
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
