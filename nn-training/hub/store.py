"""hub/store.py — `_JobStore` 组合类（S4 第十五刀，从 `hub_server` 搬出）。

第十四刀把 `_JobStore` 的**六个域混入**拆到 `hub/store_*.py`，但组合类本身还留在
`hub_server.py`；本刀把组合类也搬出来，理由不是「对称好看」，而是**下游需要它的名字**：

* `_HubQueue` 的课程表域（`hub/queue_scope.py::add_course`）要**构造**一个 store；
* `_store_of` 的返回类型要**注解**它；
* 而 `hub/*` 不得 import `hub_server`（成环）。

搬完之后是一条向下的 DAG（每一条边都严格向下，`tests/test_remote_dag.py` 逐条对账）：

    hub_server ──▶ hub.queue ──▶ hub.queue_scope ──▶ hub.store ──▶ hub.store_* / hub.auth

`hub_server` 仍 re-export `_JobStore` —— 约 20 个测试、`e2e/`、`hub/smoke_loopback.py`
与 `hub/tunnel_ab_probe.py` 都从 `hub.server` 取这个名字（**名字是契约，位置不是**）。
"""

from __future__ import annotations

from pathlib import Path

from common.protocol import CLAIM_MODE_EXCLUSIVE, CLAIM_TTL_SEC, ROLE_ONLINE
from hub.auth import _AuthGuard
from hub.store_leases import ClaimOutcome, LeaseBook
from hub.store_ledger import Ledger
from hub.store_offline import OfflineRoundsMixin
from hub.store_results import ResultsMixin
from hub.store_scheduling import Scheduler
from hub.store_wire import WireMeterMixin

# ───────────────── 状态类：六个域混入 + 组合（S4 第十四刀）─────────────────
#
# `_JobStore` **按域拆成六个混入**，而**不是**拆成各自持锁的协作对象——三条实测判据：
#
# 1. **一把锁是类的不变式**：30/49 个方法在同一把 `_lock` 下（`_*_locked` 后缀标的就是
#    临界区内的那半）。协作对象各持一把锁 = **换语义**（并发行为不同），不满足「零行为
#    变化」；
# 2. **跨域互调是常态**（37/49）：`_claim_locked` → `_job_priority_locked` /
#    `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked` …
#    混入把它们留在 `self.X` 上 ⇒ **零 seam**（既不用迁移也不用注入）；
# 3. **tests 直接读私有属性**（`store._leases` / `._lease_owners` / `._stale_holders` /
#    `._claimed` / `._backup_authorized` / `._last_heartbeat` / `halt_workers`，20+ 处
#    断言）——协作对象会让这些**全部改路**；混入是同一个对象 ⇒ 一行测试都不用改。
#
# 代价（明确记在案）：**状态声明分散到六个 `_init_*` 里**。所以组合类的 `__init__` 把六次
# 调用**逐个显式写出来**，而不是走 `super().__init__()` 链：谁初始化了什么、什么顺序，
# 只有读这一个地方才对得齐；MRO 链会让顺序隐式化（本仓先例：`_AuthGuard.__init__` 也是
# 被显式调用的）。
#
# 混入顺序只影响同名的解析，而六个混入**零重名**（守卫
# `tests/hub/test_hub_job_store_split.py` 钉住）；这里按「越底层越靠后」排在便于读。



