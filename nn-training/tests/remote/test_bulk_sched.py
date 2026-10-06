"""test_bulk_sched.py — bulk 单通道 + 控制面让路（plan/transfer-scheduling §2.2 / P0，2026-09-22）。

要钉住的三条不变量（现场依据：`docs/nn/remote-transport.md` §21 —— 大 body 把控制环拖到分钟级、
两条大 body 并发让链路双侧静默）：

1. **单通道**：任意并发申请下 `inflight_bulk == 1`（P1/P2 一起申请也只有一条在途）。
2. **P1 不被抢断**：`post_result` 一旦开传就只能等它传完（POST 大 body 没有安全 Range，
   抢断 = 整份白传）；P2 预取相反——被 **P1** 挤到就该**丢半截**（幂等可重下）；
   而控制面（P0）**只让它让路、不抢占**（2026-09-25：取消环每 1.5s 一个包是常态，
   让它能抢占 = 多 MB 预取数学上永远传不完）。
3. **让路预算有界**：单次让路 ≤ `PAUSE_BUDGET_SEC`；常量与两侧超时的**安全裕度断言**
   住在 `tests/remote/test_pause_budget.py`（plan §5 点名的文件），这里只测「跑到预算就停」。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.bulk_sched import (
    BULK_P1_CRITICAL,
    BULK_P2_PREFETCH,
    BULK_YIELD_STEP_SEC,
    BulkPreemptError,
    BulkScheduler,
    control_path,
)


@pytest.fixture(autouse=True)
def _clean_bulk():
    """每个用例一份干净的调度器账（模块级 `_BULK` 跨用例共享）。"""
    W._BULK.reset()
    W._WIRE.clear()
    yield
    W._BULK.reset()
    W._WIRE.clear()


def _sched(**kw) -> BulkScheduler:
    """小步长调度器（用例里别真等 0.5s/步）。"""
    kw.setdefault("yield_step_sec", 0.01)
    kw.setdefault("wait_step_sec", 0.005)
    return BulkScheduler(**kw)


# --------------------------------------------------------------- 1. 单通道


class _CountingEvent(threading.Event):
    """`threading.Event` + 「当前有几个线程卡在 `wait` 上」+ 等它到齐（**事件驱动**）。

    为什么要它：`slot()` 内部的等待者本身不发出任何信号（`queue_waits` 要等它**拿到**槽位
    才记账），所以「其余 7 个已经排上队」只能从等待原语上观测。每次进入/退出 `notify_all`，
    `wait_until` 因此是条件驱动的：不轮询、不等固定时长。
    """

    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self._waiting = 0

    def wait(self, timeout: float | None = None) -> bool:
        with self._cond:
            self._waiting += 1
            self._cond.notify_all()
        try:
            return super().wait(timeout)
        finally:
            with self._cond:
                self._waiting -= 1
                self._cond.notify_all()

    def wait_until(self, waiting: int, timeout: float) -> bool:
        """等到「同时在等的线程数 ≥ `waiting`」；`timeout` 只是挂起兜底（不是同步手段）。"""
        with self._cond:
            return self._cond.wait_for(lambda: self._waiting >= waiting, timeout)


def test_single_channel_under_concurrency():
    """N 个并发申请（P1/P2 混合）⇒ 恰好一个持有者，其余 N-1 个**必然**排队。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：原来的「睡 10ms 模拟传输」隐含
    「8 个线程会同时到达」—— 而线程启动本身就会错开，机器一满载，后面的还没起来前面的
    已经跑完，`queue_waits` 就凑不够（实测 4 < 6）；真正的不变量（`peak == 1`）其实一直成立。
    现在：首个持有者等**计数事件**报出「其余 7 个都已卡在等待上」才放行 ⇒ 排队是构造性的，
    与调度无关，而且可以钉**精确值**（`== 7` 而不是 `>= 6`）。
    """
    s = _sched(wait_step_sec=0.001)
    n = 8
    inside = 0
    peak = 0
    lock = threading.Lock()
    done = threading.Event()
    left = n
    waiting = _CountingEvent()
    s._released = waiting  # 只换等待原语（仍是 Event），语义不变

    def worker(i: int) -> None:
        nonlocal inside, peak, left
        prio = BULK_P1_CRITICAL if i % 2 == 0 else BULK_P2_PREFETCH
        with s.slot(prio, label=f"t{i}") as token:
            with lock:
                inside += 1
                peak = max(peak, inside)
            # **只有首个持有者**等其余申请者排上队 —— 它持着槽位，其余 7 个必然只能排队。
            # 只能用轮次判别：后续持有者时最多只有 6 个在等（它自己就是第 7 个还没进队列的
            # 人），再等「7 个」就会死等到超时。
            if token == 1:
                assert waiting.wait_until(n - 1, timeout=10.0), "其余申请者没能在 10s 内排上队"
            with lock:
                inside -= 1
                left -= 1
                if left == 0:
                    done.set()

    for i in range(n):
        threading.Thread(target=worker, args=(i,), daemon=True).start()
    assert done.wait(10), "并发申请没有全部完成（单通道死锁？）"
    assert peak == 1, f"同一时刻有 {peak} 条 bulk 在途（§2.2 要求恰好 1）"
    st = s.stats()
    assert st["inflight_bulk"] == 0
    # 恰好 N-1 个申请者排过队（构造出来的，不是碰运气碰上的）
    assert st["queue_waits"] == n - 1, f"排队记账不对：{st['queue_waits']} != {n - 1}"
    # 门禁的两个输入必须归零（泄漏 ⇒ P2 永久空转；2026-09-25）
    assert st["p1_waiting"] == 0 and st["control_waiting"] == 0, st


def test_p1_waits_for_p1_then_runs():
    """P1 等 P1：先到者传完，后到者才拿到通道（无抢断、无饿死）。"""
    s = _sched()
    order: list[str] = []
    release = threading.Event()

    entered = threading.Event()
    waiting = _CountingEvent()
    s._released = waiting  # 只换等待原语（仍是 Event）

    def first() -> None:
        with s.slot(BULK_P1_CRITICAL, label="first"):
            order.append("first-enter")
            entered.set()
            release.wait(5)
            order.append("first-exit")

    t = threading.Thread(target=first, daemon=True)
    t.start()
    assert entered.wait(5), "先到者没进通道"

    def second() -> None:
        with s.slot(BULK_P1_CRITICAL, label="second"):
            order.append("second-enter")

    t2 = threading.Thread(target=second, daemon=True)
    t2.start()
    # 事件驱动：等到「第二个**已排上队**」再断言 —— 槽位在 first 手里，它不可能已进通道。
    # （原来靠 `time.sleep(0.1)`：满载时线程还没起来就断言，等于在赌调度 —— 而且它还会
    #  变成假绿：第二个根本没申请时 `order == ["first-enter"]` 也成立。）
    assert waiting.wait_until(1, timeout=10.0), "第二个申请者没能在 10s 内排上队"
    assert order == ["first-enter"], "P1 被抢断了：第二条在第一条没传完时就进了通道"
    release.set()
    t.join(5)
    t2.join(5)
    assert order == ["first-enter", "first-exit", "second-enter"]


# --------------------------------------------------------------- 2. 抢占语义


def test_p2_preempted_by_p1():
    """P2 预取持有中来了 P1 ⇒ P2 在分片间隙（`pace`）看到并被中断。"""
    s = _sched()
    waiting = _CountingEvent()
    s._released = waiting  # 同上：用来观测「P1 已经排上队」
    got: list[BaseException] = []
    holding = threading.Event()
    go = threading.Event()

    def prefetch() -> None:
        try:
            with s.slot(BULK_P2_PREFETCH, label="prefetch") as tok:
                holding.set()
                go.wait(5)
                s.pace(tok, BULK_P2_PREFETCH)  # 分片间隙
        except BaseException as e:
            got.append(e)

    t = threading.Thread(target=prefetch, daemon=True)
    t.start()
    assert holding.wait(5)

    def critical() -> None:
        with s.slot(BULK_P1_CRITICAL, label="critical"):
            # sleep-ok: 夹具模拟的工作量：P1 持有者在「传」一小段（不是同步手段）
            time.sleep(0.05)

    t2 = threading.Thread(target=critical, daemon=True)
    t2.start()
    # 事件驱动：P1 一旦**排上队**，抢占标记（`_preempt_at`）就已写好 —— `slot()` 是先写
    # 标记再进等待循环的。原来 `time.sleep(0.05)` 赌「P1 已经跑到了 `slot()`」：满载时
    # P1 还没起来就放行 `go`，`pace` 看不到标记 ⇒ P2 不被中断 ⇒ 用例红。
    assert waiting.wait_until(1, timeout=10.0), "P1 没能在 10s 内排上队（抢占标记未写）"
    go.set()
    t.join(5)
    t2.join(5)
    assert got and isinstance(got[0], BulkPreemptError), f"P2 没被打断，拿到了 {got}"
    assert s.stats()["preempted"] >= 1


def test_p2_not_preempted_by_control_yields_only():
    """控制面（P0）**只让路、不抢占**：预取不丢半截（2026-09-25 语义，取代旧的可抢占）。

    旧语义的现场后果（x20-dodge-l1/l3 双课程单 worker，2026-09-24）：取消环每 1.5s 打一个
    `/jobs/{id}/status`（`control_path()` 判为控制面）⇒ 每 1.5s 必然打断一次 P2 ⇒ 每个
    attempt 最多搬 ~0.5MB ⇒ 3.4MB 的 payload **数学上永远传不完**（预取零命中）。
    """
    budget, step = 0.12, 0.02
    s = _sched(yield_budget_sec=budget, yield_step_sec=step)
    got: list[BaseException] = []
    holding = threading.Event()
    entered = threading.Event()
    go = threading.Event()
    stop = threading.Event()

    def prefetch() -> None:
        try:
            with s.slot(BULK_P2_PREFETCH, label="prefetch") as tok:
                holding.set()
                go.wait(5)
                s.pace(tok, BULK_P2_PREFETCH)  # 分片间隙：控制面在途 ⇒ 只暂停
        except BaseException as e:
            got.append(e)

    t = threading.Thread(target=prefetch, daemon=True)
    t.start()
    assert holding.wait(5)

    def p0() -> None:
        with s.control(label="/jobs/x/status"):
            entered.set()
            stop.wait(5)

    tp = threading.Thread(target=p0, daemon=True)
    tp.start()
    # 事件驱动：`control()` 先计数再 yield ⇒「entered 已置位」⇔ 控制面确实在途。
    assert entered.wait(5), "控制面没能进入在途状态"
    go.set()
    t.join(10)
    stop.set()
    tp.join(5)
    assert got == [], f"控制面把 P2 挤走了（改后应只让路）：{got}"
    st = s.stats()
    assert st["preempted"] == 0, "控制面仍然在抢占 P2"
    assert st["yield_count"] >= 1, "控制面在途却没有让路（另一头失衡）"


def test_p1_waiting_blocks_p2():
    """P1 在等 ⇒ 新的 P2 不许新开工，且 P1 走后能接手（docstring 承诺过、此前只写不读的门禁）。

    现场依据：抢占释放后 P2 立刻回抢 ⇒ 关键下载排队 17.6s / 16.8s / 12.7s（2026-09-24）。
    """
    s = _sched()
    order: list[str] = []
    waiting = _CountingEvent()
    s._released = waiting  # 只换等待原语（仍是 Event）：用来观测「谁已经排上队」
    holder_in = threading.Event()
    holder_go = threading.Event()
    p1_go = threading.Event()
    p2_go = threading.Event()
    p1_inside = threading.Event()
    p1_hold = threading.Event()
    p2_in = threading.Event()

    def holder() -> None:
        with s.slot(BULK_P2_PREFETCH, label="holder"):
            order.append("holder-enter")
            holder_in.set()
            holder_go.wait(5)

    def p1() -> None:
        p1_go.wait(5)
        with s.slot(BULK_P1_CRITICAL, label="critical"):
            order.append("p1-enter")
            p1_inside.set()
            p1_hold.wait(5)
            order.append("p1-exit")

    def p2() -> None:
        p2_go.wait(5)
        with s.slot(BULK_P2_PREFETCH, label="prefetch"):
            order.append("p2-enter")
            p2_in.set()

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert holder_in.wait(5), "持有者没进通道"
    t1 = threading.Thread(target=p1, daemon=True)
    t1.start()
    p1_go.set()
    assert waiting.wait_until(1, timeout=10.0), "P1 没能在 10s 内排上队"
    assert s.stats()["p1_waiting"] == 1, "p1_waiting 没记上（门禁的输入是空的）"
    t2 = threading.Thread(target=p2, daemon=True)
    t2.start()
    p2_go.set()
    assert waiting.wait_until(2, timeout=10.0), "P2 没能在 10s 内排上队"
    # 槽位空出来：P1 必进（它不受门禁）；P2 被 p1_waiting 挡在门外
    holder_go.set()
    assert p1_inside.wait(10), "P1 没能在持有者走后拿到通道"
    assert not p2_in.is_set(), "P1 还在等/在传的时候 P2 抢到了通道（门禁没生效）"
    p1_hold.set()
    t1.join(10)
    t2.join(10)
    assert order == ["holder-enter", "p1-enter", "p1-exit", "p2-enter"], order
    assert s.stats()["p1_waiting"] == 0, "p1_waiting 没归零（泄漏 ⇒ P2 会永久空转）"


def test_control_waiting_blocks_p2():
    """控制面在途 ⇒ 新的 P2 不许新开工；控制面一走就能开工（门禁的另一半）。"""
    s = _sched()
    entered = threading.Event()
    waiting = _CountingEvent()
    s._released = waiting

    def p2() -> None:
        with s.slot(BULK_P2_PREFETCH, label="prefetch"):
            entered.set()

    with s.control(label="/jobs/x/status"):
        assert s.stats()["control_waiting"] == 1
        t = threading.Thread(target=p2, daemon=True)
        t.start()
        assert waiting.wait_until(1, timeout=10.0), "P2 没有在控制面在途时排队"
        assert s.inflight() == 0, "控制面在途时 P2 抢到了通道（门禁没生效）"
    t.join(10)
    assert entered.is_set(), "控制面走后 P2 仍没进（门禁没释放）"
    assert s.stats()["control_waiting"] == 0


def test_p1_is_not_gated_by_p1_waiting():
    """**向前守卫**：这道门禁只能作用于 P2 —— `p1_waiting` 非零时 P1 仍必能拿到槽位。

    它不钉某个旧代码的缺陷（旧实现根本没有门禁），钉的是「以后别把 P1 也圈进去」：
    P1 被 `p1_waiting` 挡住 = 两个 P1 互相堵死（现场表现为「结果回传与关键下载互等」）。
    """
    s = _sched()
    s._p1_waiting = 1  # 白盒：等价于「另一个 P1 正排着队」，不必真起线程赌时序
    got: list[int] = []

    def run() -> None:
        with s.slot(BULK_P1_CRITICAL, label="critical") as tok:
            got.append(tok)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(5)
    assert got, "P1 被 p1_waiting 门禁挡住了（这道门禁只能作用于 P2）"
    assert s._p1_waiting == 1, "白盒置入的计数被吃掉了（增/减不对称）"


def test_p1_not_preempted_by_control():
    """P1（在传的结果）遇控制面**不中断**：只暂停让路，绝不丢半截。"""
    s = _sched(yield_budget_sec=0.05, yield_step_sec=0.01)
    with s.slot(BULK_P1_CRITICAL, label="result") as tok:
        with s.control(label="/jobs/x/status"):
            s.pace(tok, BULK_P1_CRITICAL)  # 不得抛
        s.pace(tok, BULK_P1_CRITICAL)  # 控制面走了再调一次，也不得抛
    assert s.stats()["preempted"] == 0


# --------------------------------------------------------------- 3. 让路预算


def test_yield_stops_at_budget_even_if_control_stays():
    """控制面一直不走也不能停超预算：单次让路**记账与步数**都受预算约束。"""
    budget, step = 0.12, 0.02
    s = _sched(yield_budget_sec=budget, yield_step_sec=step)
    with s.slot(BULK_P1_CRITICAL, label="result") as tok:
        stop = threading.Event()
        entered = threading.Event()

        def p0() -> None:
            with s.control(label="/jobs/x/status"):
                entered.set()
                stop.wait(2)  # 控制面「一直在途」

        t = threading.Thread(target=p0, daemon=True)
        t.start()
        # 事件驱动：`control()` 先计数再 yield ⇒「entered 已置位」⇔ 控制面确实在途。
        # 原来 `time.sleep(0.05)` 赌线程已经跑起来：满载时会停到 `control_active()` 还是
        # False ⇒ `pause_if_needed` 返回 0 ⇒ `yield_count == 0` ⇒ 用例红。
        assert entered.wait(5), "控制面没能进入在途状态"
        spent = s.pause_if_needed(tok)
        stop.set()
        t.join(5)
    assert spent <= budget + step + 1e-6, f"让路超预算：{spent}"
    # 让路**步数**恰好是 ceil(budget/step)：这是「受预算约束」的确定性陈述（spent 是循环
    # 自己的记账，yield_count 是它真走了几格），与机器快慢无关。
    assert s.stats()["yield_count"] == 6, s.stats()
    # ❌ 曾经这里有一条 `elapsed <= budget + step + 0.25` 的**墙钟**断言（"让路墙钟超预算"）：
    # 它赌的是「一次 `sleep(0.02)` 真的只花 0.02s」，而在负载 13 的机器上实测该让路墙钟
    # 2.06s（每格睡成 ~0.34s，17×）⇒ 门禁在满机时假红（2026-09-29 全量第 1 轮实测）。
    # 判据侧有两个问题：① 它就是 §21 那族「拿绝对数字当机器够快」；② 它**分不清**
    # 「循环多睡了几格」（真回归，已被上面两条钉住）与「OS 把 sleep 跑晚了」（环境）。
    # 故删除：契约由 `spent`（循环记账）+ `yield_count`（步数）确定性钉住。


def test_no_yield_when_no_control():
    """没有控制面在途 ⇒ 让路点零成本（不能让每次分片都睡一格）。"""
    s = _sched()
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok:
        t0 = time.time()
        spent = s.pause_if_needed(tok)
    assert spent == 0.0
    # timing-ok: 契约上界（无控制面 ⇒ 让路点零成本，上界即契约）
    assert time.time() - t0 < 0.05


# ─────────── 3b. 条件让路 / 按传输累计上限（2026-10-02，plan/transfer-residual W1/W1b） ───────────


class _FakeClock:
    """可推的注入时钟（`BulkScheduler.clock` 的形状）：让 age 判据确定、不赌调度。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_oldest_control_age_tracks_the_oldest_inflight():
    """`oldest_control_age()`：没有在途 ⇒ None；多个 ⇒ 取**最老**那个的 age（S1 的输入）。"""
    clk = _FakeClock()
    s = _sched(clock=clk)
    assert s.oldest_control_age() is None
    with s.control(label="a"):
        clk.t = 0.4
        with s.control(label="b"):
            clk.t = 0.8
            assert s.oldest_control_age() == pytest.approx(0.8)
        clk.t = 1.1
        assert s.oldest_control_age() == pytest.approx(1.1)


