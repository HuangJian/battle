"""`_plan_after` 的决策表（无网络、无时钟、无 sleep）。

重试控制流收进 `_RetryPlan` 之后，每种尝试结果都只产出「动作 + 新预算 + 续传前缀 + 日志」。
本文件逐行钉住那张表；`test_wire_reroll.py` 继续负责真行为面（差分/挤走/续传跑通）。
"""

from __future__ import annotations

import types
from typing import Any

import pytest

import remote.http as http
import remote.wire as wire
from common.ports import PortPair
from remote.bulk_sched import BULK_P1_CRITICAL, BULK_P2_PREFETCH, BulkPreemptError
from remote.http import WireGet, _Attempt, _plan_after, _RetryAction, _RetryState
from remote.wire import WireSlowError

#: 记账函数 → 记录器上的**公开**字段名（断言不读私有键，G5 口径）。
LEDGER = {
    "_wire_note_preempt": "preempt",
    "_wire_note_failed": "failed",
    "_wire_note_reroll": "reroll",
    "_wire_note_resend": "resend",
    "_note_rate": "rate",
}


@pytest.fixture
def seen(monkeypatch):
    """把所有分支记账换成记录器；决策表只看「记了哪几笔」。"""
    calls = types.SimpleNamespace(
        preempt=[], failed=[], reroll=[], resend=[], add=[], rate=[]
    )
    for name, attr in LEDGER.items():
        monkeypatch.setattr(
            http, name, (lambda a: lambda *args: getattr(calls, a).append(args))(attr)
        )
    # `_wire_add` 不再是裸调用（P0-8 第三代）：它被 `WIRE.add` 承载 ⇒ 打公开端口，
    # 发送面保留真实现（本文件不发请求，只走 `_plan_*` 的记账分支）。
    monkeypatch.setattr(
        http,
        "WIRE",
        PortPair(request=http.WIRE.request, add=lambda *args: calls.add.append(args)),
    )
    # `_min_rate` 读的是 `remote.wire.rate_reference()` ⇒ 打公开名的替身，不动私有名。
    monkeypatch.setattr(
        wire, "rate_reference", lambda: types.SimpleNamespace(floor=lambda: 10_000.0)
    )
    # 假钟：决策里的记账秒数 = 105 - t_xfer(101) = 4.0，不读墙钟。
    monkeypatch.setattr(
        http, "time", types.SimpleNamespace(time=lambda: 105.0, sleep=lambda _s: None)
    )
    return calls


def spec(**kw: Any) -> WireGet:
    base: dict[str, Any] = dict(
        base_url="http://hub", token="t", path="/jobs/j1/payload", timeout=30.0
    )
    base.update(kw)
    return WireGet(**base)


def attempt(**kw: Any) -> _Attempt:
    base: dict[str, Any] = dict(status=None, body=b"", got=0, t_req=100.0, t_xfer=101.0)
    base.update(kw)
    return _Attempt(**base)


def state(**kw: Any) -> _RetryState:
    base: dict[str, Any] = dict(attempt=1)
    base.update(kw)
    return _RetryState(**base)


def plan(sp, st, att, parts=b""):
    return _plan_after(sp, st, att, bytearray(parts))


def test_preempt_p2_reschedules_without_burning_attempt(seen):
    e = BulkPreemptError("挤走", bytes_read=64)
    out = plan(spec(bulk_prio=BULK_P2_PREFETCH), state(), attempt(exc=e))
    assert out.action is _RetryAction.RESCHEDULE
    assert (out.state.attempt, out.state.preempts) == (1, 1)
    assert out.error is None
    assert seen.preempt == [("", 64)]
    assert seen.failed == [("", 64, 4.0)]


def test_preempt_p2_budget_exhausted_raises_the_original(seen):
    e = BulkPreemptError("挤走", bytes_read=64)
    out = plan(spec(bulk_prio=BULK_P2_PREFETCH), state(preempts=6), attempt(exc=e))
    assert out.action is _RetryAction.RAISE
    assert out.error is e
    assert out.state.preempts == 7
    assert out.log_lines[0].startswith("bulk ")


