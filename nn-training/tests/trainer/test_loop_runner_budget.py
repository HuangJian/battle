"""LoopRunner 的预算判据：活读课程 args + 显式 CLI 冻结（plan/iters-hot-reload-live §5）。

现场（z11-ent）：课程 `iters 500→800`、`course_edit verdict=applied`，it501 只多跑一轮就收官。
根因两条：`LoopRunner.iters` 是构造期快照；入队通道 `_enqueue` 又没有预算闸。本文件钉住：

1. 热更后**不重建 runner**，下一拍必须放行（`_budget_iters` 活读 `loop.args.iters`）；
2. serve 级 `--iters` 显式值时仍冻结（用户显式压过课程）；
3. 课程 `iters=0` 仍是不限；
4. `finish_reason` 与判据同源（文案里的数 = 判满用的数）；
5. 停→开 的清态由 `loop_serve._revive_hygiene` 负责（`finished` 不清则 planner 永远空表）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_runner import LoopRunner


class _Loop:
    """最小引擎：`ledger_next_it` 是 planner 判预算的唯一外部事实（不碰盘）。"""

    def __init__(self, next_it: int, iters: int) -> None:
        self._next_it = int(next_it)
        self.args = types.SimpleNamespace(iters=int(iters))

    def ledger_next_it(self, fallback: int = 1) -> int:
        return self._next_it


def _runner(loop: _Loop, *, iters: int = 0) -> LoopRunner:
    return LoopRunner(loop=loop, course="c", iters=iters)


def test_budget_live_reads_args_after_hot_edit() -> None:
    """★ z11-ent 形状：it500 收官 → 课程改 800 → 不重建 runner，it501 必须继续。

    停→开 的清态（`finished=False`）是生产 `_revive_hygiene` 的职责；不在此清，
    就会被 `planner` 第一行的收工短路挡掉（修复后仍是「红」的假象）。
    """
    loop = _Loop(next_it=501, iters=500)
    runner = _runner(loop)

    assert runner.planner("c", 1, None) == []  # type: ignore[arg-type]
    assert runner.finished is True
    assert "500" in runner.finish_reason

    # 生产同款：停→开 先清收工态（loop_serve._revive_hygiene）
    runner.finished = False
    runner.finish_reason = ""
    loop.args.iters = 800

    tasks = runner.planner("c", 501, None)  # type: ignore[arg-type]
    assert [t.it for t in tasks] == [501]
    assert runner.finished is False


def test_explicit_iters_still_frozen() -> None:
    """serve 级 `--iters` 显式值 = 冻结：课程热更改大也不生效。"""
    loop = _Loop(next_it=501, iters=800)
    runner = _runner(loop, iters=500)

    assert runner.planner("c", 501, None) == []  # type: ignore[arg-type]
    assert runner.finished is True
    assert "500" in runner.finish_reason


def test_budget_live_zero_means_unlimited() -> None:
    """课程 `iters=0` 仍是不限：活读到 0 ⇒ 不判满（既有语义不破）。"""
    loop = _Loop(next_it=5000, iters=0)
    runner = _runner(loop)

    tasks = runner.planner("c", 5000, None)  # type: ignore[arg-type]
    assert [t.it for t in tasks] == [5000]
    assert runner.finished is False


def test_finish_reason_uses_live_budget() -> None:
    """判满文案与判据同源：热更到 800 后必须写 `iters=800 跑满`，不是 500。"""
    loop = _Loop(next_it=801, iters=800)
    runner = _runner(loop)

    assert runner.planner("c", 801, None) == []  # type: ignore[arg-type]
    assert runner.finished is True
    assert runner.finish_reason == "iters=800 跑满"


def test_reopen_clears_finished_so_live_budget_takes_effect() -> None:
    """停→开 的清态是生产契约：`_revive_hygiene` 清 `finished`，活读预算才轮得到。"""
    from trainer.loop_serve import _revive_hygiene

    loop = _Loop(next_it=501, iters=500)
    runner = _runner(loop)
    assert runner.planner("c", 1, None) == []  # type: ignore[arg-type]
    assert runner.finished is True

    rt = types.SimpleNamespace(course="c", engine=None, runner=runner)
    _revive_hygiene(rt)  # type: ignore[arg-type]
    loop.args.iters = 800

    assert runner.finished is False
    assert [t.it for t in runner.planner("c", 501, None)] == [501]  # type: ignore[arg-type]
