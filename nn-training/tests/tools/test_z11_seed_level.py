"""`tools/z11_seed_level.py` 的 C8 算术契约（plan/classic35-path.plan.md §5 C8）。

要钉住的事：
  · 统计单位是 `(stage, seed)`，窗口只取所有腿都**完整**的 iter 交集；
  · sign / McNemar 用精确二项（不引 scipy），bootstrap 固定 RNG 可复算；
  · ICC / 有效 seed 数只解释旧 p 为何虚高，不改变判决口径；
  · 半截评估点（行数 < expect）绝不进窗口。
"""

from __future__ import annotations

import importlib.util
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
    """按文件路径加载被测模块（`tools/` 不是包；门禁跑 mypy .）。"""
    spec = importlib.util.spec_from_file_location(
        "_z11_seed_level_under_test", NN_ROOT / "tools" / "z11_seed_level.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


Z = _load()


# ─────────────────────────────────────────────────────────────────────────────
# 读账本 / 完整性
# ─────────────────────────────────────────────────────────────────────────────
def test_game_key_accepts_only_well_formed_eval_rows() -> None:
    good = {"event": "eval", "iter": 5, "stage": 2000, "seed": 2000000001, "win": 1}
    assert Z.game_key(good) == (5, 2000, 2000000001)
    assert Z.game_key({**good, "event": "eval_summary"}) is None
    assert Z.game_key({**good, "win": None}) is None
    assert Z.game_key({**good, "seed": "2000000001"}) is None
    assert Z.game_key({**good, "iter": True}) is None
    assert Z.game_key({**good, "win": True}) == (5, 2000, 2000000001)


def test_load_eval_rows_skips_broken_lines_and_counts_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"event": "eval", "iter": 0, "stage": 2000, "seed": 1, "win": 1}),
                json.dumps({"event": "eval_summary", "iter": 0, "winRate": 0.5}),
                "not-json",
                json.dumps({"event": "eval", "iter": 0, "stage": 2000, "seed": 2, "win": 0}),
                # 同一键重复：后者覆盖，计数 +1
                json.dumps({"event": "eval", "iter": 0, "stage": 2000, "seed": 2, "win": 1}),
            ]
        ),
        encoding="utf-8",
    )
    rows, st = Z.load_eval_rows(p)
    assert rows[(0, 2000, 2)] == 1.0
    assert len(rows) == 2
    assert st == {"lines": 5, "skipped": 2, "duplicates": 1}


def _rows(iters: list[int], stages: tuple[int, ...], seeds: tuple[int, ...],
          fn: Any) -> dict[tuple[int, int, int], float]:
    return {
        (it, stage, seed): float(fn(it, stage, seed))
        for it in iters
        for stage in stages
        for seed in seeds
    }


def test_complete_iters_excludes_partial_points() -> None:
    stages, seeds = (2000, 2001), (1, 2)
    rows = _rows([0, 5, 10], stages, seeds, lambda *_: 1)
    # it10 只有一半局：必须剔除
    rows = {k: v for k, v in rows.items() if not (k[0] == 10 and k[1] == 2001)}
    assert Z.complete_iters(rows, expect_games=4) == [0, 5]
    assert Z.iter_counts(rows)[10] == 2


def test_common_iters_is_the_intersection_of_complete_points() -> None:
    stages, seeds = (2000,), (1, 2)
    a = _rows([0, 5, 10], stages, seeds, lambda *_: 1)
    b = _rows([0, 5, 15], stages, seeds, lambda *_: 0)
    assert Z.common_iters({"a": a, "b": b}, expect_games=2) == [0, 5]
    assert Z.common_iters({}, expect_games=2) == []


# ─────────────────────────────────────────────────────────────────────────────
# 聚合 / 配对
# ─────────────────────────────────────────────────────────────────────────────
def test_per_seed_win_averages_only_the_window() -> None:
    rows = {
        (0, 2000, 1): 1.0,
        (5, 2000, 1): 0.0,
        (10, 2000, 1): 1.0,  # 窗口外
        (0, 2000, 2): 0.0,
        (5, 2000, 2): 1.0,
    }
    out = Z.per_seed_win(rows, [0, 5])
    assert out[(2000, 1)] == (0.5, 2)
    assert out[(2000, 2)] == (0.5, 2)


def test_paired_diffs_uses_only_shared_seed_cells() -> None:
    a = {(2000, 1): (1.0, 2), (2000, 2): (0.0, 2), (2001, 1): (1.0, 2)}
    b = {(2000, 1): (0.5, 2), (2000, 2): (0.5, 2), (2001, 2): (0.0, 2)}
    keys, diffs = Z.paired_diffs(a, b)
    assert keys == [(2000, 1), (2000, 2)]
    assert diffs == [0.5, -0.5]