def test_preempt_p1_burns_attempt_and_stops_at_last(seen):
    e = BulkPreemptError("挤走", bytes_read=8)
    sp = spec(attempts=3, bulk_prio=BULK_P1_CRITICAL)
    mid = plan(sp, state(attempt=1), attempt(exc=e))
    assert (mid.action, mid.state.attempt, mid.state.preempts) == (
        _RetryAction.RESCHEDULE,
        2,
        1,
    )
    last = plan(sp, state(attempt=3, preempts=2), attempt(exc=e))
    assert last.action is _RetryAction.RAISE
    assert last.error is e
    assert last.log_lines[0].endswith("—— 重试次数用完，放弃这份提前量")


def test_reroll_appends_partial_and_counts(seen):
    e = WireSlowError(1000, 5000.0, 99.0, partial=b"abc")
    out = plan(
        spec(reroll=True), state(attempt=2, rerolls=1, reroll_ok=True), attempt(exc=e), b"xy"
    )
    assert out.action is _RetryAction.REROLL
    assert out.parts == b"xyabc"
    assert (out.state.attempt, out.state.rerolls) == (3, 2)
    assert out.state.prev_rate == 5000.0
    assert seen.reroll == [("", 0)]
    assert seen.failed == [("", 3, 4.0)]


def test_reroll_without_gain_disables_further_rerolls(seen):
    e = WireSlowError(1000, 5000.0, 99.0, partial=b"a")
    out = plan(
        spec(reroll=True),
        state(attempt=1, rerolls=1, reroll_ok=True, prev_rate=10_000.0),
        attempt(exc=e),
    )
    assert out.action is _RetryAction.REROLL
    assert out.state.reroll_ok is False
    assert len(out.log_lines) == 2
    assert out.log_lines[1].startswith("wire: 重抽无收益")


def test_206_appends_tail_and_delivers_without_log(seen):
    out = plan(spec(), state(), attempt(status=206, body=b"def"), b"abc")
    assert out.action is _RetryAction.DELIVER
    assert out.payload == b"abcdef"
    assert out.log_lines == ()
    assert seen.add == [("", "", 3, 4.0)]


def test_200_ignoring_range_resets_prefix_and_still_records_success(seen):
    out = plan(spec(), state(), attempt(status=200, body=b"full"), b"abc")
    assert out.action is _RetryAction.DELIVER
    assert out.payload == b"full"
    assert out.log_lines[0].endswith("——已收 3 bytes 作废，本次从头传")
    assert seen.resend == [("", 3)]
    assert seen.add == [("", "", 4, 4.0)]


def test_4xx_raises_without_failed_ledger(seen):
    out = plan(spec(), state(), attempt(status=404, body=b"nope"))
    assert out.action is _RetryAction.RAISE
    assert type(out.error).__name__ == "ProtocolError"
    assert str(out.error).endswith("HTTP 404")
    assert seen.failed == []


def test_transient_failure_backs_off_then_raises(seen):
    sp = spec(attempts=2)
    first = plan(sp, state(attempt=1), attempt(status=500, body=b"boom"))
    assert first.action is _RetryAction.BACKOFF
    assert first.sleep_sec == 2
    assert (first.state.attempt, first.state.last) == (2, "HTTP 500")
    assert first.log_lines[0].endswith("2s 后第 2/2 次重试")

    last = plan(sp, state(attempt=2), attempt(status=500, body=b"boom"))
    assert last.action is _RetryAction.RAISE
    assert type(last.error).__name__ == "RetryableError"
    assert str(last.error).endswith("重试 2 次仍失败: HTTP 500")


def test_network_exception_records_got_bytes(seen):
    class GotError(Exception):
        got = 123

    err = GotError("reset")
    out = plan(
        spec(attempts=2), state(attempt=1), attempt(got=123, body=repr(err).encode(), exc=err)
    )
    assert out.action is _RetryAction.BACKOFF
    assert seen.failed == [("", 123, 4.0)]
    assert out.state.last == repr(repr(err))


def test_planner_never_logs_or_sleeps(seen, monkeypatch):
    def forbid(_sec):
        raise AssertionError("_plan_after 不许睡眠")

    monkeypatch.setattr(http.time, "sleep", forbid)
    out = plan(spec(), state(), attempt(status=200, body=b"ok"))
    assert out.action is _RetryAction.DELIVER
