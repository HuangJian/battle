"""hub/queue_scope.py — 课程表 · 归属路由 · 停机达令 · worker 登记。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。本簇是**状态的主人**：`_stores` / `_order` /
`_mode_ignored` / `_solo` / `_locate_cache` / `_halts` / `_workers` 都写在它身上，而组合类的
`__init__` 是这些字段**唯一**的带值声明点（见 `hub/queue.py` 头部那段「为什么 `__init__`
不再拆钩子」）。

形状（`_HubQueue` 类 docstring 重组）：**一个进程托管 N 份 `_JobStore`**，磁盘布局逐字节
不变（每课程仍是 `tmp/<course>/remote-jobs` + `tmp/<course>/training_log.jsonl`）。

本簇回答三件事：

* **路由**：任意 `/jobs/{id}/...` 先按 job_id 找归属课程。判据 = 「哪个课程的 job 目录里
  真有它」（`<job_root>/<job_id>/` 的存在就是归属证据，且只要一次 fs 调用——账本行里
  没有课程字段，磁盘契约不变）；命中即入 `_locate_cache`。
* **队形**：每课程一条 FIFO，由 `queue_claims` 跨课程轮转派发。
* **进程级状态**（停机达令 / worker 登记 / 鉴权计数）：单课程时**借**那一份 `_JobStore`
  的，多课程时用自己的 —— 既有用例会在 store 上预热 `_auth_blocked_until` / 摆 `_workers`
  再发 HTTP 请求，若本类另有副本，那些预热就不生效了（见 `_adopt_solo` / `_registry`）。
* **自动发现三相**（B7-S1 从 `queue_discover` 并入）：`discover` 扫盘登记新鲜课程、
  `_serves_course` 是派发前的开课标记闸、`_course_dir_live` 判「在训」。并入的理由：
  它们只经 `self` 读写课程表与 `_lock`，与 scope 是同一份状态、同一把锁；独立成基类
  只增加一层壳。两个 `DISCOVER_*` 类常量也随本簇走。

## 依赖方向

`queue_scope → {common.protocol, hub.store, hub.task_pack}`（向下）。**不 import 任何兄弟混入**
——跨域调用一律经 `self`（这是「一个对象、一把锁」能成立的前提）。`hub/store` 是
**类型 + 构造**需求：`add_course` 要建 store，`_stores` 的注解要它；`hub/task_pack`
只取 `hold_progress_at`（★M1b：推 hold 镜像时要取进度锚，判据源与 `queue_offline` 同一份）。

## 对外名字（名字是契约）

`hub.server` re-export 整个 `_HubQueue`，所以 `hs._HubQueue` 仍可解析。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Protocol

from common.logutil import tag_logger
from common.protocol import (
    COURSE_ENABLE_MARKER,
    WORKER_SEEN_WINDOW_SEC,
    ProtocolError,
    parse_course_arg,
)
from hub.store import _JobStore
from hub.task_pack import course_finished as _course_finished
from hub.task_pack import hold_progress_at

#: 行日志：格式/时间戳只走 `common/logutil`（`[HH:MM:SS] [tag] msg`）。
_log = tag_logger("hub-server", lambda: time)


#: 开课标记（与 `stack/courses.COURSE_ENABLE_MARKER` 同名；hub 不 import dashboard）。
_ENABLE_MARKER = "training-enabled.txt"


class _ScopeHost(Protocol):
    """`QueueScope` 需要的**组合类表面**（B7-S2）：状态与 `hold_of` 都在 `_HubQueue` 上。

    写成 Protocol 而不是 `Any`：`QueueScope` 搬走后仍要读 `_stores` / `_workers` /
    `_auth_fail`，这些都是组合类的公开内部面；Protocol 让 mypy 继续查，同时不产生
    `queue_scope → queue` 的 import 边（层号不变）。
    """

    _ambiguous: dict[str, list[str]]
    _auth_blocked_until: dict[str, float]
    _auth_fail: dict[str, int]
    _cursor: str | None
    _discover_fresh: float
    _discover_last: float
    _discover_root: Path | None
    _halt_default: bool
    _halts: dict[str, bool]
    _halt_when_idle: bool
    _locate_cache: dict[str, str]
    _mode_ignored: set[str]
    _no_marker_warned: set[str]
    _order: list[str]
    _peeked: dict[str, float]
    _solo: _JobStore | None
    _stores: dict[str, _JobStore]
    _workers: dict[str, float]

    def hold_of(self, course: str) -> dict: ...


class QueueScope:
    """课程表 · 归属路由 · 停机达令 · 自动发现（B7-S2 从混入切成协作者）。

    26 个方法体逐字保留；状态读写改为 `self.hub._X`（组合类的同名属性），锁与时钟由
    组合根注入。`_HubQueue` 保留 26 个同名转发壳，跨域调用点与 HTTP 面不变。
    """

    #: 自动发现时判定「课程目录是不是活的」的新鲜窗口（秒）。
    #: 一个 PPO 轮次是分钟级（rollout 采集 + 云端结算 10–30min），窗口取 1h：正在跑的课
    #: 每轮都会在 `remote-jobs/` 里增删条目、往 jsonl 追加行，秒级就落在窗口内；而几天前
    #: 的陈旧实验目录（同样的磁盘形状，同样残留 pending job）永远不会被误当成「在跑的课」
    #: ——误登记会把已死课程的 job 继续派给真 GPU worker（白烧租约）。
    DISCOVER_FRESH_SEC = 3600.0
    #: 两次扫描之间的最小间隔（秒）：`claim_next` 是派发热路径（worker 每几秒一轮询），
    #: 每次 readdir 都扫一遍没必要，也没意义。
    DISCOVER_SCAN_MIN_SEC = 2.0

    def __init__(self, hub: _ScopeHost, *, lock: Lock, clock: Callable[[], float]) -> None:
        #: 组合类（状态的主人；本类不存副本）
        self.hub = hub
        #: 全 store **唯一**那把锁（组合根注入；本类不自建锁）
        self._lock = lock
        #: 时钟（组合根注入；不读组合类的 `_now`）
        self._clock = clock

    def halt_of(self, course: str) -> bool:
        """本课程是否在停机态（单课程借 store 时恒看那一份 store 的旗标）。"""
        if self.hub._solo is not None:
            return bool(self.hub._solo.halt_workers)
        return bool(self.hub._halts.get(course, self.hub._halt_default))

    def all_halted(self) -> bool:
        """**所有已登记课程**都在停机态。

        空课程表 → False（`--discover` 刚起、还没有课程时“没课可停”，不是停机）——
        否则空闲 worker 会收到一个凭空的停机达令。
        """
        if self.hub._solo is not None:
            return bool(self.hub._solo.halt_workers)
        return bool(self.hub._order) and all(self.hub._halts.get(c, self.hub._halt_default) for c in self.hub._order)

    def _work_left(self, exclude: str = "") -> str | None:
        """还有活吗？→ 人读原因（None = 真的空闲）。

        判据（用户 2026-11-12）：① 任何课程的**待领 job**（含 PPO）；② 任何课程的
        **在飞 job**（活还没回传，停了也白等）；③ 除 `exclude` 外任何**已开课但未收官**
        的课（PPO 一轮结束到下一轮发布之间队列是空的，只看队列会把它误判成收工）。
        `exclude` = 刚跑完、正在请求停机的课（它的 `run_complete` 还没写）。
        """
        for course in self.hub._order:
            st = self.hub._stores.get(course)
            if st is None:
                continue
            pend = st.claimable_job_ids()
            if pend:
                return f"{course} 还有 {len(pend)} 个待领 job"
            flying = st.inflight_job_ids()
            if flying:
                return f"{course} 还有 {len(flying)} 个在飞 job"
            if course == exclude:
                continue
            if (st.jsonl_path.parent / _ENABLE_MARKER).exists() and not _course_finished(
                st.jsonl_path
            ):
                return f"{course} 已开课且未收官"
        return None

    def halt_if_idle(self, course: str = "") -> tuple[bool, str]:
        """**干完了才停**：还有活就不下停机达令，改为「空闲即停」门闩。

        返回 `(是否已置停机, 原因)`。被挡住时不抛、不回 4xx——云机继续干活，
        下一次空闲 worker 来取活时 `maybe_halt_when_idle` 会重新评估。
        """
        reason = self._work_left(course)
        if reason:
            self.hub._halt_when_idle = True
            return False, reason
        self.hub._halt_when_idle = False
        self.set_halt(True)
        return True, "无待领 job、也没有未收官的已开课课程"

    def maybe_halt_when_idle(self) -> bool:
        """门闩评估（**空闲取活时**调）：活全清了才真正置停机。"""
        if not self.hub._halt_when_idle:
            return False
        reason = self._work_left("")
        if reason:
            return False
        self.hub._halt_when_idle = False
        self.set_halt(True)
        return True

    def set_halt(self, halt: bool, course: str = "") -> bool:
        """置/解停机达令；未知 course → False（不猜、不静默改写全局）。"""
        # 人工停机/恢复都覆盖「空闲即停」门闩（意图优先于之前的自动决策）。
        self.hub._halt_when_idle = False
        if self.hub._solo is not None:
            self.hub._solo.halt_workers = bool(halt)
            return True
        if course:
            if course not in self.hub._stores:
                return False
            self.hub._halts[course] = bool(halt)
            return True
        self.hub._halt_default = bool(halt)
        self.hub._halts.clear()
        return True

    # ---- 课程表自动发现（`--discover`） ----
    def add_course(self, name: str) -> bool:
        """登记一门课程（现建 `_JobStore`）；已登记/空名/未开发现 → False（幂等）。

        派生目录与 `--course` 启动参数**逐字节相同**（`<root>/<name>/remote-jobs` +
        `<root>/<name>/training_log.jsonl`）⇒ 自动发现的课与显式声明的课在观测面、诊断
        工具、`tmp/<course>` 约定里无法区分，也不该区分。
        """
        c = str(name or "")
        if not c or c in self.hub._stores or self.hub._discover_root is None:
            return False
        self._adopt_solo()
        self.hub._stores[c] = _JobStore(
            self.hub._discover_root / c / "remote-jobs",
            self.hub._discover_root / c / "training_log.jsonl",
            now_fn=self._clock,  # 时钟同源：租约时间戳与判定不能一边真墙钟一边假钟
        )
        self.hub._order.append(c)
        # ★六轮 R3-a（P0-8）：发现/重启路径与构造路径同口径——不调 `_sync_hold` 的话，
        # 盘上的 hold 只在**读面**（清单/取包）生效，而派发闸的镜像还是空的 ⇒
        # 重启后一台盘能领走别人正在跑的课（接管闸与读面分叉）。
        self._sync_hold(c)
        return True

    def drop_course(self, name: str) -> bool:
        """把一门课从课程表摘掉（连带**释放**它的 store 与那份账本缓存）。

        ★ 2026-10-17（plan/hub-server-memory-slim L3）：课程表此前**只增不减**
        （`discover` 里 `if ent.name in self.hub._stores: continue`）——目录被删、课被停、tmp 被清
        之后，那门课的 store 与它的 `_ledger_cache`（整份账本解析出来的 Python 对象）**永久
        驻留**。hub 那次 6.2GB 里就有这一类：tmp 被清掉了，内存里的账本一份没少。

        为什么不在 `_stores` 上直接 `del`：还有四个旁表按课程键挂着（`_order` / `_halts` /
        `_no_marker_warned` / `_peeked`），以及 `_locate_cache` 里指向它的 jid——留着它们就是
        「表里没这门课，但别处还认它」的半死状态。落盘的两个（`_dispatch` 落
        `offline-dispatch.json`、`_leases` 的意义是重启后接管）**不动**：课程若重新登记，
        盘上状态本来就该继续生效。
        """
        c = str(name or "")
        with self._lock:
            if c not in self.hub._stores:
                return False
            del self.hub._stores[c]
            self.hub._order = [x for x in self.hub._order if x != c]
            self.hub._halts.pop(c, None)
            self.hub._no_marker_warned.discard(c)
            self.hub._peeked.pop(c, None)
            if self.hub._cursor == c:
                self.hub._cursor = None
            # 归属缓存里的条目指向已不存在的课 ⇒ `course_of` 会把它交给 `_store_of`
            # 的缺课分支（那分支按"未知 job"处理，比"指向一个空壳 store"好得多）。
            for jid in [j for j, owner in self.hub._locate_cache.items() if owner == c]:
                del self.hub._locate_cache[jid]
            self._adopt_solo()
        return True

    def _adopt_solo(self) -> None:
        """从「单课程借 store」切到「多课程自有状态」：把进程级状态搬到自己身上。

        为什么必须搬：单课程时 halt / worker 登记 / 鉴权计数都住在那一份 store 里
        （既有用例直接预热 store 字段），一旦课程数变成 2，这些状态必须继续生效——不搬
        就是「多发现一门课，把停机达令、worker 登记、鉴权闭锁全悄悄清了」。
        """
        st = self.hub._solo
        if st is None:
            return
        self.hub._halt_default = bool(st.halt_workers)
        self.hub._workers = dict(st._workers)
        self.hub._auth_fail = dict(st._auth_fail)
        self.hub._auth_blocked_until = dict(st._auth_blocked_until)
        self.hub._solo = None

    def _registry(self) -> dict[str, float]:
        """生效的 worker 登记表（单课程 = store 的，多课程 = 自己的）。"""
        return self.hub._solo._workers if self.hub._solo is not None else self.hub._workers

    def note_worker(self, worker_id: str) -> None:
        """登记一次 worker 轮询（peek / priority 入口）。空 id 不记（无身份无法去重计数）。"""
        wid = (worker_id or "").strip()
        if not wid:
            return
        reg = self._registry()
        with self._lock:
            reg[wid] = self._clock()

    def worker_registry(self) -> dict[str, float]:
        """worker 登记表的**副本**（id → 最近一次轮询时刻；诊断 / 用例读，不窗口过滤）。

        与 `_registry()` 的分工：那个返回**正在用的那张表**（单课程时就是 store 那份，
        身份本身由 `_adopt_solo` 的用例钉住），改它会直接改状态；读数面只给副本。
        不按 `WORKER_SEEN_WINDOW_SEC` 过滤：问「登记还在不在」的人不该被时钟影响。
        """
        with self._lock:
            return dict(self._registry())

    def active_worker_count(self) -> int:
        """窗口内**不同** worker 数（避让判据「还有别的卡能接手」的唯一口径）。"""
        now = self._clock()
        with self._lock:
            return len(
                {wid for wid, seen in self._registry().items() if now - seen <= WORKER_SEEN_WINDOW_SEC}
            )

    # ---- 课程表与归属 ----
    def courses(self) -> list[str]:
        return list(self.hub._order)

    def course_of(self, job_id: str) -> str | None:
        """job_id → 归属课程；**找不到 / 归属有歧义都返回 None**（不是空串！）。

        为什么必须用 None 区分「找不到」：单课程队列（以及旧单课程 hub）的课程名**就是空串**
        （`tmp/nocourse` 那套约定）。用空串兼作「找不到」会把它当成找不到 —— 直接后果
        是 `/jobs/peek` 刚列出的 job 立刻解析不到归属，handler 打到哨兵路径上 500
        （2026-09-18 白测一次的真故障）。

        为什么搜目录而不是搜账本：账本行里没有课程字段（磁盘契约不变），而
        `<job_root>/<job_id>/` 的存在本身就是归属证据，且是一次 fs 调用 —— 比读账本便宜。

        ★ **归属唯一才认**（2026-09-24 job 身份事故，plan/job-identity-collision.plan.md §3.2）：
        ≥2 门课都认识同一个 jid ⇒ 返回 None + 打一行「身份歧义」。原来「取第一个匹配」正是
        事故的**静默通道**：worker 领的是 l3 的候选，hub 把它路由到 l1 的副本（租约/结果/
        账本各写一份，而两个 trainer 的 `wait_job` 也读到同一份 result ⇒ 权重互串）。
        歧义一律拒答的代价是「响亮失败」（job 作用域入口 404、训练轮等到超时），
        收益是「绝不污染」—— 这个方向是刻意选的。
        """
        jid = str(job_id or "")
        if not jid:
            return None
        hit = self.hub._locate_cache.get(jid)
        if hit is not None:
            return hit
        holders: list[str] = []
        for course in self.hub._order:
            try:
                if (self.hub._stores[course].job_root / jid).exists():
                    holders.append(course)
            except OSError:
                continue
        if not holders:
            return None
        if len(holders) > 1:
            self._note_ambiguous(jid, holders)
            return None  # 歧义**绝不**进缓存（一次歧义会变成永久归属）
        self.hub._locate_cache[jid] = holders[0]
        return holders[0]

    def course_store(self, course: str) -> _JobStore | None:
        """这门课的 store（未知课程 ⇒ None）。**只读查询**：不改课程表、不建 store。

        为什么需要公开入口：`_stores` 只是课程 → store 的登记表，而「这门课的队列此刻
        长什么样」（hold 闸 / 可领池 / 即时闸）都住在 store 上——诊断与用例要问的是后者，
        不该伸手进登记表本体。
        """
        return self.hub._stores.get(str(course or ""))

    def _store_of(self, job_id: str) -> _JobStore | None:
        course = self.course_of(job_id)
        return None if course is None else self.hub._stores.get(course)

    def mode_ignored(self, course: str) -> bool:
        """启动参数里这门课带过模式段（★M4b / P1-3：`--course a=offline` ⇒ WARN + 忽略）。

        只服务 `/admin/courses` 行上的 `mode_ignored` 标记（排障时知道「有人还在按老脚本
        传模式」）；不参与任何派发判据。
        """
        return course in self.hub._mode_ignored

    def active_courses(self) -> int:
        """**在实时派发**的课程数（竞速判据的分母）：有待领或未过期在飞 job，且未被接管。

        ★M4b（plan §1.5.4-P2-7）：课程不再有模式 —— 被 **live hold** 压住的课不算
        （它的队列被派发闸压着，没人能领）；已跑完无待办的课不算（没活可抢）。
        读数会随接管建立/掉线在 1↔N 之间跳（§69 写明），这是**观测口径**不是派发输入。
        """
        n = 0
        for course in self.hub._order:
            if str(self.hub.hold_of(course).get("state") or "") == "live":
                continue
            st = self.hub._stores[course]
            if st.claimable_job_ids() or st.inflight():
                n += 1
        return n

    def _sync_hold(self, course: str) -> None:
        """把本课当前的 hold 推给 store（★M1b / Q5：派发闸的第三层）。

        ★M1c：它就是**唯一**的闸输入推送口（旧的 `_sync_parked` 随 `parked` 一起退役）——
        构造 / 发现 / 每次改 hold 都必须过它，否则镜像与事实分叉。

        推的是**读数**（`hold_of`：惰性过期后的 live/stale 形状），不是判据：闸那侧自己拿
        `last_progress_at` 对时钟自判活——镜像可能很久没被推过（hub 重启、长时间不派活），
        而「很久没推」恰恰就是它要防的假活现场。stale 的 hold 推成 `{}`（不占闸）。
        """
        st = self.hub._stores.get(course)
        if st is None:
            return
        hold = self.hub.hold_of(course)
        if str(hold.get("state") or "") != "live":
            st.leases.hold_meta = {}
            return
        # 到这里 hold 是活的（`hold_state` 只看进度）：镜像只带判活需要的两样
        # （`hold_blocked` 自己拿时钟对窗，不依赖「推的人刚刚才推过」）。
        st.leases.hold_meta = {
            "worker_id": str(hold.get("worker_id") or ""),
            "last_progress_at": float(hold_progress_at(hold)),
        }

    def _note_ambiguous(self, job_id: str, holders: list[str]) -> None:
        """歧义只报一次（按 jid 去重）：`course_of` 在每个 job 作用域请求上都会跑，
        逐次打点会把日志刷爆，反而埋掉真正要看的那一行。"""
        if job_id in self.hub._ambiguous:
            return
        self.hub._ambiguous[job_id] = list(holders)
        _log(
            f"⚠ job 身份歧义：job={job_id} 同时存在于 "
            f"{'、'.join(holders)} —— 一律拒答（不猜归属）。"
            "多半是旧 runId/旧代码留下的同名 job 目录：清掉非当前 runId 的 "
            "`remote-jobs/<jid>`（或换 runId 重跑）即可。",
        )

    def ambiguous_jids(self) -> dict[str, list[str]]:
        """同一 jid 挂在 ≥2 门课上的清单（`/admin/queue` 的观测面，只读）。

        与 `course_of` 同一个事实（「哪几门课持有这个 jid」）的两个方向：那边按 jid 逐课探，
        这边按课程列目录一次扫完 —— 观测面要的是**全量**，且不在派发热路径上。
        单课程（<2 门）恒空，零开销。
        """
        if len(self.hub._order) < 2:
            return {}
        seen: dict[str, list[str]] = {}
        for course in self.hub._order:
            try:
                entries = list(self.hub._stores[course].job_root.iterdir())
            except OSError:
                continue
            for p in entries:
                # 「是 job 目录」的判据与调度面同源：带 manifest.json（`.extra_tmp`、
                # `offline/` 这些 job_root 下的旁系目录一律不算）。
                try:
                    if p.name.startswith(".") or not (p / "manifest.json").exists():
                        continue
                except OSError:
                    continue
                seen.setdefault(p.name, []).append(course)
        return {jid: cs for jid, cs in sorted(seen.items()) if len(cs) > 1}

    def discover(self, force: bool = False) -> list[str]:
        """扫 `<traj_root>/<course>/{remote-jobs,offline}`，把新鲜且未登记的课程登记进来。

        返回本次新增的课程（目录序，稳定）。`--discover` 未开 → 恒空（零开销）。

        `force=True` 跳过一次扫描的最小间隔闸：**只在「这一刻必须看到真课程表」的动作用**
        —— ① 人工动作 `POST /admin/courses` 指名要某门课；② hub 启动时（`hub/boot.py`
        的引导链，2026-09-29 加：盘上已有的课不该等一个后台节拍才登记，缺省节拍 5s ⇒
        控制台/worker 启动初期看到空表）。为什么需要它（2026-09-23 事故，用户报障「三个离线课里有一个
        显示在训」）：间隔闸是为了给派发热路径（`claim_next` 每拍调）减去重扫成本，但
        它也让「刚建好 `remote-jobs/` 的课」在下一次顺带扫描之前不存在于 `_stores` 里
        ——而那个窗口里打来的 mode POST 只会得到 400（「需要合法 course」），控制台
        那一次有界重试（默认 3×2s）可能整段落在窗口内 ⇒ 意图从此静默失配（该课留在
        online，面板一直显示「在训/切离线」）。指名一门课的写动作有资格要求一次真扫。

        为什么以**磁盘**为发现源、而不是让控制台/训练器走一次 HTTP 注册：训练侧把 job
        发布到 `<traj>/remote-jobs` 是**文件系统事实**（hub 与 trainer 共享同一份盘），
        所以「有新课程在跑」这件事本身就写在盘上。再加一条注册旁路就是「会失败、会乱序、
        会忘了调」的第二事实源——而漏注册的后果是那门课**永久饿死**（跨课程轮转表里
        没有它），且表面上「训练正常」。
        """
        root = self.hub._discover_root
        if root is None:
            return []
        now = self._clock()
        if not force and now - self.hub._discover_last < self.DISCOVER_SCAN_MIN_SEC:
            return []
        self.hub._discover_last = now
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        added: list[str] = []
        for ent in entries:
            try:
                if not ent.is_dir():
                    continue
            except OSError:
                continue
            if ent.name in self.hub._stores:
                continue
            try:
                parse_course_arg(ent.name)
            except ProtocolError:
                continue  # 非课程目录（tmp/training-start 之类）——安静跳过
            if not self._course_dir_live(ent, now):
                continue
            if self.add_course(ent.name):
                added.append(ent.name)
        # ---- ★ 淘汰（2026-10-17，plan/hub-server-memory-slim L3）：课程表**只增不减**的
        # 那一半。目录被删 / 停课删了开课标记 = **确定性**的死信号（不靠"超时"猜——
        # 卡住的课不该被当成死课摘掉）。且**在飞或有人接管时不摘**：淘汰是为了释放内存，
        # 不是给正在跑的活断电。
        dropped = self._drop_dead_courses(root)
        if dropped:
            _log(f"dropped courses: {', '.join(dropped)}")
        if added:
            _log(f"discovered courses: {', '.join(added)}")
        return added

    def _drop_dead_courses(self, root: Path) -> list[str]:
        """摘掉「**课程目录整个没了**」且没有在飞、没人接管的课程（目录序，稳定）。

        ⚠ 判据只取「目录不存在」这**一条**，不取「开课标记被删」：停课（删
        `training-enabled.txt`）之后那门课的权重/任务包还在盘上，控制台要能看见它、
        云机要能取它的包、人要能导它的段——把它从课程表摘掉 = 那些入口 404
        （派发那一层已经由 `_serves_course` 挡住了，摘表只带来损失）。
        只有目录本身没了（tmp 被清 / 课程目录被删）时，留着 store 与它的账本缓存
        才是纯亏：那正是 2026-10-17 那次 tmp 清空之后内存一份没少的形状。
        """
        out: list[str] = []
        now = self._clock()
        for c in sorted(self.hub._stores):
            ent = root / c
            if ent.is_dir():
                continue  # 目录还在 ⇒ 不摘（停课 ≠ 死课）
            st = self.hub._stores[c]
            # ★ 在飞判据走**内存租约**，不走 `inflight_job_ids()`：后者要求 job 目录与
            # payload 还在盘上，而「目录整个没了」正是本函数的前提 ⇒ 它恒为空，等于没有这道闸。
            if st.leases.active_lease_ids(now):
                continue  # 有活租约在跑
            if str(self.hub.hold_of(c).get("state") or "") == "live":
                continue  # 离线盘正接管着
            if self.drop_course(c):
                out.append(c)
        return out

    def _serves_course(self, course: str) -> bool:
        """派发闸：**发现模式**下课程目录必须仍带开课标记（`training-enabled.txt`）。

        为什么发现时判过还要在这里再判一次（2026-09-20 事故）：课程表是**发现那一刻**
        建的，而 `remote-jobs/` 里躺着的 pending job 不会自己消失。没有这道闸，任何
        在旧表/旧代码里登记过的课程会把它的**陈旧 job 继续派给真 GPU worker**——
        白烧租约，云端逐份失败（D14 血缘不匹配 / 旧 code.zip 触发自重启），而训练侧
        什么都看不到（那门课早就不跑了）。用户口径：「课程开训需要用户手动开启」——
        删掉标记就该立刻停止派发，不能等到下一次发现扫描或靠控制台记得置离线。

        单课程模式（`--job-root` 直给、无 `--discover`）不受影响：那条路径的「开课」
        就是有人显式起了这个 hub。
        """
        if self.hub._discover_root is None:
            return True
        st = self.hub._stores.get(course)
        if st is None:
            return False
        if (st.job_root.parent / COURSE_ENABLE_MARKER).exists():
            return True
        with self._lock:
            if course not in self.hub._no_marker_warned:
                self.hub._no_marker_warned.add(course)
                _log(
                    f"跳过未开课的 {course}：无 {COURSE_ENABLE_MARKER}"
                    "（控制台「训练」写入 / 「停课」删除）——队列原样保留，开课即恢复派发",
                )
        return False

    def _course_dir_live(self, ent: Path, now: float) -> bool:
        """课程目录「在训」判据：**已开课标记**存在，且 `{remote-jobs,offline}` 之一存在且新鲜。

        ★ 开课标记（`training-enabled.txt`）是 2026-09-20 加的**显式闸**：没有它，hub 会把
        tmp/ 下每一门历史课（都有 remote-jobs/ 残影）都当成「在跑的课」登记进课程表，并继续
        把残留的 pending job 派给真 GPU worker（白烧租约）。用户口径：「课程开训需要用户手动
        开启」；标记由控制台开课写、停课删（`remote.protocol.COURSE_ENABLE_MARKER`）。
        """
        if not (ent / COURSE_ENABLE_MARKER).exists():
            return False
        # ★ 2026-09-25（评审 S-1）：活证据多一条「**已导出的任务包**」，且不再要求
        #   `remote-jobs`/`offline` 目录存在。为什么：**离线课本机不训练** ⇒ 那两个目录与
        #   training_log 一小时后全部变旧，而这门课在 hub 表里消失会造成两处静默失效：
        #   ① 控制台的「导出任务包」按课程名找目录（表里没它就找不到）⇒ 意图失配；
        #   ② 云机取包的 404 正文里 `known_courses` 也不会有它（排障人被指向错方向）。
        #   包是文件系统事实（与 `task_pack_path` 同源推导），与「开课标记」一样可靠。
        newest = 0.0
        for p in (
            ent / "remote-jobs",
            ent / "offline",
            ent / "training_log.jsonl",
            ent / f"task-{ent.name}.zip",
        ):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
        return newest > 0 and now - newest <= self.hub._discover_fresh

