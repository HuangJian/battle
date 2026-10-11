"""tests/worker/test_spread_games.py —— 补差批的**满铺**配额（`plan/rollout-topup-fastpath.plan.md` §3.3）。

钉住五件事：

  1. **批 = 机器宽度**：`sum(plan) == width`（除非真没事可干），批大小不再由缺口方程决定；
  2. **不再有「1 局的批」**：宽机器 + 少关数 ⇒ 每关 ≥ 2 局（这正是现场 48.9s 的那一类形状）；
  3. **硬顶是硬顶**：`game_cap` 余量不越；批变小的**唯一**理由是「所有 eligible 关都到顶了」；
  4. **达标关不进 eligible**；全达标 ⇒ `{}`（本轮不再补差）；
  5. **不吃 est / 不吃 `last_lo_factor`**：签名里就没有它们（改回缺口解算会当场红）。

纯函数、无 IO（同 `tests/worker/test_volume_alloc.py` 的形态）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.volume_alloc import spread_games_by_stage

STAGES = [2000, 2001, 2002]
TARGET = 6000
QUOTA = 2000  # ceil(6000/3)


def _spread(
    width: int,
    *,
    stages: list[int] = STAGES,
    collected: dict[int, int] | None = None,
    games_done: dict[int, int] | None = None,
    caps: dict[int, int] | None = None,
) -> dict[int, int]:
    return spread_games_by_stage(
        stages=stages,
        collected=collected or {},
        games_done=games_done or {},
        target_transitions=TARGET,
        game_caps=caps or {},
        width=width,
    )


def test_batch_size_is_the_machine_width_not_the_gap() -> None:
    """批大小 == width（三关轮转均分），与「缺口还剩多少」无关。"""
    assert _spread(6) == {2000: 2, 2001: 2, 2002: 2}
    assert _spread(7) == {2000: 3, 2001: 2, 2002: 2}  # 大余数落在 stages 给定序的前面
    assert sum(_spread(94).values()) == 94
    # 缺口极小（每关只差 1 个样本）也照样铺满 —— 这不是缺口方程的行为。
    almost = {s: QUOTA - 1 for s in STAGES}
    assert sum(_spread(94, collected=almost).values()) == 94


def test_no_one_game_batch_while_the_machine_is_wide() -> None:
    """宽机器（width ≥ 2×关数）⇒ 每关 ≥ 2 局；退化档（width < 关数）才允许 1 局。"""
    plan = _spread(94)
    assert min(plan.values()) >= 2, plan
    assert _spread(2) == {2000: 1, 2001: 1}  # 机器比关数窄：前 width 个关各 1 局
    assert _spread(1) == {2000: 1}


def test_game_cap_is_a_hard_ceiling_and_the_batch_only_shrinks_for_that() -> None:
    """cap 余量不越：一关到顶 ⇒ 局流转给还有余量的关；全到顶 ⇒ 批才变小。"""
    caps = {2000: 2, 2001: 100, 2002: 100}
    got = _spread(6, caps=caps)
    assert got[2000] == 2, "cap 余量 2 ⇒ 最多 2 局（不越顶）"
    assert sum(got.values()) == 6, "余下的局轮转给其它关 ⇒ 批仍是 width"
    # 全到顶：批小于 width —— 这是唯一允许变小的情形（课程字段是硬顶）
    all_capped = _spread(6, games_done={s: 5 for s in STAGES}, caps={s: 5 for s in STAGES})
    assert all_capped == {}
    assert _spread(6, games_done={2000: 5}, caps={2000: 5}) == {2001: 3, 2002: 3}


def test_met_stages_are_excluded_and_empty_means_stop() -> None:
    """达标关不进 eligible（不再补差）；eligible 空 ⇒ `{}`（调用方据此收工）。"""
    met = {2000: QUOTA}
    assert _spread(6, collected=met) == {2001: 3, 2002: 3}
    assert _spread(6, collected={s: QUOTA for s in STAGES}) == {}
    assert _spread(6, collected={s: QUOTA + 10_000 for s in STAGES}) == {}


def test_it_does_not_take_est_or_the_last_batch_discount() -> None:
    """「不吃 est / 不吃 `last_lo_factor`」是设计（批大小只问机器能同时跑几局）——签名即契约。

    缺口解算那套旋钮一旦被“顺手加回来”（est / batches_left / last_lo_factor），满铺就退化成
    旧口径（批随缺口变小）⇒ 这条当场红，不需要任何行为用例去猜。
    """
    params = set(inspect.signature(spread_games_by_stage).parameters)
    assert params == {
        "stages",
        "collected",
        "games_done",
        "target_transitions",
        "game_caps",
        "width",
    }, params


def test_width_and_stages_are_validated_loudly() -> None:
    """`width ≤ 0` / 空 stages ⇒ 响亮（静默返回空会被读成「已达标」）。"""
    with pytest.raises(ValueError):
        _spread(0)
    with pytest.raises(ValueError):
        _spread(-1)
    with pytest.raises(ValueError):
        _spread(4, stages=[])


def test_single_stage_gets_the_whole_width() -> None:
    """单关课程：整批都给它（这也是云机最常见的一档：1.15 上界下的首批只有一关未达标）。"""
    assert _spread(94, stages=[2000]) == {2000: 94}
