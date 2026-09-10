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
from typing import cast

import numpy as np
import numpy.typing as npt
import torch
import torch.nn.functional as F

from rl.log import log  # 统一时间戳日志（与 ppo 旧 log 逐字节一致）

# numpy 的 RandomState.get_state() 存根把 legacy 默认标成 False（→ dict），但 numpy 2.x
# 实测返回的是 legacy 元组。显式传 legacy=True 并断言元组形态，双层保险。
_RNGState = tuple[str, np.ndarray, int, int, float]


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


def approx_kl_est(lp_old: torch.Tensor, lp_new: torch.Tensor) -> torch.Tensor:
    """PPO 策略漂移估计——Schulman et al. 2017 的无偏下界估计量。

    KL(π_old‖π_new) ≈ E[(r − 1) − ln r]，r = exp(lp_new − lp_old)。
    对 π_old 期望下 r−1−ln r ≥ 0，是 KL 的无偏下界，且对任意漂移幅度稳健
    （大漂移时 r−1−ln r 仍保持正确的单调关系，不像二阶近似那样失真）。

    历史（plan/python-refactor.md P0-3，2026-09-02 修复）：旧实现
    `((lp_old - lp_new) ** 2).mean()` 是二阶近似 ½·E[(Δlnπ)²] 的 **2 倍**——
    三处一致高估，而所有阈值（TARGET_KL / breaker KL_WARN / KL_BREAK /
    streamKlCap / rl-config kl_break）都是照这个有偏口径标定的（内部自洽、
    永不报错，但语义漂移：early stopping 实际在真实 KL≈½ 阈值处触发，且与
    文献经验不可比）。本次修复**估计量 × 阈值 同步换算**，行为等价：
      TARGET_KL       0.04 → 0.02
      breaker KL_WARN 0.08 → 0.04
      breaker KL_BREAK 0.15 → 0.075
      streamKlCap     0.20 → 0.10（config）
      intent kl_break 0.6  → 0.30（config）
    """
    ratio = torch.exp(lp_new - lp_old)
    return ((ratio - 1.0) - (lp_new - lp_old)).mean()


# ---------------- GAE ----------------
def compute_gae(
    rewards,
    values,
    dones,
    gamma: float,
    lam: float,
    dt: np.ndarray | Sequence[int] | None = None,
):
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


def load_shard_fields(
    dirpath: str, spec: dict[str, tuple[str, npt.DTypeLike]]
) -> dict[str, np.ndarray]:
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
    s = cast(_RNGState, np.random.get_state(legacy=True))
    return [s[0], s[1].tolist(), s[2], s[3], s[4]]


def _unpack_np_state(packed: list) -> None:
    np.random.set_state(
        (packed[0], np.asarray(packed[1], dtype=np.uint32), packed[2], packed[3], packed[4])
    )


# ---------------- 设备无关助手（TPU/XLA 接入，2026-09-10） ----------------
# 背景：GPU 配额耗尽后要拿 Kaggle TPU 的**独立** 20h/周配额接力（net +20h/周）。
# torch_xla 的语义与 CPU/CUDA 有两处硬差异，不显式处理会「静默不训练」或写坏 ckpt：
#   1. 优化器步进必须落在显式图执行边界（xm.optimizer_step）——裸 opt.step() 在
#      XRT/GSPMD 路径上会漏掉边界，梯度不回写参数；
#   2. state_dict() 里的参数/动量是 XLATensor，torch.save 会 pickle 出设备张量，
#      跨机 torch.load 还原即炸 —— 必须先物化到 CPU 再落盘。
# 下面两个助手在 CPU/CUDA 上是**恒等操作**：数值、行为、落盘张量全部不变
# （AGENTS §2.3 确定性承诺不受影响）。XLA 相关 import 全部延迟到调用点，
# 保证「未装 torch_xla 的机器」行为与今日逐字节相同。


def is_xla(device) -> bool:
    """device 是否为 XLA/TPU（torch.device('xla') 或 xm.xla_device()）。"""
    return getattr(device, "type", None) == "xla"


