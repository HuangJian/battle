"""remote/hub/queue_observe.py — 观测面与 job 作用域的只读摘要。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。这一簇的成员有一条共同点：**只读、不参与任何
调度决策**（唯一的例外是 `_job_dir`，它是路径解析而不是决策）。

* `queue_state` —— `/admin/queue`：每课程的深度/在飞/最近心跳/退避记录 + 竞速的两个判据数。
  它是「为什么某门课在饿着」的**唯一答案面**。
* `job_status` / `job_priority_of` / `epoch_of` / `lease_expires_in` /
  `last_heartbeat_ago` —— `/jobs/{id}/status` 的调度面摘要与 push 腿用的快照。
  为什么快照一份而不是让调用方自己去拼：判据落在 store 的 `_claimed` / `_computing` /
  `_ready` 上（同一把锁下的一致读），在队列层重新拼就是第二个事实源。
* `frozen_info` / `frozen_jobs` / `consume_freeze_announcement` / `unfreeze` / `reclaims`
  —— §4.1 毒包熔断的读面与**唯一**可逆口（人工解冻）。
* `_job_dir` / `job_root_of` / `shared_code_zip` —— 路径解析。

## `_MISSING_ROOT`：找不到归属时的**哨兵**根

`_job_dir` 在归属解析不到时返回 `<哨兵>/<job_id>`，而 `manifest.json` 必不存在 ⇒ 调用方
的 `(... / "manifest.json").exists()` 是 False，行为与「没这个 job」同。选系统临时目录而
不是仓库内目录：任何漏网的 mkdir 都落在 tmp（不污染真 store）。

## 依赖方向

`queue_observe → {common.protocol, remote.hub.store}`（向下，`remote.hub.store` 是
`_stores` 的注解需求）。**不 import 任何兄弟混入**。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.protocol import PRIORITY_NONE, ROLE_OFFLINE
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore
from remote.hub.task_pack import OFFLINE_DISK_WINDOW_SEC, OFFLINE_LEG_HINT

#: 未知 job_id 的哨兵根（随 `_job_dir` 一起搬进来）。
#: 选 tempdir 而不是仓库内目录：任何漏网的 mkdir 都落在系统临时目录（不污染真 store），
#: 而 handler 侧的 `(... / "manifest.json").exists()` 仍是 False ⇒ 行为与「没这个 job」同。
_MISSING_ROOT = Path(tempfile.gettempdir()) / "hub-queue-missing"


class QueueObserveMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _cursor: str | None
    #: 离线盘报名表与它自己的锁（组合类 `__init__` 建；见 `note_offline_disk` 的理由）
    _disk_lock: Any
    _now: Any
    _offline_disks: dict[str, float]
    _order: list[str]
    _stores: dict[str, _JobStore]

    # 本簇要调、而不在共同声明面 `QueuePeer` 里的那一个（理由见 `queue_peer.py` 头部）
    _store_of: Callable[[str], _JobStore | None]

    # ---- 观测面 ----
    def queue_state(self) -> dict:
        """`/admin/queue`：每课程的深度/在飞/最近心跳/退避记录 + 竞速的两个判据数。

        只读观测——不参与任何调度决策，但它是「为什么某门课在饿着」的唯一答案面。
        """
        now = self._now()
        courses: dict[str, dict] = {}
        for course in self._order:
            st = self._stores[course]
            pending = st.claimable_job_ids()
            inflight: list[dict] = []
            for jid in st.inflight():
                holder = st.lease_worker(jid) or "?"
                inflight.append(
                    {
                        "job_id": jid,
                        "worker": holder,
                        "heartbeat_ago": round(now - st._last_heartbeat.get(jid, now), 1),
                    }
                )
            courses[course] = {
                "mode": self.mode_of(course),
                # 停机达令是**每课程**的（一门课的门禁 ABORT 只停那门课的云机）
                "halt": self.halt_of(course),
                "pending": pending,
                "pending_n": len(pending),
                "inflight": inflight,
                "next_job": pending[0] if pending else None,
                # 归属可见（2026-09-25）：不说清楚「谁在等谁」，事后只能看到
                # 「队列不降」而不知道它是在等另一块盘（事故现场就是这样）。
                # 注意：这里**不**报 claimable 布尔——「可不可领」现在是**相对请求方角色**的
                # 属性，一个观察者不带角色，任何布尔都会误导（plan §2.2 评审修订）。
                "roles": {jid: self._stores[course].job_role(jid) for jid in pending},
                # §4.1 可观测（毒包熔断）：冻了谁、冻在几次；已冻的 job 已不在 pending 里，
                # 不给这一行就只剩「队列莫名其妙短了」
                "frozen": {
                    jid: st.frozen_info(jid) for jid in st.frozen_job_ids()
                },
            }
        return {
            "courses": courses,
            "order": self._order,
            "cursor": self._cursor,
            "offline": self.offline_courses(),
            "active_courses": self.active_courses(),
            "active_workers": self.active_worker_count(),
            "halt": self.all_halted(),
            # job 身份歧义面（2026-09-24 事故）：非空 = 有 jid 挂在 ≥2 门课上，而
            # `course_of` 对它们一律拒答（那些 job 谁都跑不了）⇒ 必须让操作员一眼看见。
            "ambiguous_jids": self.ambiguous_jids(),
            # 离线盘的报到面 + 「没人能领的离线队列项」（plan §7.2.3 的读数；见方法 docstring）
            "offline_disk": self.offline_disk_readout(),
        }

    # ---- job 作用域委派（与 `_JobStore` 同名同签名） ----
    def _job_dir(self, job_id: str) -> Path:
        """归属课程 job 目录；找不到 → 哨兵路径（`manifest.json` 必不存在 ⇒ 调用方 404）。"""
        st = self._store_of(job_id)
        if st is None:
            return _MISSING_ROOT / str(job_id)
        return st._job_dir(job_id)

    def job_root_of(self, course: str) -> Path | None:
        st = self._stores.get(course)
        return st.job_root if st else None

    def shared_code_zip(self, course: str = "") -> Path | None:
        """共享 code.zip 路径：给课程就用它，否则用第一份**真存在**的（bootstrap 用）。"""
        order = [course] if course in self._stores else self._order
        for c in order:
            p = self._stores[c].job_root / "code.zip"
            if p.exists():
                return p
        return None

    def epoch_of(self, job_id: str) -> int:
        """该 job 归属 store 的调度面版本（单课程/多课程统一口径；未知 → 0）。"""
        st = self._store_of(job_id)
        return st.scheduling_epoch() if st else 0

    def job_status(self, job_id: str) -> dict:
        """`GET /jobs/{id}/status` 的调度面摘要（cancel-watcher 的判据就在里面）。"""
        st = self._store_of(job_id)
        if st is None:
            return {}
        facts = st.scheduling_facts(job_id)
        prio, why = st.priority_for(job_id)
        return {
            "landed": bool(facts["landed"]),
            "ready": bool(facts["ready"]),
            "computing_at": facts["computing_at"],
            "lease_holder": facts["lease_holder"],
            "epoch": st.scheduling_epoch(),
            "priority": prio,
            "reason": why,
        }

    def job_priority_of(self, job_id: str, *, exclude_worker: str = "") -> str:
        """该 job 当前的优先级档（push 腿用的**同一张表**，§2.9；未知 → none）。

        为什么快照一份而不是让 push 腿自己去拼：判据落在 store 的 `_claimed`/`_computing`/
        `_ready` 上（同一把锁下的一致读），在队列层重新拼就是第二个事实源。
        """
        st = self._store_of(job_id)
        if st is None:
            return PRIORITY_NONE
        prio, _why = st.priority_for(job_id, exclude_worker=exclude_worker)
        return prio

    def reclaims(self, job_id: str) -> int:
        """该 job 的「认领后零回传」次数（§4.1 熔断判据；未知 job → 0）。"""
        st = self._store_of(job_id)
        return st.reclaims(job_id) if st else 0

    def frozen_info(self, job_id: str) -> dict | None:
        """该 job 的冻结记录（未冻结/未知 → None）。"""
        st = self._store_of(job_id)
        return st.frozen_info(job_id) if st else None

    def consume_freeze_announcement(self, job_id: str) -> dict | None:
        """取一次「刚刚落冻」的告警载荷（一次性；未冻结/已喊过 → None）。"""
        st = self._store_of(job_id)
        return st.consume_freeze_announcement(job_id) if st else None

    def unfreeze(self, job_id: str) -> dict | None:
        """人工解冻（§4.1 可逆口）：返回被解冻的记录（本来未冻结 → None）。"""
        st = self._store_of(job_id)
        return st.unfreeze(job_id) if st else None

    def frozen_jobs(self) -> list[str]:
        """全部已冻结 job（跨课程，观测面用）。"""
        out: list[str] = []
        for st in self._stores.values():
            out.extend(st.frozen_job_ids())
        return out

    def lease_expires_in(self, job_id: str) -> float | None:
        """距租约到期秒数；无租约/无归属 → None（`/jobs/{id}/status` 观测用）。"""
        st = self._store_of(job_id)
        if st is None:
            return None
        exp = st._leases.get(job_id)
        return None if exp is None else round(exp - st._now(), 1)

    def last_heartbeat_ago(self, job_id: str) -> float | None:
        """距上次心跳秒数；从未心跳/无归属 → None。"""
        st = self._store_of(job_id)
        if st is None:
            return None
        hb = st._last_heartbeat.get(job_id)
        return None if hb is None else round(st._now() - hb, 1)

    def note_offline_disk(self, disk_id: str) -> None:
        """登记一次**离线盘**露面（`X-Battle-Offline` 的持有者）；空 id 记成 `<offline>`。

        判据（头）由 handler 侧解析，这里只记账——与 job 腿的归属闸共用同一份 `ROLE_HEADER`
        语义：带标 = 离线盘，缺席 = 在线盘（旧 hub/旧 worker 混合部署逐字节兼容）。
        """
        did = (disk_id or "").strip() or "<offline>"
        with self._disk_lock:
            self._offline_disks[did] = self._now()

    def offline_disk_readout(self) -> dict:
        """「本环境有没有离线盘」+「有没有没人能领的离线队列项」——plan §7.2.3 的读数。

        两个数字各治一个坑：
        · `recent_n`：有离线盘在线 ⇒ 离线课的任务包有人取（这是取包链**唯一**的报到面）；
        · `stale_jobs`：队列里还挂着 `role=offline` 的**待领**项 ⇒ **没有消费者**。
          `kind=run` 队列腿 2026-09-25 退役后，这类项只可能来自「盘上遗留 / 手写参数 /
          混部期旧 hub」，`hint` 直接给该走哪条路（本机也不再有任何人在等它，不再白等 8h）。
        """
        now = self._now()
        with self._disk_lock:
            recent = sorted(
                d
                for d, seen in self._offline_disks.items()
                if now - seen <= OFFLINE_DISK_WINDOW_SEC
            )
            last = max(self._offline_disks.values(), default=0.0)
        stale: list[dict] = []
        for course in self._order:
            st = self._stores[course]
            for jid in st.claimable_job_ids():
                if st.job_role(jid) == ROLE_OFFLINE:
                    stale.append({"course": course, "job_id": jid})
        out: dict = {
            "recent_n": len(recent),
            "recent": recent,
            "last_seen_ago": round(now - last, 1) if last else None,
            "stale_jobs": stale,
        }
        if stale:
            # 只有真存在「没人能领的离线项」才喊：这句话是给操作员的下一步，不是背景噪音。
            out["hint"] = OFFLINE_LEG_HINT
        return out
