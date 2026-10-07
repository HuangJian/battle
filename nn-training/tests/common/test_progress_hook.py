"""tests/common/test_progress_hook.py —— 轮内打点的**上报口**（`common/progress_hook.py`，★M3）。

契约只有两个事实（两个方向都有守卫）：

  * 快照侧（`plan_run` / `iter_rollout`）调 `report(...)` 时**只会**做一件事：把事件交给注册者；
    没有注册者 / 注册者抛异常 ⇒ 一律返回 False 且**绝不外抛**（打点是观测，不是训练的一环）；
  * 注册名 `HOOK_NAME` 与云机侧（`remote/offline_boot.py`）那份逐字相同——两边各自演进，
    名字是唯一接缝，漂了就是「打点层装了但没人报」或「报了没人接」（都是静默的）。
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from common import progress_hook
from remote import offline_boot


@pytest.fixture()
def installed(monkeypatch: pytest.MonkeyPatch):
    """装一个**记录型**打点层（返回 `(seen, hook)`；teardown 自动卸下注册）。"""

    def _install(exc: BaseException | None = None):
        seen: list[tuple[str, dict]] = []

        def note_progress(kind: str = "", **kw: Any) -> bool:
            if exc is not None:
                raise exc
            seen.append((kind, kw))
            return True

        mod = ModuleType(progress_hook.HOOK_NAME)
        mod.__dict__["note_progress"] = note_progress
        monkeypatch.setitem(sys.modules, progress_hook.HOOK_NAME, mod)
        return seen

    return _install


def test_report_without_a_registered_hook_is_a_cheap_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有注册者（本机训练 / 老快照 / 没领到租约）⇒ False，什么都不做、什么都不抛。"""
    monkeypatch.delitem(sys.modules, progress_hook.HOOK_NAME, raising=False)
    assert progress_hook.hook() is None
    assert progress_hook.report("iter", done=3, total=10) is False


def test_report_forwards_the_event_shape(installed) -> None:
    """事件形状（位置参 `kind` + 关键字 it/done/total/force）是两边的契约，逐字钉住。"""
    seen = installed()
    assert progress_hook.report("round-done", it=42, done=7, total=9, force=True) is True
    assert seen == [("round-done", {"it": 42, "done": 7, "total": 9, "force": True})]


def test_report_swallows_a_broken_hook(installed) -> None:
    """打点层炸了也不许把训练带走（观测腿的纪律与心跳线程同一条）。"""
    seen = installed(exc=RuntimeError("网络栈炸了"))
    assert progress_hook.report("iter", done=1, total=2) is False
    # 连「返回值本身不可 bool()」这种怪形状也不放出来
    stub: Any = SimpleNamespace(note_progress=lambda *a, **k: object())
    sys.modules[progress_hook.HOOK_NAME] = stub
    try:
        assert progress_hook.report("iter") is True  # bool(object()) 恒真，但**不许抛**
    finally:
        sys.modules.pop(progress_hook.HOOK_NAME, None)
    assert seen == []


def test_report_ignores_a_hook_without_the_contract(installed, monkeypatch) -> None:
    """注册了但没有 `note_progress`（别人占了同名键）⇒ 当作没注册。"""
    monkeypatch.setitem(sys.modules, progress_hook.HOOK_NAME, SimpleNamespace())
    assert progress_hook.report("iter") is False


def test_hook_name_matches_the_boot_side_constant() -> None:
    """★M3：注册名两边各有一份（boot 侧不许 import 本仓代码）——必须逐字相同。"""
    assert offline_boot.PROGRESS_HOOK_NAME == progress_hook.HOOK_NAME


def test_boot_side_reports_through_the_common_hook() -> None:
    """云机侧装的层被**本模块**的 `report` 找到（端到端一次：注册 → 上报 → 撤销）。"""
    calls: list[str] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        calls.append(url)
        return 200, {}

    detach = offline_boot.install_progress_pinger(
        "http://hub", "tok", "c5-gae", "lease1", lambda _m: None, post=fake_post
    )
    try:
        assert progress_hook.report("iter", done=1, total=3) is True
    finally:
        assert detach() == ""
    assert calls and "lease=lease1" in calls[0]
    assert progress_hook.hook() is None, "detach() 之后不能再有注册者（下一门课会装自己的）"
