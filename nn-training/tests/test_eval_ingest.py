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


def test_engine_epoch_derives_from_ssot_codehash() -> None:
    """2026-09-17 用户指令：eval 节点门与 rollout 门同源 = codehash-files.txt。

    engine_epoch = sha256(codeHash)[:16]，**不再掺 git commit**——任何与 rollout/eval
    无关的提交（dashboard / nn-training / docs）都不得让节点判 stale。旧式
    sha256(git_head + GAMEPLAY_SPECS 指纹) 的双份清单已删。
    """
    import hashlib
    import inspect

    import dist_common

    ch = dist_common.compute_code_hash()
    ep = dist_common.compute_engine_epoch()
    assert ep == hashlib.sha256(ch.encode()).hexdigest()[:16]
    assert len(ep) == 16
    # 不掺 git：实现里不得出现 git 调用（旧式为 sha256(git_head + gameplay)）
    assert "git" not in inspect.getsource(dist_common.compute_engine_epoch)
    # 引擎文件已并入 SSOT 清单（改引擎 ⇒ codeHash 变 ⇒ eval 节点门变红）
    rels = [rel for rel, _c in dist_common._collect_code_hash_files()]
    for need in (
        "src/game/SimulationCombat.ts",
        "tools/det-golden.v1.sha256",
        "tools/sim/export-eval-game.ts",
    ):
        assert need in rels
    for spec in ("src/game/", "src/config/", "src/utils/", "src/ai/"):
        assert any(r.startswith(spec) for r in rels), spec
    # 无关树不入集（入集 = 每次无关提交都触发节点重启波）
    assert not any(r.startswith(("dashboard/", "nn-training/")) for r in rels)
    # 节点门（2026-09-17 统一）：rollout 与 eval 同一判据 = codeHash；
    # engine_epoch 只是账本记录值，不再进 /v1/ping、也不再是门。
    assert dist_common.check_code_hash({"codeHash": ch}, ch) is None
    assert dist_common.check_code_hash({}, ch) is not None
    assert dist_common.check_code_hash({"codeHash": "0" * 64}, ch) is not None
    assert not hasattr(dist_common, "check_engine_epoch")
