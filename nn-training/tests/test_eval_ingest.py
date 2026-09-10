"""test_eval_ingest — EvalBench Python 入账（T0.3/D5-a）单测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_ingest import (
    iter_eval_rows,
    m1_game_row,
    seed_space_of,
    segment_of_seed,
    write_m1_game_rows,
)
from rl.eval_m1 import parse_m1_eval_report


def test_segments_mirror_store_ts() -> None:
    assert segment_of_seed(860001) == 0
    assert segment_of_seed(860100) == 0
    assert segment_of_seed(860101) == 1
    assert segment_of_seed(42) == -1
    assert seed_space_of(860001) == "eval860k"
    assert seed_space_of(5) == "probe0"


def test_m1_game_row_keys_align_with_record() -> None:
    r = m1_game_row(
        {"stage": 3, "seed": 7, "outcome": "stage_clear", "win": True, "cleared": True,
         "ticks": 900, "kills": 20, "playerShots": 300, "cellsVisited": 120},
        it=5, wver16="ab" * 8, policy="intent-exec",
    )
    assert r["event"] == "eval"
    assert r["win"] == 1 and r["cleared"] == 1
    assert r["playerShots"] == 300
    assert r["cellsVisited"] == 120
    # sim-worker 天然缺失 = None（豁免清单，不伪造）
    assert r["playerHits"] is None
    assert r["stuckTicks"] is None
    assert r["score"] is None


def test_write_m1_game_rows_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    games = [
        {"stage": 0, "seed": 1, "outcome": "stage_clear", "win": True, "cleared": True, "ticks": 1, "kills": 1},
        {"stage": 0, "seed": 2, "outcome": "gameover", "win": False, "cleared": False, "ticks": 2, "kills": 0},
    ]
    assert write_m1_game_rows(p, games, it=5, wver16="ab" * 8, policy="nn") == 2
    assert write_m1_game_rows(p, games, it=5, wver16="ab" * 8, policy="nn") == 0
    rows = iter_eval_rows(p)
    assert len(rows) == 2
    assert {r["seed"] for r in rows} == {1, 2}


def test_parse_m1_report_per_game_only_from_clean_stdout() -> None:
    doc = {"total": 2, "outcomes": {"stage_clear": 1, "gameover": 1},
           "perGame": [{"stage": 0, "seed": 1, "win": True}]}
    clean = json.dumps(doc)
    # 合并文本（banner 在 stderr）→ 整段 json 失败 → perGame 空（由 run_clean_eval 从 stdout 补）
    merged = clean + "\n[m1-eval] WIN RATE 50.0% (gate 60%) -> FAIL\n"
    assert parse_m1_eval_report(merged)["perGame"] == []
    assert parse_m1_eval_report(merged)["winRate"] == pytest.approx(0.5)
    # 干净 stdout 直解 → perGame 命中
    assert parse_m1_eval_report(clean)["perGame"] == [{"stage": 0, "seed": 1, "win": True}]


def test_engine_epoch_deterministic_and_gate() -> None:
    import dist_common

    assert "src/game/" in dist_common.GAMEPLAY_SPECS
    assert "tools/sim/export-eval-game.ts" in dist_common.GAMEPLAY_SPECS
    fp1 = dist_common.gameplay_fingerprint()
    assert dist_common.gameplay_fingerprint() == fp1
    ep = dist_common.compute_engine_epoch("abc123")
    import hashlib

    assert ep == hashlib.sha256(f"abc123\n{fp1}".encode()).hexdigest()[:16]
    # 节点门：一致通过；缺失/不符拒收（B/C 严格，A 层过渡见 eval_dispatch）
    assert dist_common.check_engine_epoch({"engineEpoch": ep}, ep) is None
    assert dist_common.check_engine_epoch({}, ep) is not None
    assert dist_common.check_engine_epoch({"engineEpoch": "0" * 16}, ep) is not None
