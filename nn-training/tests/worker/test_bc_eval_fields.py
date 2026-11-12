"""B4/B5：BC 周期 eval 的行为门口径字段（2026-11-05）。

报告 §5：周期 eval 只落了 `pickup_mean`（拾取**个数**），判据要的是
`powerUpsCollected / powerUpsSpawned` 的**率**；`cellsVisited` / `moveHist` 都没进产物
⇒ 五项判据一项也测不到。这里钉住纯聚合的口径，防止以后又把「个数」当「率」。
"""

from __future__ import annotations

from worker.bc_eval import aggregate_eval_rows, build_eval_panel


def _row(**over: object) -> dict:
    r = {
        "win": False,
        "cleared": False,
        "outcome": "timeout",
        "ticks": 12000,
        "score": 100,
        "kills": 2,
        "playerHits": 1,
        "powerUpsCollected": 1,
        "powerUpsSpawned": 2,
        "cellsVisited": 60,
        "moveHist": [10, 20, 20, 20, 30],
    }
    r.update(over)
    return r


def test_pickup_rate_is_ratio_not_count() -> None:
    agg = aggregate_eval_rows(
        [
            _row(powerUpsCollected=2, powerUpsSpawned=2),
            _row(powerUpsCollected=0, powerUpsSpawned=2),
        ]
    )
    assert agg["pickup_mean"] == 1.0  # 旧口径：每局平均**个数**
    assert agg["pickup_rate"] == 0.5  # 门口径：Σ收集 / Σ生成
    assert agg["pickup_spawn_mean"] == 2.0


def test_zero_spawn_is_none_not_zero() -> None:
    agg = aggregate_eval_rows([_row(powerUpsCollected=0, powerUpsSpawned=0)])
    assert agg["pickup_rate"] is None, "0 分母 = 无读数，不能编成 0（会被当成真没捡）"


def test_cells_and_move_fraction() -> None:
    agg = aggregate_eval_rows(
        [
            _row(cellsVisited=100, moveHist=[0, 10, 0, 0, 0]),
            _row(cellsVisited=50, moveHist=[20, 0, 0, 0, 0]),
        ]
    )
    assert agg["cells_mean"] == 75.0
    # 槽 0 = 停/无输入；方向移动 10 / 总数 30
    assert agg["move_frac"] == 0.3333


def test_missing_optional_fields_degrade_to_none() -> None:
    agg = aggregate_eval_rows([{"win": True, "ticks": 100}])
    assert agg["cells_mean"] is None
    assert agg["move_frac"] is None
    assert agg["pickup_rate"] is None


def test_panel_records_both_ticks_declarations() -> None:
    """B4：关卡值 + 课程声明都进 panel——漂移可见，读数不跨面板相减。"""
    p = build_eval_panel(
        level_name="arena6",
        max_ticks=36000,
        difficulty="hard",
        lives=3,
        games_per_stage=30,
        stages=4,
        course_max_ticks=20000,
    )
    assert p == {
        "level": "arena6",
        # 2026-11-12：panel 加 source（levels=关卡文件 / course=继承课程环境）
        "source": "levels",
        "max_ticks": 36000,
        "course_max_ticks": 20000,
        "difficulty": "hard",
        "lives": 3,
        "games_per_stage": 30,
        "seed0": 1,
        "stages": 4,
    }
    # 未声明课程值时也不编造
    p2 = build_eval_panel(
        level_name="arena4",
        max_ticks=12000,
        difficulty="hard",
        lives=None,
        games_per_stage=10,
        stages=1,
    )
    assert p2["course_max_ticks"] is None and p2["lives"] is None


def test_empty_rows_have_all_keys() -> None:
    agg = aggregate_eval_rows([])
    for k in ("pickup_rate", "pickup_spawn_mean", "cells_mean", "move_frac"):
        assert k in agg and agg[k] is None
