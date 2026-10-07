"""tests/worker/test_volume_alloc.py —— 分关采样分配（`plan/rollout-stage-balance.plan.md` §3）。

钉住四件事（每条都对应一个现场事故形态）：

  1. **反解算术是定点化的**：`est_hi = ceil(est × 1.15)` 不许被二进制浮点抬一档
     （`20 × 1.15 = 23` 必须还是 23，否则局数 / `--out` 序号 / 硬顶全跟着漂）；
  2. **跨关独立**：动一关的 est 不改别关的局数（分关达标线是用户口径，且种子流按关独立）；
  3. **缺口是硬指标、浪费是软指标**：末批偏保守（`last_lo_factor`）、`game_cap` 截断、
     触顶/未达标由调用方响亮记事件——本模块只保证「不静默多派/少派」；
  4. **同源校验**：运行时块里的 `per_stage_quota` / `games_per_stage_by_stage` 必须能从
     `target/stages/est` 重新解出来（两侧各自重算，规则改了但块没跟上 ⇒ 响亮失败）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.volume_alloc import (
    DEFAULT_EST_HI_FACTOR,
    DEFAULT_LAST_BATCH_LO_FACTOR,
    DEFAULT_TOPUP_MAX_BATCHES,
    VOLUME_ALLOC_RULE,
    alloc_games_by_stage,
    default_game_caps,
    est_hi,
    shortfall_by_stage,
    stage_totals,
    topup_games_by_stage,
    validate_runtime_volume,
    wasted_samples,
)

STAGES = [2000, 2001, 2002, 2003]
TARGET = 49152
QUOTA = 12288  # ceil(49152/4)
#: it168 实测（produced 集）逐关 samples/局 —— 见 plan §1 表 / 评审 §1。
IT168_EST = {2000: 422, 2001: 310, 2002: 241, 2003: 327}
#: 导出那一刻真机可得的 trailing（it163–it167 分关均值）。
TRAIL_EST = {2000: 340, 2001: 307, 2002: 342, 2003: 365}


# ────────────────────────── 1. 常量与定点算术 ──────────────────────────


def test_rule_constants_are_pinned() -> None:
    """§3.4 的冻结值：改它们 = 采样规则变更（要配一次真机回标 + DECISIONS）。"""
    assert VOLUME_ALLOC_RULE == "per-stage-v3"
    assert DEFAULT_EST_HI_FACTOR == 1.15
    assert DEFAULT_TOPUP_MAX_BATCHES == 3
    assert DEFAULT_LAST_BATCH_LO_FACTOR == 0.85


@pytest.mark.parametrize(
    "est, want",
    [
        (422, 486),  # 485.3 → 486
        (310, 357),  # 356.5 → 357
        (241, 278),  # 277.15 → 278
        (327, 377),  # 376.05 → 377
        (20, 23),  # ★ 浮点陷阱：20×1.15 若算成 23.000000000000004 会被 ceil 抬成 24
        (100, 115),
        (1, 2),  # ceil(1.15)
    ],
)
def test_est_hi_is_exact(est: int, want: int) -> None:
    assert est_hi(est) == want


@pytest.mark.parametrize("bad", [0, -3])
def test_est_hi_refuses_nonpositive_est(bad: int) -> None:
    """没有估计值就是乱采——响亮报错，不静默兜底。"""
    with pytest.raises(ValueError, match="est"):
        est_hi(bad)
    with pytest.raises(ValueError, match="factor"):
        est_hi(100, factor=0)


# ────────────────────────── 2. 首批反解 ──────────────────────────


def test_alloc_matches_plan_counterfactual() -> None:
    """DoD：`est = it168 实测` ⇒ `est_hi = [486,357,278,377]` ⇒ `G = [26,35,45,33]`。"""
    hi = {s: est_hi(IT168_EST[s]) for s in STAGES}
    assert [hi[s] for s in STAGES] == [486, 357, 278, 377]
    assert alloc_games_by_stage(STAGES, TARGET, ests_hi=hi) == {
        2000: 26,
        2001: 35,
        2002: 45,
        2003: 33,
    }


def test_alloc_matches_trailing_forecast() -> None:
    """真机可得的是 trailing（不是本轮实测）：`G = [32,35,32,30]`（评审 §1 的预报版）。"""
    hi = {s: est_hi(TRAIL_EST[s]) for s in STAGES}
    assert [hi[s] for s in STAGES] == [391, 354, 394, 420]
    assert alloc_games_by_stage(STAGES, TARGET, ests_hi=hi) == {
        2000: 32,
        2001: 35,
        2002: 32,
        2003: 30,
    }


def test_alloc_cross_stage_independent() -> None:
    """改一关的 est，只动那一关的局数（跨关独立不变量）。"""
    base = {s: 400 for s in STAGES}
    other = {**base, 2002: 200}
    a = alloc_games_by_stage(STAGES, TARGET, ests_hi=base)
    b = alloc_games_by_stage(STAGES, TARGET, ests_hi=other)
    assert {s: a[s] for s in STAGES if s != 2002} == {s: b[s] for s in STAGES if s != 2002}
    assert b[2002] > a[2002]


def test_alloc_never_zero_and_refuses_missing_est() -> None:
    """局数 ≥1（0 局 = 这一关整轮不采，静默缺额）；缺 est 响亮报错。"""
    assert alloc_games_by_stage([2000], TARGET, ests_hi={2000: 10**9}) == {2000: 1}
    with pytest.raises(ValueError, match="est_hi"):
        alloc_games_by_stage(STAGES, TARGET, ests_hi={2000: 400})
    with pytest.raises(ValueError, match="stage"):
        alloc_games_by_stage([], TARGET, ests_hi={})


# ────────────────────────── 3. 补差批 ──────────────────────────


def test_topup_omits_met_and_capped_stages() -> None:
    """已达标的关不进结果；触 hard cap 的关也不进（调用方负责把「未达标 + 触顶」记事件）。"""
    plan = topup_games_by_stage(
        stages=STAGES,
        collected={2000: QUOTA, 2001: 11000, 2002: 0, 2003: 0},
        games_done={2000: 30, 2001: 36, 2002: 0, 2003: 200},
        target_transitions=TARGET,
        ests={2000: 422, 2001: 310, 2002: 241, 2003: 327},
        game_caps={2000: 152, 2001: 164, 2002: 144, 2003: 148},
        batches_left=2,
    )
    assert 2000 not in plan  # 已达标
    assert 2003 not in plan  # games_done 200 ≥ cap 148 ⇒ 触顶（响亮由调用方做）
    assert plan[2001] == 5  # ceil(1288/310) = 5
    assert plan[2002] == 51  # ceil(12288/241) = 51


def test_topup_last_batch_is_conservative() -> None:
    """末批（`batches_left == 1`）按 `×0.85` 定尺寸 ⇒ 派得更多（缺口不可恢复）。"""
    collected = {2000: QUOTA, 2001: QUOTA, 2002: 8920, 2003: QUOTA}
    games_done = {s: 37 for s in STAGES}
    ests = dict(IT168_EST)
    caps = {s: 0 for s in STAGES}
    not_last = topup_games_by_stage(
        stages=STAGES,
        collected=collected,
        games_done=games_done,
        target_transitions=TARGET,
        ests=ests,
        game_caps=caps,
        batches_left=2,
    )
    last = topup_games_by_stage(
        stages=STAGES,
        collected=collected,
        games_done=games_done,
        target_transitions=TARGET,
        ests=ests,
        game_caps=caps,
        batches_left=1,
    )
    assert set(not_last) == {2002}
    assert not_last[2002] == 14  # ceil(3368/241)
    assert last[2002] == 17  # ceil(3368/204)（204 = floor(241×0.85)）
    assert last[2002] > not_last[2002]


#: 单关课程的等价目标（quota = 12288）——让 cap 用例不必构造另外三关的账本。
TARGET1 = QUOTA


def test_topup_truncates_at_cap_room() -> None:
    """`game_cap` 是硬顶：本批也不许越（越界就是「下批才停」的软顶）。"""
    plan = topup_games_by_stage(
        stages=[2002],
        collected={2002: 0},
        games_done={2002: 140},
        target_transitions=TARGET1,
        ests={2002: 241},
        game_caps={2002: 144},
        batches_left=3,
    )
    assert plan == {2002: 4}
    assert (
        topup_games_by_stage(
            stages=[2002],
            collected={2002: 0},
            games_done={2002: 144},
            target_transitions=TARGET1,
            ests={2002: 241},
            game_caps={2002: 144},
            batches_left=3,
        )
        == {}
    )


def test_topup_refuses_missing_est() -> None:
    with pytest.raises(ValueError, match="est"):
        topup_games_by_stage(
            stages=STAGES,
            collected={s: 0 for s in STAGES},
            games_done={s: 0 for s in STAGES},
            target_transitions=TARGET,
            ests={2000: 400},
            game_caps={s: 0 for s in STAGES},
            batches_left=2,
        )


def test_shortfall_and_waste_are_separate_metrics() -> None:
    """缺口（硬）与浪费（软）分开量——DoD 不再要求 `dropped == 0`。"""
    collected = {2000: QUOTA + 3333, 2001: 11453, 2002: 8920, 2003: 12086}
    assert shortfall_by_stage(stages=STAGES, collected=collected, target_transitions=TARGET) == {
        2001: 835,
        2002: 3368,
        2003: 202,
    }
    assert wasted_samples(stages=STAGES, collected=collected, target_transitions=TARGET) == 3333


# ────────────────────────── 4. 产出集口径与硬顶 ──────────────────────────


def test_stage_totals_reads_produced_set() -> None:
    """只认 `stage` + `nSamples`（回退 `totalSamples`）；坏行跳过（少一局读数 ≠ 改口径）。"""
    manifests = [
        {"stage": 2000, "nSamples": 651},
        {"stage": 2000, "nSamples": 99},
        {"stage": 2002, "totalSamples": 120},
        {"stage": 2002, "nSamples": 0},  # 0 样本 ⇒ 不计（也不是一局）
        {"nSamples": 5},  # 缺 stage ⇒ 跳过
        {"stage": 2003},  # 缺样本数 ⇒ 跳过
        {"stage": True, "nSamples": 5},  # bool 不算 int（Python 的坑）
        "junk",  # 非 mapping ⇒ 跳过
    ]
    collected, games = stage_totals(manifests)
    assert collected == {2000: 750, 2002: 120}
    assert games == {2000: 2, 2002: 1}


def test_default_game_caps_follow_per_stage_est() -> None:
    """评审 P1-5：低 est 的关需要更多局 ⇒ cap 必须按**分关** est（否则配额未满就触顶）。"""
    caps = default_game_caps(STAGES, TARGET, ests={2000: 422, 2001: 310, 2002: 80, 2003: 327})
    assert caps == {2000: 4 * 30, 2001: 4 * 40, 2002: 4 * 154, 2003: 4 * 38}
    # 旧的全局 est 口径会把 2002 卡在 4×37 = 148 局（= 缺口），这就是要修的那条。
    assert caps[2002] > 4 * 37
    assert default_game_caps(STAGES, TARGET, ests={s: 400 for s in STAGES}, explicit=99) == {
        s: 99 for s in STAGES
    }


# ────────────────────────── 5. 运行时块：形状 + 同源 ──────────────────────────


def _block(**over: object) -> dict:
    hi = {s: est_hi(TRAIL_EST[s]) for s in STAGES}
    blk: dict = {
        "target_transitions": TARGET,
        "stages": list(STAGES),
        "per_stage_quota": QUOTA,
        "games_per_stage_by_stage": alloc_games_by_stage(STAGES, TARGET, ests_hi=hi),
        "est_s_by_stage": dict(TRAIL_EST),
        "est_hi_factor": DEFAULT_EST_HI_FACTOR,
        "topup": {
            "enabled": True,
            "max_batches": DEFAULT_TOPUP_MAX_BATCHES,
            "last_lo_factor": DEFAULT_LAST_BATCH_LO_FACTOR,
            "max_games_per_stage": 0,
        },
        "it": 168,
        "rotate_seed": 20261012,
    }
    blk.update(over)
    return blk


def test_validate_runtime_volume_accepts_consistent_block() -> None:
    blk = validate_runtime_volume(_block())
    assert blk["est_s_by_stage"] == TRAIL_EST
    assert blk["games_per_stage_by_stage"] == {2000: 32, 2001: 35, 2002: 32, 2003: 30}
    assert blk["est_hi_factor"] == 1.15


@pytest.mark.parametrize(
    "over, frag",
    [
        ({"per_stage_quota": 12001}, "per_stage_quota"),
        ({"games_per_stage_by_stage": {2000: 31, 2001: 35, 2002: 32, 2003: 30}}, "同源"),
        ({"est_s_by_stage": {2000: 340}}, "缺关"),
        ({"games_per_stage_by_stage": {2000: 32}}, "缺关"),
        ({"stages": [2000, 2001, 2002]}, "多出关"),
        ({"est_hi_factor": 0}, "est_hi_factor"),
        ({"topup": {"enabled": True}}, "topup 缺字段"),
        ({"topup": {"enabled": True, "max_batches": 3, "last_lo_factor": 1.5, "max_games_per_stage": 0}}, "last_lo_factor"),
        ({"it": 0}, "it"),
        ({"rotate_seed": "x"}, "rotate_seed"),
    ],
)
def test_validate_runtime_volume_refuses_drift(over: dict, frag: str) -> None:
    """规则改了但块没跟上 / 形状被手改 ⇒ 响亮失败（静默漂移正是要防的事）。"""
    with pytest.raises(ValueError, match=frag):
        validate_runtime_volume(_block(**over))


def test_validate_runtime_volume_refuses_non_dict_and_missing_keys() -> None:
    with pytest.raises(ValueError, match="对象"):
        validate_runtime_volume(["not", "a", "dict"])
    with pytest.raises(ValueError, match="缺字段"):
        validate_runtime_volume({"stages": STAGES})
