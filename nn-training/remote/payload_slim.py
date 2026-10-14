"""remote/payload_slim —— payload 字节瘦身（wire-diet）的**唯一实现**。

`plan/wire-diet.plan.md` §3 的 P1（打包端先算 reward）+ P2（训练输入字段 f16）都落在这里，
因为两者的判据同源：**都只改「传出去的那份副本」的字节表示**——hub 本地 shard 不动、训练语义
不动、`data_fp` 不动（它只取 shard 的 `(name, wver, stage, seed)`，`common/manifest.py`）。

三条必须守住的判据（评审 `plan/wire-diet.review-bf.md` 的 P0/P1）：

* **决定性**：只产出内存字节（`pack_payload(member_bytes=/shard_extra=)`），**不落临时 f16
  文件** —— 临时文件的 mtime 会进 tar 头，同一份内容每打一次 `payload_sha256` 都变
  （sha 是校验锚；`PAYLOAD_PERTURB_NAME` 的存在也以「打包决定性」为前提）。
* **血缘自述**：打包端算好的 reward 必须带 `reward_meta.json`（it / metrics_version /
  formula_hash）—— 奖励在**别处**算，「it 用错」在数值上与正常 reward 无从区分
  （评审 P1-4：`{metrics_version, formula_fp}` 这类不含 it 的守卫抓不到）。
* **可回退**：拿不到 `reward_spec` 就**不做 P1**（照旧带 `metrics.npy`，逐字节旧行为）；
  P2 与它正交，仍生效（f16 对任何代码版本的读取端都透明 —— `astype(f32, copy=False)`）。

依赖面 = `numpy` + `biz.reward_library`（numpy-only）+ `common.*`（**含 `common.shard`**：
文件名/版本/f16 字段表的唯一定义处 —— 写端与读端 `worker.ppo.np_core` 都 import 它，而 hub 侧
**不得** import `worker.*`，否则 `remote.payload_slim` 的拓扑秩会被读端抬起来连坐一串上位模块）。
**顶层零 torch**（hub 侧 D2 不变：本模块只碰 npy 字节）。
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from common.errors import ProtocolError
from common.payload import DROP_MEMBER, MemberBytes
from common.shard import (
    METRICS_NAME,
    REWARD_META_NAME,
    REWARD_NAME,
    REWARD_VERSION,
    RL_F16_FIELDS,
)

#: 允许降精度传输的**文件名**（由字段清单派生：读端 `_RL_SHARD_SPEC` 里 key → `<key>.npy`）。
F16_FILES: frozenset[str] = frozenset(f"{k}.npy" for k in RL_F16_FIELDS)

#: `METRICS_DIM` 缓存（`biz.reward_library` 是重模块，import 期不进包）。
_METRICS_DIM = 0


def _metrics_dim() -> int:
    global _METRICS_DIM
    if _METRICS_DIM == 0:
        from biz.reward_library import METRICS_DIM

        _METRICS_DIM = int(METRICS_DIM)
    return _METRICS_DIM


def _npy_bytes(arr: np.ndarray) -> bytes:
    """npy 落内存（`np.save` 的头部/对齐是确定性的 ⇒ 打包决定性不受影响）。"""
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _json_bytes(obj: dict) -> bytes:
    """紧凑 + 排序键 + 无空白 ⇒ 决定性字节（同内容必同 sha，重打不会漂）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ShardSlim:
    """一轮发布的变换决策表：构造一次，逐目录/逐成员用。

    只为打包期算术而生（不是玩法状态）：调用方要读回「这一轮到底改了什么」（`fmt` + 计数），
    而那些值只能在遍历里累出来。
    """

    def __init__(
        self,
        *,
        reward_spec: Any | None,
        it: int,
        metrics_version: int,
        formula_hash: str,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.reward_spec = reward_spec
        self.it = int(it)
        self.metrics_version = int(metrics_version)
        self.formula_hash = str(formula_hash or "")
        self.log = log
        self.n_reward_dirs = 0
        self.n_untouched_dirs = 0
        self.n_f16_files = 0
        self.n_f16_dirs = 0
        self._kind: dict[str, str] = {}  # shard 目录名 → 本轮对它的处置（每目录只判一次）
        self._f16_dirs: set[str] = set()

    # ---------------------------------------------------------------- 判定
    def _kind_of(self, d: Path) -> str:
        """这个 shard 目录本轮怎么处置。判据 = **盘上有 `metrics.npy`**（per-tick RL 指纹）。

        为什么用存在性而不是 kind/manifest 字段：BC 语料（`BC_SHARD_FILES`）与意图 shard
        （`INTENT_SHARD_FILES`，自带 reward.npy）都没有 `metrics.npy` ⇒ 一条存在性判据就把它们
        全部排除在外，不需要第二份「哪些 kind 可瘦身」的名单（那会漂）。
        """
        got = self._kind.get(d.name)
        if got is not None:
            return got
        if not (d / METRICS_NAME).exists():
            got = "skip:non-rl"
        elif (d / REWARD_NAME).exists():
            # 已经是新形态（历史 / 重打）⇒ 不动。幂等：重打不会叠一次量化。
            got = "skip:already"
        else:
            got = "slim"
        self._kind[d.name] = got
        if got != "slim":
            self.n_untouched_dirs += 1
        return got

    # ---------------------------------------------------------------- 逐成员
    def member_bytes(self, shard_dir: Path, filename: str) -> MemberBytes:
        """`pack_payload` 的逐成员回调：替换 / 原样（None）/ 丢弃。"""
        d = Path(shard_dir)
        if self._kind_of(d) != "slim":
            return None
        if filename == METRICS_NAME:
            if self.reward_spec is None:
                return None  # 缺 spec ⇒ P1 不做（照旧带 metrics，逐字节旧行为）
            return DROP_MEMBER  # 换成 reward.npy（见 shard_extra）
        if filename in F16_FILES:
            raw = self._f16_bytes(d, filename)
            if raw is not None:
                return raw
        return None

    def shard_extra(self, shard_dir: Path) -> dict[str, bytes]:
        """`pack_payload` 的每 shard 额外成员：`reward.npy` + `reward_meta.json`。

        只对**盘上尚不存在**的名字产出（`reward.npy` 已在盘上的目录在 `_kind_of` 就被判
        `skip:already`）⇒ 不会与磁盘成员撞 arcname（`pack_payload` 另有同名硬拦）。
        """
        d = Path(shard_dir)
        if self._kind_of(d) != "slim" or self.reward_spec is None:
            return {}
        self.n_reward_dirs += 1
        return {
            REWARD_NAME: self._reward_bytes(d),
            REWARD_META_NAME: _json_bytes(
                {
                    "reward_version": REWARD_VERSION,
                    "it": self.it,
                    "metrics_version": self.metrics_version,
                    "formula_hash": self.formula_hash,
                }
            ),
        }

    # ---------------------------------------------------------------- 变换本体
    def _f16_bytes(self, d: Path, filename: str) -> bytes | None:
        """该字段 → f16 的 npy 字节；不该动/动不了（非 f32）返回 None（= 原样）。"""
        p = d / filename
        try:
            arr = np.load(p, mmap_mode="r")
        except (OSError, ValueError) as e:
            raise ProtocolError(f"payload_slim: {p} 读不动（{type(e).__name__}: {e}）") from e
        if arr.dtype != np.float32:
            # 不是 f32（或已是 f16）⇒ 不动。静默改别人的 dtype 会造出「表里没有的形态」。
            return None
        out = _npy_bytes(np.asarray(arr, dtype=np.float16))
        self.n_f16_files += 1
        if d.name not in self._f16_dirs:
            self._f16_dirs.add(d.name)
            self.n_f16_dirs += 1
        return out

    def _reward_bytes(self, d: Path) -> bytes:
        """`metrics.npy` → reward（f32, (N,)）的 npy 字节。

        判据与云端 `_reward_from_metrics` 同源：同一 `reward_from_spec`（包住同一编译结果）、
        同一 `outcome`/`score` 缺省。**顺带把两件今天只在上云后才查得出来的事挪到打包前**：
        列宽 == `METRICS_DIM`（版本漂移）与 metrics 行数 == obs 行数 + 1（行失配）。
        """
        dp = d / METRICS_NAME
        try:
            metrics = np.load(dp)
        except (OSError, ValueError) as e:
            raise ProtocolError(f"payload_slim: {dp} 读不动（{type(e).__name__}: {e}）") from e
        manifest = _read_manifest(d)
        got_mver = manifest.get("metrics_version")
        if (
            self.metrics_version > 0
            and got_mver is not None
            and int(got_mver) != self.metrics_version
        ):
            raise ProtocolError(
                f"{d.name}: shard metrics_version={int(got_mver)} != 本轮 {self.metrics_version}"
                "——产出端导出器与读取端版本不同（发布端本应已剔除，见 hub_client.iter_shard_dirs）"
            )
        if metrics.ndim != 2 or metrics.shape[1] != _metrics_dim():
            raise ProtocolError(
                f"{d.name}: metrics 形状应为 [N+1,{_metrics_dim()}]，收到 {tuple(metrics.shape)}"
                "——写盘端行构造与列常量脱钩（同一版本内的 bug），拒绝打包"
            )
        n_obs = _obs_rows(d)
        if n_obs is not None and metrics.shape[0] != n_obs + 1:
            raise ProtocolError(
                f"{d.name}: metrics 行数 {metrics.shape[0]} != nSamples+1={n_obs + 1}"
                "——指标行失配（决策行 + 终局行），拒绝打包"
            )
        from biz.reward_library import reward_from_spec

        spec = self.reward_spec
        if spec is None:
            # 不变量：`shard_extra` / `member_bytes` 只在 spec 非空时才产出 reward 成员。
            # 写死一句挡在这里，比让 `reward_from_spec(None, …)` 去报一个看不懂的 TypeError 好。
            raise ProtocolError(f"{d.name}: 无 reward_spec 却走到 P1 打包（不变量破裂）")
        outcome = str(manifest.get("outcome", "timeout"))
        score = float(manifest.get("score", 0.0))
        r = np.asarray(reward_from_spec(spec, metrics, outcome, score, self.it), dtype=np.float32)
        if r.ndim != 1 or r.shape[0] != metrics.shape[0] - 1:
            raise ProtocolError(
                f"{d.name}: reward 形状 {tuple(r.shape)} != ({metrics.shape[0] - 1},)"
                "——公式产出的样本数与 metrics 行数不符"
            )
        return _npy_bytes(r)

    # ---------------------------------------------------------------- 报表
    @property
    def fmt(self) -> str:
        """本轮 payload 形态自述（进 job manifest 的 `payload_fmt`）：`reward+f16` / `f16` / 空。"""
        parts: list[str] = []
        if self.n_reward_dirs:
            parts.append("reward")
        if self.n_f16_files:
            parts.append("f16")
        return "+".join(parts)

    def fmt_line(self) -> str:
        """发布日志那一行。取名刻意避开 `summary`（本仓已有两处同名函数，再加一份就顶
        `tests/test_reuse_ledger.py` 的「同名 ≥3 模块」基线 —— 那个计数只许降）。"""
        return (
            f"wire-diet: fmt={self.fmt or '（未改）'} reward={self.n_reward_dirs} shard"
            f" f16={self.n_f16_files} 字段/{self.n_f16_dirs} shard"
            f" 未触及={self.n_untouched_dirs} shard"
        )


def _read_manifest(d: Path) -> dict:
    mp = d / "manifest.json"
    try:
        with open(mp, encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError) as e:
        raise ProtocolError(f"payload_slim: {mp} 不可读（{type(e).__name__}: {e}）") from e
    if not isinstance(got, dict):
        raise ProtocolError(f"payload_slim: {mp} 不是对象（{type(got).__name__}）")
    return got


def _obs_rows(d: Path) -> int | None:
    """`obs.npy` 的行数（mmap 只读头部，不读数据）。缺 obs.npy 时返回 None（不做交叉校验）。"""
    p = d / "obs.npy"
    if not p.exists():
        return None
    try:
        arr = np.load(p, mmap_mode="r")
    except (OSError, ValueError):
        return None
    if arr.ndim < 1:
        return None
    return int(arr.shape[0])


def build_slim(
    *,
    reward_spec: Any | None = None,
    it: int = 0,
    metrics_version: int = 0,
    formula_hash: str = "",
    log: Callable[[str], None] = lambda _m: None,
) -> ShardSlim:
    """`ShardSlim` 的工厂（调用方只 import 它 —— 留出以后换实现的口）。

    `formula_hash` 缺省时从 `reward_spec.identity()` 现取（发布端传的是 job manifest 的值，
    两者同源；空指纹会让节点侧的校验无从比对 ⇒ 取不到就**不做 P1**，宁可不省字节也不发
    一份无法判定血缘的 reward）。
    """
    if reward_spec is not None and not formula_hash:
        try:
            formula_hash = str(reward_spec.identity())
        except Exception as e:  # spec 无 identity()（旧形态）⇒ 降级不做 P1（不是静默：有一行 log）
            log(f"wire-diet: reward_spec 取不到 identity（{type(e).__name__}: {e}）⇒ 只做 P2")
            reward_spec = None
    if reward_spec is None:
        log("wire-diet: 无 reward_spec ⇒ 只做 P2（f16），P1 退回 metrics.npy（逐字节旧行为）")
    return ShardSlim(
        reward_spec=reward_spec,
        it=it,
        metrics_version=metrics_version,
        formula_hash=formula_hash,
        log=log,
    )


__all__ = ["F16_FILES", "ShardSlim", "build_slim"]
