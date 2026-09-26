"""`tools/course_compare.py` 契约（plan/course-archive.plan.md §3.5 / §3.6-7）。

三条要钉住的性质：
  · **封存 = 活体**：读 `.jsonl.gz` 与读 `.jsonl` 同输出 ⇒ 「活体 vs 封存」与
    「活体 vs 活体」的对照表逐字相同（除表头路径）——这是 G5「可比」的全部内容；
  · **缺键 ≠ 0**：某列一行都没有 ⇒ `未知`；且分母按键分别算（旧报告有 `win` 没 `kills`
    的行不该被静默算进 `win_pct` 分母）；
  · **it 对齐**：缺省取两侧共有 it 的交集，显式 `--iters` 才允许出现单侧无数据的点。
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load_course_compare() -> Any:
    """按**文件路径**加载 `tools/course_compare.py`（不依赖包布局）。

    刻意不写 `from tools import course_compare`：`tools/` 无 `__init__.py`，那样 import 会让
    mypy 把同一份文件当成两个模块（`course_compare` 与 `tools.course_compare`）而直接报错——
    门禁跑的是 `mypy .`。同款做法见 `tests/test_wire_report.py`。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_course_compare_under_test", NN_ROOT / "tools" / "course_compare.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # 先登记：模块内 dataclass 建类要按 __module__ 反查
    spec.loader.exec_module(mod)
    return mod


CC = _load_course_compare()


def _row(it: int, seed: int, **kw) -> dict:
    base = {
        "event": "eval",
        "iter": it,
        "wver": "abc123",
        "stage": 2000,
        "seed": seed,
        "kills": 5,
        "win": 1,
        "cleared": 0,
        "ticks": 1000,
        "outcome": "timeout",
    }
    base.update(kw)
    return base


def _write(dirpath: Path, rows: list[dict], *, gz: bool = False) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if gz:
        p = dirpath / "eval_log.jsonl.gz"
        p.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    else:
        p = dirpath / "eval_log.jsonl"
        p.write_text(text, encoding="utf-8")
    return p


# ────────────────────────── 活体 = 封存 ──────────────────────────


def test_gz_and_plain_produce_identical_rows(tmp_path):
    rows = [_row(10, s, kills=k) for s, k in enumerate([0, 2, 4, 8])]
    live = tmp_path / "live"
    arch = tmp_path / "arch"
    _write(live, rows)
    _write(arch, rows, gz=True)

    plain = CC.compare_iters(live, live)
    withgz = CC.compare_iters(live, arch)
    assert plain["rows"] == withgz["rows"]
    assert plain["iters"] == withgz["iters"] == [10]
    # 路径只出现在表头
    assert "archive" not in CC.render_markdown(withgz).split("\n\n")[1]
    assert str(arch) in CC.render_markdown(withgz)


def test_live_vs_live_matches_live_vs_archived(tmp_path):
    """同一对读数：B 是活体目录 或 B 是封存目录，**表格内容逐字相同**。"""
    a_rows = [_row(5, 1, kills=1), _row(5, 2, kills=7)]
    b_rows = [_row(5, 1, kills=2), _row(5, 2, kills=9)]
    a = tmp_path / "a"
    b_live = tmp_path / "b_live"
    b_arch = tmp_path / "b_arch"
    _write(a, a_rows)
    _write(b_live, b_rows)
    _write(b_arch, b_rows, gz=True)

    r1 = CC.compare_iters(a, b_live)
    r2 = CC.compare_iters(a, b_arch)
    assert r1["rows"] == r2["rows"]

    # CSV 也一致（注释行里的路径不算）
    def body(t: str) -> list[str]:
        return [ln for ln in t.splitlines() if not ln.startswith("#") and not ln.startswith("- A")]

    assert body(CC.render_csv(r1)) == body(CC.render_csv(r2))


# ────────────────────────── 缺键 ≠ 0 ──────────────────────────


