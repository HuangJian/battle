"""
ppo_common.py — PPO 训练基础设施共享模块（工程化抽取，行为零变化）。

历史：ppo.py（per-tick RL）与 ppo_intent.py（意图步 semi-MDP RL）各自维护了一份
log / masked_logsoftmax / cat_logprob / cat_entropy / chunk_episodes / GAE /
checkpoint(RNG) / shard 发现与加载 / episode 骨架 样板。本模块把这些"逐字节相同或
仅参数化差异"的逻辑收拢为单一实现；两个训练器保留各自模块级公共名（re-export），
对外行为不变（trainer/run_rl.py / trainer/stream.py / 测试按原名字引用）。

统一点（与原实现逐字节等价）：
  * compute_gae(dt=None) = 原 ppo.compute_gae 定长路径；
    compute_gae(dt=数组) = 原 ppo_intent.compute_gae_variable 变步长路径
    （Δt≡1 时两路径逐字节一致，test_ppo_common.py 断言）。
  * load_episodes_common（2026-09-26 已搬到 ppo/np_core，此处再导出）同时覆盖
    ppo.load_episodes（只归一 adv）与 ppo_intent.load_episodes_intent（adv+ret 双归一）；
    日志前缀/措辞按原样参数化。
  * _ppo_save / _ppo_load / _pack_np_state / _unpack_np_state 原样搬移——
    ppo_intent 原内联的 checkpoint 段改为调用本实现（逐字节相同）。

2026-09-26（续扫「免 torch 半边」）：**XLA 设备 / 诊断助手**（xla_device / optimizer_step /
  xla_mark_step / _SPEED_PROBE / xla_fingerprint / xla_device_speed_probe / xla_world_size）
  也搬到 ppo/np_core——它们本就顶层零 torch（全部延迟 import），却与 torch 张量助手同住这里，
  逼得「测判据」的用例连坐 torch。这里再导出，既有调用点（engine / intent / goal / train_core）
  一行不改。
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np
import numpy.typing as npt
import torch
import torch.nn.functional as F

# 统一时间戳日志（与 ppo 旧 log 逐字节一致）。**再导出**：ppo.intent / ppo.engine /
# ppo.goal / train.goal_bc 都从本模块取 `log`（load_episodes_common 搬去 np_core 后，
# 本模块自身不再直接调用它 ⇒ 显式 `log as log` + noqa 标成公开再导出，勿当死代码删）。
from common.log import log as log

# 纯 numpy/stdlib 核心已拆到 ppo/np_core.py（2026-09-26：让业务逻辑用例不必拖 torch）。
# 这里再导出，既有 `from ppo.common import compute_gae / chunk_episodes / ...` 一行不改。
from worker.ppo.np_core import (  # noqa: F401  (re-export：既有调用点不变)
    _DUR_RE,
    _SPEED_PROBE,
    _UNIT_SEC,
    _XLA_CACHE_STATE,
    TPU_ATTR_KEYS,
    XLA_COUNTERS,
    XLA_TIME_METRICS,
    _pack_np_state,
    _parse_xla_duration,
    _RNGState,
    _unpack_np_state,
    chunk_episodes,
    compute_gae,
    discover_shards,
    is_xla,
    load_episodes_common,
    load_shard_fields,
    optimizer_step,
    tpu_backend_missing_reason,
    trim_shard_arrays,
    xla_delta_str,
    xla_device,
    xla_device_speed_probe,
    xla_enable_compile_cache,
    xla_fingerprint,
    xla_mark_step,
    xla_metrics_delta,
    xla_metrics_snapshot,
    xla_world_size,
)

# `cat_entropy` 的实现是**两个框架通用**的位置参数写法（torch 的 `dim=` / Paddle 的 `axis=`
# 只有位置形式两边都收）⇒ 单一实现住在 `worker.ppo.trainer`(L0，免框架)，这里再导出：
# Paddle 双生（`worker.ppo.engine_paddle`）从同一个地方取，不再各存一份。
from worker.ppo.trainer import cat_entropy as cat_entropy


# ---------------- 标量同步（N 次 device→host 同步收成 1 次） ----------------
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


# ---------------- policy helpers ----------------
def masked_logsoftmax(logits: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Masked log-softmax: 1 valid, 0 invalid → push invalid to -inf."""
    big = torch.tensor(1e9, device=logits.device, dtype=logits.dtype)
    m = mask.to(logits.dtype)
    return F.log_softmax(logits + (1.0 - m) * (-big), dim=-1)


