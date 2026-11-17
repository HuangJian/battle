"""`common.runtime.Runtime` 的行为契约（`python.refactor.md` §P1-6 / B6 第一刀）。

守的不是「有没有一个类」，而是三件会被下一个人静默改掉的事：

1. **线程名可认**：`<scope>-<pool>` 前缀是线程转储里唯一的身份（G15 的池侧那一半）。
2. **池归本域所有**：同域同名 = 同一个池；`close()` 之后不许再开新池。
3. **取消点真的在池边生效**：置位后不再投新活、排队的活当场取消、已开跑的等它收尾
   （Python 线程杀不掉 —— 假装能杀才是最大的坑）；正常 `close()` 不取消排队活，
   否则一次提前 `return` 就静默丢掉已投出去的局。

另有一条口径钉死：**显式并发数说话算数**，上限只约束隐式请求（`workers=None`），
`describe()["over"]` 只报数不节流（理由见模块 docstring）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from common import runtime


def _wait_until(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    """有界轮询（命中即退）：只用来等**事件**，从不断言时间。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.001)  # sleep-ok: 轮询步长 —— 等的是事件（谓词），命中即退
    raise AssertionError(f"等待超时（{timeout}s）：条件始终没成立")


# ── 线程名 ───────────────────────────────────────────────────────────────────


def test_pool_threads_are_named_after_scope_and_pool() -> None:
    seen: list[str] = []
    with runtime.Runtime(scope="unit") as rt:
        rt.map(
            lambda _i: seen.append(threading.current_thread().name),
            range(3),
            name="sweep",
            workers=3,
        )
    assert seen, "并行扇出一个线程都没跑到（扫描入口可能坏了）"
    assert all(n.startswith("unit-sweep") for n in seen), seen


def test_name_format_is_the_documented_two_part_form() -> None:
    assert runtime.NAME_FORMAT.format(scope="cloud-eval", pool="games") == "cloud-eval-games"


# ── 池的所有权 ───────────────────────────────────────────────────────────────


def test_same_name_is_the_same_pool() -> None:
    with runtime.Runtime(scope="unit") as rt:
        assert rt.pool("x", workers=1) is rt.pool("x")
        assert rt.pool("x", workers=9) is rt.pool("x"), "同名池不许被第二次的 workers 换掉"


def test_close_refuses_new_pools_and_is_idempotent() -> None:
    rt = runtime.Runtime(scope="unit")
    rt.close()
    rt.close()  # 幂等
    with pytest.raises(RuntimeError, match="已关闭"):
        rt.pool("late")


def test_close_waits_for_started_work() -> None:
    done = threading.Event()
    rt = runtime.Runtime(scope="unit")
    rt.submit(done.set)
    rt.close()
    assert done.is_set(), "close(wait=True) 必须在返回前等在飞的活收尾"


def test_no_pool_threads_survive_close() -> None:
    rt = runtime.Runtime(scope="survive")
    rt.map(lambda _i: None, range(4), name="p", workers=2)
    rt.close()
    assert [t.name for t in threading.enumerate() if t.name.startswith("survive-p")] == []


def test_map_preserves_order() -> None:
    with runtime.Runtime(scope="unit") as rt:
        assert rt.map(lambda i: i * i, [3, 1, 2], workers=3) == [9, 1, 4]
        assert rt.map(lambda i: i, []) == []


# ── 取消点 ───────────────────────────────────────────────────────────────────


def test_mutating_after_stop_is_refused_before_any_work_runs() -> None:
    ran: list[int] = []
    rt = runtime.Runtime(scope="unit")
    rt.request_stop()
    with pytest.raises(runtime.RuntimeCancelledError):
        rt.map(ran.append, [1, 2])
    with pytest.raises(runtime.RuntimeCancelledError):
        rt.submit(ran.append, 3)
    assert ran == []
    rt.close()


def test_stop_then_close_cancels_queued_work_and_lets_the_started_one_finish() -> None:
    order: list[int] = []
    gate = threading.Event()
    rt = runtime.Runtime(scope="unit")
    ex = rt.pool(workers=1)

    def job(i: int) -> None:
        order.append(i)
        gate.wait(10)

    futs = [ex.submit(job, i) for i in range(4)]
    _wait_until(lambda: order == [0])  # 第 0 个真的开跑了
    rt.request_stop()
    closers = threading.Thread(target=rt.close, name="test-closer")
    closers.start()
    # `shutdown(cancel_futures=True)` 会**同步**取消排队项，然后才开始等 —— 所以这条观察
    # 一定早于 close 返回，放开 gate 不会漏给排队项执行的机会。
    _wait_until(lambda: all(f.cancelled() for f in futs[1:]))
    gate.set()
    closers.join(10)
    assert not closers.is_alive(), "close() 没能在 gate 放开后收尾"
    assert order == [0]


