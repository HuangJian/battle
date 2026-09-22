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
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

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


def xla_device():
    """取 XLA 设备句柄。优先 `torch_xla.device()`（2.5+ 推荐），旧版回退 `xm.xla_device()`。

    2026-09-10 实测：Kaggle TPU 镜像的 torch_xla 会给 `xm.xla_device()` 发
    DeprecationWarning（"Use torch_xla.device instead"）。两条都保留是为了跨版本可用。
    """
    import torch_xla
    import torch_xla.core.xla_model as xm

    dev_fn = getattr(torch_xla, "device", None)
    if callable(dev_fn):
        return dev_fn()
    return xm.xla_device()


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
        import torch_xla
        import torch_xla.core.xla_model as xm

        # 2.5+ 把 mark_step 改名为 sync()；两个都探，兼容旧版。
        for _name in ("sync", "mark_step"):
            _fn = getattr(torch_xla, _name, None)
            if callable(_fn):
                _fn()
                return
        xm.mark_step()


#: TPU 后端在设备属性里留下的指纹（CPU 后端的 XLA 设备没有这两个键）。
TPU_ATTR_KEYS = ("coords", "core_on_chip")

#: 设备自检的缓存（同一进程只测一次——它含一次 XLA 编译，按 job 跑会白付 300 次）。
_SPEED_PROBE: dict[str, float] = {}


def xla_fingerprint(device: object = None) -> dict:
    """XLA 运行时的**后端指纹**（诊断与护栏用，2026-09-22）。

    为什么需要：`xla_device()` 在 **CPU 后端**上照样返回 `xla:0`（XLA 的 CPU 插件是合法后端），
    于是「`--device tpu`」可能整段跑在 CPU 上，而日志里看不出任何异常——2026-09-22 Kaggle
    TPU 实例上离线课程 PPO 单步 8~9s（本地 CPU 基准 ~4.7s/step、TPU 参考 ~44ms）就是这个嫌疑。

    **别拿 `world_size()` 当判据**：torch_xla 源码里无复制时它恒为 1（同一次实测：
    `world_size=1` 而 `global_device_count=8`、`device_type=TPU`、matmul 快 12×）。

    返回（读不到的项记 None，**绝不抛**——诊断不能反过来把训练搞挂）：
      device_type              —— `torch_xla.runtime.device_type()`（= PJRT_DEVICE 的设备名部分）
      global_device_count      —— XLA 运行时看到的设备总数（TPU v5e-8 = 8；CPU 后端 = 1）
      addressable_device_count —— 本进程可见设备数
      replication_devices      —— `_xla_get_replication_devices_count()`（0 = 无复制）
      world_size               —— 仅记录（见上，**不是** TPU 判据）
      attrs                    —— 设备属性原文（TPU 有 `coords`/`core_on_chip`）
    """
    out: dict = {
        "device_type": None,
        "global_device_count": None,
        "addressable_device_count": None,
        "replication_devices": None,
        "world_size": None,
        "attrs": None,
    }
    try:
        import torch_xla
        import torch_xla.runtime as xr
    except Exception:
        return out
    for key, fn in (
        ("device_type", lambda: xr.device_type()),
        ("global_device_count", lambda: xr.global_device_count()),
        ("addressable_device_count", lambda: xr.addressable_device_count()),
        ("world_size", lambda: xr.world_size()),
    ):
        try:
            out[key] = fn()
        except Exception:
            pass
    try:
        out["replication_devices"] = int(
            torch_xla._XLAC._xla_get_replication_devices_count()
        )
    except Exception:
        pass
    try:
        dev = str(device) if device is not None else str(xla_device())
        out["attrs"] = str(xr.runtime_device_attributes(dev))
    except Exception:
        pass
    return out


def tpu_backend_missing_reason(fp: dict) -> str:
    """指纹 → 「这**不是** TPU 后端」的原因；空串 = 看着就是 TPU（或读不到、无法证伪）。

    判据两条，都不依赖 `world_size`：
      ① `device_type != "TPU"`（PJRT_DEVICE 选的就不是 TPU）；
      ② 设备属性里没有 `coords`/`core_on_chip`（XLA 的 CPU 插件没有这两个键）。
    属性读不到（None）时**不**判负——宁可放过也不能把能跑的 TPU job 拦死。
    """
    dt = fp.get("device_type")
    if dt is not None and str(dt).upper() != "TPU":
        return f"device_type={dt}（PJRT_DEVICE 选的是 {dt}，不是 TPU）"
    attrs = fp.get("attrs")
    if attrs and not any(k in str(attrs) for k in TPU_ATTR_KEYS):
        return f"设备属性里没有 TPU 指纹（attrs={attrs}）"
    return ""


