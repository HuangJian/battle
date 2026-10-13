"""M4 弱版（plan/ppo-job-lifecycle-ownership §5）：开关对不上账时的**一行可 grep WARN**。

被测 = `trainer/loop_serve.py::switch_mismatch_warnings`（纯函数，判据唯一）。

背景（§0 #6）：06:17 现场，serve 打印「发现 2 门已开课」，hub 同一时刻打印「跳过未开课的
x27-noleash」。评审已把「两套判据互不校验」证伪（两边读的是**同一个标记文件**；真实分歧是
时间线）——所以本刀**零行为变更**：只把「本地在跑但标记不在」与「有标记但被暂停」两类
不一致喊出来（各一行），让人在下一次现场能一眼对齐两边。

三条纪律（与 `ControlApplier` 的「只在变化时产出日志」同口径）：

1. 一致时**不打**（否则每拍一行 = 把日志淹掉，人反而看不见真问题）；
2. 每个 `(课, 类型)` 只喊一次——恢复一致后自动摘名，**再犯再喊**；
3. 纯函数：`marker_present` 注入 ⇒ 单测零 IO。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_serve import (
    SWITCH_MISMATCH_NO_MARKER,
    SWITCH_MISMATCH_PAUSED,
    switch_mismatch_warnings,
)


def _markers(*courses: str):
    have = set(courses)
    return lambda c: c in have


def test_consistent_state_is_silent() -> None:
    """① 一致 ⇒ 零输出：在跑的都有标记、没有任何课被暂停（两个轴各自对齐）。"""
    warned: set[str] = set()
    out = switch_mismatch_warnings(
        ["c5", "x27-obs"], [], _markers("c5", "x27-obs"), warned=warned
    )
    assert out == []
    assert warned == set()
    # 暂停但不在本地调度里（例如进程刚重启、课还没被发现）：不是不一致，不喊。
    assert switch_mismatch_warnings([], ["c5"], _markers("c5"), warned=warned) == []
    assert warned == set()


def test_running_without_marker_warns_once() -> None:
    """② 「在跑但没有开课标记」⇒ 一行 WARN；重复拍不再喊。"""
    warned: set[str] = set()
    lines = switch_mismatch_warnings(["x27-noleash"], [], _markers(), warned=warned)
    assert len(lines) == 1
    # 这一行**就是**交付物（要 grep 的现场读数，同 aistudio / prefetch 先例）：
    # 场次名 + 「标记不在」的理由都要在同一行上。
    assert lines[0].startswith("[serve] WARN") and "x27-noleash" in lines[0]
    assert "开课标记不在" in lines[0]
    assert warned == {f"x27-noleash:{SWITCH_MISMATCH_NO_MARKER}"}
    assert switch_mismatch_warnings(["x27-noleash"], [], _markers(), warned=warned) == []


def test_marker_present_but_paused_warns_once() -> None:
    """③ 「有标记但已暂停」⇒ 一行 WARN（hub 照旧派发，本机不 probe ⇒ 可能白算）。"""
    warned: set[str] = set()
    lines = switch_mismatch_warnings(["c5"], ["c5"], _markers("c5"), warned=warned)
    assert len(lines) == 1
    assert lines[0].startswith("[serve] WARN") and "c5" in lines[0]
    assert warned == {f"c5:{SWITCH_MISMATCH_PAUSED}"}
    assert switch_mismatch_warnings(["c5"], ["c5"], _markers("c5"), warned=warned) == []


def test_recovery_clears_the_bookkeeping_so_it_can_warn_again() -> None:
    """④ 恢复一致 ⇒ 摘名；再犯再喊（否则「喊过一次」会掩盖第二次事故）。"""
    warned: set[str] = set()
    assert switch_mismatch_warnings(["c5"], [], _markers(), warned=warned) != []
    assert warned  # 记账
    assert switch_mismatch_warnings(["c5"], [], _markers("c5"), warned=warned) == []
    assert warned == set()  # 恢复一致：摘名
    again = switch_mismatch_warnings(["c5"], [], _markers(), warned=warned)
    assert len(again) == 1  # 再犯：再喊


def test_two_kinds_are_independent_keys() -> None:
    """两类不一致各自记账：一门课只恢复不会把另一门的课号从账里摘掉。"""
    warned: set[str] = set()
    # c5：在跑、无标记 ⇒ no-marker；c6：在跑、有标记但被暂停 ⇒ paused。
    lines = switch_mismatch_warnings(["c5", "c6"], ["c6"], _markers("c6"), warned=warned)
    assert len(lines) == 2
    assert warned == {f"c5:{SWITCH_MISMATCH_NO_MARKER}", f"c6:{SWITCH_MISMATCH_PAUSED}"}
    # 只恢复 c5（补上标记）⇒ 只摘 c5；c6 那类不受影响、也不重复喊。
    assert (
        switch_mismatch_warnings(["c5", "c6"], ["c6"], _markers("c5", "c6"), warned=warned)
        == []
    )
    assert warned == {f"c6:{SWITCH_MISMATCH_PAUSED}"}
