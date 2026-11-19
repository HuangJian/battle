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
from hub.dispatch_state import Dispatch, DispatchLocks, DispatchTables
from hub.queue_auth import QueueAuthMixin
from hub.queue_claims import QueueClaimsMixin
from hub.queue_observe import QueueObserveMixin
from hub.queue_offline import QueueOfflineMixin
from hub.queue_resume import QueueResumeMixin
from hub.queue_scope import QueueScope
from hub.queue_store_face import QueueStoreFaceMixin
from hub.store import _JobStore
from hub.worker_board import WorkerBoard

# `DISCOVER_FRESH_SEC` / `DISCOVER_SCAN_MIN_SEC` 两个类常量随发现簇住 `queue_scope`
# （B7-S1 并入），本类经 MRO 继承（`hub.DISCOVER_SCAN_MIN_SEC` 这类实例访问照旧）。
# 注意 `DISCOVER_SCAN_SEC`（后台兜底线程的节拍，启动参数用）是**另一件事**，留在 hub_server。


# ------------------------------------------------------------------ 组合


class _HubQueue(
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
    # （常量本身只有一份，住 `queue_scope`——与 `BC_EPOCH_BODY_MAX` 同一个做法）。
    DISCOVER_FRESH_SEC = QueueScope.DISCOVER_FRESH_SEC
    DISCOVER_SCAN_MIN_SEC = QueueScope.DISCOVER_SCAN_MIN_SEC

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
        # B7-P4：离线派发 / 观测的 6 张表 + 5 把旁路锁收进 `Dispatch`。锁仍由组合根建、
        # 构造时注入（L2 闭集不破）；锁集合与加锁顺序逐字不变，只换持有者。
        self.dispatch = Dispatch(
            DispatchTables(
                peeked={},
                dispatch={},
                leases={},
                offline_disks={},
                worker_prefetch={},
                worker_errors=deque(),
            ),
            DispatchLocks(
                dispatch_lock=Lock(),
                lease_lock=Lock(),
                disk_lock=Lock(),
                pf_lock=Lock(),
                we_lock=Lock(),
            ),
        )
        #: 自动发现的扫描闸（`_discover_last` 初值 0 ⇒ 首次调用必扫）
        if self._discover_root is not None:
            self._discover_last = float("-inf")
        _AuthGuard.__init__(self, now_fn)
        # 时钟与单课程 store 同源（测试注入的假时钟必须一致，否则 claimed 标记的时间戳
        # 会混入真实墙钟）。
        self._now = self._solo._now if self._solo is not None else (now_fn or time.time)
        # B7-S2：课程表 / 路由 / 停机 / 发现面切成 `QueueScope` 协作者。构造排在 `_now`
        # 之后（`add_course` 要给新 store 同一个 now_fn），在 hold 同步循环之前（后者经
        # `self._sync_hold` 转发进 scope）。
        self.scope = QueueScope(self, lock=self._lock, clock=self._now)
        # 盘上的 hold 镜像同步（★M1b）：必须排在 `self._now` **之后** —— 盘上有 hold 时
        # `_dispatch_load` 要打恢复宽限（`hold-restored`），那需要时钟。旧顺序下「带 hold
        # 重启」= 构造期 AttributeError（hub 直接起不来）。
        # ★M4b：`_modes` 与 `dispatch_effective_mode` 已随「课程无模式」删除。
        for _c in self._order:
            self._sync_hold(_c)

    # ---- B7-S2：课程表 / 路由 / 停机 / 发现面的**转发壳**（26 个口） ----
    # 实现与状态主人住 `self.scope`；跨域调用点（`self.course_of` 等）逐字不变。
    def halt_of(self, course: str) -> bool:
        """转发到 `QueueScope.halt_of`（B7-S2）。"""
        return self.scope.halt_of(course)

    def all_halted(self) -> bool:
        """转发到 `QueueScope.all_halted`（B7-S2）。"""
        return self.scope.all_halted()

    def _work_left(self, exclude: str = '') -> str | None:
        """转发到 `QueueScope._work_left`（B7-S2）。"""
        return self.scope._work_left(exclude)

    def halt_if_idle(self, course: str = '') -> tuple[bool, str]:
        """转发到 `QueueScope.halt_if_idle`（B7-S2）。"""
        return self.scope.halt_if_idle(course)

    def maybe_halt_when_idle(self) -> bool:
        """转发到 `QueueScope.maybe_halt_when_idle`（B7-S2）。"""
        return self.scope.maybe_halt_when_idle()

    def set_halt(self, halt: bool, course: str = '') -> bool:
        """转发到 `QueueScope.set_halt`（B7-S2）。"""
        return self.scope.set_halt(halt, course)

    def add_course(self, name: str) -> bool:
        """转发到 `QueueScope.add_course`（B7-S2）。"""
        return self.scope.add_course(name)

    def drop_course(self, name: str) -> bool:
        """转发到 `QueueScope.drop_course`（B7-S2）。"""
        return self.scope.drop_course(name)

    def _adopt_solo(self) -> None:
        """转发到 `QueueScope._adopt_solo`（B7-S2）。"""
        self.scope._adopt_solo()

    def _registry(self) -> dict[str, float]:
        """转发到 `QueueScope._registry`（B7-S2）。"""
        return self.scope._registry()

    def note_worker(self, worker_id: str) -> None:
        """转发到 `QueueScope.note_worker`（B7-S2）。"""
        self.scope.note_worker(worker_id)

    def worker_registry(self) -> dict[str, float]:
        """转发到 `QueueScope.worker_registry`（B7-S2）。"""
        return self.scope.worker_registry()

    def active_worker_count(self) -> int:
        """转发到 `QueueScope.active_worker_count`（B7-S2）。"""
        return self.scope.active_worker_count()

    def courses(self) -> list[str]:
        """转发到 `QueueScope.courses`（B7-S2）。"""
        return self.scope.courses()

    def course_of(self, job_id: str) -> str | None:
        """转发到 `QueueScope.course_of`（B7-S2）。"""
        return self.scope.course_of(job_id)

    def course_store(self, course: str) -> _JobStore | None:
        """转发到 `QueueScope.course_store`（B7-S2）。"""
        return self.scope.course_store(course)

    def _store_of(self, job_id: str) -> _JobStore | None:
        """转发到 `QueueScope._store_of`（B7-S2）。"""
        return self.scope._store_of(job_id)

    def mode_ignored(self, course: str) -> bool:
        """转发到 `QueueScope.mode_ignored`（B7-S2）。"""
        return self.scope.mode_ignored(course)

    def active_courses(self) -> int:
        """转发到 `QueueScope.active_courses`（B7-S2）。"""
        return self.scope.active_courses()

    def _sync_hold(self, course: str) -> None:
        """转发到 `QueueScope._sync_hold`（B7-S2）。"""
        self.scope._sync_hold(course)

    def _note_ambiguous(self, job_id: str, holders: list[str]) -> None:
        """转发到 `QueueScope._note_ambiguous`（B7-S2）。"""
        self.scope._note_ambiguous(job_id, holders)

    def ambiguous_jids(self) -> dict[str, list[str]]:
        """转发到 `QueueScope.ambiguous_jids`（B7-S2）。"""
        return self.scope.ambiguous_jids()

    def discover(self, force: bool = False) -> list[str]:
        """转发到 `QueueScope.discover`（B7-S2）。"""
        return self.scope.discover(force)

    def _drop_dead_courses(self, root: Path) -> list[str]:
        """转发到 `QueueScope._drop_dead_courses`（B7-S2）。"""
        return self.scope._drop_dead_courses(root)

    def _serves_course(self, course: str) -> bool:
        """转发到 `QueueScope._serves_course`（B7-S2）。"""
        return self.scope._serves_course(course)

    def _course_dir_live(self, ent: Path, now: float) -> bool:
        """转发到 `QueueScope._course_dir_live`（B7-S2）。"""
        return self.scope._course_dir_live(ent, now)

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

    # ---- B7-P4：离线派发 / 观测状态的转发属性（持有者 = `self.dispatch`） ----
    @property
    def _peeked(self) -> dict[str, float]:
        return self.dispatch._peeked

    @_peeked.setter
    def _peeked(self, value: dict[str, float]) -> None:
        self.dispatch._peeked = value

    @property
    def _dispatch(self) -> dict[str, dict]:
        return self.dispatch._dispatch

    @_dispatch.setter
    def _dispatch(self, value: dict[str, dict]) -> None:
        self.dispatch._dispatch = value

    @property
    def _dispatch_lock(self) -> Lock:
        return self.dispatch._dispatch_lock

    @_dispatch_lock.setter
    def _dispatch_lock(self, value: Lock) -> None:
        self.dispatch._dispatch_lock = value

    @property
    def _leases(self) -> dict[str, dict]:
        return self.dispatch._leases

    @_leases.setter
    def _leases(self, value: dict[str, dict]) -> None:
        self.dispatch._leases = value

    @property
    def _lease_lock(self) -> Lock:
        return self.dispatch._lease_lock

    @_lease_lock.setter
    def _lease_lock(self, value: Lock) -> None:
        self.dispatch._lease_lock = value

    @property
    def _offline_disks(self) -> dict[str, float]:
        return self.dispatch._offline_disks

    @_offline_disks.setter
    def _offline_disks(self, value: dict[str, float]) -> None:
        self.dispatch._offline_disks = value

    @property
    def _disk_lock(self) -> Lock:
        return self.dispatch._disk_lock

    @_disk_lock.setter
    def _disk_lock(self, value: Lock) -> None:
        self.dispatch._disk_lock = value

    @property
    def _worker_prefetch(self) -> dict[str, dict]:
        return self.dispatch._worker_prefetch

    @_worker_prefetch.setter
    def _worker_prefetch(self, value: dict[str, dict]) -> None:
        self.dispatch._worker_prefetch = value

    @property
    def _pf_lock(self) -> Lock:
        return self.dispatch._pf_lock

    @_pf_lock.setter
    def _pf_lock(self, value: Lock) -> None:
        self.dispatch._pf_lock = value

    @property
    def _worker_errors(self) -> deque[dict]:
        return self.dispatch._worker_errors

    @_worker_errors.setter
    def _worker_errors(self, value: deque[dict]) -> None:
        self.dispatch._worker_errors = value

    @property
    def _we_lock(self) -> Lock:
        return self.dispatch._we_lock

    @_we_lock.setter
    def _we_lock(self, value: Lock) -> None:
        self.dispatch._we_lock = value

