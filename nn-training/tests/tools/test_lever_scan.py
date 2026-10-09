"""`tools/lever_scan.py` 契约（塑形杠杆扫描，2026-09-30）。

要钉住的三件事：
  · **共用算术只有一份**：账本归属/键来自 `tools/wave2_judge.py`，相关系数来自
    `tools/paired_power.py`（按路径加载，`LS.PP is LS.WJ.PP`），本工具不许复制一份；
  · **相位口径是 pooled**（Σ伤害/Σtick，含 ≤600t 短局）：逐局均值口径有短局截尾陷阱
    （阵亡局窗后只剩几十 tick ⇒ 后段密度虚高、方向都能读反）；
  · **税基口径是 pooled 占比**（Σnum/Σticks）：「这笔税实际压在多少 tick 上」——
    这是本扫描全部结论的地基（lane 税基 0.251%）。

端到端读数（§2 0.251% / §4 −3.12 / §5 1.15 & 0.37 / §9 144–316）**不再钉死在测试里**（2026-10-09，`plan/nn-training-test-debt-cleanup.plan.md` §2-T1）：它们的输入是 `tmp/` 下的未入库账本 ⇒ 门禁里只有 skip 空壳；结论已归档，要重跑就 `bash tools/githook/nn-py-safe.sh -m tools.lever_scan`。本文件只守上列三件事（纯逻辑，无输入依赖）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    """按**文件路径**加载 `tools/lever_scan.py`（tools/ 无 `__init__.py`，同 test_wave2_judge）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_lever_scan_under_test", NN_ROOT / "tools" / "lever_scan.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # 先登记再 exec（同 test_paired_power 的理由）
    spec.loader.exec_module(mod)
    return mod


LS = _load()


# ─────────────────────────────────────────────────────────────────────────────
# 共用算术只有一份 + 常量与引用口径
# ─────────────────────────────────────────────────────────────────────────────
def test_shared_arithmetic_comes_from_wave2_judge_and_paired_power() -> None:
    assert LS.PP is LS.WJ.PP  # 不是第二份拷贝
    assert LS.PP.__file__.replace("\\", "/").endswith("tools/paired_power.py")
    assert hasattr(LS.PP, "corr")  # §6 的相关系数来自共用面
    assert LS.WJ.ITS == (30, 35, 40) and LS.WJ.SEED0 == 862001


def test_constants_and_quoted_human_number() -> None:
    assert LS.HUMAN_OPENING_STOP_PCT == 54.5  # **引用** h5a/h5b 课程头注，非本工具产出
    assert len(LS.SCAN_COLS) == 14 and "onLaneExemptTicks" in LS.SCAN_COLS
    assert "dmgFirst600" in LS.SCAN_COLS and "playerDamageTaken" in LS.SCAN_COLS
    assert [name for name, _ in LS.C05_LEGS] == ["h5a-earlydmg", "h5b-clean"]


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：分母/口径不编
# ─────────────────────────────────────────────────────────────────────────────
def test_per_tick_uses_ticks_plus_one() -> None:
    assert LS.per_tick({"onLaneTicks": 7, "ticks": 99}, "onLaneTicks") == pytest.approx(0.07)


def test_mean_and_sum_col_treat_missing_as_zero() -> None:
    rows = [{"a": 1, "ticks": 10}, {"a": 3, "ticks": 30}]
    assert LS.mean_col(rows, "a") == 2.0 and LS.sum_col(rows, "a") == 4.0
    assert LS.mean_col(rows, "b") == 0.0  # 缺列按 0（v9 前的行没有方位列）
    assert LS.mean_col([], "a") == 0.0 and LS.sum_col([], "a") == 0.0


def test_tax_base_share_is_pooled_and_guards_zero_denominator() -> None:
    rows = [{"a": 1, "ticks": 10}, {"a": 3, "ticks": 30}]
    assert LS.tax_base_share(rows, "a") == pytest.approx(10.0)  # 4/40
    assert LS.tax_base_share([{"ticks": 0}], "a") == 0.0
    assert LS.tax_base_share([], "a") == 0.0


def test_cohens_d_edges_and_hand_computed_value() -> None:
    assert LS.cohens_d([1, 2, 3], [1, 2, 3]) == 0.0
    assert LS.cohens_d([5], [1, 2]) == 0.0  # 任一组 <2
    assert LS.cohens_d([2, 2], [1, 1]) == 0.0  # 零方差 ⇒ 不产 d
    assert LS.cohens_d([2, 4], [0, 2]) == pytest.approx(1.414, abs=1e-3)


