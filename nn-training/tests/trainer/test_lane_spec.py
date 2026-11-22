"""`LaneSpec` 的形状守卫（`python.refactor.md` §P0-3，批 D2，2026-11-22）。

批 D2 第一刀：`trainer/queue_local.LocalLane.__init__`(12 参含 self → 1 个 `LaneSpec`)。

坏点：`local_slots`（初始孵几条）与 `cap_full`（远端失联后补到几条）是两个相邻 int —— 互换后
要么一开始就满额、要么永远补不上去，而日志只说「本机槽 = N」；`local_cap`（本机槽余量
`list[int]`）与 `last_remote_ok`（最近结算时刻 `list[float]`）是两份共享账，互换后
`slot_allowed` 的判据恒真/恒假；`all_settled` 与 `halt_event` 是两个 Event，互换则 `watch`
等错了信号（后者可为 `None`，互换还会直接把等待打成 AttributeError 或永久空转）。

除了形状，这里还钉住 `slot_allowed` 的两条语义（本机余量优先 / 远端超时才放行补孵槽）。
"""

from __future__ import annotations

import dataclasses
import inspect
import threading
import time
from collections.abc import Callable
from typing import cast

import pytest

from trainer.queue_local import LaneSpec, LocalLane

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


#: 旧 `LocalLane.__init__` 的 11 个形参（第 1 个是位置）。
FIELDS: list[tuple[str, object]] = [
    ("worker", MISSING),
    ("local_slots", MISSING),
    ("cap_full", MISSING),
    ("local_cap", MISSING),
    ("last_remote_ok", MISSING),
    ("remote_dead_sec", MISSING),
    ("all_settled", MISSING),
    ("halt_event", MISSING),
    ("deadline", MISSING),
    ("lock", MISSING),
    ("threads", MISSING),
]


def _noop(_slot: dict | None, _n: int | None) -> None:
    """worker 闭包（这条用例不孵任何槽，所以不会被调用）。"""


def _lane(*, local_cap: int = 2, ok_ago: float = 0.0, dead_sec: float = 30.0) -> LocalLane:
    """造一条本机槽账：`local_cap` 余量、最后一次远端结算在 `ok_ago` 秒前。"""
    return LocalLane(
        LaneSpec(
            worker=_noop,
            local_slots=1,
            cap_full=4,
            local_cap=[local_cap],
            last_remote_ok=[time.time() - ok_ago],
            remote_dead_sec=dead_sec,
            all_settled=threading.Event(),
            halt_event=None,
            deadline=float("inf"),
            lock=threading.Lock(),
            threads=[],
        )
    )


def test_field_table_matches_the_old_signature() -> None:
    assert _table(LaneSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(LaneSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _lane_spec()
    name = rows[-1].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, name, [])


def _lane_spec() -> LaneSpec:
    return LaneSpec(
        worker=_noop,
        local_slots=1,
        cap_full=4,
        local_cap=[2],
        last_remote_ok=[0.0],
        remote_dead_sec=30.0,
        all_settled=threading.Event(),
        halt_event=None,
        deadline=0.0,
        lock=threading.Lock(),
        threads=[],
    )


def test_entry_takes_exactly_one_spec() -> None:
    assert list(inspect.signature(LocalLane.__init__).parameters) == ["self", "spec"]


def test_old_keyword_form_is_rejected() -> None:
    make = cast(Callable[..., object], LocalLane)
    with pytest.raises(TypeError):
        make(_noop, local_slots=1, cap_full=4)


def test_slot_allowed_prefers_local_headroom() -> None:
    """本机还有余量（`slot` < `local_cap[0]`）⇒ 允许；超出且远端仍活着 ⇒ 拒。"""
    lane = _lane(local_cap=2, ok_ago=0.0)
    assert lane.slot_allowed(1) is True
    assert lane.slot_allowed(2) is False


def test_slot_allowed_opens_up_after_remote_goes_quiet() -> None:
    lane = _lane(local_cap=2, ok_ago=9_999.0, dead_sec=30.0)
    assert lane.slot_allowed(2) is True
