"""`PushDispatcher._await_result` 五段的行为守卫（engineering §347，2026-11-25）。

入口只剩「续租 → 取结果 → 按状态分派」；判据拆在 `_await_stale` / `_await_queue_expired` /
`_await_landed` / `_await_running` / `_await_gone`。这里钉四件事：

1. 202 queued 不打点、不烧无结果预算，但排队上限到点回落；
2. 202 running / 200 首次看到才打 `computing_at` 并把 deadline 重新起算；
3. 200 的入账四档：200 落盘、409 丢弃、400/403 重排、其它只记录；
4. 410 落 fail.json；探活离场与停机各走自己的回落。

不读源码；状态用 `__new__` 手装，断言只看公开调用序列。
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

import remote.push_dispatch as pd

OK = json.dumps({"ok": True}).encode()
QUEUED = json.dumps({"status": "queued"}).encode()
RUNNING = json.dumps({"status": "running"}).encode()


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 0.25
        return self.t


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[tuple[int, bytes] | None],
    *,
    online: bool = True,
    clock: _Clock | None = None,
) -> tuple[Any, dict[str, list[Any]]]:
    calls: dict[str, list[Any]] = {
        "requeue": [],
        "mark": [],
        "wait": [],
        "warn": [],
        "cancel": [],
        "heartbeat": [],
        "store": [],
        "log": [],
    }
    d = cast(Any, pd.PushDispatcher.__new__(pd.PushDispatcher))
    d._log = calls["log"].append
    d.timeout_sec = 2.0
    d._stop = threading.Event()
    d._now = clock or _Clock()
    d.workers = SimpleNamespace(is_online=lambda _wid: online)
    d.hub = SimpleNamespace(
        heartbeat=lambda jid, lease: calls["heartbeat"].append((jid, lease)),
        store_job_failure=lambda jid, rec: calls["store"].append((jid, rec)),
        _now=lambda: 7.0,
    )
    d._requeue = lambda *a, **k: calls["requeue"].append((a, k))
    d._wait_next_poll = lambda jid: calls["wait"].append(jid)
    d._mark_started = lambda jid, wid: calls["mark"].append((jid, wid))
    d._warn_slow_result = lambda *a, **k: calls["warn"].append((a, k))
    d._cancel_others = lambda *a, **k: calls["cancel"].append((a, k))
    seq = list(responses)
    seen = [0]

    def opener(_req: object, timeout: float | None = None) -> _Resp:
        seen[0] += 1
        if seen[0] >= 12:
            d._stop.set()
        item = seq.pop(0) if seq else None
        if item is None:
            raise OSError("no answer")
        return _Resp(item[0], item[1])

    monkeypatch.setattr(pd.net_http, "urlopen", opener)
    return d, calls


def _leg() -> Any:
    return pd.JobLeg(
        course="course-a",
        jid="j1",
        wid="w1",
        url="http://worker.invalid",
        key="k",
        lease="lease-1",
        t0=0.0,
    )


def test_queued_202_does_not_mark_started(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(202, QUEUED), (503, b"")])
    d._await_result(_leg(), started=False)
    assert calls["mark"] == [], "排队不是开算，不能打 computing_at"
    assert calls["wait"] and len(calls["log"]) >= 1
    assert calls["requeue"][0][0][3].startswith("排队 2s")


def test_running_202_marks_started_once(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(202, RUNNING), (503, b"")])
    d._await_result(_leg(), started=False)
    assert calls["mark"] == [("j1", "w1")]


def test_queued_202_respects_the_queue_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    d, calls = _dispatcher(monkeypatch, [(202, QUEUED)] * 12, clock=clock)
    d._await_result(_leg(), started=False)
    assert len(calls["requeue"]) == 1
    reason = calls["requeue"][0][0][3]
    assert reason.startswith("排队 2s")


def test_landed_200_accepts_and_cancels_others(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(200, OK)])
    monkeypatch.setattr(pd, "accept_result", lambda *_a, **_k: (200, "ok"))
    d._await_result(_leg(), started=False)
    assert calls["mark"] == [("j1", "w1")]
    assert len(calls["cancel"]) == 1 and calls["requeue"] == []


def test_accept_409_is_dropped_not_requeued(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(200, OK)])
    monkeypatch.setattr(pd, "accept_result", lambda *_a, **_k: (409, "lost"))
    d._await_result(_leg())
    assert calls["requeue"] == [] and calls["cancel"] == []


def test_accept_400_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(200, OK)])
    monkeypatch.setattr(pd, "accept_result", lambda *_a, **_k: (400, "bad"))
    d._await_result(_leg())
    assert len(calls["requeue"]) == 1
    assert calls["requeue"][0][0][3].startswith("结果入账被拒 HTTP 400")


def test_410_stores_a_failure_record(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [(410, b'{"error": "no bun"}')])
    monkeypatch.setattr(pd, "push_failure_record", lambda fb, wid, now: {"fb": fb, "wid": wid})
    d._await_result(_leg())
    assert calls["store"] == [("j1", {"fb": {"error": "no bun"}, "wid": "w1"})]


def test_offline_worker_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [], online=False)
    d._await_result(_leg())
    assert len(calls["requeue"]) == 1
    assert calls["requeue"][0][0][3].startswith("worker 连续")


def test_stale_deadline_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    d, calls = _dispatcher(monkeypatch, [])
    d._await_result(_leg(), started=True)
    assert len(calls["requeue"]) == 1
    assert calls["requeue"][0][0][3].startswith("推送后 2s 无结果")