def test_phase_densities_are_pooled_and_keep_short_games() -> None:
    rows = [
        {"ticks": 1000, "playerDamageTaken": 300, "dmgFirst600": 60},
        {"ticks": 400, "playerDamageTaken": 100, "dmgFirst600": 100},  # 短局：不进任何截尾
    ]
    early, late, tick_share, dmg_share = LS.phase_densities(rows)
    assert early == pytest.approx(0.16)  # 160/1000（含短局的 400 个"开局"tick）
    assert late == pytest.approx(0.6)  # 240/400
    assert tick_share == pytest.approx(1000 / 1400)
    assert dmg_share == pytest.approx(160 / 400)
    # 空组 / 全短局 / 零伤害：分母不编
    assert LS.phase_densities([]) == (0.0, 0.0, 0.0, 0.0)
    assert LS.phase_densities([{"ticks": 300, "playerDamageTaken": 50, "dmgFirst600": 50}])[1] == 0.0
    assert LS.phase_densities([{"ticks": 700, "playerDamageTaken": 0, "dmgFirst600": 0}])[3] == 0.0


def test_quantile_edges() -> None:
    vals = [5, 1, 3, 2, 4]  # 升序 1..5
    assert LS.quantile(vals, 0.0) == 1
    assert LS.quantile(vals, 0.5) == 3  # int(0.5·5)=2 ⇒ 索引 2
    assert LS.quantile(vals, 0.9) == 5
    assert LS.quantile([], 0.5) == 0.0


def test_action_shares_pools_five_buckets_and_ignores_bad_rows() -> None:
    rows = [{"moveHist": [1, 2, 3, 4, 0]}, {"moveHist": [1, 1, 1, 1, 1]}, {"moveHist": [9, 9, 9]}, {}]
    sh = LS.action_shares(rows)
    assert sh is not None
    assert sh[0] == pytest.approx(2 / 15 * 100) and sh[1] == pytest.approx(3 / 15 * 100)
    assert sh[2] == pytest.approx(4 / 15 * 100) and sh[3] == pytest.approx(5 / 15 * 100)
    assert sh[4] == pytest.approx(1 / 15 * 100)
    assert LS.action_shares([{}, {"moveHist": None}]) is None


def test_group_by_outcome() -> None:
    g = LS.group_by_outcome(
        [{"outcome": "stage_clear"}, {"outcome": "gameover"}, {"outcome": "stage_clear"}, {}]
    )
    assert len(g["stage_clear"]) == 2 and len(g["gameover"]) == 1 and len(g["None"]) == 1


def test_leg_trend_reads_eval_rows_only_and_sorts_by_iter(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"event": "eval", "iter": 5, "win": 1, "playerDamageTaken": 100, "dmgFirst600": 10}),
                json.dumps({"event": "eval", "iter": 5, "win": 0, "playerDamageTaken": 200, "dmgFirst600": 20}),
                json.dumps({"event": "eval", "iter": 0, "win": 1, "playerDamageTaken": 50, "dmgFirst600": 5}),
                json.dumps({"event": "eval_summary", "iter": 5, "winRate": 1.0}),  # 不是逐局行
                "{oops",  # 坏 JSON
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    trend = LS.leg_trend(p)
    assert [t[0] for t in trend] == [0, 5]
    assert trend[1][1] == 2 and trend[1][2] == pytest.approx(0.5)
    assert trend[1][3] == pytest.approx(150.0) and trend[1][4] == pytest.approx(15.0)
    assert LS.leg_trend(tmp_path / "missing.jsonl") == []


# ─────────────────────────────────────────────────────────────────────────────
# main()：账本缺席 ⇒ 跳过（非失败）
# ─────────────────────────────────────────────────────────────────────────────
def test_main_skips_when_ledgers_missing(monkeypatch: Any, tmp_path: Path, capsys: Any) -> None:
    bogus = {it: tmp_path / f"judge_it{it}.jsonl" for it in LS.WJ.ITS}
    monkeypatch.setattr(LS.WJ, "LEDGERS", bogus)
    assert LS.main() == 0
    assert "扫描跳过" in capsys.readouterr().out