def test_no_yield_while_control_is_young():
    """S1：控制请求在途但 age < T ⇒ 不让路、不记账（为「大概率自己会完成」的请求停 bulk 是纯税）。"""
    clk = _FakeClock()
    s = _sched(yield_after_sec=1.0, clock=clk)
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok, s.control(label="/jobs/x/status"):
        clk.t += 0.3
        assert s.pause_if_needed(tok) == 0.0
        assert s.stats()["yield_count"] == 0


def test_yield_once_control_has_waited_past_the_threshold():
    """S1：同一个控制请求 age ≥ T ⇒ 让路（阈值只改「谁值得等」，不改让路机制本身）。"""
    clk = _FakeClock()
    s = _sched(yield_after_sec=1.0, yield_budget_sec=0.03, yield_step_sec=0.01, clock=clk)
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok, s.control(label="/jobs/x/status"):
        clk.t += 1.2
        assert s.pause_if_needed(tok) == pytest.approx(0.03)
        assert s.stats()["yield_count"] == 3


def test_always_mode_keeps_the_old_semantics():
    """`always` 档（after=0.0）逐字回旧语义：控制面一在途就让；映射在 `worker._bulk_yield_params`。"""
    assert W._bulk_yield_params("always") == (0.0, None)
    clk = _FakeClock()
    s = _sched(yield_budget_sec=0.02, yield_step_sec=0.01, clock=clk)
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok, s.control(label="/jobs/x/status"):
        assert s.pause_if_needed(tok) == pytest.approx(0.02)


