"""test_eval_census_fields — Phase 0 逐敌种画像七列接线（T5 分敌种信用）单测。

背景：主端点「power 曝光归一命中/千 tick」需要 hitsByKind/killsByKind/
exposureByKind + 三个序列表；`export-eval-game.ts` 顶层自 2026-09-19 起携带，
但 A/B/C/m1 四条逐局行构造点此前都没有搬它们 ⇒ EvalStore 里查不到这一列。

本助手刻意只认**顶层**：这七列不像掉落三列那样在 `scorable.telemetry` 里
（那是 basePressure/powerUps 一类标量），所以没有 telemetry 回退；缺失一律
None（不伪造），交给 ingest 侧 PHASE0_FIELDS 计入覆盖率豁免。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_local import EVAL_CENSUS_KEYS, eval_census_fields


def test_keys_are_the_seven_phase0_columns() -> None:
    assert EVAL_CENSUS_KEYS == (
        "hitsByKind",
        "killsByKind",
        "exposureByKind",
        "firstHitKind",
        "firstKillKind",
        "killOrder",
        "killerKinds",
    )


def test_passthrough_values() -> None:
    man = {
        "hitsByKind": [1, 2, 3, 4],
        "killsByKind": [10, 5, 3, 2],
        "exposureByKind": [100, 50, 20, 10],
        "firstHitKind": "power",
        "firstKillKind": "fast",
        "killOrder": ["basic", "fast"],
        "killerKinds": ["armor", None],
    }
    assert eval_census_fields(man) == man


def test_missing_keys_are_none_not_fabricated() -> None:
    """旧报告/未同步节点：没有就是 None（ingest 侧进豁免清单，绝不填零冒充数据）。"""
    got = eval_census_fields({"outcome": "stage_clear"})
    assert set(got) == set(EVAL_CENSUS_KEYS)
    assert all(v is None for v in got.values())


def test_no_telemetry_fallback() -> None:
    """与 eval_loot_fields 的区别：即便 scorable.telemetry 里有同名键也不回退。"""
    man = {"scorable": {"telemetry": {"hitsByKind": [9, 9, 9, 9]}}}
    assert eval_census_fields(man)["hitsByKind"] is None


def test_none_and_non_dict_manifest_are_safe() -> None:
    assert all(v is None for v in eval_census_fields(None).values())
    assert all(v is None for v in eval_census_fields("not-a-dict").values())  # type: ignore[arg-type]
