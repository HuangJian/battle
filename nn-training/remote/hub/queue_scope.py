"""remote/hub/queue_scope.py — 课程表 · 归属路由 · 模式 · 停机达令 · worker 登记。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。本簇是**状态的主人**：`_stores` / `_order` /
`_modes` / `_solo` / `_locate_cache` / `_halts` / `_workers` 都写在它身上，而组合类的
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

`queue_scope → {common.protocol, remote.hub.store}`（向下）。**不 import 任何兄弟混入**
——跨域调用一律经 `self`（这是「一个对象、一把锁」能成立的前提）。`remote/hub/store` 是
**类型 + 构造**需求：`add_course` 要建 store，`_stores` 的注解要它。

## 对外名字（名字是契约）

`remote.hub_server` re-export 整个 `_HubQueue`，所以 `hs._HubQueue` 仍可解析。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import (
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    COURSE_MODES,
    WORKER_SEEN_WINDOW_SEC,
)
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore


class QueueScopeMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _auth_blocked_until: dict[str, float]
    _auth_fail: dict[str, int]
    _discover_root: Path | None
    _halt_default: bool
    _halts: dict[str, bool]
    _locate_cache: dict[str, str]
    _lock: Lock
    _modes: dict[str, str]
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
    def add_course(self, name: str, mode: str = COURSE_MODE_ONLINE) -> bool:
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
        m = (mode or "").strip().lower()
        self._modes[c] = m if m in COURSE_MODES else COURSE_MODE_ONLINE
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
        """job_id → 归属课程；**找不到返回 None**（不是空串！）。

        为什么必须用 None 区分：单课程队列（以及旧单课程 hub）的课程名**就是空串**
        （`tmp/nocourse` 那套约定）。用空串兼作「找不到」会把它当成找不到 —— 直接后果
        是 `/jobs/peek` 刚列出的 job 立刻解析不到归属，handler 打到哨兵路径上 500
        （2026-09-18 白测一次的真故障）。

        为什么搜目录而不是搜账本：账本行里没有课程字段（磁盘契约不变），而
        `<job_root>/<job_id>/` 的存在本身就是归属证据，且是一次 fs 调用 —— 比读账本便宜。
        """
        jid = str(job_id or "")
        if not jid:
            return None
        hit = self._locate_cache.get(jid)
        if hit is not None:
            return hit
        for course in self._order:
            try:
                if (self._stores[course].job_root / jid).exists():
                    self._locate_cache[jid] = course
                    return course
            except OSError:
                continue
        return None

    def _store_of(self, job_id: str) -> _JobStore | None:
        course = self.course_of(job_id)
        return None if course is None else self._stores.get(course)

    def mode_of(self, course: str) -> str:
        return self._modes.get(course, COURSE_MODE_ONLINE)

    def offline_courses(self) -> list[str]:
        return [c for c in self._order if self.mode_of(c) == COURSE_MODE_OFFLINE]

    def set_mode(self, course: str, mode: str) -> bool:
        """热切一门课的模式（在线/离线）。非法课程/模式 → False。

        volatile（与 halt 同性质）：重启回启动参数给定的模式。
        """
        if course not in self._stores:
            return False
        m = (mode or "").strip().lower()
        if m not in COURSE_MODES:
            return False
        self._modes[course] = m
        return True

    def active_courses(self) -> int:
        """**在实时派发**的课程数（竞速判据的分母）：非离线，且有待领或未过期在飞 job。

        离线课程不算（用户口径：它不实时派发 PPO）；已跑完无待办的课程不算（没活可抢，
        把它算进去只会白降压竞速阈值）。
        """
        n = 0
        for course in self._order:
            if self.mode_of(course) == COURSE_MODE_OFFLINE:
                continue
            st = self._stores[course]
            if st.claimable_job_ids() or st.inflight():
                n += 1
        return n

