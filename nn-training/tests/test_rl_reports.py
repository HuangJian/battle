"""rl/reports.py — aggregation invariants."""
from __future__ import annotations

from rl.reports import combine_reports, win_of


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


def test_combine_reports_score_stats() -> None:
    combined = combine_reports([
        {"games": 2, "totalSamples": 10, "totalTicks": 100,
         "outcomes": {"stage_clear": 2}, "scoreList": [1, 2], "dimLists": {}},
    ])
    assert "scoreStats" in combined
    assert combined["scoreStats"]["mean"] == 1.5
    assert combined["scoreStats"]["min"] == 1
    assert combined["scoreStats"]["max"] == 2
