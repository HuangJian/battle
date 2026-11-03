"""tests/remote/test_eval_row_selection.py —— 评估行回传的上界与按关分层（2026-11-03）。

钉住三件事：

1. **整轮放行**：`eval_rows_cap` 让「关数 × 种子数」的完整读数一条不裁（z5 = 35×20 = 700）；
2. **异常大按关分层**：恰好 `EVAL_ROWS_HARD` 条、覆盖全部关、每关条数差 ≤1；
3. **确定性**：同一批行打乱文件顺序，投影逐条相同（前缀截断做不到这一点）。

为什么不是随机抽：`submit_eval_round` 会在评估陆续完成时重复投同一轮，投影必须稳定；
否则逐局表与账本并集会随投递时刻漂。
"""

from __future__ import annotations

import inspect
import random

from remote import deliver_outbox
from remote.deliver_outbox import LocalOutboxDeliverer
from remote.deliver_parts import EVAL_ROWS_BASE, eval_rows_cap, select_eval_rows
from remote.deliver_wire import EVAL_ROWS_HARD


def _row(stage: int, seed: int, *, win: bool = False) -> dict:
    return {"event": "eval", "iter": 7, "wver": "a" * 16, "stage": stage, "seed": seed, "win": win}


def _round(stages: int, seeds: int) -> list[dict]:
    return [_row(2000 + s, i) for s in range(stages) for i in range(seeds)]


def _keys(rows: list[dict]) -> list[tuple[int, int]]:
    return [(r["stage"], r["seed"]) for r in rows]


def test_eval_rows_cap_lets_a_complete_round_through() -> None:
    """700 条（z5 的 35×20）一条不裁；小轮次保持旧行为；异常大收到硬上界。"""
    assert eval_rows_cap(700) == 700
    assert eval_rows_cap(300) == EVAL_ROWS_BASE
    assert eval_rows_cap(5000) == EVAL_ROWS_HARD

    rows = _round(35, 20)
    assert select_eval_rows(rows, eval_rows_cap(len(rows))) is rows


def test_oversize_round_is_stratified_exactly() -> None:
    """3200 条 ⇒ 恰好 3000 条、覆盖全部 40 关、每关 75 条（差 ≤1），且无重键。"""
    rows = _round(40, 80)
    out = select_eval_rows(rows, EVAL_ROWS_HARD)
    assert len(out) == EVAL_ROWS_HARD
    counts = {st: sum(1 for r in out if r["stage"] == st) for st in range(2000, 2040)}
    assert set(counts) == set(range(2000, 2040))
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len(_keys(out)) == len(set(_keys(out)))


def test_selection_is_order_independent() -> None:
    """同一批行、不同落盘顺序（并发完成）必须得到同一投影。"""
    rows = _round(35, 34)  # 1190 条；显式传 400 测裁法（与硬上界无关）
    a = select_eval_rows(rows, 400)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert _keys(select_eval_rows(shuffled, 400)) == _keys(a)


def test_cap_below_stage_count_covers_one_row_each() -> None:
    """cap 比关数还小时，覆盖优先：每个 stage 至多一条、取 stage 最小的前 cap 关。"""
    out = select_eval_rows(_round(35, 20), 10)
    assert len(out) == 10
    assert {r["stage"] for r in out} == set(range(2000, 2010))


def test_duplicate_stage_seed_keeps_last() -> None:
    """同键 (stage, seed) 取最后一条（与账本「后到覆盖」同口径）。"""
    rows = [_row(2000, 1, win=False), _row(2001, 1), _row(2000, 1, win=True)]
    assert [(r["stage"], r["seed"], r["win"]) for r in select_eval_rows(rows, 1)] == [
        (2000, 1, True)
    ]


def test_outbox_uses_the_single_hard_cap() -> None:
    """outbox 不再各存一份 400：默认值就是 `deliver_wire.EVAL_ROWS_HARD`。"""
    default = inspect.signature(LocalOutboxDeliverer.__init__).parameters["eval_cap"].default
    assert default == EVAL_ROWS_HARD
    assert not hasattr(deliver_outbox, "EVAL_ROWS_CAP")
