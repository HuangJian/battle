"""tests/worker/test_plan_rotation_multiround.py —— 多轮离线计划里的**逐轮窗口**。

plan/it-stage-ratation.plan.md §9.5（2026-10-28，用户口径「云机 rollout 也要支持轮换」）。

为什么这组用例必须存在：`plan["volume"]` 是**一份**块（钉着导出那一轮的窗口），而窗口是
`it` 的函数。v1 的解法是「轮换 + 多轮包一律拒导」（§3.7）；现在改成把**轮换配方**写进
计划、每轮按配方重解自己的块。这个改动最危险的失效形态是**静默**的：

  * 第 2 轮起还在用第 1 轮的关集（采了不该采的关，缺了该采的关，账面看不出来）；
  * 逐关局数用「别的窗口」的 est 反解（G_s 与本机链不同源 ⇒ 样本量对不上目标）；
  * 配方非法/est 表缺关，跑到半途才炸（已经产了一批不可信 shard）。

所以这里钉四件事：① 每轮窗口 == `window_for`；② 每轮局数 == 同一个分配器；
③ 导出那一轮的**派生块**与计划里钉的块逐字段相同（同源）；④ 缺/坏配置在交接前响亮。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError
from worker.plan import (
    build_plan,
    check_plan_against_args,
    dump_plan,
    pairs_for,
    planned_iters,
    rotation_recipe,
    validate_plan,
    volume_for_round,
)
from worker.stage_rotation import window_from_recipe
from worker.volume_alloc import DEFAULT_EST_HI_FACTOR, alloc_games_by_stage, est_hi
from worker.volume_waves import volume_block

STAGES_TEXT = "2000-2003"
ALL_STAGES = [2000, 2001, 2002, 2003]
TARGET = 48000
EST = 147
# 全关 est 表：故意让各关不同（同源用例才有区分度）+ 一关 0（无估计 ⇒ 回退全局 est）。
ESTS_ALL = {2000: 100, 2001: 150, 2002: 80, 2003: 0}


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 `tests/worker/test_volume_plan_block.py::_args` 同形）。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": STAGES_TEXT,
        "seed_rotate": 150,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
        "mode": "per-tick",
        "target_transitions": TARGET,
        "est_samples_per_game": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _recipe(mode: str = "even", per_it: int = 2, pinned: list[int] | None = None) -> dict:
    return {"mode": mode, "per_it": per_it, "pinned": list(pinned or [])}


def _window_ests(window: list[int]) -> dict[int, int]:
    """导出那一轮窗口的 est（与 `loop_export` 同规：从全关表里切）。"""
    return {s: ESTS_ALL[s] for s in window}


def _plan(
    *,
    recipe: dict | None = None,
    iters_total: int = 4,
    it: int = 0,
    rotate_seed: int = 4242,
) -> dict:
    """一份真实的轮换计划（走 `build_plan` 的同一条路：窗口 + 分关块 + 自检）。"""
    args = _args()
    recipe = recipe or _recipe()
    window = window_from_recipe(recipe, it + 1, ALL_STAGES, rotate_seed=rotate_seed)
    block = volume_block(
        args,
        est_samples_per_game=EST,
        ests_by_stage=_window_ests(window),
        stages_override=window,
    )
    assert block is not None
    block["est_s_all"] = dict(ESTS_ALL)
    return build_plan(
        args,
        it=it,
        iters_total=iters_total,
        rotate_seed=rotate_seed,
        volume=block,
        rotation=recipe,
        log=lambda _m: None,
    )


def test_each_round_replays_its_own_window() -> None:
    """★ 每轮对集的关集 == 该轮窗口（不是导出那一轮的窗口）。"""
    plan = _plan()
    rotation = rotation_recipe(plan)
    assert rotation is not None
    seen: dict[int, list[int]] = {}
    for it in planned_iters(plan):
        window = window_from_recipe(
            rotation, it, ALL_STAGES, rotate_seed=int(plan["rotate_seed"])
        )
        seen[it] = window
        pairs = pairs_for(plan, it)
        assert {s for s, _ in pairs} == set(window)
        # 每关局数 = 同一个分配器（`volume_block` 与派生块共用）反解的结果
        block = volume_for_round(plan, it)
        assert block is not None
        games = alloc_games_by_stage(
            window,
            TARGET,
            ests_hi={s: est_hi(_window_ests(window)[s] or EST) for s in window},
        )
        assert len(pairs) == sum(games.values())
    # 2 轮（4 关 ÷ 每轮 2 关）铺满一遍：并集 == 全集，且两轮不重复
    assert set(seen[1]) | set(seen[2]) == set(ALL_STAGES)
    assert not set(seen[1]) & set(seen[2])


def test_export_round_block_matches_the_pinned_block() -> None:
    """★ 同源：导出那一轮的**派生块**与计划里钉的块逐字段相同。

    派生（`volume_for_round`）与钉（`volume_block`）是两条写路径；只要它们对同一窗口
    给出同一份数字，「非导出轮」的口径才可信。
    """
    plan = _plan()
    pinned = plan["volume"]
    derived = volume_for_round(plan, 1)
    assert derived is not None
    for key in (
        "target_transitions",
        "est_samples_per_game",
        "stages",
        "games_per_stage",
        "per_stage_quota",
        "est_s_by_stage",
        "est_hi_factor",
        "games_per_stage_by_stage",
    ):
        assert derived[key] == pinned[key], f"{key} 派生与钉住的块不同源"
    # `widen_width` 是运行时的事：派生块一律未加宽
    assert derived.get("widen_width") is None


def test_pinned_recipe_keeps_pinned_stage_every_round() -> None:
    """pinned 关每轮都在窗口里（其余关轮换）；对集随之每轮含它。"""
    plan = _plan(recipe=_recipe("pinned", per_it=1, pinned=[2003]))
    for it in planned_iters(plan):
        pairs = pairs_for(plan, it)
        assert 2003 in {s for s, _ in pairs}


def test_off_downgrade_is_all_stages_and_requotas() -> None:
    """笔记本 `--rotation off` 的降级面：块换成**全关**，达标线按全关数重解。"""
    plan = _plan()
    block = volume_for_round(plan, 2, window_override=ALL_STAGES)
    assert block is not None
    assert block["stages"] == ALL_STAGES
    assert block["per_stage_quota"] == 12000  # ceil(48000/4)
    # 降级后 `remote/plan_run` 会把配方摘掉（块已是全关）⇒ `pairs_for` 直接重放，不再派生
    downgraded = {**plan, "volume": block, "rotation": None}
    assert {s for s, _ in pairs_for(downgraded, 2)} == set(ALL_STAGES)


def test_rotation_survives_dump_and_validate() -> None:
    """计划落盘再读回：配方与全关 est 表原样（`validate_plan` 是交接/续跑同一道门）。"""
    plan = _plan()
    raw = json.loads(dump_plan(plan).decode("utf-8"))
    again = validate_plan(raw)
    assert again["rotation"] == plan["rotation"]
    assert again["volume"]["est_s_all"] == {str(k): v for k, v in ESTS_ALL.items()}
    # dump 的 key 顺序稳定 ⇒ 同一份计划两次落盘逐字节相同（sha 是 job 身份）
    assert dump_plan(again) == dump_plan(plan)


def test_plan_without_recipe_is_byte_identical() -> None:
    """未开轮换 ⇒ 计划里**没有** rotation 键、块里没有 est_s_all（老包逐字节不变）。"""
    args = _args()
    block = volume_block(args, est_samples_per_game=EST, ests_by_stage=_window_ests(ALL_STAGES))
    assert block is not None
    plan = build_plan(args, it=0, iters_total=4, rotate_seed=4242, volume=block, log=lambda _m: None)
    assert plan.get("rotation") is None
    assert plan["volume"].get("est_s_all") is None
    # 每轮同一个关集（全集）
    for it in planned_iters(plan):
        assert {s for s, _ in pairs_for(plan, it)} == set(ALL_STAGES)


def test_rotation_without_volume_block_is_refused() -> None:
    """配了轮换却没有 `volume` 块 ⇒ 拒发（轮换只在按样本量的配额下生效）。"""
    with pytest.raises(ProtocolError, match="volume"):
        build_plan(
            _args(),
            it=0,
            iters_total=4,
            rotate_seed=4242,
            volume=None,
            rotation=_recipe(),
            log=lambda _m: None,
        )


def test_plan_with_rotation_requires_est_s_all() -> None:
    """派生非导出轮要用全关 est 表：缺表/缺关一律在 `validate_plan` 响亮拒收。"""
    plan = _plan()
    broken = json.loads(dump_plan(plan).decode("utf-8"))
    del broken["volume"]["est_s_all"]
    with pytest.raises(ProtocolError, match="est_s_all"):
        validate_plan(broken)

    partial = json.loads(dump_plan(plan).decode("utf-8"))
    partial["volume"]["est_s_all"] = {"2000": 100, "2001": 150}
    with pytest.raises(ProtocolError, match="缺关"):
        validate_plan(partial)


def test_bad_recipe_values_are_loud() -> None:
    """未知 mode / 负 per_it / 非整数 pinned ⇒ `ProtocolError`（不是运行时才炸）。"""
    args = _args()
    block = volume_block(args, est_samples_per_game=EST, ests_by_stage=_window_ests(ALL_STAGES))
    assert block is not None
    block["est_s_all"] = dict(ESTS_ALL)
    for bad in (
        {"mode": "bogus", "per_it": 2, "pinned": []},
        {"mode": "even", "per_it": -1, "pinned": []},
        {"mode": "even", "per_it": 2, "pinned": ["x"]},
    ):
        with pytest.raises(ProtocolError):
            build_plan(
                args, it=0, iters_total=4, rotate_seed=4242, volume=block,
                rotation=bad, log=lambda _m: None,
            )


def test_check_plan_against_args_catches_stage_set_drift() -> None:
    """发布期自检：args 的关集与计划不一致 ⇒ 拒（逐轮窗口不改变这条守卫）。"""
    plan = _plan()
    ok = _args()
    check_plan_against_args(ok, plan)  # 一致 ⇒ 静默通过
    with pytest.raises(ProtocolError, match="自检"):
        check_plan_against_args(_args(stages="2000-2002"), plan)


def test_derived_blocks_are_same_source_validated() -> None:
    """派生块必须过 `validate_runtime_volume` 的同源复算（够格进逐局 spec）。"""
    from worker.volume_alloc import validate_runtime_volume

    plan = _plan()
    for it in planned_iters(plan):
        block = volume_for_round(plan, it)
        assert block is not None
        out = validate_runtime_volume({**block, "it": it, "rotate_seed": plan["rotate_seed"]})
        assert out["stages"] == block["stages"]
        assert out["per_stage_quota"] == block["per_stage_quota"]


def test_est_hi_factor_is_carried() -> None:
    """`est_hi_factor` 从块里带过来（别用默认值静默替换掉导出时的口径）。"""
    plan = _plan()
    block = volume_for_round(plan, 2)
    assert block is not None
    assert block["est_hi_factor"] == pytest.approx(DEFAULT_EST_HI_FACTOR)
