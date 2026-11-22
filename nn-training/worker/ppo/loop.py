"""共享 PPO 更新循环（goal / intent 两个 numpy 头）—— `python.refactor.md` §0.4-11 / B8 ④。

`worker/ppo/goal.py` 与 `worker/ppo/intent.py` 的 update 不是「拷了一整段」，而是
**同一顺序写了两遍**：装载 → 前向 → loss 组装 → 反向 → 记录 → 早停。两处差异只有
名字与少数几项 loss，所以这里只留**顺序**，差异全部落成两份配置：

  · `HeadSpec` —— 名字面：动作 / 旧 logp / 掩码的列名，日志前缀，`bc` 列；
  · `LossTerms` —— 算式面：前向（返回 `(logp, aux_logits, value)`）与可选辅助项
    （goal 的 engage CE），以及 clip / vf / ent / grad-norm 系数由调用方给。

数值面逐位不变：每一步都与原实现同序、同表达式（含「每 chunk 一次 `sync_scalars`」
与 warmup 期 `kl` 恒 0 的短路）。两个入口的签名与 argv 也不动 —— 它们只剩转发。

`tests/worker/test_ppo_loop_core.py` 钉住三件事：核心不认识两个头的名字（字符串
字面量里零 `goal`/`intent`）、两个入口仍是薄适配器、装载面零 12 行克隆窗口。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

import torch
import torch.nn.functional as F

from worker.ppo.common import _ppo_load, _ppo_save, approx_kl_est, log, sync_scalars
from worker.ppo.np_core import optimizer_step
from worker.ppo.rng import PPORng
from worker.ppo.trainer import tensored_chunks

#: target_kl 早停阈值（Schulman 无偏估计量口径，§P0-3）。两个头同值。
TARGET_KL = 0.02
#: 心跳日志间隔（秒）。
HB_SEC = 60.0

#: 前向：`(model, obs, scalars, inject, mask) → (logp_full, aux_logits, value)`。
#: aux_logits 由 `LossTerms.aux` 消费（goal 的 engage 头；intent 不用，给 `None`）。
#: `model` 用 `object`（torch / paddle / 测试替身都是合法句柄，框架类型不进本模块）。
Forward = Callable[
    [object, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor],
    tuple[torch.Tensor, torch.Tensor | None, torch.Tensor],
]
#: 辅助项：`(aux_logits, chunk) → loss | None`（返回 None = 本 chunk 不训该项）。
AuxLoss = Callable[[torch.Tensor, dict[str, torch.Tensor]], torch.Tensor | None]


class _PPOModel(Protocol):
    """循环只需要模型的三件事（`torch.nn.Module` 与测试替身都满足）。

    比 `object` 多一层：`object` 下 `model.train()` 是 mypy 错误，得就地 `cast`（把验错藏在
    函数体里）；Protocol 把这三件事写成契约，框架类型依然不进签名。
    """

    def train(self) -> object: ...

    def parameters(self) -> Iterable[torch.Tensor]: ...

    def named_parameters(self) -> Iterable[tuple[str, torch.Tensor]]: ...


class _GradOpt(Protocol):
    """优化器句柄：循环只调 `zero_grad()`（步进在 `np_core.optimizer_step` 里）。"""

    def zero_grad(self) -> None: ...


@dataclass(frozen=True)
class HeadSpec:
    """一个头的**名字面**：列名 + 日志前缀 + 是否保留 `bc` 统计列。"""

    tag: str
    a_key: str
    lp_key: str
    mask_key: str
    stats_with_bc: bool = False


@dataclass(frozen=True)
class LossTerms:
    """一个头的**算式面**：前向、系数来源、可选辅助项。"""

    forward: Forward
    ent_coef: float
    vf_coef: float = 1.0
    clip_eps: float = 0.2
    max_grad_norm: float = 1.0
    aux: AuxLoss | None = None


@dataclass(frozen=True, kw_only=True)
class PPOSpec:
    """一次 PPO update 的全部输入（`run_ppo` 只收它）。

    B8 ④（2026-11-19）曾特意把 15 个形参摊在签名上，理由是「形参表就是两个入口的契约，
    包成对象只会把它隐形」。2026-11-22（P0-3 第四十六刀）改判：契约其实**不在**签名上 ——
    它在 `goal.py` / `intent.py` 那两只「每条腿必须交齐」的入口，而入口本身由
    `tests/worker/test_ppo_loop_core.py::test_entry_signatures_are_frozen` 钉死；
    摊开的签名反而给了第二条隐形通道：两个入口各自把 10 个关键字手抄一遍（`ckpt_path` /
    `target_kl` / `seed` / `value_warmup_epochs` / `ref_model` / `kl_coef` /
    `on_epoch_done` / `rng`），漏抄一个不报错 —— 三级训练旋钮（warmup / kickstarting /
    早停）就此静默失效。kw_only 的字段表钉在 `tests/worker/test_ppo_loop_spec.py`，
    隐形度不高于原来的位置参数。
    """

    #: 2. 句柄面（torch / 测试替身都是合法句柄，框架类型不进本模块）。
    model: _PPOModel
    opt: _GradOpt
    #: numpy minibatch 列表（`tensored_chunks` 的入参）。
    chunks: list[dict]
    epochs: int
    device: torch.device | str
    # 3. 配置面：名字面 + 算式面。
    head: HeadSpec
    terms: LossTerms
    # 4. 训练旋钮（全部带缺省：工具/冒烟调用面只给前六项）。
    ckpt_path: str | None = None
    target_kl: float = TARGET_KL
    seed: int = 7
    value_warmup_epochs: int = 0
    ref_model: object | None = None
    kl_coef: float = 0.0
    on_epoch_done: Callable[[int, object], None] | None = None
    rng: PPORng | None = None


def _empty(head: HeadSpec) -> dict[str, float | bool]:
    """空 chunks 的返回值（键集与真跑一遍后的聚合完全一致）。"""
    out: dict[str, float | bool] = {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0}
    if head.stats_with_bc:
        out["bc"] = 0.0
    out["gnorm"] = 0.0
    out["mean_ret"] = 0.0
    out["early_stopped"] = False
    return out


@dataclass(frozen=True)
class _StepCtx:
    """一个 epoch 内恒定的 step 旋钮（`warmup` 是 epoch 粒度，其余是整个 update 粒度）。"""

    warmup: bool
    ref_model: object
    kl_coef: float
    device: torch.device | str


def _step(
    model,
    opt,
    e: dict[str, torch.Tensor],
    head: HeadSpec,
    terms: LossTerms,
    ctx: _StepCtx,
) -> dict[str, float]:
    """一个 minibatch：前向 → loss → 反向 → 一次 host 同步 → 统计行。"""
    warmup = ctx.warmup
    ref_model = ctx.ref_model
    kl_coef = ctx.kl_coef
    device = ctx.device
    obs = e["obs"]
    sc = e["scalars"]
    inj = e["inject"]
    a = e[head.a_key]
    lp_old = e[head.lp_key]
    mask = e[head.mask_key]
    adv = e["adv"]
    ret = e["ret"]

    lp_new, aux_logits, val = terms.forward(model, obs, sc, inj, mask)
    lp_new_a = lp_new.gather(1, a.unsqueeze(1)).squeeze(1)

    value_loss = F.mse_loss(val.squeeze(-1), ret)
    entropy = -(lp_new.exp() * lp_new).sum(dim=-1).mean()

    if warmup:
        # value-only 预热：策略 loss/entropy/辅助项/KL 都不入 loss（策略已冻结）。
        policy_loss = torch.zeros((), device=device)
        loss = terms.vf_coef * value_loss
    else:
        ratio = torch.exp(lp_new_a - lp_old)
        surr1 = ratio * adv
        surr2 = torch.clamp(ratio, 1.0 - terms.clip_eps, 1.0 + terms.clip_eps) * adv
        policy_loss = -torch.min(surr1, surr2).mean()
        loss = policy_loss + terms.vf_coef * value_loss - terms.ent_coef * entropy
        if terms.aux is not None and aux_logits is not None:
            aux_loss = terms.aux(aux_logits, e)
            if aux_loss is not None:
                loss = loss + aux_loss
        if ref_model is not None and kl_coef > 0:
            with torch.no_grad():
                r_lp, _r_aux, _r_val = terms.forward(ref_model, obs, sc, inj, mask)
            # KL(π_curr ‖ π_ref) = Σ_a π_curr·(log π_curr − log π_ref)
            kl_to_ref = (lp_new.exp() * (lp_new - r_lp)).sum(dim=-1).mean()
            loss = loss + kl_coef * kl_to_ref

    opt.zero_grad()
    loss.backward()
    gn = torch.nn.utils.clip_grad_norm_(model.parameters(), terms.max_grad_norm)
    optimizer_step(opt, device)

    with torch.no_grad():
        # warmup 期 kl 恒 0（原实现短路成 Python float 0.0）；这里用等价的
        # 0 维零张量，保持「一次同步」路径统一。
        kl_t = torch.zeros((), device=device) if warmup else approx_kl_est(lp_old, lp_new_a)
        m = sync_scalars(
            {
                "policy": policy_loss,
                "value": value_loss,
                "entropy": entropy,
                "kl": kl_t,
                "mean_ret": ret.mean(),
                "gnorm": gn,
            }
        )
    row: dict[str, float] = {
        "policy": m["policy"],
        "value": m["value"],
        "entropy": m["entropy"],
        "kl": m["kl"],
    }
    if head.stats_with_bc:
        row["bc"] = 0.0  # bc 软目标已清理（P2-6a）；保留键保 schema 兼容
    row["mean_ret"] = m["mean_ret"]
    row["gnorm"] = m["gnorm"]
    return row


def _log_epoch(
    stats: list[dict[str, float]], head: HeadSpec, ep: int, epochs: int, warmup: bool, t0: float
) -> float:
    """一个 epoch 的收尾日志，返回该 epoch 的平均 KL（早停判据）。"""
    n_e = max(1, len(stats))
    ep_kl = sum(s["kl"] for s in stats) / n_e
    log(
        f"[{head.tag}] epoch {ep + 1}/{epochs}{'[warmup]' if warmup else ''} done "
        f"({time.time() - t0:.0f}s total): kl={ep_kl:.4f} "
        f"entropy={sum(s['entropy'] for s in stats) / n_e:.4f} "
        f"policy={sum(s['policy'] for s in stats) / n_e:.4f} "
        f"value={sum(s['value'] for s in stats) / n_e:.4f}"
    )
    return ep_kl


def run_ppo(spec: PPOSpec) -> dict[str, float | bool]:
    """跑 epochs 遍 PPO，返回聚合指标（policy / value / entropy / kl / gnorm / mean_ret
    [+ bc] / early_stopped）。入参全在 `PPOSpec`（字段语义见该类 docstring）。

    value_warmup_epochs > 0：前 N 个 epoch 只训 `value_head.*`（其余参数 `requires_grad=False`），
    策略 loss/entropy 不入 loss ⇒ KL 恒 0。
    ref_model + kl_coef > 0：kickstarting，非 warmup 时加 `kl_coef·KL(π_curr‖π_ref)`。
    on_epoch_done(ep_done, model)：每个 epoch 完成后同步回调（stream 的双缓冲预采触发点）。
    rng：显式随机流（§P1-7）；不给就按 `seed` 现造（工具/冒烟调用面）。
    """
    model = spec.model
    opt = spec.opt
    chunks = spec.chunks
    epochs = spec.epochs
    device = spec.device
    head = spec.head
    terms = spec.terms
    ckpt_path = spec.ckpt_path
    target_kl = spec.target_kl
    seed = spec.seed
    value_warmup_epochs = spec.value_warmup_epochs
    ref_model = spec.ref_model
    kl_coef = spec.kl_coef
    on_epoch_done = spec.on_epoch_done
    rng = spec.rng
    if not chunks:
        return _empty(head)
    # §P1-7（2026-11-17）：随机流显式给（`PPORng`），全局 `np.random` 不再参与。
    # P1-5（2026-09-02）：**不在 update 内重播种 RNG** —— 流式模式每波调用一次 update，
    # 重播种会让每波 minibatch permutation 序列逐字节相同。RNG 由调用方播种一次；
    # seed 参数保留供签名兼容。
    ppo_rng = rng if rng is not None else PPORng.from_seed(seed)
    model.train()
    stats: list[dict[str, float]] = []
    tensored = tensored_chunks(chunks, device)
    t0 = time.time()
    last_hb = t0
    start_epoch = 0
    if ckpt_path:
        start_epoch = _ppo_load(ckpt_path, model, opt, ppo_rng)
        if start_epoch:
            log(f"[{head.tag}] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done")

    early_stopped = False
    for ep in range(start_epoch, epochs):
        warmup = ep < value_warmup_epochs
        for name, p in model.named_parameters():
            p.requires_grad = (not warmup) or name.startswith("value_head.")
        perm = ppo_rng.epoch.permutation(len(tensored))
        n_ep_start = len(stats)
        ctx = _StepCtx(warmup=warmup, ref_model=ref_model, kl_coef=kl_coef, device=device)
        for j, i in enumerate(perm):
            stats.append(_step(model, opt, tensored[int(i)], head, terms, ctx))
            now = time.time()
            if now - last_hb >= HB_SEC:
                last_hb = now
                recent = stats[-32:]
                n_r = len(recent)
                log(
                    f"[{head.tag}] ep {ep + 1}/{epochs}{'[warmup]' if warmup else ''} "
                    f"chunk {j + 1}/{len(tensored)} elapsed={now - t0:.0f}s "
                    f"kl={sum(s['kl'] for s in recent) / n_r:.4f} "
                    f"entropy={sum(s['entropy'] for s in recent) / n_r:.4f} "
                    f"policy={sum(s['policy'] for s in recent) / n_r:.4f} "
                    f"value={sum(s['value'] for s in recent) / n_r:.4f}"
                )
        if ckpt_path:
            _ppo_save(ckpt_path, model, opt, ep + 1, ppo_rng)
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
        ep_kl = _log_epoch(stats[n_ep_start:], head, ep, epochs, warmup, t0)
        if not warmup and ep_kl > target_kl:
            log(
                f"[{head.tag}] target_kl={target_kl} exceeded (epoch {ep + 1} kl={ep_kl:.4f}) "
                f"— early stopping remaining epochs"
            )
            early_stopped = True
            break

    n = len(stats)
    if n:
        agg: dict[str, float | bool] = {k: sum(s[k] for s in stats) / n for k in stats[0]}
    else:
        agg = {k: v for k, v in _empty(head).items() if k != "early_stopped"}
    agg["early_stopped"] = early_stopped
    return agg
