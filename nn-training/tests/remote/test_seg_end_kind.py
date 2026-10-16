"""段末三态（J2，`plan/offline-open-ended-iters-and-loud-export` §3.2.3）——**五格判定表**。

为什么单独成家：`_end_it_reached`（T6 的两态）与 `_seg_end_kind`（J2 的三态）差别只在
「这一段有没有触及课程预算」，而表里最容易错的是**有限课程的部分段**那一格 —— v1 的
`end_it_reached ∧ not open_ended` 会把它误判成课末（课被提前封死），而它正是
`offline-segment-restart` §93 那条「人手动续段」路径今天也在走的形态。

判据是**纯函数** ⇒ 这里只钉表：不碰 HTTP、不起进程、不看日志文本。
hub 那一侧（盖章照旧 + 按三态触发下一段）在 `tests/hub/test_auto_handoff.py`。
"""

from __future__ import annotations

import pytest

from remote import plan_run as plan_run_mod


def _plan(*, open_ended: bool, end_it: int, iters_total: int) -> dict:
    return {"open_ended": open_ended, "end_it": end_it, "iters_total": iters_total}


#: 五格 + 三负例：`(情形, plan, state, it_end, 期望 kind, 期望 end_it_reached)`。
#: `state` 只认 `complete`/`noop`（`budget`/`failed` 停下 = 没跑到终点，没有段末可言）。
#: id 用 ASCII 短名：pytest 的 tmp 目录名会拼上 id，中文 id 在 Windows/长路径下会炸
#: （Errno 36 文件名太长——本条注释就是那条现场）。
_IDS = ["open", "open-noop", "course", "segment", "budget", "short", "failed", "no-end"]

_TABLE = [
    (
        "无限课（J1 开区间段）跑到段末 ⇒ open",
        _plan(open_ended=True, end_it=500, iters_total=0),
        "complete",
        500,
        plan_run_mod.SEG_END_OPEN,
        True,
    ),
    (
        "无限课 · noop（接续点已在终点之后）⇒ 照旧是 open（幂等重报）",
        _plan(open_ended=True, end_it=500, iters_total=0),
        "noop",
        500,
        plan_run_mod.SEG_END_OPEN,
        True,
    ),
    (
        "有限课 · 本段触到课程预算 ⇒ course（课末，封课）",
        _plan(open_ended=False, end_it=10, iters_total=10),
        "complete",
        10,
        plan_run_mod.SEG_END_COURSE,
        True,
    ),
    (
        "有限课 · 本段未触及预算（--run-iters N 截短的一段）⇒ segment（**不是课末**）",
        _plan(open_ended=False, end_it=5, iters_total=10),
        "complete",
        5,
        plan_run_mod.SEG_END_SEGMENT,
        True,
    ),
    (
        "预算到点停下 ⇒ 没有段末（kind 空）",
        _plan(open_ended=False, end_it=10, iters_total=10),
        "budget",
        5,
        "",
        False,
    ),
    (
        "跑完但计划终点没到（截断后停在中间）⇒ 没有段末",
        _plan(open_ended=False, end_it=10, iters_total=10),
        "complete",
        4,
        "",
        False,
    ),
    (
        "失败停下 ⇒ 没有段末",
        _plan(open_ended=False, end_it=10, iters_total=10),
        "failed",
        3,
        "",
        False,
    ),
    (
        "计划没有 end_it（老/坏计划）⇒ 没有段末（不拿它去触发下一段）",
        {"iters_total": 3},
        "complete",
        3,
        "",
        False,
    ),
]


@pytest.mark.parametrize(
    ("name", "plan", "state", "it_end", "want_kind", "want_reached"),
    _TABLE,
    ids=_IDS,
)
def test_segment_end_kind_table(
    name: str, plan: dict, state: str, it_end: int, want_kind: str, want_reached: bool
) -> None:
    assert plan_run_mod._end_it_reached(plan, state=state, it_end=it_end) is want_reached, name
    assert plan_run_mod._seg_end_kind(plan, state=state, it_end=it_end) == want_kind, name


def test_segment_end_kind_is_never_course_for_a_partial_segment() -> None:
    """反探针（v1 那一格）：「`end_it_reached` ∧ 非开区间」会把部分段判成课末。

    这是三态里唯一「两态判据会给出危险答案」的格子：课末 ⇒ hub 封课 + 不发下一段 ⇒
    有限课程被 `--run-iters` 截短的那一段跑完后，课就停在那里（而它本该续下一段）。
    """
    partial = _plan(open_ended=False, end_it=5, iters_total=10)
    assert plan_run_mod._end_it_reached(partial, state="complete", it_end=5) is True
    assert (
        plan_run_mod._seg_end_kind(partial, state="complete", it_end=5)
        != plan_run_mod.SEG_END_COURSE
    )