def test_never_mode_never_yields():
    """`never` 档（after=inf）：控制面在途一辈子也不让路——量「让路买到什么」的极端臂。"""
    after, total = W._bulk_yield_params("never")
    assert after == float("inf") and total is None
    clk = _FakeClock()
    s = _sched(
        yield_after_sec=after,
        yield_total_budget_sec=total,
        yield_budget_sec=0.02,
        yield_step_sec=0.01,
        clock=clk,
    )
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok, s.control(label="/jobs/x/status"):
        clk.t += 999.0
        assert s.pause_if_needed(tok) == 0.0
        assert s.stats()["yield_count"] == 0


def test_auto_mode_mapping_matches_the_plan():
    """`auto` = 条件让路 + 单条传输累计 5s（W1/W1b 的目标态）。"""
    assert W._bulk_yield_params("auto") == (1.0, 5.0)


def test_yield_total_is_capped_per_transfer():
    """S2/W1b：同一条传输内多次让路的**累计**封顶——控制面一直在途也拿不走更多。"""
    clk = _FakeClock()
    s = _sched(
        yield_after_sec=0.0,  # 老条件（在途即让）：把变量单独留给 total
        yield_total_budget_sec=0.03,
        yield_budget_sec=1.0,  # 单次预算故意放大：封顶只能来自 total
        yield_step_sec=0.01,
        clock=clk,
    )
    with s.slot(BULK_P1_CRITICAL, label="payload") as tok, s.control(label="/jobs/x/status"):
        total = 0.0
        for _ in range(20):
            total += s.pause_if_needed(tok)
        cur = s._yield_cur or {}
        cur_sec = float(cur.get("sec", 0.0))
    assert total == pytest.approx(0.03), f"累计让路没有封顶：{total}"
    assert cur_sec == pytest.approx(0.03), f"本传输的账不封顶：{cur_sec}"
    assert s.stats()["yield_sec"] == pytest.approx(0.03)


