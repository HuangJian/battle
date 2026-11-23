"""tests/remote/test_notebook_keepalive.py —— 保活是**内核单例**，不是「执行一次 cell 就再来一条」。

为什么：四个 notebook（`battle.cloudflared` / `battle.offline` / `battle.tailscale` / `rollout`）的
保活都是「Event 停机柄 + 一条 daemon 线程」。裸 `threading.Event()` 赋值加无条件
`Thread(...).start()` ⇒ 同一 kernel 重跑 cell 就有第二条线程拿着第二个 Event，旧线程永远收不到
`set()`（现场：两串 alive 分钟数交替递增，其中一个属于早已退出的会话）。

判据是**行为**（不是措辞）：把每个 notebook 的保活段（Event 守卫 → 起线程）在同一个命名空间里
执行两遍（= 重跑 cell），只该剩一条 `keepalive` 线程，且 `set()` 能把它停掉。

新增 notebook 只要起了保活线程就自动进这张表（参数以 `threading.Thread(..., name="keepalive")`
为准），不用回来登记。
"""

from __future__ import annotations

import ast
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

NN = Path(__file__).resolve().parents[2]
IPYNB = NN / "ipynb"


def _cells(kind: str, notebook: Path) -> str:
    import json

    nb = json.loads(notebook.read_text(encoding="utf-8"))
    return "".join("".join(c.get("source", [])) for c in nb["cells"] if c["cell_type"] == kind)


def _code(notebook: Path) -> str:
    return _cells("code", notebook)


def _has_keepalive_thread(notebook: Path) -> bool:
    return 'name=\\"keepalive\\"' in notebook.read_text(encoding="utf-8")


KEEPALIVE_NOTEBOOKS = sorted(p for p in IPYNB.glob("*.ipynb") if _has_keepalive_thread(p))


def test_every_keepalive_notebook_is_in_the_table() -> None:
    """表是扫出来的，不是手抄的 —— 但也不能空（空了这条守卫就恒绿）。"""
    assert len(KEEPALIVE_NOTEBOOKS) >= 4, [p.name for p in KEEPALIVE_NOTEBOOKS]


def _event_name(stmt: ast.stmt) -> str:
    """`threading.Event()` 那条语句绑出来的名字（`_stop` / `_keepalive_stop`）。"""
    for node in ast.walk(stmt):
        if isinstance(node, ast.Assign) and "threading.Event" in ast.unparse(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    return t.id
    raise AssertionError(f"没找到 Event 绑定：{ast.unparse(stmt)}")


def _keepalive_block(notebook: Path) -> list[ast.stmt]:
    """摘出保活段：Event 守卫（单例 + 复位）· `_keepalive*` 定义 · 起线程的闸。

    只取这三段，不整段跑 cell（CFG / 凭据 / clone 都不在这里面）。
    """
    body = ast.parse(_code(notebook), filename=f"<{notebook.name}>").body
    ev_idx = next(i for i, n in enumerate(body) if "threading.Event" in ast.unparse(n))
    def_idx = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.FunctionDef) and "keepalive" in n.name
    )
    start_idx = next(
        i
        for i, n in enumerate(body)
        if i > def_idx and "threading.Thread" in ast.unparse(n) and "keepalive" in ast.unparse(n)
    )
    name = _event_name(body[ev_idx])
    guards = [n for n in body[ev_idx:def_idx] if name in ast.unparse(n)]
    return [*guards, body[def_idx], body[start_idx]]


def _threading_shim(events: list[threading.Event]) -> SimpleNamespace:
    """替身 `threading`：`Event()` 建出来的都登记进 `events`。

    为什么需要：旧写法重跑时会把 Event 换掉，收工只能 `set()` 到新的那个 —— 登记表让用例
    自己收干净，不给下一条用例留野线程。
    """

    def _event() -> threading.Event:
        ev = threading.Event()
        events.append(ev)
        return ev

    return SimpleNamespace(
        Event=_event, Thread=threading.Thread, enumerate=threading.enumerate
    )


def _live() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "keepalive" and t.is_alive()]


def _stop_all(events: list[threading.Event]) -> None:
    for ev in events:
        ev.set()
    for t in _live():
        t.join(timeout=5)


@pytest.mark.parametrize(
    "notebook", KEEPALIVE_NOTEBOOKS, ids=[p.name for p in KEEPALIVE_NOTEBOOKS]
)
def test_rerunning_the_cell_keeps_one_heartbeat(notebook: Path) -> None:
    """同一 kernel 跑两遍 = 1 条线程（旧写法：2 条，且 `set()` 只能停掉新那条）。"""
    payload = ast.Module(body=_keepalive_block(notebook), type_ignores=[])
    code = compile(payload, f"<{notebook.name}:keepalive>", "exec")
    events: list[threading.Event] = []
    ns: dict[str, Any] = {
        "threading": _threading_shim(events),
        "time": time,
        "os": os,
        "_log": lambda _m: None,
        "CFG": {"keepalive_every_sec": 600},
    }
    try:
        for _ in range(2):
            exec(code, ns)  # 被测物就是 cell 源码
        assert len(_live()) == 1, (
            f"{notebook.name}: 重跑 cell 起了 {len(_live())} 条心跳（应只有 1 条）—— "
            "Event 要做模块级单例，起线程前按 name + is_alive 去重"
        )
    finally:
        _stop_all(events)
    assert not _live(), f"{notebook.name}: `set()` 停不掉心跳（Event 被重跑换掉了）"
