"""greedy 采集语料的分层预筛（plan/greedy-outcome-quota rev4）。

纯函数：不读 World、不派发、不改训练状态。输入 = shard 名 + manifest 映射 + 课程配额，
输出 = 保留的 greedy 名集合 + 统计。`loop_remote_job` 在 `_trim_shards_to_quota` 之前
调用，把 greedy 子集标成 **protected**，避免逐关 seed 序前缀把它裁掉。

**为什么按逐关选**：失败集中在 2002；全局配额可以被单一关独占。逐关
`per_stage_target` 保证每关的 greedy 占比一致（某关某档不够就留缺口，不跨关借）。

**provenance**：greedy shard 的 manifest 带 `labelMode="greedy"`（TS 只在
`--teacher-argmax` 时写）；旧 shard 无此键 = 采样语料。它比 seed 段更可靠：
进程重启后仍认得出，且兄弟复用能据此拒绝跨模式借局。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from biz.course_spec import GreedyQuotaBlock

#: 规则版本（进 `corpus_identity_fp`；改选择/种子语义 = bump + 新实验）。
#: v2：timeout 目标按实际供给只分给有 timeout 的关（win/loss 仍逐关；样本不跨关）。
GREEDY_QUOTA_RULE_V1 = 2
#: greedy 种子流的异或盐（与普通连续配额流分开；同名 shard 仍靠 seed 不相交避免去重）。
GREEDY_SEED_SALT = 0x6EED
BINS: tuple[str, ...] = ("win", "loss", "timeout")
BIN_BY_OUTCOME: dict[str, str] = {
    "stage_clear": "win",
    "lives_exhausted": "loss",
    "base_destroyed": "loss",
    "timeout": "timeout",
}


def _ceil_div(a: int, b: int) -> int:
    return -(-int(a) // int(b))


def is_greedy_manifest(mm: Mapping[str, Any] | None) -> bool:
    """manifest 是否来自 `--teacher-argmax` 的 greedy 采集。"""
    if mm is None:
        return False
    return str(mm.get("labelMode") or "") == "greedy"


def quota_active(raw: object) -> bool:
    """三档比例和 > 0 才算**激活**（三档全 0 是合法的「关闭」声明）。

    `step_rollout` 的 node 拒绝门用这个判据：只用 truthy 会把「声明了块但全 0」的
    课程也拒掉——那门课语义上就是老行为，不该连 node 腿都跑不了。
    """
    if raw is None:
        return False
    if isinstance(raw, GreedyQuotaBlock):
        return (raw.win + raw.loss + raw.timeout) > 0
    if isinstance(raw, dict):
        for b in BINS:
            v = raw.get(b, 0)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if float(v) > 0:
                return True
        return False
    return False


def manifest_samples(mm: Mapping[str, Any]) -> int:
    """manifest → 本局 transition 数（公开：采集侧的 `on_result` 账本也用它对齐口径）。

    盘上两种单局形都会出现：TS exporter 写 `nSamples`，远端/队列 path 的聚合形写
    `totalSamples`（`trainer/dispatch.py::_ensure_games`）；`samples` 是 report 聚合形
    的别名（`collect_only._on_result` 同款兜底）。`load_manifests` 只保证 JSON 可解析，
    字段值仍可能是字符串或脏值 ⇒ 这里统一兜住，不让一局脏 manifest 炸掉整轮发布。
    """
    raw = mm.get("nSamples") or mm.get("totalSamples") or mm.get("samples")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class GreedyQuotaPlan:
    """课程声明的 greedy 结果配额，已换算成 transition 预算。"""

    quotas: dict[str, float]
    total_target: int
    collect_target: int
    cap: float
    soft: bool
    stages: tuple[int, ...]
    per_stage_target: int
    per_stage_collect: int
    per_stage_bin_target: dict[str, int]
    seed_salt: int = GREEDY_SEED_SALT

    @property
    def enabled(self) -> bool:
        return self.total_target > 0


def _split_per_stage(per_stage: int, quotas: Mapping[str, float]) -> dict[str, int]:
    """逐关预算 → 逐档预算（最大余数法，保证和 == per_stage）。"""
    total = sum(float(quotas.get(b, 0.0)) for b in BINS)
    if per_stage <= 0 or total <= 0:
        return {b: 0 for b in BINS}
    raw = {b: per_stage * float(quotas.get(b, 0.0)) / total for b in BINS}
    out = {b: math.floor(raw[b]) for b in BINS}
    left = int(per_stage) - sum(out.values())
    order = sorted(BINS, key=lambda b: (-(raw[b] - out[b]), b))
    for b in order[: max(0, left)]:
        out[b] += 1
    return out


def parse_greedy_quota(
    raw: object,
    *,
    target_transitions: int,
    stages: Sequence[int],
) -> GreedyQuotaPlan | None:
    """课程块 → 采集/预筛预算；`None` = 未激活或三档全 0。"""
    if raw is None:
        return None
    if isinstance(raw, GreedyQuotaBlock):
        block = raw
    elif isinstance(raw, dict):
        block = GreedyQuotaBlock.model_validate(raw)
    else:
        raise ValueError(f"greedy_quota 必须是对象，收到 {type(raw).__name__}")
    total_ratio = float(block.win + block.loss + block.timeout)
    if total_ratio <= 0:
        return None
    target = int(target_transitions)
    if target <= 0:
        raise ValueError("greedy_quota 需要 target_transitions > 0（分母是每轮总 transition）")
    stage_list = tuple(int(s) for s in stages)
    if not stage_list:
        return None
    total_target = round(total_ratio * target)
    if total_target <= 0:
        return None
    cap = float(block.oversample_cap)
    collect_target = math.ceil(total_target * cap)
    per_stage_target = _ceil_div(total_target, len(stage_list))
    per_stage_collect = _ceil_div(collect_target, len(stage_list))
    quotas = {
        "win": float(block.win),
        "loss": float(block.loss),
        "timeout": float(block.timeout),
    }
    return GreedyQuotaPlan(
        quotas=quotas,
        total_target=total_target,
        collect_target=collect_target,
        cap=cap,
        soft=bool(block.soft),
        stages=stage_list,
        per_stage_target=per_stage_target,
        per_stage_collect=per_stage_collect,
        per_stage_bin_target=_split_per_stage(per_stage_target, quotas),
        seed_salt=GREEDY_SEED_SALT,
    )


def bin_targets(plan: GreedyQuotaPlan) -> dict[str, int]:
    """全局三档目标 `T_d`（仅统计/日志口径；逐关目标在 `plan.per_stage_bin_target`）。"""
    total = sum(plan.quotas.values())
    if total <= 0:
        return {b: 0 for b in BINS}
    return {
        b: round(plan.total_target * plan.quotas[b] / total) for b in BINS
    }


def load_manifests(
    shard_dirs: Sequence[str | Path],
    mem: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict]:
    """shard 目录 → `name -> manifest`；缺/坏 manifest 跳过（少一局不炸整轮）。"""
    out: dict[str, dict] = {}
    for d in shard_dirs:
        p = Path(d)
        mm = (mem or {}).get(p.name)
        if mm is None:
            try:
                raw = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            mm = raw if isinstance(raw, Mapping) else None
        if isinstance(mm, Mapping):
            out[p.name] = dict(mm)
    return out


def _walk_shard_dirs(root: Path) -> list[Path]:
    """递归列 `rl_s*_seed*` 目录；`os.walk` 容忍 dispatch 退役目录的并发删除。

    不能 import `worker.resume.walk_shard_dirs`：两者同为 L0，顶层依赖必须严格
    向下（`tests/test_remote_dag.py`）。这里是 greedy 专用的最小副本。
    """
    out: list[Path] = []
    for dirpath, _dirnames, _filenames in os.walk(root, followlinks=True):
        if fnmatch.fnmatch(os.path.basename(dirpath), "rl_s*_seed*"):
            out.append(Path(dirpath))
    return out


def shard_dirs_for_round(
    traj_dir: str | Path,
    it: int,
    mem: Mapping[str, Any] | None = None,
) -> list[Path]:
    """本轮 shard 目录（内存账 ∪ 盘上 `itN/**/rl_s*_seed*`），按 shard 名去重。

    **必须递归**：真实采集布局是 `itN/w<wave>/rl_s*_seed*`（`export-rl-rollout --out`
    指向波目录），`itN` 的直接子目录只有 `w0/w1/...`。只 glob 一层 ⇒ 每波都看不到
    已结算局，`start_idx` 不前进、采集一路烧到波次上限（2026-11-09 S3 实测 12 波
    过采 2.1 倍）。`walk_shard_dirs` 同时容忍 dispatch 退役目录的并发删除。

    内存账里的 shard 可能不落盘（零落盘路径）⇒ 给一个名义路径；调用方读 manifest
    时优先查 `mem`，不会碰这个路径。
    """
    by_name: dict[str, Path] = {}
    it_dir = Path(traj_dir) / f"it{it}"
    if it_dir.is_dir():
        for p in _walk_shard_dirs(it_dir):
            by_name.setdefault(p.name, p)
    for name in mem or {}:
        by_name.setdefault(name, it_dir / name)
    return [by_name[name] for name in sorted(by_name)]


def shard_names_for_round(
    traj_dir: str | Path,
    it: int,
    mem: Mapping[str, Any] | None = None,
) -> list[str]:
    """本轮 shard 名（`shard_dirs_for_round` 的名字视图）。"""
    return [p.name for p in shard_dirs_for_round(traj_dir, it, mem)]


def greedy_progress(
    shard_names: Iterable[str],
    manifests: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, int], int]:
    """已结算 greedy transitions 的三档总量 + 总和。"""
    bins = {b: 0 for b in BINS}
    total = 0
    for name in shard_names:
        mm = manifests.get(name)
        if mm is None or not is_greedy_manifest(mm):
            continue
        n = manifest_samples(mm)
        if n <= 0:
            continue
        b = BIN_BY_OUTCOME.get(str(mm.get("outcome") or ""))
        if b is None:
            continue
        bins[b] += n
        total += n
    return bins, total


@dataclass
class GreedySelection:
    """预筛结果：`keep` = 保住/标记 protected 的 greedy 名；`drop` = 多余 greedy 名。"""

    keep: set[str] = field(default_factory=set)
    drop: set[str] = field(default_factory=set)
    stats: dict[str, Any] = field(default_factory=dict)


def _hash_key(it: int, name: str) -> str:
    """确定性随机序（不吃 wall clock / Math.random；同轮同输入两遍一致）。"""
    return hashlib.sha256(f"{int(it)}:{name}".encode()).hexdigest()


def _timeout_targets_by_supply(
    stages: Sequence[int],
    candidates: Mapping[tuple[int, str], Sequence[tuple[str, int]]],
    total_target: int,
) -> dict[int, int]:
    """全局 timeout 目标 → 逐关目标（只分给实际产出 timeout 的关）。

    timeout 是**环境结构**决定的（S0：只来自 Steel Fortress；fast 另有少量 Brickworks），
    不是每关都有的结果。按逐关等额分 target 会给 s0/s1 派一个永远填不上的配额，
    把真实 timeout 压到目标的 25%–39%。这里按已结算 shard 的 timeout 供给比例分配；
    **shard 本身仍留在原关**，不跨关借样本。没有任何关有 timeout ⇒ 全 0（留缺口）。
    """
    supply = {
        int(stage): sum(int(n) for _name, n in candidates.get((int(stage), "timeout"), ()))
        for stage in stages
    }
    total_supply = sum(supply.values())
    target = max(0, int(total_target))
    if target <= 0 or total_supply <= 0:
        return {int(s): 0 for s in stages}
    raw = {s: target * n / total_supply for s, n in supply.items()}
    out = {s: min(supply[s], math.floor(raw[s])) for s in supply}
    left = target - sum(out.values())
    # 最大余数法；同余数按 stage 升序（确定性）。供不应求时 budget 用不完 = 缺口。
    order = sorted(supply, key=lambda s: (-(raw[s] - math.floor(raw[s])), s))
    while left > 0:
        progressed = False
        for s in order:
            if out[s] >= supply[s]:
                continue
            out[s] += 1
            left -= 1
            progressed = True
            if left == 0:
                break
        if not progressed:
            break
    return out


def select_greedy_shards(
    shard_names: Sequence[str],
    manifests: Mapping[str, Mapping[str, Any]],
    plan: GreedyQuotaPlan,
    *,
    it: int = 0,
) -> GreedySelection:
    """按 outcome 分桶、逐关选 greedy；非 greedy shard 一律不动。"""
    candidates: dict[tuple[int, str], list[tuple[str, int]]] = {}
    bins = {b: 0 for b in BINS}
    for name in shard_names:
        mm = manifests.get(name)
        if mm is None or not is_greedy_manifest(mm):
            continue
        raw_stage = mm.get("stage")
        if not isinstance(raw_stage, (int, float, str)):
            continue
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        if stage not in plan.stages:
            # 轮换后残留的异关 shard：不进本轮语料（最后统一进 drop）。
            continue
        b = BIN_BY_OUTCOME.get(str(mm.get("outcome") or ""))
        n = manifest_samples(mm)
        if b is None or n <= 0:
            continue
        bins[b] += n
        candidates.setdefault((stage, b), []).append((name, n))

    keep: set[str] = set()
    drop: set[str] = set()
    timeout_targets = _timeout_targets_by_supply(
        plan.stages, candidates, bin_targets(plan)["timeout"]
    )
    per_stage_bin_kept: dict[str, dict[str, int]] = {}
    for (stage, b), rows in sorted(candidates.items()):
        rows.sort(key=lambda t: _hash_key(it, t[0]))
        target = (
            int(timeout_targets.get(stage, 0))
            if b == "timeout"
            else int(plan.per_stage_bin_target.get(b, 0))
        )
        acc = 0
        for name, n in rows:
            if acc >= target:
                drop.add(name)
                continue
            keep.add(name)
            acc += n
        per_stage_bin_kept.setdefault(str(stage), {})[b] = acc

    # 未知 stage/outcome 的 greedy shard 不留在训练集里（宁可少，不可无界）。
    for name in shard_names:
        if is_greedy_manifest(manifests.get(name)) and name not in keep:
            drop.add(name)

    kept_samples = 0
    for name in keep:
        mm = manifests.get(name) or {}
        kept_samples += manifest_samples(mm)
    stats = {
        "rule": GREEDY_QUOTA_RULE_V1,
        "target": int(plan.total_target),
        "collect_target": int(plan.collect_target),
        "cap": float(plan.cap),
        "soft": bool(plan.soft),
        "per_stage_target": int(plan.per_stage_target),
        "per_stage_bin_target": dict(plan.per_stage_bin_target),
        "timeout_targets": {str(s): int(t) for s, t in timeout_targets.items()},
        "bin_targets": bin_targets(plan),
        "bins": dict(bins),
        "kept": len(keep),
        "kept_samples": int(kept_samples),
        "dropped": len(drop),
        "per_stage_bin_kept": per_stage_bin_kept,
    }
    return GreedySelection(keep=keep, drop=drop, stats=stats)


def merge_greedy_stats(
    collect: Mapping[str, Any] | None,
    prefilter: Mapping[str, Any],
) -> dict[str, Any]:
    """发布阶段合并两处读数：采集留痕不丢，预筛读数挂 `prefilter` 子键。

    `_remote_ppo_publish` 在 `step_rollout` **之后**、`record_iteration` **之前**跑，
    两处事件读的是同一个 `_greedy_stats` 槽位。直接覆盖会让 iteration 事件丢掉
    `degraded` / `cap_hit` / `waves` / `targets`（plan rev4 §5.2 的留痕硬要求）。
    """
    out = dict(collect or {})
    out["prefilter"] = dict(prefilter)
    return out
