"""
npy IO for the NN training pipeline.

The TS exporter (`src/nn/npy.ts`) hand-writes standard NumPy 1.0 `.npy` files
(no external dependency). This module reads them with `numpy.load` and also
provides a synthetic writer used only by `tools/smoke_test.py`.

Standard `.npy` (v1.0) layout (must match `src/nn/npy.ts`):
    magic  \x93NUMPY          (6 bytes)
    version 0x01 0x00          (2 bytes)
    header_len uint16 LE       (2 bytes)
    header  dict repr + spaces, 64-byte aligned
    raw bytes (C order)

## 大语料装载（2026-11-13）

`load_dataset(..., lazy=True)` 给 `obs` / `scalars` / `extra` 返回 **ShardArray**
（每个 shard 一个 `mmap_mode="r"`，按帧读取）。原来的 `np.concatenate` 会同时持有
逐 shard 原始数组与拼接结果（峰值 ≈ 2×），9.8 GB 的 g2-classic35 在 Colab 上直接
把 notebook 顶重启。训练路径（`make_loaders` / `bc_paddle`）默认走 lazy；评测等
小语料工具仍可 eager（默认 False，行为逐字节不变）。

装载前先做**内存预检**：`plan_corpus_memory` + `check_corpus_memory` 在分配之前
按当前可用内存判断这条路径装不装得下，装不下就 `MemoryError` 并说明处置，而不是
等内核把进程杀掉（"Your notebook tried to allocate more memory than is available"）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from common.log import say
from common.platform_utils import available_memory_bytes

# Per-shard file names produced by the TS exporter.
SHARD_FILES = {
    "obs": "obs.npy",  # uint8  (N, OBS_CHANNELS, BOARD, BOARD) — v3: (N, 16, 26, 26)
    "scalars": "scalars.npy",  # float32 (N, SCALAR_DIM) — v3: (N, 30)
    # v5（plan/obs-v5-vector-gap.plan.md）：POLICY_EXTRA(37) float32 —— 独立张量。
    "extra": "extra.npy",  # float32 (N, POLICY_EXTRA_DIM)
    "actions": "actions.npy",  # uint8  (N, 2)  [move, fire] (v2: item 头删除)
    "masks": "masks.npy",  # uint8  (N, 7) [move5, fire2], 1=valid
    "conditions": "conditions.npy",  # uint8 (N,) decision condition
}
MANIFEST_FILE = "manifest.json"

#: lazy 只对「按帧读」的大张量生效；动作/掩码这类小数组仍整份拼好（索引/切片语义不变）。
_LAZY_KEYS = frozenset({"obs", "scalars", "extra"})


class ShardArray:
    """按 shard 分片的只读数组视图；全局下标 → (shard, 局部下标)。

    每个分片是一个 `np.load(mmap_mode="r")`：只有被取到的那一帧进内存，拼接本身
    不分配另一份 N 帧缓冲。**不实现 `__array__`**——整份物化只能显式
    `materialize()`，防下游一句 `np.asarray` 把 9.8 GB 语料悄悄拉回来。

    （2026-11-13：与 `npyio` 同住一个模块，而不是独立的 `shard_array.py`——
    `tests/helpers/remote_dag.py` 的层号是严格递减的，npyio 已在 L0，独立的
    L0 模块会造出 L0→L0 的边；本类只服务 npyio 的 lazy 装载，没有第二个消费者。）
    """

    def __init__(self, parts: Sequence[np.ndarray], *, name: str = "array") -> None:
        if not parts:
            raise ValueError(f"[shard-array] {name}: 至少要一个分片")
        tail = tuple(int(x) for x in parts[0].shape[1:])
        dtype = parts[0].dtype
        for i, p in enumerate(parts):
            if tuple(int(x) for x in p.shape[1:]) != tail:
                raise ValueError(
                    f"[shard-array] {name}: 第 {i} 个分片形状 {tuple(p.shape)} "
                    f"与首个分片 {tuple(parts[0].shape)} 不一致"
                )
            if p.dtype != dtype:
                raise ValueError(
                    f"[shard-array] {name}: 第 {i} 个分片 dtype {p.dtype} != {dtype}"
                )
        self._parts = list(parts)
        sizes = np.fromiter(
            (int(p.shape[0]) for p in self._parts), dtype=np.int64, count=len(self._parts)
        )
        self._ends = np.cumsum(sizes)
        self._total = int(self._ends[-1]) if len(self._parts) else 0
        self.shape = (self._total, *tail)
        self.ndim = len(self.shape)
        self.dtype = dtype
        self.name = name

    def __len__(self) -> int:
        return self._total

    def __repr__(self) -> str:
        return f"ShardArray({self.name}, shape={self.shape}, dtype={self.dtype})"

    def _at(self, index: int) -> np.ndarray:
        if index < 0:
            index += self._total
        if not 0 <= index < self._total:
            raise IndexError(f"[shard-array] {self.name}: 下标 {index} 越界（N={self._total}）")
        shard = int(np.searchsorted(self._ends, index, side="right"))
        base = int(self._ends[shard - 1]) if shard else 0
        # `np.asarray` 只把 memmap 收窄成 ndarray 视图（不复制数据），mypy 也能拿到
        # 确定的 ndarray 返回类型（numpy 的 `__getitem__` 存根是 Any）。
        return np.asarray(self._parts[shard][index - base])

    def __getitem__(self, index: Any) -> Any:
        # `Any` 与 ndarray 的索引面保持一致（int / slice / 数组 / bool 掩码 / 元组）；
        # 本类的实际调用方只读单帧，其余形态按需逐帧物化。
        if isinstance(index, (int, np.integer)):
            return self._at(int(index))
        if isinstance(index, slice):
            picks = range(*index.indices(self._total))
            if not picks:
                return np.empty((0, *self.shape[1:]), dtype=self.dtype)
            return np.stack([self._at(i) for i in picks], axis=0)
        arr = np.asarray(index)
        if arr.dtype == bool:
            if int(arr.shape[0]) != self._total:
                raise IndexError(
                    f"[shard-array] {self.name}: bool 掩码长度 {arr.shape[0]} != N={self._total}"
                )
            arr = np.flatnonzero(arr)
        if arr.ndim != 1:
            raise IndexError(f"[shard-array] {self.name}: 只支持一维索引，收到 {arr.ndim} 维")
        if int(arr.shape[0]) == 0:
            return np.empty((0, *self.shape[1:]), dtype=self.dtype)
        return np.stack([self._at(int(i)) for i in arr], axis=0)

    def materialize(self) -> np.ndarray:
        """显式整份拼进内存（**唯一**会分配 N 帧缓冲的入口；大语料勿用）。"""
        if self._total == 0:
            return np.empty(self.shape, dtype=self.dtype)
        return np.concatenate(self._parts, axis=0)

    def __reduce__(self) -> tuple[Any, ...]:
        """pickle 协议：memmap 分片按**路径**重开，绝不序列化张量内容。

        `DataLoader(num_workers>0)` 在 Windows spawn 下会把整个 dataset pickle 给
        worker；`np.memmap` 是 ndarray 子类，默认 pickle 会复制全部数据（9.4 GiB
        语料 × N 个 worker = 直接把内存打回 OOM）。这里把 memmap 分片换成
        `(绝对路径, name)`，worker 侧重新 `mmap`。非 memmap 分片（单测合成）仍按
        原数组序列化——它们本来就是内存里的普通 ndarray。
        """
        paths: list[str] = []
        for p in self._parts:
            if isinstance(p, np.memmap) and p.filename is not None:
                paths.append(os.path.abspath(str(p.filename)))
            else:
                return (self.__class__, (self._parts,), {"name": self.name})
        return (_reopen_shard_array, (paths, self.name))


def _reopen_shard_array(paths: list[str], name: str) -> ShardArray:
    """`ShardArray.__reduce__` 的 worker 侧还原（只重开 mmap，不读数据）。"""
    return ShardArray([read_npy(p, mmap=True) for p in paths], name=name)


#: 语料字段：真实 ndarray，或 mmap 分片的只读拼接视图。
ArrayField = np.ndarray | ShardArray


def read_npy(path: str, *, mmap: bool = False) -> np.ndarray:
    """Read a standard .npy file written by either numpy or the TS writer."""
    # np.load 存根返回 Any（mmap/NpzFile 多态），显式收窄为本函数契约的 ndarray。
    arr: np.ndarray = np.load(
        path, allow_pickle=False, mmap_mode="r" if mmap else None
    )
    return arr


OPTIONAL_FILES = {"returns": "returns.npy"}  # v2: M3 value MC 预置（可选）


def load_shard(shard_dir: str, *, mmap: bool = False) -> dict[str, np.ndarray]:
    """Load one exported replay shard into a dict of numpy arrays."""
    out: dict[str, np.ndarray] = {}
    for key, fname in SHARD_FILES.items():
        p = os.path.join(shard_dir, fname)
        if not os.path.exists(p):
            raise FileNotFoundError(f"missing shard file {p}")
        out[key] = read_npy(p, mmap=mmap)
    for key, fname in OPTIONAL_FILES.items():
        p = os.path.join(shard_dir, fname)
        if os.path.exists(p):
            out[key] = read_npy(p, mmap=mmap)
    return out


# ---------------------------------------------------------------- 内存预检


@dataclass(frozen=True)
class CorpusMemoryPlan:
    """语料的磁盘/内存足迹（不读张量内容，只 stat + mmap 头部取 N）。"""

    data_dir: str
    shards: int
    samples: int
    obs_bytes: int
    other_bytes: int

    @property
    def total_bytes(self) -> int:
        return int(self.obs_bytes) + int(self.other_bytes)

    @property
    def eager_peak_bytes(self) -> int:
        """旧路径峰值：逐 shard 原始数组 + `np.concatenate` 结果 ≈ 2×。"""
        return 2 * self.total_bytes + (256 << 20)

    def lazy_peak_bytes(self, batch_size: int = 256, num_workers: int = 0) -> int:
        """mmap 路径峰值：非 obs 张量 + 在飞批次 + 进程余量（page cache 可回收）。"""
        per_sample = self.obs_bytes // max(1, self.samples)
        inflight = max(1, int(batch_size)) * (max(0, int(num_workers)) + 2)
        return self.other_bytes + per_sample * inflight * 2 + (768 << 20)


def plan_corpus_memory(data_dir: str) -> CorpusMemoryPlan:
    """扫描语料根 → `CorpusMemoryPlan`（只开每个 shard 的 obs 头，读不到 N 不装载）。"""
    shards = scan_shards(data_dir)
    if not shards:
        raise FileNotFoundError(f"no shards (obs.npy) found under {data_dir}")
    samples = 0
    obs_bytes = 0
    other_bytes = 0
    for s in shards:
        obs_path = os.path.join(s, SHARD_FILES["obs"])
        obs = read_npy(obs_path, mmap=True)
        samples += int(obs.shape[0])
        try:
            obs_bytes += os.path.getsize(obs_path)
        except OSError:
            obs_bytes += int(obs.nbytes)
        for name in os.listdir(s):
            if not name.endswith(".npy") or name == SHARD_FILES["obs"]:
                continue
            try:
                other_bytes += os.path.getsize(os.path.join(s, name))
            except OSError:
                pass
    return CorpusMemoryPlan(str(data_dir), len(shards), samples, obs_bytes, other_bytes)


def _gib(n: int) -> str:
    return f"{n / (1 << 30):.1f} GiB"


def check_corpus_memory(
    plan: CorpusMemoryPlan,
    *,
    lazy: bool,
    batch_size: int = 256,
    num_workers: int = 0,
    available: int | None = None,
) -> int | None:
    """装载前内存预检：装不下就 `MemoryError`（附可用内存与处置），返回可用字节数。

    `available` 可注入（单测用）；缺省问 `platform_utils.available_memory_bytes()`。
    探测不到可用内存（macOS / 异常）时不编造数字：只跳过拒绝，日志写 unknown。
    """
    avail = available_memory_bytes() if available is None else int(available)
    need = (
        plan.lazy_peak_bytes(batch_size, num_workers)
        if lazy
        else plan.eager_peak_bytes
    )
    if avail is not None and avail < need:
        mode = "mmap 按需读取" if lazy else "整份装入内存"
        raise MemoryError(
            f"[npyio] 内存预检失败：语料 {plan.data_dir} 共 {_gib(plan.total_bytes)}"
            f"（{plan.shards} shards / {plan.samples} 样本），{mode}预计需 {_gib(need)}，"
            f"当前可用 {_gib(avail)}。"
            + (
                "训练请走 lazy 装载（worker/train/bc.py 已默认）；"
                if not lazy
                else "请换更大内存的机器或先裁剪语料；"
            )
            + "否则会在训练中被内核杀掉（Colab：notebook has restarted）。"
        )
    return avail


def _log_memory_plan(
    plan: CorpusMemoryPlan,
    *,
    lazy: bool,
    available: int | None,
    batch_size: int,
    num_workers: int,
) -> None:
    """大语料才打一行（小语料/单测不制造噪音）。"""
    if plan.total_bytes < (256 << 20):
        return
    need = (
        plan.lazy_peak_bytes(batch_size, num_workers)
        if lazy
        else plan.eager_peak_bytes
    )
    avail = "unknown" if available is None else _gib(available)
    mode = "mmap 按需" if lazy else "eager 整份"
    say(
        f"[npyio] 内存预检：语料 {_gib(plan.total_bytes)} / {plan.samples} 样本 / "
        f"{plan.shards} shards｜可用 {avail}｜装载={mode}｜预计峰值 {_gib(need)}"
    )


# ---------------------------------------------------------------- 装载


def verify_shard_schema(shard_dir: str, arrays: dict[str, np.ndarray]) -> None:
    """语料身份校验（v3，obs spec §3.4-7）：形状 + shard manifest 指纹。

    major bump 后旧语料**全部作废**（权重同理）。两道判据：
      · 形状（最硬，无 manifest 也判）：obs 必须 (N, OBS_CHANNELS, BOARD, BOARD)、
        scalars 必须 (N, SCALAR_DIM)；
      · 指纹（manifest 有 `schemaFingerprint` 时判）：与当前 schema 常量不符即错。
    manifest 缺指纹（v2 及更早产物）不豁免形状判据——形状不符同样 raise。
    """
    from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM, SCHEMA_FINGERPRINT

    obs = arrays.get("obs")
    if obs is not None and (obs.ndim != 4 or tuple(obs.shape[1:]) != (OBS_CHANNELS, BOARD, BOARD)):
        raise ValueError(
            f"[npyio] {shard_dir}: obs 形状 {tuple(obs.shape)} 与当前 schema 不符"
            f"（期望 (N, {OBS_CHANNELS}, {BOARD}, {BOARD})）——该 shard 来自另一个"
            f"schema major，语料已作废，请重新导出（major bump ⇒ 旧 shard 全废）"
        )
    sc = arrays.get("scalars")
    if sc is not None and (sc.ndim != 2 or sc.shape[1] != SCALAR_DIM):
        raise ValueError(
            f"[npyio] {shard_dir}: scalars 形状 {tuple(sc.shape)} 与当前 schema 不符"
            f"（期望 (N, {SCALAR_DIM})）——请重新导出该 shard"
        )
    # v5：extra 形状判据（POLICY_EXTRA(37)）；缺文件由 load_shard 的 FileNotFoundError 拦。
    ex = arrays.get("extra")
    if ex is not None and (ex.ndim != 2 or ex.shape[1] != POLICY_EXTRA_DIM):
        raise ValueError(
            f"[npyio] {shard_dir}: extra 形状 {tuple(ex.shape)} 与当前 schema 不符"
            f"（期望 (N, {POLICY_EXTRA_DIM})）——请重新导出该 shard"
        )
    mpath = os.path.join(shard_dir, MANIFEST_FILE)
    if not os.path.exists(mpath):
        return
    try:
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict):
        return
    fp = manifest.get("schemaFingerprint")
    if isinstance(fp, str) and fp and fp != SCHEMA_FINGERPRINT:
        raise ValueError(
            f"[npyio] {shard_dir}: manifest 指纹 {fp} != 当前 schema {SCHEMA_FINGERPRINT}"
            "——该 shard 来自另一个 schema 版本，语料已作废，请重新导出"
        )


def scan_shards(data_dir: str) -> list[str]:
    """Find all shard directories under `data_dir` (each contains obs.npy)."""
    shards: list[str] = []
    for root, _dirs, files in os.walk(data_dir):
        if SHARD_FILES["obs"] in files:
            shards.append(root)
    return sorted(shards)


#: v6 三头语料的文件名表（`export-rl-rollout.ts` 的产物，★与 legacy 的 `SHARD_FILES` **并存**）。
#: 差别：动作拆成三份（`a_move/a_fire/a_hold`，不是单份 2 列 `actions`）·
#: 掩码 9 位叫 `mask.npy`（单数，不是 `masks.npy` 的 7 位）· **没有 `conditions`**（恒 0 补）。
#: 为什么另立一张表而不是改 `SHARD_FILES`：PPO 侧（`np_core`）与 legacy BC 都在读那份，
#: 动它 = 两套语料集体失配。这里只做「按需分派」。
V6_FILES = {
    "obs": "obs.npy",  # uint8 (N, 17, 26, 26)
    "scalars": "scalars.npy",  # float32 (N, 46)
    "extra": "extra.npy",  # float32 (N, POLICY_EXTRA_DIM)
    "a_move": "a_move.npy",  # uint8 (N,) 0..3
    "a_fire": "a_fire.npy",  # uint8 (N,) 0..2
    "a_hold": "a_hold.npy",  # uint8 (N,) 0..1
    "mask": "mask.npy",  # uint8 (N, 9) [move4, fire3, hold2], 1=valid
}


def _load_dataset_v6(data_dir: str, *, lazy: bool = False) -> dict[str, ArrayField]:
    """v6 三头语料装载（`export-rl-rollout.ts` 的 shard）。

    产出的 dict 与 legacy 版**同键**（下游 `NNDataset` / `make_loaders` 不用分叉），
    额外多一个 `hold` 键（`(N,) int64`）——只有 v6 分支的 `__getitem__` 会读它。

    `lazy=True` 时大张量走 `ShardArray`（逐 shard mmap），不再 `np.concatenate`。

    ⚠ 不走 `verify_shard_schema`：那条按 legacy 的文件名/宽度校验（7 位掩码、`actions` 2 列），
    v6 语料必然全红。这里用**形状自检**替代（宽度 9 / 动作三类齐全 / 四者等长）。
    """
    shards = scan_shards(data_dir)
    if not shards:
        raise FileNotFoundError(f"no v6 shards (obs.npy) found under {data_dir}")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in V6_FILES}
    for s in shards:
        for key, fname in V6_FILES.items():
            p = os.path.join(s, fname)
            if not os.path.exists(p):
                raise FileNotFoundError(f"[npyio] v6 shard 缺 {fname}：{s}")
            parts[key].append(read_npy(p, mmap=lazy and key in _LAZY_KEYS))
    out: dict[str, ArrayField] = {}
    for k, v in parts.items():
        out[k] = ShardArray(v, name=k) if lazy and k in _LAZY_KEYS else np.concatenate(v, axis=0)
    n = int(out["obs"].shape[0])
    for k in ("scalars", "extra", "a_move", "a_fire", "a_hold", "mask"):
        if int(out[k].shape[0]) != n:
            raise ValueError(f"[npyio] v6 语料长度不一致：obs={n} {k}={out[k].shape[0]}")
    mask_check = out["mask"]
    if mask_check.ndim != 2 or int(mask_check.shape[1]) != 9:
        raise ValueError(f"[npyio] v6 mask 必须是 (N,9)，收到 {mask_check.shape}")
    # C9 value-only：v6 语料也可能带 `returns.npy`（value 头 MC 回归目标）。
    # 允许部分 shard 缺失：缺失局填 NaN，与 legacy `OPTIONAL_FILES` 的 n/a 语义一致。
    ret_parts: list[np.ndarray] = []
    has_returns = False
    for s, obs_part in zip(shards, parts["obs"], strict=True):
        rp = os.path.join(s, "returns.npy")
        if os.path.exists(rp):
            arr = read_npy(rp).astype(np.float32)
            if arr.shape[0] != obs_part.shape[0]:
                raise ValueError(
                    f"[npyio] v6 returns 长度 {arr.shape[0]} != obs {obs_part.shape[0]}（{s}）"
                )
            has_returns = True
            ret_parts.append(arr)
        else:
            ret_parts.append(np.full(obs_part.shape[0], np.nan, dtype=np.float32))
    if has_returns:
        out["returns"] = np.concatenate(ret_parts)
    a_hold = out.pop("a_hold")
    a_move = out.pop("a_move")
    a_fire = out.pop("a_fire")
    if not (
        isinstance(a_hold, np.ndarray)
        and isinstance(a_move, np.ndarray)
        and isinstance(a_fire, np.ndarray)
    ):
        raise TypeError("[npyio] v6 动作数组不应走 lazy 分片视图")
    out["hold"] = a_hold.astype(np.int64)
    # 兼容层：legacy 键名保留（`actions` 2 列 = move+fire，`masks` = 9 位原样）
    out["actions"] = np.stack([a_move, a_fire], axis=1).astype(np.int64)
    out["masks"] = np.asarray(out["mask"], dtype=np.float32)
    out["conditions"] = np.zeros(n, dtype=np.int64)
    out["shard_ids"] = np.repeat(
        np.arange(len(shards), dtype=np.int64), [v.shape[0] for v in parts["obs"]]
    )
    return out


def load_dataset(
    data_dir: str,
    action_v6: bool = False,
    *,
    lazy: bool = False,
    batch_size: int = 256,
    num_workers: int = 0,
) -> dict[str, ArrayField]:
    """Concatenate every shard under `data_dir` into one big sample dict.

    P2-6d（2026-09-02）：额外产出 **"shard_ids"**（(N,) int64，每样本所属 shard
    索引）——让 dataset 层能做 **shard 级 train/val 切分**，杜绝"同局相邻帧跨集
    泄漏 → val 虚高"。旧调用方只读已知键，不受影响。

    v3（obs-schema-v3.plan.md v4.0 §3.4-7）：逐个 shard 跑 **语料身份校验**
    （`verify_shard_schema`）——v2 的 14ch/19sc shard 混进 v3 语料会静默错位成
    一张「半张脸」的观测（major bump 全旧权重失效 ⇒ 同理旧语料全废），必须先
    炸在加载处而不是等训练曲线发疯。

    `lazy=True`（2026-11-13）：`obs/scalars/extra` 用 `ShardArray`（每 shard mmap），
    避免 `np.concatenate` 的 2× 峰值；训练路径默认走这条。装载前做内存预检。
    """
    plan = plan_corpus_memory(data_dir)
    avail = check_corpus_memory(
        plan, lazy=lazy, batch_size=batch_size, num_workers=num_workers
    )
    _log_memory_plan(
        plan,
        lazy=lazy,
        available=avail,
        batch_size=batch_size,
        num_workers=num_workers,
    )
    if action_v6:
        return _load_dataset_v6(data_dir, lazy=lazy)
    shards = scan_shards(data_dir)
    parts: dict[str, list[np.ndarray]] = {k: [] for k in SHARD_FILES}
    opt_parts: dict[str, list[np.ndarray]] = {k: [] for k in OPTIONAL_FILES}
    for s in shards:
        d = load_shard(s, mmap=lazy)
        verify_shard_schema(s, d)
        for k in SHARD_FILES:
            parts[k].append(d[k])
        for k in OPTIONAL_FILES:
            if k in d:
                opt_parts[k].append(d[k])
    out: dict[str, ArrayField] = {}
    for k, v in parts.items():
        out[k] = ShardArray(v, name=k) if lazy and k in _LAZY_KEYS else np.concatenate(v, axis=0)
    out["shard_ids"] = np.repeat(
        np.arange(len(shards), dtype=np.int64), [v.shape[0] for v in parts["obs"]]
    )
    n = int(out["obs"].shape[0])
    for k, v in opt_parts.items():
        if v:
            # 可选文件部分 shard 缺失 → 拼接后以 NaN 补齐（与 dataset.py 的 n/a 语义一致）
            full = np.full((n,), np.nan, dtype=np.float32)
            off = 0
            for arr in v:
                full[off : off + arr.shape[0]] = arr
                off += arr.shape[0]
            out[k] = full
    return out


def save_shard(shard_dir: str, arrays: dict[str, np.ndarray], manifest: dict[str, Any]) -> None:
    """Write a shard (numpy) — used by smoke_test to synthesize data."""
    os.makedirs(shard_dir, exist_ok=True)
    for key, fname in SHARD_FILES.items():
        np.save(
            os.path.join(shard_dir, fname),
            arrays[key].astype(
                np.uint8 if key in ("obs", "actions", "masks", "conditions") else np.float32
            ),
        )
    with open(os.path.join(shard_dir, MANIFEST_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
