"""收官 drain 的去重判据（2026-11-17 `replay-a-cont` it20 事故）。

事故形状：`iters=20` 的课跑完 it20 → `round_done next_it=21` → 收官 `run_complete`
→ **收官 drain 又把 it20 派了一次**，`eval_log.jsonl` 里 it20 变成 **1,169 行**
（700 + 469，469 条重复 `(stage,seed)`）⇒ 末点胜率不可用。

根因：`_uncovered_eval_rounds` 判「这轮有没有被覆盖」只看 `eval_summary`，而在飞的轮
**还没写 summary** ⇒ 收官那一刻必然判成未覆盖。判据要问「是不是已经有人在做」。

本文件钉住纯函数判据 `drop_inflight_rounds` 与仲裁器的新读面 `active_its`：

1. 在飞的轮必须被剔出 cand，且**原样报告**给日志（不静默丢）；
2. 没在飞的一律保留（旧缺口照补，不能因为修去重把补账废掉）；
3. `active_its` 只报**本课**的轮号，别的课同号轮不能互相抵消。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_eval import drop_inflight_rounds
from worker.eval_arbiter import eval_arbiter, reset_eval_arbiter


def test_inflight_round_is_dropped() -> None:
    """★ 事故形状：cand=[20]，it20 在飞 ⇒ 保留空表、跳过 [20]。"""
    keep, skipped = drop_inflight_rounds([20], {20})
    assert keep == []
    assert skipped == [20]


def test_other_rounds_still_backfilled() -> None:
    """去重只针对在飞轮：旧缺口（18/19）不得被顺手废掉。"""
    keep, skipped = drop_inflight_rounds([18, 19, 20], {20})
    assert keep == [18, 19]
    assert skipped == [20]


def test_no_inflight_keeps_everything() -> None:
    keep, skipped = drop_inflight_rounds([3, 7], set())
    assert keep == [3, 7]
    assert skipped == []


def test_empty_cand() -> None:
    assert drop_inflight_rounds([], {5}) == ([], [])


def test_arbiter_active_its_empty_by_default() -> None:
    reset_eval_arbiter()
    try:
        assert eval_arbiter().active_its("no-such-course-fp") == set()
        assert eval_arbiter().active_for("no-such-course-fp") == 0
    finally:
        reset_eval_arbiter()
