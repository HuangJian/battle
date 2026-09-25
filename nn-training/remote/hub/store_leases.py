"""remote/hub/store_leases.py — 租约、心跳、毒包熔断与 job 终局。

`_JobStore` 的六个域混入之一（S4 第十四刀），也是最大的一簇。它守着两条互相咬合的规则：

1. **租约**（H2）：认领即下发 `lease_token`，心跳/结果回传必须携带——杜绝「任何持 token
   者都能续租/抢租约」的多 worker 竞态。`_collect_expired_locked` 是唯一的过期回收点。
2. **毒包熔断**（§4.1）：同一 job「认领后**零回传**」满 `FREEZE_AFTER_RECLAIMS` 次 ⇒
   冻结 + 响亮告警。判据是「零回传」而不是「失败」——报得上来的失败早有确定性通道
   （`POST /jobs/{id}/fail`），这里兑的是**未知崩溃类型**（进程被杀 / OOM 硬死）。

`_frozen` 刻意**不**复用 `fail.json`：`publish` 重发同 job_id 会清失败标记（重发即重试），
冻结若住那里，重发当场解冻。两者正交，解冻只走人工入口。

## 依赖方向

`store_leases → {common.protocol}`（向下）；跨域调用经 `self` 到 `store_scheduling`
（`_drop_commitment_locked` / `_bump_epoch_locked` / `_job_priority_locked`）与
`store_ledger`（`_job_dir` / 账本）。**不 import 任何兄弟混入**。

## 状态（`_init_leases`）

`_leases` · `_lease_owners` · `_last_heartbeat` · `_lease_workers` · `_stale_holders` ·
`_reclaims` · `_frozen`，外加 `_backup_authorized`（显式授权备份的回传放行票——它写在这里
而不是调度簇：读它的三处全在租约/结果路径上）。

归属路由（2026-09-25）也住本簇：`parked`（课程停摆位）· `_roles` / `_role_lock`（归属缓存）
与两个判据源 `job_role()` / `role_blocked()`——**闸必须与租约写入在同一个临界区**：
`_claim_locked` 是租约的唯一入口，闸住那里则四条认领面（claim_next / peek+claim / 按 id
直领 / push 派发）天然同源。

## 两个对外名字（**名字是契约**）

`ClaimOutcome` 与 `FREEZE_AFTER_RECLAIMS` 随本簇搬进本模块：`_HubQueue` 与
`tests/test_poison_freeze.py` 都要它们，而谁都 import 不了 `hub_server`（成环）
⇒ 由 `hub_server` 反过来 import 本模块（`FREEZE_AFTER_RECLAIMS as FREEZE_AFTER_RECLAIMS`
保留 `remote.hub_server.FREEZE_AFTER_RECLAIMS` 这个取名字的入口）。
"""

from __future__ import annotations

import json
from collections import namedtuple
from threading import Lock
from typing import Any

from common.protocol import (
    CLAIM_MODE_BACKUP,
    CLAIM_MODE_EXCLUSIVE,
    CLAIM_MODES,
    CLAIM_TTL_SEC,
    FAIL_NAME,
    PRIORITY_HIGHEST,
    ROLE_OFFLINE,
    ROLE_ONLINE,
    role_of,
)

#: `claim_outcome()` 的返回形状（新 HTTP 面的出口；`token` 为空串 = 无租约/未拿到）。
#: `status ∈ {"ok", "backup", "demoted", "held", "frozen", "stale_holder"}`——worker 侧
#: 只关心「拿到了吗」+「没拿到是降级还是真轮不到」：前者丢副本、后者按 low 处理。
ClaimOutcome = namedtuple("ClaimOutcome", "ok token status reason")

#: 毒包熔断阈值（plan/accident.plan.md §4.1，2026-09-21）：同一 job 被**认领后零回传**满这么多次
#: ⇒ hub 冻结它并响亮告警。为什么是「零回传」而不是「失败」：worker 报得上来的失败早就有
#: 确定性通道了（`POST /jobs/{id}/fail`，§4.0/P0）；这里兑的是**未知崩溃类型**——worker 连
#: 报都报不上来（进程被杀 / OOM 硬死 / 归档层以外的死法），只能从「租约过期且无结果」的
#: 节奏里认出来。本次事故：40 次 × 5 分钟，无告警、无计数。
#:
#: 为什么不用 1：合法重试是存在的（worker 挂掉一次、换台机器接着跑）——阈值 3 给了一轮
#: 「换台机器 / 重启 worker」的自然愈合机会（认领 TTL 300s ⇒ 最多烧 ~15 分钟），又不至于
#: 把 3.5 小时的静默空转让它过去。
FREEZE_AFTER_RECLAIMS = 3


class LeaseMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 兄弟簇 `store_scheduling` 的状态与方法（认领要同时读三张优先级表并推 epoch）
    _claimed: dict[str, dict]
    _epoch: int
    _bump_epoch_locked: Any
    _drop_commitment_locked: Any
    _job_priority_locked: Any
    #: 兄弟簇 `store_ledger` 的状态与方法（读账本判「这份 job 还在不在池里」）
    _job_dir: Any
    _append_ledger: Any
    _read_ledger: Any

    def _init_leases(self) -> None:
        #: job_id -> lease 到期时间戳（monotonic 无关；用墙钟，重启即空）
        self._leases: dict[str, float] = {}
        #: job_id -> 租约持有人 lease_token（H2；重启即丢，随租约重建）
        self._lease_owners: dict[str, str] = {}
        #: job_id -> 最近一次心跳（领取算一次）墙钟（P3b 可观测；/jobs/status 暴露）
        self._last_heartbeat: dict[str, float] = {}
        #: job_id -> 租约持有人的 worker 身份（v5 多课程单 hub，2026-09-18）。
        #: 与 `_lease_owners`（token，鉴权用）**分工不同**：这个只用来回答「上一份租约是
        #: 谁跑死的」，从而在超时回收时把那台 worker 排除在本次重派之外（用户口径：
        #: 超时回落队首后「改为推送其它 worker」）。无身份（旧 worker / 手写 curl）不记。
        self._lease_workers: dict[str, str] = {}
        #: job_id -> 上一次租约**过期**时死掉的持有人（不避让自己时不清，避免误让）
        self._stale_holders: dict[str, str] = {}
        #: job_id -> 「认领后零回传」次数（毒包熔断的判据，见 FREEZE_AFTER_RECLAIMS）。
        #: 只在**租约过期且无结果/无失败标记**的那一刻 +1（主动 release 不算：那是 worker
        #: 自己说「这个失败我能自愈」）。volatile：hub 重启即丢——重启本身就会重发未完成
        #: job（D8），计数从头起不改变结论（再烧 N 次即再冻）。
        self._reclaims: dict[str, int] = {}
        # ---- 归属角色（2026-09-25，plan/online-offline-role-routing）----
        #: job_id -> 冻结记录（毒包熔断的**独立第二状态**）：{"reclaims", "worker", "ts",
        #: "announced"}。刻意**不**复用 `fail.json`（失败标记）：`publish_job` 重发同 job_id
        #: 会清失败标记（“重发即重试”语义，见 `claimable_job_ids` 注释）——冻结若住那里，
        #: 重发当场解冻，本次事故照烧 3.5 小时。两者正交：重发不清冻结，解冻只走人工入口。
        self._frozen: dict[str, dict] = {}
        #: 已被**显式授权备份**的 job_id ⇒ 它们的回传不吃 403（R2-3）。
        #: 为什么不是「pop 掉原租约」（本轮评审推翻的写法）：pop 后原 worker 硬死无租约
        #: 可过期 ⇒ 毒包熔断失明；job 立刻回池 ⇒ 第三/第四份可自由领取；push 腿
        #: 「hub 持租约防同一份活两处跑」的自保也会失效。标记只放行回传，不动其它语义。
        self._backup_authorized: set[str] = set()
        #: 课程停摆（离线课 = 活留着等切回在线，2026-09-20 的既有语义）。与 job 级归属闸
        #: **正交**：这一位说的是「这门课现在还派不派活」（课程级），`role` 说的是「这份活
        #: 归哪块盘」（job 级）。两者共用同一个咽喉点（`role_blocked` → `_claim_locked`），
        #: 不各自为政——由 `_HubQueue` 在 `set_mode` / 构造时同步（它是唯一知道 mode 的层）。
        self.parked = False
        #: job_id -> 归属角色缓存（`manifest.role`，2026-09-25）。为什么缓存：`claim_next` /
        #: `peek` 每拍都要按角色过滤候选，而 manifest 在盘上——每拍每候选重读一次盘是白烧 IO。
        #: 失效点 = `publish`（重发覆盖 manifest ⇒ 旧归属作废；见那里的 pop）——不靠
        #: 「同一 job_id 的 role 永不变」这种假设。只缓存**读成功**的值（manifest 还没落定时不缓存）。
        #: 用独立锁：`job_role` 会被持 `_lock` 的调度临界区调到，共锁会自锁。
        self._roles: dict[str, str] = {}
        self._role_lock = Lock()

    def role_blocked(self, job_id: str, role: str) -> str:
        """这份活能不能交给 `role`；`""` = 可以，否则是拒因（`"parked"` / `"role"`）。

        **两道闸的唯一判据源**（2026-09-25）：派发面（`claim_next` / `peek_jobs` / push）
        用它**过滤候选**，临界区（`_claim_locked`）用它**拒绝**——同一份判据两个方向，
        不会出现「peek 说能领、claim 说不能」这类两套尺子。

        为什么停摆闸也住这里（而不住各自的调用点）：push 腿（`Hub.claim`）**不经过**
        `claim_job`，按 id 直领（`POST /jobs/{id}/claim`）也不经过 `claim_next`——闸写在
        调用点必然漏一条（F5 就是这么来的）。
        """
        if self.parked and role != ROLE_OFFLINE:
            # 离线课：只对离线盘放行（旧口径就是「带标 worker 才领得走」，现在改成按归属判）。
            return "parked"
        if self.job_role(job_id) != role:
            return "role"
        return ""

    def job_role(self, job_id: str) -> str:
        """job 的**归属角色**（`manifest.role`；旧 job 按 `kind` 兜底；读不到 ⇒ online）。

        为什么读不到就归 online：这个函数的返回值会参与「你能不能领这份活」的判断。
        归 online 的后果是「少一个人能领离线活」（看得见：队列不降），归 offline 的后果是
        「一个能跑的活没人领、且看起来一切正常」（看不见）——两者不对称，所以倒向后者。
        """
        with self._role_lock:
            cached = self._roles.get(job_id)
        if cached is not None:
            return cached
        try:
            man = json.loads((self._job_dir(job_id) / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ROLE_ONLINE
        if not isinstance(man, dict):
            return ROLE_ONLINE
        role = role_of(man)
        with self._role_lock:
            self._roles[job_id] = role
        return role

    # ---- 租约（P3b 独占加超时：领取即设租约，心跳续租，过期回池） ----
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
        """领取（独占 = 设租约 + owner + last_heartbeat 三件套**同时置**）。

        B3 必杀细节：只写 `_leases` 不写 `_lease_owners` 会导致 heartbeat 恒 False，
        300s 后长 job 被重广播——故领取必须走本函数，不许手写 `_leases[jid] = ...`。
        活租约在持 → 返回 None（调用方跳过本 jid，不是阻塞等）。

        `mode`（2026-09-22，R1-1）：
          * `"exclusive"` = 正常独占（设租约 + 写 `_claimed` + `epoch += 1`）；
          * `"backup"` = **备份副本**：不设租约、返回空 token，胜负由 `store_result`
            首写锁定决定。⚠ 它**不动**原持有者的租约（R2-3），只置 `_backup_authorized`
            让备份的回传**不吃 403**——否则 `ProtocolError` ⇒ `report_job_failure` ⇒
            训练停腿（这个坑本文件的旧注释里已写过一次：一个赢家把输家炸成事故）。

        `expected_epoch`（§2.3 highest 唯一性闸）：版本不匹配**不是错误**，是「有人比我快」
        的正常信号；本函数在**同一个临界区**内重新判定该 job 的优先级，仍为最高才放行。
        要区分「降级」与「领不到」用 `claim_outcome()`（同一出口，两个返回形状）。
        """
        ok, token, _why = self._claim_locked(
            job_id,
            ttl=ttl,
            mode=mode or CLAIM_MODE_EXCLUSIVE,
            worker_id=worker_id,
            avoid_stale_holder=avoid_stale_holder,
            expected_epoch=expected_epoch,
            role=role,
        )
        return token if ok else None

    def claim_outcome(
        self,
        job_id: str,
        *,
        mode: str = CLAIM_MODE_EXCLUSIVE,
        worker_id: str = "",
        avoid_stale_holder: bool = False,
        expected_epoch: int | None = None,
        role: str = ROLE_ONLINE,
    ) -> ClaimOutcome:
        """带原因的领取（新 HTTP 面的唯一入口）：区分「降级」与「领不到」。

        为什么不给 `claim()` 换返回类型：`str | None` 被既有调用方（`claim_next`、push
        派发、多份用例）依赖；而「降级 → 按 low 处理」只有新 worker 需要。两者共用同一个
        `_claim_locked` ⇒ 不会出现「两处各自校验 epoch」的第二个事实源（§3.1 末段）。
        """
        ok, token, why = self._claim_locked(
            job_id,
            ttl=CLAIM_TTL_SEC,
            mode=mode,
            worker_id=worker_id,
            avoid_stale_holder=avoid_stale_holder,
            expected_epoch=expected_epoch,
            role=role,
        )
        if ok:
            status = "backup" if mode == CLAIM_MODE_BACKUP else "ok"
            return ClaimOutcome(True, token, status, why)
        return ClaimOutcome(False, "", why, why)

    def _claim_locked(
        self,
        job_id: str,
        *,
        ttl: float,
        mode: str,
        worker_id: str,
        avoid_stale_holder: bool,
        expected_epoch: int | None,
        role: str = ROLE_ONLINE,
    ) -> tuple[bool, str, str]:
        """claim 的**唯一**临界区（返回 `(ok, token, 原因)`）。

        ★ 别在别处手写租约写入：B3 的坑（只写 `_leases` 不写 `_lease_owners` ⇒ heartbeat
        恒 False ⇒ 长 job 300s 后被重派）就靠「唯一入口」防住。
        """
        import secrets

        if mode not in CLAIM_MODES:
            # 纵深防御：队列层 handler 已按白名单拒收，但 store 才是**唯一**的租约写入
            # 入口（B3：手写租约的坑靠入口唯一性防住）——一个写错的模式在这里被
            # 当成独占静默放行，就是「以为在做备份、其实是独占」，必须响亮拒。
            return False, "", "bad_mode"
        with self._lock:
            if job_id in self._frozen:
                # ★ 熔断（§4.1）：任何入口都不再下发（含备份副本）。
                return False, "", "frozen"
            blocked = self.role_blocked(job_id, role)
            if blocked:
                # ★ 归属/停摆闸（2026-09-25，plan/online-offline-role-routing §2.2）：租约
                # 写入的**唯一**入口就在本函数（B3 的入口唯一性契约），所以全部认领面
                # （claim_next / peek+claim / 按 id 直领 / push 派发）天然同源——**别**在
                # 各自的调用点再各判一次，那又是两套会漂的判据（push 腿根本不经过
                # `claim_job`，就是这条的必要性所在）。
                return False, "", blocked
            now = self._now()
            if mode == CLAIM_MODE_BACKUP:
                # 备份副本：不设租约、不动原租约（R2-3），只授权「你的回传不吃 403」。
                self._backup_authorized.add(job_id)
                self._last_heartbeat[job_id] = now  # 仅供观测（谁在跑）
                return True, "", "backup"
            lease = self._leases.get(job_id)
            recovering = lease is not None and lease <= now
            if recovering:
                # 过期租约：回收并记下「谁跑死的」——下一个 worker 该顶上（而不是让它
                # 自领自己跑死的活，那只是把同一个故障重演一遍）。
                self._collect_expired_locked(job_id)
                if self._frozen.get(job_id):
                    return False, "", "frozen"  # ★ 刚达阈（或已冻结）
            if not recovering and job_id in self._claimed:
                # ★ highest 唯一性闸（R1-5）：同一份 job 只能有一个「承诺在跑」的人。
                # 这一条才是「N 个 worker 同拍问询全拿 highest」的真正闸门——epoch 只是
                # 提醒「调度面变过」，不匹配本身不等于有人抢了**这一份**。
                if expected_epoch is not None and int(expected_epoch) != self._epoch:
                    return False, "", "demoted"
                return False, "", "held"
            # 调度面在问询之后变过 ⇒ **在该 job 上重新判一次**（§2.3 ③）：仍是最高才放行。
            if (
                expected_epoch is not None
                and int(expected_epoch) != self._epoch
                and self._job_priority_locked(job_id, exclude_worker=worker_id)
                != PRIORITY_HIGHEST
            ):
                return False, "", "demoted"
            # 避让：上一份**过期死掉**的租约若就是这个请求者跑的，本次不给他（让别的
            # worker 顶上）。身份比对只能在这里做——上面刚完成租约回收，stale 记录此刻
            # 才是最新的；在队列层先判会恒为空（2026-09-18 实测）。
            if avoid_stale_holder and worker_id and self._stale_holders.get(job_id, "") == worker_id:
                return False, "", "stale_holder"
            token = secrets.token_hex(16)
            self._leases[job_id] = now + ttl
            self._lease_owners[job_id] = token
            self._claimed[job_id] = {"worker": worker_id, "at": now}
            if worker_id:
                self._lease_workers[job_id] = worker_id
                self._stale_holders.pop(job_id, None)  # 有人接手了 ⇒ 避让记录使命结束
            self._last_heartbeat[job_id] = now
            self._bump_epoch_locked()
            return True, token, "ok"

    def abandon_job(self, job_id: str, worker_id: str = "") -> bool:
        """`POST /jobs/{id}/abandon`：合法放弃（R1-3）。

        = **release 租约** + 清 claimed/computing/ready 可见性 + **零 reclaim**。
        为什么必须同时 release：job 在 `CLAIM_TTL_SEC=300` 内会被 `claimable_job_ids`
        按「活租约」挡在池外，而租约自然过期又会走 `_collect_expired_locked` ⇒
        `_reclaims+1` ⇒ 三度达 `FREEZE_AFTER_RECLAIMS` 被冻成毒包（合法放弃被读成
        「认领后零回传」）。幂等：没租约/已清过 → 照样返回 True。
        """
        with self._lock:
            self._leases.pop(job_id, None)
            self._lease_owners.pop(job_id, None)
            self._lease_workers.pop(job_id, None)
            self._last_heartbeat.pop(job_id, None)
            self._stale_holders.pop(job_id, None)  # 主动放弃 ≠ 跑死，不该触发避让
            self._drop_commitment_locked(job_id)
            return True

    def _collect_expired_locked(self, job_id: str) -> str:
        """回收过期租约（调用方**必须持锁**）：转 stale 记录 + **毒包计数 +1**。

        为什么计数住这里而不是 `claim()` 里贴一段：过期这件事有三个观测入口
        （`claim` / `lease_worker` / `claimable_job_ids` 的资格判定），谁先看到谁就回收。
        早先只在 `claim` 里贴的写法会被 `/admin/queue` 的轮询（`lease_worker`，控制台
        每秒都在调）抢在前面——计数恒为 0，熔断永远不触发（这就是「判据要有唯一入口」
        在本仓的第三次同一教训）。

        「零回传」只在**结果未落盘且失败标记不在**时计数——已结算的 job 不算毒包。
        """
        dead = self._lease_workers.get(job_id, "")
        self._leases.pop(job_id, None)
        self._lease_owners.pop(job_id, None)
        self._lease_workers.pop(job_id, None)
        # 过期 = 承诺失效：不清的话「有人承诺在跑」会在死 worker 上永远挂着 ⇒
        # 该 job 的优先级永远上不到 highest（唯一性闸的判据）。
        self._drop_commitment_locked(job_id)
        if dead:
            self._stale_holders[job_id] = dead
        jd = self._job_dir(job_id)
        unresolved = not (jd / "result").exists() and not (jd / FAIL_NAME).exists()
        if unresolved:
            n = self._reclaims.get(job_id, 0) + 1
            self._reclaims[job_id] = n
            if n >= FREEZE_AFTER_RECLAIMS and job_id not in self._frozen:
                self._frozen[job_id] = {
                    "reclaims": n,
                    "worker": dead,
                    "ts": self._now(),
                    "announced": False,
                }
        return dead

    def reclaims(self, job_id: str) -> int:
        """「认领后零回传」次数（未发生 → 0）。观测面 + 熔断判据的可查值。"""
        with self._lock:
            return int(self._reclaims.get(job_id, 0))

    def frozen_info(self, job_id: str) -> dict | None:
        """冻结记录（未冻结 → None）。"""
        with self._lock:
            info = self._frozen.get(job_id)
            return dict(info) if info else None

    def frozen_job_ids(self) -> list[str]:
        """已冻结的 job_id（观测面）。"""
        with self._lock:
            return sorted(self._frozen)

    def consume_freeze_announcement(self, job_id: str) -> dict | None:
        """取一次「刚刚落冻」的告警载荷（取过即清；未冻结/已喊过 → None）。

        为什么需要「喊一次」的记账：检测点在 store（它才看得到租约），而告警要有课程名与
        认领者（调用方才知道）。把它做成一次性事件，既不会漏喊，也不会每次轮询重喊。
        """
        with self._lock:
            info = self._frozen.get(job_id)
            if not info or info.get("announced"):
                return None
            info["announced"] = True
            return dict(info)

    def unfreeze(self, job_id: str) -> dict | None:
        """人工解冻（**熔断唯一的可逆口**）：清除冻结与计数 ⇒ job 立即回池可重领。

        重发（`publish`）刻意不走这里：重发不清冻结（见 `_frozen` 注释），否则「重发即重试」
        会把熔断当场抹掉。返回被解冻的记录（本来就未冻结 → None）。
        """
        with self._lock:
            info = self._frozen.pop(job_id, None)
            self._reclaims.pop(job_id, None)
            return dict(info) if info else None

    def stale_holder(self, job_id: str) -> str:
        """上一份**过期**租约的持有人（无 → 空串）。给 `/admin/queue` 观测用。"""
        with self._lock:
            return self._stale_holders.get(job_id, "")

    def lease_worker(self, job_id: str) -> str:
        """当前租约持有人身份（无 → 空串）；同时在租约已过期时走**同一个**回收入口
        （`_collect_expired_locked`：stale 记录 + 毒包计数）——本函数是 `/admin/queue`
        每秒都在调的观测面，若绕开回收，计数会被它抢在前面吞掉。"""
        with self._lock:
            lease = self._leases.get(job_id)
            if lease is None:
                return ""
            if lease <= self._now():
                self._collect_expired_locked(job_id)
                return ""
            return self._lease_workers.get(job_id, "")

    def inflight(self) -> list[str]:
        """持有**未过期**租约的 job_id（在飞）。观测面与「在派发课程数」共用一份口径。"""
        now = self._now()
        with self._lock:
            return [jid for jid, exp in self._leases.items() if exp > now]

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """心跳续租（60s 节奏；H2：非原租者拒续）。
        返回 True = 续租成功；False = job 不存在 / lease_token 不符。

        B3 必杀细节：续租必须改用 CLAIM_TTL_SEC（本函数是 claim/heartbeat/
        _get_status 的**唯一** TTL 来源）——否则死 worker 隐身 30min（LEASE_SEC）。
        """
        with self._lock:
            if not (self._job_dir(job_id) / "manifest.json").exists():
                return False
            owner = self._lease_owners.get(job_id)
            if owner is None or owner != lease_token:
                return False
            now = self._now()
            self._leases[job_id] = now + CLAIM_TTL_SEC
            self._last_heartbeat[job_id] = now
            return True

    def release(self, job_id: str, lease_token: str) -> bool:
        """worker 瞬时失败主动还租约（2026-09-05）：job 立即回池可重领，
        不再干等 LEASE_SEC 过期。H2：仅租约持有人可释放。返回 False = 无租约/非持有人。"""
        with self._lock:
            owner = self._lease_owners.get(job_id)
            if not lease_token or owner != lease_token:
                return False
            self._leases.pop(job_id, None)
            self._lease_owners.pop(job_id, None)
            self._lease_workers.pop(job_id, None)
            self._stale_holders.pop(job_id, None)  # 主动还租约 = 不是「跑死了」，不该避让
            self._last_heartbeat.pop(job_id, None)
            self._drop_commitment_locked(job_id)  # 还租约 = 撒销承诺（见该方法 docstring）
            return True

    def result_token_ok(self, job_id: str, lease_token: str) -> bool:
        """结果回传鉴权（P3b）：有活租约 → 须持有人 token；无租约（过期/释放/
        从未领取/旧 worker）→ 照收。HTTP 层薄调用本函数。"""
        with self._lock:
            if self._leases.get(job_id, 0) > self._now():
                owner = self._lease_owners.get(job_id)
                if bool(lease_token) and owner == lease_token:
                    return True
                # 备份副本（R2-3）：**显式授权**的重复计算 ⇒ 无租约回传也放行。
                # 不这么做的话备份先到就吃 403 ⇒ ProtocolError ⇒ report_job_failure ⇒
                # 训练停腿（409-先于-租约校验只在「结果已落盘」时救场，备份先到救不了）。
                return job_id in self._backup_authorized
            return True

    def mark_completed(self, job_id: str) -> None:
        """训练主循环验收落位后写 job_completed 账本事件（§3.1）。幂等。"""
        with self._lock:
            self._leases.pop(job_id, None)
            self._lease_owners.pop(job_id, None)
            self._last_heartbeat.pop(job_id, None)
            completed_ids = {
                e.get("job_id") for e in self._read_ledger() if e.get("event") == "job_completed"
            }
            if job_id not in completed_ids:
                self._append_ledger({"event": "job_completed", "job_id": job_id, "ts": self._now()})
