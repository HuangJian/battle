"""test_eval_a_once — 手动 evalA 必须与 in-loop eval **同一条派发路**（2026-09-22 用户指令）。

缺口：`rl/eval_a_once.py` 曾自带一个「本机串行」循环 —— 400 局 × ~0.85s ≈ 340s
（实测 x20-noexplore it177 = 339.2s，账本 `nodes` 只有 `{"local-evalA": 400}`），
而 in-loop 同一份语料 50–64s（三台节点分摊）。手动 evalA 是**同一次评估的手动触发**：
语料 / 节点池 / 账本 schema 都必须与 in-loop 同源，否则指标表里两种行不可比。

本文件钉住：① 确实走 in-loop 派发器（不再自己跑局）；② summary 读回契约（含
「同 wver 已评完 → 按账本回填」那条 in-loop 没有的手动兜底）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rl import eval_a_once

ROOT = Path(__file__).resolve().parent.parent


def test_eval_a_once_dispatches_through_in_loop_dispatcher() -> None:
    """回归：手动 evalA 必须经 `rl/eval_dispatch` 派到节点，不得自己串行跑局。

    源码级断言（同 test_eval_loot_fields.py 的接线口径）——它抓的正是那 339s 的根因：
    本地-only 的 `for stage, seed in todo: run_local_eval_game(...)`。
    """
    src = (ROOT / "rl" / "eval_a_once.py").read_text(encoding="utf-8")
    assert "from rl.eval_dispatch import dispatch_eval_round" in src
    assert "dispatch_eval_round(" in src
    # 不再直接调本地 runner（自己跑局 = 又把节点池甩掉了）
    assert "run_local_eval_game" not in src
    # 本机份额来自 policy（与 in-loop 同一旋钮），不是脚本自造的并发数
    assert "evalLocalSlots" in src
    # in-loop 的调用点也用同一模块（两边同源，不是各写一套）
    loop_src = (ROOT / "rl" / "loop_steps.py").read_text(encoding="utf-8")
    assert "eval_dispatch import dispatch_eval_bg" in loop_src


def _row(**kw) -> dict:
    base = {
        "event": "eval",
        "iter": 105,
        "wver": "a" * 16,
        "stage": 2000,
        "seed": 1,
        "node": "self",
        "win": 1,
        "cleared": 1,
        "outcome": "stage_clear",
    }
    base.update(kw)
    return base


def test_read_summary_returns_last_row_for_iter_and_wver(tmp_path: Path) -> None:
    """只认本 (iter, wver) 的 eval_summary，且取**最后一条**（重复触发以最新为准）。"""
    p = tmp_path / "eval_log.jsonl"
    rows = [
        {"event": "eval_summary", "iter": 105, "wver": "a" * 16, "games": 100, "sec": 60.0},
        _row(),  # 逐局行不该被当成 summary
        {"event": "eval_summary", "iter": 106, "wver": "a" * 16, "games": 100, "sec": 1.0},
        {"event": "eval_summary", "iter": 105, "wver": "b" * 16, "games": 100, "sec": 2.0},
        {"event": "eval_summary", "iter": 105, "wver": "a" * 16, "games": 100, "sec": 47.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    got = eval_a_once._read_summary(p, "a" * 16, 105)
    assert got is not None and got["sec"] == 47.0
    assert eval_a_once._read_summary(p, "a" * 16, 999) is None
    assert eval_a_once._read_summary(tmp_path / "missing.jsonl", "a" * 16, 105) is None


def test_write_summary_for_wver_backfills_reused_readout(tmp_path: Path) -> None:
    """同 wver 已在别轮评完（派发器早退不写 summary）时，按账本回填本 iter 的读数。"""
    p = tmp_path / "eval_log.jsonl"
    rows = [
        _row(seed=1, node="self", win=1, cleared=1),
        _row(seed=2, node="mac", win=0, cleared=0, outcome="gameover"),
        _row(seed=3, node="self", win=1, cleared=1),
        # source 行 = B/C evalboard 写入，不得混进 A 层读数
        _row(seed=4, node="self", win=1, cleared=1, source="B"),
        # 别的 wver 不得混进
        _row(seed=5, wver="c" * 16, win=1, cleared=1),
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    n = eval_a_once._write_summary_for_wver(p, "a" * 16, 177, time.time())
    assert n == 3

    got = eval_a_once._read_summary(p, "a" * 16, 177)
    assert got is not None
    assert got["games"] == 3 and got["wins"] == 2
    assert got["winRate"] == round(2 / 3, 4)
    assert got["nodes"] == {"mac": 1, "self": 2}
    assert got["reused_wver"] is True
    assert got["rolloutWinRate"] is None  # 手动补写没有 rollout 读数，不许编


def test_write_summary_for_wver_no_rows_returns_zero(tmp_path: Path) -> None:
    """账本里根本没有该 wver 的局 → 0（调用方据此响亮失败，不写空 summary）。"""
    p = tmp_path / "eval_log.jsonl"
    p.write_text(json.dumps(_row(wver="d" * 16)) + "\n", encoding="utf-8")
    assert eval_a_once._write_summary_for_wver(p, "a" * 16, 177, time.time()) == 0
    assert eval_a_once._read_summary(p, "a" * 16, 177) is None
