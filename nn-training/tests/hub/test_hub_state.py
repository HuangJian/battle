"""`HubHandler` 的进程级可变状态**只有一个家**：`HubState`（2026-11-19，`python.refactor.md` 附录 B）。

`ThreadingHTTPServer` 每请求新建一个 handler 实例 ⇒ 任何跨请求的共享态只能挂在类上。原先那是
**四份散着的类属性**（`_blocked_logged` / `_seen_worker_sources` / `_worker_source_warned` /
`_worker_pulse`），于是「这是一份进程级共享状态」在调用点看不出来（`self._blocked_logged[ip] = now`
与实例字段长得一模一样），用例隔离也只能逐表 `.clear()`（漏清一张不会报错，只会让用例之间串味）。

本文件钉两件事：

  ① **结构**：`HubHandler` 类体里不许再出现「可变容器字面量」的类属性（`{}` / `[]` / `set{}` 与
     `dict()` / `list()` / `set()` 这类构造）——共享态一律走 `state: HubState`；
  ② **行为**：`HubState` 的两个实例互不相干（换一份 = 真隔离），且 `HubHandler.state` 是
     import 期建好的单一份（两个 handler 实例看到同一个对象）。

① 是 AST 判据，用 `pytest.fail` 而不是 `assert`：本文件读生产 `.py`，计数类判据不走文本断言口径。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hub.http_face import HubHandler, HubState
from hub.worker_pulse import WorkerPulse

HTTP_FACE = Path(__file__).resolve().parents[2] / "hub" / "http_face.py"

#: 「可变容器」的构造形态：字面量 + 内建构造调用（`{}` / `dict()` / `[]` / `list()` / `set()`）。
_MUTABLE_LITERALS = (ast.Dict, ast.List, ast.Set)
_MUTABLE_CTORS = frozenset({"dict", "list", "set", "defaultdict"})


def test_the_handler_class_keeps_only_one_shared_state_object() -> None:
    """`HubHandler` 类体里只有 `state` 这一份共享态：可变容器类属性一个都不许有。"""
    tree = ast.parse(HTTP_FACE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HubHandler")
    offenders: list[str] = []
    for node in cls.body:
        names: list[str]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        if isinstance(value, _MUTABLE_LITERALS) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in _MUTABLE_CTORS
        ):
            offenders += names
    if offenders:
        pytest.fail(
            f"HubHandler 类体里又有可变的共享类属性 {offenders} —— 进程级状态一律收进 HubState"
            f"（self.state.…），否则「这是共享态」在调用点看不见"
        )
    # 那个唯一允许的共享态真的在（防「清干净了但 object 也删了」）。
    state_value = next(
        (
            n.value
            for n in cls.body
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "state"
        ),
        None,
    )
    if not (isinstance(state_value, ast.Call) and getattr(state_value.func, "id", "") == "HubState"):
        pytest.fail("HubHandler.state 必须声明为 `state: HubState = HubState()`")


def test_two_state_objects_do_not_share_their_tables() -> None:
    """换一份 `HubState` = 真隔离：三个节流/观测表与哨兵都是各自的。"""
    one, two = HubState(), HubState()
    one.blocked_logged["10.0.0.1"] = 1.0
    one.seen_sources["kaggle-c"] = ("10.0.0.2", 2.0)
    one.source_warned["kaggle-c"] = 3.0
    one.pulse.touch("kaggle-c")
    assert two.blocked_logged == {}
    assert two.seen_sources == {}
    assert two.source_warned == {}
    assert isinstance(two.pulse, WorkerPulse)
    assert two.pulse is not one.pulse


def test_the_class_state_is_one_object_shared_by_every_request() -> None:
    """`HubHandler.state` 是 import 期的单一份：两个 handler 实例写的是同一个对象。

    这就是「进程级」的定义（`ThreadingHTTPServer` 每请求新建实例）——若有人把它改成
    `__init__` 里的实例字段，每个请求都会拿到一份空表，封禁节流与同名来源告警当场失效。
    """
    state = HubHandler.state
    assert isinstance(state, HubState)
    # 两个「请求实例」（`object.__new__` 绕开 `BaseHTTPRequestHandler.__init__`，那个会开始
    # 处理请求）看到的是同一个 `state` —— 这就是跨请求共享面的定义。
    one = object.__new__(HubHandler)
    two = object.__new__(HubHandler)
    assert one.state is state and two.state is state
    one.state.blocked_logged["10.0.0.9"] = 4.0
    assert two.state.blocked_logged["10.0.0.9"] == 4.0
    del state.blocked_logged["10.0.0.9"]
