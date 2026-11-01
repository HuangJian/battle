"""半面板拒答（plan/eval-bias §3.1 Step 3，2026-11-01）：`eval-behavior-stats` 不许把 400 行当 800 局出表。

现场：`z3-p2002` / `z3-p2002ctl` 的云腿每点 summary 写 800、账本只到 400 行，工具照常出表
——池化 clear 看着正常，分关/基毁率错 10pp（丢的正好是最难的 2002/2003）。这里端到端钉
**一个行为**：行数与 summary 声明的局数对不上时，进程非 0 退出且不出表。

判据本身（`incomplete_summaries`，重算不信旧标记）在 `tests/worker/test_eval_ledger_merge.py`
单测；本文件只管「工具真的拒答」这一层。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[3] / "tools" / "eval-behavior-stats.py"


def _game(it: int, seed: int) -> dict:
    return {
        "event": "eval",
        "iter": it,
        "wver": "a" * 16,
        "stage": 2000,
        "seed": seed,
        "node": "cloud",
        "outcome": "stage_clear",
        "win": True,
        "cleared": True,
        "ticks": 10,
    }


def _summary(it: int, games: int) -> dict:
    return {
        "event": "eval_summary",
        "iter": it,
        "wver": "a" * 16,
        "games": games,
        "wins": 0,
        "winRate": 0.0,
        "outcome": {"stage_clear": 0},
    }


def _ledger(tmp_path: Path, course: str, rows: list[dict]) -> None:
    d = tmp_path / "tmp" / course
    d.mkdir(parents=True)
    (d / "eval_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _run(tmp_path: Path, course: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--course", course],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_tool_refuses_a_half_panel(tmp_path: Path) -> None:
    """summary 8 局、账本 4 行 ⇒ 非 0 退出，且不输出统计表（只留一行拒因）。"""
    _ledger(tmp_path, "half", [_game(5, s) for s in range(4)] + [_summary(5, games=8)])
    # 只钉行为（退出码），不钉文本形态：拒答 = 进程非 0，不是「出表 + 警告」。
    assert _run(tmp_path, "half").returncode == 2


def test_tool_accepts_a_complete_panel(tmp_path: Path) -> None:
    """行数 = games ⇒ 正常出表（对照组：别把正常面板误伤成「不可用」）。"""
    _ledger(tmp_path, "whole", [_game(5, s) for s in range(4)] + [_summary(5, games=4)])
    assert _run(tmp_path, "whole").returncode == 0
