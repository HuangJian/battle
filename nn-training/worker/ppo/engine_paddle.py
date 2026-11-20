"""Paddle twin of `worker.ppo.engine.ppo_update` — math parity, not a rewrite.

Scope (PoC, 2026-10-27): the update core only. Every line that touches numbers
mirrors `worker/ppo/engine.py` and `worker/ppo/common.py` one-for-one, so the
parity harness can assert `stats` and post-step weights against the torch run.

Deliberately absent (torch-only concerns, zero effect on the math):
  · XLA step diagnostics / compile cache / mark_step      (no torch_xla here)
  · torch.profiler and `PPO_CUDA_DIAG` wall-clock readouts (logging only)
  · DataParallel                                          (single-device worker)

`ckpt_path` is live: `checkpoint_paddle` writes the **same torch-format**三件套
(model.pt / opt.pt / state.json), so a torch worker can resume from a Paddle-written
checkpoint and vice versa (user ruling 2026-10-27: hub 统一 torch 格式).

Present because they change the numbers: masked log-softmax, clipped surrogate,
KL penalty, BC-kickstart exact KL (precomputed once per chunk), demo-bank move
CE, value-anchor pull, policy/value new-column rms, Adam + global-norm clip.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import paddle

from common.contracts import ActionSpace, head_layout
from worker.ppo.backend import Backend, StepConfig, StepState, run_step
from worker.ppo.checkpoint_paddle import load_ckpt, save_ckpt
from worker.ppo.demo import DemoBank
from worker.ppo.rng import PPORng
from worker.ppo.trainer import aggregate_stats, cat_entropy, cat_entropy_per, chunk_count
from worker.ppo.vram import paddle_free_bytes, require_corpus

CLIP_EPS = 0.2
VF_COEF = 1.0
ENT_COEF = 0.01
LR = 3e-4
MAX_GRAD_NORM = 1.0
HB_SEC = 60.0

F = paddle.nn.functional


def tensored_chunks(chunks: list[dict]) -> list[dict]:
    """numpy minibatch chunks → paddle tensors（数值逐位：同一份 numpy 字节）。"""
    return [{k: paddle.to_tensor(v) for k, v in c.items()} for c in chunks]


def tensored_batch(item: dict) -> dict:
    """单个 numpy minibatch → paddle tensors（P3 lazy 的逐批上卡）。

    只读数组（`np.load(mmap_mode="r")` 的切片视图）先复制成可写；`gather()` 的
    fancy index 结果本就可写，这里零成本。
    """
    return {
        k: paddle.to_tensor(v if v.flags.writeable else np.array(v)) for k, v in item.items()
    }


def _ref_logprobs(ref_model: Any, e: dict) -> tuple[paddle.Tensor, ...]:
    """冻结 ref 在单个 minibatch 上的分头 log-softmax（kickstart KL）。

    eager 在训练前按 chunk 预计算，lazy 在 chunk 首次被取到时现算——ref 冻结且与
    训练模型状态无关 ⇒ 两者数值逐位相同。抽成函数让两条路径共用同一份
    「头数 vs 数据动作空间」校验。
    """
    _ro = ref_model(e["obs"], e["scalars"], e.get("extra"))
    _m = e["mask"]
    _ref_space = ActionSpace.from_forward_arity(len(_ro))
    _data_space = ActionSpace.from_batch(e)
    if _ref_space is not _data_space:
        raise ValueError(
            f"[ppo-paddle] kickstart ref 与数据动作空间不同源：ref 返回 {len(_ro)} 个头"
            f"（{_ref_space.value}），chunk 是 {_data_space.value}"
        )
    _layout = head_layout(_ref_space)
    _heads: tuple[paddle.Tensor, ...] = (
        masked_logsoftmax(_ro[0], _m[:, _layout.move_slice]),
        masked_logsoftmax(_ro[1], _m[:, _layout.fire_slice]),
    )
    if _layout.hold_slice is not None:
        _heads = (*_heads, masked_logsoftmax(_ro[2], _m[:, _layout.hold_slice]))
    return _heads


def masked_logsoftmax(logits: paddle.Tensor, mask: paddle.Tensor) -> paddle.Tensor:
    # 不能直接用 `F.log_softmax`：Paddle 的内部实现对低于 ~-64 的对数概率截断
    # （masked 项 -1e9 与「合法但极低概率」项都会变成 -64.3），torch 保留真值。
    # 差异在概率上是 0（exp(-64)≈0），但 `lp_new - lp_old` 会被放大成 60 量级的
    # 假 KL/假 ratio —— PPO 会当场爆掉。这里手写同一份稳定公式：先减 max，再减
    # log-sum-exp；masked 项保持 -1e9 量级，与 torch 逐位同口径。
    big = paddle.full([], 1e9, dtype=logits.dtype)
    m = mask.astype(logits.dtype)
    z = logits + (1.0 - m) * (-big)
    z = z - z.max(axis=-1, keepdim=True)
    return z - paddle.log(paddle.exp(z).sum(axis=-1, keepdim=True))


def cat_logprob(action: paddle.Tensor, logp: paddle.Tensor) -> paddle.Tensor:
    # `paddle.gather` 的轴语义与 torch.gather 不同（index 会整体拼进输出形状，
    # (B,1) 索引得到 (B,B,1)）——逐行取类必须用 take_along_axis。
    return paddle.take_along_axis(logp, action.astype("int64").unsqueeze(1), axis=1).squeeze(1)


def approx_kl_est(lp_old: paddle.Tensor, lp_new: paddle.Tensor) -> paddle.Tensor:
    r = paddle.exp(lp_new - lp_old)
    return ((r - 1.0) - (lp_new - lp_old)).mean()


def _zero_scalar() -> paddle.Tensor:
    return paddle.zeros([], dtype="float32")


def sync_scalars(values: dict[str, paddle.Tensor]) -> dict[str, float]:
    """一次 `.numpy()` 批量取回（与 torch 侧 `stack(...).tolist()` 同值）。"""
    if not values:
        return {}
    keys = list(values)
    flat = paddle.concat([values[k].detach().reshape([1]) for k in keys]).numpy()
    return {k: float(flat[i]) for i, k in enumerate(keys)}


def _demo_masked_ce(
    logits: paddle.Tensor, target: paddle.Tensor, mask: paddle.Tensor
) -> paddle.Tensor:
    """合法类掩码 CE（与 engine._demo_masked_ce 同数学；只用于 demo 的 move 头）。"""
    m = mask > 0
    keep = m.astype("int32").sum(axis=-1) >= 2
    if not bool(keep.any()):
        return logits.sum() * 0.0
    z = paddle.where(m, logits, paddle.full_like(logits, -1e9))
    # `x[keep]`（布尔下标）在 Paddle 3.3 打一条 `tensor.cc:111` 弃用警告 ⇒ nonzero+gather（同序）。
    idx = paddle.nonzero(keep).flatten()
    per = F.cross_entropy(
        paddle.gather(z, idx), paddle.gather(target.astype("int64"), idx), reduction="none"
    )
    return per.mean()


def apply_value_anchor_pull(model: Any, rho: float) -> paddle.Tensor:
    """`value_head` 新列的乘性收缩（ρ≤0 只读数）；返回新列片 rms。"""
    slices: list[tuple[int, int]] = (
        model.value_anchor_slices() if hasattr(model, "value_anchor_slices") else []
    )
    vh = getattr(model, "value_head", None)
    if vh is None or not slices:
        return _zero_scalar()
    with paddle.no_grad():
        w = vh.weight
        if rho > 0.0:
            pull = 1.0 - float(rho)
            new = w
            for lo, hi in slices:
                new = paddle.concat([new[:, :lo], new[:, lo:hi] * pull, new[:, hi:]], axis=1)
            w.set_value(new)
        parts = [w[:, lo:hi].flatten() for lo, hi in slices]
        return paddle.concat(parts).pow(2).mean().sqrt() if parts else _zero_scalar()


def policy_new_rms_of(model: Any) -> paddle.Tensor:
    """policy 双头（move/fire）在新列上的 rms（与 value 同列片，纯只读）。"""
    slices: list[tuple[int, int]] = (
        model.value_anchor_slices() if hasattr(model, "value_anchor_slices") else []
    )
    heads = [
        h
        for h in (getattr(model, "move_head", None), getattr(model, "fire_head", None))
        if h is not None
    ]
    if not slices or not heads:
        return _zero_scalar()
    with paddle.no_grad():
        parts = [h.weight[:, lo:hi].flatten() for h in heads for lo, hi in slices]
        return paddle.concat(parts).pow(2).mean().sqrt() if parts else _zero_scalar()


def clip_grad_norm_(parameters, max_norm: float) -> paddle.Tensor:
    """torch `clip_grad_norm_` 的逐式对应：返回总范数，按 `max/(total+1e-6)` 就地缩放。"""
    grads = [p.grad for p in parameters if p.grad is not None]
    if not grads:
        return _zero_scalar()
    total = paddle.sqrt(paddle.add_n([g.pow(2).sum() for g in grads]))
    coef = paddle.minimum(max_norm / (total + 1e-6), paddle.ones([], dtype=total.dtype))
    with paddle.no_grad():
        for g in grads:
            g.set_value(g * coef)
    return total


def _prepare_demo(
    demo_bank: DemoBank | None, demo_bc_coef: float, demo_per_mb: int
) -> tuple[dict[str, paddle.Tensor], int, bool]:
    """demo bank → tensor 视图 + 局数 + 是否启用（自 `ppo_update` 原样搬出）。"""
    demo_on = demo_bank is not None and float(demo_bc_coef) > 0.0 and int(demo_per_mb) > 0
    demo_t: dict[str, paddle.Tensor] = {}
    demo_n = 0
    if demo_on:
        assert demo_bank is not None
        demo_t = {
            "obs": paddle.to_tensor(np.asarray(demo_bank["obs"])),
            "scalars": paddle.to_tensor(np.asarray(demo_bank["scalars"]).astype(np.float32)),
            "actions": paddle.to_tensor(np.asarray(demo_bank["actions"]).astype(np.int64)),
            "masks": paddle.to_tensor(np.asarray(demo_bank["masks"]).astype(np.float32)),
        }
        demo_n = int(demo_t["actions"].shape[0])
        demo_on = demo_n > 0
        if demo_on and "extra" in demo_bank:
            demo_t["extra"] = paddle.to_tensor(np.asarray(demo_bank["extra"]).astype(np.float32))
    return demo_t, demo_n, demo_on


def _build_ref_cache(
    tensored: list[dict], ref_model, kickstart_kl: float
) -> list[tuple[paddle.Tensor, ...] | None]:
    """kickstart ref 前向每 chunk 只算一次（与 torch 侧 ref_cache 同语义；数值相同）。"""
    ref_cache: list[tuple[paddle.Tensor, ...] | None] = [None] * len(tensored)
    if ref_model is not None and kickstart_kl > 0:
        with paddle.no_grad():
            for _i, _e in enumerate(tensored):
                ref_cache[_i] = _ref_logprobs(ref_model, _e)
    return ref_cache


def _zero_stats() -> dict[str, float]:
    """0 步时的零值表（键集合与正常 `stats` 一致）。"""
    return {
        k: 0.0
        for k in (
            "policy",
            "value",
            "entropy",
            "kl",
            "per_step_kl",
            "clip_fraction",
            "kickstart",
            "demo_bc",
            "mean_ret",
            "mean_adv",
            "gnorm",
            "value_new_rms",
            "policy_new_rms",
            "early_stopped",
        )
    }


def _paddle_backend() -> Backend:
    """Paddle 侧的算子表（与 `engine._torch_backend` 逐字段对应，钩子调用时解引用）。"""
    return Backend(
        exp=lambda x: paddle.exp(x),
        clip=lambda x, lo, hi: paddle.clip(x, lo, hi),
        minimum=lambda a, b: paddle.minimum(a, b),
        mse_loss=lambda a, b: F.mse_loss(a, b),
        masked_logsoftmax=lambda logits, mask: masked_logsoftmax(logits, mask),
        cat_logprob=lambda action, logp: cat_logprob(action, logp),
        cat_entropy=lambda logp: cat_entropy(logp),
        cat_entropy_per=lambda logp: cat_entropy_per(logp),
        astype=lambda x, dt: x.astype(dt),
        kl_rows=lambda logp, ref: (logp.exp() * (logp - ref)).sum(axis=-1),
        approx_kl=lambda lp_old, lp_new: approx_kl_est(lp_old, lp_new),
        zeros=_zero_scalar,
        demo_masked_ce=lambda logits, target, mask: _demo_masked_ce(logits, target, mask),
        demo_index=lambda _buf, idx: paddle.to_tensor(idx.astype(np.int64)),
        zero_grad=lambda opt: opt.clear_grad(),
        backward=lambda loss: loss.backward(),
        clip_grad_norm=lambda params, mx: clip_grad_norm_(params, mx),
        optimizer_step=lambda opt: opt.step(),
        no_grad=paddle.no_grad,
        sync=lambda values: sync_scalars(values),
        anchor_pull=lambda model, rho: apply_value_anchor_pull(model, rho),
        policy_rms=lambda model: policy_new_rms_of(model),
    )


def ppo_update(
    model,
    opt,
    chunks,
    epochs,
    device=None,
    ckpt_path: str | None = None,
    on_epoch_done=None,
    kl_coef: float = 0.0,
    ref_model=None,
    kickstart_kl: float = 0.0,
    ent_coef: float | None = None,
    demo_bank: DemoBank | None = None,
    demo_bc_coef: float = 0.0,
    demo_per_mb: int = 0,
    progress: Any = None,
    progress_head: str = "[ppo] PPO",
    value_anchor: float = 0.0,
    #: 引擎内部步数的**唯一出口**（`{"steps_done": int}`）：调用方不许拿
    #: `epochs × len(chunks)` 自己重算 —— 引擎一旦跳 chunk / 从 ckpt 中途起步，重算就会静默
    #: 漂移，而那个数要写进 `state.json` 与回传的 opt `step`（bias correction 的时基）。
    steps_out: dict[str, int] | None = None,
    rng: PPORng | None = None,
    lazy: bool = False,
    log=lambda _msg: None,
):
    """Paddle PPO update. Same math as `worker.ppo.engine.ppo_update`; see module doc.

    `ckpt_path` 语义与 torch 侧一致：开跑前载入（model/opt/epochs_done/三条随机流的
    `bit_generator.state`），每个 epoch 尾落盘一次；torch 与 Paddle 写出的 checkpoint
    互相可读。`rng` 与 torch 侧同口径（None = 种子 0，仅冒烟/工具用）。

    `lazy`（P3）：`chunks` 是 `LazyChunks`（逐 chunk 物化 numpy）。此时不建全量
    paddle 副本，每步 `tensored_batch` 现转当前 minibatch；kickstart ref 改为
    chunk 首次使用时现算（ref 冻结 ⇒ 与 eager 预计算逐位相同）。缺省 False =
    旧路径逐字节不变。
    """
    model.train()
    if device is not None:
        # Paddle 的设备是**进程级**状态（没有 torch.device 那种对象）：把调用方给的同一个值
        # 再落一次（幂等；`remote/train_core` 的 Paddle 核开跑前也 set_device）。留着这个形参
        # 是为了与 torch 侧 `ppo_update(model, opt, chunks, epochs, device, …)` 的调用面一致
        # —— 不落地它就是一个**静默失效的承诺**（评审 2026-10-27 抓到的那一类）。
        paddle.set_device(str(device))
    clip = CLIP_EPS
    ent_c = ENT_COEF if ent_coef is None else float(ent_coef)
    stats: list[dict[str, float]] = []
    ppo_rng = rng if rng is not None else PPORng.from_seed(0)
    # 与 torch 侧同规：eager 全量上卡前查显存（探测不到就放行，绝不误拦 CPU 训练）。
    # lazy 逐批上卡 ⇒ 没有「全量语料」可言，`corpus_bytes` 也不接受 LazyChunks。
    if not lazy:
        require_corpus(chunks, paddle_free_bytes(device))
    tensored: Any = chunks if lazy else tensored_chunks(chunks)
    n_chunks = chunk_count(chunks)
    total_steps = n_chunks * epochs
    # 动作空间只在数据边界嗅一次；ref 与每个 minibatch 都读这份契约。
    _first = tensored.get(0) if lazy else (tensored[0] if tensored else None)
    space = ActionSpace.from_batch(_first) if _first is not None else ActionSpace.LEGACY
    layout = head_layout(space)
    demo_t, demo_n, demo_on = _prepare_demo(demo_bank, demo_bc_coef, demo_per_mb)
    # lazy：不预计算（那要遍历全量 chunks，破坏延迟物化），chunk 首次取用时现算。
    ref_cache: list[tuple[Any, ...] | None] = (
        [None] * n_chunks if lazy else _build_ref_cache(tensored, ref_model, kickstart_kl)
    )
    # 步级系数 / 跨步状态 / 算子表各构建一次（热路径不再分配）。
    bk = _paddle_backend()
    cfg = StepConfig(
        clip=clip,
        ent_coef=ent_c,
        vf_coef=VF_COEF,
        kl_coef=kl_coef,
        kickstart_kl=kickstart_kl,
        value_anchor=value_anchor,
        demo_bc_coef=demo_bc_coef,
        demo_per_mb=demo_per_mb,
        max_grad_norm=MAX_GRAD_NORM,
    )
    st = StepState(
        layout=layout,
        tensored=tensored,
        ref_cache=ref_cache,
        demo_on=demo_on,
        demo_n=demo_n,
        demo_t=demo_t,
    )
    t0 = time.time()
    last_hb = t0
    start_epoch, steps_done = load_ckpt(model, opt, ckpt_path, ppo_rng)
    if start_epoch:
        log(
            f"[ppo-paddle] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done "
            f"(continuing remaining {epochs - start_epoch}, steps_done={steps_done})"
        )
    for ep in range(start_epoch, epochs):
        perm = ppo_rng.epoch.permutation(n_chunks)
        for j, i in enumerate(perm):
            if lazy:
                # 只把当前 minibatch 上卡；全量 chunks 不再有 paddle 副本。
                e = tensored_batch(tensored.get(int(i)))
                if (
                    ref_model is not None
                    and kickstart_kl > 0
                    and st.ref_cache[int(i)] is None
                ):
                    # lazy：chunk 首次使用时现算（ref 冻结 ⇒ 与 eager 预计算逐位相同）。
                    with paddle.no_grad():
                        st.ref_cache[int(i)] = _ref_logprobs(ref_model, e)
                stats.append(run_step(bk, model, opt, st, cfg, ppo_rng, int(i), batch=e))
            else:
                stats.append(run_step(bk, model, opt, st, cfg, ppo_rng, int(i)))
            steps_done += 1
            if progress is not None:
                now = time.time()
                if now - last_hb >= HB_SEC:
                    last_hb = now
                    done = ep * n_chunks + j + 1
                    el = now - t0
                    eta = el / done * (total_steps - done)
                    progress.add(
                        "进度",
                        f"ep {ep + 1}/{epochs} step {done}/{total_steps} "
                        f"elapsed={el:.0f}s eta~{eta:.0f}s",
                    )
                    # 与 torch 侧同两句（`engine.py` 的心跳）：`progress_head` 是那行的**头**
                    # ——它之前没人用（静默失效的形参），现在与 torch 逐字对应。
                    progress.beat(progress_head)
        if ckpt_path:
            save_ckpt(
                model, opt, ckpt_path, epochs_done=ep + 1, steps_done=steps_done, rng=ppo_rng
            )
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
    if steps_out is not None:
        # 正常收尾与「0 步」早返回都从这里出去（唯一口径）。
        steps_out["steps_done"] = int(steps_done)
    if not stats:
        log("[ppo-paddle] 0 grad steps — returning zero aggregate")
        return _zero_stats()
    agg = aggregate_stats(stats, list(stats[0].keys()))
    # torch 侧 `ppo_update` 总会带这个键（early-stop 门是 torch 侧专属；paddle 无该门 ⇒ 恒 False）。
    # 返回键集必须与 torch 同形，否则 parity harness 报 KeyError 而不是数值差异。
    agg["early_stopped"] = False
    return agg


update = ppo_update

__all__ = [
    "LR",
    "MAX_GRAD_NORM",
    "apply_value_anchor_pull",
    "approx_kl_est",
    "cat_entropy",
    "cat_logprob",
    "clip_grad_norm_",
    "masked_logsoftmax",
    "policy_new_rms_of",
    "ppo_update",
    "sync_scalars",
    "tensored_batch",
    "tensored_chunks",
    "update",
]
