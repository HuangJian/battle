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

# 仓库根探测（B4，2026-09-02；2026-09-30 修）：包已安装（pip install -e .）或
# script-dir/cwd 在 nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入
# sys.path——不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正
# import，避免探针导入产生 F401。
# ⚠ 本文件搬到 `worker/<pkg>/` 后上溯层数 +1，且探针名随刀 2 的 `schema` → `common`
# 更正：算错一层会插进 `worker/`，于是 `worker/cmd.py` 遮蔽 stdlib `cmd`（torch 的
# `import pdb` 当场炸）——「兜底路径」写错从来不是小事。
import importlib.util as _ilu

if _ilu.find_spec("common") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import os
from typing import cast

import numpy as np
import numpy.typing as npt
import torch
import torch.nn.functional as F

from common.cli import run_main
from common.contracts import GOAL_EPISODE_FIELDS, GOAL_EPISODE_OPTIONAL, GoalEpisode
from common.schema import BOARD
from worker.models.goal_net import GoalNet, export_goal_weights, load_goal_weights
from worker.ppo._episode import episode_from_shard
from worker.ppo.cli import CliSpec
from worker.ppo.cli import run as run_cli

# `_ppo_load` 是 `worker/backend.py::RolloutBackend` 的成员（`trainer/stream.py` 调
# `backend._ppo_load`）—— 更新循环搬去 `worker/ppo/loop.py` 后本模块不再直接用它，
# 但这个名字必须留在模块命名空间里。
from worker.ppo.common import _ppo_load as _ppo_load  # RolloutBackend 契约（见上）
from worker.ppo.common import log, masked_logsoftmax
from worker.ppo.loop import HeadSpec, LossTerms, PPOSpec, run_ppo
from worker.ppo.np_core import (
    # `chunk_episodes` 是 `worker/backend.py::RolloutBackend` 的成员（`trainer/stream.py` 调 `backend.chunk_episodes`）；
    # 本模块不再直接调用它，但这个名字必须留在模块命名空间里。
    chunk_episodes as chunk_episodes,
)
from worker.ppo.np_core import (
    compute_gae,
    discover_shards,
    load_episodes_common,
    load_shard_fields,
)
from worker.ppo.rng import PPORng

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
            from worker.data.weights_io import load_weights_json

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


def load_episode_from_shard(
    dirpath: str, gamma: float = GAMMA_TICK, lam: float = LAM
) -> GoalEpisode | None:
    """流式 backend 接口（trainer/stream.py）：goal shard → 可训练 episode（adv/ret 未归一）。

    `gamma` / `lam` **必须收**：stream.py 恒传 `(shard, args.gamma, args.lam)`，而本函数以前
    只收 `dirpath` ⇒ 逐 shard 装载全类型 TypeError，被 `except Exception` 吞成一行
    `skip bad shard`（不崩、也不训）。缺省值 = 模块常量（数值逐字节不变）。
    """
    ep = episode_from_shard(
        load_goal_shard(dirpath),
        lambda d: compute_gae_variable(
            d["reward"], d["value"], d["done"], d["dt"], gamma_tick=gamma, lam=lam
        ),
        fields=GOAL_EPISODE_FIELDS,
        optional=GOAL_EPISODE_OPTIONAL,
    )
    # 键集与序就是上面那份契约（fields 从它派生）⇒ cast 只是把骨架的动态 dict 交回静态类型。
    # 带标注的盘上多一列 `engage_label`（`GoalEpisodeLabeled` 是子型）。
    return None if ep is None else cast(GoalEpisode, ep)


def load_episodes_goal(
    data_root: str, gamma: float = GAMMA_TICK, lam: float = LAM, normalize_adv: bool = True
) -> list[dict]:
    """全盘回退装载（stream.py 无新结算时调它，**同样带** gamma/lam）。"""
    return load_episodes_common(
        data_root,
        label="ppo_goal",
        shard_kind="goal RL",
        need_files=("reward.npy", "obs.npy", "dt.npy", "goal_mask.npy"),
        shard_loader=load_goal_shard,
        gae=lambda d: compute_gae_variable(
            d["reward"], d["value"], d["done"], d["dt"], gamma_tick=gamma, lam=lam
        ),
        gae_name="variable-step GAE",
        normalize_adv=normalize_adv,
        normalize_ret=True,
    )


