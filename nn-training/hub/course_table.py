"""hub/course_table.py — 课程表：store 登记 · 轮转序 · 归属缓存（B7-P3）。

从 `_HubQueue` 切出的**有状态协作者**。它只装数据，不装行为：`_stores`（课程 → store）、
`_order`（轮转序）、`_solo`（单课程借表指针）、`_cursor`（轮转起点）、`_locate_cache`
（job → 课程归属缓存）、`_ambiguous`（身份歧义记录）。

## 为什么只搬状态

`_HubQueue` 的 8 个混入共 138 个方法都经 `self` 读这几张表（`self._stores[...]` 等）；
把方法也搬走要留 90 个跨域 seam。本片只换**持有者**：组合类保留同名属性（getter/setter
转发到本对象），既有调用点与测试逐字不变；`self.courses._stores` 是新的显式读写面。

## 约束

* 不 import 任何仓内模块（L0）；`T` 由组合类实例化为 `_JobStore`。
* `_solo` 的**借表**语义不变：单课程时它指向那唯一一份 store，`_adopt_solo` 在运行期把
  进程级状态搬去 `WorkerBoard` 后置 None（由组合类的同名 setter 写入）。
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class CourseTable(Generic[T]):
    """课程 → store 的登记表 + 路由/轮转缓存（纯状态）。"""

    def __init__(self, stores: dict[str, T], order: list[str] | None = None) -> None:
        self._stores: dict[str, T] = dict(stores)
        self._order: list[str] = [c for c in (order or list(self._stores)) if c in self._stores]
        #: 单课程 = 旧形状：进程级状态借那一份 store（见 `_adopt_solo`）
        self._solo: T | None = next(iter(self._stores.values())) if len(self._stores) == 1 else None
        #: 上次派发过的课程（轮转起点）；None = 从序首开始
        self._cursor: str | None = None
        #: job_id -> course（归属解析缓存；job_id 不可复用，故不会失效）
        self._locate_cache: dict[str, str] = {}
        #: jid -> 同时持有它的课程（≥2 = 身份歧义）
        self._ambiguous: dict[str, list[str]] = {}
