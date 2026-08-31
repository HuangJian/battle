"""
ppo_intent.py — M8 意图 PPO（semi-MDP，变步长 GAE）(plan/Intent-Policy-NN-Plan.md §6/I13/P1-7k3)。

与 ppo.py（per-tick move/fire）相对，本模块在 **意图步** 上做 PPO：
  - 决策只发生在 replan tick；动作 = 采样意图（8 类，死类掩码与 rollout 一致）；
  - 奖励按窗口累计（export-intent-rollout.ts 的 reward.npy = 窗口内密集分量 + shaping
    + 无产出切换成本 + 终局）；
  - **GAE 变步长**：每个意图步的折扣 γ_step = γ_tick^Δt（Δt = 窗口时长 tick，dt.npy）——
    Δt≡1 时退化为 ppo.py 的定长 per-tick GAE（单元测试断言字节一致）；
  - value 头与三头并列消费同一 137 隐藏（128+9 注入，P1-5②）——value 必须看到承诺状态。

流程（经统一启动器）：
  1) init：python ppo_intent.py --init-from tmp/intent-weights-Bp.json --out tmp/intent-rl/weights.json
     构建 IntentNet(with_value=True)，主干+三头从 B′ 迁移（load_intent_weights 校验
     三头齐全、value 头保持随机），导出含 value_head 的 RL 权重。
  2) update：python ppo_intent.py --resume <rl> --data <traj> --out <rl> --epochs K
     加载 → 意图步 GAE → clipped PPO（意图头 + value 头）。

target_kl 早停：每个 epoch 后累计 KL 超阈值即停止剩余 epoch（防单轮漂移过大，
run_rl_intent.py 用它做 pace 护栏）。
"""
from __future__ import annotations

import argparse
import os
import time
from typing import Dict

import numpy as np
import torch
import torch.nn.functional as F

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM  # noqa: F401 — re-export for callers
from intent_net import IntentNet, load_intent_weights, export_intent_weights
# 共享 PPO 基础设施（ppo_common.py；行为与旧内联实现逐字节一致，见其模块 doc）。
from ppo_common import (  # noqa: E402
    log,
    masked_logsoftmax,
    compute_gae,
    discover_shards,
    load_shard_fields,
    _ppo_save,
    _ppo_load,
    chunk_episodes,
    load_episodes_common,
)

# ---- hyper-params（与 ppo.py 同源；γ 换算口径 P1-5③）----
GAMMA_TICK = 0.995  # per-tick 折扣（与 ppo.py GAMMA 一致）
LAM = 0.95
CLIP_EPS = 0.2
VF_COEF = 1.0
ENT_COEF = 0.08  # 2026-08-27 M8 坍缩修复：0.02 → 0.08（8 类意图步熵正则 4×，
# 反制 it22→it28 实测的 HUNT 单点坍缩 entropy 0.38→0.006；0.02 在 ~280 步/轮的
# 小梯度体量下已失效）。
LR = 1e-4  # 低于 per-tick 的 3e-4：强策略微调，慢而稳（B′ 逐状态峰值，梯度易塌缩）
MAX_GRAD_NORM = 1.0
INTENT_DIM = 8
# target_kl 早停：单 epoch 平均近似 KL 超此值即停止剩余 epoch。参照现有 per-tick RL
# 健康稳态 kl≈0.045-0.054/iter（rl/breaker.py，熔断 0.15×连续 3）——per-epoch 预算取 0.1
#（2026-08-27 §30 改：0.1 → **0.04**。意图 8 类小空间 ~280 步/轮，单 epoch KL=0.101 的
# 大更新就把策略从多样（熵 0.346）推到近单点（0.098）——too coarse。0.04 把单轮漂移
# 压到意图熵正则（0.08）能拉回的幅度内）
# 允许每迭代 ~2 epoch 策略更新，同时早停仍能拦截单轮剧烈漂移（P1-1k3 pace 护栏）。
TARGET_KL = 0.04
LOAD_LOG_EVERY = 128
HB_SEC = 60.0


