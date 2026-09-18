"""R2c-3：让位闸门（`rl/loop_runner.LoopRunner._wait_gate` 与 `WAIT_HOOKS` 表）。

表本身是一条**设计决定**（哪一步该让位、哪一步刻意不让），所以要有用例钉住它——
「加一步就顺手加个让位钩子」这种改法在这里最容易把已经压掉的墙钟又贴回来。

三条性质：

1. 表里的 kind 必须都在 `ROUND_TASKS` 里（拼错 kind = 钩子永远不生效，静默）；
2. `eval_join` **不在**表里（本机 eval 局的墙钟是藏在下一轮 rollout 里的，不该串回轮边界）；
3. 闸门的三态：钩子缺失 ⇒ 不让位（老引擎行为逐字节不变）；说不就绪 ⇒ `WAIT` 且带原因；
   说就绪 ⇒ 正常跑那一步。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.loop_runner import WAIT_HOOKS, LoopRunner
from rl.loop_scheduler import CourseQueue
from rl.loop_tasks import ROUND_TASKS, WAIT, Task, TaskResult


def _loop(*, ready: bool | None) -> types.SimpleNamespace:
    """最小引擎替身：`precollect_ready` 是唯一被问的钩子（None = 引擎没实现）。"""
    ns = types.SimpleNamespace(step_precollect_join=lambda ctx: None)
    if ready is not None:
        ns.precollect_ready = lambda it: ready
    return ns


def _run(*, ready: bool | None) -> tuple[TaskResult, list[int]]:
    asked: list[int] = []
    loop = _loop(ready=ready)
    if ready is not None:

        def _hook(it: int) -> bool:
            asked.append(it)
            return bool(ready)

        loop.precollect_ready = _hook
    runner = LoopRunner(
        loop=loop, course="a", step_mode=True, now=lambda: 1000.0, poll_interval=15.0
    )
    queue = CourseQueue(course="a", next_it=1)
    task = Task(course="a", it=1, kind="precollect_join")
    return runner.run_step(task, queue), asked


def test_every_hooked_kind_exists_in_the_round_table() -> None:
    assert set(WAIT_HOOKS) <= set(ROUND_TASKS)
    assert WAIT_HOOKS["precollect_join"] == "precollect_ready"


def test_eval_join_is_deliberately_not_gated() -> None:
    """把本机 eval 尾巴串回轮边界会抵消 2026-09-17 压掉的软等窗口（写死在用例里）。"""
    assert "eval_join" not in WAIT_HOOKS


def test_ppo_is_not_gated_because_the_step_yields_itself() -> None:
    """`ppo` 的让位在**步骤内部**（三相拆分），不在表里。

    往表里加回去 = 把闸门放到「还没发布」之前 ⇒ 永远等不到回传（这条陷阱已经踩过一轮，
    所以把「不在表里」写成断言）。
    """
    assert "ppo" not in WAIT_HOOKS


def test_missing_hook_means_no_yield() -> None:
    """老引擎（没实现钩子）⇒ 不让位：行为与改造前一致，不会被闸门凭空挡住。"""
    res, asked = _run(ready=None)
    assert res.status == "done"
    assert asked == []


def test_not_ready_yields_with_a_readable_reason() -> None:
    res, asked = _run(ready=False)
    assert res.status == WAIT
    assert asked == [1]
    assert "预采" in res.reason
    assert res.resume_at == 1015.0  # now + poll_interval
    assert res.hold is False  # 预采是子进程，不占本机资源票


def test_ready_runs_the_step_normally() -> None:
    res, asked = _run(ready=True)
    assert res.status == "done"
    assert asked == [1]
