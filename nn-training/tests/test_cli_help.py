"""`--help` 可用性 + `--rotate-seed` 的「调试专用」标注（plan/accident.plan.md §2.4，2026-09-21）。

两件事：

① **裸 `%` 会让整个 `--help` 崩**（argparse 的 `_expand_help` 对 help 再做一次 `% params`）——
   2026-09-21 实测：`run_rl.py --help` 抛 `ValueError: unsupported format character '?' (0xff0c)`，
   病灶是 `--remote-precollect` 的一行 help 里的 `30%`（全仓唯一裸 `%`）。这个 bug 平时不会暴露
   （训练从不打印 help），但它是**唯一的自保手段**在人工排查时失效——守住它只需一个 format_help()。

② §2.4 要求 `--rotate-seed` 的 `--help` 标注「调试专用、训练配对以课程文件为准」：配对值现在写在
   课程文件 `paired_rotate_seed` 里（两条腿各写同一把 V），CLI 旗标退化为后门。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.cli import build_argparser


def _help_text() -> str:
    ap = build_argparser("per-tick", {})
    return ap.format_help()


def test_help_renders_without_crashing() -> None:
    """裸 `%` 回归：format_help 必须不抛（`%` 要写成 `%%`）。"""
    text = _help_text()
    assert "--rotate-seed" in text


def test_rotate_seed_help_marks_debug_only_and_points_at_course_file() -> None:
    text = _help_text()
    assert "调试专用" in text
    assert "paired_rotate_seed" in text  # 指向课程文件里的键（§2.3 的落点）
    assert "控制台开课" in text  # 唯一入口（§1.1）


@pytest.mark.parametrize("mode", ["per-tick"])
def test_no_single_percent_in_any_help(mode: str) -> None:
    """结构性防线：parser 里任何 help 都不许有裸 `%`（新增参数时立刻红）。"""
    ap = build_argparser(mode, {})
    for action in ap._actions:
        h = action.help or ""
        stripped = h.replace("%%", "")
        assert "%" not in stripped, f"{action.dest} 的 help 含裸 '%'（写成 '%%'）：{h}"
