"""
ppo_common.py — PPO 训练基础设施共享模块（工程化抽取，行为零变化）。

历史：ppo.py（per-tick RL）与 ppo_intent.py（意图步 semi-MDP RL）各自维护了一份
log / masked_logsoftmax / cat_logprob / cat_entropy / chunk_episodes / GAE /
checkpoint(RNG) / shard 发现与加载 / episode 骨架 样板。本模块把这些"逐字节相同或
仅参数化差异"的逻辑收拢为单一实现；两个训练器保留各自模块级公共名（re-export），
对外行为不变（run_rl.py / rl/stream.py / 测试按原名字引用）。

统一点（与原实现逐字节等价）：
  * compute_gae(dt=None) = 原 ppo.compute_gae 定长路径；
    compute_gae(dt=数组) = 原 ppo_intent.compute_gae_variable 变步长路径
    （Δt≡1 时两路径逐字节一致，test_ppo_common.py 断言）。
  * load_episodes_common 同时覆盖 ppo.load_episodes（只归一 adv）与
    ppo_intent.load_episodes_intent（adv+ret 双归一）；日志前缀/措辞按原样参数化。
  * _ppo_save / _ppo_load / _pack_np_state / _unpack_np_state 原样搬移——
    ppo_intent 原内联的 checkpoint 段改为调用本实现（逐字节相同）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence

import numpy as np
import torch
import torch.nn.functional as F

from rl.log import log  # 统一时间戳日志（与 ppo 旧 log 逐字节一致）


# ---------------- policy helpers ----------------
def masked_logsoftmax(logits: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Masked log-softmax: 1 valid, 0 invalid → push invalid to -inf."""
    big = torch.tensor(1e9, device=logits.device, dtype=logits.dtype)
    m = mask.to(logits.dtype)
    return F.log_softmax(logits + (1.0 - m) * (-big), dim=-1)


def cat_logprob(action: torch.Tensor, logp: torch.Tensor) -> torch.Tensor:
    return logp.gather(1, action.unsqueeze(1)).squeeze(1)


def cat_entropy(logp: torch.Tensor) -> torch.Tensor:
    return -(logp.exp() * logp).sum(dim=-1).mean()


# ---------------- GAE ----------------
def compute_gae(rewards, values, dones, gamma: float, lam: float, dt: Sequence[int] | None = None):
    """Per-episode GAE. rewards[t]=r_{t+1}, values[t]=V(s_t).

    dt=None — 定长 per-tick 折扣（ppo.py 原路径，逐字节一致）。
    dt 给定 — 变步长折扣 γ_step = γ^Δt（ppo_intent.py 原路径；Δt≡1 时退化为定长）。
    """
    N = len(rewards)
    adv = np.zeros(N, dtype=np.float32)
    last = 0.0
    for t in reversed(range(N)):
        non_term = 1.0 - float(dones[t])
        gamma_step = float(gamma) if dt is None else float(gamma ** float(dt[t]))
        next_value = 0.0 if t + 1 >= N else float(values[t + 1])
        delta = float(rewards[t]) + gamma_step * next_value - float(values[t])
        last = delta + gamma_step * lam * non_term * last
        adv[t] = last
    ret = adv + np.asarray(values, dtype=np.float32)
    return adv, ret


# ---------------- shard discovery / loading ----------------
def discover_shards(root: str, need: Sequence[str]) -> list[str]:
    """Find all dirs under `root` containing every marker file in `need`."""
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        if all(f in files for f in need):
            out.append(dirpath)
    return sorted(out)


def load_shard_fields(dirpath: str, spec: dict[str, tuple[str, np.dtype]]) -> dict[str, np.ndarray]:
    """Load a shard by field spec {key: (filename, dtype)} with zero-copy astype.

    `dtype` 用 np.uint8/np.float32/np.int64；astype(..., copy=False) 在 dtype 已
    符合时零拷贝（obs 每轮 ~550MB，无谓拷贝纯烧内存带宽）。
    """
    out: dict[str, np.ndarray] = {}
    for key, (fname, dtype) in spec.items():
        out[key] = np.load(os.path.join(dirpath, fname)).astype(dtype, copy=False)
    return out


# ---------------- checkpoint (epoch-granularity, RNG-preserving) ----------------
def _pack_np_state() -> list:
    """numpy MT19937 全局状态 → JSON 可序列化（续跑需精确重建 epoch 乱序）。"""
    s = np.random.get_state()
    return [s[0], s[1].tolist(), s[2], s[3], s[4]]


def _unpack_np_state(packed: list) -> None:
    np.random.set_state(
        (packed[0], np.asarray(packed[1], dtype=np.uint32), packed[2], packed[3], packed[4])
    )


