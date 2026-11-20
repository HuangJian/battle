"""hub/worker_board.py — 进程级 worker 登记 + 停机达令（B7-P3）。

从 `_HubQueue` 切出的**有状态协作者**：`_workers`（worker_id → 最近轮询时刻）、
`_halt_default`（无课程参数时的达令）、`_halts`（按课程的例外）、`_halt_when_idle`
（「本轮干完再停」门闩）。

## 为什么鉴权计数不在这里

`_AuthGuard`（`hub/auth.py`）同时被 `_JobStore` 与 `_HubQueue` 继承，`_auth_fail` /
`_auth_blocked_until` 是**它**的状态；本片不动鉴权语义（只切队列自己的两张表）。
组合类仍保留同名属性转发，`_adopt_solo` 的借表语义不变。

## 约束

不 import 任何仓内模块（L0）；无方法、无锁——锁仍由组合根注入的 `_AuthGuard` 持有。
"""

from __future__ import annotations


class WorkerBoard:
    """worker 登记表 + 停机达令（纯状态）。"""

    def __init__(self) -> None:
        #: worker 登记表：worker_id -> last_seen（秒）。单课程时借 store 那一份。
        self._workers: dict[str, float] = {}
        #: 无课程参数 = 全课程（旧调用方语义，新发现的课也继承）
        self._halt_default = False
        #: 按课程的停机例外（`?course=` 只动那一门）
        self._halts: dict[str, bool] = {}
        #: 「本轮干完再停」门闩（`halt_if_idle` 置位；空闲取活时重评）
        self._halt_when_idle = False
