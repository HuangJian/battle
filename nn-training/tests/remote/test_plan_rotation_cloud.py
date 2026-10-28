"""tests/remote/test_plan_rotation_cloud.py —— 云机轮的**轮换开关 + 账目搬运**。

plan/it-stage-ratation.plan.md §9.5（2026-10-28，用户口径「云机 rollout 也要支持轮换。
ipynb 加个配置开关」）。三条口径：

  ① **三态开关**（`CFG["rotation"]` → `--rotation`）：`auto` 跟随计划；`on` 计划没带轮换
     就**拒跑**（不猜「那就不轮换吧」）；`off` 把窗口降级成**全关**并留痕（语料分布变了
     = 另一个实验面，必须可读）；
  ② **账目搬运**：轮换读数（`rotation`）与分关账（`volume_stage_stats`）要能从产物行
     进到课程账本 —— 否则控制台对云机轮只能显 `—`（同一条曲线两条腿不可比）；
  ③ **缺数据不编 0**：未开轮换/老产物行 ⇒ 一个键都不多（老账本逐字节不变）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError
from remote.artifacts import ledger_row_from_metrics
from remote.plan_handoff import ROTATION_POLICIES, rotation_policy
from remote.plan_run import _row_extra, rotation_window_for_round
from tests.worker.test_plan_rotation_multiround import (
    ALL_STAGES,
    ESTS_ALL,
    _args,
    _recipe,
    _window_ests,
)
from worker.plan import build_plan
from worker.volume_waves import volume_block

ROTATION = {"mode": "even", "per_it": 2, "pinned": []}


def _plan_with_rotation() -> dict:
    args = _args()
    window = [2000, 2001]
    block = volume_block(
        args,
        est_samples_per_game=147,
        ests_by_stage={s: ESTS_ALL[s] for s in window},
        stages_override=window,
    )
    assert block is not None
    block["est_s_all"] = dict(ESTS_ALL)
    return build_plan(
        args,
        it=0,
        iters_total=4,
        rotate_seed=4242,
        volume=block,
        rotation=dict(ROTATION),
        log=lambda _m: None,
    )


def _plan_without_rotation() -> dict:
    args = _args(target_transitions=0)
    return build_plan(args, it=0, iters_total=4, rotate_seed=4242, log=lambda _m: None)


# ────────────────────────── ① 三态开关 ──────────────────────────


def test_auto_follows_the_plan() -> None:
    """auto：不覆盖窗口（计划带轮换就按配方逐轮解；不带就照旧）。"""
    assert rotation_window_for_round(_plan_with_rotation(), 2, "auto") == (None, "")
    assert rotation_window_for_round(_plan_without_rotation(), 2, "auto") == (None, "")


def test_on_refuses_a_plan_without_rotation() -> None:
    """★ on：计划没带轮换 ⇒ 拒跑（不是静默按全关采）。"""
    with pytest.raises(ProtocolError, match="rotation on"):
        rotation_window_for_round(_plan_without_rotation(), 2, "on")
    # 计划带了 ⇒ 正常放行（不覆盖）
    assert rotation_window_for_round(_plan_with_rotation(), 2, "on") == (None, "")


def test_off_downgrades_to_all_stages_and_records() -> None:
    """★ off：窗口降级成**全关** + 留痕标记（账本 `rotation.override` 的取值）。"""
    window, override = rotation_window_for_round(_plan_with_rotation(), 2, "off")
    assert window == ALL_STAGES
    assert override == "off"
    # 本来就没轮换 ⇒ 没有可降级的东西（老行为逐字节不变）
    assert rotation_window_for_round(_plan_without_rotation(), 2, "off") == (None, "")


def test_bad_policy_is_loud() -> None:
    """非法三态值在开跑之前拒（`--rotation` 的 choices 之外还有这一层）。"""
    assert rotation_policy("AUTO") == "auto"
    assert rotation_policy(None) == "auto"
    assert set(ROTATION_POLICIES) == {"auto", "on", "off"}
    for bad in ("yes", "1", "offf"):
        with pytest.raises(ValueError, match="rotation"):
            rotation_policy(bad)


# ────────────────────────── ② 账目搬运 ──────────────────────────


def test_row_extra_carries_rotation_and_volume_stats() -> None:
    """产物行带 `rotation` / `volume_stage_stats`（`_checkpoint` → `metrics_row(extra=…)`）。"""
    rot = {"mode": "even", "per_it": 2, "pinned": [], "all": 4, "window": [2000, 2001]}
    vstats = {"rule": "per-stage-v3", "quota": 1, "stages": {"2000": {"collected": 1}}}
    extra = _row_extra({"rotation": rot, "volume_stage_stats": vstats}, "train")
    assert extra == {"rotation": rot, "volume_stage_stats": vstats}
    # phase 只在非训练轮带（老口径不变）
    assert _row_extra({}, "eval") == {"phase": "eval"}
    assert _row_extra({}, "train") is None
    # 空/坏形状 ⇒ 一个键都不多（不编 0）
    assert _row_extra({"rotation": {}, "volume_stage_stats": None}, "train") is None


def test_ledger_row_maps_rotation_and_volume_stats() -> None:
    """★ 产物行 → 课程账本事件：两个键原样搬（控制台 `parseIterRotation/parseIterVolume` 读它）。"""
    rot = {"mode": "off", "per_it": 0, "pinned": [], "all": 4, "window": ALL_STAGES, "override": "off"}
    vstats = {
        "rule": "per-stage-v3",
        "est_src": "plan",
        "halted": True,
        "quota": 12000,
        "stages": {"2000": {"collected": 12000, "games": 82, "est_s": 100, "cap_games": 400}},
    }
    row = {
        "it": 5,
        "report": {"winRate": 0.4, "games": 82, "shards": 82, "totalSamples": 48000, "totalTicks": 1},
        "agg": {"kl": 0.01},
        "rotation": rot,
        "volume_stage_stats": vstats,
    }
    ev = ledger_row_from_metrics(row, run_id="r1", source="offline_backfeed")
    assert ev is not None
    assert ev["event"] == "iteration" and ev["iter"] == 5
    assert ev["rotation"] == rot
    assert ev["volume_stage_stats"] == vstats


def test_ledger_row_without_reads_stays_clean() -> None:
    """老产物行（无这两格）⇒ 事件里**没有键**（不是 null/0）——老账本逐字节不变。"""
    row = {"it": 3, "report": {"winRate": 0.2}, "agg": {"kl": 0.0}}
    ev = ledger_row_from_metrics(row, run_id="r1", source="deliver_import")
    assert ev is not None
    assert ev.get("rotation") is None
    assert ev.get("volume_stage_stats") is None
