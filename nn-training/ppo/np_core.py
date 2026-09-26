"""ppo/np_core.py — ppo/common 的**纯 numpy/stdlib 核心**（不 import torch）。

ppo/common.py 混装两类东西：
  * numpy/stdlib：GAE、shard 发现/装载、episode 装载骨架（load_episodes_common）、
    **per-tick RL shard 装载（_RL_SHARD_SPEC / load_shard / load_episodes，2026-09-26
    自 ppo/engine.py 搬来）**、minibatch 切分、per-stage 配额截断、XLA 文本指标解析、
    TPU 指纹判据、numpy RNG 状态打包；
  * torch 张量：masked_logsoftmax / approx_kl_est / sync_scalars / 设备助手 / ckpt。
前者被大量「业务逻辑」用例使用，却因同住一个模块而被 torch 拖进测试路径。
2026-09-26 拆出本模块（torch-free）；`ppo/common.py` 从这里再导出，调用点一行不改。
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

from rl.log import log

# numpy 的 RandomState.get_state() 存根把 legacy 默认标成 False（→ dict），但 numpy 2.x
# 实测返回的是 legacy 元组。显式传 legacy=True 并断言元组形态，双层保险。
_RNGState = tuple[str, np.ndarray, int, int, float]

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

def is_xla(device) -> bool:
    """device 是否为 XLA/TPU（torch.device('xla') 或 xm.xla_device()）。"""
    return getattr(device, "type", None) == "xla"

#: TPU 后端在设备属性里留下的指纹（CPU 后端的 XLA 设备没有这两个键）。
TPU_ATTR_KEYS = ("coords", "core_on_chip")

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

# ---------------- XLA 设备 / 诊断助手（2026-09-26 从 ppo/common.py 搬来） ----------------
# 顶层零 torch：torch / torch_xla 全部延迟到调用点（未装 torch_xla 的机器行为逐字节不变）。
# 为什么搬：这一簇本就是「判据 + 诊断」（读设备属性 / 打一行自检日志），却与 torch 张量
# 助手同住 ppo/common.py ⇒ 想测它们的用例只能连坐 torch（tests/test_tpu_backend_guard.py）。

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

# ---------------- minibatch chunking ----------------
def chunk_episodes(episodes: list[dict], mb: int, shuffle: bool = True) -> list[dict]:
    """Split per-episode dicts into fixed-size minibatch chunks (every chunk exactly `mb`).

    GAE is computed per-episode BEFORE chunking; chunks are only an update-
    granularity unit (bounds activation memory, adds gradient steps).

    shuffle（P1-6，2026-09-02 默认开启）：旧实现按 episode 顺序连续切片——每个
    minibatch 是同一局内 mb 个**相邻**决策步，帧间强相关，SGD 的 i.i.d. 假设被
    严重违反（梯度方差大 → KL spikes → 熔断/早停；H1 假设的主因）。默认改为
    **全局 transition 级重排**：展平全部 episodes → 单次 permutation → 按 mb 切片。
    GAE/adv/ret 是逐 transition 存储的，重排不改变任何数学（on-policy 正确性
    不受影响）；RNG 用全局 np.random（由 main 播种，可复现）。
    shuffle=False 保留旧行为（逐字节一致，供对照实验）。

    mb 对齐（2026-09-23，见 docs/nn.progress.md §140）：重排路径**丢弃尾部
    `n % mb` 步**，让每个 chunk 恰为 `mb` 行（旧行为是末块 ragged）。为什么值这个
    代价：ragged 末块每个 epoch 只用一次，中间隔着几十个满块步 ⇒ XLA 的程序缓存
    必然把它挤出、下一 epoch 重新编译（真机实录 12~13s × epochs；it98 一轮 83.8s
    里 50s 是它，而满块稳态只要 0.179s/步）。形状恒定后图签名恒为 `B{mb}`，编译
    只付一次。丢的是重排序列的**尾部** ⇒ 均匀随机子集，无偏；`n % mb == 0` 时
    逐字节不变（`idx` 的抽样顺序未动）。
    归一化（load_episodes_common 的 adv/ret）覆盖**整池**（含被丢的 <mb 步）——
    刻意保持「归一化粒度 = 本轮池」的既有语义，不因丢弃而改变。
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
    n_used = (n // mb) * mb
    if n_used == 0:
        # 池子不足一个 mb（只在小样本冒烟/单测里出现）：保留旧的一块 ragged。
        # 丢弃会得到 0 个 chunk ⇒ 静默不训练，比多付一次小 shape 编译糟得多。
        log(f"[chunk] WARN 池子 {n} 步 < mb={mb} ⇒ 不裁剪（单独一块 ragged）")
        n_used = n
    elif n_used < n:
        log(
            f"[chunk] mb 对齐：pooled={n} 步 → 丢弃尾部 {n - n_used} 步"
            f"（{n_used // mb} 块 × {mb}；无 ragged 末块 ⇒ XLA 图签名恒定、不重编）"
        )
    return [
        {k: v[idx[s : s + mb]] for k, v in flat.items()} for s in range(0, n_used, mb)
    ]

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


# ---------------- episode loading（ppo / ppo_intent 共用骨架）----------------
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
    bundle: Any = None,
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
    # ★ 2026-09-24（日志节食）：这四行是「装载阶段」的读数集合，传给 bundle 时攒进调用方
    # 的那**一行**（见 `log_bundle.py`）；bundle=None 时逐字节保持原输出（goal/intent/
    # 本机三条线共用本函数，行为不变）。
    if bundle is not None:
        bundle.add("shards", f"{len(shards)} {shard_kind} ← {data_root}")
    else:
        log(f"[{label}] loaded {len(shards)} {shard_kind} shards from {data_root}")

    episodes: list[dict] = []
    seen_steps: dict[int, int] = {}  # stage → 已收步数（仅 per_stage_quota > 0 时启用）
    dropped_shards = 0
    t_load = time.time()
    for k, sd in enumerate(shards):
        if k > 0 and k % load_log_every == 0:
            if bundle is not None:
                bundle.add("装载", f"{k}/{len(shards)} {time.time() - t_load:.0f}s")
            else:
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

    _io_line = (
        f"shard IO + {gae_name} done for {len(episodes)} episodes "
        f"({time.time() - t_load:.0f}s)"
    )
    if bundle is not None:
        bundle.add("装载", f"{len(shards)}/{len(shards)} {time.time() - t_load:.0f}s")
        bundle.add("episodes", f"{len(episodes)} eps（{gae_name} 已算）")
    else:
        log(f"[{label}] {_io_line}")
    if per_stage_quota > 0:
        total = sum(seen_steps.values())
        short = {
            s: per_stage_quota - n
            for s, n in sorted(seen_steps.items())
            if n < per_stage_quota
        }
        _quota_line = (
            f"per-stage quota={per_stage_quota}: kept {total} steps / "
            f"{len(seen_steps)} stages, dropped {dropped_shards} shards"
        )
        if bundle is not None:
            bundle.add("配额", _quota_line)
            if short:
                # 供给不足是**要看的**（缺哪关、缺多少），攒行不能把它埋掉 ⇒ 单独一句。
                bundle.note(f"SHORT (供给不足) stages={short}")
        else:
            log(f"[{label}] {_quota_line}" + (f"; SHORT (供给不足) stages={short}" if short else ""))
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


# ---------------- GAE / 装载默认折扣（CLI 可覆盖：ppo/engine.py --gamma/--lam） ----------------
# R6（2026-08-25 训练质量审计）：GAMMA 0.99 → 0.995——决策间隔 K=10 下有效时域从
# ~100 决策(16.7s) 拉长到 ~200 决策(33s)，守家/拦截是长时域行为，需要更远的信用回溯。
# （同批把 VF_COEF 0.5 → 1.0，仍留在 ppo/engine.py。）
GAMMA = 0.995
LAM = 0.95


# ---------------- RL shard 装载（per-tick / 2026-09-26 从 ppo/engine.py 搬来） ----------------
# ---------------- trajectory loading ----------------
# RL shard 字段表：{key: (filename, dtype)} —— 与旧 load_shard 逐字段一致（copy=False 零拷贝）。
# plan/rl-training-config.md §4.2：per-tick shard 的 reward 由 TS 落盘改为 Python
# 公式引擎计算——TS 只落 `metrics.npy`（[N+1,21] f8：N 个决策快照 + 1 个终局快照），
# 加载器读 holder（rl.reward_context）的 RewardFn 按配置公式算 reward。
_RL_SHARD_SPEC: dict[str, tuple[str, npt.DTypeLike]] = {
    "obs": ("obs.npy", np.uint8),
    "scalars": ("scalars.npy", np.float32),
    "a_move": ("a_move.npy", np.int64),
    "a_fire": ("a_fire.npy", np.int64),
    "lp_move": ("lp_move.npy", np.float32),
    "lp_fire": ("lp_fire.npy", np.float32),
    "value": ("value.npy", np.float32),
    "metrics": ("metrics.npy", np.float64),
    "done": ("done.npy", np.int64),
    "mask": ("mask.npy", np.int64),
}


def discover_rl_shards(root: str) -> list[str]:
    return discover_shards(root, ("metrics.npy", "obs.npy"))


def load_shard(dirpath: str) -> dict[str, np.ndarray]:
    """读 shard 字段 + manifest → `reward` 由公式引擎按 metrics 算（N 样本）。

    无 holder（reward_fn None）时**响亮报错**——旧 reward.npy 直读路径已随
    TS 落盘改版删除（历史 run 需在公式引擎下重采，plan §4.2 / §12-2）。
    """
    d = load_shard_fields(dirpath, _RL_SHARD_SPEC)
    metrics = d.pop("metrics")  # [N+1,21]
    n_obs = int(d["obs"].shape[0])
    if metrics.shape[0] != n_obs + 1:
        raise ValueError(
            f"metrics 行数 {metrics.shape[0]} != nSamples+1={n_obs + 1}（{dirpath}）——"
            "指标行失配（决策行 + 终局行），shard 损坏或格式不符"
        )
    manifest: dict = {}
    mp = os.path.join(dirpath, "manifest.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    d["reward"] = _reward_from_metrics(metrics, manifest, dirpath)
    # stage：供 load_episodes_common 的**逐关**配额（target_transitions 路线）分组。
    # 它不进 episode 字段集（load_episodes_common 显式排除 "stage"），也不是 GAE 输入；
    # 0 维数组 ⇒ trim_shard_arrays 不会截它。manifest 缺该键时取 -1（所有 shard 归一组，
    # 配额退化为全局，加载日志里会显示只有 1 个 stage）。
    d["stage"] = np.asarray(int(manifest.get("stage", -1)))
    return d


def _reward_from_metrics(metrics: np.ndarray, manifest: dict, dirpath: str) -> np.ndarray:
    """metrics [N+1,29] + manifest {outcome, score} → reward [N]（float32）。

    wrapper（§4.3.3）：Φ = formula(metrics)；r[i] = Φ[i+1]−Φ[i]；末样本 +=
    reconcile（score_reconcile → scale·score(gated)−(Φ[N]−Φ[0])；toy → terminal）。
    """
    from rl.reward_context import current as _ctx_current
    from rl.reward_library import METRICS_DIM

    ctx = _ctx_current()
    m = np.asarray(metrics, dtype=np.float64)
    if m.ndim != 2 or m.shape[1] != METRICS_DIM:
        raise ValueError(
            f"metrics 形状应为 [N+1,{METRICS_DIM}]，收到 {m.shape}（{dirpath}）——metrics_version 不匹配？"
        )
    mver = manifest.get("metrics_version")
    if mver is not None and int(mver) != ctx.metrics_version:
        raise ValueError(
            f"shard metrics_version={mver} != 期望 {ctx.metrics_version}（{dirpath}）——"
            "shard 格式版本不匹配，禁止静默错读（评审 LC §1.1）"
        )
    if ctx.reward_fn is None:
        raise RuntimeError(
            f"metrics shard 需要 RewardFn，但 reward_context holder 未设置（{dirpath}）——"
            "per-tick 训练请经 `python run_rl.py --course <name>` 启动（奖励唯一定义源=课程配置公式）"
        )
    outcome = str(manifest.get("outcome", "timeout"))
    score = float(manifest.get("score", 0.0))
    r = ctx.reward_fn(m, outcome, score, ctx.it)
    return np.asarray(r, dtype=np.float32)


def load_episode_from_shard(dirpath: str, gamma: float = GAMMA, lam: float = LAM) -> dict | None:
    """流式 backend 接口（rl/stream.py）：单个 shard → 可训练 episode（adv/ret 未归一）。

    ppo_intent.load_episode_from_shard 同签名——run_rollout_stream 以 backend 参数
    复用同一套流式基础设施（工程化共享，勿在 stream 内复制第二份加载逻辑）。
    """
    d = load_shard(dirpath)
    N = d["obs"].shape[0]
    if N == 0:
        return None
    adv, ret = compute_gae(d["reward"], d["value"], d["done"], gamma, lam)
    return {
        "obs": d["obs"],
        "scalars": d["scalars"],
        "a_move": d["a_move"],
        "a_fire": d["a_fire"],
        "lp_move": d["lp_move"],
        "lp_fire": d["lp_fire"],
        "value": d["value"],
        "adv": adv.astype(np.float32),
        "ret": ret.astype(np.float32),
        "mask": d["mask"],
    }


def load_episodes(
    data_root: str,
    gamma: float = GAMMA,
    lam: float = LAM,
    normalize_adv: bool = True,
    normalize_ret: bool = False,
    per_stage_quota: int = 0,
    bundle: Any = None,
) -> list[dict]:
    """Discover trajectory shards under `data_root`, compute per-episode GAE,
    and normalize advantages across the whole batch. Shared by this CLI's
    update mode and the run_rl.py loop.

    normalize_ret（R5，默认 False = 历史行为逐字节不变）：True 时 ret 跨 batch
    归一（mean 0 / std 1，intent 头同款），让 value 头拟合 O(1) 量级目标。
    备注：GAE 自举仍用 rollout 时 head 输出的原始尺度 V——baseline 不改变策略
    梯度的无偏性，只改变方差；当前 V 近乎常数（MSE≫return 方差）时归一化只会
    把 baseline 从"无"变"有"，不会变坏。stream 路径不走本函数（见
    rl/stream.py），该 flag 只覆盖串行（local/remote）路径。
    """
    return load_episodes_common(
        data_root,
        label="ppo",
        shard_kind="RL",
        need_files=("metrics.npy", "obs.npy"),
        shard_loader=load_shard,
        gae=lambda d: compute_gae(d["reward"], d["value"], d["done"], gamma, lam),
        gae_name="GAE",
        normalize_adv=normalize_adv,
        normalize_ret=normalize_ret,
        per_stage_quota=per_stage_quota,
        # ★ 2026-09-24（日志节食）：装载读数攒进调用方的那一行（None = 逐字节原行为）。
        bundle=bundle,
    )