class IntentRLNet(IntentNet):
    """IntentNet + value 头（M8 默认 inject=True, with_value=True）。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("inject", True)
        kwargs.setdefault("with_value", True)
        super().__init__(**kwargs)


def build_rl_net(weights_path: str | None) -> IntentRLNet:
    h = d = None
    if weights_path and os.path.exists(weights_path):
        try:
            from weights_io import load_weights_json
            meta, _ = load_weights_json(weights_path)
            a = meta.get("arch", {})
            h = a.get("h", 64)
            d = a.get("d", 8)
        except Exception as e:  # 权重损坏/格式不符 → 回落默认架构（保留原行为，仅不再静默）
            log(f"[ppo_intent] WARN build_rl_net: ignoring {weights_path}: {e!r}")
    return IntentRLNet(h=h or 64, d=d or 8)


# ---------------- trajectory loading ----------------
# 意图 shard 字段表：{key: (filename, dtype)} —— 与旧 load_intent_shard 逐字段一致。
_INTENT_SHARD_SPEC = {
    "obs": ("obs.npy", np.uint8),
    "scalars": ("scalars.npy", np.float32),
    "inject": ("inject.npy", np.float32),
    "a_intent": ("a_intent.npy", np.int64),
    "lp_intent": ("lp_intent.npy", np.float32),
    "value": ("value.npy", np.float32),
    "reward": ("reward.npy", np.float32),
    "done": ("done.npy", np.int64),
    "mask": ("mask.npy", np.int64),
    "dt": ("dt.npy", np.int64),
}


def discover_intent_shards(root: str) -> list[str]:
    return discover_shards(root, ("reward.npy", "obs.npy", "dt.npy"))


def load_intent_shard(dirpath: str) -> Dict[str, np.ndarray]:
    return load_shard_fields(dirpath, _INTENT_SHARD_SPEC)


def compute_gae_variable(rewards, values, dones, dt,
                         gamma_tick: float = GAMMA_TICK, lam: float = LAM):
    """意图步 GAE：每步折扣 γ_step = γ_tick^Δt（Δt = 窗口时长 tick）。

    Δt≡1 时与 ppo.py 的定长 per-tick GAE 逐字节一致（test_ppo_intent.py 断言）。
    rewards[t]=r_{t+1}（意图步回报），values[t]=V(s_t)，dones[t]=是否终局步。
    （实现统一收敛至 ppo_common.compute_gae(dt=...)，本名保留为兼容别名。）
    """
    return compute_gae(rewards, values, dones, gamma_tick, lam, dt)


def load_episode_from_shard(dirpath: str) -> dict | None:
    """流式 backend 接口（rl/stream.py）：意图 shard → 可训练 episode（adv/ret 未归一）。

    与 ppo.load_episode_from_shard 同签名——run_rollout_stream 以 backend 参数复用
    同一套流式基础设施（意图步变步长 GAE + inject/dt 字段）。"""
    d = load_intent_shard(dirpath)
    N = d["obs"].shape[0]
    if N == 0:
        return None
    adv, ret = compute_gae_variable(d["reward"], d["value"], d["done"], d["dt"])
    return {
        "obs": d["obs"],
        "scalars": d["scalars"],
        "inject": d["inject"],
        "a_intent": d["a_intent"],
        "lp_intent": d["lp_intent"],
        "value": d["value"],
        "adv": adv.astype(np.float32),
        "ret": ret.astype(np.float32),
        "mask": d["mask"],
        "dt": d["dt"],
    }


def load_episodes_intent(data_root: str) -> list[dict]:
    return load_episodes_common(
        data_root,
        label="ppo_intent",
        shard_kind="intent RL",
        need_files=("reward.npy", "obs.npy", "dt.npy"),
        shard_loader=load_intent_shard,
        gae=lambda d: compute_gae_variable(d["reward"], d["value"], d["done"], d["dt"]),
        gae_name="variable-step GAE",
        normalize_ret=True,  # intent：adv 与 ret 都全局归一（I13 逐关规范化）
    )


# ---------------- PPO update ----------------
# masked_logsoftmax / chunk_episodes / checkpoint 原语（_ppo_save/_ppo_load）由
# ppo_common 提供（re-export 见顶部 import）；ppo_update_intent 直接使用。


def ppo_update_intent(model, opt, chunks, epochs, device,
                      ckpt_path: str | None = None,
                      target_kl: float = TARGET_KL,
                      seed: int = 7,
                      value_warmup_epochs: int = 0,
                      ref_model: torch.nn.Module | None = None,
                      kl_coef: float = 0.0,
                      on_epoch_done=None):
    """chunks: intent 步 minibatch dicts（obs/scalars/inject/a_intent/lp_intent/adv/ret/mask）。

    value_warmup_epochs（M8 冷启动，plan "kickstarting 辅助项递减"）：前 N 个 epoch 只训
    value 头（policy loss/entropy 置零——策略权重零梯度）。B′ 冷启动 value 头为随机，
    直接 PPO 会让优势被 value 噪声主导、策略单 epoch 塌缩（实测 KL 爆炸 262）。先让
    value 学出 B′ 的回报基线，再从稳定基线上优化策略。warmup 阶段策略不动 → KL=0。
    预热期**冻结主干+三头**：经共享主干训 value 会扰动策略特征→意图分布（实测熵
    0.90→0.33，等于 RL 前先毁掉 B′）；value 头在冻结的 B′ 特征上学基线。

    kickstarting（plan "kickstarting 辅助项递减"，预注册 #5 衰减 0.5/iter）：
    ref_model = B′ 策略冻结快照；非 warmup 时在 loss 加 kl_coef·KL(π_curr ‖ π_ref)。
    强策略微调（B′ 逐状态峰值）单 PPO 轮可塌缩（实测熵 1.33→0.06），KL 惩罚把它
    钉在 B′ 附近，系数随迭代衰减 → 后期放开探索。ref_model 必须在调用方 freeze。

    target_kl 早停：单 epoch 平均近似 KL > target_kl → 停止剩余 epoch（P1-1k3 pace 护栏）。
    ckpt_path: 非空则每 epoch 落盘（model/opt/epochs_done/numpy RNG），支持断点续跑。
    """
    if not chunks:
        return {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "gnorm": 0.0,
                "mean_ret": 0.0, "early_stopped": False}
    np.random.seed(seed)
    model.train()
    clip = CLIP_EPS
    stats = []
    tensored = [
        {k: torch.from_numpy(v).to(device) for k, v in c.items()} for c in chunks
    ]
    total_steps = len(tensored) * epochs
    log(f"[ppo_intent] update start: {len(tensored)} chunks x {epochs} epochs "
        f"(~{total_steps} grad steps) value_warmup={value_warmup_epochs} "
        f"kickstart_kl_coef={kl_coef}")
    t0 = time.time()
    last_hb = t0
    start_epoch = 0
    if ckpt_path:
        start_epoch = _ppo_load(ckpt_path, model, opt)
        if start_epoch:
            log(f"[ppo_intent] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done")

    early_stopped = False
    for ep in range(start_epoch, epochs):
        warmup = ep < value_warmup_epochs
        # warmup 只训 value 头（冻结主干+三头，防扰动 B′ 策略特征）；否则全量可训。
        for name, p in model.named_parameters():
            p.requires_grad = (not warmup) or name.startswith('value_head.')
        perm = np.random.permutation(len(tensored))
        n_ep_start = len(stats)
        for j, i in enumerate(perm):
            e = tensored[int(i)]
            obs = e["obs"]
            sc = e["scalars"]
            inj = e["inject"]
            a = e["a_intent"]
            lp_old = e["lp_intent"]
            adv = e["adv"]
            ret = e["ret"]
            mask = e["mask"]

            i_log, _en, _an, val = model.forward_rl(obs, sc, inj)
            lp_new = masked_logsoftmax(i_log, mask)
            lp_new_a = lp_new.gather(1, a.unsqueeze(1)).squeeze(1)

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = -(lp_new.exp() * lp_new).sum(dim=-1).mean()

            if warmup:
                # value-only 预热：策略 loss/entropy 不入 loss（主干已冻结）。
                policy_loss = torch.zeros((), device=device)
                loss = VF_COEF * value_loss
            else:
                ratio = torch.exp(lp_new_a - lp_old)
                surr1 = ratio * adv
                surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
                policy_loss = -torch.min(surr1, surr2).mean()
                loss = policy_loss + VF_COEF * value_loss - ENT_COEF * entropy
                if ref_model is not None and kl_coef > 0:
                    with torch.no_grad():
                        r_log, _re, _ra, _rv = ref_model.forward_rl(obs, sc, inj)
                        rp_ref = masked_logsoftmax(r_log, mask)
                    # KL(π_curr ‖ π_ref) = Σ_a π_curr·(log π_curr − log π_ref)
                    kl_to_ref = (lp_new.exp() * (lp_new - rp_ref)).sum(dim=-1).mean()
                    loss = loss + kl_coef * kl_to_ref

            opt.zero_grad()
            loss.backward()
            gn = torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            opt.step()

            with torch.no_grad():
                approx_kl = 0.0 if warmup else ((lp_old - lp_new_a) ** 2).mean().item()
            stats.append({
                "policy": float(policy_loss.item()),
                "value": float(value_loss.item()),
                "entropy": float(entropy.item()),
                "kl": float(approx_kl),
                "mean_ret": float(ret.mean().item()),
                "gnorm": float(gn),
            })
            now = time.time()
            if now - last_hb >= HB_SEC:
                last_hb = now
                recent = stats[-32:]
                n_r = len(recent)
                log(f"[ppo_intent] ep {ep + 1}/{epochs}{'[warmup]' if warmup else ''} "
                    f"chunk {j + 1}/{len(tensored)} elapsed={now - t0:.0f}s "
                    f"kl={sum(s['kl'] for s in recent) / n_r:.4f} "
                    f"entropy={sum(s['entropy'] for s in recent) / n_r:.4f} "
                    f"policy={sum(s['policy'] for s in recent) / n_r:.4f} "
                    f"value={sum(s['value'] for s in recent) / n_r:.4f}")
        if ckpt_path:
            _ppo_save(ckpt_path, model, opt, ep + 1)
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
        ep_stats = stats[n_ep_start:]
        n_e = max(1, len(ep_stats))
        ep_kl = sum(s["kl"] for s in ep_stats) / n_e
        log(f"[ppo_intent] epoch {ep + 1}/{epochs}{'[warmup]' if warmup else ''} done "
            f"({time.time() - t0:.0f}s total): kl={ep_kl:.4f} "
            f"entropy={sum(s['entropy'] for s in ep_stats) / n_e:.4f} "
            f"policy={sum(s['policy'] for s in ep_stats) / n_e:.4f} "
            f"value={sum(s['value'] for s in ep_stats) / n_e:.4f}")
        if not warmup and ep_kl > target_kl:
            log(f"[ppo_intent] target_kl={target_kl} exceeded (epoch {ep + 1} kl={ep_kl:.4f}) "
                f"— early stopping remaining epochs")
            early_stopped = True
            break

    n = len(stats)
    agg = {k: sum(s[k] for s in stats) / n for k in stats[0]} if n else \
        {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "gnorm": 0.0, "mean_ret": 0.0}
    agg["early_stopped"] = early_stopped
    return agg


# ---- stream backend 接口别名（rl/stream.py 的 backend.update / load_episodes / _ppo_load）----
# ---- stream backend 接口别名（rl/stream.py 的 backend.update / load_episodes）----
# 意图 RL 与 per-tick RL 共用同一套流式基础设施；checkpoint 原语由 ppo_common
# 提供（顶部 import _ppo_save/_ppo_load）。ppo 不依赖本模块，无环。
update = ppo_update_intent
load_episodes = load_episodes_intent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-from", type=str, default=None, help="B′/BC intent weights (init mode)")
    ap.add_argument("--resume", type=str, default=None, help="RL intent weights (update mode)")
    ap.add_argument("--data", type=str, default=None, help="intent trajectory shard root (update)")
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--value-warmup-epochs", type=int, default=0,
                    help="前 N 个 epoch 只训 value 头（策略冻结；B′ 冷启动 value 随机）")
    ap.add_argument("--kl-coef", type=float, default=0.0,
                    help="kickstarting KL 惩罚系数（plan #5 衰减 0.5/iter；0=关闭）")
    ap.add_argument("--ref-weights", type=str, default=None,
                    help="kickstarting 参考策略权重（B′ 冻结快照；缺省 = 当前权重）")
    ap.add_argument("--device", type=str, default="cpu")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    np.random.seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    try:
        torch.set_flush_denormal(True)
    except (RuntimeError, AttributeError):
        pass
    device = torch.device(args.device)

    if args.init_from and not args.data:
        model = build_rl_net(args.init_from)
        load_intent_weights(model, args.init_from)  # 主干+三头迁移；value 头保持随机
        export_intent_weights(model, args.out)
        log(f"[ppo_intent] init RL weights (B′/BC intent + random value) -> {args.out}")
        log(f"[ppo_intent] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    assert args.resume and args.data, "--resume and --data required in update mode"
    model = build_rl_net(args.resume)
    load_intent_weights(model, args.resume)
    model.to(device)

    episodes = load_episodes_intent(args.data)
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    log(f"[ppo_intent] total intent-steps={total_steps}")

    chunks = chunk_episodes(episodes, args.mb)
    log(f"[ppo_intent] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    # kickstarting 参考策略：显式 ref-weights（B′ 快照）或当前权重（策略尚未被本 CLI 更新）。
    ref_model = None
    if args.kl_coef > 0:
        ref_src = args.ref_weights or args.resume
        ref_model = build_rl_net(ref_src)
        load_intent_weights(ref_model, ref_src)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
        log(f"[ppo_intent] kickstarting: ref={ref_src} kl_coef={args.kl_coef}")
    agg = ppo_update_intent(model, opt, chunks, args.epochs, device, seed=args.seed,
                            value_warmup_epochs=args.value_warmup_epochs,
                            ref_model=ref_model, kl_coef=args.kl_coef)

    model.to("cpu")
    export_intent_weights(model, args.out)
    log(
        f"[ppo_intent] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} "
        f"early_stopped={agg['early_stopped']} mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )


if __name__ == "__main__":
    main()
