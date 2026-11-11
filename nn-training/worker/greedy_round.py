"""greedy_round —— 云机轮内的 greedy 采集阶段（自主离线包专用）。

## 它解决什么

`trainer/loop_volume._volume_collect_greedy` 只在**本机 trainer 当编排者**的那条腿上存在。
自主云机离线包（`remote/plan_run` 驱动、`remote/worker.run_job` 在云机内闭环 rollout→PPO）
里没有 trainer ⇒ 课程写的 `greedy_quota` 从未执行，语料 100% 是采样局
（`plan/greedy-offline-autonomous.plan.md` §1 的三方取证）。

本模块把那一阶段搬到云机轮内：**rollout 之后、PPO 之前**跑若干波 `--teacher-argmax` 采集，
产出落 `<PPO 装载根>/greedy/`，于是 PPO 的递归扫描天然看到它们（`discover_shards` 是
`os.walk` + `sorted`，`greedy/` 排在 `w0/` 之前 ⇒ greedy 语料先入账）。

## 与在线腿的两处**故意**差异

1. **预算 = `plan.total_target`（挤进普通采样），不是 `collect_target = ⌈total×cap⌉`**：
   用户 2026-11-11 裁决「总量不变，greedy 占 12%」——普通采样在 `remote/plan_run` 侧已经少采
   了这份，greedy 再按 `oversample_cap` 多采就是二次挤占。`cap` 仍进账本（`greedy_quota_stats.cap`），
   只是不再当本轮上限。
2. **每波一波一 spec、独立产出根**：云机的普通轮走流式采集（`worker/stream_collect` 把 argv
   当每关模板、局由调度器现场长），往那份 spec 里追加行会被静默忽略；而且
   `run_iter_rollout` 轮末按「实产 == 声明」对账（`verify_shards`），两批混在一个根里必红。
   ⇒ greedy 自己生成 argv（模板 + `--teacher-argmax`）与自己的根。

## 口径复用

配额/分档/种子/账本键名**全部**复用在线那套纯函数与形状（`worker.greedy_quota` /
`worker.volume_alloc` / `worker.volume_quota`），不另立第二份：账本两腿同键，控制台不写分支。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from common.manifest import validate_rollout_spec
from common.protocol import ProtocolError
from worker.greedy_quota import (
    BINS,
    GREEDY_QUOTA_RULE_V1,
    bin_targets,
    greedy_progress,
    is_greedy_manifest,
    load_manifests,
    manifest_samples,
    parse_greedy_quota,
    report_brief,
)
from worker.reports import combine_reports
from worker.rollout_argv import flag_value, retarget_argv
from worker.volume_alloc import default_game_caps, pool_games_by_stage, spread_games_by_stage
from worker.volume_quota import DEFAULT_MAX_BATCHES, continuous_pairs

#: greedy 产出根在 PPO 装载根下的目录名（`discover_shards` 按路径排序 ⇒ 它排在 `w0/` 之前）。
GREEDY_DIR_NAME = "greedy"
#: 导出器的贪心开关（`tools/sim/export-rl-rollout.ts:2238`；manifest 随之带 `labelMode="greedy"`）。
TEACHER_ARGMAX_FLAG = "--teacher-argmax"


def stage_templates(spec: Mapping[str, Any]) -> dict[int, list[str]]:
    """每关一条 argv 模板（同一关多条时取第一条，与 `stream_collect` 同口径）。"""
    out: dict[int, list[str]] = {}
    for row in spec.get("argv") or ():
        argv = [str(x) for x in row]
        raw = flag_value(argv, "--stages") or ""
        try:
            stage = int(raw)
        except (TypeError, ValueError):
            continue
        out.setdefault(stage, argv)
    return out


def greedy_argv_rows(spec: Mapping[str, Any], pairs: Sequence[tuple[int, int]]) -> list[list[str]]:
    """`(stage, seed)` 序列 → greedy 的一行行 argv（模板重定向 + `--teacher-argmax`）。

    缺该关模板 ⇒ `ProtocolError`（响亮：悄悄少跑一关 = 配额永远不达标，却没有任何读数说为什么）。
    """
    templates = stage_templates(spec)
    wver = str(spec.get("wver") or "")
    rows: list[list[str]] = []
    for idx, (stage, seed) in enumerate(pairs):
        tmpl = templates.get(int(stage))
        if tmpl is None:
            raise ProtocolError(
                f"greedy 采集缺关 {stage} 的 argv 模板（本轮 spec 的 argv 里没有该关）——"
                "计划/关集与 greedy 配额不一致，拒绝静默少采"
            )
        row = retarget_argv(
            list(tmpl),
            stage=int(stage),
            seed=int(seed),
            out=f"w{idx}",
            wver=wver,
            stage_json=flag_value(tmpl, "--stage-json") or None,
        )
        if TEACHER_ARGMAX_FLAG not in row:
            row.append(TEACHER_ARGMAX_FLAG)
        rows.append(row)
    return rows


def greedy_spec(spec: Mapping[str, Any], pairs: Sequence[tuple[int, int]]) -> dict:
    """一波 greedy 的 rollout 规格（**不带** `volume`/`greedy_quota`：显式 argv，非流式）。"""
    return validate_rollout_spec(
        {
            "argv": greedy_argv_rows(spec, pairs),
            "wver": str(spec.get("wver") or ""),
            "workers": int(spec.get("workers", 1) or 0),
            "game_timeout_sec": float(spec.get("game_timeout_sec", 0.0) or 0.0),
            "bun": str(spec.get("bun") or "bun"),
        }
    )


def _stage_totals(manifests: Mapping[str, Mapping[str, Any]]) -> tuple[dict[int, int], dict[int, int]]:
    """greedy 已结算的 (逐关样本, 逐关局数)；只认 `labelMode=greedy` 的 manifest。"""
    collected: dict[int, int] = {}
    games: dict[int, int] = {}
    for mm in manifests.values():
        if not is_greedy_manifest(mm):
            continue
        raw_stage = mm.get("stage")
        if isinstance(raw_stage, bool) or not isinstance(raw_stage, (int, float, str)):
            continue
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        sample_n = manifest_samples(mm)
        games[stage] = games.get(stage, 0) + 1
        if sample_n > 0:
            collected[stage] = collected.get(stage, 0) + sample_n
    return collected, games


def run_greedy_round(
    *,
    raw_quota: object,
    round_target: int,
    stages: Sequence[int],
    it: int,
    rotate_seed: int,
    ordinary: Sequence[tuple[int, int]],
    run_wave: Callable[[list[tuple[int, int]]], Mapping[str, Any]],
    sampling_brief: Mapping[str, Any] | None = None,
    ests: Mapping[int, int] | None = None,
    width: int = 0,
    max_batches: int | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """在云机轮内跑完 greedy 采集，返回 `{shard_dirs, report, greedy_quota_stats, by_stage, waves}`。

    空 dict = 未激活（`quota_active` 为假或三档全 0）⇒ 调用方**一个键都不写**（旧包逐字节不变）。

    `run_wave(pairs)` 由调用方注入（`remote/worker` 用 `run_iter_rollout` 跑）：
    返回 `{report, shard_dirs}`；一波 = 一个独立 spec + 独立产出根，轮末对账天然隔离。

    **进度只认 `shard_dirs`，不扫盘**：换根档下 shard 住节点本地 scratch 的镜像目录，
    而调用方拿到的 job 目录里什么都没有（扫盘会看到 0 局 ⇒ 一路空转到波次上限）。
    """
    plan = parse_greedy_quota(raw_quota, target_transitions=int(round_target), stages=list(stages))
    if plan is None:
        return {}
    budget = int(plan.total_target)  # B 口径：不吃 oversample_cap（见模块头 §故意差异 1）
    targets = bin_targets(plan)
    stage_list = [int(s) for s in plan.stages]
    est_map = {int(k): int(v) for k, v in (ests or {}).items()}
    # 冷启动没有 est ⇒ `default_game_caps` 不设默认顶（与在线同：没有估计就不猜一个顶）。
    game_caps = default_game_caps(
        stage_list, budget, ests=est_map, mult=max(1, math.ceil(plan.cap))
    )
    start_idx: dict[int, int] = {s: 0 for s in stage_list}
    ordinary_set = {(int(s), int(sd)) for s, sd in ordinary}
    limit = int(max_batches or DEFAULT_MAX_BATCHES)
    waves = 0
    stopped_no_games = False
    greedy_report: dict | None = None
    produced: list[str] = []
    while waves < limit:
        dirs = [Path(d) for d in produced]
        manifests = load_manifests(dirs)
        bins, total = greedy_progress([p.name for p in dirs], manifests)
        if all(int(bins[b]) >= int(targets[b]) for b in BINS if int(targets[b]) > 0):
            break
        if total >= budget:
            break
        collected, games = _stage_totals(manifests)
        collected = {s: int(collected.get(s, 0)) for s in stage_list if s in collected}
        games = {s: int(games.get(s, 0)) for s in stage_list if s in games}
        # 重启后从已结算局数续抽，不重放已结算 seed（同在线）。
        for s in stage_list:
            start_idx[s] = max(start_idx.get(s, 0), games.get(s, 0))
        learned = {
            s: int(collected.get(s, 0) / games[s]) if games.get(s) else 0 for s in stage_list
        }
        if any(learned.values()) or est_map:
            est_dispatch = {
                s: max(1, int(learned.get(s) or est_map.get(s, 0) or 0)) for s in stage_list
            }
            plan_g = pool_games_by_stage(
                stages=stage_list,
                collected=collected,
                games_done=games,
                target_transitions=budget,
                ests=est_dispatch,
                game_caps=game_caps,
                width=0,
                mult=1.0,
            )
        else:
            plan_g = spread_games_by_stage(
                stages=stage_list,
                collected=collected,
                games_done=games,
                target_transitions=budget,
                game_caps=game_caps,
                width=max(1, int(width or len(stage_list))),
            )
        if not plan_g:
            stopped_no_games = True
            break
        pairs = continuous_pairs(int(rotate_seed) ^ plan.seed_salt, it, plan_g, start_idx)
        pairs = [(int(s), int(sd)) for s, sd in pairs if (int(s), int(sd)) not in ordinary_set]
        if not pairs:
            stopped_no_games = True
            break
        # seed 索引只按**实际派出**的局前进（被普通采样占掉的 seed 直接跳过，下一波补）。
        for stage, _seed in pairs:
            start_idx[stage] = start_idx.get(stage, 0) + 1
        waves += 1
        out = run_wave(pairs)
        new_dirs = out.get("shard_dirs") if isinstance(out, Mapping) else None
        if isinstance(new_dirs, (list, tuple)):
            produced.extend(str(d) for d in new_dirs)
        rep = out.get("report") if isinstance(out, Mapping) else None
        if isinstance(rep, Mapping):
            greedy_report = (
                dict(rep) if greedy_report is None else combine_reports([greedy_report, dict(rep)])
            )
        log(
            f"[greedy] it{it}: batch{waves} games={len(pairs)} bins={bins} "
            f"targets={targets} total={total}/{budget}"
        )
    dirs = [Path(d) for d in produced]
    manifests = load_manifests(dirs)
    names = [p.name for p in dirs]
    bins, total = greedy_progress(names, manifests)
    degraded = any(int(bins[b]) < int(targets[b]) for b in BINS if int(targets[b]) > 0)
    cap_hit = bool(degraded and total >= budget)
    by_stage: dict[str, dict[str, int]] = {}
    greedy_games = 0
    for name in names:
        mm = manifests.get(name) or {}
        if not is_greedy_manifest(mm):
            continue
        greedy_games += 1
        row = by_stage.setdefault(str(mm.get("stage")), {"samples": 0, "games": 0})
        row["samples"] += manifest_samples(mm)
        row["games"] += 1
    stats = {
        "rule": GREEDY_QUOTA_RULE_V1,
        "target": int(plan.total_target),
        "collect_target": budget,
        "cap": float(plan.cap),
        "soft": bool(plan.soft),
        "per_stage_target": int(plan.per_stage_target),
        "bins": dict(bins),
        "targets": dict(targets),
        "total": int(total),
        "degraded": bool(degraded),
        "cap_hit": cap_hit,
        "waves": int(waves),
        "stopped_no_games": bool(stopped_no_games),
        # 拆分读数：只在**采样口径**上算 est（与 trainer 同键，`worker/resume` 优先读它）。
        "sampling_report": dict(sampling_brief or {}),
        "greedy_report": report_brief(greedy_report),
    }
    log(
        f"[greedy] it{it}: 收官 bins={bins} targets={targets} total={total} "
        f"degraded={degraded} cap_hit={cap_hit} waves={waves}"
    )
    if degraded and not plan.soft:
        raise RuntimeError(
            f"greedy_quota 硬目标未达成（soft=false）：bins={bins} targets={targets} "
            f"total={total}/{budget}"
        )
    return {
        "shard_dirs": [str(p) for p in dirs if is_greedy_manifest(manifests.get(p.name) or {})],
        "report": dict(greedy_report or {}),
        "greedy_quota_stats": stats,
        "by_stage": {"samples": int(total), "games": int(greedy_games), "stages": by_stage},
        "waves": int(waves),
    }
