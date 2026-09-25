"""remote/hub/queue_claims.py — 派发与认领：轮转挑选 · peek · 熔断告警 · 合法放弃。

`_HubQueue` 的七个域混入之一（S4 第十五刀），是**两条认领路径**的共同家园：

* 旧路径 `claim_next` —— 服务端**替 worker 挑**（跨课程轮转 + 超时避让 + 离线课能力闸）；
* 新路径 `peek_jobs` + `priority_view` + `claim_job`（2026-09-22，
  plan/transfer-scheduling）—— 挑活在客户端，服务端只管「这一份归不归你」。

两条路径共用同一批闸，所以它们必须住在一起：`_serves_course`（开课标记）、离线课能力闸
（`offline_ok` = worker 自报能跑完整段）、`may_avoid_stale_holder`（避让链）、以及
`_announce_freeze`（★ 毒包熔断告警，§4.1）。

`_announce_freeze` 为什么住这里而不是观测簇：检测点在 store（它才看得到租约过期），而告警
要课程名与认领者，两者都在队列层手上；**新 claim 面也必须喊**（R2-6）——否则旧的轮询面
退役后熔断就只剩「静默不再回池」，而那正是 §4.1 事故要治的东西。

## 轮转游标 `_cursor` 的唯一写点

`claim_next` 与 `claim_job`（**只有真正拿到才推进**，R2-C2）。`peek_jobs` 只读不写——否则
「看一眼」就会移走别人的轮次；而若完全不推，轮转又会钉死在序首（一门课饿死）。

## 依赖方向

`queue_claims → {common.protocol, remote.hub.store, remote.hub.store_leases}`（向下；
`store_leases` 取 `ClaimOutcome` 这一个对外名字）。**不 import 任何兄弟混入**。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from threading import Lock

from common.protocol import (
    CLAIM_MODE_EXCLUSIVE,
    CLAIM_MODES,
    CLAIM_TTL_SEC,
    PEEK_MAX,
    PRIORITY_NONE,
    ROLE_ONLINE,
    find_payload,
    may_avoid_stale_holder,
    rotation_order,
)
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore
from remote.hub.store_leases import ClaimOutcome


class QueueClaimsMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _ambiguous: dict[str, list[str]]
    _cursor: str | None
    _lock: Lock
    _order: list[str]
    _stores: dict[str, _JobStore]

    # 本簇要调、而不在共同声明面 `QueuePeer` 里的那一个（理由见 `queue_peer.py` 头部）
    _store_of: Callable[[str], _JobStore | None]

    # ---- 派发（跨课程轮转 + 超时换 worker） ----
    def claim_next(
        self, worker_id: str = "", role: str = ROLE_ONLINE
    ) -> tuple[str, str, str] | None:
        """取下一份该派发的 job → (course, job_id, lease_token)；无 → None。

        轮转从**上次派发的下一门**开始（`rotation_order`）：每课程一条 FIFO，若每次都
        从序首扫，一门课的积压会把其它课程饿死。

        避让（用户口径「超时回落队首并改为推送其它 worker」）：候选 job 的上一份租约是
        **过期死掉的**且持有人就是本次请求者时，本次跳过它（`avoid_expired_holder`）——
        但机群只剩一个活跃 worker 时不避让（否则它自己超时过的 job 谁都领不到 = 停摆）。

        **两道闸（2026-09-25，本节头的 plan）**，判据都在 `_JobStore.role_blocked`：
          * 归属闸（job 级）：派发只看 **job 自己的 `role`**，不再看课程当前 mode
            （mode 易变：切一次模式，历史 job 的归属就跳一次 —— 事故本体）；
          * 停摆闸（课程级）：离线课不向在线盘派发（活留着等切回在线）。
        旧口径「带标 worker 仍可领在线课」**已取消**：一个盘一种任务（用户 2026-09-25
        裁决）——这条是**行为变更**，见 `DECISIONS.md §2026-09-25-goalnn-role-routing`。
        """
        # 派发前扫一次（有最小间隔闸）：新课程/新 job 目录出现后，**下一次轮询**就能被领到，
        # 不必等后台节拍——否则新开的课在最坏情况下要等一个扫描周期才有人来领活。
        self.discover()
        active_workers = self.active_worker_count()
        for course in rotation_order(self._order, self._cursor):
            if not self._serves_course(course):
                continue
            st = self._stores[course]
            for jid in st.claimable_job_ids():
                if st.role_blocked(jid, role):
                    # 归属/停摆不符：**跳过这一份**，不是跳过整门课——同门课同时躺着两类
                    # 归属的活是「模式刚热切过」的常态（正是事故现场的形状）。
                    # 「跳过后一直无人领」的收尾是撤单腿的事
                    # （`plan/switch-mode-drops-jobs.plan.md`），不是这一层的职责。
                    continue
                # 只给「允不允许避让」的闸；身份比对在 store 里（它才知道租约回收后的
                # stale 记录，在这里判会踩时序——见 `may_avoid_stale_holder` docstring）。
                avoid = may_avoid_stale_holder(worker_id, active_workers)
                tok = st.claim(
                    jid, worker_id=worker_id, avoid_stale_holder=avoid, role=role
                )
                if tok is None:
                    self._announce_freeze(course, jid)
                    continue  # 活租约在持 / 本次该避让 / 并发领取竞负 / 已熔断冻结
                with self._lock:
                    self._cursor = course
                return course, jid, tok
        return None

    def _announce_freeze(self, course: str, jid: str) -> None:
        """★ 毒包熔断告警（§4.1）——一次性事件，喊过就不重喊。

        为什么在这里喊：检测点在 store（它才看得到租约过期），而告警要课程名与认领者，
        两者都在队列层手上。**新 claim 面也必须喊**（R2-6）：否则旧的轮询面退役后
        熔断就只剩下「静默不再回池」，而那正是 §4.1 事故要治的东西。
        """
        st = self._store_of(jid)
        froze = st.consume_freeze_announcement(jid) if st else None
        if froze is None:
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] ★ 熔断冻结："
            f"job={jid} course={course or '-'} "
            f"—— 连续 {froze.get('reclaims')} 次认领后零回传"
            f"（最后一次认领者={froze.get('worker') or '?'}）；"
            "已从可领取池移除，**重发不清冻结**；"
            f"确认后解冻：POST /admin/unfreeze job_id={jid}",
            flush=True,
        )

    def claimable_job_ids(self, course: str) -> list[str]:
        st = self._stores.get(course)
        return st.claimable_job_ids() if st else []

    def job_role(self, job_id: str) -> str:
        """job 归属角色（经 store 的缓存读）；不归本 hub 管 ⇒ online（保守：不锁死别人）。"""
        st = self._store_of(job_id)
        return st.job_role(job_id) if st is not None else ROLE_ONLINE

    def role_blocked(self, job_id: str, role: str) -> str:
        """归属/停摆闸的**只读**探针（派发面用）；不归本 hub 管 ⇒ `""`（不锁死别人）。"""
        st = self._store_of(job_id)
        return st.role_blocked(job_id, role) if st is not None else ""

    def claim(
        self,
        job_id: str,
        ttl: float = CLAIM_TTL_SEC,
        worker_id: str = "",
        avoid_stale_holder: bool = False,
        mode: str | None = None,
        expected_epoch: int | None = None,
        role: str = ROLE_ONLINE,
    ) -> str | None:
        """（单份领取；多课程的挑选入口是 `claim_next`——归属/停摆闸在 store 里，两处共用。）

        这条路的调用者包括 **push 派发**（`push_dispatch._dispatch`）——它**不是**由
        `claim_job` 转过来的，所以闸必须住在 `_claim_locked` 里而不是各调用点。
        """
        st = self._store_of(job_id)
        if st is None:
            return None
        return st.claim(
            job_id,
            ttl=ttl,
            worker_id=worker_id,
            avoid_stale_holder=avoid_stale_holder,
            mode=mode,
            expected_epoch=expected_epoch,
            role=role,
        )

    # ---- 新调度面（2026-09-22，plan/transfer-scheduling）：peek / claim / priority ----
    def peek_jobs(
        self,
        *,
        worker_id: str = "",
        role: str = ROLE_ONLINE,
        n: int = PEEK_MAX,
    ) -> list[dict]:
        """候选 job（**不认领**：无租约、无副作用、不动游标）——§2.6 的软持有候选来源。

        与 `claim_next` 同三道闸：`_serves_course`（开课标记）、**归属/停摆**
        （`role` = 请求方自报的归属，判据与闸同源：`_JobStore.role_blocked`）、
        冻结/已落盘的排除（在 `claimable_job_ids` 里）。

        跨课程公平性：顺序取 `rotation_order(self._order, self._cursor)`，**只读不写**
        （R2-C2）——游标由真正 claim 成功的那一方推进（`claim_job`）。若在这里推进，
        「看一眼」就会移走别人的轮次；而若完全不推，轮转又会钉死在序首（一门课饿死）。

        每课程**至多给一个**候选：候选是「这轮可以干哪几门课」，不是「把队首扫空」
        （深度 3 的预取靠多轮 peek 填满，而不是靠一次拿 16 个）。
        """
        self.discover()
        # R2-2：登记表（避让链的唯一输入）改由 peek/priority 喂——缺它
        # `active_worker_count()` 恒 0 ⇒ 避让链静默失效（纯函数用例测不出「调用点为 0」）。
        self.note_worker(worker_id)
        out: list[dict] = []
        want = max(1, int(n))
        for course in rotation_order(self._order, self._cursor):
            if len(out) >= want:
                break
            if not self._serves_course(course):
                continue
            st = self._stores[course]
            ids = [j for j in st.claimable_job_ids() if not st.role_blocked(j, role)]
            if not ids:
                continue
            jid = ids[0]
            man = self._manifest_summary(jid)
            out.append(
                {
                    "job_id": jid,
                    "course": course,
                    "payload_bytes": man.get("payload_bytes", 0),
                    "payload_sha256": man.get("payload_sha256"),
                    "runId": man.get("runId"),
                    "it": man.get("it"),
                }
            )
        return out

    def _manifest_summary(self, job_id: str) -> dict:
        """候选的 manifest 摘要（**不**把整份 manifest 塞进 peek：那是认领后才需要的东西）。"""
        jd = self._job_dir(job_id)
        try:
            man = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(man, dict):
            return {}
        try:
            pl = find_payload(jd)
            size = pl.stat().st_size if pl is not None else 0
        except OSError:
            size = 0
        return {
            "runId": man.get("runId"),
            "it": man.get("it"),
            "payload_bytes": int(size),
            # `payload_sha256` 也进摘要（2026-09-22，P2）：预取拿到的字节必须能**就地**校验
            # 是不是这份 job 的 payload——不带它的话，预取会把「sha 不符」的发现推到开算前
            # （那时已占了 claim 租约，错一份就多一次租约往返）。
            "payload_sha256": man.get("payload_sha256"),
        }

    def claim_job(
        self,
        job_id: str,
        *,
        mode: str = CLAIM_MODE_EXCLUSIVE,
        worker_id: str = "",
        expected_epoch: int | None = None,
        role: str = ROLE_ONLINE,
    ) -> ClaimOutcome:
        """新 claim 面（`POST /jobs/{id}/claim`）的唯一实现入口。

        与旧 `claim_next` 的差别：挑活已在客户端（peek + priority）；这里只负责「这一份
        归不归你」+ 游标推进 + 熔断告警——**不再**在这里扫整张表。
        避让的「允不允许」仍在调用方算（`may_avoid_stale_holder`，R2-2 的避让链）。

        `role`（2026-09-25）：归属/停摆不符 ⇒ `ClaimOutcome(False, "", "role"|"parked", …)`
        ——**确定性拒**（不是 409 busy：重试一百次也不会变），面向「这个盘本来就不该跑它」。
        """
        st = self._store_of(job_id)  # 内部走 course_of：歧义会记进 self._ambiguous
        if st is None:
            holders = self._ambiguous.get(job_id)
            # 「不归本 hub 管」与「归属有歧义」在**调度面**都是拒答（都不许跨课程兜底），
            # 但对排障是两件事 ⇒ reason 要分开（2026-09-24 事故：现场只看到一句
            # 「hub 异常」，真因是身份歧义导致的跨课程路由）。
            return ClaimOutcome(
                False,
                "",
                "unknown",
                f"归属歧义: {'/'.join(holders)}" if holders else "unknown",
            )
        if mode not in CLAIM_MODES:
            return ClaimOutcome(False, "", "bad_mode", f"mode 必须是 {list(CLAIM_MODES)}")
        avoid = may_avoid_stale_holder(worker_id, self.active_worker_count())
        out = st.claim_outcome(
            job_id,
            mode=mode,
            worker_id=worker_id,
            avoid_stale_holder=avoid,
            expected_epoch=expected_epoch,
            role=role,
        )
        if out.ok:
            course = self.course_of(job_id) or ""
            with self._lock:
                self._cursor = course  # R2-C2：只有真正拿到才推进轮转起点
            return out
        self._announce_freeze(self.course_of(job_id) or "", job_id)
        return out

    def priority_view(
        self,
        *,
        worker_id: str = "",
        job_ids: list[str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, str]]:
        """`POST /jobs/priority` 的事实面：`(epoch, {jid: 优先级}, {jid: 一行理由})`。

        问询的 job 集合 = 调用方给的 `held`（软持有 ∪ 已 claim 未开算）；`worker_id` 用来
        把**自己的**痕迹排除掉——自己手里那份 computing 不叫「别处在算」（§1.4）。
        """
        self.note_worker(worker_id)  # R2-2
        prios: dict[str, str] = {}
        reasons: dict[str, str] = {}
        epoch = 0
        for jid in job_ids or []:
            st = self._store_of(jid)
            if st is None:
                prios[jid] = PRIORITY_NONE
                reasons[jid] = "unknown job（本 hub 无此 job）"
                continue
            p, why = st.priority_for(jid, exclude_worker=worker_id or "")
            prios[jid] = p
            reasons[jid] = why
            epoch = max(epoch, st.scheduling_epoch())
        if not job_ids:
            epoch = max((st.scheduling_epoch() for st in self._stores.values()), default=0)
        return epoch, prios, reasons

    def abandon(self, job_id: str, worker_id: str = "") -> bool:
        """合法放弃（R1-3）：release 租约 + 清可见性 + **零** reclaim。幂等。"""
        st = self._store_of(job_id)
        return st.abandon_job(job_id, worker_id) if st else False

