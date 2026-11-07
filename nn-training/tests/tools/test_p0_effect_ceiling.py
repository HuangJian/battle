"""`tools/p0_effect_ceiling.py` 的算术契约（plan/classic35-path.plan.md §5 C1）。

钉住的事：
  · `ceiling = |new-old| * cap`，不是 `new*cap`；
  · `<30` 判不可测，`>=30` 判 ≥30；
  · 无 cap 时不给假数；
  · 报告保留原始 row 与汇总计数。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_p0_effect_ceiling_under_test", NN_ROOT / "tools" / "p0_effect_ceiling.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load()


def test_compute_row_uses_delta_times_cap() -> None:
    row = C.compute_row(
        C.Lever("x", "wPickup", 4.0, 8.0, 6.0, "cap", "note")
    )
    assert row["delta_w"] == pytest.approx(4.0)
    assert row["ceiling"] == pytest.approx(24.0)
    assert row["ratio_to_30"] == pytest.approx(0.8)
    assert row["verdict"] == "不可测"


def test_compute_row_marks_above_threshold() -> None:
    row = C.compute_row(
        C.Lever("x", "wBase", 3.0, 7.0, 8.0, "8 ring", "note")
    )
    assert row["ceiling"] == pytest.approx(32.0)
    assert row["verdict"] == "≥30"


def test_compute_row_without_cap_is_na() -> None:
    row = C.compute_row(
        C.Lever("x", "wDmg", 0.12, 0.025, None, "no shards", "note")
    )
    assert row["ceiling"] is None
    assert row["ratio_to_30"] is None
    assert row["verdict"] == "n/a"


def test_build_report_counts_and_renders() -> None:
    levers = [
        C.Lever("a", "wPickup", 4.0, 8.0, 6.0, "6", "small"),
        C.Lever("b", "wBase", 3.0, 7.0, 8.0, "8", "large"),
        C.Lever("c", "wDmg", 0.12, 0.025, None, "none", "n/a"),
    ]
    res = C.build_report(
        levers,
        {"shards": "fake", "n_shards": 0, "metrics_version": 16, "w_dmg_max": None},
    )
    assert res["summary"] == {"rows": 3, "below_30": 1, "at_or_above_30": 1, "n_a": 1}
    md = C.render_markdown(res)
    assert "# C1" in md
    assert "wPickup" in md
    assert "不可测" in md
