"""remote/bulk_sched.py — bulk 单通道 + 控制面让路（plan/transfer-scheduling §2.2 / P0）。

要解决的问题（`docs/nn/remote-transport.md` §21 的现场）：大 body（payload 下载 / result 回传）与
控制面小包共用一条链路时，**大 body 会把控制环拖到分钟级**（取消/问询读不到回音 ⇒ 调度开环），
而两条大 body 并发会让链路双侧静默（§104）。

三条流的分工（§2.2）：

  · **P0 控制面** —— peek / priority / claim / start / ready / abandon / status / heartbeat /
    release / fail。**永不进 bulk 队列**：它们各自是独立连接（`urllib` 每次请求新开
    connection，没有连接池），所以「旁路」在传输层天然成立；本模块只负责让 bulk **让路**
    （控制面在途时 bulk 在分片间隙暂停）。
  · **P1 bulk 高优** —— `post_result`、**开算前的关键下载**。至多一条在途；**不被抢断**
    （POST 大 body 没有安全的 Range 语义，抢断 = 整份白传）。
  · **P2 bulk 低优** —— 预取下载（软持有）。P1 在等 / 控制面在途时**不开工**；避让 P1；
    **可立即打断**（丢半截，稍后重下；payload 幂等 + `payload_sha256` 校验）。

**抢占权专属 P1**（2026-09-25）：控制面（P0）**只让路、不抢占**。它有自己的独立 socket，
怕的是被大 body 拖到分钟级 —— 那由 `pause_if_needed`（分片间隙暂停，预算 ≤5s/次）解决；
而「每 1.5s 一个取消环小包必然触发一次抢占」会让多 MB 预取**数学上永远传不完**
（现场：预取零命中而每个 job 都 `reroll=1(wasted 0.25MB)`）。

让路预算（硬约束，写死并有用例钉住）：单次让路总预算 ≤ `PAUSE_BUDGET_SEC = 5s`，
上界取两侧较小者 —— worker 侧 `BODY_IDLE_TIMEOUT_SEC = 45s`（`_read_body` 的空闲超时：停久了
会被判「body 停滞」而重试）与 hub 侧 `SEND_TIMEOUT_SEC = 60s`（分片写超时）。**两个都要看**，
只看 45s 是最容易犯的错。

为什么不复用 `threading.Lock`：并发申请下要区分**优先级**（P1 可以把 P2 挤走）与**可打断**
（P2 在分片间隙自行退出），而锁没有优先级也没有「持有者轮次」的概念——用它就只剩「先来先等」，
P1 会被一份 5 分钟的预取堵在后面。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: bulk 优先级（§2.2 三类流里的后两类；P0 控制面不进本模块）。
BULK_P1_CRITICAL = "P1"
BULK_P2_PREFETCH = "P2"

#: 单次让路的总预算（秒）——见模块 docstring 的上界推导（45s / 60s 取小）。
PAUSE_BUDGET_SEC = 5.0
#: 让路的单步暂停（秒）：每步复查「控制面是否还需要带宽」，避免一口气停满预算。
BULK_YIELD_STEP_SEC = 0.5
#: 槽位等待的轮询步长（秒）：`Event.wait` 兜底用（真释放会立刻唤醒）。
BULK_WAIT_STEP_SEC = 0.05


class BulkPreemptError(RuntimeError):
    """P2 bulk 被 **P1** 挤走：**丢半截、稍后重下**（payload 幂等，不算失败）。

    为什么要独立异常：它绝不能落进「重试 3 次仍失败 ⇒ RetryableError ⇒ release 租约回池」
    那条路——预取本来就是可有可无的提前量，丢半截只是少赚一次命中，把它当失败会把
    「网络抖动」误报成节点故障。也正因为如此，抢占重排的**上限用完时抛的仍是它**
    （不是 `RetryableError`）：`_prefetch_fill` 只吞前者。

    `bytes_read`：被挤走时已收的字节（丢掉的半截）。作废字节要入账，否则预取的
    真实成本在 wire 账上恒等于 0（现场 `job prefetch: … 合计=0.00MB`）。
    """

    def __init__(self, message: str = "", *, bytes_read: int = 0) -> None:
        super().__init__(message or "定期预取被高优 bulk 挤走：丢半截，稍后重下（payload 幂等）")
        self.bytes_read = int(bytes_read)


class BulkScheduler:
    """bulk 单通道调度器（线程安全；一个 worker 进程一份）。

    用法（发送方必须自己保证「整段传输在 slot 内完成」）：

        with sched.slot(BULK_P2_PREFETCH, label="prefetch j1") as tok:
            ... 分片读写间隙调用 sched.pause_if_needed(tok) 与 sched.check_preempted(tok) ...
    """

    def __init__(
        self,
        *,
        clock: Any = time.time,
        log: Any = None,
        wait_step_sec: float = BULK_WAIT_STEP_SEC,
        yield_budget_sec: float = PAUSE_BUDGET_SEC,
        yield_step_sec: float = BULK_YIELD_STEP_SEC,
    ) -> None:
        self._clock = clock
        self._log = log
        self._wait_step = float(wait_step_sec)
        self._yield_budget = float(yield_budget_sec)
        self._yield_step = float(yield_step_sec)
        # 槽位状态
        self._lock = threading.Lock()
        self._released = threading.Event()
        self._released.set()
        self._inflight = 0
        self._holder_prio: str | None = None
        self._seq = 0  # 持有者轮次（token）
        self._preempt_at = 0  # 第几轮之后到达过 P1/控制面（P2 的退出判据）
        self._holding = 0  # 当前持有者的 token
        # 控制面状态
        self._control = 0
        #: 与 `_control` 同行 ±1（⇒ 恒等）：它在 `slot()` 里是 P2 的开工否决位之一。
        #: 名字留「waiting」是历史原因——P0 从不排队（独立 socket），所以在途 ≡ 在等。
        self._control_waiting = 0
        #: 正在等槽位的 P1 数（含排在另一个 P1 后面）：P2 的开工否决位。
        #: 抢到槽位的那一瞬就 −1（持有期间恒 0）。
        self._p1_waiting = 0
        # 统计
        self._queue_wait_total = 0.0
        self._queue_wait_max = 0.0
        self._queue_waits = 0
        self._yield_count = 0
        self._yield_sec_total = 0.0
        self._preempted_count = 0
        self._p0_ms: list[float] = []
        #: **当前 slot（= 一次传输）** 的让路账：`{"token", "count", "sec"}`。让路是分片间隙
        #: 反复发生的（现场一次 payload 下载能让路 6 次），逐次打点会把日志刷爆 ⇒ 先记在这里，
        #: 由 `slot()` 退出时**合并成一行**（见 `slot` 的 finally）。
        self._yield_cur: dict[str, Any] | None = None

    # ------------------------------------------------------------------ 槽位

    def set_log(self, log: Any) -> None:
        """注入日志函数（worker 在进入循环时把现场日志交给调度器）。"""
        self._log = log

    def inflight(self) -> int:
        """当前在途 bulk 数（**不变量：任意时刻 ≤ 1**）。"""
        with self._lock:
            return self._inflight

    def holder_prio(self) -> str | None:
        with self._lock:
            return self._holder_prio

    @contextmanager
    def slot(self, prio: str, *, label: str = "") -> Iterator[int]:
        """占住唯一的 bulk 槽位（拿不到就在此等；P1 可以把 P2 挤出去）。

        退出时把**本次传输**的让路账合并成一行（`bulk <label>: 让路合计 …`）——
        让路在分片间隙反复发生，逐次打点只会刷屏。
        """
        if prio not in (BULK_P1_CRITICAL, BULK_P2_PREFETCH):
            raise ValueError(f"未知 bulk 优先级: {prio!r}")
        t0 = self._clock()
        waited = False
        if prio == BULK_P1_CRITICAL:
            with self._lock:
                self._p1_waiting += 1
        try:
            with self._lock:
                if self._inflight and prio == BULK_P1_CRITICAL and self._holder_prio == BULK_P2_PREFETCH:
                    # ★ 抢占请求：P2 持有者在下一个分片间隙看到它就丢半截退出（见 check_preempted）。
                    #   这是**唯一**的抢占来源（控制面只让路，2026-09-25）。
                    self._preempt_at = self._holding
            while True:
                with self._lock:
                    # P2 的开工门禁：此刻有 P1 在等 / 有控制面在途 ⇒ 不许新开工
                    # （docstring 一直这么承诺，但这两个计数此前只写不读 —— 2026-09-25 补上）。
                    blocked = prio == BULK_P2_PREFETCH and (
                        self._p1_waiting > 0 or self._control_waiting > 0
                    )
                    if not self._inflight and not blocked:
                        self._seq += 1
                        self._inflight = 1
                        self._holder_prio = prio
                        self._holding = self._seq
                        token = self._seq
                        self._released.clear()
                        # 本次传输的让路账从零开始（退出时合并成一行）
                        self._yield_cur = {"token": token, "count": 0, "sec": 0.0}
                        break
                waited = True
                self._released.wait(self._wait_step)
        finally:
            if prio == BULK_P1_CRITICAL:
                with self._lock:
                    self._p1_waiting -= 1
        if waited:
            dt = self._clock() - t0
            with self._lock:
                self._queue_wait_total += dt
                self._queue_waits += 1
                self._queue_wait_max = max(self._queue_wait_max, dt)
            if self._log is not None and dt > 1.0:
                self._log(f"bulk {label or prio}: 排队 {dt:.1f}s 才拿到单通道（§2.2：同一时刻仅 1 条）")
        try:
            yield token
        finally:
            with self._lock:
                self._inflight = 0
                self._holder_prio = None
                self._holding = 0
                self._released.set()
                cur, self._yield_cur = self._yield_cur, None
            # 让路账**合并成一行**（一次传输一条）：现场一次 payload 下载能让路 6 次，
            # 逐次打点只是刷屏；秒数与次数在这里一次说清。锁外打点（日志是 I/O）。
            if (
                self._log is not None
                and cur is not None
                and cur["token"] == token
                and cur["count"]
            ):
                self._log(
                    f"bulk {label or prio}: 让路合计 {cur['sec']:.1f}s / {cur['count']} 次"
                    f"（控制面在途；单次预算 ≤ {self._yield_budget:.0f}s）"
                )

    def pace(self, token: int, prio: str = BULK_P1_CRITICAL) -> float:
        """分片间隙的让路回调（worker 把 `_read_body(pace=…)` 接到这里）。

        两件事：① P2 先查是否已被 P1 挤走（**必须先查**：下面那个暂停点在没有控制面
        在途时会立刻返回，也就永远不会去查抢占——被 P1 挤走的 P2 就会一路传到底）；
        ② 控制面在途则暂停（预算内）。

        **返回本次实际让路的秒数**：`_read_body` 用它算「净值 elapsed」喂给速率判据
        （让路不是链路的错，不该把预取判成坏签）。
        """
        if prio == BULK_P2_PREFETCH:
            self.check_preempted(token)
        return self.pause_if_needed(token)

    def check_preempted(self, token: int) -> None:
        """P2 的持有者在分片间隙调用：被 **P1** 挤走 ⇒ 抛 `BulkPreemptError`（丢半截重下）。"""
        with self._lock:
            preempted = self._preempt_at >= token > 0
        if preempted:
            with self._lock:
                self._preempted_count += 1
                self._released.set()  # 让等待者立刻看到我们（马上）要退
            raise BulkPreemptError("定期预取被高优 bulk 挤走：丢半截，稍后重下（payload 幂等）")

    # ------------------------------------------------------------------ 控制面

    @contextmanager
    def control(self, *, label: str = "") -> Iterator[None]:
        """标记一段**控制面**请求（不进 bulk 队列，只用来让 bulk 让路）。

        独立连接由 `urllib` 天然保证（每次请求新开 connection）；本上下文只做两件事：
        ① `control_active()` 为真 ⇒ bulk 分片间隙暂停；② 计数「有控制面在等」⇒ P2 不再新开工。

        **不抢占**（2026-09-25）：取消环每 1.5s 一个 `/jobs/{id}/status` 是常态，让一个
        「每 1.5s 必然触发」的事件去打断 P2（丢半截重下，3 次 attempt 上限 ≈1.5MB）
        ⇒ 多 MB 的预取**数学上永远传不完**（现场：预取零命中）。P0 怕的是被大 body 拖到
        分钟级，而那由**让路**（`pause_if_needed`，预算 ≤5s/次）解决就够了。抢占权专属 P1。

        `_control_waiting` 与 `_control` 在同一对语句里 ±1（不是独立的时间概念）；它只作
        `slot()` 里 P2 门禁的读数。
        """
        with self._lock:
            self._control += 1
            self._control_waiting += 1
        t0 = self._clock()
        try:
            yield
        finally:
            ms = (self._clock() - t0) * 1000.0
            with self._lock:
                self._control -= 1
                self._control_waiting -= 1
                self._p0_ms.append(ms)
                if len(self._p0_ms) > 512:  # 有界：只留最近一段（P0 延迟的 p95 判据）
                    del self._p0_ms[: len(self._p0_ms) - 512]

    def control_active(self) -> bool:
        with self._lock:
            return self._control > 0

    def pause_if_needed(self, token: int) -> float:
        """bulk 分片间隙的让路点：控制面在途时暂停（预算内、逐步复查）。

        返回本次实际暂停秒数（0.0 = 没有让路）。预算 = `PAUSE_BUDGET_SEC`（单次动作），
        写死且有用例钉住 —— 停久了会被 worker 自己的 `BODY_IDLE_TIMEOUT_SEC=45s` 判成
        「body 停滞」而整份重试，也会撞上 hub 的 `SEND_TIMEOUT_SEC=60s`。

        本函数**只在 `slot()` 内**被调用（worker 的 `pace` 回调就是这么接的）：让路账记在
        所属 slot 上，由 `slot()` 退出时合并成**一行**（一次传输一条 log，见 `slot`）。
        """
        if not self.control_active():
            return 0.0
        spent = 0.0
        while spent < self._yield_budget and self.control_active():
            step = min(self._yield_step, self._yield_budget - spent)
            time.sleep(step)
            spent += step
            with self._lock:
                self._yield_count += 1
        with self._lock:
            self._yield_sec_total += spent
            cur = self._yield_cur
            if cur is not None and cur["token"] == token:
                cur["count"] += 1
                cur["sec"] += spent
        # 让路之后 P2 可能已被挤走 —— 但只对 P2 查：`_preempt_at` 只有 P1 会写
        # （控制面不写，2026-09-25），P1 查它 = 自己把自己打断。
        # ⚠ 这个尾检查是**承重**的：它抓的是「暂停**期间**被 P1 写上标记」那种情形
        # （直接调 `pause_if_needed` 的调用方不会经过 `pace()` 的前置检查）。
        if self.holder_prio() == BULK_P2_PREFETCH:
            self.check_preempted(token)
        return spent

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict:
        """观测面：`inflight_bulk` / 排队 / 让路 / 抢占 / `p0_rt_ms`（p50/p95/max）。"""
        with self._lock:
            ms = sorted(self._p0_ms)
            n = len(ms)
            p50 = ms[n // 2] if n else 0.0
            p95 = ms[min(n - 1, int(n * 0.95))] if n else 0.0
            return {
                "inflight_bulk": self._inflight,
                # P2 门禁的两个读数（§3.2）：`p1_waiting` 长跑不归零 = 泄漏 ⇒ P2 永久空转。
                "p1_waiting": self._p1_waiting,
                "control_waiting": self._control_waiting,
                "queue_waits": self._queue_waits,
                "queue_wait_sec": round(self._queue_wait_total, 3),
                "queue_wait_max_sec": round(self._queue_wait_max, 3),
                "yield_count": self._yield_count,
                "yield_sec": round(self._yield_sec_total, 3),
                "preempted": self._preempted_count,
                "p0_count": n,
                "p0_rt_ms_p50": round(p50, 1),
                "p0_rt_ms_p95": round(p95, 1),
                "p0_rt_ms_max": round(ms[-1], 1) if n else 0.0,
            }

    def reset(self) -> None:
        """归零统计（用例与长跑会话用；不动槽位状态）。"""
        with self._lock:
            self._queue_wait_total = 0.0
            self._queue_wait_max = 0.0
            self._queue_waits = 0
            self._yield_count = 0
            self._yield_sec_total = 0.0
            self._preempted_count = 0
            self._p0_ms.clear()


def control_path(path: str) -> bool:
    """该 HTTP 路径是不是**控制面**（§2.2 的左列；bulk = payload 下载 与 result 回传）。

    判据是**路径**而不是调用点：这样「谁让路」只有一处实现，加一个新控制端点也不会漏
    （`_request` 是唯一的转发点）。bulk 侧只两处：`/jobs/{id}/payload` 与 `/jobs/{id}/result`。
    """
    p = (path or "").split("?", 1)[0]
    if p.endswith("/payload") or p.endswith("/result"):
        return False
    if p.endswith("/blob") or p.endswith("/code") or p.endswith("/ts_code"):
        return False  # 大 body 的 GET（内容寻址缓存）也算 bulk
    return p.startswith("/jobs/") or p.startswith("/admin/")