def test_sign_test_exact_binomial_and_ties() -> None:
    # 10 正 0 负：双侧 p = 2/2^10
    assert Z.sign_test([1.0] * 10)["p"] == pytest.approx(2 / 1024)
    # 5/5：p = 1
    assert Z.sign_test([1.0] * 5 + [-1.0] * 5)["p"] == pytest.approx(1.0)
    # tie 被剔除，不进 n
    out = Z.sign_test([1.0, 1.0, 0.0, 0.0])
    assert out == {"pos": 2, "neg": 0, "ties": 2, "n": 2, "p": pytest.approx(0.5)}
    assert Z.sign_test([])["p"] == 1.0


def test_mcnemar_exact_matches_binomial_two_sided() -> None:
    assert Z.mcnemar_exact(0, 0) == 1.0
    assert Z.mcnemar_exact(10, 0) == pytest.approx(2 / 1024)
    assert Z.mcnemar_exact(5, 5) == pytest.approx(1.0)
    assert Z.mcnemar_exact(9, 1) == pytest.approx(2 * (1 + 10) / 1024)


def test_exact_binomial_handles_large_n_without_overflow() -> None:
    # 16,400 局的全量 McNemar 会产生 n≈5,000 的 discordant 对；
    # 写成 float(2**n) 会 OverflowError（实测 2026-11-07）。
    assert Z.mcnemar_exact(2500, 2500) == pytest.approx(1.0)
    p = Z.mcnemar_exact(3000, 2000)
    assert 0.0 < p < 1e-10
    assert Z.sign_test([1.0] * 3000 + [-1.0] * 2000)["p"] == pytest.approx(p)


def test_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    vals = [float(i) for i in range(40)]
    a = Z.bootstrap_mean_ci(vals, n_boot=500, seed=7)
    b = Z.bootstrap_mean_ci(vals, n_boot=500, seed=7)
    assert a == b
    lo, hi = a
    assert lo <= 19.5 <= hi
    assert (lo, hi) == (lo, lo) or hi > lo
    # 常数序列零宽
    assert Z.bootstrap_mean_ci([1.0] * 10, n_boot=50, seed=1) == (1.0, 1.0)
    assert math.isnan(Z.bootstrap_mean_ci([], n_boot=10)[0])


def test_icc_oneway_extremes() -> None:
    # 行间完全不同、行内常数 ⇒ ICC = 1
    assert Z.icc_oneway([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]) == pytest.approx(1.0)
    # 行内波动、行间均值相同 ⇒ ICC ≈ 0（截断不为负）
    assert Z.icc_oneway([[0.0, 1.0], [0.0, 1.0]]) == pytest.approx(0.0)
    assert Z.icc_oneway([[1.0]]) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 全量
# ─────────────────────────────────────────────────────────────────────────────
def test_build_report_uses_common_window_and_seed_level_sign() -> None:
    stages, seeds, iters = (2000, 2001), (1, 2), [0, 5]
    control = _rows(iters, stages, seeds, lambda *_: 0)
    treated = _rows(iters, stages, seeds, lambda *_: 1)
    # control 有一个半截 it99，必须不进窗口
    control.update({(99, 2000, 1): 1.0})
    res = Z.build_report(
        {"null": control, "win": treated},
        "null",
        expect_games=4,
        n_boot=200,
        boot_seed=3,
    )
    assert res["window"] == {
        "iters": [0, 5],
        "n_iters": 2,
        "seeds": 4,
        "games": 8,
        "icc_control": 0.0,
        "design_effect": 1.0,
        "equivalent_independent_points": 2.0,
        "effective_binary_observations": 4.0,
    }
    c = res["comparisons"]["win"]
    assert c["seeds"] == 4
    assert c["run_average"]["delta"] == pytest.approx(1.0)
    assert c["run_average"]["sign"]["p"] == pytest.approx(0.125)
    assert c["endpoint"]["iter"] == 5
    assert c["naive_pooled"]["pairs"] == 8
    assert res["panel_mde80"]["at_400_seeds"] == 0.0


def test_build_report_rejects_empty_window() -> None:
    rows = _rows([0], (2000,), (1,), lambda *_: 1)
    with pytest.raises(ValueError, match="完整"):
        Z.build_report({"null": rows, "x": rows}, "null", expect_games=99)


def test_render_markdown_has_the_three_reading_layers() -> None:
    stages, seeds, iters = (2000,), (1, 2), [0, 5]
    res = Z.build_report(
        {"null": _rows(iters, stages, seeds, lambda *_: 0),
         "x": _rows(iters, stages, seeds, lambda *_: 1)},
        "null",
        expect_games=2,
        n_boot=50,
        boot_seed=1,
    )
    md = Z.render_markdown(res)
    assert "## 1. 主读数" in md
    assert "## 2. 副读数" in md
    assert "## 3. 旧口径对照" in md
    assert "上界" in md