# ---------------- PPO update ----------------
#: 名字面：goal 的列名 + 日志前缀 + `bc` 统计列（schema 兼容占位）。
_SPEC = HeadSpec(
    tag="ppo_goal",
    a_key="a_goal",
    lp_key="lp_goal",
    mask_key="goal_mask",
    stats_with_bc=True,
)


def _engage_ce(aux_logits, e: dict) -> torch.Tensor | None:
    """engage 辅助 CE（仅 shard 带 `engage_label` 的离线标注语料；on-policy 不训）。"""
    if "engage_label" not in e:
        return None
    return F.cross_entropy(aux_logits, e["engage_label"])


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
    rng: PPORng | None = None,
):
    """chunks: goal 承诺步 minibatch dicts（obs/scalars/inject/a_goal/lp_goal/adv/ret/goal_mask
    [+ engage_label]）。

    **顺序在 `worker/ppo/loop.py::run_ppo`**（B8 ④：与 intent 曾各写一份同一顺序）；这里只
    给两份配置：`_SPEC`（列名/日志前缀/bc 列）+ `LossTerms`（前向 = `policy_logprobs`，
    辅助项 = engage CE）。数值逐位不变。

    on_epoch_done(ep_done, model)：每个 epoch 完成后同步回调（与 ppo / ppo_intent 逐
    行对齐）——双缓冲提前预采的触发点：stream 在 epoch3 完成时把 θ_{N,e3} 存盘 spawn
    首波预采，PPO 继续最后一个 epoch。trainer/stream.py:123-124 **无条件**注入该回调，
    故本形参是契约的一部分（缺失即 `--mode goal --stream 1` 首个波次 TypeError，
    plan/python-refactor.md P0-1；由 tests/worker/test_backend_contract.py 守护）。

    value_warmup：同 ppo_intent——前 N epoch 冻结主干只训 value 头（goal-BC 冷启动
    value 随机，直接 PPO 会被 value 噪声主导）。
    kickstarting：ref_model = goal-BC 冻结快照，kl_coef·KL(π_curr‖π_ref)，系数外置退火。
    target_kl 早停同 ppo_intent。
    """
    return run_ppo(
        PPOSpec(
            model=model,
            opt=opt,
            chunks=chunks,
            epochs=epochs,
            device=device,
            head=_SPEC,
            terms=LossTerms(forward=policy_logprobs, ent_coef=ENT_COEF, aux=_engage_ce),
            ckpt_path=ckpt_path,
            target_kl=target_kl,
            seed=seed,
            value_warmup_epochs=value_warmup_epochs,
            ref_model=ref_model,
            kl_coef=kl_coef,
            on_epoch_done=on_epoch_done,
            rng=rng,
        )
    )

# ---- stream backend 接口别名（trainer/stream.py 的 backend.update / load_episodes / _ppo_load）----
update = ppo_update_goal
load_episodes = load_episodes_goal


#: CLI 面：顺序与文案在 `worker/ppo/cli.py`；这里只绑 goal 的五件事与两句差异。
def _spec() -> CliSpec:
    """goal 的 CLI 配置（**每次入口调用时**现取模块全局 ⇒ 打桩面不被冻结）。"""
    return CliSpec(
        tag="ppo_goal",
        help_init="goal-BC weights (init mode)",
        help_resume="RL goal weights (update mode)",
        help_data="goal trajectory shard root (update)",
        help_warmup=None,
        help_kl=None,
        help_ref="kickstarting 参考策略权重（goal-BC 冻结快照；缺省 = 当前权重）",
        step_word="goal-steps",
        init_desc="goal-BC + random value",
        with_bc=True,
        lr=LR,
        build_net=build_rl_net,
        load_weights=load_goal_weights,
        export_weights=export_goal_weights,
        load_episodes=load_episodes_goal,
        update=ppo_update_goal,
    )


def main() -> None:
    """goal CLI（顺序与文案在 `worker/ppo/cli.py`；本函数只绑定 goal 的配置）。"""
    run_cli(_spec())


if __name__ == "__main__":
    run_main(main)
