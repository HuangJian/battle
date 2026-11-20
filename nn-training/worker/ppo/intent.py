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

from common.cli import run_main
from common.contracts import INTENT_EPISODE_FIELDS, IntentEpisode
from worker.models.intent_net import IntentNet, export_intent_weights, load_intent_weights

# 共享 PPO 基础设施（ppo_common.py；行为与旧内联实现逐字节一致，见其模块 doc）。
from worker.ppo._episode import episode_from_shard
from worker.ppo.cli import CliSpec
from worker.ppo.cli import run as run_cli

# `_ppo_load` 是 `worker/backend.py::RolloutBackend` 的成员（`trainer/stream.py` 调
# `backend._ppo_load`）—— 更新循环搬去 `worker/ppo/loop.py` 后本模块不再直接用它，
# 但这个名字必须留在模块命名空间里。
from worker.ppo.common import _ppo_load as _ppo_load  # RolloutBackend 契约（见上）
from worker.ppo.common import log, masked_logsoftmax
from worker.ppo.loop import HeadSpec, LossTerms, run_ppo
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
# 健康稳态 kl≈0.045-0.054/iter（biz/breaker.py，熔断 0.15×连续 3）——per-epoch 预算取 0.1
# （2026-08-27 §30 改：0.1 → **0.04**。意图 8 类小空间 ~280 步/轮，单 epoch KL=0.101 的
# 大更新就把策略从多样（熵 0.346）推到近单点（0.098）——too coarse。0.04 把单轮漂移
# 压到意图熵正则（0.08）能拉回的幅度内）
# 允许每迭代 ~2 epoch 策略更新，同时早停仍能拦截单轮剧烈漂移（P1-1k3 pace 护栏）。
# 2026-09-02（plan/python-refactor.md P0-3）：估计量改为 Schulman 无偏式
# （ppo.common.approx_kl_est），阈值随口径换算 ×0.5——旧 2× 有偏口径下"0.04"
# 实为真实 KL≈0.02，early stopping 在真实漂移减半处即触发。
TARGET_KL = 0.02
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
            from worker.data.weights_io import load_weights_json

            meta, _ = load_weights_json(weights_path)
            a = meta.get("arch", {})
            h = a.get("h", 64)
            d = a.get("d", 8)
        except Exception as e:  # 权重损坏/格式不符 → 回落默认架构（保留原行为，仅不再静默）
            log(f"[ppo_intent] WARN build_rl_net: ignoring {weights_path}: {e!r}")
    return IntentRLNet(h=h or 64, d=d or 8)


