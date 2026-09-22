"""test_eval_loot_fields — eval_log 掉落三列接线（x5⑧③）单测。

缺口：export-eval-game 报告顶层已有 puGotOther；powerUpsSpawned/starsCollected
只在 scorable.telemetry。eval_dispatch/batch_eval/eval_a_once 的 row 字典此前
只接了 puSpawn*/puGot{四桶}/puSpawnStar，导致 §5 供给上限只能从 rollout 反推。
本补齐刻意 Python-only：不改 codehash 集内的 export-eval-game.ts。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_local import EVAL_LOOT_KEYS, eval_loot_fields


def test_loot_keys_are_the_three_missing_columns() -> None:
    assert EVAL_LOOT_KEYS == ("powerUpsSpawned", "puGotOther", "starsCollected")


def test_top_level_preferred_over_scorable() -> None:
    man = {
        "powerUpsSpawned": 5,
        "puGotOther": 2,
        "starsCollected": 1,
        "scorable": {
            "telemetry": {
                "powerUpsSpawned": 99,
                "starsCollected": 99,
            }
        },
    }
    assert eval_loot_fields(man) == {
        "powerUpsSpawned": 5,
        "puGotOther": 2,
        "starsCollected": 1,
    }


def test_scorable_telemetry_fallback_when_top_level_absent() -> None:
    """export-eval-game 当前报告形态：puGotOther 顶层；spawn/stars 只在 scorable。"""
    man = {
        "puGotOther": 3,
        "scorable": {
            "telemetry": {
                "powerUpsSpawned": 4,
                "powerUpsCollected": 2,
                "starsCollected": 1,
            }
        },
    }
    got = eval_loot_fields(man)
    assert got["puGotOther"] == 3
    assert got["powerUpsSpawned"] == 4
    assert got["starsCollected"] == 1


def test_missing_everywhere_returns_none() -> None:
    assert eval_loot_fields({}) == {
        "powerUpsSpawned": None,
        "puGotOther": None,
        "starsCollected": None,
    }
    # 畸形 scorable 不得炸（旧 agent / 截断报告）
    assert eval_loot_fields({"scorable": None})["powerUpsSpawned"] is None
    assert eval_loot_fields({"scorable": {"telemetry": None}})["starsCollected"] is None
    assert eval_loot_fields({"scorable": "oops"})["puGotOther"] is None


def test_writers_wire_three_loot_columns() -> None:
    """源码级接线断言：eval_log 的写入方都必须落这三列。

    2026-09-22：手动 evalA（`rl/eval_a_once.py`）不再自己写行——它改为薄包装
    `rl/eval_dispatch.py::dispatch_eval_round`（与 in-loop 同一条派发路），三个
    写入方就此收敛成两个（见 docs/nn/runtime-opt.md §6）。

    2026-09-22（云机评估）：行构造收敛为**唯一实现点** `rl/eval_local.eval_row`
    （in-loop 派发器与 `remote/offline_eval.py` 共用）。于是判据分两档：

      * 走 `eval_row` 的写入方：三列由那一处保证（本文件只断言它们接了共享构造点）；
      * 自己拼行的写入方（`rl/batch_eval.py`）：必须自己接到 `eval_loot_fields`。
    """
    for rel, marker in (
        ("rl/eval_dispatch.py", "eval_row"),
        ("rl/batch_eval.py", "eval_loot_fields"),
        ("remote/offline_eval.py", "eval_row"),
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert marker in src, f"{rel} 没接上 {marker}（三列会静默缺传）"
        if "eval_loot_fields" in src:
            for key in EVAL_LOOT_KEYS:
                assert f'"{key}"' in src, f"{rel} missing {key}"
        else:
            assert (
                "eval_row" in src
            ), f"{rel} 既不接 eval_loot_fields 也不接 eval_row —— 自己拼行会漏列"


def test_m1_ingest_row_has_loot_schema_keys() -> None:
    from rl.eval_ingest import m1_game_row

    r = m1_game_row(
        {"stage": 0, "seed": 1, "win": False, "cleared": False},
        it=0,
        wver16="ab" * 8,
        policy="nn",
    )
    for key in EVAL_LOOT_KEYS:
        assert key in r, key