def cat_logprob(action: torch.Tensor, logp: torch.Tensor) -> torch.Tensor:
    return logp.gather(1, action.unsqueeze(1)).squeeze(1)


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







# ---------------- 设备无关助手（TPU/XLA 接入，2026-09-10） ----------------
# 背景：GPU 配额耗尽后要拿 Kaggle TPU 的**独立** 20h/周配额接力（net +20h/周）。
# torch_xla 的语义与 CPU/CUDA 有两处硬差异，不显式处理会「静默不训练」或写坏 ckpt：
#   1. 优化器步进必须落在显式图执行边界（xm.optimizer_step）——裸 opt.step() 在
#      XRT/GSPMD 路径上会漏掉边界，梯度不回写参数；
#   2. state_dict() 里的参数/动量是 XLATensor，torch.save 会 pickle 出设备张量，
#      跨机 torch.load 还原即炸 —— 必须先物化到 CPU 再落盘。
# 下面两个助手在 CPU/CUDA 上是**恒等操作**：数值、行为、落盘张量全部不变

# ---------------- XLA 步耗诊断（2026-09-22） ----------------
# 背景：Kaggle v5e-8 上离线课程 PPO 单步 8~10s（同引擎在线课程 ~44ms/step，见 engine.py
# 的 mark_step 注释），而真机探针实测：**一次新编译 7.7s**、编译命中后单步执行仅 ~20-90ms。
# 也就是说「8s/步」最可能的解释是「每个 chunk 迭代都触发了一次新编译」——但这必须
# 由证据定案，不能靠墙钟猜。于是把 XLA 自己的账本摊开：每个 chunk 迭代前后各取一次
# metrics 快照，差分出「编译/执行/惰性追踪各占多少、命中缓存几次、新编译几次、图执行
# 几次」，连同本步的**图签名**（batch 形状 + 哪些可选分支开着）一起进日志。
# 纯观测：不碰 RNG、不改任何数值；非 XLA 机器（无 torch_xla）快照返空、调用点直接跳过。






def demo_index(buf: torch.Tensor | None, didx: npt.NDArray[np.int64]) -> Any:
    """demo 混 batch 的索引：有设备缓冲就**复用同一张量**（`copy_` 就地改值）。

    为什么必须复用（2026-09-22 真机定案）：XLA 下把 **host numpy 数组直接交给高级索引**
    （`bank[_didx]`）会让索引数据进不了「图输入」那条路——同一批 B/flags 下每步都是一张
    **新图** ⇒ 每步一次全图重编译。离线课程实录：单步 8~10s 而其中 2 次新编译 ≈11s；
    同一进程里编译命中的那一步只要 **0.31s**（168 步的整轮本该 ~1 分钟，实际 35 分钟）。
    微探针对照：每步新建 host 索引 → 连续两步各 `新=2`；复用设备张量 / 先物化 → `新=0`。

    数值逐位相同：抽哪些样本完全不取决于索引张量的来路（RNG 仍是调用方的
    `np.random.randint`，调用一次算一次）；`copy_` 走的是正常 host→device 传输。
    `buf=None`（非 demo 路径）⇒ 原样返回 numpy 索引，行为与接线前逐字节一致。
    """
    if buf is None:
        return didx
    buf.copy_(torch.from_numpy(didx))
    return buf




def _xla_fetch_cpu(tensors: list) -> list:
    """XLA 张量 → CPU 张量的**一次**批量取回（torch_xla 内部 API；不可用即抛，由调用方回退）。

    为什么存在：逐个 `.cpu()` 在 torch_xla 上是**逐次**设备同步。2026-10-29 云机实录
    `产物落盘 43.4s` = 168 个张量（model 42 + Adam 状态 42×3）× ~0.25s/次，而字节只有
    1MB —— 那 40 秒买的是「同步次数」，不是带宽/算力。
    """
    import torch_xla

    fn = getattr(getattr(torch_xla, "_XLAC", None), "_xla_get_cpu_tensors", None)
    if fn is None:
        raise RuntimeError("torch_xla 无 _XLAC._xla_get_cpu_tensors")
    # 一次图执行边界（同步只付一次；之后那次批量传输不再逐张量等设备）。
    xla_mark_step(tensors[0].device)
    return list(fn(list(tensors)))