# ---------------- trajectory loading ----------------
# 意图 shard 字段表：{key: (filename, dtype)} —— 与旧 load_intent_shard 逐字段一致。
_INTENT_SHARD_SPEC: dict[str, tuple[str, npt.DTypeLike]] = {
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


def load_intent_shard(dirpath: str) -> dict[str, np.ndarray]:
    return load_shard_fields(dirpath, _INTENT_SHARD_SPEC)


def compute_gae_variable(
    rewards, values, dones, dt, gamma_tick: float = GAMMA_TICK, lam: float = LAM
):
    """意图步 GAE：每步折扣 γ_step = γ_tick^Δt（Δt = 窗口时长 tick）。

    Δt≡1 时与 ppo.py 的定长 per-tick GAE 逐字节一致（test_ppo_intent.py 断言）。
    rewards[t]=r_{t+1}（意图步回报），values[t]=V(s_t)，dones[t]=是否终局步。
    （实现统一收敛至 ppo_common.compute_gae(dt=...)，本名保留为兼容别名。）
    """
    return compute_gae(rewards, values, dones, gamma_tick, lam, dt)


def load_episode_from_shard(
    dirpath: str, gamma: float = GAMMA_TICK, lam: float = LAM
) -> IntentEpisode | None:
    """流式 backend 接口（trainer/stream.py）：意图 shard → 可训练 episode（adv/ret 未归一）。

    与 ppo.load_episode_from_shard 同签名——run_rollout_stream 以 backend 参数复用
    同一套流式基础设施（意图步变步长 GAE + inject/dt 字段）。

    `gamma` / `lam` **必须收**：stream.py 恒传 `(shard, args.gamma, args.lam)`，而本函数以前
    只收 `dirpath` ⇒ 逐 shard 装载全类型 TypeError，被 `except Exception` 吞成一行
    `skip bad shard`（不崩、也不训）。值与模块常量同源（`GAMMA_TICK`/`LAM` = 0.995/0.95），
    缺省时数值逐字节不变；传了就以调用方为准（与 per-tick 的 `load_episode_from_shard` 一致）。
    """
    ep = episode_from_shard(
        load_intent_shard(dirpath),
        lambda d: compute_gae_variable(
            d["reward"], d["value"], d["done"], d["dt"], gamma_tick=gamma, lam=lam
        ),
        fields=INTENT_EPISODE_FIELDS,
    )
    # 键集与序就是上面那份契约（fields 从它派生）⇒ cast 只是把骨架的动态 dict 交回静态类型。
    return None if ep is None else cast(IntentEpisode, ep)


def load_episodes_intent(
    data_root: str, gamma: float = GAMMA_TICK, lam: float = LAM, normalize_adv: bool = True
) -> list[dict]:
    """全盘回退装载（stream.py 无新结算时调它，**同样带** gamma/lam）。"""
    return load_episodes_common(
        data_root,
        label="ppo_intent",
        shard_kind="intent RL",
        need_files=("reward.npy", "obs.npy", "dt.npy"),
        shard_loader=load_intent_shard,
        gae=lambda d: compute_gae_variable(
            d["reward"], d["value"], d["done"], d["dt"], gamma_tick=gamma, lam=lam
        ),
        gae_name="variable-step GAE",
        normalize_adv=normalize_adv,
        normalize_ret=True,  # intent：adv 与 ret 都全局归一（I13 逐关规范化）
    )


# ---------------- PPO update ----------------
#: 名字面：intent 的列名 + 日志前缀（没有 `bc` 统计列）。
_SPEC = HeadSpec(tag="ppo_intent", a_key="a_intent", lp_key="lp_intent", mask_key="mask")


def _forward_intent(model, obs, sc, inj, mask):
    """意图头前向：三头 logits → 掩码 log-softmax（无辅助项 ⇒ 第二项恒 `None`）。"""
    i_log, _en, _an, val = model.forward_rl(obs, sc, inj)
    return masked_logsoftmax(i_log, mask), None, val


def ppo_update_intent(
    model,
    opt,
    chunks,
    epochs,
    device,
    ckpt_path: str | None = None,
    target_kl: float = TARGET_KL,
    seed: int = 7,
    value_warmup_epochs: int = 0,
    ref_model: IntentNet | None = None,
    kl_coef: float = 0.0,
    on_epoch_done=None,
    rng: PPORng | None = None,
):
    """chunks: intent 步 minibatch dicts（obs/scalars/inject/a_intent/lp_intent/adv/ret/mask）。

    **顺序在 `worker/ppo/loop.py::run_ppo`**（B8 ④：与 goal 曾各写一份同一顺序）；这里只
    给两份配置：`_SPEC`（列名/日志前缀）+ `LossTerms`（前向 = 三头 + 掩码 softmax，
    无辅助项）。数值逐位不变。

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
    return run_ppo(
        model,
        opt,
        chunks,
        epochs,
        device,
        spec=_SPEC,
        terms=LossTerms(forward=_forward_intent, ent_coef=ENT_COEF),
        ckpt_path=ckpt_path,
        target_kl=target_kl,
        seed=seed,
        value_warmup_epochs=value_warmup_epochs,
        ref_model=ref_model,
        kl_coef=kl_coef,
        on_epoch_done=on_epoch_done,
        rng=rng,
    )

# ---- stream backend 接口别名（trainer/stream.py 的 backend.update / load_episodes）----
# 意图 RL 与 per-tick RL 共用同一套流式基础设施；checkpoint 原语由 ppo_common
# 提供（顶部 import _ppo_save/_ppo_load）。ppo 不依赖本模块，无环。
update = ppo_update_intent
load_episodes = load_episodes_intent


#: CLI 面：顺序与文案在 `worker/ppo/cli.py`；这里只绑 intent 的五件事与两句差异。
def _spec() -> CliSpec:
    """intent 的 CLI 配置（**每次入口调用时**现取模块全局 ⇒ 打桩面不被冻结）。"""
    return CliSpec(
        tag="ppo_intent",
        help_init="B′/BC intent weights (init mode)",
        help_resume="RL intent weights (update mode)",
        help_data="intent trajectory shard root (update)",
        help_warmup="前 N 个 epoch 只训 value 头（策略冻结；B′ 冷启动 value 随机）",
        help_kl="kickstarting KL 惩罚系数（plan #5 衰减 0.5/iter；0=关闭）",
        help_ref="kickstarting 参考策略权重（B′ 冻结快照；缺省 = 当前权重）",
        step_word="intent-steps",
        init_desc="B′/BC intent + random value",
        with_bc=False,
        lr=LR,
        build_net=build_rl_net,
        load_weights=load_intent_weights,
        export_weights=export_intent_weights,
        load_episodes=load_episodes_intent,
        update=ppo_update_intent,
    )


def main() -> None:
    """intent CLI（顺序与文案在 `worker/ppo/cli.py`；本函数只绑定 intent 的配置）。"""
    run_cli(_spec())


if __name__ == "__main__":
    run_main(main)
