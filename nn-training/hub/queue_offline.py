"""hub/queue_offline.py — 离线任务清单 + 领取租约（`_HubQueue` 的第八个域混入）。

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

`queue_offline → {common.protocol, hub.queue_peer, hub.store,
hub.task_pack}`（严格向下；跨域调用一律走 `self.`，形参面由 `QueuePeer` 声明）。
"""

from __future__ import annotations

import json
import math
import os
import secrets
from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import (
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    COURSE_MODES,
    INIT_WEIGHTS_NAME,
    OFFLINE_LEASE_TTL_SEC,
    ProtocolError,
)
from hub.queue_peer import QueuePeer
from hub.store import _JobStore
from hub.task_pack import (
    TASK_STATE_COMPLETED,
    TASK_STATE_NOT_OFFLINE,
    TASK_STATE_RANK,
    _file_sha256,
    lease_verdict,
    pack_index_meta,
    pack_index_part_sha,
    reset_auto_handoff_triggers,
    task_pack_stale_reason,
    task_state,
)

# ── 自动离线交接：派发状态（plan/auto-offline-handoff §3.2/§3.3/§3.8，2026-10-03）──
#
# 治什么：hub 的 `_modes` 是 volatile（重启回启动参数），而自动 claim 翻出来的 offline
# 一旦重启就消失 —— 正在 TPU 上跑的课会解封队列（数据损坏级）。落盘与纯判据在这里，
# 临界区在 `claim_offline` / `set_mode_pinned` / `begin_auto_handoff`。
#: 派发状态文件名（落 `<traj>/<课>/`，与 hub 自己的账本/manifest 同域）。
DISPATCH_NAME = "offline-dispatch.json"
#: 文件 schema 版本（将来加字段时给人一个判据，不猜）。
DISPATCH_VERSION = 1
#: 默认停滞阈值（秒；压「已翻 offline、没人跑」的窗口，§3.9）。env 可覆盖（测试用）。
OFFLINE_STALL_SEC = 1800.0
#: 交接窗口（秒）：claim 已翻 offline、包还没出现 —— 这段时间内别的自动课也算「机器忙」
#: （§3.3a 一拖一）。超窗 = 交接失败，交给 `offline_stalled` 告警，**不再占闸**
#: （否则一次导出失败会把整条自动链冻死）。
AUTO_HANDOFF_PENDING_SEC = 900.0


def dispatch_record_default(fallback_mode: str) -> dict:
    """一条派发记录的缺省形状（**纯函数**；读不到文件时用它）。"""
    return {
        "v": DISPATCH_VERSION,
        "mode": fallback_mode,
        "pinned": False,
        "claimed_offline": False,
        "claimed_by": "",
        "claimed_at": 0.0,
        "flipped_at": 0.0,
        "completed_pack_sha": "",
        "updated_at": 0.0,
    }


def dispatch_record_merge(raw: object, fallback_mode: str) -> dict:
    """把盘上那份（不可信）归一成一条记录：形状不对的字段一律退回缺省（**纯函数**）。

    为什么宽容：派发状态是「重启后能不能继续跑」的唯一依据，一个写半行的 json 不该让
    hub 起不来；同时 mode/pinned 的合法性必须钉死（它们决定派发闸）。
    """
    rec = dispatch_record_default(fallback_mode)
    if not isinstance(raw, dict):
        return rec
    mode = str(raw.get("mode") or "").strip().lower()
    if mode in COURSE_MODES:
        rec["mode"] = mode
    rec["pinned"] = raw.get("pinned") is True
    rec["claimed_offline"] = raw.get("claimed_offline") is True
    rec["claimed_by"] = str(raw.get("claimed_by") or "")
    for key in ("claimed_at", "flipped_at", "updated_at"):
        try:
            rec[key] = max(0.0, float(raw.get(key) or 0.0))
        except (TypeError, ValueError):
            rec[key] = 0.0
    rec["completed_pack_sha"] = str(raw.get("completed_pack_sha") or "")
    return rec


