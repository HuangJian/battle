"""`common/cell.py` —— 进程级单例的显式持有者（P0-5，2026-11-20）。

守的是**形状**而不是「有没有状态」：进程里确实只有一份 eval 池 / 仲裁器，问题出在
「模块级可变量 + `global` + 锁外的惰性判断」。所以这里钉三件事：

1. `get()` 永远返回同一份（没人能把它换回 `None`，也没有「首次才造」的锁外判断）；
2. `replace()` 返回新值，并在声明了 `retire` 时把旧值交给它（声明在拥有值的那一行）；
3. 两个 `Cell` 互不相见 —— 用例要隔离就在进程级单例之外自建一份。
"""

from __future__ import annotations

from common.cell import Cell


def test_get_returns_the_same_object_until_replaced() -> None:
    cell: Cell[list[str]] = Cell([])
    first = cell.get()
    assert cell.get() is first
    assert cell.get() is cell.get()


def test_replace_swaps_and_returns_the_new_value() -> None:
    cell: Cell[list[str]] = Cell([])
    first = cell.get()
    second = cell.replace(["given"])
    assert second == ["given"]
    assert cell.get() is second, "replace 之后 get 必须看到新值"
    assert second is not first


def test_cells_are_independent() -> None:
    a: Cell[str] = Cell("a")
    b: Cell[str] = Cell("b")
    a.replace("a2")
    assert a.get() == "a2"
    assert b.get() == "b", "另一个 Cell 不得看见别人的替换"


class _Tracked:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_replace_retires_the_old_value_when_declared() -> None:
    """退休动作声明在**拥有值**的那一行（`Cell(EvalExecutors(), retire=…)`），值一换就收尾。"""
    retired: list[_Tracked] = []
    first = _Tracked()
    cell: Cell[_Tracked] = Cell(first, retire=retired.append)
    assert cell.get() is first
    second = cell.replace(_Tracked())
    assert retired == [first], "旧的必须在换值时退休，且只退休一次"
    assert second.closed is False, "新值不该被顺手关掉"
    assert cell.replace(_Tracked()) is not second
    assert len(retired) == 2


def test_replace_without_retire_leaves_the_old_value_alone() -> None:
    """没声明 retire 就只丢引用（用例自建的 Cell 不该有隐式副作用）。"""
    old = _Tracked()
    cell: Cell[_Tracked] = Cell(old)
    cell.replace(_Tracked())
    assert old.closed is False
