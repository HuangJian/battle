"""BC 语料采集编排（从 `trainer/bc_loop.py` 拆出，plan §16.9）。

本模块只负责「一轮语料怎么补齐」：断点续跑过滤、派发、ratio 裁剪、fixed 校验。
发布/训练/账本轮指针仍归 `trainer/bc_loop.py`；拆出是为了单文件 LOC 预算。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.log import log
from worker.bc_config import BcCourseConfig, round_seeds
from worker.bc_corpus import (
    OUTCOME_CLASSES,
    apply_corpus_policy,
    load_shards,
    validate_fixed_corpus,
)
from worker.bc_dispatch import BcDispatchError, BcDispatchSpec, dispatch_bc_corpus, landed_pairs
from worker.bc_ledger import append_ledger


def smoke_overrides(course: BcCourseConfig) -> dict:
    """--smoke 尺寸压缩：1 局 / max_ticks≤300 / epochs=1（真一轮，分钟级内）。

    wins_only 同步关闭：300 tick 内不可能 stage_clear，wins-only 会把唯一一局也
    过滤掉（实测教训 2026-09-13）——冒烟要的是全链打通，不是语料质量。"""
    return {
        "games_per_stage": 1,
        "max_ticks": min(int(course.max_ticks), 300),
        "epochs": 1,
        "batch": min(int(course.train.batch), 256),
        "wins_only": False,
        "near_miss_times": 1,
    }


@dataclass(frozen=True)
class CollectSpec:
    """`collect_corpus` 的旋钮袋（P0-3：9 参 → 一个对象）。"""

    course: BcCourseConfig
    course_fp: str
    corpus_fp: str
    it: int
    data_round_dir: Path
    smoke: bool
    cfg: dict | None
    jsonl_path: Path | None = None
    log: Callable[[str], None] = log


def collect_corpus(spec: CollectSpec) -> Path:
    """补齐本轮语料并返回训练根（collect = 轮目录；fixed = 现成语料根）。

    smoke 轮**不做断点续跑**（todo = 全量重采）——smoke 覆盖（wins_only/max_ticks）
    与真跑口径不同，复用盘上 shard 会拿冒烟语料冒充真语料（2026-09-13 实测：
    smoke 的 timeout shard 落进 it1 被真跑复用，语料污染）。

    `fixed` 不派发、不改写语料；只校验 v6 形状并记 `data_fp`。ratio 在派发完成后
    统一裁剪（必须看到整轮 outcome 才能算配额），未选中的 shard 留 manifest 作
    skip marker。

    四段各自成函数：fixed 分支（`_fixed_corpus`）· 全已齐（`_complete_corpus`）·
    派发（`_dispatch_corpus`）· ratio 落账（`_apply_policy`，两条出口共用）。
    """
    course = spec.course
    if course.corpus.source == "fixed":
        return _fixed_corpus(spec)
    plan = _collect_plan(course, spec.it, spec.data_round_dir, spec.smoke)
    if not plan.todo:
        return _complete_corpus(spec, plan)
    label_policy = course.label_policy_file
    if course.resolved_action_v6 and (
        label_policy is None or not label_policy.is_file()
    ):
        raise BcDispatchError(
            f"it{spec.it}: action_v6 采集缺标签策略权重 corpus.label_policy="
            f"{course.corpus.label_policy!r}（解析为 {label_policy}）——拒采"
        )
    spec.log(
        f"[run_bc] it{spec.it}: 需采 {len(plan.todo)} 局（已有 {len(plan.have)}）"
        f"stages={plan.stages} seeds={plan.seeds[0]}..{plan.seeds[-1]} "
        f"source={course.corpus.source} policy={plan.policy} wins_only={plan.wins_only}"
    )
    _dispatch_corpus(spec, plan, label_policy)
    _apply_policy(spec, plan)
    return spec.data_round_dir


@dataclass(frozen=True)
class _CollectPlan:
    """一轮语料的待采面（种子段 / 目标集 / 已落盘 / 配额策略）；不含任何写点。"""

    seeds: list[int]
    stages: list[int]
    have: set[tuple[int, int]]
    todo: list[tuple[int, int]]
    policy: str
    max_ticks: int
    wins_only: bool
    near_miss: int


def _fixed_corpus(spec: CollectSpec) -> Path:
    """fixed 源：只校验 + 记 `data_fp`（不派发、不改写语料）。"""
    course = spec.course
    raw = str(course.corpus.fixed_dir or "").strip()
    fixed_dir = Path(raw) if raw else spec.data_round_dir.parent
    data_fp = validate_fixed_corpus(fixed_dir, action_v6=course.resolved_action_v6)
    spec.log(
        f"[run_bc] it{spec.it}: fixed 语料 {fixed_dir}（data_fp={data_fp[:12]}…）——跳过采集"
    )
    if spec.jsonl_path is not None:
        shards = load_shards(fixed_dir)
        collected = {k: sum(1 for s in shards if s.klass == k) for k in OUTCOME_CLASSES}
        total = sum(collected.values())
        append_ledger(
            spec.jsonl_path,
            {
                "event": "bc_corpus_mix",
                "it": int(spec.it),
                "source": "fixed",
                "policy": "all",
                "scope": "stage",
                "target": None,
                "collected": collected,
                "kept": dict(collected),
                "actual": {
                    k: (collected[k] / total if total else 0.0) for k in OUTCOME_CLASSES
                },
                "infeasible": [],
                "data_fp": data_fp,
            },
        )
    return fixed_dir


def _collect_plan(
    course: BcCourseConfig, it: int, data_round_dir: Path, smoke: bool
) -> _CollectPlan:
    """种子段 × 关 → 目标集；smoke 改覆盖并**全量重采**，否则剔除已落盘。"""
    seeds = round_seeds(course, it)
    stages = course.stage_ids
    want = {(s, seed) for s in stages for seed in seeds}
    have = landed_pairs(data_round_dir)
    max_ticks = int(course.max_ticks)
    wins_only = bool(course.corpus.wins_only)
    near_miss = int(course.corpus.near_miss_times)
    policy = "all" if smoke else str(course.corpus.outcome_policy)
    if smoke:
        ov = smoke_overrides(course)
        max_ticks = ov["max_ticks"]
        wins_only = ov["wins_only"]
        near_miss = ov["near_miss_times"]
        seeds = seeds[:1]
        want = {(s, seeds[0]) for s in stages}
        todo = sorted(want)  # smoke 全量重采（不复用盘上 shard，见 docstring）
        policy = "all"
    else:
        todo = sorted(want - have)
    return _CollectPlan(
        seeds=seeds,
        stages=stages,
        have=have,
        todo=todo,
        policy=policy,
        max_ticks=max_ticks,
        wins_only=wins_only,
        near_miss=near_miss,
    )


def _complete_corpus(spec: CollectSpec, plan: _CollectPlan) -> Path:
    """没有待采：要么响亮拒（空配置 + 空盘），要么按策略裁剪后直接返回轮目录。"""
    course = spec.course
    if not plan.have:
        # 2026-11-21 g8-god-bc 现场：collect + games_per_stage=0 ⇒ want 空 ⇒
        # todo 空且 have 空。旧行为打"语料已齐（0 shards）"后继续发布，
        # 发布端扫不到 shard ⇒ 课被 abort 而日志全文看不出真因。这里是配置
        # 错误的第一现场，必须在进入发布链之前响亮拒，绝不静默放行。
        raise BcDispatchError(
            f"it{spec.it}: collect 模式既无待采（games_per_stage="
            f"{course.corpus.games_per_stage}）又无落盘 shard（{spec.data_round_dir}）"
            "——不可能的合法状态；读现成语料请配 corpus.source=fixed + fixed_dir，"
            "别留默认 collect"
        )
    spec.log(
        f"[run_bc] it{spec.it}: 语料已齐（{len(plan.have)} shards on disk）— 跳过采集"
    )
    _apply_policy(spec, plan)
    return spec.data_round_dir


def _dispatch_corpus(spec: CollectSpec, plan: _CollectPlan, label_policy: Path | None) -> None:
    """按待采集派发（失败任务数 > 0 即响亮拒，重跑靠断点续跑补齐）。"""
    course = spec.course
    sj_for = {s: course.custom_stage_payload(s) for s in plan.stages}
    stats = dispatch_bc_corpus(
        BcDispatchSpec(
            tasks=plan.todo,
            out_dir=spec.data_round_dir,
            it=spec.it,
            corpus_fp=spec.corpus_fp,
            course_fp=spec.course_fp,
            difficulty=course.difficulty,
            max_ticks=plan.max_ticks,
            stage_json_for=lambda s: sj_for.get(s),
            lives_override=course.player.lives,
            player_level=course.player.level,
            wins_only=plan.wins_only,
            near_miss_times=plan.near_miss,
            cfg=spec.cfg,
            task_timeout_sec=max(120.0, float(plan.max_ticks) * 0.5 + 120.0),
            iter_suffix="-smoke" if spec.smoke else "",
            action_v6=bool(course.resolved_action_v6),
            label_policy_path=str(label_policy) if label_policy is not None else "",
            label_mode=str(course.corpus.label_mode),
            log=spec.log,
        )
    )
    if stats["failed"] > 0:
        tripped = stats.get("failed_nodes") or []
        raise BcDispatchError(
            f"it{spec.it}: {stats['failed']} 个语料任务彻底失败（重跑 run_bc 断点续跑补齐）"
            + (
                f"；已熔断节点：{'、'.join(tripped)}——先 /v1/ping 看该节点 codeHash"
                " 与节点侧日志，再决定 /v1/update 或停用"
                if tripped
                else ""
            )
        )


def _apply_policy(spec: CollectSpec, plan: _CollectPlan) -> None:
    """ratio / 配额裁剪（必须在看到整轮 outcome 之后；两条出口共用一份落账）。"""
    if plan.wins_only:
        return
    event = apply_corpus_policy(
        spec.data_round_dir,
        policy=plan.policy,
        corpus_fp=spec.corpus_fp,
        it=spec.it,
        target=spec.course.corpus.target,
        scope=str(spec.course.corpus.scope),
        source="collect",
        log=spec.log,
    )
    if spec.jsonl_path is not None:
        append_ledger(spec.jsonl_path, event)
