"""remote/hub/store.py — `_JobStore` 组合类（S4 第十五刀，从 `hub_server` 搬出）。

第十四刀把 `_JobStore` 的**六个域混入**拆到 `hub/store_*.py`，但组合类本身还留在
`hub_server.py`；本刀把组合类也搬出来，理由不是「对称好看」，而是**下游需要它的名字**：

* `_HubQueue` 的课程表域（`remote/hub/queue_scope.py::add_course`）要**构造**一个 store；
* `_store_of` 的返回类型要**注解**它；
* 而 `remote/hub/*` 不得 import `hub_server`（成环）。

搬完之后是一条向下的 DAG（每一条边都严格向下，`tests/test_remote_dag.py` 逐条对账）：

    hub_server ──▶ hub.queue ──▶ hub.queue_scope ──▶ hub.store ──▶ hub.store_* / hub.auth

`hub_server` 仍 re-export `_JobStore` —— 约 20 个测试、`e2e/`、`remote/smoke_loopback.py`
与 `remote/tunnel_ab_probe.py` 都从 `remote.hub_server` 取这个名字（**名字是契约，位置不是**）。
"""

from __future__ import annotations

from pathlib import Path

from remote.hub.auth import _AuthGuard
from remote.hub.store_leases import LeaseMixin
from remote.hub.store_ledger import LedgerMixin
from remote.hub.store_offline import OfflineRoundsMixin
from remote.hub.store_results import ResultsMixin
from remote.hub.store_scheduling import SchedulingMixin
from remote.hub.store_wire import WireMeterMixin

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
# `tests/test_hub_job_store_split.py` 钉住）；这里按「越底层越靠后」排在便于读。



class _JobStore(
    LedgerMixin,
    WireMeterMixin,
    SchedulingMixin,
    LeaseMixin,
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
    不随账本线性变慢）。"""

    def __init__(self, job_root: str | Path, jsonl_path: str | Path, now_fn=None) -> None:
        self.job_root = Path(job_root)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path)
        # 域私有状态：**随所属域进了各自混入**（第十四刀）——四个有状态的域各有一个钩子，
        # 逐个显式调用，理由见本类上方那段「为什么不用 super() 链」。
        # （`store_results` / `store_offline` **没有钩子**：它们真的没有常驻状态——
        # 守卫对它们改成正面断言「方法体里零 `self.X = …` 赋值」，不造 `pass` 空钩。）
        LedgerMixin._init_ledger(self)
        WireMeterMixin._init_wire(self)
        SchedulingMixin._init_scheduling(self)
        LeaseMixin._init_leases(self)
        # 鉴权面（`_AuthGuard`）：进程级一份——多课程单 hub 下不按课程各算一套计数。
        # 它同时建了**本 store 唯一的那把 `_lock`** ⇒ 组合类不再自建一把（旧的
        # `self._lock = Lock()` 本来就会被这一行覆盖掉：一个被丢弃的锁对象是下次读的人
        # 的陷阱，第十四刀顺手删了）。
        _AuthGuard.__init__(self, now_fn)
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