def xla_device_speed_probe(device: object, *, n: int = 2048) -> float | None:
    """一次 `n×n` matmul 的墙钟（秒）——**同一进程只测一次**，失败返回 None。

    为什么它值得：TPU 与 CPU 的 XLA 后端在这一项上差一个数量级（Kaggle v5e-8 实测
    **1.5ms vs 17.9ms**，快 12×）⇒ 它能把「日志说 TPU、实际跑 CPU」的静默降级照出来
    （`xla_device()` 两种后端都返回 `xla:0`，指纹之外只剩速度能区分）。
    """
    key = str(device)
    if key in _SPEED_PROBE:
        return _SPEED_PROBE[key]
    try:
        import torch

        # device 是 XLA 设备对象（torch 的 device 形参类型桩不认它）——显式 cast 说明意图。
        dev = cast(Any, device if device is not None else xla_device())
        a = torch.randn(n, n, device=dev)
        b = torch.randn(n, n, device=dev)
        t0 = time.perf_counter()
        (a @ b).cpu()
        xla_mark_step(dev)
        sec = time.perf_counter() - t0
    except Exception:
        return None
    _SPEED_PROBE[key] = sec
    return sec


def xla_world_size() -> int | None:
    """TPU 核数（诊断/日志用，2026-09-11）。新版 torch_xla 挪到
    torch_xla.runtime.world_size()；旧版 xm.xrt_world_size()。非 TPU 或读不到
    返回 None（延迟 import，未装 torch_xla 的机器行为不变）。

    ⚠ 2026-09-22 更正：torch_xla 的 `runtime.world_size()` 在**无复制时恒为 1**
    （源码：`_xla_get_replication_devices_count() == 0` ⇒ 1），所以这个数**不能**当
    TPU 判据（同一次实测 world_size=1 而 global_device_count=8、device_type=TPU）。
    要判后端用 `xla_fingerprint()`。
    """
    try:
        import torch_xla.runtime as xr

        return int(xr.world_size())
    except Exception:
        try:
            import torch_xla.core.xla_model as xm

            return int(xm.xrt_world_size())
        except Exception:
            return None


# ---------------- XLA 步耗诊断（2026-09-22） ----------------
# 背景：Kaggle v5e-8 上离线课程 PPO 单步 8~10s（同引擎在线课程 ~44ms/step，见 engine.py
# 的 mark_step 注释），而真机探针实测：**一次新编译 7.7s**、编译命中后单步执行仅 ~20-90ms。
# 也就是说「8s/步」最可能的解释是「每个 chunk 迭代都触发了一次新编译」——但这必须
# 由证据定案，不能靠墙钟猜。于是把 XLA 自己的账本摊开：每个 chunk 迭代前后各取一次
# metrics 快照，差分出「编译/执行/惰性追踪各占多少、命中缓存几次、新编译几次、图执行
# 几次」，连同本步的**图签名**（batch 形状 + 哪些可选分支开着）一起进日志。
# 纯观测：不碰 RNG、不改任何数值；非 XLA 机器（无 torch_xla）快照返空、调用点直接跳过。

_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|us|ns|h|m|s)")
_UNIT_SEC = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}

#: 关心的 XLA 累积耗时指标（秒）与计数器（次数）。
XLA_TIME_METRICS = (
    "CompileTime",
    "ExecuteTime",
    "LazyTracing",
    "TransferToDeviceTime",
    "TransferFromDeviceTime",
)
XLA_COUNTERS = ("UncachedCompile", "CachedCompile", "ExecuteComputation", "MarkStep")


def _parse_xla_duration(text: str) -> float:
    """XLA 的 `07s703ms868.692us` 形态 → 秒（`TotalSamples: 42`/`1%=` 里的数字不会被误读）。"""
    return sum(float(v) * _UNIT_SEC[u] for v, u in _DUR_RE.findall(text))


