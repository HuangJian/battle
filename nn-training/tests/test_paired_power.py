"""`tools/paired_power.py` 契约（Wave 2 功效定案，2026-09-30）。

要钉住的三件事：
  · **算术独立可对**：均值 / sd(ddof=1) / SE / 相关 / 正态尾 / MDE / N 反解，全部用
    手算常数对账——工具里的数进 `DECISIONS.md` 与 `experiments.md`，不许只有一条实现；
  · **归档口径**：§65 的 MDE 三元组（25.0 / 17.7 / 12.5）自己就服从 MDE ∝ 1/√N
    （200 / 400 / 800 局）——这就是「那组数是从某个点的 SE 按 √N 外推」的证据；
  · **逐局复算对得上归档**：`main()` 读三腿逐局账本（`tmp/` 证据，未入库；缺则 skip），
    对账不通过就非零退出。
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    """按**文件路径**加载 `tools/paired_power.py`（不依赖包布局）。

    刻意不写 `from tools import paired_power`：`tools/` 无 `__init__.py`，那样 import 会让
    mypy 把同一份文件当成两个模块而直接报错——门禁跑的是 `mypy .`。同款做法见
    `tests/test_course_compare.py`。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_paired_power_under_test", NN_ROOT / "tools" / "paired_power.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # 先登记再 exec（同 test_course_compare 的理由）
    spec.loader.exec_module(mod)
    return mod


PP = _load()
CORPUS_OK = all(p.exists() for p in PP.PATHS.values())
EXT_OK = CORPUS_OK and all(p.exists() for p in PP.EXT_LEDGERS)


# ─────────────────────────────────────────────────────────────────────────────
# 口径：rA2 = onLaneTicks/(ticks+1)（每局率）
# ─────────────────────────────────────────────────────────────────────────────
def test_ra2_is_the_per_game_rate_with_plus_one_denominator() -> None:
    assert PP.ra2({"onLaneTicks": 7, "ticks": 99}) == pytest.approx(0.07)
    # 分母是 ticks+1（与 §65 主终点同式）：ticks=0 时不是除零，也不等于 0
    assert PP.ra2({"onLaneTicks": 3, "ticks": 0}) == pytest.approx(3.0)
    assert PP.ra2({"onLaneTicks": 0, "ticks": 0}) == 0.0


def test_game_key_accepts_only_well_formed_per_game_rows() -> None:
    good = {"event": "eval", "iter": 5, "seed": 860001, "onLaneTicks": 7, "ticks": 99}
    assert PP.game_key(good) == (5, 860001)
    assert PP.game_key({**good, "event": "eval_summary"}) is None
    assert PP.game_key({**good, "seed": None}) is None
    assert PP.game_key({**good, "seed": "860001"}) is None  # 字符串种子不入账
    assert PP.game_key({**good, "iter": 5.0}) is None
    assert PP.game_key({**good, "onLaneTicks": None}) is None
    assert PP.game_key({**good, "ticks": "99"}) is None


