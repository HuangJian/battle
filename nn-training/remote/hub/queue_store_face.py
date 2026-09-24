"""remote/hub/queue_store_face.py — job 作用域**门面**：18 个与 `_JobStore` 同名同签名的方法。

`_HubQueue` 的七个域混入之一（S4 第十五刀），也是唯一**没有状态、没有兄弟引用**的一簇：
它只做「先解析归属，再转发」。

## 为什么这层存在（不是重复代码）

`_HubQueue` 的类 docstring 写着「对外的 job 作用域方法与 `_JobStore` **同名同签名**（内部先
解析归属），所以 handler 侧只需把 `self.hub.` 换成 `self.hub.`，单课程行为逐字节等价」。
这是**有意的门面**：路由混入（`hub/schedule.py` / `hub/result.py` / `hub/blob.py` /
`hub/offline.py` / `hub/admin.py`）与 `remote/push_dispatch.py` 都只认 `self.hub.X`，
它们不该知道「单课程 / 多课程」这个区分。本簇是那个让区分**消失**的地方。

## 为什么不收成一个通用的 `__getattr__` / 名字表

侦察（第十五刀）逐条量过 28 个同名方法，结论是**不能收**：

* **缺归属时的默认值逐方法不同**（`False` / `None` / `{}` / `[]` / `0` / 不返回）；
* 名字表 + `getattr(st, "start_job")` 会把 `_JobStore` 是否真有这个方法从 **mypy 的检查范围**
  里拿掉——本仓 mypy 是绿的门禁项，拿它换 12 行样板不划算；
* 守卫 `tests/test_hub_queue_split.py` 反而能做一件更强的事：把 docstring 承诺的
  「**同名同签名**」变成**可执行断言**（逐参数名 + 默认值 + 注解对账）。写得越显式，这条
  断言越有信息量。

所以：**保留显式写法，给它一个家，并把契约钉成测试。**

## 唯一的语义点（`count` 不出来的那类）

`result_token_ok` 的注释「找不到归属 = 这个 job 不归本 hub 管 ⇒ **拒收**」是刻意保留的：
放行会写出一个无归属的 result。`claim` 保持**多行显式转发**（6 个形参全用关键字传，与
`_JobStore.claim` 的参数名一一对应）。

## 依赖方向

`queue_store_face → {remote.hub.store, remote.hub.queue_peer}`（向下）—— 它**不** import 任何
兄弟混入：所有转发都是 `self._store_of(...)`（那个方法住 `queue_scope`，经 `self` 解析）。
`_store_of` 的声明在本簇自己那段（它要 `_JobStore`，而共同声明面 `QueuePeer` **不能**引用它
—— 理由见 `queue_peer.py` 头部）；`claim` 的 `CLAIM_TTL_SEC` 缺省值随它一起在
`queue_claims`，不在本簇。
"""

from __future__ import annotations

from collections.abc import Callable

from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore


class QueueStoreFaceMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----

    # 本簇要调、而不在共同声明面 `QueuePeer` 里的那一个（理由见 `queue_peer.py` 头部）
    _store_of: Callable[[str], _JobStore | None]

    def start_job(self, job_id: str, worker_id: str = "") -> bool:
        st = self._store_of(job_id)
        return st.start_job(job_id, worker_id) if st else False

    def set_ready(self, job_id: str, worker_id: str = "") -> bool:
        st = self._store_of(job_id)
        return st.set_ready(job_id, worker_id) if st else False

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        return st.heartbeat(job_id, lease_token) if st else False

    def release(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        return st.release(job_id, lease_token) if st else False

    def result_token_ok(self, job_id: str, lease_token: str) -> bool:
        st = self._store_of(job_id)
        # 找不到归属 = 这个 job 不归本 hub 管 ⇒ **拒收**（放行会写出一个无归属的 result）
        return st.result_token_ok(job_id, lease_token) if st else False

    def store_result(self, job_id: str, result: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_result(job_id, result) if st else False

    def store_job_failure(self, job_id: str, rec: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_job_failure(job_id, rec) if st else False

    def job_failure(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.job_failure(job_id) if st else None

    def get_result(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.get_result(job_id) if st else None

    def mark_completed(self, job_id: str) -> None:
        st = self._store_of(job_id)
        if st:
            st.mark_completed(job_id)

    def record_payload_sent(self, job_id: str, n: int) -> None:
        st = self._store_of(job_id)
        if st:
            st.record_payload_sent(job_id, n)

    def record_result_recv(self, job_id: str, n: int) -> None:
        st = self._store_of(job_id)
        if st:
            st.record_result_recv(job_id, n)

    def record_push_wire(self, job_id: str, n: int, payload_bytes: int, upload_sec: float) -> None:
        """hub 中介推送的传输实测（按 job 归属委派；未知 job 静默跳过）。"""
        st = self._store_of(job_id)
        if st:
            st.record_push_wire(job_id, n, payload_bytes, upload_sec)

    def wire_stats(self, job_id: str) -> dict:
        st = self._store_of(job_id)
        return st.wire_stats(job_id) if st else {}

    def store_bc_epoch(self, job_id: str, body: dict) -> bool:
        st = self._store_of(job_id)
        return st.store_bc_epoch(job_id, body) if st else False

    def get_bc_resume(self, job_id: str) -> dict | None:
        st = self._store_of(job_id)
        return st.get_bc_resume(job_id) if st else None

    def get_bc_metrics(self, job_id: str) -> list[dict]:
        st = self._store_of(job_id)
        return st.get_bc_metrics(job_id) if st else []

    def append_ledger(self, job_id: str, event: dict) -> None:
        """往**归属课程**的账本追加一行（`/jobs/{id}/fail` 的 job_cancelled 用）。"""
        st = self._store_of(job_id)
        if st is not None:
            st._append_ledger(event)

