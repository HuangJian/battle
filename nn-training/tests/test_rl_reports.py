"""rl/reports.py — aggregation invariants."""
from __future__ import annotations

from rl.reports import adopt_volume_report, aggregate_rollout_collect, combine_reports, win_of


def test_win_of() -> None:
    assert win_of({"outcomes": {"stage_clear": 1}}) == 1
    assert win_of({"outcomes": {"stage_clear": 0, "base_destroyed": 1}}) == 0
    assert win_of({}) == 0


def test_combine_reports_basic() -> None:
    combined = combine_reports([
        {"games": 2, "totalSamples": 100, "totalTicks": 200,
         "outcomes": {"stage_clear": 1, "base_destroyed": 1}, "scoreList": [10, 20],
         "dimLists": {"move": [0.1, 0.2]}},
        {"games": 3, "totalSamples": 150, "totalTicks": 300,
         "outcomes": {"stage_clear": 2, "timeout": 1}, "scoreList": [30, 40, 50],
         "dimLists": {"move": [0.3, 0.4, 0.5]}},
    ])
    assert combined["games"] == 5
    assert combined["winRate"] == 0.6  # (1 win stage_clear + 2 wins) / 5 games
    assert combined["totalSamples"] == 250
    assert combined["totalTicks"] == 500
    assert combined["outcomes"]["stage_clear"] == 3


def test_combine_reports_empty() -> None:
    combined = combine_reports([])
    assert combined["games"] == 0
    assert combined["winRate"] == 0.0
    assert "pure_collect_sec" not in combined


def test_combine_reports_score_stats() -> None:
    combined = combine_reports([
        {"games": 2, "totalSamples": 10, "totalTicks": 100,
         "outcomes": {"stage_clear": 2}, "scoreList": [1, 2], "dimLists": {}},
    ])
    assert "scoreStats" in combined
    assert combined["scoreStats"]["mean"] == 1.5
    assert combined["scoreStats"]["min"] == 1
    assert combined["scoreStats"]["max"] == 2


def _wave(games: int, t0: float, t1: float, pure: float) -> dict:
    return {
        "games": games,
        "totalSamples": 100 * games,
        "totalTicks": 1000 * games,
        "outcomes": {"lives_exhausted": games},
        "scoreList": [0.3] * games,
        "dimLists": {},
        "weights_dist_start_ts": t0,
        "collect_end_ts": t1,
        "pure_collect_sec": pure,
    }


def test_aggregate_rollout_collect_multi_wave_it_caliber() -> None:
    """it 级：首波分发 → 末波样本齐（不是各波 pure_collect 相加，也不是只取末波）。"""
    w0 = _wave(124, t0=1000.0, t1=1030.0, pure=30.0)
    w1 = _wave(10, t0=1050.0, t1=1062.0, pure=12.0)
    w2 = _wave(2, t0=1080.0, t1=1082.0, pure=2.0)
    out = aggregate_rollout_collect([w0, w1, w2])
    assert out["rollout_collect_aggregated"] is True
    assert out["rollout_collect_waves"] == 3
    assert out["weights_dist_start_ts"] == 1000.0
    assert out["collect_end_ts"] == 1082.0
    assert out["pure_collect_sec"] == 82.0  # 1082-1000，含波间空隙
    # 只取末波会低估；求和会高估
    assert out["pure_collect_sec"] != 2.0
    assert out["pure_collect_sec"] != 30.0 + 12.0 + 2.0


def test_combine_reports_carries_it_level_pure_collect() -> None:
    w0 = _wave(4, t0=0.0, t1=40.0, pure=40.0)
    w1 = _wave(2, t0=50.0, t1=70.0, pure=20.0)
    combined = combine_reports([w0, w1])
    assert combined["games"] == 6
    assert combined["pure_collect_sec"] == 70.0
    assert combined["rollout_collect_aggregated"] is True
    assert combined["rollout_collect_waves"] == 2


def test_aggregate_rollout_collect_fallback_without_ts() -> None:
    """无 ts 锚点：回退 max(per-wave) 作为下界，并标记未完整聚合。"""
    out = aggregate_rollout_collect([
        {"games": 1, "totalSamples": 1, "totalTicks": 1, "pure_collect_sec": 30.0},
        {"games": 1, "totalSamples": 1, "totalTicks": 1, "pure_collect_sec": 20.0},
    ])
    assert out["rollout_collect_aggregated"] is False
    assert out["pure_collect_sec"] == 30.0
    assert out["rollout_collect_waves"] == 2


def test_aggregate_rollout_collect_single_wave_passthrough() -> None:
    w = _wave(3, t0=10.0, t1=25.0, pure=15.0)
    out = aggregate_rollout_collect([w])
    assert out["pure_collect_sec"] == 15.0
    assert out["rollout_collect_aggregated"] is True
    assert out["rollout_collect_waves"] == 1


def test_adopt_volume_report_rejects_prior_iter_merge() -> None:
    """continuous 收官：只采纳本轮；与上一轮 combine 会把起点钉在历史波（x20 it6=425s）。"""
    prior = _wave(4, t0=1000.0, t1=1030.0, pure=30.0)  # 上一轮残留 _report
    it2 = combine_reports([
        _wave(4, t0=2000.0, t1=2020.0, pure=20.0),
        _wave(2, t0=2025.0, t1=2040.0, pure=15.0),
    ])
    got = adopt_volume_report(it2)
    assert got is it2
    assert got["pure_collect_sec"] == 40.0  # 2040-2000，本轮窗口
    # 反例（历史 bug 路径）：combine([prior, it2]) 会得到 2040-1000=1040
    wrong = combine_reports([prior, it2])
    assert wrong["pure_collect_sec"] == 1040.0
    assert got["pure_collect_sec"] != wrong["pure_collect_sec"]


def test_adopt_volume_report_empty_is_valid_shape() -> None:
    """本轮无采集：返回合法空 shape（games 等键齐全），不得是 {}（KeyError: games 回归）。"""
    empty = adopt_volume_report(None)
    assert empty["games"] == 0
    assert empty["winRate"] == 0.0
    assert empty["outcomes"] == {}
    assert empty["totalSamples"] == 0
    assert empty["totalTicks"] == 0
    # _log_report / events 直接读这些键——空 dict 会在训练主循环炸掉
    assert adopt_volume_report({})["games"] == 0


def test_combine_reports_skips_empty_dicts() -> None:
    """轮初 `_report={}` 与真波报告一起 combine 不得 KeyError。"""
    w0 = _wave(2, t0=0.0, t1=20.0, pure=20.0)
    combined = combine_reports([{}, w0, {}])
    assert combined["games"] == 2
    assert combined["pure_collect_sec"] == 20.0
