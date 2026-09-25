"""remote/hub/queue.py — `_HubQueue` 组合类：多课程单 hub 的调度面。

S4 第十五刀把 1033 行 / 76 个方法的 `_HubQueue` 按**域**拆成七个混入（`hub/queue_*.py`），
组合类只剩 docstring、一个类常量与 `__init__`：

```
class _HubQueue(QueueScopeMixin, QueueDiscoverMixin, QueueAuthMixin, QueueClaimsMixin,
                QueueResumeMixin, QueueObserveMixin, QueueStoreFaceMixin, _AuthGuard)
  queue_scope       课程表 · 归属路由 · 模式 · 停机达令 · worker 登记（状态的主人）
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
2. **跨域互调是常态**：`claim_next` → `discover` / `_serves_course` / `mode_of` /
   `active_worker_count` / `_announce_freeze`；`queue_state` → `halt_of` / `mode_of` /
   `active_courses` / `offline_courses` / `all_halted` / `active_worker_count`；
   `resume_anchor` → `resume_sources`…混入把它们留在 `self.X` 上 ⇒ **零 seam**；
3. **tests 直接读私有状态**（`hub._stores` / `hub._order` / `hub._halts` / `hub._modes` /
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
`tests/test_hub_queue_split.py` 钉住这条，并逐字段对账「谁写它」。

## 依赖方向

`hub.queue → {hub.queue_* 七混入, hub.auth, hub.store}`（严格向下）。
`remote/hub_server.py` 是整个 hub 的组装 + HTTP 面，从上面拿 `_HubQueue` 并 re-export
（`hs._HubQueue` 是约 10 个测试与 e2e 的取名字入口——**名字是契约，位置不是**）。
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from common.protocol import COURSE_MODE_ONLINE
from remote.hub.auth import _AuthGuard
from remote.hub.queue_auth import QueueAuthMixin
from remote.hub.queue_claims import QueueClaimsMixin
from remote.hub.queue_discover import QueueDiscoverMixin
from remote.hub.queue_observe import QueueObserveMixin
from remote.hub.queue_offline import QueueOfflineMixin
from remote.hub.queue_resume import QueueResumeMixin
from remote.hub.queue_scope import QueueScopeMixin
from remote.hub.queue_store_face import QueueStoreFaceMixin
from remote.hub.store import _JobStore

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
         `runId`，而 runId 是每进程随机的 8 字节 hex（`rl/queue.py::RUN_ID`）⇒ 跨课程
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
        stores,
        order=None,
        modes=None,
        now_fn=None,
        discover_root=None,
        discover_fresh_sec: float = DISCOVER_FRESH_SEC,
    ) -> None:
        self._stores: dict[str, _JobStore] = dict(stores)
        self._order: list[str] = [c for c in (order or list(self._stores)) if c in self._stores]
        #: 自动发现根（`--traj-root`）；None = 关（`--discover` 未给，零开销零行为变化）
        self._discover_root: Path | None = Path(discover_root) if discover_root else None
        self._discover_fresh = float(discover_fresh_sec)
        self._discover_last = 0.0
        #: 「跳过未开课课程」的告警去重集（每门课只喊一次，不刷屏）。
        self._no_marker_warned: set[str] = set()
        md = modes or {}
        self._modes: dict[str, str] = {
            c: str(md.get(c) or COURSE_MODE_ONLINE) for c in self._order
        }
        # 停摆位同步到 store（唯一知道 mode 的层是它）：`set_mode` 热切与启动参数两条路
        # 都得过这里，否则「重启后离线课变成可领」这类偏差没有任何一处会报错。
        for _c in self._order:
            self._sync_parked(_c)
        #: 上次派发过的课程（轮转起点）；None = 从序首开始
        self._cursor: str | None = None
        #: job_id -> course（归属解析缓存；job_id 不可复用，故不会失效）
        self._locate_cache: dict[str, str] = {}
        #: jid -> 同时持有它的课程（≥2 = 身份歧义）。两个用途：`course_of` 的**去重打点**
        #: （每个 job 作用域请求都会跑它，逐次打点会把日志刷爆），以及拒答时把
        #: 「谁和谁撞了」带进 reason。观测面的**全量**清单另有 `ambiguous_jids()`（扫盘）。
        self._ambiguous: dict[str, list[str]] = {}
        #: 单课程 = 旧形状：进程级状态一律借那一份 store（见类 docstring ③）
        self._solo: _JobStore | None = (
            next(iter(self._stores.values())) if len(self._stores) == 1 else None
        )
        #: 自动发现的扫描闸（`_discover_last` 初值 0 ⇒ 首次调用必扫）
        if self._discover_root is not None:
            self._discover_last = float("-inf")
        #: **离线租约**（课程 → `{token, worker_id, at, expires_at}`）：进程内、惰性过期。
        #: 为什么住实例而不是模块（与 `_TASK_PACK_TRIGGERS` 不同）：生产一个进程一个 hub 两者等价，
        #: 而单测里每个 hub 各自干净（共享就得分用例清账）。重启即清——与触发账本同口径：
        #: 最坏情形由回传侧 `(run_id, it)` 首写幂等兜底（plan §3.2）。
        self._leases: dict[str, dict] = {}
        self._lease_lock = Lock()
        #: 离线**盘**报名表：disk_id -> last_seen（秒）。★ 为什么单独一张表：跑
        #: `battle.offline.ipynb` 的机器**不碰队列**（取包链全在 `/offline/*` 上），它的身份
        #: 只能在那一面被看到；而「本环境有没有离线盘」这个读数此前恒为空（审计 §4-L3：
        #: `CFG["offline_worker"]` 全仓只有测试设过）。与 `_leases` 同口径：进程内、只做观测
        #: （重启即清，最坏情形由回传侧首写幂等兜底）。
        self._offline_disks: dict[str, float] = {}
        #: 独立锁：`offline_disk_readout` 会被 `/admin/queue` 调到，而那条路不持 `_lease_lock`
        #: 也不该持 `_lock`（观测面不许和调度临界区互等）。
        self._disk_lock = Lock()
        _AuthGuard.__init__(self, now_fn)
        # 时钟与单课程 store 同源（测试注入的假时钟必须一致，否则 claimed 标记的时间戳
        # 会混入真实墙钟）。
        self._now = self._solo._now if self._solo is not None else (now_fn or time.time)
        #: 多课程时自己的 worker 登记表（worker_id -> last_seen）——避让链的唯一事实源。
        self._workers: dict[str, float] = {}
        # 停机达令**按课程**（2026-09-18 单 hub 化）：一个 hub 服务所有课程之后，若达令还是
        # 进程级一个布尔，「A 课门禁 ABORT」会连坐 B 课的云机（B 的 worker 下一轮轮询就
        # 拿到 halt 并自停）。故：无课程参数 = 全课程（旧调用方语义，落 `_halt_default`，
        # 新发现的课也继承）；`?course=` = 只动那一门课的例外（`_halts`）。
        self._halt_default = False
        self._halts: dict[str, bool] = {}