def sync_scalars(values: dict[str, torch.Tensor]) -> dict[str, float]:
    """把一组 device 标量张量用**一次**同步搬回主机（N 次 ``.item()`` → 1 次）。

    为什么（2026-09-10）：三后端每个梯度步各有 6-8 处 ``.item()``/``float()``。CPU 上
    近乎免费（数据已在主机内存，实测同步税仅 +4 ms/step），但 **CUDA 上每一次都是全设备
    同步** —— 强制 drain 尚未执行的 kernel 队列，把 CPU 与 GPU 的流水线彻底串行化。
    per-tick 每轮 148 个梯度步 × 8 次 = **1184 次强制同步/轮**；GPU 侧实测利用率仅
    ~3%（236 GFLOP/s vs T4 fp32 峰值 8.1 TFLOPS），同步串行化是首要嫌疑，而 CPU 基准
    对这个开销**完全失明**（这正是它必须按设备分别实测的原因）。

    数值逐位不变：``torch.stack(...).tolist()`` 只 materialize 一次，每个元素与逐项
    ``float(t.item())`` 返回**同一个 Python float**（float32 → double 无损；混合 dtype
    由 torch 提升到公共 dtype，不会截断）。

    入参张量会被 ``reshape(())`` 规整为标量；请只传 0 维或单元素张量。
    """
    if not values:
        return {}
    keys = list(values)
    stacked = torch.stack([values[k].detach().reshape(()) for k in keys]).tolist()
    return dict(zip(keys, stacked, strict=True))


def optimizer_step(opt, device) -> None:
    """设备感知的优化器步进：XLA 走 xm.optimizer_step，其余 == 裸 opt.step()。"""
    if is_xla(device):
        import torch_xla.core.xla_model as xm

        xm.optimizer_step(opt)
    else:
        opt.step()


def xla_mark_step(device) -> None:
    """XLA 图执行边界（非 XLA 设备为 no-op）——保证 host 侧读到的权重是最新值。"""
    if is_xla(device):
        import torch_xla.core.xla_model as xm

        xm.mark_step()


def _to_cpu_state(obj):
    """state_dict（可嵌套）→ 张量全部物化到 CPU 的副本，容器类型保持不变。

    CPU/CUDA 上 .detach().cpu() 是 no-op（同一 storage）⇒ torch.save 输出不变；
    XLA 上把 XLATensor 拉回主机，避免 pickle 设备张量导致跨机还原失败。
    容器类型必须保留（state_dict 是 OrderedDict，改成 dict 会改变 pickle 字节）。
    """
    if isinstance(obj, dict):
        out = obj.__class__()
        for k, v in obj.items():
            out[k] = _to_cpu_state(v)
        return out
    if isinstance(obj, (list, tuple)):
        return obj.__class__(_to_cpu_state(v) for v in obj)
    if torch.is_tensor(obj):
        return obj.detach().cpu()
    return obj


def _ppo_save(ckpt_path: str, model, opt, epochs_done: int) -> None:
    """epoch 粒度 checkpoint：model+optimizer 状态 + 已完成 epoch 数 + numpy RNG。
    恢复粒度 = 一个 epoch（从最近 checkpoint 续，重跑该 epoch 的梯度步，秒级）。"""
    os.makedirs(ckpt_path, exist_ok=True)
    torch.save(_to_cpu_state(model.state_dict()), os.path.join(ckpt_path, "model.pt"))
    torch.save(_to_cpu_state(opt.state_dict()), os.path.join(ckpt_path, "opt.pt"))
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
def chunk_episodes(episodes: list[dict], mb: int, shuffle: bool = True) -> list[dict]:
    """Split per-episode dicts into fixed-size minibatch chunks (last chunk ragged).

    GAE is computed per-episode BEFORE chunking; chunks are only an update-
    granularity unit (bounds activation memory, adds gradient steps).

    shuffle（P1-6，2026-09-02 默认开启）：旧实现按 episode 顺序连续切片——每个
    minibatch 是同一局内 mb 个**相邻**决策步，帧间强相关，SGD 的 i.i.d. 假设被
    严重违反（梯度方差大 → KL spikes → 熔断/早停；H1 假设的主因）。默认改为
    **全局 transition 级重排**：展平全部 episodes → 单次 permutation → 按 mb 切片。
    GAE/adv/ret 是逐 transition 存储的，重排不改变任何数学（on-policy 正确性
    不受影响）；RNG 用全局 np.random（由 main 播种，可复现）。
    shuffle=False 保留旧行为（逐字节一致，供对照实验）。
    """
    if not shuffle or len(episodes) <= 1:
        out: list[dict] = []
        for e in episodes:
            n = e["obs"].shape[0]
            for s in range(0, n, mb):
                out.append({k: v[s : s + mb] for k, v in e.items()})
        return out
    keys = list(episodes[0].keys())
    flat = {k: np.concatenate([e[k] for e in episodes], axis=0) for k in keys}
    n = flat["obs"].shape[0]
    idx = np.random.permutation(n)
    return [{k: v[idx[s : s + mb]] for k, v in flat.items()} for s in range(0, n, mb)]


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
    normalize_adv: bool = True,
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
    # P1-7（2026-09-02）：adv 归一化粒度参数化（normalize_adv=False 供
    # --adv-norm none 对照实验；默认 True 保持全局归一现状）。
    if normalize_adv:
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
