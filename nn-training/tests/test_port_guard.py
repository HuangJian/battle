"""test_port_guard.py — 双监听守卫（remote/_port_guard.ensure_port_free）单测。

2026-09-09 事故：Windows SO_REUSEADDR 允许两个 hub_server 同时 bind 8787，后启动
者不崩、静默变成收不到连接的僵尸（实测新连接全进先绑定者）。守卫用 TCP 探测拒绝
「端口已有活监听者」时的启动。

运行（经统一启动器）：-Script test_port_guard.py；全过 0，否则 1。
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from remote._port_guard import ensure_port_free


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_free_port_passes() -> None:
    """无人监听 → 守卫放行（正常启动不被误伤）。"""
    ensure_port_free("127.0.0.1", _free_port())


def test_occupied_port_refuses() -> None:
    """已有活监听者 → 守卫拒绝（RuntimeError）。"""
    srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(RuntimeError, match="已被占用"):
            ensure_port_free("127.0.0.1", port)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def test_occupied_with_reuseaddr_still_refuses() -> None:
    """占用方自己设了 SO_REUSEADDR（事故场景：双绑定已成立）→ 守卫照样拒绝。"""
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        with pytest.raises(RuntimeError, match="已被占用"):
            ensure_port_free("127.0.0.1", port)
    finally:
        s.close()
