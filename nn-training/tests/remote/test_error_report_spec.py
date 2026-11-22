"""`ErrorReportSpec` 的形状守卫（`python.refactor.md` §P0-3，批 F3，2026-11-22）。

批 F3：`remote/error_report.report_error`(2 位置 + 9 关键字 → 1 个 `ErrorReportSpec`)。
调用面 8 处（`job_round` 3 · `lifecycle` 1 · `run_loop` 1 · 用例 3）。

坏点全是「互换后照样发出去、只是内容错位」：`course` / `job_id` / `worker_id` 三个身份串 —
写错一位就把这条错误记在另一门课 / 另一份活 / 另一台机器头上，而观测腿的读者正是靠它对账；
`reason` / `detail` 两个自由文本 — `reason` 是控制台那一行标题、`detail` 是 traceback 正文，
互换后 4 KB 堆栈进标题（再被 2000 字封顶截断）、一行摘要进详情；`it`（`int | None`）与
`severity`（两档字符串）相邻 — 互换后轮次读成字符串、告警级别读成数字归 `warn`，一条 `err`
静默降级成 `warn`。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

from remote import error_report as er

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: 旧 `report_error` 的 11 个形参（2 个位置在前，`log` 在最后）。
FIELDS: list[tuple[str, object]] = [
    ("base_url", MISSING),
    ("token", MISSING),
    ("reason", MISSING),
    ("detail", ""),
    ("component", "ppo-worker"),
    ("course", ""),
    ("job_id", ""),
    ("it", None),
    ("severity", "warn"),
    ("worker_id", ""),
    ("log", None),
]


def test_field_table_matches_the_old_signature() -> None:
    assert _table(er.ErrorReportSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(er.ErrorReportSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = er.ErrorReportSpec(base_url="http://hub", token="tok", reason="boom")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[0].name, object())


def test_entry_takes_exactly_one_spec() -> None:
    entry = er.report_error
    assert list(inspect.signature(entry).parameters) == ["spec"]


def test_old_form_is_rejected() -> None:
    call = cast(Callable[..., object], er.report_error)
    with pytest.raises(TypeError):
        call("http://hub", "tok", reason="boom")


def test_identity_bits_stay_distinct() -> None:
    """课程 / 活 / worker 是三个独立身份位：一个都不能冒充另一个（错位只会记错账）。"""
    spec = er.ErrorReportSpec(
        base_url="http://hub",
        token="tok",
        reason="boom",
        course="c1-gae",
        job_id="j-7",
        worker_id="kaggle-c:1",
    )
    assert (spec.course, spec.job_id, spec.worker_id) == ("c1-gae", "j-7", "kaggle-c:1")
    payload = er.error_payload(worker=spec.worker_id, reason=spec.reason, course=spec.course)
    assert (payload["worker"], payload["course"], payload["reason"]) == ("kaggle-c:1", "c1-gae", "boom")


def test_round_and_severity_keep_their_types() -> None:
    """`it` 缺席 = 不可知（不编 0），`severity` 只认 `err` 那一档 —— 两者互换会静默降级。"""
    spec = er.ErrorReportSpec(base_url="http://hub", token="tok", reason="boom")
    assert spec.it is None and spec.severity == "warn"
    # 键集判据（不是「字符串在不在」的语言断言）：`it` 缺席 ⇒ 控制台按不可知渲染。
    keys = set(er.error_payload(worker="w", reason="r", it=spec.it))
    assert keys == {"worker", "component", "course", "job_id", "severity", "reason", "detail", "ts"}
    err = er.ErrorReportSpec(base_url="http://hub", token="tok", reason="boom", it=3, severity="err")
    assert err.it == 3
    assert er.error_payload(worker="w", reason="r", it=err.it, severity=err.severity)["severity"] == "err"


def test_without_a_hub_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """空地址 / 空 token ⇒ 一声不响（观测面没有不存在的义务），线程都不许起。"""
    spawned: list[object] = []

    class _NoThread:
        def __init__(self, *_a: object, **_kw: object) -> None:
            spawned.append(self)

        def thread(self, *_a: object, **_kw: object) -> None:
            spawned.append("thread")

    monkeypatch.setattr(er.runtime, "Runtime", _NoThread)
    er.report_error(er.ErrorReportSpec(base_url="", token="", reason="boom"))
    assert spawned == []
