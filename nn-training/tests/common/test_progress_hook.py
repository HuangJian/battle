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


def test_iter_progress_logs_at_most_once_a_minute() -> None:
    """`iter`（采集局）**日志行**：一轮 3s 的采集一行都不打；跑久了每分钟一行（2026-10-29 瘦身）。

    现场：一轮 239 局只要 3s，`should_log_progress` 的 25% 一桶打 5 行
    （1/94 → 31/124 → 93/186 → 180/239 → 239/239），而它们说的同一件事已经写在轮末那行
    （`进度=239/239 games settled`）。**发报不受这层窗影响**：`interval=0` 时每句都发
    ——「发不发」仍只有打点层一处决定，这里只压日志行。
    """
    lines: list[str] = []
    posts: list[str] = []
    now = [1000.0]

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        posts.append(url)
        return 200, {}

    detach = offline_boot.install_progress_pinger(
        "http://hub",
        "tok",
        "c5-gae",
        "lease1",
        lines.append,
        interval=0.0,  # 只看日志窗：发报一律放行
        post=fake_post,
        clock=lambda: now[0],
    )
    try:
        # 一轮 3s 的采集：一行都不打（换轮即重置窗口 + 该轮第一条不打）
        for done, total in [(1, 94), (31, 124), (93, 186), (180, 239), (239, 239)]:
            now[0] += 0.5
            progress_hook.report("iter", it=111, done=done, total=total)
        # 别的 kind 不受这个窗影响（轮边界那几句必须照旧出现）
        progress_hook.report("round-done", it=111, force=True)
        # 同一轮跑过一分钟 ⇒ 采集局再开口一行（长 rollout 仍然看得见活着）
        now[0] += offline_boot.ITER_LOG_MIN_SEC + 1
        progress_hook.report("iter", it=111, done=300, total=400)
        # 换到下一轮：又回到「一条不打」
        progress_hook.report("iter", it=112, done=1, total=94)
    finally:
        detach()

    iter_lines = [m for m in lines if "采集局" in m]
    assert len(iter_lines) == 1 and "第111轮" in iter_lines[0], iter_lines
    assert "第112轮" not in "".join(iter_lines), "换轮第一条（1/94）没有信息量，不该打"
    assert any("本轮完成" in m for m in lines), "非 iter 的进度行不许被这个窗吃掉"
    assert len(posts) == 8, f"发报照旧逐事件（8 句），实际 {len(posts)}"
