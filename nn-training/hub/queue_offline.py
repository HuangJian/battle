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
    AUTHORITY_AUTO,
    AUTHORITY_NOT_OFFLINE,
    AUTHORITY_PINNED_OFFLINE,
    AUTHORITY_PINNED_ONLINE,
    AUTHORITY_STOPPED,
    TASK_STATE_COMPLETED,
    TASK_STATE_NOT_OFFLINE,
    TASK_STATE_RANK,
    _file_sha256,
    hold_expires_in,
    hold_progress_at,
    hold_progress_stale_sec,
    hold_restore_grace,
    hold_state,
    hold_touch_at,
    offline_lease_stale_sec,
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
#: ★ v2（2026-10-07，plan/worker-type-dispatch-model §3-M1a）：另加 `hold`（接管）与
#: `pending_export`（导包软态），**与 v1 的 mode/pinned/claimed_* 字段并存**（双写）——
#: 读侧继续收 v1 形状（`dispatch_record_merge` 宽容），旧读方零变化。
DISPATCH_VERSION = 2
#: 进度打点的**落盘节流**（秒，plan §1.5.2-P0-1）：距上次落盘小于它就只改内存。
#: 打点频率是「轮内每 ≤300s」量级，逐次落盘会把盘 IO 变成热路径；丢掉的那点龄
#: （≤60s）正好被 `HOLD_RESTORE_GRACE_SEC=300` 的恢复宽限吸收。
HOLD_PROGRESS_PERSIST_SEC = 60.0
#: 默认停滞阈值（秒；压「已翻 offline、没人跑」的窗口，§3.9）。env 可覆盖（测试用）。
OFFLINE_STALL_SEC = 1800.0
#: 交接窗口（秒）：claim 已翻 offline、包还没出现 —— 这段时间内别的课也算「机器忙」
#: （§3.3a 一拖一）。超窗 = 交接失败，交给 `offline_stalled` 告警，**不再占闸**
#: （否则一次导出失败会把整条自动链冻死）。
#: ★ 它同时是「新一轮交接」的判据之一（★六轮 F4：换主 ∨ 距上次 claim 超窗 ⇒ 重置触发账本）。
#: ★M1b：它也是 `offline_stalled` 的 `pending-export` 腿的窗（F11：旧 busy 腿② 删掉后，
#: 这个常数就只剩这一个消费点）——「说了要导包、多久没动静算出事」。
AUTO_HANDOFF_PENDING_SEC = 900.0
#: 导包窗的 env 旋钮（e2e/单测调秒级，与 `offline_lease_stale_sec()` /
#: `hold_progress_stale_sec()` 同一套做法：现场与用例都要能把长窗口压到秒级）。
AUTO_HANDOFF_PENDING_ENV = "BCITY_AUTO_HANDOFF_PENDING_SEC"


def auto_handoff_pending_sec() -> float:
    """导包窗（秒；缺省 `AUTO_HANDOFF_PENDING_SEC`，`AUTO_HANDOFF_PENDING_ENV` 可覆盖）。

    非法 / 非正 ⇒ 回缺省（绝不 0：0 窗会让刚写的导包意向当场过期）。
    """
    raw = os.environ.get(AUTO_HANDOFF_PENDING_ENV, "").strip()
    if raw:
        try:
            v = float(raw)
            if v > 0.0:
                return v
        except ValueError:
            pass
    return AUTO_HANDOFF_PENDING_SEC


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
        # ★ v2 两键（M1a 双写）：接管与导包软态。
        "hold": {},
        "pending_export": {},
        "updated_at": 0.0,
    }


def _pos_float(v: object) -> float:
    """宽容的「非负浮点」（**纯函数**）：None / 坏值 ⇒ 0.0。

    状态文件是「重启后能不能继续跑」的唯一依据，一个写错的字段不该让 hub 起不来
    （与 `dispatch_record_merge` 同一条纪律）；先 `str()` 再 `float()` 是为了让 mypy
    看到一个合法形参，而不是在调用点撒 `type: ignore`。
    """
    try:
        return max(0.0, float(str(v)))
    except ValueError:
        return 0.0