def to_cpu_batch(tensors: list) -> list:
    """一串张量 → 一串 CPU 张量；XLA 那部分**一批一次** D2H，非 XLA 逐张量 `.detach().cpu()`。

    回退路径 = 改动前的行为（逐张量），**绝不抛**：这条路在训练收尾的关键路径上，
    宁可变慢也不能让一整轮白跑（批量 API 是 torch_xla 内部件，跨版本不保证）。
    """
    out: list = [None] * len(tensors)
    idx = []
    for i, t in enumerate(tensors):
        if torch.is_tensor(t) and is_xla(getattr(t, "device", None)):
            idx.append(i)
        else:
            out[i] = t.detach().cpu() if torch.is_tensor(t) else t
    if idx:
        ok = False
        try:
            got = _xla_fetch_cpu([tensors[i] for i in idx])
            if len(got) != len(idx):
                raise RuntimeError(f"批量取回数量不符：{len(got)} != {len(idx)}")
            for i, t in zip(idx, got, strict=True):
                out[i] = t.detach() if torch.is_tensor(t) else t
            ok = True
        except Exception:
            ok = False
        if not ok:  # 逐张量回退（今天的行为）
            for i in idx:
                out[i] = tensors[i].detach().cpu()
    return out


class HostModule(torch.nn.Module):
    """`Module` 的**只读主机视图**：`state_dict()` 是已批量物化到 CPU 的副本，其余原样转发。

    为什么不是 `module.to("cpu")`、也不是逐个 `p.data = cpu_t`（2026-10-29 云机现场）：
    torch_xla 的 Parameter 是 lazy tensor，给它的 `.data` 赋一个 **CPU** 张量会被 autograd 拒
    ——`RuntimeError: Attempted to call variable.set_data(tensor), but variable and tensor have
    incompatible tensor type`（`Module.to()` 能过是因为 torch_xla 给 `Module._apply` 打了补丁，
    而那是**逐张量**设备同步：本模型 42 个参数 = 42 次 × ~0.25s）。

    下游真正需要的只有 `state_dict()` / `arch()` / `parameters()` 三样（`save_weights_json` 与
    `_ppo_save` 的入参面）⇒ 给一个视图就够，**源模块一个字节都不动**（也就没有那条会炸的赋值）。
    继承 `Module` 只为让「写权重的入参是 Module」这条既有契约继续成立（零 `cast`/`Any`）。
    """

    def __init__(self, module: torch.nn.Module) -> None:
        super().__init__()
        self._src = module
        #: 一次批量 D2H（`to_cpu_batch`）；之后所有下游读的都是它。
        self._sd = _to_cpu_state(module.state_dict())

    def state_dict(self, *args, **kwargs):  # type: ignore[override]
        """已物化的 CPU 副本（`*args/**kwargs` 只为签名兼容：`_ppo_save` 会无参调用）。"""
        return self._sd

    def arch(self):
        """架构元信息（源模块没有就给空 dict——`save_weights_json` 的既有兜底口径）。"""
        return getattr(self._src, "arch", dict)()

    def parameters(self, recurse: bool = True):
        return self._src.parameters()


def _to_cpu_state(obj):
    """state_dict（可嵌套）→ 张量全部物化到 CPU 的副本，容器类型保持不变。

    CPU/CUDA 上 .detach().cpu() 是 no-op（同一 storage）⇒ torch.save 输出不变；
    XLA 上把 XLATensor 拉回主机，避免 pickle 设备张量导致跨机还原失败。
    容器类型必须保留（state_dict 是 OrderedDict，改成 dict 会改变 pickle 字节）。

    两遍走（2026-10-29）：先收集全部叶子、**一次**批量取回，再按原容器重建 ——
    逐张量递归时每个叶子各付一次设备同步，Adam 状态（126 个张量）就是 126 次。
    """
    leaves: list = []

    def _collect(o):
        if isinstance(o, dict):
            for v in o.values():
                _collect(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                _collect(v)
        elif torch.is_tensor(o):
            leaves.append(o)

    _collect(obj)
    if not leaves:
        return obj
    cpus = iter(to_cpu_batch(leaves))

    def _build(o):
        if isinstance(o, dict):
            out = o.__class__()
            for k, v in o.items():
                out[k] = _build(v)
            return out
        if isinstance(o, (list, tuple)):
            return o.__class__(_build(v) for v in o)
        if torch.is_tensor(o):
            return next(cpus)
        return o

    return _build(obj)


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
