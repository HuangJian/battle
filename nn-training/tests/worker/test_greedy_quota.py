"""greedy 结果配额：纯预筛（plan/greedy-outcome-quota rev4 §5.2/§7.7）。

钉住：

- 预算换算（`T_g` / `collect_target` / 逐关 `per_stage_bin_target`）；
- **逐关**选择：某关某档不够就留缺口，不跨关借（失败集中的 2002 不能独占全局）；
- 三档映射与 `labelMode` provenance；非 greedy shard 一律不动；
- 同一轮同输入两遍逐字节相同（确定性哈希序，不吃 wall clock）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biz.corpus_fp import GREEDY_QUOTA_RULE_V1 as IDENTITY_RULE
from biz.course_spec import GreedyQuotaBlock
from worker.greedy_quota import (
    GREEDY_QUOTA_RULE_V1,
    bin_targets,
    greedy_progress,
    manifest_samples,
    merge_greedy_stats,
    parse_greedy_quota,
    quota_active,
    select_greedy_shards,
)


def _mm(
    stage: int,
    seed: int,
    outcome: str,
    n: int,
    *,
    greedy: bool = True,
) -> dict:
    mm = {"stage": stage, "seed": seed, "outcome": outcome, "nSamples": n}
    if greedy:
        mm["labelMode"] = "greedy"
    return mm


def _plan(stages: list[int], *, target: int = 1000):
    plan = parse_greedy_quota(
        {"win": 0.2, "loss": 0.0, "timeout": 0.2, "oversample_cap": 2.0, "soft": True},
        target_transitions=target,
        stages=stages,
    )
    assert plan is not None
    return plan


def test_budget_math_and_disabled_paths() -> None:
    plan = _plan([2000, 2001])
    assert plan.total_target == 400  # 0.4 × 1000
    assert plan.collect_target == 800  # cap 2
    assert plan.per_stage_target == 200
    assert plan.per_stage_collect == 400
    assert plan.per_stage_bin_target == {"win": 100, "loss": 0, "timeout": 100}
    assert bin_targets(plan) == {"win": 200, "loss": 0, "timeout": 200}

    assert parse_greedy_quota(None, target_transitions=1000, stages=[2000]) is None
    assert (
        parse_greedy_quota(
            {"win": 0, "loss": 0, "timeout": 0},
            target_transitions=1000,
            stages=[2000],
        )
        is None
    )


def _selection_fixture():
    names: list[str] = []
    mm: dict[str, dict] = {}
    for stage in (2000, 2001):
        for i in range(6):
            name = f"rl_s{stage}_seed{i}"
            names.append(name)
            mm[name] = _mm(stage, i, "stage_clear", 40)
        for i in range(6, 12):
            name = f"rl_s{stage}_seed{i}"
            names.append(name)
            mm[name] = _mm(stage, i, "timeout", 100)
    ordinary = "rl_s2000_seed99"
    names.append(ordinary)
    mm[ordinary] = _mm(2000, 99, "stage_clear", 1000, greedy=False)
    return names, mm


def test_selects_per_stage_and_per_bin_without_borrowing() -> None:
    names, mm = _selection_fixture()
    sel = select_greedy_shards(names, mm, _plan([2000, 2001]), it=1)

    # 每关：win 3×40=120（≥100 后停）、timeout 1×100=100 ⇒ 4 保 / 8 丢。
    assert len(sel.keep) == 8
    assert len(sel.drop) == 16
    assert (sel.keep | sel.drop).isdisjoint({"rl_s2000_seed99"}), (
        "非 greedy shard 一律不动"
    )
    assert sel.stats["per_stage_bin_kept"]["2000"] == {"win": 120, "timeout": 100}
    assert sel.stats["per_stage_bin_kept"]["2001"] == {"win": 120, "timeout": 100}
    assert sel.stats["kept_samples"] == 2 * (3 * 40 + 100)
    assert sel.stats["bins"] == {"win": 480, "loss": 0, "timeout": 1200}


def test_gap_stays_in_its_own_stage() -> None:
    """stage 2000 没有 timeout、stage 2001 没有 win ⇒ win 各自留缺口，不互相补。"""
    names: list[str] = []
    mm: dict[str, dict] = {}
    for i in range(6):
        name = f"rl_s2000_seed{i}"
        names.append(name)
        mm[name] = _mm(2000, i, "stage_clear", 40)
    for i in range(6):
        name = f"rl_s2001_seed{i}"
        names.append(name)
        mm[name] = _mm(2001, i, "timeout", 100)

    sel = select_greedy_shards(names, mm, _plan([2000, 2001]), it=3)
    kept = sel.stats["per_stage_bin_kept"]
    assert kept["2000"]["win"] == 120
    assert kept["2000"].get("timeout", 0) == 0
    assert kept["2001"].get("win", 0) == 0
    # timeout 是唯一例外：目标只分给有供给的关（样本仍不跨关）。
    assert kept["2001"]["timeout"] == 200
    assert sel.stats["timeout_targets"] == {"2000": 0, "2001": 200}


def test_timeout_target_splits_by_supply_across_supplying_stages() -> None:
    """两个关都有 timeout 供给 ⇒ 全局 timeout 目标按供给比例分，不平均分。"""
    names: list[str] = []
    mm: dict[str, dict] = {}
    for i in range(2):  # 2000 只有 200 供给
        name = f"rl_s2000_seed{i}"
        names.append(name)
        mm[name] = _mm(2000, i, "timeout", 100)
    for i in range(6):  # 2001 有 600 供给
        name = f"rl_s2001_seed{i}"
        names.append(name)
        mm[name] = _mm(2001, i, "timeout", 100)

    sel = select_greedy_shards(names, mm, _plan([2000, 2001]), it=4)
    assert sel.stats["timeout_targets"] == {"2000": 50, "2001": 150}
    assert sel.stats["per_stage_bin_kept"]["2000"]["timeout"] == 100
    assert sel.stats["per_stage_bin_kept"]["2001"]["timeout"] == 200


def test_selection_is_deterministic_and_outcome_mapped() -> None:
    names, mm = _selection_fixture()
    plan = _plan([2000, 2001])
    first = select_greedy_shards(names, mm, plan, it=7)
    second = select_greedy_shards(names, mm, plan, it=7)
    assert first.keep == second.keep
    assert first.drop == second.drop

    progress_manifests = {
        "a": _mm(2000, 0, "stage_clear", 40),
        "b": _mm(2000, 1, "lives_exhausted", 10),
        "c": _mm(2000, 2, "base_destroyed", 5),
        "d": _mm(2000, 3, "timeout", 100),
        "e": _mm(2000, 4, "stage_clear", 999, greedy=False),
    }
    bins, total = greedy_progress(list(progress_manifests), progress_manifests)
    assert bins == {"win": 40, "loss": 15, "timeout": 100}
    assert total == 155


def test_quota_active_needs_positive_ratios() -> None:
    """「声明了块但三档全 0」是合法关闭，node 腿不该被拒（评审 P1）。"""
    assert quota_active(None) is False
    assert quota_active({"win": 0, "loss": 0, "timeout": 0}) is False
    assert quota_active({"win": 0.0, "timeout": 0.0}) is False
    assert quota_active(GreedyQuotaBlock(win=0, loss=0, timeout=0)) is False
    assert quota_active({"win": 0.1}) is True
    assert quota_active(GreedyQuotaBlock(timeout=0.05)) is True
    # 未知形态不擅自判「激活」（非法值由 CourseConfig 校验先拦）。
    assert quota_active(object()) is False


def test_rule_version_matches_corpus_identity() -> None:
    """选择器规则与语料身份必须同源；只 bump 一处 = 旧 shard 静默混训。"""
    assert GREEDY_QUOTA_RULE_V1 == IDENTITY_RULE


def test_manifest_samples_accepts_both_schemas_and_bad_values() -> None:
    """TS 单局形 nSamples / 队列聚合形 totalSamples / 脏值都不炸整轮发布。"""
    assert manifest_samples({"nSamples": 600}) == 600
    assert manifest_samples({"totalSamples": 600}) == 600
    assert manifest_samples({"samples": 600}) == 600
    assert manifest_samples({"nSamples": "600"}) == 600
    assert manifest_samples({"nSamples": "oops"}) == 0
    assert manifest_samples({"nSamples": None}) == 0
    assert manifest_samples({"nSamples": True}) == 0
    assert manifest_samples({}) == 0

    bins, total = greedy_progress(
        ["a", "b"],
        {
            "a": _mm(2000, 0, "timeout", 0) | {"totalSamples": 120},
            "b": _mm(2000, 1, "timeout", 0) | {"nSamples": "bad"},
        },
    )
    assert bins["timeout"] == 120 and total == 120


def test_merge_greedy_stats_keeps_collect_trace() -> None:
    """发布阶段不能覆盖采集留痕：degraded/cap_hit/waves 要能进 iteration 事件。

    回归：`_remote_ppo_publish` 原先 `self._greedy_stats = sel.stats`，而
    `record_iteration` 排在 ppo 之后 ⇒ 计划 §5.2 要求的「是否触发降级」被抹掉。
    """
    collect = {
        "rule": 2,
        "degraded": True,
        "cap_hit": True,
        "waves": 3,
        "targets": {"win": 100},
    }
    prefilter = {"kept": 30, "dropped": 63, "timeout_targets": {"2002": 1843}}
    merged = merge_greedy_stats(collect, prefilter)
    assert merged["degraded"] is True
    assert merged["cap_hit"] is True
    assert merged["waves"] == 3
    assert merged["prefilter"] == prefilter
    assert collect["degraded"] is True  # 不改入参
    assert merge_greedy_stats(None, prefilter)["prefilter"] == prefilter