def open_time_key(mtime: float | None) -> float:
    """排序键「开课时间」的哨兵方向（**纯函数**）：读不到 ⇒ **`+inf`（排最后）**。

    为什么必须是 `+inf`：清单按升序排，旧键在缺包时取 `0.0` 会排最前 —— 叠加自动课
    「普遍无包」会得到「首选一门跑不了的课」（二轮 P1-5/P1-4）。
    """
    return math.inf if mtime is None else float(mtime)


def auto_claimable(
    *,
    auto: bool,
    offline: bool,
    pack_exists: bool,
    holder_present: bool,
    completed: bool,
    busy: bool,
) -> bool:
    """清单的 claimable 判据（**纯函数**；清单与 claim 面同源）。

    · auto 课允许**无包**（领它触发导包 —— P0-1 的唯一入口）；
    · pinned/普通离线课仍是「有包才能领」；
    · 完成态（当前包已跑满）不可再领（二轮 P1-1）；busy 闸对 auto 课生效（§3.3a）。
    """
    if holder_present or completed or busy:
        return False
    if pack_exists and (offline or auto):
        return True
    return bool(auto and not pack_exists)


def stall_verdict(
    *,
    treat_offline: bool,
    pinned: bool,
    completed: bool,
    holder_present: bool,
    last_progress_mtime: float,
    flipped_at: float,
    threshold: float,
    now: float,
) -> str:
    """停滞判据（**纯函数**）：`""` = 没停；否则是原因。

    覆盖两段（二轮 P1-3）：① running（活租约）但进度超阈值；② 已翻 offline、无活租约、
    未完成（导包窗口 / 导出失败）且翻 mode 时刻超阈值。只用已有事实，不引入新状态。
    """
    if not treat_offline or pinned or completed:
        return ""
    progress_age = now - last_progress_mtime if last_progress_mtime > 0 else math.inf
    if holder_present:
        return "running-stale" if progress_age > threshold else ""
    anchor = max(flipped_at, last_progress_mtime if last_progress_mtime > 0 else 0.0)
    if anchor > 0 and now - anchor > threshold:
        return "pending-export"
    return ""