def test_queue_wait_is_attached_to_its_own_token():
    """S3d：排队秒数按 token 挂账、取后即删（不重复计数）；会话级对账面仍在。"""
    s = _sched(wait_step_sec=0.005)
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with s.slot(BULK_P1_CRITICAL, label="holder"):
            holding.set()
            release.wait(5)

    def _free_soon() -> None:
        # sleep-ok: 夹具模拟的工作量：占住通道一段时间（不是同步手段）
        time.sleep(0.05)
        release.set()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert holding.wait(5), "占位线程没进通道"
    threading.Thread(target=_free_soon, daemon=True).start()
    with s.slot(BULK_P1_CRITICAL, label="queued") as tok:
        wait = s.take_wait(tok)
    t.join(5)
    assert wait > 0.0, "排队秒数没有挂到 token 上"
    assert s.take_wait(tok) == 0.0, "take_wait 取后即删（不能重复计数）"
    assert s.stats()["queue_wait_sec"] > 0.0, "会话级对账面被误删"


# 已退役（2026-10-06，用户指令「删除云机 worker 刷屏 log」）：原
# `test_yield_log_is_one_line_per_transfer` 钉的是「让路合计」行——那行已从 `slot()` 里删除
# （它虽已是「一次传输一行」，仍是每 job 必然多出来的一行）。判据没丢：让路计数/秒数仍由上面
# 的 S2 封顶用例（`_yield_cur` 是累计上限的输入）与 `stats()["yield_sec"]` 钉住；每 job 的
# 调度读数仍在 wire 行的 `wait=…/yield=…` 里。


