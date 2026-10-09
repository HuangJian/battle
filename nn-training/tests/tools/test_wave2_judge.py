"""`tools/wave2_judge.py` 契约（Wave 2 b 腿判据段判决，2026-09-30）。

要钉住的三件事：
  · **共用算术只有一份**：主终点 / 端点 spec / 配对统计全部来自 `tools/paired_power.py`
    （按路径加载，`WJ.PP`），本工具不许复制一份（否则两处会分叉）；
  · **配对键的第一元是判据点**：一次性评估账本 `iter` 恒 0，拿它当键会把 it30/35/40 折成一批
    （这是本工具诞生时的实测坑：2700 行全「未知 wver」）；
  · **守卫线的方向语义**：`rel_down` / `rel_up` / `abs_down` / `abs_up` 四种，各自能分辨
    「已证实破线」与「只是没证据」——§11 的 13 条线一条都不能漏。

端到端判决读数（b1 −10.60% / b2 +3.19%、§11 十三条线）**不再钉死在测试里**（2026-10-09，`plan/nn-training-test-debt-cleanup.plan.md` §2-T1）：它们的输入是 `tmp/` 下的未入库账本 ⇒ 门禁里只有 skip 空壳；结论已归档，要重跑就 `bash tools/githook/nn-py-safe.sh -m tools.wave2_judge`。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    """按**文件路径**加载 `tools/wave2_judge.py`（tools/ 无 `__init__.py`，同 test_paired_power）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_wave2_judge_under_test", NN_ROOT / "tools" / "wave2_judge.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # 先登记再 exec（同 test_paired_power 的理由）
    spec.loader.exec_module(mod)
    return mod


WJ = _load()


# ─────────────────────────────────────────────────────────────────────────────
# 共用算术只有一份 + 常量与课程头注一致
# ─────────────────────────────────────────────────────────────────────────────
def test_shared_arithmetic_comes_from_paired_power_not_a_second_copy() -> None:
    assert WJ.PP.ra2({"onLaneTicks": 7, "ticks": 99}) == pytest.approx(0.07)
    assert hasattr(WJ.PP, "endpoint_pair_stats") and hasattr(WJ.PP, "ENDPOINTS")
    # 端点 spec 与阈值表的标签必须一一对应（端点改名 ⇒ 立刻红，防守卫表静默漏线）
    labels = {ep.label for ep in WJ.PP.ENDPOINTS}
    assert set(WJ.MARGINS) == labels
    assert len(labels) == 13
    # 与课程头注 ② 逐字对应的常量
    assert WJ.ARMS == ("b0", "b1", "b2") and WJ.CONTROL == "b0" and WJ.TREATED == ("b1", "b2")
    assert WJ.ITS == (30, 35, 40) and WJ.SEED0 == 862001 and WJ.GAMES_PER_ARM == 300
    assert WJ.F_PCT == {"b0": 0.0, "b1": 7.5, "b2": 15.0}
    assert WJ.BURN_MARGIN_PP == 5.0  # 来自 rl.kickstart_burn（止损口径的单一来源）


