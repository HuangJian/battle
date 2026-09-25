"""remote/hub/queue_offline.py — 离线任务清单 + 领取租约（`_HubQueue` 的第八个域混入）。

## 这个域治什么

离线课（`mode=offline`）的活**不走 job 队列**：云机从 hub 领的是**任务包**（`task-<课>.zip`），
而 hub 得回答两个问题：

  ① **「有哪些活可领」** —— `GET /offline/tasks`（零副作用：不触发重导、不写账本、不动游标）；
  ② **「这一门现在归谁」** —— 领取租约 `claim` / `heartbeat` / `release`（plan §3.2），
     好让两台云机不会同时从同一份旧包起跑。

读面（清单）与写面（租约）**必须同源**：清单里的 `holder` / `claimable` / `state` 与租约
领取的判据是同一批函数（`lease_verdict` / `task_state`，住 `hub/task_pack.py`）——
两处各写一份判据的话，「清单说可领、领取说被人持有」这种自相矛盾迟早出现。

## 为什么是第八个混入（而不是塞进已有的那一簇）

它跟七个老簇**都不共享状态**：`_leases` / `_lease_lock` 只有它读写（组合类 `__init__` 建），
`_offline_disks` 属观测簇（`queue_observe`），任务包路径/进度属 `queue_resume`。硬塞进任何一簇，
那一簇就会多出「跟谁都不相干」的一半。

## 依赖方向

`queue_offline → {common.protocol, remote.hub.queue_peer, remote.hub.store,
remote.hub.task_pack}`（严格向下；跨域调用一律走 `self.`，形参面由 `QueuePeer` 声明）。
"""

from __future__ import annotations

import secrets
from threading import Lock
from typing import Any

from common.protocol import (
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    INIT_WEIGHTS_NAME,
    OFFLINE_LEASE_TTL_SEC,
    ProtocolError,
)
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore
from remote.hub.task_pack import (
    TASK_STATE_NOT_OFFLINE,
    TASK_STATE_RANK,
    _file_sha256,
    lease_verdict,
    pack_index_meta,
    pack_index_part_sha,
    task_pack_stale_reason,
    task_state,
)