class QueueOfflineMixin(QueuePeer):
    """域混入：离线任务清单 + 领取租约。见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`，声明一律是裸注解）----
    _discover_root: Any
    _order: list[str]
    _stores: dict[str, _JobStore]
    #: 课程 → 生效模式（自动交接要翻它、claim 后要把 claim 翻的 offline 落进去）。
    _modes: dict[str, str]
    _now: Any
    #: **离线租约**（课程 → `{token, worker_id, at, expires_at}`）：进程内、惰性过期
    #: （组合类 `__init__` 里那份声明是带值的那一半）。
    _leases: dict[str, dict]
    _lease_lock: Lock
    #: **派发状态**（课程 → 记录；T1，2026-10-03）：`pinned` / claim 翻的 offline / 完成锚
    #: （包 sha）。带值声明在组合类 `__init__`；每次更新原子落盘 `<课>/offline-dispatch.json`。
    _dispatch: dict[str, dict]
    _dispatch_lock: Lock


    def offline_task_courses(self) -> list[str]:
        """清单的**候选面**（评审 S-1）：课程表里 `mode=offline` 的 ∪ 盘上有开课标记的。

        为什么不只认课程表：课程表是「1 小时新鲜度扫描」的产物，而**离线课本机不训练**
        ⇒ 课冷掉 / hub 重启之后它从表里消失，而包还在盘上——只认表会让云机问清单时得到
        「没有任务」（明明有一份包在等它领）。判据与 404 自愈门同源（`_task_pack_miss_candidate`）。
        """
        out = [
            c
            for c in self._order
            if self.mode_of(c) == COURSE_MODE_OFFLINE or self.auto_eligible(c)
        ]
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
            # 自动交接（T2）：未 pin ∧ 在训（开课标记在）⇒ 可见且可领（允许无包）
            auto = self.auto_eligible(course)
            pack_sha = str((pack or {}).get("sha256") or "")
            completed = self.completion_blocked(course, pack_sha)
            busy = self.busy_reason(course) if auto else ""
            treat_offline = offline or auto
            if completed:
                state = TASK_STATE_COMPLETED
            elif treat_offline:
                state = task_state(
                    pack_exists=pack is not None, stale=bool(stale_reason), held=holder is not None
                )
            else:
                state = TASK_STATE_NOT_OFFLINE
            reason = ""
            if not treat_offline:
                reason = "not_offline"
            elif completed:
                reason = "completed: 本段已跑满（等人停课 / 重导包）"
            elif holder is not None:
                reason = f"held: {holder.get('worker_id') or '?'}"
            elif busy:
                reason = busy
            runs = progress.get(course) or {}
            latest = max(runs.values(), key=lambda r: float(r.get("last_mtime") or 0.0)) if runs else None
            rows.append(
                {
                    "course": course,
                    "state": state,
                    # 可领（判据唯一实现 `auto_claimable`）：离线/自动 ∧ 无主 ∧ 未完成 ∧ 不忙；
                    # **自动课允许无包**（领它触发导包 —— P0-1 的唯一入口）。过期包也可领：
                    # 包旧只意味着起点旧，而「领不领」是云机的判断（它还要比 `served` 的 sha）。
                    "claimable": auto_claimable(
                        auto=auto,
                        offline=offline,
                        pack_exists=pack is not None,
                        holder_present=holder is not None,
                        completed=completed,
                        busy=bool(busy),
                    ),
                    "reason": reason,
                    #: 领它会触发「自动交接」（写 rl-config + 导包；不写意图、不 pin）
                    "auto_handoff": auto,
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
                self.open_time_of(str(r["course"])),
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
        auto = self.auto_eligible(course)
        with self._lease_lock:
            verdict = lease_verdict(now, self._leases.get(course), wid)
            if verdict == "foreign" and not takeover:
                return {}, "foreign"
            if auto:
                busy = self._busy_locked(course)
                if busy:
                    return {}, "busy"
            lease = {
                "token": secrets.token_hex(8),
                "worker_id": wid,
                "at": now,
                "expires_at": now + OFFLINE_LEASE_TTL_SEC,
            }
            self._leases[course] = lease
            pub = self._lease_pub(course, lease)
        # 临界区外落账（盘 IO 不阻塞租约判定）：claimed_by/at + 自动课翻 offline（T2）
        self.note_claim(course, wid)
        reset_auto_handoff_triggers(course)
        return pub, ""

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
            elif str(rec.get("token") or "") != str(lease_token or ""):
                return False, "foreign"
            else:
                del self._leases[course]
        # mode 保持 offline（U3 的 waiting）；只清「谁在跑」的记账
        self.note_release(course)
        return True, ""

    def offline_leases(self) -> dict[str, dict]:
        """有效租约一览（`/admin/offline` 的 `leases` 字段：控制台回答「谁在跑哪门课」）。"""
        out: dict[str, dict] = {}
        for course in list(self._leases):
            info = self.holder_info(course)
            if info is not None:
                out[course] = info
        return out

    # ── 自动离线交接：派发状态读写（T1/T2/T4/T8，plan/auto-offline-handoff，2026-10-03）──

    def _dispatch_path(self, course: str) -> Path:
        """派发状态文件落点：`<课程目录>/offline-dispatch.json`（与账本同域）。"""
        st = self._stores.get(course)
        if st is not None:
            return st.job_root.parent / DISPATCH_NAME
        root = self.traj_root()
        if root is None:
            raise ProtocolError("hub 不知道课程根目录（--traj-root / --discover 未给）")
        return root / course / DISPATCH_NAME

    def dispatch_record(self, course: str) -> dict:
        """读一条派发记录（首次从盘上载入；读不到 ⇒ 缺省形状）。**返回副本**。"""
        with self._dispatch_lock:
            rec = self._dispatch.get(course)
            if rec is None:
                rec = self._dispatch_load(course)
                self._dispatch[course] = rec
            return dict(rec)

    def _dispatch_load(self, course: str) -> dict:
        """从盘上载入（**不抛**：坏文件只是退回缺省——状态文件不该让 hub 起不来）。"""
        fallback = self.mode_of(course) if course in self._stores else COURSE_MODE_ONLINE
        try:
            raw = json.loads(self._dispatch_path(course).read_text(encoding="utf-8"))
        except (OSError, ValueError, ProtocolError):
            raw = None
        return dispatch_record_merge(raw, fallback)

    def _dispatch_update(self, course: str, **fields: Any) -> dict:
        """改一条记录并**原子落盘**（写 `.tmp` 再 `os.replace`；盘 IO 不持任何业务锁）。"""
        with self._dispatch_lock:
            rec = self._dispatch.get(course)
            if rec is None:
                rec = self._dispatch_load(course)
            rec.update(fields)
            rec["v"] = DISPATCH_VERSION
            rec["updated_at"] = float(self._now())
            self._dispatch[course] = rec
            path = self._dispatch_path(course)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, path)
            except OSError as e:
                print(f"[hub-server] 派发状态落盘失败 {course}: {e}", flush=True)
            return dict(rec)

    def dispatch_effective_mode(self, course: str, default: str) -> str:
        """登记课程时的生效模式：**盘上的记录优先**（T1 数据损坏防线）。

        启动参数给的 mode 只是「没有记录时」的缺省 —— 记录里可能有 claim 翻的 offline
        或人的 pin，重启一次回 online 会把正在 TPU 上跑的课解封（§3.8）。
        """
        rec = self.dispatch_record(course)
        mode = str(rec.get("mode") or "").strip().lower()
        return mode if mode in COURSE_MODES else default

    def pinned_of(self, course: str) -> bool:
        """这门课是否由人显式管住（pin 落盘，重启不丢；二轮 P1-3）。"""
        return bool(self.dispatch_record(course).get("pinned"))

    def auto_eligible(self, course: str) -> bool:
        """自动交接候选：在课程表里 ∧ 未被 pin ∧ **开课标记在**（在训）。

        为什么还要看标记：停掉的课会删标记（F17/F18），而它的 dispatch 记录还留在盘上 ——
        只看 pin 会让停掉的课继续被离线盘领走。
        """
        if course not in self._stores or self.pinned_of(course):
            return False
        try:
            return (self.course_dir(course) / COURSE_ENABLE_MARKER).exists()
        except (OSError, ProtocolError):
            return False

    def set_mode_pinned(self, course: str, mode: str, pin: bool | None) -> tuple[bool, str]:
        """模式写入的**人类入口**（`/admin/courses?...&pin=1|0` 走这里；二轮 P0-2）。

        `pin is None` = 非人（legacy 调用）：**拒绝覆盖「由 claim 产生的 offline」**；
        人（显式 pin 字段，1 或 0）可覆盖任何 claim 状态。落地 = `_modes` + 记录（原子写盘）。
        返回 `(ok, why)`。
        """
        if course not in self._stores:
            return False, "未知课程"
        m = str(mode or "").strip().lower()
        if m not in COURSE_MODES:
            return False, "模式非法"
        rec = self.dispatch_record(course)
        if pin is None and rec.get("claimed_offline") and m == COURSE_MODE_ONLINE:
            return False, "该课由 claim 翻成 offline（自动交接中）——要切回在线请带 pin 参数"
        fields: dict[str, Any] = {"mode": m}
        if pin is not None:
            fields["pinned"] = bool(pin)
        if pin is not None or m != COURSE_MODE_OFFLINE:
            # 只有「人写入」或「真正离开 offline」才清 claim 记账：legacy 的 offline 重写
            # 不该顺手把一个正在进行的自动交接“洗白”。
            fields["claimed_offline"] = False
        self._dispatch_update(course, **fields)
        self._modes[course] = m
        self._sync_parked(course)
        return True, ""

    def begin_auto_handoff(self, course: str) -> tuple[str, str]:
        """claim 无包分支的临界区（§3.1a-b + §3.3a）：过 busy 闸 → 翻 mode（落盘）。

        返回 verdict：`flipped`（可触发控制台）/ `busy`（别的课在跑）/ `not_auto`（人管或未知）。
        **不建租约**（包还没出现，领租约还早）；触发控制台是调用方（HTTP 层）的事——网络调用不持锁。
        """
        if course not in self._stores:
            return "not_auto", "未知课程"
        if not self.auto_eligible(course):
            return "not_auto", "该课由人管（pin）/未开课——缺包请走控制台「导出任务包」"
        with self._lease_lock:
            busy = self._busy_locked(course)
            if busy:
                return "busy", busy
        self._modes[course] = COURSE_MODE_OFFLINE
        self._sync_parked(course)
        rec0 = self.dispatch_record(course)
        fields: dict[str, Any] = {
            "mode": COURSE_MODE_OFFLINE,
            "claimed_offline": True,
        }
        # 重复 claim（云机在导包窗口里轮询）不刷新 `flipped_at`：它是停滞告警与 busy 窗口的
        # 锚点，被每次重试推到「刚刚」会让这两条判据永远不触发。
        if not (rec0.get("claimed_offline") and str(rec0.get("mode")) == COURSE_MODE_OFFLINE):
            fields["flipped_at"] = float(self._now())
        self._dispatch_update(course, **fields)
        return "flipped", ""

    def note_claim(self, course: str, worker_id: str) -> None:
        """claim 成功后的派发记账（claimed_by/at + 自动课翻 offline——T2 的同步小事之一）。"""
        fields: dict[str, Any] = {"claimed_by": worker_id, "claimed_at": float(self._now())}
        if self.auto_eligible(course) and self.mode_of(course) != COURSE_MODE_OFFLINE:
            self._modes[course] = COURSE_MODE_OFFLINE
            self._sync_parked(course)
            fields["mode"] = COURSE_MODE_OFFLINE
            fields["claimed_offline"] = True
            fields["flipped_at"] = float(self._now())
        self._dispatch_update(course, **fields)

    def note_release(self, course: str) -> None:
        """release 后的派发记账：持有者清空；mode 保持 offline（U3 的 waiting）。"""
        self._dispatch_update(course, claimed_by="", claimed_at=0.0)

    def note_offline_completed(self, course: str) -> None:
        """段末摘要报到跑满（`end_it_reached`）：记「哪个包已完成」⇒ 不可再领（U6）。

        锚定**包 sha**：人重导包 ⇒ sha 变 ⇒ 自动解封（不引入新状态机；二轮 P1-1）。
        """
        try:
            pack = self.task_pack_path(course)
        except ProtocolError:
            return
        sha = _file_sha256(pack) if pack.is_file() else ""
        self._dispatch_update(course, completed_pack_sha=sha)

    def completion_blocked(self, course: str, pack_sha: str) -> bool:
        """当前包是否已被报到跑满（纯比较；无包/无记录/包换了 ⇒ 不拦）。"""
        if not pack_sha:
            return False
        rec = self.dispatch_record(course)
        return rec.get("completed_pack_sha") == pack_sha

    def _busy_locked(self, course: str) -> str:
        """busy 闸（调用方持 `_lease_lock`）：别的课在跑 / 正在交接 ⇒ 拒因文案（§3.3a 一拖一）。

        两条腿：① 别的课有**活租约**；② 别的课**正在交接**（已翻 offline、包还没出现、
        窗内）——只算租约的话，两台云机会在导包窗口里同时翻开两门课。
        租约过期的课不算（U3 的 waiting 不占闸，否则一台死掉的 TPU 会把所有课冻在离线）。
        """
        now = float(self._now())
        for other, rec in self._leases.items():
            if other == course:
                continue
            if float(rec.get("expires_at", 0.0)) <= now:
                continue  # 惰性过期：过期的租约不算「在跑」
            who = str(rec.get("worker_id") or "?")
            return f"busy: {other} 正在 {who} 上跑（一拖一：它 release 后自动解除）"
        for other in self._order:
            if other == course:
                continue
            live = self._leases.get(other)
            if live is not None and float(live.get("expires_at", 0.0)) > now:
                continue  # 活租约已在上面处理
            rec = self.dispatch_record(other)
            if not rec.get("claimed_offline"):
                continue
            flipped = float(rec.get("flipped_at") or 0.0)
            if flipped <= 0 or now - flipped > AUTO_HANDOFF_PENDING_SEC:
                continue  # 超窗 = 交接失败（告警面兜），不占闸
            try:
                if self.task_pack_path(other).is_file():
                    continue  # 包已出现 = 等云机领取（running/waiting），不占闸
            except ProtocolError:
                continue
            return f"busy: {other} 正在交接（导包中；失败会由停滞告警兜住）"
        return ""

    def busy_reason(self, course: str) -> str:
        """只读版 busy 拒因（清单面用；空串 = 不忙）。"""
        with self._lease_lock:
            return self._busy_locked(course)

    def open_time_of(self, course: str) -> float:
        """开课时间 = `training-enabled.txt` 的 mtime（T4，SSOT）；读不到 ⇒ `+inf` 排最后。"""
        try:
            mtime: float | None = (self.course_dir(course) / COURSE_ENABLE_MARKER).stat().st_mtime
        except (OSError, ProtocolError, KeyError):
            mtime = None
        return open_time_key(mtime)

    def offline_stalled(self) -> list[dict]:
        """停滞清单（§3.9 / T8）：`running` 无进度 / 已翻 offline 无人跑。**只读**。"""
        threshold = float(os.environ.get("BCITY_OFFLINE_STALL_SEC", "") or OFFLINE_STALL_SEC)
        now = float(self._now())
        progress = self.offline_progress()
        out: list[dict] = []
        for course in self.courses():
            rec = self.dispatch_record(course)
            treat_offline = self.mode_of(course) == COURSE_MODE_OFFLINE or self.auto_eligible(course)
            if not treat_offline:
                continue
            pack_sha = ""
            try:
                pack = self.task_pack_path(course)
                if pack.is_file():
                    pack_sha = _file_sha256(pack)
            except ProtocolError:
                continue
            if self.completion_blocked(course, pack_sha):
                continue
            holder = self.holder_info(course)
            runs = progress.get(course) or {}
            last = max((float(r.get("last_mtime") or 0.0) for r in runs.values()), default=0.0)
            why = stall_verdict(
                treat_offline=treat_offline,
                pinned=self.pinned_of(course),
                completed=False,
                holder_present=holder is not None,
                last_progress_mtime=last,
                flipped_at=float(rec.get("flipped_at") or 0.0),
                threshold=threshold,
                now=now,
            )
            if not why:
                continue
            anchor = max(last, float(rec.get("flipped_at") or 0.0))
            out.append(
                {
                    "course": course,
                    "why": why,
                    "holder": holder,
                    "last_progress_mtime": last,
                    "flipped_at": float(rec.get("flipped_at") or 0.0),
                    "age_sec": round(now - anchor, 1) if anchor else 0.0,
                }
            )
        return out



# ── 生成器备注（tmp/gen_queue_offline.py；生成后的人工部分已并入正文）─────
# 这十个方法是 origin `hub/server.py::_HubQueue` 的**逐字**搬移（顺序按调度面原序：
# 清单 → 租约读 → 租约写）。跨域调用的形参面由 `QueuePeer` 声明，本模块只声明自己
# 拥有的那几项状态（`_leases` / `_lease_lock`）。
