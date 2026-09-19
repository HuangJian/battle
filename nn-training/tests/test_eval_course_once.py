"""test_eval_course_once — 一次性课程评估入口的**纯逻辑**单测（2026-09-19）。

覆盖三个易漂移的约定：
  1. `_stage_plan` 的种子段映射必须与 TS 侧 `buildCourseJobs` 逐值相同
     （stageLocal = g % n，seed = seed0 + g // n）——两端同语料才谈得上对拍；
  2. `_row_id` 是 `buildCourseJobs` 的 `wi * games + g` 的反解（行序对齐）；
  3. `_to_tool_row` 的 B 层行 → 工具 JSONL 契约映射（字段名/布尔化/缺列不伪造）。

节点通信与重试**不在此测**：那是 `BatchEvalRunner` + `dist_common` 的既有测试面。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nn-training"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "eval_course_once", str(ROOT / "nn-training" / "eval_course_once.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eco = _load()


def test_stage_plan_matches_ts_build_course_jobs() -> None:
    # TS: stageLocal = g % nStages ; seed = seed0 + floor(g / nStages)
    # g=0..6：s0 → 100,101,102；s1 → 100,101；s2 → 100,101（每关同一 seed 段内各取自己的那份）
    plan = eco._stage_plan(games=7, n_stages=3, seed0=100)
    assert plan == [[100, 101, 102], [100, 101], [100, 101]]
    # 局数 < 关卡数：前面的关各 1 局，后面的关为空
    assert eco._stage_plan(games=2, n_stages=4, seed0=9) == [[9], [9], [], []]


def test_row_id_is_inverse_of_game_index() -> None:
    # games=6, n=3 ⇒ g = (seed - seed0) * n + stageLocal 必须落回 0..5
    seen = set()
    for g in range(6):
        stage_local, seed = g % 3, 405000 + g // 3
        seen.add(eco._row_id(405000, 3, 6, 0, stage_local, seed))
    assert seen == set(range(6))
    # 第二个权重的 id 段偏移 = games
    assert eco._row_id(405000, 3, 6, 1, 0, 405000) == 6


def test_to_tool_row_maps_b_layer_row() -> None:
    raw = {
        "stage": 2001,
        "seed": 405002,
        "outcome": "stage_clear",
        "win": 1,
        "cleared": 1,
        "ticks": 1234,
        "kills": 7,
        "enemyHits": 21,
        "playerHits": 2,
        "playerDamageTaken": 400,
        "playerShots": 30,
        "powerUpsCollected": 3,
        "score": 0.5,
        "hitsByKind": [3, 2, 1, 1],
        "killsByKind": [3, 2, 1, 1],
        "exposureByKind": [100, 200, 300, 400],
        "firstHitKind": "basic",
        "firstKillKind": "fast",
        "killOrder": ["basic", "fast"],
        "killerKinds": [None, "basic"],
        "node": "a95",
    }
    meta = {(2001, 405002): {"label": "it30", "weightIdx": 0, "stageName": "ladder-c06"}}
    row = eco._to_tool_row(raw, meta, 3, 405000, 6)
    assert row["label"] == "it30"
    assert row["stageId"] == 2001
    assert row["stageName"] == "ladder-c06"
    assert row["id"] == 7  # g = (405002-405000)*3 + 1（buildCourseJobs 的 id 反解）
    assert row["win"] is True and row["cleared"] is True  # B 层 1 → 工具契约 bool
    assert row["hitsByKind"] == [3, 2, 1, 1]
    assert row["killOrder"] == ["basic", "fast"]
    assert row["node"] == "a95"  # 来源列保留（判读「这批局谁跑的」不再靠 TS 自报）


def test_to_tool_row_does_not_fabricate_missing_columns() -> None:
    raw = {"stage": 2000, "seed": 5, "outcome": "max_ticks", "win": 0, "cleared": 0}
    meta = {(2000, 5): {"label": "god", "weightIdx": 0, "stageName": "s"}}
    row = eco._to_tool_row(raw, meta, 1, 5, 1)
    assert row["win"] is False and row["cleared"] is False
    assert row["hitsByKind"] == [] and row["killerKinds"] == []  # 缺列 → 空表，不伪造零值
    assert row["firstHitKind"] is None