def xla_metrics_snapshot() -> dict[str, float]:
    """XLA 累积指标快照：`t.<Metric>` = 秒、`c.<Counter>` = 次数；非 XLA → {}。

    读 `torch_xla.debug.metrics.metrics_report()`（C++ 侧文本，单次毫秒级）。任何异常
    都吞掉并返回已读到的部分——诊断绝不能反过来把训练搞挂。
    """
    try:
        import torch_xla.debug.metrics as met

        report = met.metrics_report()
    except Exception:
        return {}
    out: dict[str, float] = {}
    # 前缀用非捕获组 + `[\s\S]*?`（不要 re.S：`(.*)$` 在 re.S|re.M 下会贪婪地
    # 吞到报告末尾，把别的指标的耗时也加进来——正是本函数第一版踩的坑）。
    # 捕获组 1 恒为我们要的那一行的值。
    for name in XLA_TIME_METRICS:
        m = re.search(
            rf"^Metric: {name}\b[\s\S]*?^\s*Accumulator: ([^\n]*)", report, re.M
        )
        if m is not None:
            out[f"t.{name}"] = _parse_xla_duration(m.group(1))
    for name in XLA_COUNTERS:
        m = re.search(rf"^Counter: {name}\b[\s\S]*?^\s*Value: ([\d.]+)", report, re.M)
        if m is not None:
            out[f"c.{name}"] = float(m.group(1))
    return out


#: 持久化编译缓存是否已初始化（同进程重复初始化会抛；记一笔做幂等，也供日志查询）。
_XLA_CACHE_STATE: dict[str, str] = {}


def xla_enable_compile_cache(path: object, *, enabled: bool | None = None) -> str:
    """开启 torch_xla 的**持久化编译缓存**；返回一行状态（**绝不抛**）。

    为什么（2026-09-22 真机定案）：XLA 的程序缓存是有界的。一轮只用一次的形状（离线课程里
    的末 chunk：48000 % 1024 = 896）会被挤出 ⇒ 每个 epoch 重编两张图 ≈14~26s（单轮 85s 里
    的大头）。`torch_xla.runtime.initialize_cache(dir)` 把编译产物**落盘**，被挤出后再用到时
    是「从磁盘加载同一份可执行」而不是「重编」——**不改变任何数值**（同一 HLO 哈希 ⇒ 同一
    程序；只把「重新编译」换成「读盘」）。真机日志参见 docs/nn/tpu-perf.md §6 的 ragged tail。

    硬约束（torch_xla API）：必须在**任何计算发生之前**调用；同进程重复调用会抛，故这里
    记账做幂等。`enabled=False`（或 env `XLA_PERSISTENT_CACHE=0`）⇒ 完全跳过，行为与接线前
    逐字节一致。任何异常都吞掉并返回原因——缓存是加速手段，绝不能反过来把训练搞挂。
    """
    if enabled is None:
        enabled = os.environ.get("XLA_PERSISTENT_CACHE", "1") not in ("0", "false", "False")
    if not enabled:
        return "已停用（XLA_PERSISTENT_CACHE=0）"
    prev = _XLA_CACHE_STATE.get("dir")
    if prev is not None:
        return f"已开启（幂等：本进程已在用 {prev}）"
    try:
        import torch_xla.runtime as xr
    except Exception as err:  # 未装 torch_xla（CPU/CUDA 机器）：与接线前一致
        return f"不可用（{type(err).__name__}: 无 torch_xla）"
    fn = getattr(xr, "initialize_cache", None)
    if not callable(fn):
        return "不可用（本版 torch_xla 无 runtime.initialize_cache）"
    try:
        path_s = str(path)
        os.makedirs(path_s, exist_ok=True)
        fn(path_s)
    except Exception as err:
        return f"初始化失败（{type(err).__name__}: {err}）"
    _XLA_CACHE_STATE["dir"] = path_s
    return f"已开启 → {path_s}（缓存被挤出时读盘而非重编，不改变数值）"


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


def xla_metrics_delta(prev: dict[str, float], cur: dict[str, float]) -> dict[str, float]:
    """两次快照的差（prev 缺项按 0）；cur 缺项不产生键。"""
    return {k: v - prev.get(k, 0.0) for k, v in cur.items()}