def test_load_games_skips_broken_and_non_game_lines(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    rows = [
        {"event": "eval", "iter": 0, "seed": 1, "onLaneTicks": 1, "ticks": 9},
        {"event": "eval_summary", "iter": 0, "winRate": 0.5},  # summary 不是逐局行
        {"event": "eval", "iter": 0, "seed": 2, "onLaneTicks": 2, "ticks": 18},
        {"event": "eval", "iter": 5, "seed": 3, "onLaneTicks": 0, "ticks": 4},
    ]
    lines = [
        json.dumps(rows[1]),  # summary：不是逐局行
        "{oops",  # 坏 JSON
        "",  # 空行
        json.dumps(rows[0]),  # 真逐局行
        json.dumps(rows[2]),
        json.dumps(rows[3]),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    got = PP.load_games(p)
    assert got.keys() == {(0, 1), (0, 2), (5, 3)}  # 坏行与 summary 都不入账
    assert got[(0, 1)] == pytest.approx(1 / 10)
    assert got[(0, 2)] == pytest.approx(2 / 19)
    assert got[(5, 3)] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 配对：同 iter 同 seed 的逐局差
# ─────────────────────────────────────────────────────────────────────────────
def test_paired_diffs_uses_only_shared_seeds_in_seed_order() -> None:
    own = {(5, 3): 0.30, (5, 1): 0.10, (5, 2): 0.20, (10, 1): 0.90}
    peer = {(5, 1): 0.11, (5, 2): 0.18, (5, 9): 0.99, (10, 1): 0.90}
    # seed 3 只有 own、seed 9 只有 peer ⇒ 都不参与；按升序 seed ⇒ [1, 2]；别的 iter 不进
    assert PP.paired_diffs(own, peer, 5) == [pytest.approx(-0.01), pytest.approx(0.02)]
    assert PP.paired_diffs(own, peer, 10) == [pytest.approx(0.0)]
    assert PP.paired_diffs(own, peer, 99) == []


def test_arm_level_and_per_game_sd_match_hand_computation() -> None:
    g = {(0, 1): 0.01, (0, 2): 0.02, (0, 3): 0.03, (5, 1): 1.0}
    assert PP.values_at(g, 0) == [pytest.approx(0.01), pytest.approx(0.02), pytest.approx(0.03)]
    assert PP.arm_level(g, 0) == pytest.approx(0.02)
    # ddof=1：sqrt(((−0.01)²+0²+(0.01)²)/2) = 0.01
    assert PP.per_game_sd(g, 0) == pytest.approx(0.01)
    # 只点一局 ⇒ 抛（不能静默给 0：sd=0 会让「零方差」混进功效表）
    with pytest.raises(statistics.StatisticsError):
        PP.per_game_sd(g, 5)


def test_se_of_mean_divides_by_sqrt_n() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    xs_sd = math.sqrt(sum((x - 3.0) ** 2 for x in xs) / 4)  # 手算 ddof=1
    assert PP.se_of_mean(xs) == pytest.approx(xs_sd / math.sqrt(5))
    assert PP.se_of_mean(xs) < statistics.pstdev(xs)  # SE < 总体 sd，永远


def test_corr_extremes_and_degenerate() -> None:
    xs = [0.1, 0.2, 0.3, 0.4]
    assert PP.corr(xs, xs) == pytest.approx(1.0)
    assert PP.corr(xs, [-v for v in xs]) == pytest.approx(-1.0)
    assert PP.corr(xs, [0.5, 0.5, 0.5, 0.5]) == 0.0  # 无方差 ⇒ 不是 NaN
    # 工具用它解释「配对买到多少方差」：sd_Δ/σ_arm = sqrt(2(1−ρ))（ρ=0 ⇒ √2 = 白配）
    for rho, expect in ((0.0, math.sqrt(2)), (0.5, 1.0), (1.0, 0.0)):
        assert math.sqrt(2 * (1 - rho)) == pytest.approx(expect)


def test_normal_tails_are_exact_and_complementary() -> None:
    assert PP.norm_cdf(0.0) == pytest.approx(0.5)
    assert PP.norm_sf(0.0) == pytest.approx(0.5)
    z = 1.959963985
    assert PP.norm_cdf(-z) == pytest.approx(0.025, abs=1e-6)
    assert PP.norm_sf(z) == pytest.approx(0.025, abs=1e-6)
    for t in (-2.0, -1.0, -0.5, 0.0, 0.5):
        assert PP.norm_cdf(t) + PP.norm_sf(t) == pytest.approx(1.0)
    # 单侧备择「下降」：Δ<0（t<0）⇒ p<0.5；Δ>0 ⇒ p>0.5（§65 归档表就是这个方向）
    assert PP.norm_cdf(-2.0) < 0.05 < PP.norm_cdf(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# MDE：乘数、√N 律、N 反解
# ─────────────────────────────────────────────────────────────────────────────
def test_mde_multiplier_is_the_two_tail_sum() -> None:
    assert abs(PP.MDE_K - (1.959963985 + 0.8416212336)) < 1e-12
    assert PP.mde_rel(1.0) == pytest.approx(PP.MDE_K)
    assert PP.mde_rel(9.0) == pytest.approx(PP.MDE_K * 9)


def test_n_for_target_inverts_the_sqrt_law_round_trip() -> None:
    se = 8.94
    for n_ref in (200, 400):
        for target in (12.0, 16.0, 25.0):
            n = PP.n_for_target(PP.mde_rel(se), n_ref, target)
            got = PP.mde_rel(se * math.sqrt(n_ref / n))
            assert got == pytest.approx(target, rel=1e-12)
            # 目标比当前 MDE 更大 ⇒ 所需 N 不比参照少（N ∝ 1/MDE²）
            if target <= PP.mde_rel(se):
                assert n >= n_ref
            else:
                assert n <= n_ref


def test_archived_mde_triple_obeys_the_sqrt_n_law() -> None:
    """归档 25.0@200 / 17.7@400 / 12.5@800 自己就是 √N 外推 ⇒ 口径可反推。"""
    m200 = PP.ARCHIVED_MDE[200]
    for n, arch in PP.ARCHIVED_MDE.items():
        assert m200 * math.sqrt(200.0 / n) == pytest.approx(arch, abs=0.05)


# ─────────────────────────────────────────────────────────────────────────────
# 尾巴合并（§6/§7）：SE 的 √K 律与 N 的 1/K 律
# ─────────────────────────────────────────────────────────────────────────────
def test_se_pooled_and_se_of_pool_agree_on_equal_ses() -> None:
    assert PP.se_pooled(9.0, 4) == pytest.approx(4.5)
    assert PP.se_of_pool([9.0, 9.0, 9.0, 9.0]) == pytest.approx(4.5)
    # 不等值时按 sqrt(Σsᵢ²)/K 算，**不是**均值除 √K
    assert PP.se_of_pool([3.0, 4.0]) == pytest.approx(2.5)
    assert PP.se_of_pool([2.0, 2.0, 2.0]) == pytest.approx(2.0 / math.sqrt(3))


def test_n_for_pooled_divides_the_single_point_cost_by_k() -> None:
    base = PP.n_for_target(PP.mde_rel(9.0), 200, 16.0)
    for k in (1, 2, 3, 4):
        assert PP.n_for_pooled(PP.mde_rel(9.0), 200, 16.0, k) == pytest.approx(base / k)
    # 乘积律：总评估局数 N×K 与 K 无关 → 合并省的是**另开一段大评估**，不是局数
    totals = {k: PP.n_for_pooled(PP.mde_rel(9.0), 200, 16.0, k) * k for k in (1, 2, 3, 4)}
    assert len(set(round(v, 9) for v in totals.values())) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 判据表规格（§5/§8）：三条“结构不可判”必须在规格里就看得见
# ─────────────────────────────────────────────────────────────────────────────
def _full_row() -> dict[str, Any]:
    return {
        "event": "eval",
        "iter": 5,
        "seed": 860001,
        "ticks": 99,
        "win": 1,
        "outcome": "stage_clear",
        "onLaneTicks": 7,
        "onLaneMoveTicks": 3,
        "onLaneHoldFireTicks": 2,
        "playerDamageTaken": 40,
        "kills": 3,
        "playerShots": 12,
        "cellsVisited": 50,
        "stuckTicks": 20,
    }


def test_every_endpoint_extracts_a_float_from_a_full_row() -> None:
    """口径键名写错就在这条上红（否则只能等真语料跑起来才发现）。"""
    row = _full_row()
    got = {ep.label: ep.get(row) for ep in PP.ENDPOINTS}
    assert len(got) == len(PP.ENDPOINTS)  # 标签不重
    for label, v in got.items():
        assert isinstance(v, float), label
    by_label = {ep.label: ep for ep in PP.ENDPOINTS}
    assert got["主终点 rA2"] == pytest.approx(7 / 100)
    assert got["A1 终点 rA1"] == pytest.approx(3 / 100)
    assert got["洞守卫 静止∧在线"] == pytest.approx(4 / 100)
    assert got["timeout"] == 0.0
    assert by_label["timeout"].get({**row, "outcome": "max_ticks"}) == 1.0


def test_undecidable_lines_are_marked_in_the_spec_not_discovered_late() -> None:
    by_label = {ep.label: ep for ep in PP.ENDPOINTS}
    # ① margin=0（“不升”）⇒ 需无限局 ⇒ 必须改写成有限阈值
    assert by_label["洞守卫 静止∧在线"].margin_rel == 0.0
    # ② 课程写“非劣”却没给数字的四条 ⇒ 不可判
    for label in ("零伤局", "dmg/局", "kills/局", "shots/局"):
        ep = by_label[label]
        assert ep.margin_rel is None and ep.margin_abs is None, label
    # ③ 有数字的实验组：其阈值必须能换算成有限的所需局数
    assert by_label["pass"].margin_abs == pytest.approx(0.03)
    assert by_label["holdFire 守卫"].margin_rel == 30.0
    assert by_label["cellsVisited"].margin_rel == 10.0


def test_endpoint_pair_stats_matches_hand_computation() -> None:
    # rA2 = onLaneTicks/(ticks+1)，`_full_row()` 的 ticks=99 ⇒ a0 = [0.01, 0.02]、a1 = [0.02, 0.02]
    rows: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
        "a0": {(5, 1): {**_full_row(), "onLaneTicks": 1},
               (5, 2): {**_full_row(), "onLaneTicks": 2}},
        "a1": {(5, 1): {**_full_row(), "onLaneTicks": 2},
               (5, 2): {**_full_row(), "onLaneTicks": 2}},
    }
    got = PP.endpoint_pair_stats(rows, PP.ENDPOINTS[0], 5)
    assert got is not None
    mean_d, sd_d, se_d, level = got
    assert mean_d == pytest.approx(0.005)  # 逐局差 [0.01, 0.00]
    assert sd_d == pytest.approx(math.sqrt(0.00005))  # ddof=1
    assert se_d == pytest.approx(0.005)
    assert level == pytest.approx(0.015)  # 对照臂水平
    # 只共有一个 seed ⇒ 算不了（不能静默拿 0 当“没差别”）
    thin: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
        "a0": {(5, 1): _full_row()},
        "a1": {(5, 1): _full_row()},
    }
    assert PP.endpoint_pair_stats(thin, PP.ENDPOINTS[0], 5) is None


def test_load_rows_is_the_full_row_store_and_load_games_is_its_ra2_view(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    row = {"event": "eval", "iter": 0, "seed": 1, "onLaneTicks": 1, "ticks": 9}
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows = PP.load_rows(p)
    assert rows[(0, 1)]["ticks"] == 9  # 整行都在
    assert PP.load_games(p) == {(0, 1): pytest.approx(0.1)}


@pytest.mark.skipif(not CORPUS_OK, reason="Wave 1 三腿逐局账本不在 tmp/（未入库证据）")
def test_main_reconciles_per_game_rows_against_the_archive(capsys: Any) -> None:
    assert PP.main() == 0  # 对账不过就非零退出
    out = capsys.readouterr().out
    assert "配对前提 成立" in out
    assert "归档 Δrel / t / p₁ 逐点对账：**全部一致**" in out
    assert "归档 MDE 对账：**一致" in out
    # §5–§8：次要终点 / 尾巴合并 / 更长时程 / 装配表
    assert "§5 次要终点与守卫表的配对功效" in out
    assert "跨点差异**全部**由抽样噪声解释" in out  # §6 平稳性前提成立
    assert "§7 更长时程" in out
    assert "§8 Wave 2 判据表" in out
    # 三条“结构不可判”必须印出来（换成别的语料会立刻红，这是故意的）
    assert "∞（要数字）" in out  # 静止∧在线「不升」margin=0
    assert "无方差" in out  # timeout：全库 1/3400
    assert "—（要数字）" in out  # 四条「非劣」没数字
    assert "否（需 2130）" in out  # pass「−3pp 非劣」在 Wave 2 规模内不可判
    # §9：三腿回填（口径 = 尾巴 3 点均值 / 全点均值 / 旧 it20 单点）——结论改变的那条要钉住
    assert "§9 Wave 1 回填" in out
    assert "对照臂 A0（零奖励）在同一判据下的自漂：尾巴 3 点均值 +15.0%" in out
    assert "A1 全 7 点均值 = -11.14%（SE 3.68%，p₁ = 0.001）" in out  # 效应「量得出」⇒ ③ 的免责失效
    assert "但「无效」这个标签要改" in out  # 主终点判语不变、标签改判
    assert "已证实不到 16%" in out and "≈ 1.57× 局数" in out  # 补局数的唯一用途 = 证伪绿线


# ─────────────────────────────────────────────────────────────────────────────
# §67 补评估块（sha16 归属 / 异质性 Q / 两条预注册断言 / 剂量倍数）
# ─────────────────────────────────────────────────────────────────────────────
def test_sha16_is_the_ledger_ckpt_field(tmp_path: Path) -> None:
    import hashlib

    p = tmp_path / "w.json"
    p.write_bytes(b'{"x": 1}')
    assert PP.sha16(p) == hashlib.sha256(b'{"x": 1}').hexdigest()[:16]  # 账本的 ckpt_sha16 就是它


def test_cochran_q_is_zero_and_matches_ivw_when_points_agree() -> None:
    q, mean_w, crit = PP.cochran_q([-10.0, -10.0, -10.0], [5.0, 5.0, 5.0])
    assert q == pytest.approx(0.0)
    assert mean_w == pytest.approx(-10.0)  # 逆方差等权 ⇒ 就是简单均值
    assert crit == pytest.approx(5.991)  # df = K−1 = 2


def test_cochran_q_flags_heterogeneity_and_downweights_noisy_points() -> None:
    # SE 1 vs SE 10 ⇒ 权重 1 vs 0.01：精点主导加权均值，Q 由粗点贡献
    q, mean_w, crit = PP.cochran_q([-20.0, 0.0], [1.0, 10.0])
    assert mean_w == pytest.approx(-20.0 / 1.01, abs=1e-9)
    assert q == pytest.approx(3.9604, abs=1e-3)  # 手算：1·0.198² + 0.01·19.802²
    assert q > crit == pytest.approx(3.841)  # df=1 临界 ⇒ 判异质
    # 退化输入：单点 / 有 SE=0 ⇒ 无从谈异质性
    assert PP.cochran_q([-9.0], [2.0])[0] == 0.0
    assert PP.cochran_q([-9.0, -3.0], [0.0, 2.0])[0] == 0.0


def test_claim_from_pooled_is_the_two_preregistered_assertions() -> None:
    # §67 实际读数：−8.52% ± 3.22% ⇒ 下沿 −14.84 > −16（不到绿线已证实）、上沿 −2.21 < 0（效应为正）
    lo, hi, below_green, positive = PP.claim_from_pooled(-8.52, 3.22)
    assert (lo, hi) == pytest.approx((-14.83, -2.21), abs=0.02)
    assert below_green is True and positive is True
    # 效应太负（真到了绿线）⇒ 不能声称「不到绿线」，但仍然是「可靠为正」
    _, _, below, pos = PP.claim_from_pooled(-17.0, 2.0)
    assert below is False and pos is True
    # CI 同时跨 −16 与 0（SEL 大、估计贴中间）⇒ 两条都不成立（不得改口）
    _, _, below2, pos2 = PP.claim_from_pooled(-8.0, 5.0)
    assert below2 is False and pos2 is False
    # 可以只成立一条：估计够负但 CI 太宽 ⇒ 「不到绿线」成立、「为正」不成立
    _, _, below3, pos3 = PP.claim_from_pooled(-3.0, 6.0)
    assert below3 is True and pos3 is False


def test_dose_multiple_uses_the_preregistered_bracket_rule() -> None:
    assert PP.CUR_F_PCT == 3.6
    assert PP.DOSE_BRACKET_PCT == (4.0, 7.5)
    m = PP.dose_multiple(-8.52)  # 16 / 8.52
    assert m == pytest.approx(1.878, abs=1e-3)
    mults = [f / PP.CUR_F_PCT for f in PP.DOSE_BRACKET_PCT]
    assert mults[0] == pytest.approx(1.111, abs=1e-3) and mults[1] == pytest.approx(2.083, abs=1e-3)
    assert [f for f, x in zip(PP.DOSE_BRACKET_PCT, mults, strict=True) if x >= m] == [7.5]  # 只 7.5% 够
    assert math.isinf(PP.dose_multiple(0.0))  # 零效应 ⇒ 多少倍都到不了


@pytest.mark.skipif(not EXT_OK, reason="补评估块不在 tmp/h4-lane-ext/（未入库证据）")
def test_main_recomputes_the_extended_block_and_keeps_the_two_claims(capsys: Any) -> None:
    assert PP.main() == 0
    out = capsys.readouterr().out
    assert "§10 补评估后的重算" in out
    assert "不相交检查：与旧段（860001–860200）重叠 0 / 0 局" in out
    assert "已证实不到绿线" in out  # 尾巴 3 点：CI 下沿 > −16%
    assert "效应为正 True" in out  # 且 CI 完全 < 0
    assert "选 7.5%" in out  # 剂量推论：4.0% 档（1.11×）不够
    # §11：Wave 2 预注册阈值表（每条线一个数；N_WAVE2 = 300/点）
    assert "§11 Wave 2 预注册阈值表" in out
    assert f"尾巴 3 点均值 × {PP.N_WAVE2} 局/点" in out
    assert "给数字：不劣于" in out  # 四条「非劣」拿到数字
    assert "不升过 +0.007927 abs" in out  # margin=0 改成有限阈值
    assert "只报频次（全库 sd = 0" in out  # timeout 不当功效项
