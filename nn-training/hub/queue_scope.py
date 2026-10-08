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

## 依赖方向

`queue_scope → {common.protocol, hub.store, hub.task_pack}`（向下）。**不 import 任何兄弟混入**
——跨域调用一律经 `self`（这是「一个对象、一把锁」能成立的前提）。`hub/store` 是
**类型 + 构造**需求：`add_course` 要建 store，`_stores` 的注解要它；`hub/task_pack`
只取 `hold_progress_at`（★M1b：推 hold 镜像时要取进度锚，判据源与 `queue_offline` 同一份）。

## 对外名字（名字是契约）

`hub.server` re-export 整个 `_HubQueue`，所以 `hs._HubQueue` 仍可解析。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import WORKER_SEEN_WINDOW_SEC
from hub.queue_peer import QueuePeer
from hub.store import _JobStore
from hub.task_pack import hold_progress_at


class QueueScopeMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    #: jid -> 同时持有它的课程（≥2 = 身份歧义）：`course_of` 去重打点 + 拒答理由用它。
    _ambiguous: dict[str, list[str]]
    _auth_blocked_until: dict[str, float]
    _auth_fail: dict[str, int]
    _discover_root: Path | None
    _halt_default: bool
    _halts: dict[str, bool]
    _locate_cache: dict[str, str]
    _lock: Lock
    #: 启动参数里带过模式段的课程（`--course a=offline`；★M4b/P1-3：WARN + 忽略）。
    _mode_ignored: set[str]
    _now: Any
    _order: list[str]
    _solo: _JobStore | None
    _stores: dict[str, _JobStore]
    _workers: dict[str, float]

    def halt_of(self, course: str) -> bool:
        """本课程是否在停机态（单课程借 store 时恒看那一份 store 的旗标）。"""
        if self._solo is not None:
            return bool(self._solo.halt_workers)
        return bool(self._halts.get(course, self._halt_default))

    def all_halted(self) -> bool:
        """**所有已登记课程**都在停机态。

        空课程表 → False（`--discover` 刚起、还没有课程时“没课可停”，不是停机）——
        否则空闲 worker 会收到一个凭空的停机达令。
        """
        if self._solo is not None:
            return bool(self._solo.halt_workers)
        return bool(self._order) and all(self._halts.get(c, self._halt_default) for c in self._order)

    def set_halt(self, halt: bool, course: str = "") -> bool:
        """置/解停机达令；未知 course → False（不猜、不静默改写全局）。"""
        if self._solo is not None:
            self._solo.halt_workers = bool(halt)
            return True
        if course:
            if course not in self._stores:
                return False
            self._halts[course] = bool(halt)
            return True
        self._halt_default = bool(halt)
        self._halts.clear()
        return True

    # ---- 课程表自动发现（`--discover`） ----
    def add_course(self, name: str) -> bool:
        """登记一门课程（现建 `_JobStore`）；已登记/空名/未开发现 → False（幂等）。

        派生目录与 `--course` 启动参数**逐字节相同**（`<root>/<name>/remote-jobs` +
        `<root>/<name>/training_log.jsonl`）⇒ 自动发现的课与显式声明的课在观测面、诊断
        工具、`tmp/<course>` 约定里无法区分，也不该区分。
        """
        c = str(name or "")
        if not c or c in self._stores or self._discover_root is None:
            return False
        self._adopt_solo()
        self._stores[c] = _JobStore(
            self._discover_root / c / "remote-jobs",
            self._discover_root / c / "training_log.jsonl",
            now_fn=self._now,  # 时钟同源：租约时间戳与判定不能一边真墙钟一边假钟
        )
        self._order.append(c)
        # ★六轮 R3-a（P0-8）：发现/重启路径与构造路径同口径——不调 `_sync_hold` 的话，
        # 盘上的 hold 只在**读面**（清单/取包）生效，而派发闸的镜像还是空的 ⇒
        # 重启后一台盘能领走别人正在跑的课（接管闸与读面分叉）。
        self._sync_hold(c)
        return True

    def _adopt_solo(self) -> None:
        """从「单课程借 store」切到「多课程自有状态」：把进程级状态搬到自己身上。

        为什么必须搬：单课程时 halt / worker 登记 / 鉴权计数都住在那一份 store 里
        （既有用例直接预热 store 字段），一旦课程数变成 2，这些状态必须继续生效——不搬
        就是「多发现一门课，把停机达令、worker 登记、鉴权闭锁全悄悄清了」。
        """
        st = self._solo
        if st is None:
            return
        self._halt_default = bool(st.halt_workers)
        self._workers = dict(st._workers)
        self._auth_fail = dict(st._auth_fail)
        self._auth_blocked_until = dict(st._auth_blocked_until)
        self._solo = None

    def _registry(self) -> dict[str, float]:
        """生效的 worker 登记表（单课程 = store 的，多课程 = 自己的）。"""
        return self._solo._workers if self._solo is not None else self._workers

    def note_worker(self, worker_id: str) -> None:
        """登记一次 worker 轮询（peek / priority 入口）。空 id 不记（无身份无法去重计数）。"""
        wid = (worker_id or "").strip()
        if not wid:
            return
        reg = self._registry()
        with self._lock:
            reg[wid] = self._now()

    def active_worker_count(self) -> int:
        """窗口内**不同** worker 数（避让判据「还有别的卡能接手」的唯一口径）。"""
        now = self._now()
        with self._lock:
            return len(
                {wid for wid, seen in self._registry().items() if now - seen <= WORKER_SEEN_WINDOW_SEC}
            )

    # ---- 课程表与归属 ----
    def courses(self) -> list[str]:
        return list(self._order)

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
        hit = self._locate_cache.get(jid)
        if hit is not None:
            return hit
        holders: list[str] = []
        for course in self._order:
            try:
                if (self._stores[course].job_root / jid).exists():
                    holders.append(course)
            except OSError:
                continue
        if not holders:
            return None
        if len(holders) > 1:
            self._note_ambiguous(jid, holders)
            return None  # 歧义**绝不**进缓存（一次歧义会变成永久归属）
        self._locate_cache[jid] = holders[0]
        return holders[0]

    def _store_of(self, job_id: str) -> _JobStore | None:
        course = self.course_of(job_id)
        return None if course is None else self._stores.get(course)

    def mode_ignored(self, course: str) -> bool:
        """启动参数里这门课带过模式段（★M4b / P1-3：`--course a=offline` ⇒ WARN + 忽略）。

        只服务 `/admin/courses` 行上的 `mode_ignored` 标记（排障时知道「有人还在按老脚本
        传模式」）；不参与任何派发判据。
        """
        return course in self._mode_ignored

    def active_courses(self) -> int:
        """**在实时派发**的课程数（竞速判据的分母）：有待领或未过期在飞 job，且未被接管。

        ★M4b（plan §1.5.4-P2-7）：课程不再有模式 —— 被 **live hold** 压住的课不算
        （它的队列被派发闸压着，没人能领）；已跑完无待办的课不算（没活可抢）。
        读数会随接管建立/掉线在 1↔N 之间跳（§69 写明），这是**观测口径**不是派发输入。
        """
        n = 0
        for course in self._order:
            if str(self.hold_of(course).get("state") or "") == "live":
                continue
            st = self._stores[course]
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
        st = self._stores.get(course)
        if st is None:
            return
        hold = self.hold_of(course)
        if str(hold.get("state") or "") != "live":
            st.hold_meta = {}
            return
        # 到这里 hold 是活的（`hold_state` 只看进度）：镜像只带判活需要的两样
        # （`hold_blocked` 自己拿时钟对窗，不依赖「推的人刚刚才推过」）。
        st.hold_meta = {
            "worker_id": str(hold.get("worker_id") or ""),
            "last_progress_at": float(hold_progress_at(hold)),
        }

    def _note_ambiguous(self, job_id: str, holders: list[str]) -> None:
        """歧义只报一次（按 jid 去重）：`course_of` 在每个 job 作用域请求上都会跑，
        逐次打点会把日志刷爆，反而埋掉真正要看的那一行。"""
        if job_id in self._ambiguous:
            return
        self._ambiguous[job_id] = list(holders)
        print(
            f"[hub-server] ⚠ job 身份歧义：job={job_id} 同时存在于 "
            f"{'、'.join(holders)} —— 一律拒答（不猜归属）。"
            "多半是旧 runId/旧代码留下的同名 job 目录：清掉非当前 runId 的 "
            "`remote-jobs/<jid>`（或换 runId 重跑）即可。",
            flush=True,
        )

    def ambiguous_jids(self) -> dict[str, list[str]]:
        """同一 jid 挂在 ≥2 门课上的清单（`/admin/queue` 的观测面，只读）。

        与 `course_of` 同一个事实（「哪几门课持有这个 jid」）的两个方向：那边按 jid 逐课探，
        这边按课程列目录一次扫完 —— 观测面要的是**全量**，且不在派发热路径上。
        单课程（<2 门）恒空，零开销。
        """
        if len(self._order) < 2:
            return {}
        seen: dict[str, list[str]] = {}
        for course in self._order:
            try:
                entries = list(self._stores[course].job_root.iterdir())
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