def test_params_used_are_the_ones_the_courses_preregistered() -> None:
    """预注册的数字只许来自课程头注：尾巴 3 点 × 300 局/点 @ 池外 862001。"""
    assert WJ.SEED0 == 862001 and WJ.SEED0 + WJ.GAMES_PER_ARM - 1 == 862300
    assert [WJ.LEDGERS[it].name for it in WJ.ITS] == ["eval_log.jsonl"] * 3
    assert "/tmp/h4-lane-judge/" in WJ.LEDGERS[30].as_posix().replace("\\", "/")
    # 守卫表阈值（§11 那几个数）
    assert WJ.MARGINS["主终点 rA2"] == ("rel_down", 16.0)
    assert WJ.MARGINS["洞守卫 静止∧在线"] == ("abs_up", 0.007927)
    assert WJ.MARGINS["零伤局"] == ("rel_down", 15.4)
    assert WJ.MARGINS["pass"] == ("abs_down", 0.03)
    assert WJ.MARGINS["ticks/局"] is None and WJ.MARGINS["timeout"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 归属与配对键
# ─────────────────────────────────────────────────────────────────────────────
def test_load_rows_keys_by_judge_point_not_ledger_iter(tmp_path: Path) -> None:
    """一次性评估账本的 `iter` 恒 0 ⇒ 配对键第一元必须是**判据点**（来自权重归属）。"""

    def row(wid: str, seed: int, *, onlane: Any = 7, it: int = 0) -> dict[str, Any]:
        return {
            "event": "eval",
            "iter": it,
            "wver": wid,
            "ckpt_sha16": wid,
            "seed": seed,
            "ticks": 99,
            "onLaneTicks": onlane,
        }

    lines = [
        json.dumps(row("aaa", 862001)),
        json.dumps(row("bbb", 862001)),  # 同 iter=0 同 seed、不同权重 ⇒ 不同判据点
        json.dumps(row("aaa", 862002, onlane=0)),
        json.dumps({"event": "eval_summary", "wver": "aaa"}),  # 不是逐局行
        "{oops",  # 坏 JSON
        json.dumps(row("zzz", 862003)),  # 未知权重键
        json.dumps(row("aaa", 862004, onlane=None)),  # 缺 lane 列
    ]
    p = tmp_path / "eval_log.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows, unknown = WJ.load_rows(p, {"aaa": ("b0", 30), "bbb": ("b1", 35)})
    assert rows["b0"].keys() == {(30, 862001), (30, 862002)}
    assert rows["b1"].keys() == {(35, 862001)}
    assert rows["b2"] == {}
    assert unknown == 2  # 未知键 + onLaneTicks 缺失；summary / 坏 JSON 不算未知
    # 权重键既能从 wver 也能从 ckpt_sha16 认出来（账本两列逐位相等）
    assert (30, 862002) in rows["b0"]


def test_point_stats_pairs_against_the_named_control() -> None:
    """控制臂可换（Wave 2 的对照物是 b0，不是 Wave 1 的 a0）。"""
    base = {
        "event": "eval",
        "ticks": 99,
        "win": 1,
        "outcome": "stage_clear",
        "onLaneMoveTicks": 3,
        "onLaneHoldFireTicks": 2,
        "playerDamageTaken": 40,
        "kills": 3,
        "playerShots": 12,
        "cellsVisited": 50,
        "stuckTicks": 20,
    }
    rows = {
        "b0": {(30, 1): {**base, "onLaneTicks": 10}, (30, 2): {**base, "onLaneTicks": 20}},
        "b1": {(30, 1): {**base, "onLaneTicks": 5}, (30, 2): {**base, "onLaneTicks": 10}},
    }
    pts = WJ.point_stats(rows, WJ.PP.ENDPOINTS[0], "b1", its=(30, 35))
    assert len(pts) == 1  # it35 两侧都没行 ⇒ 不出点（不是 0）
    it, mean_n, se_n, level = pts[0]
    assert it == 30
    assert mean_n == pytest.approx(-0.075)  # Δ = [−0.05, −0.10] ⇒ 均值 −0.075（原生率）
    assert se_n == pytest.approx(0.025)  # sd(ddof=1) = 0.0354 / √2
    assert level == pytest.approx(0.15)  # b0 水平 = (0.10 + 0.20)/2


def test_max_negative_streak_is_the_burn_rule_shape() -> None:
    series = {5: -0.06, 10: -0.07, 15: -0.01, 20: -0.09, 25: -0.06, 30: -0.08}
    assert WJ.max_negative_streak(series, WJ.BURN_MARGIN_PP / 100.0) == 3  # it20/25/30
    assert WJ.max_negative_streak({}, 0.05) == 0
    assert WJ.max_negative_streak({5: -0.05}, 0.05) == 0  # 严格小于（等号不触发）


def test_daily_series_reads_win_and_ra2(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    rows = [
        {"event": "eval", "iter": 5, "win": 1, "ticks": 9, "onLaneTicks": 1},
        {"event": "eval", "iter": 5, "win": 0, "ticks": 9, "onLaneTicks": 3},
        {"event": "eval", "iter": 10, "win": 1, "ticks": 9, "onLaneTicks": 0},
        {"event": "eval_summary", "iter": 10, "winRate": 1.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert WJ.daily_series(p) == {5: pytest.approx(0.5), 10: pytest.approx(1.0)}
    assert WJ.daily_series(p, key="ra2") == {5: pytest.approx(0.2), 10: pytest.approx(0.0)}


# ─────────────────────────────────────────────────────────────────────────────
# 守卫线方向语义（§11）：四种方向 × 「已证实 / 只是没证据」
# ─────────────────────────────────────────────────────────────────────────────
def test_line_verdict_covers_the_four_directions() -> None:
    lv = WJ.line_verdict
    # rel_down：低于 −margin 才破；CI 上沿仍低于 −margin ⇒ 已证实
    assert lv(("rel_down", 16.0), -0.10, 0.001, 1.0) == "未破（-10.0%）"
    assert lv(("rel_down", 16.0), -0.20, 0.001, 1.0) == "破线且已证实（-20.0%）"
    assert lv(("rel_down", 16.0), -0.20, 0.05, 1.0) == "破线未证实（-20.0%）"
    # rel_up：高于 +margin 才破；CI 下沿仍高于 +margin ⇒ 已证实
    assert lv(("rel_up", 10.0), 0.12, 0.001, 1.0) == "破线且已证实（+12.0%）"
    assert lv(("rel_up", 10.0), 0.12, 0.05, 1.0) == "破线未证实（+12.0%）"
    assert lv(("rel_up", 10.0), 0.05, 0.001, 1.0) == "未破（+5.0%）"
    # abs_up（洞守卫：静止∧在线 不升过 +0.007927）
    assert lv(("abs_up", 0.007927), 0.005, 0.001, 1.0) == "未破（+0.0050）"
    assert lv(("abs_up", 0.007927), 0.010, 0.001, 1.0) == "破线且已证实（+0.0100）"
    # abs_down（pass：非劣 −3pp）
    assert lv(("abs_down", 0.03), -0.0189, 0.001, 1.0) == "未破（-0.0189）"
    assert lv(("abs_down", 0.03), -0.04, 0.001, 1.0) == "破线且已证实（-0.0400）"
    # 目标线（绿线）读作 达标 / 未达标——写成「未破」会被读成过关（b2 的 +2.9% 其实是零效应）
    assert lv(("rel_down", 16.0), -0.20, 0.001, 1.0, target=True) == "达标且已证实（-20.0%）"
    assert lv(("rel_down", 16.0), -0.18, 0.05, 1.0, target=True) == "达标未证实（-18.0%）"
    assert lv(("rel_down", 16.0), -0.10, 0.001, 1.0, target=True) == "未达标且已证实（-10.0%）"
    assert lv(("rel_down", 16.0), -0.10, 0.05, 1.0, target=True) == "未达标（未证实）（-10.0%）"
    assert WJ.TARGET_LINES == ("主终点 rA2", "A1 终点 rA1")
    assert all(label in WJ.MARGINS for label in WJ.TARGET_LINES)
    # level = 0 不许炸（相对判定退化为 0）
    assert lv(("rel_down", 16.0), -0.10, 0.001, 0.0) == "未破（+0.0%）"
    # 与共用面的 Z 常量同源（不是本地魔数）
    assert pytest.approx(1.959963985) == WJ.PP.Z_TWO_SIDED_95
    assert math.isclose(0.001 * WJ.PP.Z_TWO_SIDED_95, 0.00196, abs_tol=1e-5)