class QueueOfflineMixin(QueuePeer):
    """域混入：离线任务清单 + 领取租约。见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`，声明一律是裸注解）----
    _discover_root: Any
    _order: list[str]
    _stores: dict[str, _JobStore]
    _now: Any
    #: **离线租约**（课程 → `{token, worker_id, at, expires_at}`）：进程内、惰性过期
    #: （组合类 `__init__` 里那份声明是带值的那一半）。
    _leases: dict[str, dict]
    _lease_lock: Lock


    def offline_task_courses(self) -> list[str]:
        """清单的**候选面**（评审 S-1）：课程表里 `mode=offline` 的 ∪ 盘上有开课标记的。

        为什么不只认课程表：课程表是「1 小时新鲜度扫描」的产物，而**离线课本机不训练**
        ⇒ 课冷掉 / hub 重启之后它从表里消失，而包还在盘上——只认表会让云机问清单时得到
        「没有任务」（明明有一份包在等它领）。判据与 404 自愈门同源（`_task_pack_miss_candidate`）。
        """
        out = [c for c in self._order if self.mode_of(c) == COURSE_MODE_OFFLINE]
        root = self._discover_root
        if root is None:
            return out
        try:
            names = sorted(p.name for p in root.iterdir() if p.is_dir())
        except OSError:
            return out
        for name in names:
            if name in out or name in self._stores:
                continue  # 表里已有的走上面那条（表里是 online ⇒ 清单给 `not_offline`，不在这儿加）
            try:
                if (root / name / COURSE_ENABLE_MARKER).exists():
                    out.append(name)
            except OSError:
                continue
        return out

    def offline_tasks(self, *, include_all: bool = False) -> list[dict]:
        """`GET /offline/tasks` 的内容——**零副作用**（不触发重导、不写账本、不动游标）。

        每行字段定死在 §3.1：`course/state/claimable/pack/run_id/it/end_it/stale_reason/
        holder/progress`。读不到就如实给空值（一个坏包不该把整张清单变成 500）。
        """
        progress = self.offline_progress()
        courses = self.offline_task_courses()
        if include_all:
            for c in self._order:
                if c not in courses:
                    courses.append(c)
        rows: list[dict] = []
        for course in courses:
            try:
                pack_path = self.task_pack_path(course)
            except ProtocolError:
                continue  # 目录名不合规（历史残留）⇒ 清单里跳过，不当 500 报
            real = course in self._stores
            # 不在表里、只在盘上有开课标记 ⇒ 按离线意图算（评审 S-1）。
            offline = self.mode_of(course) == COURSE_MODE_OFFLINE if real else True
            pack: dict | None = None
            meta = {"run_id": "", "it": 0, "end_it": 0, "commit": "", "created_at": 0.0}
            stale_reason = ""
            if pack_path.is_file():
                try:
                    st = pack_path.stat()
                    pack = {
                        "name": pack_path.name,
                        "bytes": int(st.st_size),
                        "sha256": _file_sha256(pack_path),
                        "mtime": float(st.st_mtime),
                    }
                except OSError:
                    pack = None
                if pack is not None:
                    meta = pack_index_meta(pack_path)
                    stale_reason = task_pack_stale_reason(
                        pack_init_sha=pack_index_part_sha(pack_path, INIT_WEIGHTS_NAME),
                        active_sha=_file_sha256(
                            pack_path.parent / _JobStore.ACTIVE_WEIGHTS_NAME
                        ),
                    )
            holder = self.holder_info(course)
            state = (
                task_state(
                    pack_exists=pack is not None, stale=bool(stale_reason), held=holder is not None
                )
                if offline
                else TASK_STATE_NOT_OFFLINE
            )
            runs = progress.get(course) or {}
            latest = max(runs.values(), key=lambda r: float(r.get("last_mtime") or 0.0)) if runs else None
            rows.append(
                {
                    "course": course,
                    "state": state,
                    # 可领 = 离线 ∧ 有包 ∧ 无主。**过期包也可领**：包旧只意味着起点旧，
                    # 而「领不领」是云机的判断（它还要比 `served` 的 sha）。
                    "claimable": bool(offline and pack is not None and holder is None),
                    "pack": pack,
                    "run_id": meta["run_id"],
                    "it": meta["it"],
                    "end_it": meta["end_it"],
                    "stale_reason": stale_reason,
                    "holder": holder,
                    "progress": {
                        "count": int(latest["count"]) if latest else 0,
                        "last_mtime": float(latest["last_mtime"]) if latest else 0.0,
                    },
                }
            )
        rows.sort(
            key=lambda r: (
                TASK_STATE_RANK.get(str(r["state"]), 9),
                float((r["pack"] or {}).get("mtime") or 0.0),
                str(r["course"]),
            )
        )
        return rows

    def _lease_rec(self, course: str) -> dict | None:
        """有效租约（**惰性过期**：读时就地清掉 ⇒ 读面与领取面同一条判据）。"""
        now = float(self._now())
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is not None and float(rec.get("expires_at", 0.0)) <= now:
                del self._leases[course]
                return None
            return dict(rec) if rec else None

    def offline_lease(self, course: str) -> dict | None:
        """某门课的**有效**租约（对外只读面：清单 / 拒因 / 控制台都用它）。"""
        return self._lease_rec(course)

    def holder_info(self, course: str) -> dict | None:
        """持有人那三行（`worker_id` / `age_sec` / `expires_in`）——清单与拒因共用一份。"""
        rec = self._lease_rec(course)
        if not rec:
            return None
        now = float(self._now())
        return {
            "worker_id": str(rec.get("worker_id") or ""),
            "age_sec": round(max(0.0, now - float(rec.get("at") or now)), 1),
            "expires_in": round(max(0.0, float(rec.get("expires_at") or now) - now), 1),
        }

    def claim_offline(
        self, course: str, worker_id: str, *, takeover: bool = False
    ) -> tuple[dict, str]:
        """领一门课的离线租约 → `(租约, "")`；领不到 → `({}, "foreign"|"bad")`。

        `mine`（同一个 `worker_id` 回来）与 `expired` 直接续上：cell 中断后重跑不该被
        **自己留下**的租约挡住（评审 G1）。`takeover=True` 是显式接管（控制台/人工搬机）。
        """
        wid = str(worker_id or "").strip()
        if not wid:
            return {}, "bad"
        now = float(self._now())
        with self._lease_lock:
            verdict = lease_verdict(now, self._leases.get(course), wid)
            if verdict == "foreign" and not takeover:
                return {}, "foreign"
            lease = {
                "token": secrets.token_hex(8),
                "worker_id": wid,
                "at": now,
                "expires_at": now + OFFLINE_LEASE_TTL_SEC,
            }
            self._leases[course] = lease
            return self._lease_pub(course, lease), ""

    @staticmethod
    def _lease_pub(course: str, lease: dict) -> dict:
        """租约 → 响应体（`ttl_sec` 用常量；内部字段 `at` 不外漏）。"""
        return {
            "token": str(lease.get("token") or ""),
            "course": course,
            "worker_id": str(lease.get("worker_id") or ""),
            "ttl_sec": OFFLINE_LEASE_TTL_SEC,
            "expires_at": float(lease.get("expires_at") or 0.0),
        }

    def heartbeat_offline(self, course: str, lease_token: str) -> tuple[dict, str]:
        """续租 → `({"ttl_sec","expires_at"}, "")`；已过期 → `"expired"`；被接管 → `"taken"`。"""
        now = float(self._now())
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is None or float(rec.get("expires_at", 0.0)) <= now:
                self._leases.pop(course, None)
                return {}, "expired"
            if str(rec.get("token") or "") != str(lease_token or ""):
                return {}, "taken"
            rec["expires_at"] = now + OFFLINE_LEASE_TTL_SEC
            return {"ttl_sec": OFFLINE_LEASE_TTL_SEC, "expires_at": rec["expires_at"]}, ""

    def release_offline(self, course: str, lease_token: str) -> tuple[bool, str]:
        """交还租约（**不覆盖别人的**）：过期/没领过 ⇒ 本来就无主，空操作也算成功。"""
        now = float(self._now())
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is None or float(rec.get("expires_at", 0.0)) <= now:
                self._leases.pop(course, None)
                return True, ""
            if str(rec.get("token") or "") != str(lease_token or ""):
                return False, "foreign"
            del self._leases[course]
            return True, ""

    def offline_leases(self) -> dict[str, dict]:
        """有效租约一览（`/admin/offline` 的 `leases` 字段：控制台回答「谁在跑哪门课」）。"""
        out: dict[str, dict] = {}
        for course in list(self._leases):
            info = self.holder_info(course)
            if info is not None:
                out[course] = info
        return out



# ── 生成器备注（tmp/gen_queue_offline.py；生成后的人工部分已并入正文）─────
# 这十个方法是 origin `remote/hub_server.py::_HubQueue` 的**逐字**搬移（顺序按调度面原序：
# 清单 → 租约读 → 租约写）。跨域调用的形参面由 `QueuePeer` 声明，本模块只声明自己
# 拥有的那几项状态（`_leases` / `_lease_lock`）。