class _JobStore(
    WireMeterMixin,
    ResultsMixin,
    OfflineRoundsMixin,
    _AuthGuard,
):
    """磁盘 job 存储 + 内存租约状态。

    事实来源 = 磁盘（jsonl 账本 + job 目录）；内存只存租约（重启即丢，符合
    D8「重启后 job_pending 未完成的重发、job_completed 跳过」）。

    H2（review-hy）：领取时下发 `lease_token`（随机串），心跳/结果回传必须携带——
    hub 校验后才续租/收结果，杜绝「任何持 token 者都能续租/抢租约」的多 worker 竞态。
    H6（review-hy）：jsonl 增量读——记住上次文件 size，只解析新增行（长跑轮询
    不随账本线性变慢）。

    B7-P1/P2：调度与租约是**协作者**（`self.sched` / `self.leases`）。两个混入侧的
    `Any` 声明只服务“跨簇引用”，组合类这里给出具体类型，转发壳的返回值才不会被
    mypy 看成 `Any`。
    """

    #: 账本协作者（`hub/store_ledger.py::Ledger`）
    ledger: Ledger
    #: 调度协作者（`hub/store_scheduling.py::Scheduler`）
    sched: Scheduler
    #: 租约协作者（`hub/store_leases.py::LeaseBook`）
    leases: LeaseBook

    def __init__(self, job_root: str | Path, jsonl_path: str | Path, now_fn=None) -> None:
        self.job_root = Path(job_root)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path)
        # 域私有状态：**随所属域进了各自混入**（第十四刀）——四个有状态的域各有一个钩子，
        # 逐个显式调用，理由见本类上方那段「为什么不用 super() 链」。
        # （`store_results` / `store_offline` **没有钩子**：它们真的没有常驻状态——
        # 守卫对它们改成正面断言「方法体里零 `self.X = …` 赋值」，不造 `pass` 空钩。）
        WireMeterMixin._init_wire(self)
        # 鉴权面（`_AuthGuard`）：进程级一份——多课程单 hub 下不按课程各算一套计数。
        # 它同时建了**本 store 唯一的那把 `_lock`** ⇒ 组合类不再自建一把（旧的
        # `self._lock = Lock()` 本来就会被这一行覆盖掉：一个被丢弃的锁对象是下次读的人
        # 的陷阱，第十四刀顺手删了）。
        _AuthGuard.__init__(self, now_fn)
        # B7-P1/P2：调度面与租约面从混入切成协作者。锁 / 时钟 / 账本目录全部**注入同一份**
        # （L1/L2/L4）——协作者不自建锁，也不读组合类的 `_now`。
        # 两张跨协作者共享的表由组合根建、双方各持**同一引用**（谁都不留副本）：
        # `Scheduler` 读它们判事实、`LeaseBook` 写它们续租（L5：同一临界区不允许两把锁）。
        lease_workers: dict[str, str] = {}
        last_heartbeat: dict[str, float] = {}
        self.ledger = Ledger(
            lock=self._lock,
            clock=self._now,
            job_root=self.job_root,
            jsonl_path=self.jsonl_path,
            sched=Scheduler(
                lock=self._lock,
                clock=self._now,
                job_dir=self._job_dir,
                lease_workers=lease_workers,
                last_heartbeat=last_heartbeat,
            ),
        )
        self.sched = self.ledger.sched
        self.leases = LeaseBook(
            lock=self._lock,
            clock=self._now,
            job_dir=self.ledger._job_dir,
            sched=self.sched,
            ledger=self.ledger,
            lease_workers=lease_workers,
            last_heartbeat=last_heartbeat,
        )
        # 唯一的两相引用：Ledger 先构造（它没有反向依赖），LeaseBook 建好后再回填。
        self.ledger.leases = self.leases
        # ---- 进程级状态（多课程单 hub 下 `_HubQueue` **借**的就是这一份）----
        # 它们不属于任何域：job 账本每课程一份，而「停机达令 / worker 登记」是**进程**概念
        # （借用关系写在 `_HubQueue` docstring ③）。
        #: 云端停机标志（§386：停机命令随任务同发；云机先试停机、停不掉照常干活）。
        #: 置位后 /jobs/peek 响应带 halt:true；由 console 经 /admin/workers/{halt,resume}
        #: 控制；hub 重启即复位（volatile）。停机**不拦任务分发**。
        self.halt_workers = False
        #: worker 登记表：worker_id -> last_seen（秒）。2026-09-22 P3 竞速退役后，本表
        #: 的**唯一**生产消费者是避让链 `active_worker_count()`（`may_avoid_stale_holder`）。
        #: 隧道回源把全流量归成 127.0.0.1 ⇒ 源 IP 分不出 worker，必须由 worker 自报身份。
        self._workers: dict[str, float] = {}

    def note_worker(self, worker_id: str) -> None:
        """登记一次 worker 轮询（peek / priority 入口）。空 id 不记（无身份无法去重计数）。"""
        wid = (worker_id or "").strip()
        if not wid:
            return
        with self._lock:
            self._workers[wid] = self._now()

    # ---- B7-P1：调度面的**转发壳**（跨簇调用点与 `hub` 对外面逐字不变） ----
    # 名字与签名是契约：`queue_store_face` 的 `_params` 对账与 `tests/hub/test_hub_queue_split.py`
    # 把 `start_job` / `set_ready` 冻在 `FACADE_SAME_SIG` 里；本壳只是把它们交给 `self.sched`。
    def _bump_epoch_locked(self) -> None:
        self.sched._bump_epoch_locked()

    def scheduling_epoch(self) -> int:
        return int(self.sched.scheduling_epoch())

    def start_job(self, job_id: str, worker_id: str = "") -> bool:
        return bool(self.sched.start_job(job_id, worker_id))

    def set_ready(self, job_id: str, worker_id: str = "") -> bool:
        return bool(self.sched.set_ready(job_id, worker_id))

    def scheduling_facts(self, job_id: str, *, exclude_worker: str = "") -> dict:
        facts: dict = self.sched.scheduling_facts(job_id, exclude_worker=exclude_worker)
        return facts

    def _facts_locked(self, job_id: str, *, exclude_worker: str = "") -> dict:
        facts: dict = self.sched._facts_locked(job_id, exclude_worker=exclude_worker)
        return facts

    def _job_priority_locked(self, job_id: str, *, exclude_worker: str = "") -> str:
        return str(self.sched._job_priority_locked(job_id, exclude_worker=exclude_worker))

    def priority_for(self, job_id: str, *, exclude_worker: str = "") -> tuple[str, str]:
        out: tuple[str, str] = self.sched.priority_for(job_id, exclude_worker=exclude_worker)
        return out

    def _drop_commitment_locked(self, job_id: str) -> None:
        self.sched._drop_commitment_locked(job_id)

    # ---- B7-P4b：账本面的**转发壳**（15 个口；状态与实现住 `self.ledger`） ----
    def _job_dir(self, job_id: str) -> Path:
        """转发到 `Ledger._job_dir`（B7-P4b）。"""
        return self.ledger._job_dir(job_id)

    def ledger_offset(self) -> int:
        """转发到 `Ledger.ledger_offset`（B7-P4b）。"""
        return self.ledger.ledger_offset()

    def _read_ledger(self) -> list[dict]:
        """转发到 `Ledger._read_ledger`（B7-P4b）。"""
        return self.ledger._read_ledger()

    def _read_ledger_from(self, out: list[dict], off: int) -> int:
        """转发到 `Ledger._read_ledger_from`（B7-P4b）。"""
        return self.ledger._read_ledger_from(out, off)

    def _append_ledger(self, event: dict) -> None:
        """转发到 `Ledger._append_ledger`（B7-P4b）。"""
        self.ledger._append_ledger(event)

    def ledger_net_state(self, job_id: str) -> str:
        """转发到 `Ledger.ledger_net_state`（B7-P4b）。"""
        return self.ledger.ledger_net_state(job_id)

    def _unfinished_pending(self) -> list[tuple[str, float]]:
        """转发到 `Ledger._unfinished_pending`（B7-P4b）。"""
        return self.ledger._unfinished_pending()

    def claimable_job_ids(self) -> list[str]:
        """转发到 `Ledger.claimable_job_ids`（B7-P4b）。"""
        return self.ledger.claimable_job_ids()

    def inflight_job_ids(self, *, not_held_by: str = '') -> list[str]:
        """转发到 `Ledger.inflight_job_ids`（B7-P4b）。"""
        return self.ledger.inflight_job_ids(not_held_by=not_held_by)

    def _cancel_job_locked(self, job_id: str, *, reason: str) -> None:
        """转发到 `Ledger._cancel_job_locked`（B7-P4b）。"""
        self.ledger._cancel_job_locked(job_id, reason=reason)

    def is_cancelled(self, job_id: str) -> bool:
        """转发到 `Ledger.is_cancelled`（B7-P4b）。"""
        return self.ledger.is_cancelled(job_id)

    def cancel_unsettled_jobs(self, *, reason: str = 'mode-switch') -> list[str]:
        """转发到 `Ledger.cancel_unsettled_jobs`（B7-P4b）。"""
        return self.ledger.cancel_unsettled_jobs(reason=reason)

    def cancel_job(self, job_id: str, *, reason: str = 'manual') -> dict:
        """转发到 `Ledger.cancel_job`（B7-P4b）。"""
        return self.ledger.cancel_job(job_id, reason=reason)

    def publish(self, job_id: str, manifest: dict, payload_zip: bytes) -> None:
        """转发到 `Ledger.publish`（B7-P4b）。"""
        self.ledger.publish(job_id, manifest, payload_zip)

    def job_failure(self, job_id: str) -> dict | None:
        """转发到 `Ledger.job_failure`（B7-P4b）。"""
        return self.ledger.job_failure(job_id)

    def get_result(self, job_id: str) -> dict | None:
        """转发到 `Ledger.get_result`（B7-P4b）。"""
        return self.ledger.get_result(job_id)

    # ---- B7-P2：租约面的**转发壳**（37 个方法与 5 个辅助读数） ----
    # 跨簇调用点与 `hub` 对外面逐字不变；状态只在 `self.leases` 上，组合类不存副本。
    def role_blocked(self, job_id: str, role: str) -> str:
        """转发到 `LeaseBook.role_blocked`（B7-P2）。"""
        return self.leases.role_blocked(job_id, role)

    def hold_blocked(self) -> str:
        """转发到 `LeaseBook.hold_blocked`（B7-P2）。"""
        return self.leases.hold_blocked()

    def _hold_stale_sec(self) -> float:
        """转发到 `LeaseBook._hold_stale_sec`（B7-P2）。"""
        return self.leases._hold_stale_sec()

    def note_job_progress(self, job_id: str, at: float | None = None) -> None:
        """转发到 `LeaseBook.note_job_progress`（B7-P2）。"""
        self.leases.note_job_progress(job_id, at)

    def job_progress_at(self, job_id: str) -> float:
        """转发到 `LeaseBook.job_progress_at`（B7-P2）。"""
        return self.leases.job_progress_at(job_id)

    def _bc_progress_stale_sec(self) -> float:
        """转发到 `LeaseBook._bc_progress_stale_sec`（B7-P2）。"""
        return self.leases._bc_progress_stale_sec()

    def bc_drain_of(self, worker_id: str) -> str:
        """转发到 `LeaseBook.bc_drain_of`（B7-P2）。"""
        return self.leases.bc_drain_of(worker_id)

    def _bc_lock_path(self) -> Path:
        """转发到 `LeaseBook._bc_lock_path`（B7-P2）。"""
        return self.leases._bc_lock_path()

    def bc_exclusive_owner(self) -> tuple[str, str]:
        """转发到 `LeaseBook.bc_exclusive_owner`（B7-P2）。"""
        return self.leases.bc_exclusive_owner()

    def _bc_lock_write(self, job_id: str, worker_id: str) -> None:
        """转发到 `LeaseBook._bc_lock_write`（B7-P2）。"""
        self.leases._bc_lock_write(job_id, worker_id)

    def _bc_lock_touch(self, job_id: str) -> None:
        """转发到 `LeaseBook._bc_lock_touch`（B7-P2）。"""
        self.leases._bc_lock_touch(job_id)

    def _bc_lock_clear(self, job_id: str) -> None:
        """转发到 `LeaseBook._bc_lock_clear`（B7-P2）。"""
        self.leases._bc_lock_clear(job_id)

    def release_bc_exclusive(self) -> dict:
        """转发到 `LeaseBook.release_bc_exclusive`（B7-P2）。"""
        return self.leases.release_bc_exclusive()

    def job_kind(self, job_id: str) -> str:
        """转发到 `LeaseBook.job_kind`（B7-P2）。"""
        return self.leases.job_kind(job_id)

    def job_role(self, job_id: str) -> str:
        """转发到 `LeaseBook.job_role`（B7-P2）。"""
        return self.leases.job_role(job_id)

    def claim(self, job_id: str, ttl: float = CLAIM_TTL_SEC, worker_id: str = '', avoid_stale_holder: bool = False, mode: str | None = None, expected_epoch: int | None = None, role: str = ROLE_ONLINE) -> str | None:
        """转发到 `LeaseBook.claim`（B7-P2）。"""
        return self.leases.claim(
            job_id,
            ttl=ttl,
            worker_id=worker_id,
            avoid_stale_holder=avoid_stale_holder,
            mode=mode,
            expected_epoch=expected_epoch,
            role=role,
        )

    def claim_outcome(self, job_id: str, *, mode: str = CLAIM_MODE_EXCLUSIVE, worker_id: str = '', avoid_stale_holder: bool = False, expected_epoch: int | None = None, role: str = ROLE_ONLINE) -> ClaimOutcome:
        """转发到 `LeaseBook.claim_outcome`（B7-P2）。"""
        return self.leases.claim_outcome(job_id, mode=mode, worker_id=worker_id, avoid_stale_holder=avoid_stale_holder, expected_epoch=expected_epoch, role=role)

    def _lease_state(self, job_id: str, now: float) -> str:
        """转发到 `LeaseBook._lease_state`（B7-P2）。"""
        return self.leases._lease_state(job_id, now)

    def lease_state(self, job_id: str, now: float | None = None) -> str:
        """转发到 `LeaseBook.lease_state`（B7-P2）。"""
        return self.leases.lease_state(job_id, now)

    def _lease_held(self, job_id: str, now: float) -> bool:
        """转发到 `LeaseBook._lease_held`（B7-P2）。"""
        return self.leases._lease_held(job_id, now)

    def _claim_locked(self, job_id: str, *, ttl: float, mode: str, worker_id: str, avoid_stale_holder: bool, expected_epoch: int | None, role: str = ROLE_ONLINE) -> tuple[bool, str, str]:
        """转发到 `LeaseBook._claim_locked`（B7-P2）。"""
        return self.leases._claim_locked(job_id, ttl=ttl, mode=mode, worker_id=worker_id, avoid_stale_holder=avoid_stale_holder, expected_epoch=expected_epoch, role=role)

    def abandon_job(self, job_id: str, worker_id: str = '') -> bool:
        """转发到 `LeaseBook.abandon_job`（B7-P2）。"""
        return self.leases.abandon_job(job_id, worker_id)

    def _collect_expired_locked(self, job_id: str, *, orphan: bool = False) -> str:
        """转发到 `LeaseBook._collect_expired_locked`（B7-P2）。"""
        return self.leases._collect_expired_locked(job_id, orphan=orphan)

    def reclaims(self, job_id: str) -> int:
        """转发到 `LeaseBook.reclaims`（B7-P2）。"""
        return self.leases.reclaims(job_id)

    def frozen_info(self, job_id: str) -> dict | None:
        """转发到 `LeaseBook.frozen_info`（B7-P2）。"""
        return self.leases.frozen_info(job_id)

    def frozen_job_ids(self) -> list[str]:
        """转发到 `LeaseBook.frozen_job_ids`（B7-P2）。"""
        return self.leases.frozen_job_ids()

    def consume_freeze_announcement(self, job_id: str) -> dict | None:
        """转发到 `LeaseBook.consume_freeze_announcement`（B7-P2）。"""
        return self.leases.consume_freeze_announcement(job_id)

    def unfreeze(self, job_id: str) -> dict | None:
        """转发到 `LeaseBook.unfreeze`（B7-P2）。"""
        return self.leases.unfreeze(job_id)

    def stale_holder(self, job_id: str) -> str:
        """转发到 `LeaseBook.stale_holder`（B7-P2）。"""
        return self.leases.stale_holder(job_id)

    def has_lease(self, job_id: str) -> bool:
        """转发到 `LeaseBook.has_lease`（B7-P2）。"""
        return self.leases.has_lease(job_id)

    def lease_expiry(self, job_id: str) -> float | None:
        """转发到 `LeaseBook.lease_expiry`（B7-P2）。"""
        return self.leases.lease_expiry(job_id)

    def heartbeat_at(self, job_id: str) -> float | None:
        """转发到 `LeaseBook.heartbeat_at`（B7-P2）。"""
        return self.leases.heartbeat_at(job_id)

    def lease_worker(self, job_id: str) -> str:
        """转发到 `LeaseBook.lease_worker`（B7-P2）。"""
        return self.leases.lease_worker(job_id)

    def inflight(self) -> list[str]:
        """转发到 `LeaseBook.inflight`（B7-P2）。"""
        return self.leases.inflight()

    def reap_dead_leases(self) -> list[str]:
        """转发到 `LeaseBook.reap_dead_leases`（B7-P2）。"""
        return self.leases.reap_dead_leases()

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """转发到 `LeaseBook.heartbeat`（B7-P2）。"""
        return self.leases.heartbeat(job_id, lease_token)

    def release(self, job_id: str, lease_token: str) -> bool:
        """转发到 `LeaseBook.release`（B7-P2）。"""
        return self.leases.release(job_id, lease_token)

    def result_token_ok(self, job_id: str, lease_token: str) -> bool:
        """转发到 `LeaseBook.result_token_ok`（B7-P2）。"""
        return self.leases.result_token_ok(job_id, lease_token)

    def mark_completed(self, job_id: str) -> None:
        """转发到 `LeaseBook.mark_completed`（B7-P2）。"""
        self.leases.mark_completed(job_id)

