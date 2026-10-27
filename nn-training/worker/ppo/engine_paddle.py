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

from common.schema import FIRE3_DIM, FIRE_DIM, MOVE4_DIM, MOVE_DIM
from worker.ppo.checkpoint_paddle import load_ckpt, save_ckpt
from worker.ppo.trainer import aggregate_stats, cat_entropy

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
    per = F.cross_entropy(z[keep], target.astype("int64")[keep], reduction="none")
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
    demo_bank: dict | None = None,
    demo_bc_coef: float = 0.0,
    demo_per_mb: int = 0,
    progress: Any = None,
    progress_head: str = "[ppo] PPO",
    value_anchor: float = 0.0,
    #: 引擎内部步数的**唯一出口**（`{"steps_done": int}`）：调用方不许拿
    #: `epochs × len(chunks)` 自己重算 —— 引擎一旦跳 chunk / 从 ckpt 中途起步，重算就会静默
    #: 漂移，而那个数要写进 `state.json` 与回传的 opt `step`（bias correction 的时基）。
    steps_out: dict[str, int] | None = None,
    log=lambda _msg: None,
):
    """Paddle PPO update. Same math as `worker.ppo.engine.ppo_update`; see module doc.

    `ckpt_path` 语义与 torch 侧一致：开跑前载入（model/opt/epochs_done/RNG），
    每个 epoch 尾落盘一次；torch 与 Paddle 写出的 checkpoint 互相可读。
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
    tensored = tensored_chunks(chunks)
    total_steps = len(tensored) * epochs
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
    # kickstart ref 前向每 chunk 只算一次（与 torch 侧 ref_cache 同语义；数值相同）。
    ref_cache: list[tuple[paddle.Tensor, ...] | None] = [None] * len(tensored)
    if ref_model is not None and kickstart_kl > 0:
        with paddle.no_grad():
            for _i, _e in enumerate(tensored):
                _ro = ref_model(_e["obs"], _e["scalars"], _e.get("extra"))
                _m = _e["mask"]
                _ref_v6 = len(_ro) >= 4
                _data_v6 = _e.get("a_hold") is not None
                if _ref_v6 != _data_v6:
                    raise ValueError(
                        f"[ppo-paddle] kickstart ref 与数据动作空间不同源：ref 返回 {len(_ro)} 个头"
                        f"（{'v6 4/3/2' if _ref_v6 else 'legacy 5/2'}），chunk "
                        f"{'带' if _data_v6 else '不带'} a_hold"
                    )
                if _ref_v6:
                    ref_cache[_i] = (
                        masked_logsoftmax(_ro[0], _m[:, :MOVE4_DIM]),
                        masked_logsoftmax(_ro[1], _m[:, MOVE4_DIM : MOVE4_DIM + FIRE3_DIM]),
                        masked_logsoftmax(_ro[2], _m[:, MOVE4_DIM + FIRE3_DIM :]),
                    )
                else:
                    ref_cache[_i] = (
                        masked_logsoftmax(_ro[0], _m[:, :MOVE_DIM]),
                        masked_logsoftmax(_ro[1], _m[:, MOVE_DIM : MOVE_DIM + FIRE_DIM]),
                    )
    t0 = time.time()
    last_hb = t0
    start_epoch, steps_done = load_ckpt(model, opt, ckpt_path)
    if start_epoch:
        log(
            f"[ppo-paddle] resume PPO from checkpoint: epoch {start_epoch}/{epochs} done "
            f"(continuing remaining {epochs - start_epoch}, steps_done={steps_done})"
        )
    for ep in range(start_epoch, epochs):
        perm = np.random.permutation(len(tensored))
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
            mask = e["mask"]

            out = model(obs, sc, e.get("extra"))
            if e.get("a_hold") is not None:
                mv, fr, hd, val = out
            else:
                mv, fr, val = out
                hd = None
            if hd is not None:
                move_logp = masked_logsoftmax(mv, mask[:, :MOVE4_DIM])
                fire_logp = masked_logsoftmax(fr, mask[:, MOVE4_DIM : MOVE4_DIM + FIRE3_DIM])
                hold_logp = masked_logsoftmax(hd, mask[:, MOVE4_DIM + FIRE3_DIM :])
            else:
                move_logp = masked_logsoftmax(mv, mask[:, :MOVE_DIM])
                fire_logp = masked_logsoftmax(fr, mask[:, MOVE_DIM : MOVE_DIM + FIRE_DIM])
                hold_logp = None

            lp_new = cat_logprob(a_move, move_logp) + cat_logprob(a_fire, fire_logp)
            lp_old = lp_move + lp_fire
            if hold_logp is not None:
                lp_new = lp_new + cat_logprob(e["a_hold"], hold_logp)
                lp_old = lp_old + e["lp_hold"]

            ratio = paddle.exp(lp_new - lp_old)
            surr1 = ratio * adv
            surr2 = paddle.clip(ratio, 1.0 - clip, 1.0 + clip) * adv
            policy_loss = -paddle.minimum(surr1, surr2).mean()

            value_loss = F.mse_loss(val.squeeze(-1), ret)
            entropy = cat_entropy(move_logp) + cat_entropy(fire_logp)
            if hold_logp is not None:
                entropy = entropy + cat_entropy(hold_logp)

            loss = policy_loss + VF_COEF * value_loss - ent_c * entropy
            if kl_coef > 0.0:
                loss = loss + kl_coef * ((ratio - 1.0) - (lp_new - lp_old)).mean()
            kick_mean = _zero_scalar()
            _ref = ref_cache[int(i)]
            if _ref is not None:
                kl_m = (move_logp.exp() * (move_logp - _ref[0])).sum(axis=-1)
                kl_f = (fire_logp.exp() * (fire_logp - _ref[1])).sum(axis=-1)
                kl_parts = kl_m + kl_f
                if len(_ref) > 2 and hold_logp is not None:
                    kl_parts = kl_parts + (hold_logp.exp() * (hold_logp - _ref[2])).sum(axis=-1)
                kick_mean = kl_parts.mean()
                loss = loss + kickstart_kl * kick_mean
            demo_bc_mean = _zero_scalar()
            if demo_on:
                _didx = np.random.randint(0, demo_n, size=int(demo_per_mb))
                _didx_t = paddle.to_tensor(_didx.astype(np.int64))
                _dm = model(
                    demo_t["obs"][_didx_t],
                    demo_t["scalars"][_didx_t],
                    demo_t["extra"][_didx_t] if "extra" in demo_t else None,
                )[0]
                _dmm = demo_t["masks"][_didx_t]
                _dact = demo_t["actions"][_didx_t]
                demo_bc_mean = _demo_masked_ce(_dm, _dact[:, 0], _dmm[:, :MOVE_DIM])
                loss = loss + float(demo_bc_coef) * demo_bc_mean

            opt.clear_grad()
            loss.backward()
            gn = clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
            opt.step()
            steps_done += 1
            value_new_rms = apply_value_anchor_pull(model, value_anchor)
            policy_new_rms = policy_new_rms_of(model)

            stats.append(
                sync_scalars(
                    {
                        "policy": policy_loss,
                        "value": value_loss,
                        "entropy": entropy,
                        "kl": approx_kl_est(lp_old, lp_new),
                        "kickstart": kick_mean,
                        "demo_bc": demo_bc_mean,
                        "mean_ret": ret.mean(),
                        "mean_adv": adv.mean(),
                        "gnorm": gn,
                        "value_new_rms": value_new_rms,
                        "policy_new_rms": policy_new_rms,
                    }
                )
            )
            if progress is not None:
                now = time.time()
                if now - last_hb >= HB_SEC:
                    last_hb = now
                    done = ep * len(tensored) + j + 1
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
            save_ckpt(model, opt, ckpt_path, epochs_done=ep + 1, steps_done=steps_done)
        if on_epoch_done is not None:
            on_epoch_done(ep + 1, model)
        _n = 32
        recent = stats[-_n:]
        if recent:
            log(
                f"[ppo-paddle] ep {ep + 1}/{epochs} "
                f"kl={sum(s['kl'] for s in recent) / len(recent):.4f} "
                f"entropy={sum(s['entropy'] for s in recent) / len(recent):.4f} "
                f"policy={sum(s['policy'] for s in recent) / len(recent):.4f} "
                f"value={sum(s['value'] for s in recent) / len(recent):.4f} "
                f"gnorm={sum(s['gnorm'] for s in recent) / len(recent):.3f}"
            )
    if steps_out is not None:
        # 正常收尾与「0 步」早返回都从这里出去（唯一口径）。
        steps_out["steps_done"] = int(steps_done)
    if not stats:
        log("[ppo-paddle] 0 grad steps — returning zero aggregate")
        return {
            k: 0.0
            for k in (
                "policy",
                "value",
                "entropy",
                "kl",
                "kickstart",
                "demo_bc",
                "mean_ret",
                "mean_adv",
                "gnorm",
                "value_new_rms",
                "policy_new_rms",
            )
        }
    return aggregate_stats(stats, list(stats[0].keys()))


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
    "tensored_chunks",
    "update",
]