def test_a_plain_close_leaves_queued_work_alone() -> None:
    """正常退出 ≠ 取消：提前 `return` 不该丢掉已经投出去的局（与 `with ThreadPoolExecutor` 一致）。"""
    order: list[int] = []
    with runtime.Runtime(scope="unit") as rt:
        ex = rt.pool(workers=1)
        for i in range(4):
            ex.submit(order.append, i)
    assert order == [0, 1, 2, 3]


def test_an_external_event_is_shared_not_copied() -> None:
    ev = threading.Event()
    rt = runtime.Runtime(scope="unit", stop=ev)
    assert rt.stop_event is ev
    ev.set()  # 外部（`halt_event` 的置位点）说了算
    assert rt.stopped is True
    with pytest.raises(runtime.RuntimeCancelledError):
        rt.check()
    rt.close()


def test_sleep_is_cancellable() -> None:
    # 这里的时长全是哑值：判据是被测对象（`Runtime.sleep`）的返回值，不是同步。
    rt = runtime.Runtime(scope="unit")
    assert rt.sleep(0) is True  # sleep-ok: 夹具模拟 —— 哑值，验返回值
    assert rt.sleep(0.001) is True  # sleep-ok: 夹具模拟 —— 哑值，验返回值
    rt.request_stop()
    assert rt.sleep(60.0) is False, "置位后必须立刻返回"  # sleep-ok: 夹具模拟 —— 哑值，验返回值


# ── 上限与读数 ───────────────────────────────────────────────────────────────


def test_implicit_requests_take_what_is_left_under_the_ceiling() -> None:
    rt = runtime.Runtime(scope="unit", ceiling=3)
    rt.pool("a")
    assert rt.describe()["pools"] == {"a": 3}
    rt.pool("b")
    assert rt.describe()["pools"] == {"a": 3, "b": 1}, "没预算也要至少留 1 个 worker"
    rt.close()


def test_explicit_workers_win_and_over_budget_is_only_reported() -> None:
    """这一条钉的是「不静默节流」：`--workers` 是操作员的决定，我们只负责把数报出来。"""
    rt = runtime.Runtime(scope="unit", ceiling=2)
    rt.pool("wide", workers=8)
    d = rt.describe()
    assert d["pools"] == {"wide": 8}
    assert d["workers"] == 8
    assert d["over"] is True
    rt.close()


def test_ceiling_defaults_to_the_core_count_floor() -> None:
    rt = runtime.Runtime(scope="unit")
    assert rt.ceiling == runtime.thread_ceiling()
    assert rt.ceiling >= runtime.MIN_CEILING
    rt.close()


def test_describe_is_json_safe_and_reports_lifecycle() -> None:
    rt = runtime.Runtime(scope="unit", ceiling=4)
    d = rt.describe()
    assert set(d) == {"scope", "ceiling", "workers", "over", "pools", "stopped", "closed", "clock"}
    assert d["scope"] == "unit"
    assert d["stopped"] is False and d["closed"] is False
    # 时钟读数：缺省就是真钟（注入后这一行会变成测试自己的可调用名）
    assert d["clock"] == "time.time/time.monotonic", d["clock"]
    rt.close()
    assert rt.describe()["closed"] is True


def test_time_reads_are_injectable() -> None:
    """§P1-6 clock 半边：两个读数（墙钟/单调钟）缺省是真钟，注入后整段计时可复现。

    为什么要两个：墙钟只用于时间戳（会被 NTP 拉回），单调钟只用于时长/超时（绝不回跳）。
    收成一个 `now()` 就是邀请「拿单调值比墙钟时间戳」那类只在跨机器时差下才显形的错。
    """
    assert runtime.Runtime(scope="t").clock is time.time
    assert runtime.Runtime(scope="t").monotonic is time.monotonic
    ticks = iter([100.0, 100.25, 101.5])
    rt = runtime.Runtime(scope="t", clock=lambda: 4_000_000_000.0, monotonic=lambda: next(ticks))
    assert rt.clock() == 4_000_000_000.0
    assert (rt.monotonic(), rt.monotonic(), rt.monotonic()) == (100.0, 100.25, 101.5)
    declared = str(rt.describe()["clock"])
    assert declared.count("<lambda>") == 2, declared  # 两个读数各一个，一眼看得出被换过
    # 真等待与时钟注入正交：`sleep` 仍是 `stop.wait`（注入只改「读到几点」）。
    rt.request_stop()
    assert rt.sleep(5.0) is False  # sleep-ok: 夹具模拟（stop 已置位 ⇒ 立即返回）


def test_scope_is_mandatory() -> None:
    with pytest.raises(ValueError, match="scope"):
        runtime.Runtime(scope="")