def hold_record_merge(raw: object) -> dict:
    """盘上的 `hold` 归一（**纯函数**）：不是 dict / 全空 ⇒ `{}`（= 没有 hold）。

    为什么「全空 ⇒ 没有」：一个坏 dict 若被当成「有人持有」，就是幽灵接管 —— 派发闸与
    本机 held 派生会一起停摆，而这种停摆没有第二个读者会喊。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    out = {
        "worker_id": str(raw.get("worker_id") or ""),
        "token": str(raw.get("token") or ""),
        "at": _pos_float(raw.get("at")),
        "last_progress_at": _pos_float(raw.get("last_progress_at")),
        "touch_at": _pos_float(raw.get("touch_at")),
    }
    if not (out["worker_id"] or out["at"] or out["last_progress_at"] or out["touch_at"]):
        return {}
    return out


def pending_export_record_merge(raw: object) -> dict:
    """盘上的 `pending_export` 归一（**纯函数**）：不是 dict / 全空 ⇒ `{}`。"""
    if not isinstance(raw, dict) or not raw:
        return {}
    out = {"by": str(raw.get("by") or ""), "at": _pos_float(raw.get("at"))}
    if not out["by"] and not out["at"]:
        return {}
    return out


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
    # ★ v2 两键（M1a 双写）：v1 文件里没有它们 ⇒ `{}`（旧形状照读，不制造幽灵接管）。
    rec["hold"] = hold_record_merge(raw.get("hold"))
    rec["pending_export"] = pending_export_record_merge(raw.get("pending_export"))
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
    holder_stale: bool = False,
    holder_revoked: bool = False,
) -> bool:
    """清单的 claimable 判据（**纯函数**；清单与 claim 面同源）。

    · auto 课允许**无包**（领它触发导包 —— P0-1 的唯一入口）；
    · 非自动课（pinned_offline / 冷课）仍是「有包才能领」；
    · 完成态（当前包已跑满）不可再领（二轮 P1-1）；busy 闸对可领课生效（§3.3a；
      ★六轮 F2：消费点 = `is_runnable_offline`，不是 `auto_handoff_allowed`）。
    · ★五轮 P0-A：持有者**失联（stale）或已成墓碑（revoked）**时不算挡领——否则
      「死盘可接管」在清单层就被 `holder_present` 掉死（新形参带默认值，旧调用不受影响）。
    """
    if (holder_present and not (holder_stale or holder_revoked)) or completed or busy:
        return False
    if pack_exists and (offline or auto):
        return True
    return bool(auto and not pack_exists)


def stall_verdict(
    *,
    treat_offline: bool,
    authority: str,
    completed: bool,
    holder_present: bool,
    last_progress_mtime: float,
    flipped_at: float,
    threshold: float,
    now: float,
    pending_export_at: float = 0.0,
    export_window: float = 0.0,
) -> str:
    """停滞判据（**纯函数**）：`""` = 没停；否则是原因。

    覆盖两段（二轮 P1-3）：① running（有人接管）但进度超阈值；② 无人接管、未完成
    （导包窗口 / 导出失败）且超窗。只用已有事实，不引入新状态。

    ★六轮 P0-10（R3-e）：静音**只给 `pinned_online`**（人固定在在线，它不是离线候选）；
    `pinned_offline` 与 auto 的离线课照常告警（报障二里最该响的那一声就是云机停机的
    pinned_offline）。判据吃 `authority` 字符串，不再吃裸 `pinned` 布尔。

    ★M1b：② 的锚换成**导包意向**的时刻（claim 没包那一刻写的 `pending_export.at`），窗换成
    `export_window`（= `AUTO_HANDOFF_PENDING_SEC`，与 ① 的停滞阈**是两个常量**）——
    「已翻 offline」这件事在新模型里有了它自己的时间戳，不再拿 mode 翻转时刻凑。
    """
    if not treat_offline or authority == AUTHORITY_PINNED_ONLINE or completed:
        return ""
    progress_age = now - last_progress_mtime if last_progress_mtime > 0 else math.inf
    if holder_present:
        return "running-stale" if progress_age > threshold else ""
    anchor = max(
        flipped_at,
        last_progress_mtime if last_progress_mtime > 0 else 0.0,
        pending_export_at,
    )
    window = export_window if export_window > 0.0 else threshold
    if anchor > 0 and now - anchor > window:
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
        """清单的**候选面**（评审 S-1）：课程表里**开课标记在**的 ∪ 盘上有开课标记的冷课。

        为什么不只认课程表：课程表是「1 小时新鲜度扫描」的产物，而**离线课本机不训练**
        ⇒ 课冷掉 / hub 重启之后它从表里消失，而包还在盘上——只认表会让云机问清单时得到
        「没有任务」（明明有一份包在等它领）。判据与 404 自愈门同源（`_task_pack_miss_candidate`）。

        ★六轮 F3（行为变更）：**停课（标记不在）不再列**——包括盘上还留着 `mode=offline`
        记录的停课残留（此前 `mode_of==offline` 就会列，而生产 `stopCourse` 推的正是
        `mode=offline` ⇒「唯一 opt-out = 停课」在最常见路径上破防）。`pinned_online` 行
        **照发**（claimable=false / reason=pinned，云机「空队列自解释」不该少这一档）。
        """
        out = [c for c in self._order if self._marker_exists(c)]
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

    def offline_tasks(self, *, include_all: bool = False, worker: str = "") -> list[dict]:
        """`GET /offline/tasks` 的内容——**零副作用**（不触发重导、不写账本、不动游标）。

        每行字段定死在 §3.1：`course/state/claimable/pack/run_id/it/end_it/stale_reason/
        holder/progress`；`seize`/`open_time` 是 2026-10-03 用户裁决加的两个（见行内注释）。
        ★M1b：另加 `hold`（接管镜像：worker/state/expires_in）· `pending_export`（导包软态，
        **不占闸**）· `busy`（一拖一按 worker 的布尔结果）；`busy`/`claimable` 按 `?worker=` 判
        （缺省 ⇒ 上界：不含一拖一，plan §69）。`state` 的 `held` 只算 **live** 的接管
        （stale/revoked 的镜子 ⇒ 不算有主；见下方行内定案）。
        读不到就如实给空值（一个坏包不该把整张清单变成 500）。
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
            authority = self.authority_of(course)
            runnable = authority in (AUTHORITY_PINNED_OFFLINE, AUTHORITY_AUTO)
            # 在表：`offline` = 当前 mode 是离线（U3 waiting / pinned_offline / claim 翻的）；
            # 冷课（评审 S-1 + ★六轮 F6）：无记录 / pinned_offline ⇒ 按离线算；带 online
            # 记录的 ⇒ 不可领（`not_offline`）。
            offline = (
                self.mode_of(course) == COURSE_MODE_OFFLINE
                if real
                else authority == AUTHORITY_PINNED_OFFLINE
            )
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
            # ★六轮 F2 映射表：清单 `auto_handoff` = 自动导包能力（= `auto_handoff_allowed`）；
            # `busy`/`claimable`/`seize` 用「可领」闸（= `is_runnable_offline`）——pinned_offline
            # 照样吃「一拖一」（只按 auto 门控会静默漏掉它）。★ 2026-10-03 用户裁决后 pin 在线
            # 不再可被抢（§4.2 半回摆：pin online 重新获得阻止力）；在线在训的 auto 课仍可被抢占。
            auto = authority == AUTHORITY_AUTO
            pack_sha = str((pack or {}).get("sha256") or "")
            completed = self.completion_blocked(course, pack_sha)
            busy = self.busy_reason(course, worker) if runnable else ""
            hold = self.hold_of(course)
            pending_export = self.pending_export_of(course)
            holder_stale = bool(holder and holder.get("stale"))
            holder_revoked = bool(holder and holder.get("revoked"))
            #: 可被**抢占**（用户 2026-10-03 裁决「没有离线课程就抢第一个在训在线课」）：
            #: 表内在训的 auto 课 ∧ 现在还是在线 ∧ 未跑满（跑满的不能再抢——二轮 P1-1 的同一理由；
            #: busy/无主不在这里滤——云机抢到 busy 会走 409 `busy` 等下一拍，不吃 idle 预算）。
            seize = real and runnable and not offline and auto and not completed
            treat_offline = runnable
            #: ★M1b 定案：`state` 的 `held` 只算**活着的接管**（`live`）——stale / revoked 的
            #: 镜子不再算「有主」。理由：`state` 回答的是「现在能不能有人接手」，而那两个
            #: 情况 `claimable=true`；`claimed ∧ claimable` 对任何读方都是自相矛盾。
            #: 谁在跑仍然看得见：`holder` 字段照给，`reason` 前缀明说 `held-stale:` / `held-revoked:`。
            #: （不用 `stale` 表示「主失联」：那个词在本清单里已经是**包旧**的意思，别叠加第二义。）
            holder_live = bool(holder is not None and not holder_stale and not holder_revoked)
            if completed:
                state = TASK_STATE_COMPLETED
            elif treat_offline:
                state = task_state(
                    pack_exists=pack is not None,
                    stale=bool(stale_reason),
                    held=holder_live,
                )
            else:
                state = TASK_STATE_NOT_OFFLINE
            reason = ""
            if authority == AUTHORITY_PINNED_ONLINE:
                reason = "pinned: 人固定在在线（交还自动后可领）"
            elif authority == AUTHORITY_STOPPED:
                reason = "not_offline: 这门课不在训练中（开课标记已删）"
            elif not runnable:
                reason = "not_offline"
            elif completed:
                reason = "completed: 本段已跑满（等人停课 / 重导包）"
            elif holder_revoked:
                reason = (
                    f"held-revoked: {(holder or {}).get('worker_id') or '?'}"
                    "（已被撤销，可直接接管）"
                )
            elif holder is not None:
                if holder_stale:
                    # ★M1b：文案跟判据走（活性 = **进度**静默）；旧记录（没有进度字段）
                    # 退回心跳龄 —— `progress_ago < 0` 就是那一档。
                    # 哨兵是 `-1.0`，别用 `or`（真值 0.0 会被吃成缺失）
                    raw_ago = holder.get("progress_ago")
                    ago = float(raw_ago) if raw_ago is not None else -1.0
                    what = "进度" if ago >= 0 else "心跳"
                    reason = (
                        f"held-stale: {holder.get('worker_id') or '?'}"
                        f"（{what}静默 {max(0.0, ago):.0f}s，可直接接管）"
                    )
                else:
                    reason = f"held: {holder.get('worker_id') or '?'}"
            elif busy:
                reason = busy
            runs = progress.get(course) or {}
            latest = max(runs.values(), key=lambda r: float(r.get("last_mtime") or 0.0)) if runs else None
            rows.append(
                {
                    "course": course,
                    "state": state,
                    #: 权威三态（§3.1；控制台与云机都可据它排障）——`pinned_online` 行照样
                    #: 列出，只是 claimable=false（★六轮小项 5）。
                    "authority": authority,
                    # 可领（判据唯一实现 `auto_claimable`）：可领闸 ∧ 无主（或主已失联/撤销）
                    # ∧ 未完成 ∧ 不忙；**自动课允许无包**（领它触发导包 —— P0-1 的唯一入口）。
                    # 过期包也可领：包旧只意味着起点旧，而「领不领」是云机的判断（它还要比
                    # `served` 的 sha）。★六轮：停课 / pinned_online / 冷课 {online,!pin} 一律 false。
                    "claimable": bool(runnable)
                    and auto_claimable(
                        auto=auto,
                        offline=offline,
                        pack_exists=pack is not None,
                        holder_present=holder is not None,
                        completed=completed,
                        busy=bool(busy),
                        holder_stale=holder_stale,
                        holder_revoked=holder_revoked,
                    ),
                    "reason": reason,
                    #: ★M1b：**一拖一按 worker 判**的结果直接透出来（`bool(busy)`）。
                    #: 为什么不只靠 `reason` 文案：云机选下一门时只做布尔判断（不解析中文），
                    #: 而 `claimable` 把「忙」与「被别人带 held」两个原因合成了一个 false——
                    #: 分开才能让「本拍不跑换下一门」与「等它 release」两条建议各归各位。
                    "busy": bool(busy),
                    #: 领它会触发「自动交接」（写 rl-config + 导包；不写意图、不 pin）
                    "auto_handoff": auto,
                    #: 在线在训 ⇒ 离线盘可**抢占**它（hub 的 claim 会翻离线；用户 2026-10-03 裁决）。
                    #: 云机选抢的顺序 = `open_time` 升序（tie 用课名）取最小的一门。
                    "seize": bool(seize),
                    #: 开课时间（`training-enabled.txt` mtime，T4 的 SSOT）；读不到 ⇒ +inf 排最后。
                    "open_time": self.open_time_of(course),
                    "pack": pack,
                    "run_id": meta["run_id"],
                    "it": meta["it"],
                    "end_it": meta["end_it"],
                    "stale_reason": stale_reason,
                    "holder": holder,
                    #: ★M1b：接管镜像（`{}` = 无 hold）+ 导包软态（超窗读面即空）。
                    "hold": hold,
                    "pending_export": pending_export,
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
        """有效租约（**惰性过期**：读时就地清掉 ⇒ 读面与领取面同一条判据）。

        墓碑（`revoked=True`）**照返**（不是过期）：它的消费面见 `holder_info`（★六轮 F5）。
        """
        with self._lease_lock:
            return self._lease_rec_locked(course)

    def _lease_rec_locked(self, course: str) -> dict | None:
        """`_lease_rec` 的**无锁内核**（调用方持 `_lease_lock`，否则状态读到半截）。

        ★M1b 由来：`holder_info` 会被 `_busy_locked` 在 `_lease_lock` 临界区里调（busy 闸按
        worker 判 ⇒ 要看「别的课上是谁在跑」）——`threading.Lock` 不可重入，内核必须拆出来，
        否则同一个盘自己就把自己锁死（表现为 claim / 清单请求挂死到超时）。
        """
        now = float(self._now())
        rec = self._leases.get(course)
        if rec is not None and float(rec.get("expires_at", 0.0)) <= now:
            del self._leases[course]
            return None
        return dict(rec) if rec else None

    def offline_lease(self, course: str) -> dict | None:
        """某门课的**有效**租约（对外只读面：清单 / 拒因 / 控制台都用它）。"""
        return self._lease_rec(course)

    def holder_info(self, course: str) -> dict | None:
        """持有人信息（`worker_id` / 龄 / 到期 / 静默 / stale / revoked / progress_ago）——持有面共用一份。

        ★M1b（plan §1.2-5「三处同源」）：**判据源 = hold 镜像**（落盘、重启不丢；`hold_state`
        只认进度）；`_leases` 只补「心跳龄 / TTL 余量」这些 wire 记账。`stale` 从「心跳静默
        超阈」改成 **进度静默超阈**（与 `lease_verdict` 同一把尺子，`last_progress_at`）；
        心跳龄留在 `silent_sec`（现在只服务四象限排障，不再决定活性）。

        ★六轮 F5（墓碑 holder 形状定死）：**墓碑也返回**（`revoked=True` + 字段齐全）——
        清单才能渲染 `held-revoked`、`auto_claimable` 的 `holder_revoked` 才是真消费；
        墓碑本身住内存租约（进程内），与「hold 落盘」这两层不冲突（★M1b：revoke 会清 hold）。
        读面自己取 `_lease_lock`（锁序 `_lease_lock → _dispatch_lock`）；临界区内的调用方
        （`_busy_locked`）走 `_holder_info_locked`。
        """
        with self._lease_lock:
            return self._holder_info_locked(course)

    def _holder_info_locked(self, course: str) -> dict | None:
        """`holder_info` 的**无锁内核**（调用方持 `_lease_lock`）——busy 闸 / 清单共用。"""
        hold = self.hold_of(course)
        rec = self._lease_rec_locked(course)
        if not hold and not rec:
            return None
        now = float(self._now())
        # ★M1b：缺失哨兵是 `-1.0`（≥0 才是真值）——`or` 链会把「真的 0 点」当成没有。
        progress = hold_progress_at(hold)
        if progress < 0.0:
            progress = hold_progress_at(rec)
        touch = hold_touch_at(hold)
        if touch < 0.0:
            touch = hold_touch_at(rec)
        started = float((hold or {}).get("at") or (rec or {}).get("at") or 0.0)
        beat_at = float((rec or {}).get("beat_at") or 0.0)
        path_ago = round(max(0.0, now - progress), 1) if progress >= 0.0 else -1.0
        if progress >= 0.0:
            stale = path_ago > hold_progress_stale_sec()
        else:  # 旧记录（没有进度字段）⇒ 退回心跳静默（过渡读路，M1c 删）
            stale = bool(beat_at > 0 and (now - beat_at) > offline_lease_stale_sec())
        left_ttl = (touch + OFFLINE_LEASE_TTL_SEC - now) if touch >= 0.0 else 0.0
        left_progress = (progress + hold_progress_stale_sec() - now) if progress >= 0.0 else 0.0
        return {
            "worker_id": str((hold or {}).get("worker_id") or (rec or {}).get("worker_id") or ""),
            "age_sec": round(max(0.0, now - started), 1) if started > 0 else 0.0,
            "expires_in": round(max(0.0, min(left_ttl, left_progress)), 1),
            "beat_at": round(beat_at, 1),
            "silent_sec": round(max(0.0, now - beat_at), 1) if beat_at > 0 else 0.0,
            "progress_ago": path_ago,
            "stale": bool(stale),
            "revoked": bool((rec or {}).get("revoked")),
        }

    def claim_offline(
        self, course: str, worker_id: str, *, takeover: bool = False
    ) -> tuple[dict, str]:
        """领一门课的离线租约 → `(租约, "")`；领不到 → `({}, 拒因)`。

        拒因分流（HTTP 层映射成 409）：`pinned_online` / `not_offline`（停课、冷课 online 记录）/
        `busy` / `foreign` / `bad`。

        · `mine`（同一个 `worker_id` 回来）与 `expired` 直接续上：cell 中断后重跑不该被
          **自己留下**的租约挡住（评审 G1）；`revoked`（墓碑）＝新主直接覆盖；
        · `stale`（别人静默超阈）⇒ **自动接管**，不再需要 `takeover=1`（plan §3.3）；
        · `takeover=True` 保留为人工兜底（控制台/搬机）。
        """
        wid = str(worker_id or "").strip()
        if not wid:
            return {}, "bad"
        authority = self.authority_of(course)
        if authority == AUTHORITY_PINNED_ONLINE:
            return {}, AUTHORITY_PINNED_ONLINE
        if authority in (AUTHORITY_STOPPED, AUTHORITY_NOT_OFFLINE):
            return {}, AUTHORITY_NOT_OFFLINE
        now = float(self._now())
        reclaimed_from = ""
        # 锁序契约：`_lease_lock → _dispatch_lock`（hold 镜像住派发记录）。`_leases` 只剩
        # 「token / TTL / 心跳」这些 wire 记账，**判据全部读 hold**（重启后仍然算数）。
        with self._lease_lock:
            hold = self.hold_of(course)
            state = str(hold.get("state") or "")
            holder = str(hold.get("worker_id") or "")
            # `mine` **不看活性**：同一个 worker 回来续领自己（哪怕它已经静默超阈）——
            # 与 `lease_verdict` 的 `mine` 排在 `stale` 之前同一条口径（cell 中断重跑要用）。
            mine = bool(hold) and holder == wid
            if state == "live" and not mine:
                # ★不变量 3（plan §1.2-③）：live 的 hold **不可被顶**——`takeover=1` 也不行
                #（要清先 revoke）。否则「不能覆盖 live」就是句空话。
                return {}, "foreign"
            if hold and not mine:
                reclaimed_from = holder  # 别人 stale ⇒ 自动接管（不必 takeover=1）
            # ★六轮 F2 + ★M1b/D3：busy 门用「可领」覆盖面（到这里 authority 已是
            # pinned_offline 或 auto）且**按 worker 判**（同一个盘串行自己，别的盘不互挡）。
            busy = self._busy_locked(course, wid)
            if busy:
                return {}, "busy"
            lease = {
                "token": secrets.token_hex(8),
                "worker_id": wid,
                "at": now,
                "expires_at": now + OFFLINE_LEASE_TTL_SEC,
                "beat_at": now,
                "touch_at": now,
                #: 接管即第一个进度锚（否则新 hold 会因「零进度」当场被判 stale）。
                "last_progress_at": now,
                "revoked": False,
            }
            self._leases[course] = lease
            pub = self._lease_pub(course, lease)
            if reclaimed_from:
                pub["reclaimed"] = True
                pub["reclaimed_from"] = reclaimed_from
        if reclaimed_from:
            print(
                f"[hub-server] offline-hold-reclaim {course}: {reclaimed_from} 进度静默超阈，"
                f"{wid} 自动接管（旧主心跳将拿 409 taken）",
                flush=True,
            )
        # 临界区外落账（盘 IO 不阻塞租约判定）：hold 镜像 + claimed_by/at + 自动课翻 offline
        self.note_hold(course, worker_id=wid, token=str(lease.get("token") or ""))
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
        """续租 → `({"ttl_sec","expires_at"}, "")`；已过期 → `"expired"`；被撤销 → `"revoked"`；被接管 → `"taken"`。

        顺序（plan §3.3 表）：过期 → `revoked`（**不分 token/身份**：老主拿 409 并在轮边界
        收尾）→ token 比对（不符 ⇒ `taken`）→ 续租。
        ★六轮小项 4：续租**同时刷新 `beat_at`**（stale 判据读它；只续 `expires_at` 会把持续
        心跳的活主判成 stale 而被别人接管）。
        """
        now = float(self._now())
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is None or float(rec.get("expires_at", 0.0)) <= now:
                self._leases.pop(course, None)
                return {}, "expired"
            if rec.get("revoked") is True:
                return {}, "revoked"
            if str(rec.get("token") or "") != str(lease_token or ""):
                return {}, "taken"
            rec["expires_at"] = now + OFFLINE_LEASE_TTL_SEC
            # ★F2/P2-1：心跳只续 TTL 与「最近接触」——**进度字段不动**（心跳不作活性）。
            rec["beat_at"] = now
            rec["touch_at"] = now
            return {"ttl_sec": OFFLINE_LEASE_TTL_SEC, "expires_at": rec["expires_at"]}, ""

    def progress_offline(self, course: str, lease_token: str) -> tuple[bool, str]:
        """进度打点（`POST /offline/progress`；plan §1.1 的第三种进度信号）。

        与 `heartbeat_offline` 同一套 token 分流（过期 / revoked / taken），差别只在效果：
        它同时刷 **`last_progress_at`**（活性）+ `touch_at`（TTL）——即「合法接触都刷 TTL，
        只有进度刷 last_progress_at」中的那一个；并把镜像写进派发记录（落盘节流 ≥60s）。
        没有租约的课 ⇒ `("", "expired")`**不建 hold**（hold 只由 claim 建，Q1）。
        """
        now = float(self._now())
        token_now = ""
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is None or float(rec.get("expires_at", 0.0)) <= now:
                self._leases.pop(course, None)
                return False, "expired"
            if rec.get("revoked") is True:
                return False, "revoked"
            token_now = str(rec.get("token") or "")
            if token_now != str(lease_token or ""):
                return False, "taken"
            rec["expires_at"] = now + OFFLINE_LEASE_TTL_SEC
            rec["touch_at"] = now
            rec["last_progress_at"] = now
        # 镜像：内存每拍，落盘节流（`note_progress` 自带 token 门，换主后旧主的 ping 不生效）。
        self.note_progress(course, token=token_now)
        return True, ""

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
        # mode 保持 offline（U3 的 waiting）；只清「谁在跑」的记账 —— ★M1b：**hold 一并清**
        # （这是「正常交还」那条腿，清完协作派发当天就恢复）。
        self.note_release(course)
        return True, ""

    def revoke_offline_lease(self, course: str, reason: str = "") -> bool:
        """给离线租约立墓碑（`revoked=True`，保留 token/beat_at）：下一次成功 claim 覆盖它。

        唯一调用点 = `set_mode_pinned` 的在线分支（人切在线/交还自动，§3.2）与 `note_claim`
        的竞态收尾（§3.3）——两者都不持 `_lease_lock`（锁序：`_lease_lock → _dispatch_lock`
        是既有嵌套方向，本函数不许在持 `_dispatch_lock` 时调）。写 `_leases` ⇒ 已录进
        `STATE_WRITERS['_leases']`（守卫）。
        """
        token = ""
        with self._lease_lock:
            rec = self._leases.get(course)
            if rec is not None and rec.get("revoked") is not True:
                rec["revoked"] = True
                rec["revoked_reason"] = str(reason or "")
                # 显式回写：状态写者表（守卫）的 AST 扫描只认 `self.X[...] =` / `.pop` / `.del`
                # 三类写法，就地改引用不会被计入——回写一份才让「谁写 _leases」这张表不漏人。
                self._leases[course] = rec
                token = str(rec.get("token") or "")
        # ★M1b：墓碑同时（且**无条件**）清掉 hold 镜像 —— 派发闸只认 hold，不清就等于
        # 「墓碑仍占闸」。为什么无条件（而不是「有租约才清」）：hold 是**落盘的**，而
        # `_leases` 是进程内的——hub 重启后 `offline` 的 `release_hold=1`（人点「强制解除
        # 接管」）会命中「没有内存租约但盘上有 hold」，那时不清就等于按钮坏了。
        #（墓碑本身住内存租约，`holder_info`/`offline_leases` 照样看得到，供排障。）
        held = bool(self.hold_of(course))
        if held or rec is not None:
            self._dispatch_update(course, hold={})
            self._sync_hold(course)
        had = rec is not None or held
        if had:
            print(
                f"[hub-server] offline-revoke {course} lease={token[:8]}… reason={reason or '-'}",
                flush=True,
            )
        return had

    def offline_leases(self) -> dict[str, dict]:
        """租约一览（`/admin/offline` 的 `leases` 字段：控制台回答「谁在跑哪门课」）。

        ★六轮 F5：**保留 revoked 墓碑条目**（带 `revoked/beat_at/silent_sec/stale` 全字段，
        便于排障）；不做静默删除。
        """
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
        """从盘上载入（**不抛**：坏文件只是退回缺省——状态文件不该让 hub 起不来）。

        ★ 2026-10-07（M1a / plan §1.5.4-P2-2）：盘上的 hold 过一道**恢复宽限** ——
        `last_progress_at` / `touch_at` 抬到 `now - HOLD_RESTORE_GRACE_SEC`，并打一行
        `hold-restored`。理由：hub 重启时盘上的时间是旧的，而 worker 大概率还在跑
        （它不知道自己「被重启」了）—— 不抬就会把活着的盘当场判掉线，协作派发与本机
        held 会一起解开。
        """
        fallback = self.mode_of(course) if course in self._stores else COURSE_MODE_ONLINE
        try:
            raw = json.loads(self._dispatch_path(course).read_text(encoding="utf-8"))
        except (OSError, ValueError, ProtocolError):
            raw = None
        rec = dispatch_record_merge(raw, fallback)
        hold = rec.get("hold") or {}
        if not hold:
            return rec
        now = float(self._now())
        raw_progress = hold_progress_at(hold)
        raw_touch = hold_touch_at(hold)
        progress = hold_restore_grace(now, raw_progress)
        touch = hold_restore_grace(now, raw_touch)
        if progress > raw_progress or touch > raw_touch:
            hold = dict(hold)
            hold["last_progress_at"] = progress
            hold["touch_at"] = touch
            rec["hold"] = hold
        print(
            f"[hub-server] hold-restored {course}: worker={hold.get('worker_id') or '?'} "
            f"progress={max(0.0, now - progress):.0f}s 前",
            flush=True,
        )
        return rec

    def _dispatch_update(
        self, course: str, *, guard_hold_token: str = "", **fields: Any
    ) -> dict:
        """改一条记录并**原子落盘**（写 `.tmp` 再 `os.replace`；盘 IO 不持任何业务锁）。

        `guard_hold_token`：只在「盘上那份 hold 的 token 仍是它」时才写。
        为什么要有这道门：打点与换主会并发（旧主还在 ping，新主刚 claim 完）——
        不带门的写会把**旧主的 hold 复活**（新主的独占被一道过期的 ping 解除，而没有任何
        读者会喊）。旧主的 ping 本就不该续新主的命。
        """
        with self._dispatch_lock:
            rec = self._dispatch.get(course)
            if rec is None:
                rec = self._dispatch_load(course)
            if guard_hold_token:
                cur = rec.get("hold") or {}
                if str(cur.get("token") or "") != str(guard_hold_token):
                    return dict(rec)  # 换主了：本次写整笔作废（不复活旧 hold）
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

    # ── 接管（hold）写入面与读数（★ M1a 双写，2026-10-07；plan §3-M1a）─────────
    #
    # M1a 只**加**：判据在 `hub/task_pack.py`（叶子），落盘在 `offline-dispatch.json` v2，
    # 消费点切换在 M1b —— 所以这一刀不改任何既有派发行为。

    def note_hold(self, course: str, *, worker_id: str, token: str) -> dict:
        """建立 / 覆盖接管（hold）。**M1a 只落新键**：旧 mode/pinned/claimed_* 一字不动。

        `last_progress_at` / `touch_at` 都以「接管时刻」打底（claim 本身就是第一个合法接触）
        —— 否则一个刚建立的 hold 会因为「零进度」立刻被判 stale。hold 建立 = 包到手（Q1），
        所以顺手清掉 `pending_export` 那个导包软态。
        """
        now = float(self._now())
        hold = {
            "worker_id": str(worker_id),
            "token": str(token),
            "at": now,
            "last_progress_at": now,
            "touch_at": now,
        }
        rec = self._dispatch_update(course, hold=hold, pending_export={})
        self._sync_hold(course)  # ★M1b：派发闸的第三层跟着换输入
        return rec

    def note_progress(self, course: str, *, token: str = "") -> bool:
        """进度打点（轮内完成事件 → 这里）：**内存每拍更新，落盘节流 ≥60s**。

        返回「本次打点是否生效」（落盘 且**令牌仍是当前主**）。三条纪律：

        * 没有 hold 的课 ⇒ 无操作（False）—— **打点不建 hold**（hold 只由 claim 建，Q1）；
        * 给了 `token` 而它不对 ⇒ 无操作（旧主的 ping 不续新主的命；换主的竞态见
          `_dispatch_update(guard_hold_token=…)`）；
        * 节流基准只认**盘上那份**的 `updated_at` —— 在内存里把它刷成现在会把自己永远按住。
        """
        now = float(self._now())
        with self._dispatch_lock:
            rec = self._dispatch.get(course)
            if rec is None:
                rec = self._dispatch_load(course)
            hold = rec.get("hold") or {}
            if not hold:
                return False
            token_now = str(hold.get("token") or "")
            if token and token_now != str(token):
                return False  # 换主了：这一拍属于旧主
            persisted = float(rec.get("updated_at") or 0.0)
            hold = dict(hold)
            hold["last_progress_at"] = now
            hold["touch_at"] = now
            rec["hold"] = hold
            rec["v"] = DISPATCH_VERSION
            self._dispatch[course] = rec
            throttled = persisted > 0.0 and (now - persisted) < HOLD_PROGRESS_PERSIST_SEC
        if throttled:
            # 内存里已推进（闸那侧看的也是这个 now），但**盘上没动** ⇒ 镜像要推，否则
            # 闸读到的是上一次落盘时的旧进度（节流窗内每拍都推一次，代价是一次 dict 赋值）。
            self._sync_hold(course)
            return False
        wrote = self._dispatch_update(course, guard_hold_token=token_now, hold=hold)
        self._sync_hold(course)
        return str((wrote.get("hold") or {}).get("token") or "") == token_now

    def note_pending_export(self, course: str, *, by: str) -> dict:
        """导包软态（Q1）：**不建 hold、不占任何闸、不停本机** —— 只是「有人在导包」的提示。

        两条纪律（★M1b）：

        * `at` **首写为准**（同一位导包人重复 claim/轮询不刷新）——它是 `offline_stalled`
          的 `pending-export` 锚点，被每次轮询推到「刚刚」= 告警永远不响。与 `flipped_at`
          同一条理由（那里的注释也写了「两个锚点时间轴刻意分叉」）。
          换主（不同 worker）或超窗 ⇒ 那是**新一轮**导包，重新计时。
        * 窗用 `auto_handoff_pending_sec()`（与读面 `pending_export_of` 同一把尺子）。
        """
        now = float(self._now())
        prev = self.dispatch_record(course).get("pending_export") or {}
        prev_by = str(prev.get("by") or "")
        prev_at = float(prev.get("at") or 0.0)
        same_round = (
            prev_by == str(by) and prev_at > 0.0 and now - prev_at <= auto_handoff_pending_sec()
        )
        if same_round:
            return self.dispatch_record(course)
        return self._dispatch_update(course, pending_export={"by": str(by), "at": now})

    def hold_of(self, course: str) -> dict:
        """接管读数（`{}` = 没有 hold）。清单 / 派发闸 / 本机 held 派生**同源**读它。

        形状（P2-3）：`worker_id/token/at/last_progress_at/touch_at` + `state`（live/stale）
        + `expires_in`（距判掉线的剩余秒 = min(进度余量, TTL 余量)）。
        """
        now = float(self._now())
        hold = self.dispatch_record(course).get("hold") or {}
        if not hold:
            return {}
        out = dict(hold)
        out["state"] = hold_state(now, hold)
        out["expires_in"] = hold_expires_in(
            now, hold, ttl_sec=OFFLINE_LEASE_TTL_SEC, stale_sec=hold_progress_stale_sec()
        )
        return out

    def offline_advance_ok(self, course: str, lease_token: str = "") -> tuple[bool, str]:
        """补传的**这一份权重能不能推进活动起点**（★P1-1：盖章无条件，advance 要活+持准）。

        四态（为什么是这个形状，而不是简单一句「check hold 是否存在」）：

        · **`pinned_online`** ⇒ 拒（★P1-7 / R3-f 的既有腿，逐字保留）：人切了「固定在线」
          之后，旧云机跑完的那几轮不是「课程现在的进度」（把人切在线的起点拉回旧轮是报障
          一的另一半）。权威退役（M1c/M4）时这条一并删。
        · **盘上无 hold** ⇒ 准。这条腿里「没有 hold」= 手动送包 / 旧端 / 协作回传——旧行为
          逐字保留（与 `_end_seal_ok` 的兜底哲学同一条：不制造新的失败态）。
        · **hold 是 stale** ⇒ 拒。这正是要拦的：旧主的迟到回传把活动起点夺回来（F2 的
          「心跳活、进度死」在这一点上与 `lease_verdict` 同一把尺子）。
        · **hold 活但 token 不符** ⇒ 拒（别的盘 / 手写 curl 顶着别人的名头推进起点）。

        拒都只拒 **advance**：镜像 / 归档 / 账本照落（它们是「算过什么」的证据）。
        """
        if self.authority_of(course) == AUTHORITY_PINNED_ONLINE:
            return False, AUTHORITY_PINNED_ONLINE
        raw = self.dispatch_record(course).get("hold") or {}
        if not raw:
            return True, ""
        hold = self.hold_of(course)
        if str(hold.get("state") or "") != "live":
            return False, "hold_stale"
        if not lease_token or lease_token != str(raw.get("token") or ""):
            return False, "hold_token"
        return True, ""

    def pending_export_of(self, course: str) -> dict:
        """导包软态的读数：**超窗即视为没有**（惰性过期、不写盘清理）。

        ★ 评审 F8：崩溃 / 换机的导包者会留下一条永久记录 —— 读面不设窗的话就是永久
        「有人在导包」，而 `offline_stalled` 会对着一个早已不存在的导包报「停滞」。
        窗 = `AUTO_HANDOFF_PENDING_SEC`（导包窗口，与接管活性那个 900 是**两个**常量）。
        """
        rec = self.dispatch_record(course).get("pending_export") or {}
        if not rec:
            return {}
        at = float(rec.get("at") or 0.0)
        if at <= 0.0 or float(self._now()) - at > auto_handoff_pending_sec():
            return {}
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

    def authority_of(self, course: str) -> str:
        """**唯一派生函数**（plan §3.1）：这门课现在归谁。写死，不猜。

        | 值 | 条件 | 含义 |
        |---|---|---|
        | `pinned_online` | 标记在 ∧ `pinned ∧ mode=online`（含冷课同形状记录） | 人固定在线：离线盘不可 claim/seize/翻模式 |
        | `pinned_offline` | 标记在 ∧ `pinned ∧ mode=offline`，**或冷课无记录** | 人指定离线 / 历史冷课按离线 |
        | `auto` | 标记在 ∧ 在表 ∧ `!pinned` | 自动池：claim 可翻模式；开课未选模式 = 这一档 |
        | `not_offline` | 标记在 ∧ 冷课 `{online, !pin}` 记录 | 不在离线池（按记录 mode 派生，不开导包能力） |
        | `stopped` | 开课标记不在（正交维，优先级最高） | 唯一 opt-out：claim 拒、默认清单不列 |

        派生自既有事实（`dispatch_record.pinned` + `mode_of` + 开课标记），不新增字段；
        冷课读盘失败（单课程 `--job-root` 无 traj_root ⇒ ProtocolError）⇒ 退回缺省
        （无记录 ⇒ `pinned_offline`），不把 500 带进清单（★六轮小项 7）。
        """
        if not self._marker_exists(course):
            return AUTHORITY_STOPPED
        if course in self._stores:
            rec = self.dispatch_record(course)
            if rec.get("pinned"):
                return (
                    AUTHORITY_PINNED_ONLINE
                    if str(rec.get("mode")) == COURSE_MODE_ONLINE
                    else AUTHORITY_PINNED_OFFLINE
                )
            return AUTHORITY_AUTO
        # 冷课（∉ `_stores`）：盘上记录优先；读不到 ⇒ `pinned_offline`（历史兼容）。
        disk_rec = self._dispatch_disk_record(course)
        if disk_rec is None:
            return AUTHORITY_PINNED_OFFLINE
        pinned = bool(disk_rec.get("pinned"))
        mode = str(disk_rec.get("mode") or "")
        if pinned and mode == COURSE_MODE_ONLINE:
            return AUTHORITY_PINNED_ONLINE
        if pinned and mode == COURSE_MODE_OFFLINE:
            return AUTHORITY_PINNED_OFFLINE
        return (
            AUTHORITY_PINNED_OFFLINE
            if mode == COURSE_MODE_OFFLINE
            else AUTHORITY_NOT_OFFLINE
        )

    def auto_handoff_allowed(self, course: str) -> bool:
        """能不能由离线盘**入口自动交接**（翻 mode + 触发导包）—— `auto` 档专属。

        这是老 `auto_eligible` 的语义收窄：加了 `!pinned` 与「在表」两条硬条件。
        **消费点**（§3.1 映射表，别随手换成别的）：清单 `auto_handoff` 字段 / `seize` /
        `note_claim` 翻模式 / `begin_auto_handoff` / `_claim_without_pack` 入口 /
        `_ask_console_freshness`。
        """
        return self.authority_of(course) == AUTHORITY_AUTO

    def is_runnable_offline(self, course: str) -> bool:
        """离线盘现在允许领它吗（claim 门 / 清单 `busy` / `claimable` 的上位闸）。

        值域 = `{pinned_offline, auto}`：停课不领、`pinned_online` 不领、冷课 `{online,!pin}`
        不领。★六轮 F2：busy 闸的两处消费点必须用它（不是 `auto_handoff_allowed`），
        否则 pinned_offline 的 claim 会静默跳过「一拖一」。
        """
        return self.authority_of(course) in (AUTHORITY_PINNED_OFFLINE, AUTHORITY_AUTO)

    def auto_eligible(self, course: str) -> bool:
        """兼容别名 = `auto_handoff_allowed`（守卫钉「唯一读者」；新代码别再用它）。

        历史：2026-10-03 的口径是「pin 不再参与，唯一 opt-out = 停课」——本 plan §4.2
        半回摆（pin online 重新获得阻止力），语义收窄进上面两个新函数。
        """
        return self.auto_handoff_allowed(course)

    def _marker_exists(self, course: str) -> bool:
        """开课标记在不在（停课 = 删它；`stopped` 维的唯一判据）。

        在表：读课程目录（读不到 ⇒ False——这正是旧 `auto_eligible` 的容错方向）；
        冷课：读 `_discover_root`；读不到目录（单课程裸 job_root）⇒ True，**未知不误杀**
        （不能把读不到标记当成停课的证据）。
        """
        if course in self._stores:
            try:
                return (self.course_dir(course) / COURSE_ENABLE_MARKER).exists()
            except (OSError, ProtocolError):
                return False
        root = self._discover_root
        if root is None:
            return True
        try:
            return bool((root / course / COURSE_ENABLE_MARKER).exists())
        except OSError:
            return False

    def _dispatch_disk_record(self, course: str) -> dict | None:
        """盘上的派发记录（**不缓存**；读不到 ⇒ None）。冷课 `authority_of` 缺省短路用。"""
        try:
            raw = json.loads(self._dispatch_path(course).read_text(encoding="utf-8"))
        except (OSError, ValueError, ProtocolError):
            return None
        fallback = self.mode_of(course) if course in self._stores else COURSE_MODE_ONLINE
        return dispatch_record_merge(raw, fallback)

    def set_mode_pinned(
        self,
        course: str,
        mode: str,
        pin: bool | None,
        *,
        drop_jobs: bool = False,
    ) -> tuple[bool, str]:
        """模式写入的**人类入口**（`/admin/courses?...&pin=1|0` 走这里；二轮 P0-2）。

        `pin is None` = 非人（legacy 调用）：**拒绝覆盖「由 claim 产生的 offline」**；
        人（显式 pin 字段，1 或 0）可覆盖任何 claim 状态。落地 = `_modes` + 记录（原子写盘）。

        ★ 切在线/交还自动（`m == online`，pin=1 或 0）：**撤销离线租约**（立墓碑，§3.2/§3.3）
        并清 `claimed_offline/by/at/flipped_at` 四键（五轮 P1-F：`flipped_at` 此前无清理
        入口 ⇒ 交还自动后 `stall_verdict` 拿旧锚点立刻判 `pending-export` 假停滞）。
        `drop_jobs=True`（`&drop_jobs=1`，**只有人的动作会带**）：★六轮 P0-6 ② **仅
        `m == offline` 才生效**——切回在线时撤单反成 bug（停在队首的活正是回来要领的；
        §4.3 半球修正）。开课/停课/回灌走的 `pushCourseMode` 不带它，「队列一字不动」的
        既有契约（`course-lifecycle.ts`）逐字不变。
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
        if m == COURSE_MODE_ONLINE:
            # 人切在线 / 交还自动：四键归零（申请 §3.2 的行语义）。
            fields.update(
                {
                    "claimed_offline": False,
                    "claimed_by": "",
                    "claimed_at": 0.0,
                    "flipped_at": 0.0,
                }
            )
        elif pin is not None:
            # legacy 的 offline 重写不该顺手把一个正在进行的自动交接「洗白」。
            fields["claimed_offline"] = False
        self._dispatch_update(course, **fields)
        self._modes[course] = m
        self._sync_hold(course)
        if m == COURSE_MODE_ONLINE:
            self.revoke_offline_lease(course, reason="switch-online")
        if drop_jobs and m == COURSE_MODE_OFFLINE:
            # 人的动作带了 `&drop_jobs=1` ⇒ 把上一个模式留在队列里、还没人领的 job 作废
            # （plan §4 T0「别留垃圾」）。自动路径（claim 翻 offline）在下面两处**无条件**撤。
            self._drop_unsettled(course, reason="mode-switch")
        return True, ""

    def _drop_unsettled(self, course: str, *, reason: str) -> list[str]:
        """作废该课**未被认领**的未结算 job（`_JobStore.cancel_unsettled_jobs`；T0）。

        为什么住本簇：撤单是「派发状态」这个域的收尾动作——三条翻 offline 的路径
        （人的 `set_mode_pinned` / claim 无包的 `begin_auto_handoff` / claim 有包的
        `note_claim`）都住本簇；落在别处会做成三份各自判断的第二事实源。

        失败语义：`mode` 已经翻过来了，撤单失败**不回滚**（回滚会做成「切了但没切」）。
        只响亮记一笔——池子里的残留由 `claimable_job_ids` 按账本重算兜（它才是真判据）。
        """
        st = self._stores.get(course)
        if st is None:
            return []
        try:
            dropped = st.cancel_unsettled_jobs(reason=reason)
        except OSError as e:
            print(
                f"[hub-server] ⚠ 撤单失败 course={course} reason={reason}: {e}"
                "（mode 已翻，不回滚；残留由可领池按账本重算兜）",
                flush=True,
            )
            return []
        if dropped:
            print(
                f"[hub-server] 撤单 course={course} reason={reason} n={len(dropped)}"
                "（未认领的 job 已作废，在飞的不动）",
                flush=True,
            )
        return dropped

    def begin_auto_handoff(self, course: str, worker_id: str = "") -> tuple[str, str]:
        """claim 无包分支的临界区（§3.1a-b + §3.3a）：过 busy 闸 → 翻 mode（落盘）。

        返回 verdict：`flipped`（可触发控制台）/ `busy`（别的课在跑）/ `not_auto`（未在自动池或未知）。
        **不建租约**（包还没出现，领租约还早）；触发控制台是调用方（HTTP 层）的事——网络调用不持锁。

        ★五轮 P0-B + ★六轮 F4：**新一轮交接 ⇒ 重置导包触发账本**——「新一轮」= 新 `worker_id`
        ≠ 记录的 `claimed_by`（换主）**∨** 距上次 claim 超 `AUTO_HANDOFF_PENDING_SEC`
        （`.worker-id` 持久，同机重开会话沿用同一 id，只判换主会漏「同主回来」）。同时
        **刷新 `claimed_at` 且写 `claimed_by`**（R2-d；busy 窗口的新锚点，也是下一次交接的
        「上次 claim」）；`flipped_at` 仍只在首翻时写（停滞告警的锚点，重复重试不刷新）。
        """
        if course not in self._stores:
            return "not_auto", "未知课程"
        if not self.auto_handoff_allowed(course):
            return "not_auto", "该课未在自动池（开课标记已删或人固定其模式）"
        with self._lease_lock:
            busy = self._busy_locked(course, worker_id)  # D3：一拖一按 worker（同一台盘串行）
            if busy:
                return "busy", busy
        now = float(self._now())
        new_owner = str(worker_id or "").strip()
        rec0 = self.dispatch_record(course)
        prev_owner = str(rec0.get("claimed_by") or "")
        prev_at = float(rec0.get("claimed_at") or 0.0)
        #: 换主 ∨ 超窗（同主重开会话也命中）⇒ 导包触发账本清零（前任烧满的 give_up 不继承）。
        new_round = bool(prev_owner and new_owner and new_owner != prev_owner) or (
            prev_at > 0 and now - prev_at > AUTO_HANDOFF_PENDING_SEC
        )
        fields: dict[str, Any] = {
            "mode": COURSE_MODE_OFFLINE,
            "claimed_offline": True,
            "claimed_by": new_owner or prev_owner,
            "claimed_at": now,
        }
        # 重复 claim（云机在导包窗口里轮询）不刷新 `flipped_at`：它是**停滞告警**的锚点，
        # 被每次重试推到「刚刚」会让它永远不触发。（`claimed_at` 服务 busy 窗口——两个锚点
        # 时间轴**刻意分叉**，别对调；见 `_busy_locked`。）
        if not (rec0.get("claimed_offline") and str(rec0.get("mode")) == COURSE_MODE_OFFLINE):
            fields["flipped_at"] = now
        self._dispatch_update(course, **fields)
        self._modes[course] = COURSE_MODE_OFFLINE
        self._sync_hold(course)
        if new_round:
            reset_auto_handoff_triggers(course)
            print(
                f"[hub-server] auto-handoff {course}: 新一轮交接（"
                f"{prev_owner or '-'} → {new_owner or '-'}）⇒ 导包触发账本清零",
                flush=True,
            )
        # 翻 offline ⇒ 该课在线的未认领 job 即刻作废（用户 2026-10-03 裁决：claim 自动翻模式
        # 也要撤）。**在飞的不动**（裁决：不管在算的）——见 `cancel_unsettled_jobs`。
        self._drop_unsettled(course, reason="auto-handoff")
        return "flipped", ""

    def note_claim(self, course: str, worker_id: str) -> None:
        """claim 成功后的派发记账（claimed_by/at + 自动课翻 offline——T2 的同步小事之一）。

        ★六轮（§3.3 竞态收尾）：claim 在「读 authority 之后、切换之前」赢锁（人同时切了
        `pinned_online`）⇒ **不翻模式**并立刻 `revoke_offline_lease`（把刚建的租约标成墓碑）。
        两种到达顺序的终态一致 = `mode=online ∧ pinned ∧ 租约成墓碑`（契约见 §3.3）。
        """
        now = float(self._now())
        fields: dict[str, Any] = {"claimed_by": worker_id, "claimed_at": now}
        flipped = False
        if self.auto_handoff_allowed(course) and self.mode_of(course) != COURSE_MODE_OFFLINE:
            self._modes[course] = COURSE_MODE_OFFLINE
            self._sync_hold(course)
            fields["mode"] = COURSE_MODE_OFFLINE
            fields["claimed_offline"] = True
            fields["flipped_at"] = now
            flipped = True
        self._dispatch_update(course, **fields)
        if flipped:
            # claim 成功即翻 offline（有包那条腿）——同样撤掉本课未认领的在线 job（裁决同上）。
            self._drop_unsettled(course, reason="auto-handoff")
        elif self.authority_of(course) == AUTHORITY_PINNED_ONLINE:
            # 竞态：人已切固定在线 ⇒ 立即把刚领的租约标成墓碑（worker 下一次心跳 409 revoked）。
            self.revoke_offline_lease(course, reason="race-pinned-online")

    def note_release(self, course: str) -> None:
        """release 后的派发记账：持有者清空、**hold 清空**（★M1b：清完协作派发当天恢复）；
        mode 保持 offline（U3 的 waiting——M1c 随模式一起退）。"""
        self._dispatch_update(course, claimed_by="", claimed_at=0.0, hold={})
        self._sync_hold(course)

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

    def _busy_locked(self, course: str, worker: str = "") -> str:
        """busy 闸：**这台盘已经在别的课上跑** ⇒ 拒因文案（plan §1.2-6 / D3「一拖一按 worker」）。

        ★M1b 两处切换（与旧版逐字对照）：
        · 判据从 `_leases` 活租约换成 **hold 镜像**（重启后仍然算数；锁序不变：
          `_lease_lock → _dispatch_lock`——`hold_of` 只走后者）；
        · **旧腿②「别课正在交接」整条删**：Q1 定死 `pending_export` 是软态、**不占任何闸**
          （旧腿会让一次导包把所有盘冻住——那正是本次重构要拆的痛点）。
        没有 worker 身份（清单缺 `?worker=`）⇒ **不判 busy**（= `claimable` 是上界，plan §1.5.4-P2-x/§69）。

        **调用方持 `_lease_lock`**（`busy_reason` / `claim_offline` / `begin_auto_handoff` 都各自
        在临界区里调）；持有者按 `_holder_info_locked` 判，不走会再取锁的 `holder_info`。

        ★ 2026-10-07 两次现场事故（上游 `359391a6` 修的就是旧两腿）在此**结构性
        消解**，不是被丢掉——别照事故史把腿②加回来：
        * 「重试把闸续死」：旧腿②拿 `claimed_at` 当锚，而重试者**正是**被卡的那台云机
          ⇒ 「超窗就不再占闸」的逃生门永久失效（现场：k5 已跑满 it151 > iters 150 出不了包，
          却把 ready 的 k10 锁了十几分钟）。现在软态根本**不占闸**，无锚可续。
        * 「停课残留冻结全池」：旧腿②吃 `claimed_at` 残留（`stopCourse` 走 `pin=None`，
          `set_mode_pinned` 有意不清 claim 记账 ⇒ 课不在任何清单里却还能占闸）。现在唯一的
          腿只认**本 worker 手上的活 hold**，停课的课留不下 hold（claim 要开课标记 ∧ 有包），
          且已持有的 hold 进度静默超阈即 `stale` 让位 ⇒ 闸天然有界、单盘级，不会冻结全池。
          上游那条 `is_runnable_offline(other)` 豁免**有意不移植**：它把 mode 读回派发路径，
          与 M4 后的「派发路径零 mode/authority 读取」冲突。
        """
        wid = str(worker or "").strip()
        if not wid:
            return ""
        seen: set[str] = set()
        for other in (*self.offline_task_courses(), *self._order, *self._stores):
            if other == course or other in seen:
                continue
            seen.add(other)
            if self.authority_of(other) == AUTHORITY_PINNED_ONLINE:
                continue  # 人固定在线：它的记录不占任何闸（§3.4-3）
            info = self._holder_info_locked(other)  # 已在 `_lease_lock` 里：走无锁内核
            if not info or info.get("stale") or info.get("revoked"):
                continue  # 死盘 / 墓碑 / 进度静默：不算「在跑」（R2-b）
            if str(info.get("worker_id") or "") != wid:
                continue  # 别的盘在跑别的课 ⇒ **不占闸**（多机并行的本意）
            return (
                f"busy: 你这台盘正在 {other} 上跑（一拖一按 worker："
                "它 release 后自动解除；换一台盘可并行）"
            )
        return ""

    def busy_reason(self, course: str, worker: str = "") -> str:
        """只读版 busy 拒因（清单面用；空串 = 不忙）。**带 `worker` 才有意义**（见 `_busy_locked`）。"""
        with self._lease_lock:
            return self._busy_locked(course, worker)

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
            authority = self.authority_of(course)
            treat_offline = self.is_runnable_offline(course)
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
            pending = self.pending_export_of(course)
            runs = progress.get(course) or {}
            last = max((float(r.get("last_mtime") or 0.0) for r in runs.values()), default=0.0)
            why = stall_verdict(
                treat_offline=treat_offline,
                authority=authority,
                completed=False,
                holder_present=holder is not None,
                last_progress_mtime=last,
                flipped_at=float(rec.get("flipped_at") or 0.0),
                threshold=threshold,
                now=now,
                # ★M1b / F11：② 的锚——导包意向的时刻与它的窗（导包窗，独立于 ① 的 1800s）。
                pending_export_at=float(pending.get("at") or 0.0),
                export_window=auto_handoff_pending_sec(),
            )
            if not why:
                continue
            anchor = max(last, float(rec.get("flipped_at") or 0.0), float(pending.get("at") or 0.0))
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
