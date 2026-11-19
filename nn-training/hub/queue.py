"""hub/queue.py — `_HubQueue` 组合类：多课程单 hub 的调度面。

S4 第十五刀把 1033 行 / 76 个方法的 `_HubQueue` 按**域**拆成七个混入（`hub/queue_*.py`），
组合类只剩 docstring、一个类常量与 `__init__`：

```
class _HubQueue(QueueScopeMixin, QueueDiscoverMixin, QueueAuthMixin, QueueClaimsMixin,
                QueueResumeMixin, QueueObserveMixin, QueueStoreFaceMixin, _AuthGuard)
  queue_scope       课程表 · 归属路由 · 停机达令 · worker 登记（状态的主人）
  queue_discover    自动发现三相：扫盘 / 开课标记闸 / 「在训」判据
  queue_auth        鉴权面四覆写 + `halt_workers` 旧名的委派
  queue_claims      派发与认领：轮转挑选 · peek · 熔断告警 · 合法放弃
  queue_resume      续跑锚点 · 离线段补传产物 · 课程路径
  queue_observe     观测面（只读）· job 路径解析 · 哨兵根
  queue_offline     离线任务清单 + 领取租约（第八个域，2026-09-25）
  queue_store_face  job 作用域门面：18 个与 `_JobStore` 同名同签名的方法
```

## 为什么是「混入」而不是「协作对象」（与第十四刀同源，三条实测判据）

1. **一把锁是类的不变式**：`_lock` 有 5 个直接读者（`_serves_course` / `active_worker_count` /
   `claim_job` / `claim_next` / `note_worker`），而它就是 `_AuthGuard.__init__` 建的那一把。
   协作对象各持一把锁 = **换语义**（并发行为不同），不满足「零行为变化」；
2. **跨域互调是常态**：`claim_next` → `discover` / `_serves_course` /
   `active_worker_count` / `_announce_freeze`；`queue_state` → `halt_of` /
   `active_courses` / `all_halted` / `active_worker_count`；
   `resume_anchor` → `resume_sources`…混入把它们留在 `self.X` 上 ⇒ **零 seam**；
3. **tests 直接读私有状态**（`hub._stores` / `hub._order` / `hub._halts` /
   `hub._locate_cache` / `hub._workers` / `hub._discover_last`，多处断言）——协作对象会让这些
   **全部改路**；混入是同一个对象 ⇒ **一行测试都不用改**。

## 为什么 `__init__` 不拆成 `_init_<域>` 钩子（**本条与第十四刀刻意不同**）

第十四刀把状态声明拆到四个 `_init_*` 钩子里，理由是那里的状态分布在 900 行里、四个域各自
独立。这里**不拆**，两条理由：

* `__init__` 只有 44 行，是一块**连续**的声明，读一遍就对得齐 —— 拆开反而要跳七个文件；
* 几处状态**互相咬合**：`_solo` 决定 `_now`（`self._now = self._solo._now if self._solo is not
  None else (now_fn or time.time)`，时钟必须与单课程 store 同源，否则 claimed 标记的时间戳
  会混入真实墙钟）、`_discover_root` 存在与否决定 `_discover_last` 的初值、`_adopt_solo`
  还要在运行期把 `_halt_default` / `_workers` / `_auth_fail` 从 store 搬到 `self` 上。
  按域切这三处只会把一条直线扯成跳转。

代价照旧明确记在案：七个混入各自的类体里，**状态一律是裸注解**（`_stores: dict[str, _JobStore]`，
无值）—— 组合类的 `__init__` 是这些字段**唯一**的带值声明点。守卫
`tests/hub/test_hub_queue_split.py` 钉住这条，并逐字段对账「谁写它」。

## 依赖方向

`hub.queue → {hub.queue_* 七混入, hub.auth, hub.store}`（严格向下）。
`hub/server.py` 是整个 hub 的组装 + HTTP 面，从上面拿 `_HubQueue` 并 re-export
（`hs._HubQueue` 是约 10 个测试与 e2e 的取名字入口——**名字是契约，位置不是**）。
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from threading import Lock

from hub.auth import _AuthGuard
from hub.course_table import CourseTable
from hub.queue_auth import QueueAuthMixin
from hub.queue_claims import QueueClaimsMixin
from hub.queue_discover import QueueDiscoverMixin
from hub.queue_observe import QueueObserveMixin
from hub.queue_offline import QueueOfflineMixin
from hub.queue_resume import QueueResumeMixin
from hub.queue_scope import QueueScopeMixin
from hub.queue_store_face import QueueStoreFaceMixin
from hub.store import _JobStore
from hub.worker_board import WorkerBoard

# `DISCOVER_FRESH_SEC` / `DISCOVER_SCAN_MIN_SEC` 两个类常量随发现簇搬进 `queue_discover`，
# 本类经 MRO 继承（`hub.DISCOVER_SCAN_MIN_SEC` 这类实例访问照旧）。
# ba注意 `DISCOVER_SCAN_SEC`（后台兜底线程的节拍，启动参数用）是**另一件事**，留在 hub_server。


# ------------------------------------------------------------------ 组合


class _HubQueue(
    QueueScopeMixin,
    QueueDiscoverMixin,
    QueueAuthMixin,
    QueueClaimsMixin,
    QueueResumeMixin,
    QueueObserveMixin,
    QueueOfflineMixin,
    QueueStoreFaceMixin,
    _AuthGuard,
):
    """多课程单 hub 的调度面（2026-09-18 用户指令：一个进程服务所有并行课程）。

    形状：**一个进程托管 N 份 `_JobStore`**，而磁盘布局逐字节不变（每课程仍是
    `tmp/<course>/remote-jobs` + `tmp/<course>/training_log.jsonl`）。这是本设计的
    核心取舍：多课程只是「同一个进程里多挂几份账本」，不是换一套磁盘契约 —— 于是
    既有工具、既有 66 个单课程用例、`tmp/<course>` 约定全部照旧。

    本类负责三件跨课程的事：

      ① **路由**：任意 `/jobs/{id}/...` 先按 job_id 找归属课程。job_id 的幂等键含
         `runId`，而 runId 是每进程随机的 8 字节 hex（`trainer/dispatch_queue.py::RUN_ID`）⇒ 跨课程
         天然不撞；判据 = 「哪个课程的 job 目录里真有它」，命中即入缓存（一次 fs 探测）。
      ② **队形**：每课程一条 FIFO（`claimable_job_ids` 本来就是发布序）；派发时
         **跨课程轮转**（`protocol.rotation_order`）—— 否则一门积压 20 轮的课会把
         其它课程饿死（5 课程机群退化成单课程机群）。
      ③ **进程级状态**（停机达令 / 鉴权闭锁 / worker 登记）：单课程时**借**那一份
         `_JobStore` 的，多课程时用自己的。这不是洁癖 —— 既有用例会在 store 上预热
         `_auth_blocked_until` / 摆 `_workers` 再发 HTTP 请求，若本类另有
         副本，那些预热就不生效了。

    对外的 job 作用域方法**与 `_JobStore` 同名同签名**（内部先解析归属），所以 handler
    侧只需把 `self.hub.` 换成 `self.hub.`，单课程行为逐字节等价。
    """

    #: epoch POST 体上限（与 `_JobStore` 同源，不留第二份魔数）
    #: 自动发现时判定「课程目录是不是活的」的新鲜窗口（秒）。
    BC_EPOCH_BODY_MAX = _JobStore.BC_EPOCH_BODY_MAX

    # `__init__` 的形参缺省值要在**类体作用域**里解析，所以要在这里取一次名字
    # （常量本身只有一份，住 `queue_discover`——与 `BC_EPOCH_BODY_MAX` 同一个做法）。
    DISCOVER_FRESH_SEC = QueueDiscoverMixin.DISCOVER_FRESH_SEC

    def __init__(
        self,
        stores: dict[str, _JobStore],
        order: list[str] | None = None,
        ignored_modes=None,
        now_fn=None,
        discover_root=None,
        discover_fresh_sec: float = DISCOVER_FRESH_SEC,
    ) -> None:
        # B7-P3：课程表 / worker 登记与停机达令切成两个**有状态协作者**。组合类保留同名
        # 属性（见本类末尾），既有 `self._stores[...]` 调用点与测试逐字不变。
        # 名字用 `course_table` 而不是 `courses`：后者是既有的公开读法 `courses()`（方法）。
        self.course_table: CourseTable[_JobStore] = CourseTable(stores, order)
        self.board = WorkerBoard()
        #: 自动发现根（`--traj-root`）；None = 关（`--discover` 未给，零开销零行为变化）
        self._discover_root: Path | None = Path(discover_root) if discover_root else None
        self._discover_fresh = float(discover_fresh_sec)
        self._discover_last = 0.0
        #: 死租约清扫的节流基准（plan/dead-lease-sweep）：-inf ⇒ 首次调用必扫。
        self._sweep_at = float("-inf")
        #: 「跳过未开课课程」的告警去重集（每门课只喊一次，不刷屏）。
        self._no_marker_warned: set[str] = set()
        #: 启动参数里带过模式段的课程（`--course a=offline`；★M4b/P1-3：WARN + 忽略，
        #: 只在 `/admin/courses` 行上留一个 `mode_ignored` 标记供排障）。
        self._mode_ignored: set[str] = {str(c) for c in (ignored_modes or ())}
        # hold 镜像同步到 store（★M1b：`__init__` 末段的 `_sync_hold` 循环——同步要时钟
        # （`hold_of`），必须在 `self._now` 赋值之后）。
        #: 课程 -> 最近被 `peek_jobs` 返回候选的时刻（秒）。多课程预取窗口的**观测面**状态
        #: （2026-10-02，plan/course-pill-precision §4.1）：只被 `/admin/queue` 读，不参与
        #: 任何派发判据；重启即清（窗口本来就是过程量，与租约同口径）。
        self._peeked: dict[str, float] = {}
        #: 自动发现的扫描闸（`_discover_last` 初值 0 ⇒ 首次调用必扫）
        if self._discover_root is not None:
            self._discover_last = float("-inf")
        #: **离线租约**（课程 → `{token, worker_id, at, expires_at}`）：进程内、惰性过期。
        #: 为什么住实例而不是模块（与 `_TASK_PACK_TRIGGERS` 不同）：生产一个进程一个 hub 两者等价，
        #: 而单测里每个 hub 各自干净（共享就得分用例清账）。重启即清——与触发账本同口径：
        #: 最坏情形由回传侧 `(run_id, it)` 首写幂等兜底（plan §3.2）。
        self._leases: dict[str, dict] = {}
        self._lease_lock = Lock()
        #: **派发状态**（课程 → 记录；plan/auto-offline-handoff §3.2/§3.8，2026-10-03）：
        #: `pinned` / claim 翻的 offline / 完成锚（包 sha）。与 `_leases` 相反——**落盘**
        #: （`<课>/offline-dispatch.json`）：hub 重启回启动参数会把正在 TPU 上跑的课解封队列
        #: （在线云机把它的 job 领走 = 同一份活两处跑，数据损坏级）。读写见 `queue_offline`。
        self._dispatch: dict[str, dict] = {}
        self._dispatch_lock = Lock()
        #: 离线**盘**报名表：disk_id -> last_seen（秒）。★ 为什么单独一张表：跑
        #: `battle.offline.ipynb` 的机器**不碰队列**（取包链全在 `/offline/*` 上），它的身份
        #: 只能在那一面被看到；而「本环境有没有离线盘」这个读数此前恒为空（审计 §4-L3：
        #: `CFG["offline_worker"]` 全仓只有测试设过）。与 `_leases` 同口径：进程内、只做观测
        #: （重启即清，最坏情形由回传侧首写幂等兜底）。
        self._offline_disks: dict[str, float] = {}
        #: 独立锁：`offline_disk_readout` 会被 `/admin/queue` 调到，而那条路不持 `_lease_lock`
        #: 也不该持 `_lock`（观测面不许和调度临界区互等）。
        self._disk_lock = Lock()
        #: **worker 预取状态上报**（worker_id → `{held, dl, at}`；plan/dashboard-ppo-live-rows）：
        #: 「软持有里哪些下好了 / 哪些正在下」是 worker 本地事实，hub 只做**中转站**。
        #: 与 `_offline_disks` 同口径：进程内、惰性 TTL、重启即清、**绝不进任何调度判据**。
        self._worker_prefetch: dict[str, dict] = {}
        #: 独立锁：`worker_prefetch_readout` 会被 `/admin/queue` 调到（同 `_disk_lock` 的理由——
        #: 观测面不许和调度临界区互等）。
        self._pf_lock = Lock()
        #: **云机错误上报的 ring**（plan/component-error-registry §4.3-S3b，2026-10-16）：
        #: 云机上「只在云机」的失败（pull 的机器级停滞/瞬时失败、离线段的失败）在这儿**中继**
        #: 一拍，等控制台的 10s 节拍来拉。与 `_worker_prefetch` 同口径：进程内、TTL 剔除、
        #: 重启即清、**绝不进任何派发判据**。为什么不做权威落盘：持久化只该有一个地方管
        #: （控制台的错误账本），而 hub 的 import 面受纪律约束（顶层禁三方 import）。
        self._worker_errors: deque[dict] = deque()
        #: 独立锁（同 `_pf_lock` 的理由）。
        self._we_lock = Lock()
        _AuthGuard.__init__(self, now_fn)
        # 时钟与单课程 store 同源（测试注入的假时钟必须一致，否则 claimed 标记的时间戳
        # 会混入真实墙钟）。
        self._now = self._solo._now if self._solo is not None else (now_fn or time.time)
        # 盘上的 hold 镜像同步（★M1b）：必须排在 `self._now` **之后** —— 盘上有 hold 时
        # `_dispatch_load` 要打恢复宽限（`hold-restored`），那需要时钟。旧顺序下「带 hold
        # 重启」= 构造期 AttributeError（hub 直接起不来）。
        # ★M4b：`_modes` 与 `dispatch_effective_mode` 已随「课程无模式」删除。
        for _c in self._order:
            self._sync_hold(_c)

    # ---- B7-P3：课程表 / worker 板的**转发属性**（状态只在协作者上） ----
    # 八域混入仍写 `self._stores[...]` / `self._cursor = ...`：这些属性的 getter/setter 把
    # 每一次读写落到 `self.course_table` / `self.board`，组合类自己**不存副本**。写口保留是为了
    # `_adopt_solo` / `drop_course` 的整表替换语义（与旧实现逐字相同），不是给测试的缝。
    @property
    def _stores(self) -> dict[str, _JobStore]:
        return self.course_table._stores

    @_stores.setter
    def _stores(self, value: dict[str, _JobStore]) -> None:
        self.course_table._stores = value

    @property
    def _order(self) -> list[str]:
        return self.course_table._order

    @_order.setter
    def _order(self, value: list[str]) -> None:
        self.course_table._order = value

    @property
    def _solo(self) -> _JobStore | None:
        return self.course_table._solo

    @_solo.setter
    def _solo(self, value: _JobStore | None) -> None:
        self.course_table._solo = value

    @property
    def _cursor(self) -> str | None:
        return self.course_table._cursor

    @_cursor.setter
    def _cursor(self, value: str | None) -> None:
        self.course_table._cursor = value

    @property
    def _locate_cache(self) -> dict[str, str]:
        return self.course_table._locate_cache

    @_locate_cache.setter
    def _locate_cache(self, value: dict[str, str]) -> None:
        self.course_table._locate_cache = value

    @property
    def _ambiguous(self) -> dict[str, list[str]]:
        return self.course_table._ambiguous

    @_ambiguous.setter
    def _ambiguous(self, value: dict[str, list[str]]) -> None:
        self.course_table._ambiguous = value

    @property
    def _workers(self) -> dict[str, float]:
        return self.board._workers

    @_workers.setter
    def _workers(self, value: dict[str, float]) -> None:
        self.board._workers = value

    @property
    def _halt_default(self) -> bool:
        return self.board._halt_default

    @_halt_default.setter
    def _halt_default(self, value: bool) -> None:
        self.board._halt_default = value

    @property
    def _halts(self) -> dict[str, bool]:
        return self.board._halts

    @_halts.setter
    def _halts(self, value: dict[str, bool]) -> None:
        self.board._halts = value

    @property
    def _halt_when_idle(self) -> bool:
        return self.board._halt_when_idle

    @_halt_when_idle.setter
    def _halt_when_idle(self, value: bool) -> None:
        self.board._halt_when_idle = value

