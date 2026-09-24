"""remote/hub/store_wire.py — M0 统一计量：每 job 的**传输层实测字节**账。

`_JobStore` 的六个域混入之一（S4 第十四刀）。四个方法是一件事的四个面：下发（payload 字节）·
回传（结果字节）· push 腿的 wire 子字典 · 读数。

volatile，重启即丢，**不参与任何调度决策** ⇒ 与其它簇零互调，是拆分里最干净的一块
（`store_wire → {}`，零仓内依赖声明）。
"""

from __future__ import annotations

from threading import Lock


class WireMeterMixin:
    """域混入：见模块头部。"""

    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock

    def _init_wire(self) -> None:
        #: job_id -> {"sent_bytes", "recv_bytes"}（M0 统一计量：传输层实测字节，
        #: 供 iteration 事件的 wire 子字典对账 / M1 A-B 归因）。volatile，重启即丢，
        #: 只做观测，不参与任何调度决策。
        self._wire: dict[str, dict] = {}

    # ---- 统一计量（M0）：传输层实测字节 ----
    def record_payload_sent(self, job_id: str, n: int) -> None:
        """记一次 /jobs/{id}/payload 服务出去的字节数（累积——重下会累加）。"""
        with self._lock:
            w = self._wire.setdefault(job_id, {})
            w["sent_bytes"] = int(w.get("sent_bytes", 0)) + int(n)

    def record_push_wire(self, job_id: str, n: int, payload_bytes: int, upload_sec: float) -> None:
        """记一次 **hub 中介推送**的传输实测（push 腿的 `wire_hub` 来源）。

        字段名与直推（训练侧 `submit_job` 自己返回的那份）**逐字一致**
        （body_bytes/payload_bytes/upload_sec），所以训练侧 `_wire_from_result(is_push=True)`
        读法完全一样——两种 push 的可观测性不该一个有一个无（多课程并行时，哪条腿在吃
        流量要靠它分组）。重推同一 job 累加 body_bytes（与 payload_sent 同规）。
        """
        with self._lock:
            w = self._wire.setdefault(job_id, {})
            w["body_bytes"] = int(w.get("body_bytes", 0)) + int(n)
            w["payload_bytes"] = int(payload_bytes)
            w["upload_sec"] = round(float(upload_sec), 3)

    def record_result_recv(self, job_id: str, n: int) -> None:
        """记一次 /jobs/{id}/result 收到的请求体字节数（= 云上行 result 体大小）。"""
        with self._lock:
            w = self._wire.setdefault(job_id, {})
            w["recv_bytes"] = int(w.get("recv_bytes", 0)) + int(n)

    def wire_stats(self, job_id: str) -> dict:
        """该 job 的传输层实测字节（无记录 = {}）。只读快照。"""
        with self._lock:
            return dict(self._wire.get(job_id, {}))
