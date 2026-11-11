"""connect 竞速 —— 「前几个地址黑洞」不许把预算乘上地址个数。

## 现场（2026-11-11，kaggle / gcs 云机隧道）

`socket.create_connection` 对 `getaddrinfo` 的结果是**串行**试的，而 `timeout` 是
**每地址**的、不是总预算。本机没有到 Cloudflare 的可用 IPv6 路由，于是：

    kaggle 主机名 → 2606:4700::6810:e784  失败 8.00s
                    2606:4700::6810:e684  失败 8.01s
                    104.16.231.132        成功 0.21s
                    104.16.230.132        成功 0.21s

一次 ping 的 tcp 段实测 **16.24s**（dns 0.04 / tls 0.22 / headers 0.53）。
`urllib` 每请求新建连接 ⇒ 这笔税在 ping / 权重探针 / POST / 取任务 / 取结果上各交一遍，
每轮还派发 2~3 波 ⇒ rollout 63s 里 43s 是节点门（真采集只有 16s）。
gcs 的主机名恰好只解析出 IPv4 ⇒ 0.43s，同一个 Cloudflare，差别全在 DNS 答案。

## 本文件钉什么

1. 前 N 个地址黑洞时，连接在**最快可达地址**的耗时内返回，不是 N×timeout；
2. `common.distribution._request`（节点通信的唯一出口）真的走这条路；
3. 输家连接必须关掉（不许漏 socket）；单地址快路径行为不变；
4. 全黑洞时抛错，且墙钟被**总预算**兜住（不是 N×timeout）。

判据刻意用**事件**而不是墙钟阈值：黑洞 connect 挂在 `release` 上，串行实现必须等
它被 set 才可能返回 ⇒ 「2s 内拿到结果」是结构性判据，不是时序骰子。
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

import common.distribution as dist
from common import net_http

# 黑洞地址（RFC 3849 文档段，永不路由）；本文件不会真去连它们——connect 被桩截住。
_BLACKHOLES = frozenset({"2001:db8::1", "2001:db8::2"})

#: 导入期抓一份真解析器：用例内的 monkeypatch 会换掉 `socket.getaddrinfo`。
_real_getaddrinfo = socket.getaddrinfo


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a) -> None:
        pass


@pytest.fixture
def wired(monkeypatch):
    """真 HTTP 服务 + 「黑洞×2 然后可达」的 DNS 答案 + 只截黑洞 connect 的 socket 包装。

    URL 主名用 `127.0.0.1`：conftest 已把它写进 no_proxy，且回环绕代理是本仓库既有
    规矩 ⇒ 用例不受本机 HTTP_PROXY 影响（换个主名就会被代理截走，桩根本轮不到）。
    """
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    release = threading.Event()
    losers_closed = threading.Event()
    closed: list = []
    real_socket = socket.socket
    real_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(host, p, *a, **k):
        if str(host) == "127.0.0.1" and int(p) == port:
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (bh, port, 0, 0))
                for bh in sorted(_BLACKHOLES)
            ] + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real_getaddrinfo(host, p, *a, **k)

    class _Wrapped:
        """真 socket 的薄包装：只有「连黑洞」这一支被换成挂死，其余逐字转发。"""

        def __init__(self, inner) -> None:
            self._inner = inner
            self._timeout = None
            self._blackhole = False

        def settimeout(self, t) -> None:
            self._timeout = t
            self._inner.settimeout(t)

        def connect(self, sa) -> None:
            self._blackhole = bool(isinstance(sa, tuple) and sa and str(sa[0]) in _BLACKHOLES)
            if self._blackhole:
                release.wait(self._timeout if isinstance(self._timeout, (int, float)) else 5.0)
                raise TimeoutError("timed out")
            self._inner.connect(sa)

        def close(self) -> None:
            if self._blackhole:
                closed.append(self)
                if len(closed) >= len(_BLACKHOLES):
                    losers_closed.set()
            self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _socket(family=socket.AF_INET, socktype=socket.SOCK_STREAM, proto=0, fileno=None):
        inner = real_socket(family, socktype, proto, fileno)
        return inner if fileno is not None else _Wrapped(inner)

    monkeypatch.setattr(socket, "socket", _socket)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    try:
        yield {
            "port": port,
            "url": f"http://127.0.0.1:{port}",
            "release": release,
            "closed": closed,
            "losers_closed": losers_closed,
        }
    finally:
        release.set()
        srv.shutdown()
        srv.server_close()
        th.join(5.0)


def _call_soon(fn, box: dict, wait: float = 2.0) -> threading.Thread:
    """在后台线程里跑 fn，最多等 wait 秒（串行实现会卡满预算 ⇒ 判据红）。"""

    def _run() -> None:
        try:
            box["ok"] = fn()
        except BaseException as exc:
            box["err"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(wait)
    return t


# ------------------------------------------------------------------ 1) 竞速本体


def test_racing_connect_beats_the_blackholes(wired) -> None:
    """前两个地址黑洞 ⇒ 仍在「最快可达地址」的耗时内拿到连接（不是 2×timeout）。"""
    box: dict = {}
    t = _call_soon(
        lambda: net_http.racing_create_connection(("127.0.0.1", wired["port"]), timeout=5.0),
        box,
    )
    assert not t.is_alive(), "connect 被黑洞地址串行拖住（每地址各交一遍 timeout）"
    assert box.keys().isdisjoint({"err"}), box.get("err")
    sock = box["ok"]
    try:
        assert sock.getpeername()[1] == wired["port"]
    finally:
        sock.close()


def test_losers_are_closed(wired) -> None:
    """黑洞输家必须关掉——否则每请求漏 2~3 个 fd（每轮几百请求 = fd 雪崩）。"""
    box: dict = {}
    t = _call_soon(
        lambda: net_http.racing_create_connection(("127.0.0.1", wired["port"]), timeout=5.0),
        box,
    )
    assert not t.is_alive()
    box["ok"].close()
    wired["release"].set()
    assert wired["losers_closed"].wait(5.0), (
        f"只关了 {len(wired['closed'])}/{len(_BLACKHOLES)} 个黑洞连接 ⇒ 竞速输家在漏 socket"
    )


def test_single_address_fast_path_unchanged(wired, monkeypatch) -> None:
    """只有一个地址（本机 / LAN IP 字面量的常态）⇒ 行为与 stdlib 逐字一致。"""
    monkeypatch.setattr(socket, "getaddrinfo", _real_getaddrinfo)
    sock = net_http.racing_create_connection(("127.0.0.1", wired["port"]), timeout=5.0)
    try:
        assert sock.getpeername()[1] == wired["port"]
    finally:
        sock.close()


def test_all_blackholes_bounded_by_total_budget(wired, monkeypatch) -> None:
    """全黑洞 ⇒ 抛错，且墙钟 ≈ 一份总预算，不是 N 份。"""

    def _only_blackholes(host, p, *a, **k):
        if str(host) == "127.0.0.1" and int(p) == wired["port"]:
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (bh, wired["port"], 0, 0))
                for bh in sorted(_BLACKHOLES)
            ]
        return _real_getaddrinfo(host, p, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", _only_blackholes)
    budget = 1.0
    t0 = time.monotonic()
    with pytest.raises(OSError):
        net_http.racing_create_connection(("127.0.0.1", wired["port"]), timeout=budget)
    elapsed = time.monotonic() - t0
    # timing-ok: 相对判据 —— 阈值由预算×地址数推导（串行会花满 N×budget，竞速只花 1×）
    assert elapsed < budget * len(_BLACKHOLES) * 0.9, (
        f"全黑洞花了 {elapsed:.2f}s（{len(_BLACKHOLES)} 个地址 ⇒ 串行是 "
        f"{budget * len(_BLACKHOLES):.1f}s，应被总预算 {budget:.1f}s 兜住）"
    )


# ------------------------------------------------------------------ 2) 节点出口真的走它


def test_distribution_request_uses_racing_connect(wired) -> None:
    """`_request` 是节点通信唯一出口：它不竞速，ping/探针/取任务就都还在交 IPv6 税。"""
    box: dict = {}
    t = _call_soon(lambda: dist._request(wired["url"] + "/v1/ping", "", 5.0), box)
    assert not t.is_alive(), "_request 仍走串行 create_connection（节点门 17~19s 的真因）"
    assert box.keys().isdisjoint({"err"}), box.get("err")
    status, body = box["ok"]
    assert status == 200
    assert body == b'{"ok":true}'
