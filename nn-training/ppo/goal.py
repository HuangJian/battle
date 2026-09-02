"""
ppo_goal.py — Goal-Space PPO（semi-MDP，变步长 GAE）
(plan/Goal-Space-Policy-Rebuild.md §13 / 任务卡 T7.2；T9a 与 T9 共用本模块)。

与 ppo_intent.py（意图步 8 路）相对，本模块在 **goal 承诺步** 上做 PPO：
  - 决策只发生在心跳 tick（执行器 promiseTicks=T）；动作 = 采样目标格。
    动作空间二态：fine = 676 路（T9）；coarse = 169 路块级（T9a，块 logit = 块内
    26×26 logits 的 logsumexp，§T9a.1b）——由 shard 的 mask 宽度自动判别。
  - 奖励按窗口累计（export-goal-rollout.ts：R_event 继承 intent-rl-reward 量级
    §12.3.1 + R_shaping 到达 1.0 / 守家 0.5 / 交战效率 0.3 §12.3）；
  - GAE 变步长：γ_step = γ_tick^Δt（Δt = 承诺窗口 tick，dt.npy；ppo_common.compute_gae）；
  - value 头消费 137 隐藏（128+9 注入）——value 必须看到承诺状态（§8.3.0）。

multi-head loss（§T7.2，对照 ppo_intent.py）：
  loss = surrogate_clip(goal, N 路)            # 主项
       + CE(engage, engage_label)              # 辅助监督（仅 shard 带 engage_label 时；
                                               #   on-policy 采样无反事实标签 ⇒ 不训）
       + VF_COEF · value_MSE − ENT_COEF · H
       + [kl_coef · KL(π_curr ‖ π_ref)]        # goal-BC 冻结快照锚（k7）

复用边界（§13.1）：masked_logsoftmax / compute_gae / chunk_episodes / checkpoint /
shard 工具全部来自 ppo_common；网络类 / shard spec / 采集器为 goal 专属。
engage 明定：**不是 PPO 动作**（shard 无 lp_engage）——rollout/部署均 argmax（k1）。
"""



from __future__ import annotations

# 仓库根探测（B4，2026-09-02）：包已安装（pip install -e .）或 script-dir/cwd 在
# nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入 sys.path——
# 不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正 import，
# 避免探针导入产生 F401。
import importlib.util as _ilu

if _ilu.find_spec("schema") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import os
import time

import numpy as np
import numpy.typing as npt
import torch
import torch.nn.functional as F

from models.goal_net import GoalNet, export_goal_weights, load_goal_weights
from ppo.common import (
    _ppo_load,
    _ppo_save,
    approx_kl_est,
    chunk_episodes,
    compute_gae,
    discover_shards,
    load_episodes_common,
    load_shard_fields,
    log,
    masked_logsoftmax,
)
from schema import BOARD

# ---- hyper-params（与 ppo_intent 同源；γ 口径 §12.1 γ_step = 0.995^dt）----
GAMMA_TICK = 0.995
LAM = 0.95
CLIP_EPS = 0.2
VF_COEF = 1.0
ENT_COEF = 0.08  # 676/169 路大动作空间熵更高，起点与 intent 修复后的 0.08 同值
LR = 1e-4
MAX_GRAD_NORM = 1.0
TARGET_KL = 0.02  # 2026-09-02 P0-3：Schulman 无偏估计量口径（旧 2× 口径 0.04）
LOAD_LOG_EVERY = 128
HB_SEC = 60.0

FINE_DIM = BOARD * BOARD  # 676
COARSE_SIDE = 13
COARSE_DIM = COARSE_SIDE * COARSE_SIDE  # 169（T9a 块级）


def action_dim_of(mask_width: int) -> int:
    """由 shard mask 宽度判别动作空间（676 fine / 169 coarse）。"""
    if mask_width == COARSE_DIM:
        return COARSE_DIM
    if mask_width == FINE_DIM:
        return FINE_DIM
    raise ValueError(f"goal mask width {mask_width} not in {{{FINE_DIM},{COARSE_DIM}}}")