def xla_delta_str(d: dict[str, float]) -> str:
    """一行紧凑的步耗增量：编译（含新编译/命中次数）/执行/惰性追踪/拷贝。"""

    def _sec(key: str) -> float:
        return float(d.get(f"t.{key}", 0.0))

    def _cnt(key: str) -> int:
        return int(d.get(f"c.{key}", 0.0))

    return (
        f"编译={_sec('CompileTime'):.2f}s(新={_cnt('UncachedCompile')}"
        f",命中={_cnt('CachedCompile')})"
        f" 执行={_sec('ExecuteTime'):.3f}s/{_cnt('ExecuteComputation')}次"
        f" 追踪={_sec('LazyTracing'):.3f}s"
        f" h2d={_sec('TransferToDeviceTime'):.3f}s"
        f" d2h={_sec('TransferFromDeviceTime'):.3f}s"
    )


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
def trim_shard_arrays(d: dict[str, np.ndarray], keep: int) -> dict[str, np.ndarray]:
    """把 shard 的逐 step 数组截到前 `keep` 步（**在 GAE 之前**调用）。

    为什么必须在 GAE 之前：GAE 是**反向递推**（从局末往回算），保留段的 advantage
    以被切掉的尾部为递推源头 ⇒ 先算 GAE 再丢弃会让保留段的 adv/ret 全错。

    截断处**不改 `done`**：若截断点的 done 原为 0，`compute_gae` 会自然地用
    `value` 做 bootstrap（标准 truncated-episode 处理）；把它改成 1 等于假装
    这局在此结束，会低估剩余回报。

    `keep <= 0` → 空（调用方应自行跳过该 shard）；`keep >= N` → 原样返回（零拷贝）。
    只截「第 0 维长度 == N」的数组，标量/常量字段原样保留。
    """
    n = int(d["obs"].shape[0])
    if keep >= n:
        return d
    if keep <= 0:
        keep = 0
    out: dict[str, np.ndarray] = {}
    for k, v in d.items():
        if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[0] == n:
            out[k] = v[:keep]
        else:
            out[k] = v
    return out


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
    per_stage_quota: int = 0,
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
    seen_steps: dict[int, int] = {}  # stage → 已收步数（仅 per_stage_quota > 0 时启用）
    dropped_shards = 0
    t_load = time.time()
    for k, sd in enumerate(shards):
        if k > 0 and k % load_log_every == 0:
            log(f"[{label}] loading shards {k}/{len(shards)} ({time.time() - t_load:.0f}s)")
        d = shard_loader(sd)
        N = d["obs"].shape[0]
        if N == 0:
            continue
        if per_stage_quota > 0:
            # 逐关严格配额（target_transitions 路线）：每关只收前 per_stage_quota 步。
            # ① 截断在 GAE **之前**（见 trim_shard_arrays：GAE 反向递推，顺序错了
            #    保留段的 adv/ret 全错）；② **逐关**而非全局配额，是为保住
            #    「短局关淹不了长局关」的分关独立达标不变量（全局按序截断会把排在
            #    后面的关整关丢掉）。配额满后整 shard 丢弃（不切半局进新关）。
            stage = int(d.get("stage", -1))
            room = per_stage_quota - seen_steps.get(stage, 0)
            if room <= 0:
                dropped_shards += 1
                continue
            if room < N:
                d = trim_shard_arrays(d, room)
                N = room
            seen_steps[stage] = seen_steps.get(stage, 0) + N
        adv, ret = gae(d)
        episode = {k: v for k, v in d.items() if k not in ("reward", "done", "stage")}
        episode["adv"] = adv.astype(np.float32)
        episode["ret"] = ret.astype(np.float32)
        episodes.append(episode)

    log(
        f"[{label}] shard IO + {gae_name} done for {len(episodes)} episodes "
        f"({time.time() - t_load:.0f}s)"
    )
    if per_stage_quota > 0:
        total = sum(seen_steps.values())
        short = {
            s: per_stage_quota - n
            for s, n in sorted(seen_steps.items())
            if n < per_stage_quota
        }
        log(
            f"[{label}] per-stage quota={per_stage_quota}: kept {total} steps / "
            f"{len(seen_steps)} stages, dropped {dropped_shards} shards"
            + (f"; SHORT (供给不足) stages={short}" if short else "")
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
