"""test_eval_m1_once — 一次性 m1 评估入口的纯逻辑单测（2026-09-19）。

覆盖三个易漂移的约定：
  1. `plan_units` 的 spec → B 层 unit 归一（内置关 stageJson 为空 = 不覆盖 lives/level）；
  2. `to_m1_row` 的 B 层行 → m1 JSONL 契约映射（0/1 → bool、scorable 原样带回、error 局 ok=False）；
  3. policy → kind 分桶表（上传与查询必须同 kind，否则 agent 一律 409）。

节点通信与重试**不在此测**：那是 `BatchEvalRunner` + `dist_common` 的既有测试面。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nn-training"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "eval_m1_once", str(ROOT / "nn-training" / "eval_m1_once.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


emo = _load()


def _spec(**over: object) -> dict:
    base = {
        "policy": "goal",
        "weights": "tmp/gw.json",
        "difficulty": "hard",
        "maxTicks": 36000,
        "units": [{"stageId": 0, "seeds": [1, 2]}, {"stageId": 7, "seeds": [3]}],
    }
    base.update(over)
    return base


def test_plan_units_builtin_stages_and_no_lives_override() -> None:
    units = emo.plan_units(_spec())
    assert [u["stageId"] for u in units] == [0, 7]
    assert units[0]["seeds"] == [1, 2]
    # 内置关 = 空 stageJson（agent/export 侧按 stage 索引解析）
    assert all(u["stageJson"] == "" for u in units)
    # 关键契约：unit **不带** lives/level ⇒ batch_eval 不覆盖（难度默认说了算）
    assert all("lives" not in u and "level" not in u for u in units)
    assert units[0]["maxTicks"] == 36000
    assert units[0]["difficulty"] == "hard"
    assert units[0]["rung"] == "goal·s0"


def test_plan_units_skips_empty_and_rejects_negative_stage() -> None:
    units = emo.plan_units(_spec(units=[{"stageId": 3, "seeds": []}, {"stageId": 4, "seeds": [9]}]))
    assert [u["stageId"] for u in units] == [4]
    with pytest.raises(ValueError):
        emo.plan_units(_spec(units=[{"stageId": -1, "seeds": [1]}]))


def test_plan_units_keeps_ts_corpus_order() -> None:
    """行序口径：unit 顺序 = 调用方给的顺序（TS 侧按 stage 升序构造），seed 顺序原样。"""
    units = emo.plan_units(_spec(units=[{"stageId": 2, "seeds": [5, 4, 6]}]))
    assert units[0]["seeds"] == [5, 4, 6]


def test_to_m1_row_maps_bools_lives_and_scorable() -> None:
    scorable = {
        "outcome": "stage_clear",
        "ticks": 1200,
        "finalState": {"killCount": 20, "lives": 2, "baseAlive": True},
        "firstKillTick": 300,
        "telemetry": {"baseWallIntact": 12, "baseWallTotal": 16, "playerShots": 55},
    }
    row = emo.to_m1_row(
        {
            "event": "eval",
            "stage": 1,
            "seed": 7,
            "node": "mac",
            "outcome": "stage_clear",
            "win": 1,
            "cleared": 1,
            "ticks": 1200,
            "kills": 20,
            "firstKillTick": 300,
            "enemyTotal": 20,
            "playerDeaths": 1,
            "playerShots": 55,
            "powerUpsCollected": 2,
            "playerLevel": 3,
            "cellsVisited": 88,
            "scorable": scorable,
        }
    )
    assert row["win"] is True and row["cleared"] is True  # 行里是 0/1
    assert row["lives"] == 2 and row["baseAlive"] is True  # 从 scorable.finalState 取
    assert row["kills"] == 20 and row["node"] == "mac" and row["ok"] is True
    # scoreV7 的输入原样带回（TS 侧直接喂 scoreRun，不做字段级搬运）
    assert row["scorable"] is scorable


def test_to_m1_row_error_game_and_missing_scorable() -> None:
    row = emo.to_m1_row({"stage": 0, "seed": 1, "outcome": "error", "node": "a95"})
    assert row["ok"] is False
    assert row["win"] is False and row["cleared"] is False
    assert row["scorable"] is None
    assert row["lives"] is None and row["baseAlive"] is None
    assert row["ticks"] == 0 and row["kills"] == 0


def test_dispatchable_policies_match_kind_table() -> None:
    """可分派集合与 kind 分桶表必须互相对得上（kind 错 = 一律 409，静默全 error）。"""
    from rl.batch_eval import KIND_FOR_POLICY, kind_for_policy

    assert set(emo.DISPATCHABLE) == {"nn", "intent-exec", "goal", "god"}
    assert kind_for_policy("intent-exec") == "intent"
    assert kind_for_policy("goal") == "goal"
    assert kind_for_policy("nn") == "rollout"
    assert kind_for_policy("god") == "rollout"
    # 表里每个可分派 policy 都有明确 kind（不靠回落猜测）
    assert all(p in KIND_FOR_POLICY for p in emo.DISPATCHABLE)
