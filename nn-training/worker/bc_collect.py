"""BC 语料采集编排（从 `trainer/bc_loop.py` 拆出，plan §16.9）。

本模块只负责「一轮语料怎么补齐」：断点续跑过滤、派发、ratio 裁剪、fixed 校验。
发布/训练/账本轮指针仍归 `trainer/bc_loop.py`；拆出是为了单文件 LOC 预算。
"""

from __future__ import annotations

from pathlib import Path

from common.log import log
from worker.bc_config import BcCourseConfig, round_seeds
from worker.bc_corpus import (
    OUTCOME_CLASSES,
    apply_corpus_policy,
    load_shards,
    validate_fixed_corpus,
)
from worker.bc_dispatch import BcDispatchError, dispatch_bc_corpus, landed_pairs
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


def collect_corpus(
    course: BcCourseConfig,
    course_fp: str,
    corpus_fp: str,
    it: int,
    data_round_dir: Path,
    *,
    smoke: bool,
    cfg: dict | None,
    jsonl_path: Path | None = None,
    log=log,
) -> Path:
    """补齐本轮语料并返回训练根（collect = 轮目录；fixed = 现成语料根）。

    smoke 轮**不做断点续跑**（todo = 全量重采）——smoke 覆盖（wins_only/max_ticks）
    与真跑口径不同，复用盘上 shard 会拿冒烟语料冒充真语料（2026-09-13 实测：
    smoke 的 timeout shard 落进 it1 被真跑复用，语料污染）。

    `fixed` 不派发、不改写语料；只校验 v6 形状并记 `data_fp`。ratio 在派发完成后
    统一裁剪（必须看到整轮 outcome 才能算配额），未选中的 shard 留 manifest 作
    skip marker。"""
    if course.corpus.source == "fixed":
        raw = str(course.corpus.fixed_dir or "").strip()
        fixed_dir = Path(raw) if raw else data_round_dir.parent
        data_fp = validate_fixed_corpus(fixed_dir, action_v6=course.resolved_action_v6)
        log(f"[run_bc] it{it}: fixed 语料 {fixed_dir}（data_fp={data_fp[:12]}…）——跳过采集")
        if jsonl_path is not None:
            shards = load_shards(fixed_dir)
            collected = {
                k: sum(1 for s in shards if s.klass == k) for k in OUTCOME_CLASSES
            }
            total = sum(collected.values())
            append_ledger(
                jsonl_path,
                {
                    "event": "bc_corpus_mix",
                    "it": int(it),
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
    if not todo:
        log(f"[run_bc] it{it}: 语料已齐（{len(have)} shards on disk）— 跳过采集")
        if not wins_only:
            event = apply_corpus_policy(
                data_round_dir,
                policy=policy,
                corpus_fp=corpus_fp,
                it=it,
                target=course.corpus.target,
                scope=str(course.corpus.scope),
                source="collect",
                log=log,
            )
            if jsonl_path is not None:
                append_ledger(jsonl_path, event)
        return data_round_dir
    label_policy = course.label_policy_file
    if course.resolved_action_v6 and todo and (
        label_policy is None or not label_policy.is_file()
    ):
        raise BcDispatchError(
            f"it{it}: action_v6 采集缺标签策略权重 corpus.label_policy="
            f"{course.corpus.label_policy!r}（解析为 {label_policy}）——拒采"
        )
    log(
        f"[run_bc] it{it}: 需采 {len(todo)} 局（已有 {len(have)}）stages={stages} "
        f"seeds={seeds[0]}..{seeds[-1]} source={course.corpus.source} "
        f"policy={policy} wins_only={wins_only}"
    )
    sj_for = {s: course.custom_stage_payload(s) for s in stages}
    stats = dispatch_bc_corpus(
        tasks=todo,
        out_dir=data_round_dir,
        it=it,
        corpus_fp=corpus_fp,
        course_fp=course_fp,
        difficulty=course.difficulty,
        max_ticks=max_ticks,
        stage_json_for=lambda s: sj_for.get(s),
        lives_override=course.player.lives,
        player_level=course.player.level,
        wins_only=wins_only,
        near_miss_times=near_miss,
        cfg=cfg,
        task_timeout_sec=max(120.0, float(max_ticks) * 0.5 + 120.0),
        iter_suffix="-smoke" if smoke else "",
        action_v6=bool(course.resolved_action_v6),
        label_policy_path=str(label_policy) if label_policy is not None else "",
        label_mode=str(course.corpus.label_mode),
        log=log,
    )
    if stats["failed"] > 0:
        tripped = stats.get("failed_nodes") or []
        raise BcDispatchError(
            f"it{it}: {stats['failed']} 个语料任务彻底失败（重跑 run_bc 断点续跑补齐）"
            + (
                f"；已熔断节点：{'、'.join(tripped)}——先 /v1/ping 看该节点 codeHash"
                " 与节点侧日志，再决定 /v1/update 或停用"
                if tripped
                else ""
            )
        )
    if not wins_only:
        event = apply_corpus_policy(
            data_round_dir,
            policy=policy,
            corpus_fp=corpus_fp,
            it=it,
            target=course.corpus.target,
            scope=str(course.corpus.scope),
            source="collect",
            log=log,
        )
        if jsonl_path is not None:
            append_ledger(jsonl_path, event)
    return data_round_dir
