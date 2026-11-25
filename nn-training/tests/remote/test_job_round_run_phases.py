"""`_Round.run` 的四档失败 + 收尾守卫（engineering §348，2026-11-25）。

入口只剩 try/except 分派；每档的判据（取消零回传 / 机器级 release / 瞬时 release /
热替换停循环 / 确定性拒绝报 fail / SystemExit 例外）各住一个方法。这里断言的是
**调用序列与公开结局**，不读私有状态。
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import remote.job_round as jr


def _round(
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException | None = None,
    *,
    uploaded: bool = False,
    threads: bool = False,
) -> tuple[Any, dict[str, list[Any]]]:
    calls: dict[str, list[Any]] = {
        "abandon": [],
        "release": [],
        "error": [],
        "failure": [],
        "flush": [],
        "release_handoff": [],
        "stop_fill": [],
        "join": [],
    }
    r = cast(Any, jr._Round.__new__(jr._Round))
    r.base_url = "http://hub.invalid"
    r.token = "tok"
    r.jid = "j1"
    r.lease_token = "lease-1"
    r.worker_id = "w1"
    r.restart_argv = None
    r.job = {"course": "course-a", "job_id": "j1"}
    r.job_it = 3
    r.t_ppo0 = 0.0
    r.part_dir = Path(".")
    r.cache_root = None
    r.uploaded = uploaded
    r.stop = False
    r.job_ok = exc is None
    r.watch_stop = threading.Event()
    r.hb_stop = threading.Event()
    r.pf_stop = threading.Event()
    r.pf_store = (
        SimpleNamespace(release_handoff=lambda: calls["release_handoff"].append(1))
        if threads
        else None
    )
    r.pf_proc = SimpleNamespace(stop_fill=lambda: calls["stop_fill"].append(1)) if threads else None
    r.pf_thread = SimpleNamespace(join=lambda timeout=None: calls["join"].append(timeout)) if threads else None
    r.hb_thread = SimpleNamespace(join=lambda timeout=None: calls["join"].append(timeout)) if threads else None
    r.log = lambda _m: None

    def execute() -> None:
        if exc is not None:
            raise exc

    r._execute = execute
    monkeypatch.setattr(jr, "abandon_job", lambda *a, **k: calls["abandon"].append((a, k)))
    monkeypatch.setattr(jr, "release_job", lambda *a, **k: calls["release"].append((a, k)))
    monkeypatch.setattr(jr, "report_error", lambda spec: calls["error"].append(spec))
    monkeypatch.setattr(jr, "report_job_failure", lambda spec: calls["failure"].append(spec))
    return r, calls


def test_cancel_abandons_without_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, jr.JobCancelledError("landed"))
    out = jr._Round.run(r)
    assert len(calls["abandon"]) == 1 and calls["release"] == []
    assert calls["failure"] == [] and out.uploaded is False and out.stop is False


def test_unreapable_releases_and_reports_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, jr.UnreapableChildError("stuck"))
    jr._Round.run(r)
    assert len(calls["release"]) == 1 and len(calls["error"]) == 1
    assert calls["failure"] == []


def test_retryable_releases_and_reports_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, jr.RetryableError("net"))
    jr._Round.run(r)
    assert len(calls["release"]) == 1 and len(calls["error"]) == 1
    assert calls["failure"] == []


def test_code_changed_without_supervisor_stops_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, jr.CodeChangedError("old", "new"))
    out = jr._Round.run(r)
    assert len(calls["release"]) == 1
    assert out.stop is True


def test_protocol_error_reports_the_job_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, jr.ProtocolError("bad job"))
    jr._Round.run(r)
    assert len(calls["failure"]) == 1 and calls["release"] == []


def test_plain_system_exit_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, SystemExit("boom"))
    jr._Round.run(r)
    assert len(calls["failure"]) == 1


def test_hot_reload_system_exit_is_re_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    r, _calls = _round(monkeypatch, SystemExit(jr.HOT_RELOAD_EXIT))
    with pytest.raises(SystemExit):
        jr._Round.run(r)


def test_success_returns_an_ok_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch)
    out = jr._Round.run(r)
    assert out.ok is True and out.stop is False
    assert calls["abandon"] == [] and calls["release"] == [] and calls["failure"] == []


def test_wrap_up_flushes_joins_and_sets_stop_events(monkeypatch: pytest.MonkeyPatch) -> None:
    r, calls = _round(monkeypatch, uploaded=False, threads=True)
    jr._Round.run(r)
    assert len(calls["release_handoff"]) == 1 and len(calls["stop_fill"]) == 1
    assert len(calls["join"]) == 2
    assert r.watch_stop.is_set() and r.hb_stop.is_set() and r.pf_stop.is_set()