class GoalRLNet(GoalNet):
    """GoalNet + value 头（goal RL 默认 with_value=True）。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("with_value", True)
        super().__init__(**kwargs)


def build_rl_net(weights_path: str | None) -> GoalRLNet:
    h = d = None
    if weights_path and os.path.exists(weights_path):
        try:
            from data.weights_io import load_weights_json

            meta, _ = load_weights_json(weights_path)
            a = meta.get("arch", {})
            h = a.get("h", 64)
            d = a.get("d", 8)
        except Exception as e:
            log(f"[ppo_goal] WARN build_rl_net: ignoring {weights_path}: {e!r}")
    return GoalRLNet(h=h or 64, d=d or 8)


# ---------------- trajectory loading ----------------
# goal shard 字段表。goal_mask: 每步 N 路可达掩码（u1，1=可选）；N 由文件形状判别。
_GOAL_SHARD_SPEC: dict[str, tuple[str, npt.DTypeLike]] = {
    "obs": ("obs.npy", np.uint8),
    "scalars": ("scalars.npy", np.float32),
    "inject": ("inject.npy", np.float32),
    "a_goal": ("a_goal.npy", np.int64),  # u2 on disk, upcast
    "lp_goal": ("lp_goal.npy", np.float32),
    "value": ("value.npy", np.float32),
    "reward": ("reward.npy", np.float32),
    "done": ("done.npy", np.int64),
    "goal_mask": ("goal_mask.npy", np.uint8),
    "dt": ("dt.npy", np.int64),
    "engage": ("engage.npy", np.int64),  # u1 on disk, upcast
}
# 可选字段（存在才加载；缺失返回 None）
_GOAL_OPT_SPEC = {
    "engage_label": ("engage_label.npy", np.int64),
    # 注：bc_p/bc_idx 软目标 kickstart（§11.5）于 2026-09-02 清理——TS 侧
    # export-goal-rollout.ts 从不产出这两个字段（无生产方），对应分支永不执行
    # （plan/python-refactor.md P2-6a）。未来如需 bc kickstart，从 git 历史恢复。
}


def discover_goal_shards(root: str) -> list[str]:
    return discover_shards(root, ("reward.npy", "obs.npy", "dt.npy", "goal_mask.npy"))


def load_goal_shard(dirpath: str) -> dict[str, np.ndarray]:
    d = load_shard_fields(dirpath, _GOAL_SHARD_SPEC)
    for key, (fname, dtype) in _GOAL_OPT_SPEC.items():
        p = os.path.join(dirpath, fname)
        if os.path.exists(p):
            d[key] = np.load(p).astype(dtype, copy=False)
    return d


def coarse_block_logsumexp(goal_logits: torch.Tensor) -> torch.Tensor:
    """(B,676) → (B,169)：块 logit = 块内 4 格 logsumexp（可导，§T9a.1b）。

    布局：flat i = r*26+c 视作 (br, dr, bc, dc)，r = br*2+dr、c = bc*2+dc；
    块索引 b = br*13 + bc（与采集器一致）。"""
    B = goal_logits.shape[0]
    m = goal_logits.view(B, COARSE_SIDE, 2, COARSE_SIDE, 2)
    return torch.logsumexp(torch.logsumexp(m, dim=4), dim=2).reshape(B, COARSE_DIM)


def policy_logprobs(model, obs, sc, inj, mask):
    """→ (logp_full, entropy_terms) 与动作空间无关的掩码 log-softmax。

    mask 宽 676：直接 masked_logsoftmax(goal_logits)。
    mask 宽 169：块级 logsumexp 聚合后再 masked_logsoftmax（T9a 块级动作，
    log-prob 只记块级，执行侧块内 mask 约束 argmax 由采集器负责）。"""
    goal, engage, val = model.forward_rl(obs, sc, inj)
    n = action_dim_of(mask.shape[1])
    logits = coarse_block_logsumexp(goal) if n == COARSE_DIM else goal
    lp = masked_logsoftmax(logits, mask)
    return lp, engage, val


def compute_gae_variable(
    rewards, values, dones, dt, gamma_tick: float = GAMMA_TICK, lam: float = LAM
):
    """goal 承诺步 GAE（γ_step = γ_tick^Δt；统一收敛至 ppo_common.compute_gae）。"""
    return compute_gae(rewards, values, dones, gamma_tick, lam, dt)


def load_episode_from_shard(dirpath: str) -> dict | None:
    """流式 backend 接口（rl/stream.py）：goal shard → 可训练 episode（adv/ret 未归一）。"""
    d = load_goal_shard(dirpath)
    N = d["obs"].shape[0]
    if N == 0:
        return None
    adv, ret = compute_gae_variable(d["reward"], d["value"], d["done"], d["dt"])
    ep = {
        "obs": d["obs"],
        "scalars": d["scalars"],
        "inject": d["inject"],
        "a_goal": d["a_goal"],
        "lp_goal": d["lp_goal"],
        "value": d["value"],
        "adv": adv.astype(np.float32),
        "ret": ret.astype(np.float32),
        "goal_mask": d["goal_mask"],
        "dt": d["dt"],
        "engage": d["engage"],
    }
    for k in ("engage_label",):
        if k in d:
            ep[k] = d[k]
    return ep


def load_episodes_goal(data_root: str) -> list[dict]:
    return load_episodes_common(
        data_root,
        label="ppo_goal",
        shard_kind="goal RL",
        need_files=("reward.npy", "obs.npy", "dt.npy", "goal_mask.npy"),
        shard_loader=load_goal_shard,
        gae=lambda d: compute_gae_variable(d["reward"], d["value"], d["done"], d["dt"]),
        gae_name="variable-step GAE",
        normalize_ret=True,
    )


# ---------------- PPO update ----------------
def ppo_update_goal(
    model,
    opt,
    chunks,
    epochs,
    device,
    ckpt_path: str | None = None,
    target_kl: float = TARGET_KL,
    seed: int = 7,
    value_warmup_epochs: int = 0,
    ref_model: torch.nn.Module | None = None,
    kl_coef: float = 0.0,
    on_epoch_done=None,
):
    """chunks: goal 承诺步 minibatch dicts（obs/scalars/inject/a_goal/lp_goal/adv/ret/goal_mask
    [+ engage_label]）。

    on_epoch_done(ep_done, model)：每个 epoch 完成后同步回调（与 ppo / ppo_intent 逐
    行对齐）——双缓冲提前预采的触发点：stream 在 epoch3 完成时把 θ_{N,e3} 存盘 spawn
    首波预采，PPO 继续最后一个 epoch。rl/stream.py:123-124 **无条件**注入该回调，
    故本形参是契约的一部分（缺失即 `--mode goal --stream 1` 首个波次 TypeError，
    plan/python-refactor.md P0-1；由 tests/test_backend_contract.py 守护）。

    value_warmup：同 ppo_intent——前 N epoch 冻结主干只训 value 头（goal-BC 冷启动
    value 随机，直接 PPO 会被 value 噪声主导）。
    kickstarting：ref_model = goal-BC 冻结快照，kl_coef·KL(π_curr‖π_ref)，系数外置退火。
    target_kl 早停同 ppo_intent。
    """
    if not chunks:
        return {
            "policy": 0.0,
            "value": 0.0,
            "entropy": 0.0,
            "kl": 0.0,
            "bc": 0.0,
            "gnorm": 0.0,
            "mean_ret": 0.0,
            "early_stopped": False,
        }
    np.random.seed(seed)
    model.train()
    clip = CLIP_EPS
    stats: list[dict[str, float]] = []
    tensored = [{k: torch.from_numpy(v).to(device) for k, v in c.items()} for c in chunks]
    total_steps = len(tensored) * epochs
    log(
        f"[ppo_goal] update start: {len(tensored)} chunks x {epochs} epochs "
        f"(~{total_steps} grad steps) value_warmup={value_warmup_epochs} "
        f"kickstart_kl_coef={kl_coef}"
    )
    t0 = time.time()
    last_hb = t0
    start_epoch = 0
    if ckpt_path:
        start_epoch = _ppo_load(ckpt_path, model, opt)
        if start_epoch:
            log(f"[ppo_goal] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done")

    early_stopped = False
    for ep in range(start_epoch, epochs):
        warmup = ep < value_warmup_epochs
        for name, p in model.named_parameters():
            p.requires_grad = (not warmup) or name.startswith("value_head.")
        perm = np.random.permutation(len(tensored))
        n_ep_start = len(stats)
        for j, i in enumerate(perm):
            e = tensored[int(i)]
            obs = e["obs"]
            sc = e["scalars"]
            inj = e["inject"]
            a = e["a_goal"]
            lp_old = e["lp_goal"]
            adv = e["adv"]
            ret = e["ret"]
            mask = e["goal_mask"]

            lp_new, engage_log, val = policy_logprobs(model, obs, sc, inj, mask)
            lp_new_a = lp_new.gather(1, a.unsqueeze(1)).squeeze(1)

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = -(lp_new.exp() * lp_new).sum(dim=-1).mean()

            if warmup:
                policy_loss = torch.zeros((), device=device)
                loss = VF_COEF * value_loss
            else:
                ratio = torch.exp(lp_new_a - lp_old)
                surr1 = ratio * adv
                surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
                policy_loss = -torch.min(surr1, surr2).mean()
                loss = policy_loss + VF_COEF * value_loss - ENT_COEF * entropy
                # engage 辅助 CE（仅离线标注语料带 engage_label 时；on-policy 不训）
                if "engage_label" in e:
                    engage_loss = F.cross_entropy(engage_log, e["engage_label"])
                    loss = loss + engage_loss
                if ref_model is not None and kl_coef > 0:
                    with torch.no_grad():
                        r_lp, _re, _rv = policy_logprobs(ref_model, obs, sc, inj, mask)
                    kl_to_ref = (lp_new.exp() * (lp_new - r_lp)).sum(dim=-1).mean()
                    loss = loss + kl_coef * kl_to_ref

            opt.zero_grad()
            loss.backward()
            gn = torch.nn.utils.clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            opt.step()

            with torch.no_grad():
                approx_kl = 0.0 if warmup else approx_kl_est(lp_old, lp_new_a).item()
            stats.append(
                {
                    "policy": float(policy_loss.item()),
                    "value": float(value_loss.item()),
                    "entropy": float(entropy.item()),
                    "kl": float(approx_kl),
                    "bc": 0.0,  # bc 软目标已清理（P2-6a）；保留键保 schema 兼容
                    "mean_ret": float(ret.mean().item()),
                    "gnorm": float(gn),
                }
            )
            now = time.time()
            if now - last_hb >= HB_SEC:
                last_hb = now
                recent = stats[-32:]
                n_r = len(recent)
                log(
                    f"[ppo_goal] ep {ep + 1}/{epochs}{'[warmup]' if warmup else ''} "
                    f"chunk {j + 1}/{len(tensored)} elapsed={now - t0:.0f}s "
                    f"kl={sum(s['kl'] for s in recent) / n_r:.4f} "
                    f"entropy={sum(s['entropy'] for s in recent) / n_r:.4f} "
                    f"policy={sum(s['policy'] for s in recent) / n_r:.4f} "
                    f"value={sum(s['value'] for s in recent) / n_r:.4f}"
                )
        if ckpt_path:
            _ppo_save(ckpt_path, model, opt, ep + 1)
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
        ep_stats = stats[n_ep_start:]
        n_e = max(1, len(ep_stats))
        ep_kl = sum(s["kl"] for s in ep_stats) / n_e
        log(
            f"[ppo_goal] epoch {ep + 1}/{epochs}{'[warmup]' if warmup else ''} done "
            f"({time.time() - t0:.0f}s total): kl={ep_kl:.4f} "
            f"entropy={sum(s['entropy'] for s in ep_stats) / n_e:.4f} "
            f"policy={sum(s['policy'] for s in ep_stats) / n_e:.4f} "
            f"value={sum(s['value'] for s in ep_stats) / n_e:.4f}"
        )
        if not warmup and ep_kl > target_kl:
            log(
                f"[ppo_goal] target_kl={target_kl} exceeded (epoch {ep + 1} kl={ep_kl:.4f}) "
                f"— early stopping remaining epochs"
            )
            early_stopped = True
            break

    n = len(stats)
    agg = (
        {k: sum(s[k] for s in stats) / n for k in stats[0]}
        if n
        else {
            "policy": 0.0,
            "value": 0.0,
            "entropy": 0.0,
            "kl": 0.0,
            "bc": 0.0,
            "gnorm": 0.0,
            "mean_ret": 0.0,
        }
    )
    agg["early_stopped"] = early_stopped
    return agg


# ---- stream backend 接口别名（rl/stream.py 的 backend.update / load_episodes / _ppo_load）----
update = ppo_update_goal
load_episodes = load_episodes_goal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-from", type=str, default=None, help="goal-BC weights (init mode)")
    ap.add_argument("--resume", type=str, default=None, help="RL goal weights (update mode)")
    ap.add_argument("--data", type=str, default=None, help="goal trajectory shard root (update)")
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--value-warmup-epochs", type=int, default=0)
    ap.add_argument("--kl-coef", type=float, default=0.0)
    ap.add_argument(
        "--ref-weights",
        type=str,
        default=None,
        help="kickstarting 参考策略权重（goal-BC 冻结快照；缺省 = 当前权重）",
    )
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
        load_goal_weights(model, args.init_from)  # 主干+goal/engage 头迁移；value 头保持随机
        export_goal_weights(model, args.out)
        log(f"[ppo_goal] init RL weights (goal-BC + random value) -> {args.out}")
        log(f"[ppo_goal] params={sum(int(p.numel()) for p in model.parameters())}")
        return

    assert args.resume and args.data, "--resume and --data required in update mode"
    model = build_rl_net(args.resume)
    load_goal_weights(model, args.resume)
    model.to(device)

    episodes = load_episodes_goal(args.data)
    total_steps = sum(e["obs"].shape[0] for e in episodes)
    log(f"[ppo_goal] total goal-steps={total_steps}")

    chunks = chunk_episodes(episodes, args.mb)
    log(f"[ppo_goal] {len(episodes)} episodes -> {len(chunks)} minibatch chunks (mb={args.mb})")

    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    ref_model = None
    if args.kl_coef > 0:
        ref_src = args.ref_weights or args.resume
        ref_model = build_rl_net(ref_src)
        load_goal_weights(ref_model, ref_src)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
        log(f"[ppo_goal] kickstarting: ref={ref_src} kl_coef={args.kl_coef}")
    agg = ppo_update_goal(
        model,
        opt,
        chunks,
        args.epochs,
        device,
        seed=args.seed,
        value_warmup_epochs=args.value_warmup_epochs,
        ref_model=ref_model,
        kl_coef=args.kl_coef,
    )

    model.to("cpu")
    export_goal_weights(model, args.out)
    log(
        f"[ppo_goal] update done epochs={args.epochs} "
        f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
        f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} bc={agg['bc']:.4f} "
        f"early_stopped={agg['early_stopped']} mean_ret={agg['mean_ret']:.3f} -> {args.out}"
    )


if __name__ == "__main__":
    main()
