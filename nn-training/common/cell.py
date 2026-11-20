"""`common/cell.py` — 进程级单例的**显式持有者**（P0-5，2026-11-20）。

`worker/eval_exec._EXECUTORS` 与 `worker/eval_arbiter._ARBITER` 原来是「模块级名字 +
`global` + 手工换实例」：

```
_EXECUTORS: EvalExecutors | None = None

def eval_executors() -> EvalExecutors:
    global _EXECUTORS
    if _EXECUTORS is None:
        _EXECUTORS = EvalExecutors()
    return _EXECUTORS
```

问题全在**形状**上（不是「有状态」本身有问题——进程里确实只有一份池子）：

1. `global` 让人以为任何函数都能顺手改写它，`GLOBAL_BASELINE` 只能按文件记账才守得住；
2. 惰性初始化那半截（`if ... is None`）在锁外。若为它加锁，就得在临界区里调一个**注入的
   构造器** —— 正是 `tests/test_lock_reentrancy.py` 目标面棘轮要逐个审查的形状；
3. 换实例的收尾（`close_pools()` / `reset_for_tests()`）散在模块里，读代码看不出谁负责旧值。

`Cell` 把「一份值 + 取/换 + 退休」收进对象，模块只留一个持有者：

```
_EXECUTORS = Cell(EvalExecutors(), retire=EvalExecutors.close_pools)

def reset_eval_executors() -> EvalExecutors:
    return _EXECUTORS.replace(EvalExecutors())
```

于是**没有惰性路径**（构造发生在 `Cell(...)` 这一行，看得见；它只建锁/建 Runtime/建空字典），
没有 `global`，换实例的形状固定成一行、旧值的收尾声明在拥有它的那一行上。

## 为什么退休钩子是构造参数而不是 `replace(old, close=...)`

两个 `reset_*` 因此各剩一行，而「旧值怎么退休」只写一次、写在**拥有它**的那行；写在 `replace`
调用点上则每次换值都要重述一遍，且两处 reset 会退化成形状相同的三行（`test_reuse_ledger`
的结构克隆基线会立刻 +1）。

## 为什么不「首次 get 才构造」

要么在锁内调注入的构造器（目标面棘轮 +1，每次都得重新论证「不会回头拿同一把锁」），要么在
锁外调（丢了 exactly-once：慢半步的线程造出的第二份被丢弃，旧 bug 的变体）。构造廉价且无副作用，
放在模块加载时做，反而让「谁造的、何时造的」一目了然。

**只放类，不放实例**：`common/__init__.py` 契约 4（无模块级可变状态）对 `common/**` 仍然成立，
`tests/test_python_shape_budget.py::test_common_has_no_new_mutable_module_state` 盯着它。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Cell(Generic[T]):
    """一份进程级值的持有者：`get()` 读、`replace()` 换（退休旧值，返回新值）。

    线程语义：值是**指针交换**（构造时就有值，之后只在 `replace()` 里整体换掉），读者不会看到
    「换到一半」的对象 —— 不需要锁。要的是「只有一处写点、读点写明这是进程级单例」，不是一个
    事务；跨线程一致性仍由值自己的锁负责（`EvalExecutors._lock` 等）。
    """

    def __init__(self, value: T, *, retire: Callable[[T], None] | None = None) -> None:
        self._value = value
        #: 旧值退休动作（`close_pools` / `reset_for_tests`）。声明在这里而不是每次 `replace`
        #: 重述：怎么关只有值自己知道，而「换掉就关」是这份所有权的固定语义。
        self._retire = retire

    def get(self) -> T:
        """当前值（永远是同一个对象，直到 `replace()`）。"""
        return self._value

    def replace(self, value: T) -> T:
        """换一份并返回新值；旧值走 `retire`（没声明就只丢引用，由调用方自己收尾）。"""
        old = self._value
        self._value = value
        if self._retire is not None:
            self._retire(old)
        return value
