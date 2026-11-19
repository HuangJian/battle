"""训练步的**框架算子面**：数学只写一遍，torch / Paddle 各给一张函数表（B8 第十三刀）。

`worker/ppo/engine.py`（torch，per-tick）与 `worker/ppo/engine_paddle.py`（Paddle 双生）的
minibatch 步曾是**同一顺序写两遍**：

    前向 → 三头掩码 log-softmax → ratio/surr → value/entropy →（KL 惩罚 / BC-kickstart 锚 /
    demo BC）→ backward → 梯度裁剪 → step → 值锚收缩 / policy rms → 一次 host 同步

两处的差异只有**算子的名字**（`torch.clamp` vs `paddle.clip`、`torch.gather` vs
`take_along_axis`、`opt.zero_grad` vs `opt.clear_grad`、`dim=-1` vs `axis=-1` …）与
三处框架生命周期（`no_grad` / 设备侧零标量 / demo 索引缓冲）。所以：

* **顺序**住在这里（`run_step`，唯一实现）；
* **差异**住在一个 `Backend` 函数表里，两个框架的实现各自留在自己的模块里
  （`engine._torch_backend` / `engine_paddle._paddle_backend`）——本模块既不 import torch
  也不 import paddle，`--help` / 纯 numpy 路径不会拖后端。

为什么不把 `Backend` 做成类：这些钩子没有状态（设备句柄在构建时的闭包里），类只会多一层
`self`；仓规是「能写成函数就别写类」（AGENTS §5）。

数值面：`run_step` 的每一步与原实现同序、同表达式，因此 torch 侧逐位不变、Paddle 侧与
`tools/paddle_parity.py` 的基准读数不变（两侧各自对账，见 `docs/nn/engineering.md` §222）。

钩子都在**调用时**解引用（lambda 包一层模块全局），这样 `monkeypatch.setattr(engine,
"sync_scalars", …)` 这类既有打桩面继续生效 —— 本仓已因「绑定过早 ⇒ 打桩静默失效」翻车三次。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from common.contracts import HeadLayout
from common.schema import MOVE_DIM


@dataclass(frozen=True)
class Backend:
    """一个框架的算子表（每个字段都是无状态纯函数 / 生命周期钩子）。"""

    # 形参表一律 `...`：每个钩子的参数就是张量本身，typed signature 会逼出二十个同义的
    # `Any`（G14 的账要人读，不要噪声）；**语义**写在每个字段的注释里，两侧实现各自收窄。
    #: 逐元素 `exp`（ratio 与 KL 行共用）。
    exp: Callable[..., Any]
    #: `clip(x, lo, hi)`：torch `clamp` / paddle `clip`。
    clip: Callable[..., Any]
    #: 逐元素取小（PPO 的 `min(surr1, surr2)`）。
    minimum: Callable[..., Any]
    mse_loss: Callable[..., Any]
    masked_logsoftmax: Callable[..., Any]
    cat_logprob: Callable[..., Any]
    cat_entropy: Callable[..., Any]
    #: `(logp, ref_logp) → Σ_a π_a·(log π_a − log π_ref)`（逐行，不取均值）。
    kl_rows: Callable[..., Any]
    approx_kl: Callable[..., Any]
    #: 设备侧 0 维零标量（未启用的辅助项）。
    zeros: Callable[[], Any]
    demo_masked_ce: Callable[..., Any]
    #: `(复用缓冲 | None, numpy 索引) → 设备索引张量`（torch 就地 `copy_`；Paddle 现造）。
    demo_index: Callable[..., Any]
    zero_grad: Callable[..., None]
    backward: Callable[..., None]
    #: `(parameters, max_norm) → 裁剪前的总范数`（张量，不进 host）。
    clip_grad_norm: Callable[..., Any]
    optimizer_step: Callable[..., None]
    no_grad: Callable[[], AbstractContextManager[object]]
    sync: Callable[..., dict[str, float]]
    anchor_pull: Callable[..., Any]
    policy_rms: Callable[..., Any]


@dataclass(frozen=True)
class StepConfig:
    """一次 update 内恒定的步级系数（都来自调用方 / 课程）。"""

    clip: float
    ent_coef: float
    vf_coef: float
    kl_coef: float
    kickstart_kl: float
    value_anchor: float
    demo_bc_coef: float
    demo_per_mb: int
    max_grad_norm: float


@dataclass
class StepState:
    """跨 minibatch 的共享状态（张量已物化、ref 已预算、demo 已上设备）。"""

    layout: HeadLayout
    tensored: list[dict]
    ref_cache: Sequence[tuple[Any, ...] | None]
    demo_on: bool = False
    demo_n: int = 0
    demo_t: dict[str, Any] = field(default_factory=dict)
    #: torch 的复用索引缓冲（XLA 上每步现造会让每步都是一张新图）；Paddle 传 None。
    demo_idx_buf: Any = None


def run_step(
    bk: Backend,
    model,
    opt,
    st: StepState,
    cfg: StepConfig,
    rng,
    i: int,
) -> dict[str, float]:
    """一个 minibatch：前向 → loss → backward → 裁剪 → step → 遥测（**唯一实现**）。

    返回统计行（11 个键，顺序即 `sync` 的取值顺序）——调用方只负责循环 / 心跳 / 诊断。
    """
    e = st.tensored[int(i)]
    obs = e["obs"]
    sc = e["scalars"]
    mask = e["mask"]  # (T, 7) legacy / (T, 9) v6 —— 切片由 `HeadLayout` 给
    layout = st.layout

    # B1 v6：`PPOStudent.forward` 在 `action_v6` 下返回 **4-tuple**（legacy 3-tuple）。
    # 动作空间在调用方按 shard 事实定一次，这里只消费契约，不逐 chunk 嗅 `a_hold`。
    out = model(obs, sc, e.get("extra"))
    if layout.has_hold:
        mv, fr, hd, val = out
    else:
        mv, fr, val = out
        hd = None
    # 掩码布局由 `HeadLayout` 给切片（**不能复用同一组切片**）：legacy [move5 | fire2] ⇒
    # :5 / 5:7；v6 [move4 | fire3 | hold2] ⇒ :4 / 4:7 / 7:9。用 legacy 的 `MOVE_DIM` 去切
    # v6 掩码会把 hold[0] 当成 fire 的第 3 类、同时切掉 move 的 right —— 静默错位，loss 照降。
    move_logp = bk.masked_logsoftmax(mv, mask[:, layout.move_slice])
    fire_logp = bk.masked_logsoftmax(fr, mask[:, layout.fire_slice])
    hold_logp = (
        bk.masked_logsoftmax(hd, mask[:, layout.hold_slice])
        if layout.hold_slice is not None and hd is not None
        else None
    )

    lp_new = bk.cat_logprob(e["a_move"], move_logp) + bk.cat_logprob(e["a_fire"], fire_logp)
    lp_old = e["lp_move"] + e["lp_fire"]
    if hold_logp is not None:
        lp_new = lp_new + bk.cat_logprob(e["a_hold"], hold_logp)
        lp_old = lp_old + e["lp_hold"]

    adv = e["adv"]
    ret = e["ret"]
    ratio = bk.exp(lp_new - lp_old)
    surr1 = ratio * adv
    surr2 = bk.clip(ratio, 1.0 - cfg.clip, 1.0 + cfg.clip) * adv
    policy_loss = -bk.minimum(surr1, surr2).mean()

    value_loss = bk.mse_loss(val.squeeze(-1), ret)
    entropy = bk.cat_entropy(move_logp) + bk.cat_entropy(fire_logp)
    if hold_logp is not None:
        entropy = entropy + bk.cat_entropy(hold_logp)

    loss = policy_loss + cfg.vf_coef * value_loss - cfg.ent_coef * entropy
    if cfg.kl_coef > 0.0:
        # 对采样策略（lp_old）的 KL 惩罚：与 approx_kl 同估计量，但保留可微项。
        loss = loss + cfg.kl_coef * ((ratio - 1.0) - (lp_new - lp_old)).mean()
    kick_mean = bk.zeros()
    ref = st.ref_cache[int(i)]
    if ref is not None:
        # 预计算缓存：数值与「此处现算 ref_model」逐位相同，每 chunk 只付一次前向。
        # ★ v6：**三头都锚** —— hold 是 v6 唯一新增的自由度，漏掉它等于 kickstart 对
        # hold 头完全不生效（move/fire 被钉住，hold 却能第一时间学坏）。
        kl_parts = bk.kl_rows(move_logp, ref[0]) + bk.kl_rows(fire_logp, ref[1])
        if len(ref) > 2 and hold_logp is not None:
            kl_parts = kl_parts + bk.kl_rows(hold_logp, ref[2])
        kick_mean = kl_parts.mean()
        loss = loss + cfg.kickstart_kl * kick_mean
    demo_bc_mean = bk.zeros()
    if st.demo_on:
        # demo 流（`PPORng.demo`）与 epoch 顺序**分开**：改 demo 系数 / 抽样量不会推歪
        # 哪个 chunk 先训（§P1-7）；状态随 ckpt 落盘，续跑从同一点接着抽。
        didx = rng.demo.integers(0, st.demo_n, size=int(cfg.demo_per_mb))
        didx_t = bk.demo_index(st.demo_idx_buf, didx)
        demo_t = st.demo_t
        dm = model(
            demo_t["obs"][didx_t],
            demo_t["scalars"][didx_t],
            demo_t["extra"][didx_t] if "extra" in demo_t else None,
        )[0]
        dmm = demo_t["masks"][didx_t]
        dact = demo_t["actions"][didx_t]
        # 只训 move（2026-10-18）：fire 的 mask 语义是「当前打得出去吗」，label 是
        # 「有没有按住」，冷却期两者互斥 ⇒ 可训练子集里 hold 只有 1.84%，fire CE 实际
        # 只教 release。恢复方式见 `tests/worker/test_ppo_demo_mix.py` 的守灵用例。
        demo_bc_mean = bk.demo_masked_ce(dm, dact[:, 0], dmm[:, :MOVE_DIM])
        loss = loss + float(cfg.demo_bc_coef) * demo_bc_mean

    bk.zero_grad(opt)
    bk.backward(loss)
    gn = bk.clip_grad_norm(model.parameters(), cfg.max_grad_norm)
    bk.optimizer_step(opt)
    # 值锚收缩放在 clip+step **之后** ⇒ 不吃 clip 配额、不进 Adam 矩（ρ≤0 只读数）。
    value_new_rms = bk.anchor_pull(model, cfg.value_anchor)
    policy_new_rms = bk.policy_rms(model)

    with bk.no_grad():
        # 一次同步取全部 11 个标量：逐项 `.item()` 在 CUDA 上每次都是全设备同步。
        return bk.sync(
            {
                "policy": policy_loss,
                "value": value_loss,
                "entropy": entropy,
                "kl": bk.approx_kl(lp_old, lp_new),
                "kickstart": kick_mean,
                "demo_bc": demo_bc_mean,
                "mean_ret": ret.mean(),
                "mean_adv": adv.mean(),
                "gnorm": gn,
                "value_new_rms": value_new_rms,
                "policy_new_rms": policy_new_rms,
            }
        )