def _ppo_save(ckpt_path: str, model, opt, epochs_done: int) -> None:
    """epoch 粒度 checkpoint：model+optimizer 状态 + 已完成 epoch 数 + numpy RNG。
    恢复粒度 = 一个 epoch（从最近 checkpoint 续，重跑该 epoch 的梯度步，秒级）。"""
    os.makedirs(ckpt_path, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(ckpt_path, "model.pt"))
    torch.save(opt.state_dict(), os.path.join(ckpt_path, "opt.pt"))
    with open(os.path.join(ckpt_path, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"epochs_done": epochs_done, "rng": _pack_np_state()}, f)


def _ppo_load(ckpt_path: str | None, model, opt) -> int:
    """返回已完成 epoch 数（0=无 checkpoint / 无法加载）。加载 model/opt + 恢复 RNG。"""
    if not ckpt_path:
        return 0
    sp = os.path.join(ckpt_path, "state.json")
    mp = os.path.join(ckpt_path, "model.pt")
    op = os.path.join(ckpt_path, "opt.pt")
    if not all(os.path.exists(p) for p in (sp, mp, op)):
        return 0
    with open(sp, encoding="utf-8") as f:
        st = json.load(f)
    model.load_state_dict(torch.load(mp, map_location="cpu"))
    opt.load_state_dict(torch.load(op, map_location="cpu"))
    _unpack_np_state(st["rng"])
    return int(st.get("epochs_done", 0))


# ---------------- minibatch chunking ----------------
def chunk_episodes(episodes: list[dict], mb: int) -> list[dict]:
    """Split per-episode dicts into fixed-size minibatch chunks (last chunk ragged).

    GAE is computed per-episode BEFORE chunking; chunks are only an update-
    granularity unit (bounds activation memory, adds gradient steps).
    """
    out: list[dict] = []
    for e in episodes:
        n = e["obs"].shape[0]
        for s in range(0, n, mb):
            out.append({k: v[s : s + mb] for k, v in e.items()})
    return out


# ---------------- episode loading skeleton (ppo / ppo_intent 共用) ----------------
def load_episodes_common(
    data_root: str,
    *,
    label: str,
    shard_kind: str,
    need_files: Sequence[str],
    shard_loader: Callable[[str], dict[str, np.ndarray]],
    gae: Callable[[dict[str, np.ndarray]], tuple[np.ndarray, np.ndarray]],
    gae_name: str,
    normalize_ret: bool,
    load_log_every: int = 128,
) -> list[dict]:
    """Discover shards → per-shard GAE → global normalize → episode dicts.

    与原 ppo.load_episodes / ppo_intent.load_episodes_intent 逐字节等价：
      * 日志前缀/措辞经 label / shard_kind / gae_name 参数化保持原样；
      * episode 字段 = shard 去掉 (reward, done) + adv/ret（两处原手写字段集一致）；
      * adv 全局归一（mean 0 / std 1）；normalize_ret=True 时 ret 同步归一
        （intent 的 value 头目标，ppo 不归）。
    """
    shards = discover_shards(data_root, need_files)
    if not shards:
        raise SystemExit(f"[{label}] no {shard_kind} shards found under {data_root}")
    log(f"[{label}] loaded {len(shards)} {shard_kind} shards from {data_root}")

    episodes: list[dict] = []
    t_load = time.time()
    for k, sd in enumerate(shards):
        if k > 0 and k % load_log_every == 0:
            log(f"[{label}] loading shards {k}/{len(shards)} ({time.time() - t_load:.0f}s)")
        d = shard_loader(sd)
        N = d["obs"].shape[0]
        if N == 0:
            continue
        adv, ret = gae(d)
        episode = {k: v for k, v in d.items() if k not in ("reward", "done")}
        episode["adv"] = adv.astype(np.float32)
        episode["ret"] = ret.astype(np.float32)
        episodes.append(episode)

    log(
        f"[{label}] shard IO + {gae_name} done for {len(episodes)} episodes "
        f"({time.time() - t_load:.0f}s)"
    )
    all_adv = np.concatenate([e["adv"] for e in episodes])
    amean, astd = all_adv.mean(), all_adv.std() + 1e-8
    for e in episodes:
        e["adv"] = (e["adv"] - amean) / astd
    if normalize_ret:
        all_ret = np.concatenate([e["ret"] for e in episodes])
        rmean, rstd = all_ret.mean(), all_ret.std() + 1e-8
        for e in episodes:
            e["ret"] = (e["ret"] - rmean) / rstd
    return episodes
