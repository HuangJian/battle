"""volume_quota — 配额感知连续采集纯逻辑（VOLUME_RULE_V2，2026-09-19）。"""

from __future__ import annotations

from rl.volume_quota import (
    DEFAULT_MAX_BATCHES,
    VOLUME_RULE_V2,
    allocate_stage_games,
    continuous_pairs,
    continuous_seed_stream,
    default_game_cap,
    plan_continuous_batch,
    stage_seeds,
    target_per_stage,
)


def test_target_per_stage() -> None:
    assert target_per_stage(24000, 4) == 6000
    assert target_per_stage(24001, 4) == 6001


def test_stage_seeds_deterministic_and_independent() -> None:
    a1 = stage_seeds(7, 3, 2000, 0, 8)
    a2 = stage_seeds(7, 3, 2000, 0, 8)
    assert a1 == a2
    b = stage_seeds(7, 3, 2001, 0, 8)
    assert a1 != b  # 跨关独立
    cont = stage_seeds(7, 3, 2000, 3, 4)
    assert cont == a1[3:7]  # start_idx 续接同一流
    assert all(1 <= s < 2**30 for s in a1)


def test_allocate_soft_stop_and_hard_cap() -> None:
    # 已足额
    n, why = allocate_stage_games(
        collected=6000, inflight=0, quota=6000, est=200, games_done=30, game_cap=124
    )
    assert (n, why) == (0, "quota_met")
    # 在飞预计补齐 → 软停
    n, why = allocate_stage_games(
        collected=5000, inflight=6, quota=6000, est=200, games_done=25, game_cap=124
    )
    assert n == 0 and why is None
    # 差额大 → 多派
    n, why = allocate_stage_games(
        collected=2000, inflight=0, quota=6000, est=200, games_done=10, game_cap=124
    )
    assert n == 20  # (6000-2000)/200
    # game_cap
    n, why = allocate_stage_games(
        collected=100, inflight=0, quota=6000, est=200, games_done=124, game_cap=124
    )
    assert (n, why) == (0, "game_cap")


def test_plan_continuous_batch_prefers_short_stage() -> None:
    plan = plan_continuous_batch(
        stages=[2000, 2001, 2002, 2003],
        collected={2000: 6000, 2001: 2000, 2002: 5800, 2003: 5800},
        inflight={2000: 0, 2001: 0, 2002: 1, 2003: 0},
        target_transitions=24000,
        ests={2000: 200, 2001: 150, 2002: 200, 2003: 200},
        games_done={2000: 30, 2001: 15, 2002: 29, 2003: 29},
        game_cap=124,
        fallback_est=200,
    )
    assert 2000 not in plan.games_by_stage  # 已达标
    # 2002: 5800+1*200=6000 → 软停
    assert 2002 not in plan.games_by_stage
    # 2001 短产：quota 6000-2000=4000, est=150 → ~27 局
    assert plan.games_by_stage[2001] >= 20
    # 2003: 200 short / 200 → 1 局
    assert plan.games_by_stage.get(2003) == 1


def test_plan_empty_when_all_met() -> None:
    plan = plan_continuous_batch(
        stages=[1, 2],
        collected={1: 100, 2: 100},
        inflight={1: 0, 2: 0},
        target_transitions=200,
        ests={1: 50, 2: 50},
        games_done={1: 2, 2: 2},
        game_cap=10,
    )
    assert plan.games_by_stage == {}
    assert plan.capped is False


def test_continuous_pairs_uses_start_idx() -> None:
    pairs = continuous_pairs(
        11, 2, {2000: 3, 2001: 2}, {2000: 5, 2001: 0}
    )
    assert pairs[0][0] == 2000 and pairs[3][0] == 2001
    assert pairs == continuous_pairs(11, 2, {2000: 3, 2001: 2}, {2000: 5, 2001: 0})
    # 2000 从 idx=5 起，与 idx=0 起的前 3 个 seed 不同
    from0 = stage_seeds(11, 2, 2000, 0, 3)
    from5 = [sd for st, sd in pairs if st == 2000]
    assert from0 != from5


def test_default_game_cap_and_rule_version() -> None:
    assert default_game_cap(6000, 200) >= 30
    assert VOLUME_RULE_V2 == 2
    assert DEFAULT_MAX_BATCHES >= 3
    assert continuous_seed_stream(1, 1, 1) is not None
