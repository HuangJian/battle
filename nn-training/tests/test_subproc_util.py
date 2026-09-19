"""test_subproc_util —— `spawn_bound_port()` 消化掉「探测端口 → 起真进程 bind」的 TOCTOU。

2026-09-19 实测（R4 提交被 pre-commit 拦下的直接原因）：gate 跑 pytest xdist 时，A worker
探测到端口 P 后 close、再去起 hub-server（冷启动 ~1s），B worker 的探测在这窗口里拿到
同一个 P 并先绑上 ⇒ A 的 hub 被 `_port_guard` 拒启（「禁止双监听」）⇒ 报成与该用例毫无
关系的假红：`hub-server 未就绪或课程表不对`，真因只埋在子进程输出里。

本文件钉住 helper 的契约：
  ① 撞端口 ⇒ **换端口重试**（而不是把假红丢给调用方）；
  ② 非端口原因的死 ⇒ **立刻**响亮失败，不重试（重试只会把真 bug 藏起来）；
  ③ 重试上限到顶 ⇒ 响亮失败并指出是并行端口竞争；
  ④ 成功判据是**这个子进程自己**自报监听，不是「端口上有人监听」。

子进程用真 `remote._port_guard.ensure_port_free`（文案不硬编码在测试里），
所以守卫文案一改，`PORT_TAKEN_MARKER` 就跟着红 —— 否则换端口重试会静默失效。
"""

from __future__ import annotations

import os
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote._port_guard import ensure_port_free
from tests.subproc_util import (
    PORT_TAKEN_MARKER,
    SPAWN_PORT_ATTEMPTS,
    spawn_bound_port,
)

#: 假服务：先过真端口守卫，再 bind/listen 并按 hub-server / worker-serve 的形状自报。
_CHILD_LISTEN = (
    "import socket, sys, time\n"
    "from remote._port_guard import ensure_port_free\n"
    "port = int(sys.argv[1])\n"
    "ensure_port_free('127.0.0.1', port)\n"
    "s = socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind(('127.0.0.1', port))\n"
    "s.listen(8)\n"
    "print(f'listening on 127.0.0.1:{port}', flush=True)\n"
    "while True:\n"
    "    time.sleep(0.2)\n"
)

#: 什么都不 bind、也不自报的服务（钉「超时不表态 ⇒ 不硬失败」的兜底）。
_CHILD_QUIET = "import time\nprint('quiet-marker', flush=True)\nwhile True:\n    time.sleep(0.2)\n"

#: 非端口原因的死法（钉「不重试，立刻抛」）。
_CHILD_DEAD = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)\n"


def _spawn(make_argv, **kw):
    """按测试惯例给子进程配 PYTHONPATH（能 import remote.*）与 repo 根 cwd。"""
    return spawn_bound_port(
        make_argv, cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)}, **kw
    )


@contextmanager
def _held_port() -> Iterator[int]:
    """占住一个端口（有活监听者）——复刻竞争里的「先绑者」。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        yield int(s.getsockname()[1])
    finally:
        s.close()


def _connectable(port: int, *, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _reap(proc) -> None:
    proc.kill()
    proc.wait(timeout=5)


def test_marker_matches_the_real_port_guard_message() -> None:
    """常量必须跟着真守卫走（文案漂移 ⇒ 换端口重试静默失效，假红回来）。"""
    with _held_port() as taken, pytest.raises(RuntimeError) as e:
        ensure_port_free("127.0.0.1", taken)
    assert PORT_TAKEN_MARKER in str(e.value), (
        "PORT_TAKEN_MARKER 与 remote/_port_guard.py 的拒绝文案不一致"
    )


def test_retries_with_a_new_port_when_the_first_is_taken() -> None:
    """本 helper 存在的理由：第一次撞上别人的端口 ⇒ 换端口重试，而不是把假红丢给调用方。"""
    with _held_port() as taken:
        calls: list[int] = []

        def argv(port: int) -> list[str]:
            calls.append(port)
            # 第一次刻意把子进程指向**别人占着**的端口（= xdist 竞争里「后绑者」的命运）
            target = taken if len(calls) == 1 else port
            return [sys.executable, "-u", "-c", _CHILD_LISTEN, str(target)]

        srv = _spawn(argv)
        try:
            assert len(calls) == 2, f"撞端口必须换端口重试（实际尝试 {len(calls)} 次）"
            assert srv.port == calls[1] and srv.port != taken
            assert _connectable(srv.port), "返回的端口必须真的在监听"
            assert any("listening on" in ln for ln in srv.lines), srv.lines
        finally:
            _reap(srv.proc)


def test_non_port_death_fails_loudly_without_retry() -> None:
    """非端口原因的死法不重试——重试只会把真 bug 藏成偶尔红一次。"""
    calls: list[int] = []

    def argv(port: int) -> list[str]:
        calls.append(port)
        return [sys.executable, "-u", "-c", _CHILD_DEAD]

    with pytest.raises(AssertionError) as e:
        _spawn(argv)
    assert len(calls) == 1, "非端口冲突不该重试"
    assert "非端口冲突" in str(e.value)
    assert "boom" in str(e.value), "诊断消息必须带上子进程自己的输出"


def test_all_attempts_taken_fails_loudly() -> None:
    """上限到顶 ⇒ 响亮失败并说清是并行端口竞争，而不是无限重试或静默。"""
    with _held_port() as taken:
        calls: list[int] = []

        def argv(port: int) -> list[str]:
            calls.append(port)
            return [sys.executable, "-u", "-c", _CHILD_LISTEN, str(taken)]

        with pytest.raises(AssertionError, match="连续"):
            _spawn(argv, attempts=3)
    assert len(calls) == 3, f"应恰好重试到上限（实际 {len(calls)} 次）"
    assert SPAWN_PORT_ATTEMPTS >= 3, "默认上限不该低于测试里显式给的值"


def test_silent_child_is_accepted_after_timeout_and_tail_reads_output() -> None:
    """超时未表态 ⇒ 当成功（日志文案变了不该变硬失败），就绪判定交回调用方。"""
    srv = _spawn(
        lambda port: [sys.executable, "-u", "-c", _CHILD_QUIET], listen_timeout=0.3
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not srv.lines:
            time.sleep(0.02)
        assert srv.lines[:1] == ["quiet-marker"], "reader 线程必须实时收下子进程输出"
        assert srv.tail().endswith("quiet-marker"), "tail() 是诊断出口，进程活着也要能取"
    finally:
        _reap(srv.proc)


def test_call_sites_go_through_the_helper() -> None:
    """源码守卫：起真服务进程的地方不许再用裸 `_free_port()`（TOCTOU 假红会回来）。"""
    for rel in (
        "e2e/test_multi_course_single_hub_e2e.py",
        "tests/test_multi_course_hub.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "spawn_bound_port(" in src, f"{rel} 应走 spawn_bound_port()"
        assert "_free_port()" not in src, (
            f"{rel} 里的裸 _free_port() 会重现「探测→子进程 bind」的 TOCTOU 假红"
        )
