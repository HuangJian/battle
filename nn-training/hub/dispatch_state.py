"""hub/dispatch_state.py — 离线派发 / 观测旁表（B7-P4）。

从 `_HubQueue` 切出的**有状态协作者**：`_peeked`（多课程预取窗口读数）、`_dispatch`
（课程 → 离线派发记录，落盘）、`_leases`（课程级离线租约，进程内）、`_offline_disks`
（离线盘登记）、`_worker_prefetch`（worker 预取上报）、`_worker_errors`（云机错误 ring），
以及它们各自的 5 把旁路锁。

## 为什么锁仍由组合根建

L2 的闭集是「`Lock()` 只许出现在 `hub/auth.py` 与 `hub/queue.py`」。本片只换**持有者**：
`queue.py.__init__` 建锁后经 `DispatchLocks` 注入，`Dispatch` 只存引用。锁的集合与加锁
顺序一个没动（`_lease_lock` / `_dispatch_lock` / `_disk_lock` / `_pf_lock` / `_we_lock`）。

## 形状

两个 frozen dataclass 把「6 张表 + 5 把锁」各收成**一个**构造参数（`Dispatch.__init__`
只有两个业务参数，不触形状门禁的 >8 参线）。

## 约束

不 import 任何仓内模块（L0）；无方法、无判据——派发语义仍住 `queue_offline` /
`queue_observe`，本类只把状态从组合类身上挪走，组合类保留同名转发属性。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class DispatchTables:
    """6 张状态表（调用方建好、按引用传入）。"""

    peeked: dict[str, float]
    dispatch: dict[str, dict]
    leases: dict[str, dict]
    offline_disks: dict[str, float]
    worker_prefetch: dict[str, dict]
    worker_errors: deque[dict]


@dataclass(frozen=True)
class DispatchLocks:
    """5 把旁路锁（组合根建好后注入；本类不自建锁）。"""

    dispatch_lock: Lock
    lease_lock: Lock
    disk_lock: Lock
    pf_lock: Lock
    we_lock: Lock


class Dispatch:
    """离线派发 / 观测的 6 张表 + 5 把旁路锁（纯状态）。"""

    def __init__(self, tables: DispatchTables, locks: DispatchLocks) -> None:
        #: 课程 -> 最近被 `peek_jobs` 返回候选的时刻（秒），只被 `/admin/queue` 读。
        self._peeked = tables.peeked
        #: 课程 -> 离线派发记录（`pinned` / 完成锚；落盘 `offline-dispatch.json`）。
        self._dispatch = tables.dispatch
        #: `_dispatch` 的旁路锁（与 `_lock` 分开：派发状态面不与调度临界区互等）。
        self._dispatch_lock = locks.dispatch_lock
        #: 课程 -> 课程级离线租约 `{token, worker_id, at, expires_at}`（进程内）。
        self._leases = tables.leases
        #: `_leases` 的旁路锁。
        self._lease_lock = locks.lease_lock
        #: 离线盘登记表：disk_id -> last_seen（秒）。
        self._offline_disks = tables.offline_disks
        #: `_offline_disks` 的旁路锁（观测面不许和调度临界区互等）。
        self._disk_lock = locks.disk_lock
        #: worker 预取状态上报：worker_id -> `{held, dl, at}`。
        self._worker_prefetch = tables.worker_prefetch
        #: `_worker_prefetch` 的旁路锁。
        self._pf_lock = locks.pf_lock
        #: 云机错误上报的 ring（控制台 10s 节拍拉取）。
        self._worker_errors = tables.worker_errors
        #: `_worker_errors` 的旁路锁。
        self._we_lock = locks.we_lock
