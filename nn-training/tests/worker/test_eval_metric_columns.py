"""eval 行的 metrics 列集合守卫（plan/o1-eval-metrics-remediation §6 S1，2026-11-08）。

这是评审 P0-1/P0-2 的可执行形态：

* 27 列缺口在切片 5 后全部可补（`EVAL_METRIC_EXEMPT_KEYS` 为空；该表仍保留为未来
  新增列的唯一登记处，不是「想不补就不补」的清单）；
* 两个行构造点（`eval_row` 与 `trainer/batch_runner.py::record`）都经 `eval_metric_fields`
  取数 ⇒ 守卫钉在聚合入口上：**删一列就红**（运行时删生产代码的键做不到，这是唯一可执行
  的负测试形态，评审 P2-2）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.eval_rows as eval_rows
from biz.reward_library import METRICS

EXTRA = set(eval_rows.EVAL_EXTRA_KEYS)
EXEMPT = set(eval_rows.EVAL_METRIC_EXEMPT_KEYS)


def _manifest() -> dict:
    """满键 eval 报告（`export-eval-game.ts` 顶层 + `scorable.telemetry` 回退面）。"""
    m: dict = {
        "win": True,
        "cleared": True,
        "outcome": "stage_clear",
        "ticks": 321,
        "score": 12.5,
        "quality": 0.9,
        "dims": {},
        "kills": 2,
        "enemyHits": 3,
        "hitRate": 0.5,
        "powerUpsCollected": 1,
        "playerDamageTaken": 4,
        "playerHits": 1,
        "enemyTotal": 2,
        "playerDeaths": 0,
        "playerShots": 6,
        "playerLevel": 1,
        "cellsVisited": 20,
        "firstKillTick": 30,
        "stuckTicks": 0,
        "puSpawnBomb": 0,
        "puSpawnTank": 0,
        "puSpawnFreeze": 0,
        "puSpawnShield": 0,
        "puSpawnStar": 1,
        "puGotBomb": 0,
        "puGotTank": 0,
        "puGotFreeze": 0,
        "puGotShield": 0,
        "elapsedSec": 3.2,
        "policy": "nn",
        # metrics v14：27 列补齐面（报告顶层；切片 5 后含 pickupDist + th*）。
        "lives": 1,
        "baseAlive": True,
        "baseWallTotal": 8,
        "baseWallIntact": 8,
        "basePressureMean": 0.25,
        "basePressureSamples": 40,
        "clearTick": 400,
        "enemyDist": 95.5,
        "killsByKind": [1, 1, 0, 0],
        "hitsByKind": [2, 1, 0, 0],
        "exposureByKind": [10, 5, 0, 0],
        "firstHitKind": "basic",
        "firstKillKind": "basic",
        "killOrder": ["basic", "fast"],
        "killerKinds": ["basic", None],
        # metrics v13（切片 5）：`pickupDist` + `th*` 10 列（报告顶层）。
        "pickupDist": 7,
        "thBreach": 1,
        "thBreachAimed": 0,
        "thDirect": 1,
        "thDirectAimed": 0,
        "thShotsAtBaseCum": 3,
        "thBulletsAtBase": 1,
        "thCostBreach": 4,
        "thCostBreachAimed": 1,
        "thCostDirect": 6,
        "thCostDirectAimed": 2,
        # 机制闸 + 新纪元通道。
        "wallPressTicks": 3,
        "aliveTicks": 300,
        "moveHist": [1],
        "decisions": 30,
        "idleTicks": 10,
        "stopRuns": [2],
        # 掉落列在 telemetry 里（顶层没有 starsCollected / puGotOther），sum 也走原值。
        "scorable": {
            "telemetry": {
                "basePressureSum": 9.5,
                "powerUpsSpawned": 2,
                "starsCollected": 1,
                "puGotOther": 0,
            }
        },
    }
    for k in (
        eval_rows.EVAL_V8_KEYS
        + eval_rows.EVAL_V9_KEYS
        + eval_rows.EVAL_V10_KEYS
        + eval_rows.EVAL_V11_KEYS
        + eval_rows.EVAL_V12_KEYS
        + eval_rows.EVAL_V13_KEYS
    ):
        m[k] = 1
    return m


def _eval_row(m: dict) -> dict:
    return eval_rows.eval_row(m, it=0, key16="a" * 16, task=(2000, 860001), node="self")


def _tool_row(m: dict) -> dict:
    """延迟 import 入口模块，并还原它导入时改道的 `sys.stdout`（模块级副作用）。"""
    old = sys.stdout
    try:
        mod = importlib.import_module("trainer.eval_course_once")
    finally:
        sys.stdout = old
    row = _eval_row(m)
    row["ckpt_sha16"] = "a" * 16
    meta = {("a" * 16, 2000, 860001): {"label": "w", "weightIdx": 0, "stageName": "s"}}
    out: dict = mod._to_tool_row(row, meta, 4, 860001, 4)
    return out


def test_extra_and_exempt_partition_the_27_column_gap() -> None:
    """27 = 27 可补 + 0 豁免；两表互斥且都是 METRICS 的子集。"""
    assert len(EXTRA) == 27, sorted(EXTRA)
    assert len(EXEMPT) == 0, sorted(EXEMPT)
    assert not EXTRA & EXEMPT, sorted(EXTRA & EXEMPT)
    assert set(METRICS) >= EXTRA | EXEMPT, sorted((EXTRA | EXEMPT) - set(METRICS))


def test_eval_row_carries_every_metrics_column() -> None:
    row = _eval_row(_manifest())
    assert set(METRICS) - set(row) == EXEMPT, sorted(set(METRICS) - set(row))


def test_batch_runner_channel_carries_the_same_extra_columns() -> None:
    """P0-2：`batch_runner.record` 走 `eval_metric_fields`，不许只修 `eval_row`。"""
    assert set(eval_rows.eval_metric_fields(_manifest())) >= EXTRA


def test_to_tool_row_carries_every_metrics_column() -> None:
    """L3 的输入是 B 层行（不是 eval_row 的另一份白名单）：按 METRICS 原样过桥。"""
    assert set(METRICS) - set(_tool_row(_manifest())) == EXEMPT


def test_guard_goes_red_when_a_column_disappears() -> None:
    """负测试：从报告里删掉 `clearTick`，两个行构造点都必须暴露缺口。"""
    m = _manifest()
    del m["clearTick"]
    assert not set(METRICS) - set(_eval_row(m)) <= EXEMPT
    assert not set(METRICS) - set(_tool_row(m)) <= EXEMPT


def test_pickup_dist_keeps_the_rollout_sentinel() -> None:
    """-1 是合法哨兵（无存活拾取/玩家不在场），不是「缺键」；缺键才整键省略。"""
    m = _manifest()
    m["pickupDist"] = -1
    assert _eval_row(m)["pickupDist"] == -1
    m.pop("pickupDist")
    assert _eval_row(m).keys().isdisjoint(("pickupDist",))


def test_clear_tick_null_becomes_the_rollout_sentinel() -> None:
    """哨兵统一为 -1；值仍是报告给的 tick（终局口径，见 §8）。"""
    m = _manifest()
    m["clearTick"] = None
    assert _eval_row(m)["clearTick"] == -1
    m["clearTick"] = 345
    assert _eval_row(m)["clearTick"] == 345


def test_null_base_alive_is_omitted_not_coerced_to_false() -> None:
    """`null` = 无读数 ⇒ 整键省略；只有明确 `False` 才写 0。"""
    m = _manifest()
    m["baseAlive"] = None
    assert _eval_row(m).keys().isdisjoint(("baseAlive",))
    m["baseAlive"] = False
    assert _eval_row(m)["baseAlive"] == 0


def test_base_pressure_sum_prefers_telemetry_then_mean_times_samples() -> None:
    row = _eval_row(_manifest())
    assert row["basePressureSum"] == 9.5  # telemetry 原值优先
    m = _manifest()
    m.pop("scorable")
    assert _eval_row(m)["basePressureSum"] == 10.0  # 无 telemetry ⇒ mean × samples