# --------------------------------------------------------------- 4. 路径分流


def test_control_path_classification():
    """分流靠**路径**（一处实现）：控制面不排队，两处大 body（payload/result）才进队列。"""
    for p in (
        "/jobs/peek?n=3",
        "/jobs/priority",
        "/jobs/abc/claim",
        "/jobs/abc/start",
        "/jobs/abc/ready",
        "/jobs/abc/abandon",
        "/jobs/abc/status",
        "/jobs/abc/release",
        "/jobs/abc/fail",
        "/jobs/abc/heartbeat",
        "/admin/queue",
    ):
        assert control_path(p), f"{p} 应当被当作控制面"
    for p in ("/jobs/abc/payload", "/jobs/abc/result", "/jobs/abc/code", "/jobs/abc/ts_code"):
        assert not control_path(p), f"{p} 是大 body，必须进 bulk 队列"


# --------------------------------------------------------------- 5. 观测面


def test_wire_line_reports_scheduler_accounting():
    """每 job 的传输账要能读出排队/让路/控制面往返（DoD：观测可从日志读出）。"""
    W._wire_bucket("j" * 16)
    W._wire_add("j" * 16, "payload", 1024 * 1024, 1.0)
    lines: list[str] = []
    W._wire_flush("j" * 16, lines.append)
    assert len(lines) == 1
    assert "payload=" in lines[0]
    assert "wait=" in lines[0] and "yield=" in lines[0] and "p0_p95=" in lines[0]
    assert "p0_p50=" in lines[0], "W0：条件让路的定 T 依据（p50）没上日志"


def test_stats_has_dod_fields():
    """DoD 点名的三个读数必须在 `stats()` 里（P0.5 基线也靠它）。"""
    st = _sched().stats()
    for k in (
        "inflight_bulk",
        "queue_wait_sec",
        "yield_count",
        "p0_rt_ms_p95",
        "p1_waiting",
        "control_waiting",
    ):
        assert k in st, f"stats() 缺 {k}"
