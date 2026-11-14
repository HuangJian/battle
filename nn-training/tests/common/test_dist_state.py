"""`common.distribution` 的两份进程级状态现在是显式对象（P0-5 / `common/__init__.py` 契约 #4）。

模块只留 `_REQUESTS` / `_RESTARTS` 两个默认实例，状态本体在 `RequestRegistry` /
`RestartMemo` 里 ⇒ 每个实例自成一份，测试要隔离就自己 new 一个，不必去清（也不必读）
模块私有集合。这里只验隔离性：两份实例互不可见，公开函数走的仍是默认实例。
"""

from __future__ import annotations

import common.distribution as dist


def test_request_registries_are_independent() -> None:
    a = dist.RequestRegistry()
    b = dist.RequestRegistry()
    resp = object()

    a.track(("scope-a", resp))
    assert a.active == {("scope-a", resp)}
    assert b.active == set()

    targets = a.begin_abort("scope-a")
    assert targets == [resp]
    assert a.is_aborted("scope-a") is True
    assert b.is_aborted("scope-a") is False, "第二个实例不许看见别人的收工态"

    a.untrack(("scope-a", resp))
    assert a.active == set()
    b.clear_abort()
    assert b.aborted == set()


def test_bare_clear_only_clears_its_own_registry() -> None:
    a = dist.RequestRegistry()
    b = dist.RequestRegistry()
    a.begin_abort("s1")
    b.begin_abort("s1")
    a.clear_abort()
    assert a.is_aborted("s1") is False
    assert b.is_aborted("s1") is True


def test_restart_memos_are_independent() -> None:
    a = dist.RestartMemo()
    b = dist.RestartMemo()
    a.seen["mac"] = ("ping", "expected", 1.0)
    assert a.seen == {"mac": ("ping", "expected", 1.0)}
    assert b.seen == {}
    b.reset()
    assert b.seen == {}
    assert a.seen.get("mac") == ("ping", "expected", 1.0)
