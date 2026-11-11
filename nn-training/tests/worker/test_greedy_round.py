"""云机轮内 greedy 阶段（`worker/greedy_round.py`）的单测。

覆盖用户 2026-11-11 的 B 口径（挤进去：预算 = `plan.total_target`，不吃 `oversample_cap`）、
种子血缘（普通采样排除）、停波条件与账本形状（与 trainer 腿同键）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.greedy_round import greedy_argv_rows, greedy_spec, run_greedy_round


def _spec(pairs: list[tuple[int, int]], wver: str = "w1") -> dict:
    """最小 rollout spec：每关一条模板（与 `worker/plan.iter_spec` 同形）。"""
    rows = [
        [
            "tools/sim/export-rl-rollout.ts",
            "--stages",
            str(stage),
            "--seeds",
            str(seed),
            "--out",
            "w0",
            "--weights",
            "init_weights.json",
            "--wver",
            wver,
        ]
        for stage, seed in pairs
    ]
    return {"argv": rows, "wver": wver, "workers": 4, "game_timeout_sec": 30.0, "bun": "bun"}


def _write_shard(root: Path, idx: int, stage: int, seed: int, outcome: str, n: int) -> Path:
    d = root / f"w{idx}" / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "labelMode": "greedy",
                "stage": stage,
                "seed": seed,
                "nSamples": n,
                "outcome": outcome,
            }
        ),
        encoding="utf-8",
    )
    return d


def test_argv_rows_retarget_and_mark_greedy() -> None:
    spec = _spec([(0, 7), (1, 9)])
    rows = greedy_argv_rows(spec, [(0, 41), (1, 43)])
    assert len(rows) == 2
    for idx, (row, stage, seed) in enumerate(zip(rows, (0, 1), (41, 43), strict=True)):
        assert row[0] == "tools/sim/export-rl-rollout.ts"
        assert row[row.index("--stages") + 1] == str(stage)
        assert row[row.index("--seeds") + 1] == str(seed)
        assert row[row.index("--out") + 1] == f"w{idx}"
        assert row[row.index("--wver") + 1] == "w1"
        assert row[-1] == "--teacher-argmax"
        assert row.count("--teacher-argmax") == 1


def test_argv_rows_keep_custom_stage_json() -> None:
    spec = _spec([(2000, 1)])
    spec["argv"][0] += ["--stage-json", '{"custom": true}']
    rows = greedy_argv_rows(spec, [(2000, 5)])
    assert rows[0][rows[0].index("--stage-json") + 1] == '{"custom": true}'


def test_argv_rows_missing_stage_template_is_loud() -> None:
    from common.protocol import ProtocolError

    with pytest.raises(ProtocolError, match="缺关 3 的 argv 模板"):
        greedy_argv_rows(_spec([(0, 1)]), [(3, 2)])


def test_greedy_spec_is_explicit_argv_without_volume() -> None:
    """一波 greedy = **显式 argv**：键集里没有 `volume`（否则会被当模板静默吞掉）也没配额块。"""
    spec = _spec([(0, 1)])
    out = greedy_spec(spec, [(0, 11)])
    assert set(out) == {"argv", "bun", "game_timeout_sec", "workers", "wver"}
    assert out["wver"] == "w1"
    assert out["argv"][0][-1] == "--teacher-argmax"


def _runner(root: Path, *, outcome: str, n: int):
    """假 runner：每局落一个 greedy shard，返回与 `run_iter_rollout` 同形的两块。"""
    seen: list[list[tuple[int, int]]] = []

    def run(pairs: list[tuple[int, int]]) -> dict:
        seen.append(list(pairs))
        idx = len(seen) - 1
        dirs = [
            str(_write_shard(root, idx * 100 + k, stage, seed, outcome, n))
            for k, (stage, seed) in enumerate(pairs)
        ]
        return {
            "report": {
                "games": len(pairs),
                "winRate": 1.0 if outcome == "stage_clear" else 0.0,
                "totalSamples": n * len(pairs),
                "outcomes": {outcome: len(pairs)},
            },
            "shard_dirs": dirs,
        }

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_inactive_quota_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "greedy"
    run = _runner(root, outcome="stage_clear", n=100)
    assert (
        run_greedy_round(
            raw_quota=None,
            round_target=1000,
            stages=[0, 1],
            it=3,
            rotate_seed=0,
            ordinary=[],
            run_wave=run,
        )
        == {}
    )
    assert (
        run_greedy_round(
            raw_quota={"win": 0.0, "loss": 0.0, "timeout": 0.0},
            round_target=1000,
            stages=[0, 1],
            it=3,
            rotate_seed=0,
            ordinary=[],
            run_wave=run,
        )
        == {}
    )
    assert run.seen == []  # type: ignore[attr-defined]
    assert not root.exists()


def test_budget_is_total_target_not_oversample_cap(tmp_path: Path) -> None:
    """B 口径：本轮 greedy 上限 = `plan.total_target`（120），不是 `⌈120×2⌉`。"""
    root = tmp_path / "greedy"
    run = _runner(root, outcome="stage_clear", n=600)
    out = run_greedy_round(
        raw_quota={"win": 0.06, "loss": 0.0, "timeout": 0.06, "oversample_cap": 2.0},
        round_target=1000,
        stages=[0, 1],
        it=3,
        rotate_seed=11,
        ordinary=[],
        run_wave=run,
        width=2,
    )
    stats = out["greedy_quota_stats"]
    assert stats["target"] == 120
    assert stats["collect_target"] == 120  # 不是 240
    assert stats["cap"] == 2.0  # cap 仍进账本，只是不当上限
    assert out["waves"] == 1  # 一局 600 样本已过预算 ⇒ 第二波不再派
    assert stats["total"] >= 120


def test_waves_exclude_ordinary_and_report_bins(tmp_path: Path) -> None:
    root = tmp_path / "greedy"
    run = _runner(root, outcome="stage_clear", n=10)
    ordinary = [(0, 1000), (1, 2000)]
    out = run_greedy_round(
        raw_quota={"win": 0.12},
        round_target=1000,
        stages=[0, 1],
        it=4,
        rotate_seed=7,
        ordinary=ordinary,
        run_wave=run,
        ests={0: 10, 1: 10},
        width=2,
    )
    for wave in run.seen:  # type: ignore[attr-defined]
        assert set(wave).isdisjoint(set(ordinary)), "greedy 与普通采样撞了 (stage, seed)"
    ivals = [p for wave in run.seen for p in wave]  # type: ignore[attr-defined]
    assert len(ivals) == len(set(ivals)), "同一轮内 greedy 抽了重复的 (stage, seed)"
    stats = out["greedy_quota_stats"]
    assert stats["bins"]["win"] > 0
    assert out["by_stage"]["games"] >= 1
    assert set(out["by_stage"]) == {"samples", "games", "stages"}


def test_stats_shape_matches_trainer_contract(tmp_path: Path) -> None:
    root = tmp_path / "greedy"
    out = run_greedy_round(
        raw_quota={"win": 0.12},
        round_target=1000,
        stages=[0],
        it=1,
        rotate_seed=3,
        ordinary=[],
        run_wave=_runner(root, outcome="stage_clear", n=50),
        sampling_brief={"games": 7, "winRate": 0.5, "outcomes": {}, "samples": 700},
        width=1,
    )
    stats = out["greedy_quota_stats"]
    assert set(stats) == {
        "rule",
        "target",
        "collect_target",
        "cap",
        "soft",
        "per_stage_target",
        "bins",
        "targets",
        "total",
        "degraded",
        "cap_hit",
        "waves",
        "stopped_no_games",
        "sampling_report",
        "greedy_report",
    }
    assert stats["sampling_report"]["samples"] == 700
    assert stats["greedy_report"]["games"] >= 1


def test_hard_quota_raises_when_bins_unmet(tmp_path: Path) -> None:
    root = tmp_path / "greedy"
    with pytest.raises(RuntimeError, match="greedy_quota 硬目标未达成"):
        run_greedy_round(
            raw_quota={"win": 0.12, "soft": False},
            round_target=1000,
            stages=[0],
            it=2,
            rotate_seed=0,
            ordinary=[],
            run_wave=_runner(root, outcome="lives_exhausted", n=200),
            width=1,
        )


def test_stops_without_games_when_stage_is_capped(tmp_path: Path) -> None:
    """est 极小 ⇒ 逐关硬顶先到；派不出局时 `stopped_no_games` 必须响亮（不是静默空转）。"""
    root = tmp_path / "greedy"
    out = run_greedy_round(
        raw_quota={"win": 0.5},
        round_target=10000,
        stages=[0],
        it=9,
        rotate_seed=5,
        ordinary=[],
        run_wave=_runner(root, outcome="stage_clear", n=1),
        ests={0: 10**9},
        width=1,
    )
    stats = out["greedy_quota_stats"]
    assert stats["degraded"] is True
    assert stats["stopped_no_games"] is True
