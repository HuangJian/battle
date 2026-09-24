"""remote/hub/queue_discover.py — 课程表自动发现（`--discover`）三相。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。三相是一条链，判据同源：

* `discover` —— 扫 `<traj_root>/<course>/{remote-jobs,offline}`，把**新鲜且未登记**的课
  登记进来。为什么以**磁盘**为发现源、而不是让控制台/训练器走一次 HTTP 注册：训练侧把
  job 发布到 `<traj>/remote-jobs` 是**文件系统事实**（hub 与 trainer 共享同一份盘），
  「有新课程在跑」这件事本身就写在盘上。再加一条注册旁路就是「会失败、会乱序、会忘了调」
  的第二事实源——而漏注册的后果是那门课**永久饿死**（跨课程轮转表里没有它），
  且表面上「训练正常」。
* `_serves_course` —— 派发前的**开课标记闸**（`training-enabled.txt`）。为什么发现时判过
  还要再判一次（2026-09-20 事故）：课程表是**发现那一刻**建的，而 `remote-jobs/` 里躺着
  的 pending job 不会自己消失 ⇒ 没有这道闸，任何在旧表/旧代码里登记过的课程会把它的
  **陈旧 job 继续派给真 GPU worker**（白烧租约），而训练侧什么都看不到。
* `_course_dir_live` —— 「在训」判据：**开课标记**存在，且 `{remote-jobs,offline}` 之一
  存在且新鲜（窗口 = `_discover_fresh`，缺省 1h：一个 PPO 轮次是分钟级，而几天前的陈旧
  实验目录永远不该被误当成「在跑的课」）。

单课程模式（`--job-root` 直给、无 `--discover`）不受这三相影响：那条路径的「开课」就是
有人显式起了这个 hub。

## 依赖方向

`queue_discover → {common.protocol, remote.hub.store}`（向下，`remote.hub.store` 是
`_stores` 的注解需求）。**不 import 任何兄弟混入**——`self.add_course` / `self._lock` /
`self._now` 全经 `self`。

## 两个类常量

`DISCOVER_FRESH_SEC`（新鲜窗口）与 `DISCOVER_SCAN_MIN_SEC`（两次扫描的最小间隔）随本簇
搬进本模块 —— 它们只被 `discover` / `_course_dir_live` 读。注意它们与
`hub_server.DISCOVER_SCAN_SEC`（**后台兜底线程**的节拍，启动参数用）是两件事。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import COURSE_ENABLE_MARKER, ProtocolError, parse_course_arg
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore


class QueueDiscoverMixin(QueuePeer):
    """域混入：见模块头部。"""

    #: 自动发现时判定「课程目录是不是活的」的新鲜窗口（秒）。
    #: 一个 PPO 轮次是分钟级（rollout 采集 + 云端结算 10–30min），窗口取 1h：正在跑的课
    #: 每轮都会在 `remote-jobs/` 里增删条目、往 jsonl 追加行，秒级就落在窗口内；而几天前
    #: 的陈旧实验目录（同样的磁盘形状，同样残留 pending job）永远不会被误当成「在跑的课」
    #: ——误登记会把已死课程的 job 继续派给真 GPU worker（白烧租约）。
    DISCOVER_FRESH_SEC = 3600.0
    #: 两次扫描之间的最小间隔（秒）：`claim_next` 是派发热路径（worker 每几秒一轮询），
    #: 每次 readdir 都扫一遍没必要，也没意义。
    DISCOVER_SCAN_MIN_SEC = 2.0

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _discover_fresh: float
    _discover_last: float
    _discover_root: Path | None
    _lock: Lock
    _no_marker_warned: set[str]
    _now: Any
    _stores: dict[str, _JobStore]

    def discover(self) -> list[str]:
        """扫 `<traj_root>/<course>/{remote-jobs,offline}`，把新鲜且未登记的课程登记进来。

        返回本次新增的课程（目录序，稳定）。`--discover` 未开 → 恒空（零开销）。

        为什么以**磁盘**为发现源、而不是让控制台/训练器走一次 HTTP 注册：训练侧把 job
        发布到 `<traj>/remote-jobs` 是**文件系统事实**（hub 与 trainer 共享同一份盘），
        所以「有新课程在跑」这件事本身就写在盘上。再加一条注册旁路就是「会失败、会乱序、
        会忘了调」的第二事实源——而漏注册的后果是那门课**永久饿死**（跨课程轮转表里
        没有它），且表面上「训练正常」。
        """
        root = self._discover_root
        if root is None:
            return []
        now = self._now()
        if now - self._discover_last < self.DISCOVER_SCAN_MIN_SEC:
            return []
        self._discover_last = now
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
            if ent.name in self._stores:
                continue
            try:
                parse_course_arg(ent.name)
            except ProtocolError:
                continue  # 非课程目录（tmp/training-start 之类）——安静跳过
            if not self._course_dir_live(ent, now):
                continue
            if self.add_course(ent.name):
                added.append(ent.name)
        if added:
            print(f"[hub-server] discovered courses: {', '.join(added)}", flush=True)
        return added

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
        if self._discover_root is None:
            return True
        st = self._stores.get(course)
        if st is None:
            return False
        if (st.job_root.parent / COURSE_ENABLE_MARKER).exists():
            return True
        with self._lock:
            if course not in self._no_marker_warned:
                self._no_marker_warned.add(course)
                print(
                    f"[hub-server] 跳过未开课的 {course}：无 {COURSE_ENABLE_MARKER}"
                    "（控制台「训练」写入 / 「停课」删除）——队列原样保留，开课即恢复派发",
                    flush=True,
                )
        return False

    def _course_dir_live(self, ent: Path, now: float) -> bool:
        """课程目录「在训」判据：**已开课标记**存在，且 `{remote-jobs,offline}` 之一存在且新鲜。

        ★ 开课标记（`training-enabled.txt`）是 2026-09-20 加的**显式闸**：没有它，hub 会把
        tmp/ 下每一门历史课（都有 remote-jobs/ 残影）都当成「在跑的课」登记进课程表，并继续
        把残留的 pending job 派给真 GPU worker（白烧租约）。用户口径：「课程开训需要用户手动
        开启」；标记由控制台开课写、停课删（`common.protocol.COURSE_ENABLE_MARKER`）。
        """
        if not (ent / COURSE_ENABLE_MARKER).exists():
            return False
        for sub in ("remote-jobs", "offline"):
            d = ent / sub
            if not d.is_dir():
                continue
            newest = 0.0
            for p in (d, ent / "training_log.jsonl"):
                try:
                    newest = max(newest, p.stat().st_mtime)
                except OSError:
                    continue
            if newest > 0 and now - newest <= self._discover_fresh:
                return True
        return False