def test_missing_column_shows_unknown_not_zero(tmp_path):
    """旧报告缺 v8 列 ⇒ `未知`；合法 0 值必须保留。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, [_row(1, 1, kills=0), _row(1, 2, kills=0)])
    _write(b, [_row(1, 1, kills=3), _row(1, 2, kills=0)])

    res = CC.compare_iters(a, b)
    by_key = {(r["it"], r["key"]): r for r in res["rows"]}
    # v8 列两侧都没有 ⇒ 未知（不是 0）
    assert by_key[("1", "hp_ratio_mean")]["a"] == CC.UNKNOWN
    assert by_key[("1", "danger_mean")]["b"] == CC.UNKNOWN
    # 零击杀是**合法 0**：A 侧全 0 ⇒ 100%，均值 0.00
    assert by_key[("1", "zero_kill_pct")]["a"] == "100.00"
    assert by_key[("1", "mean")]["a"] == "0.00"


def test_share_denominator_is_per_key(tmp_path):
    """有 `win` 没 `kills` 的旧行：进 `win_pct` 分母，**不进** `low_pct` 分母。"""
    a = tmp_path / "a"
    rows = [
        {"event": "eval", "iter": 1, "stage": 1, "seed": 1, "win": 1},  # 无 kills
        _row(1, 2, kills=9, win=0),
    ]
    _write(a, rows)
    res = CC.compare_iters(a, a)
    by = {r["key"]: r["a"] for r in res["rows"]}
    assert by["win_pct"] == "50.00"  # 分母 2（两行都有 win）
    assert by["low_pct"] == "0.00"  # 分母 1（只有第 2 行有 kills）


# ────────────────────────── 对齐与统计 ──────────────────────────


def test_iters_default_is_intersection_and_explicit_allows_gaps(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write(a, [_row(1, 1), _row(2, 1), _row(3, 1)])
    _write(b, [_row(2, 1), _row(3, 1), _row(9, 1)])

    res = CC.compare_iters(a, b)
    assert res["iters"] == [2, 3]  # 只有共有 it
    res2 = CC.compare_iters(a, b, [2, 9])
    n9 = next(r for r in res2["rows"] if r["it"] == "9" and r["key"] == "n")
    assert n9["a"] == CC.UNKNOWN  # A 侧在 it9 没数据 ⇒ 未知，不是 0
    assert n9["b"] == "1.00"


def test_string_numeric_value_is_missing_not_zero(tmp_path):
    """★ 字符串 `kills` 是**缺键**（既不进 low_pct 分子也不进分母），不是零杀。旧实现
    `_present` 收 str 而 `_num` 拒 str，会把字符串当 0 静默拉低低杀率。"""
    a = tmp_path / "a"
    _write(a, [_row(1, 1, kills="3"), _row(1, 2, kills=9)])
    res = CC.compare_iters(a, a)
    by = {r["key"]: r["a"] for r in res["rows"]}
    assert by["low_pct"] == "0.00"  # 分母 1（只第 2 行有效），9<=3 为假
    assert by["mean"] == "9.00"  # 字符串不计入均值


def test_p10_is_nearest_rank_and_low_rate():
    rows = [_row(1, s, kills=k) for s, k in enumerate([0, 1, 2, 3, 10, 20, 30, 40, 50, 60])]
    summ = CC.summarize(rows)
    # 10 个数，p10 → round(0.1*9)=1 → 排序后第 2 小 = 1
    assert summ["p10"] == 1.0
    assert summ["mean"] == pytest.approx(21.6)
    assert summ["low_pct"] == pytest.approx(40.0)  # kills<=3 有 4 个


def test_bad_lines_and_non_eval_rows_are_skipped(tmp_path):
    p = tmp_path / "a"
    p.mkdir()
    (p / "eval_log.jsonl").write_text(
        "\n".join(
            [
                "{not json",
                json.dumps({"event": "iteration", "iter": 1}),
                json.dumps(_row(1, 1, kills=4)),
                "",
            ]
        ),
        encoding="utf-8",
    )
    rows = CC.load_eval_rows(p)
    assert len(rows) == 1 and rows[0]["kills"] == 4


def test_missing_log_raises():
    with pytest.raises(FileNotFoundError):
        CC.load_eval_rows(Path("/definitely/not/here"))
