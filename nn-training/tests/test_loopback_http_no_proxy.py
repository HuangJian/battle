"""test_loopback_http_no_proxy.py — 回环 HTTP 必须绕开环境代理（2026-09-18 门禁实测）。

**起因（真事件）**：本机用户级环境带 `HTTP_PROXY`/`HTTPS_PROXY`，而 `no_proxy` 里写的是
`127.*` —— Python 的 `proxy_bypass()` 不认这种通配（只认 `host == entry` / `*.suffix` /
`.suffix`），`proxy_bypass("127.0.0.1")` 实测 **False**。于是所有走 `127.0.0.1` 的请求都被
送到外部代理再转回来：门禁里 `tests/test_offline_deliver.py::test_offline_endpoints_require_auth`
红过一次（hub 日志明明两次 401，测试侧读到 502——代理回的错误页），本机训练也凭空多一跳。

**回归口径**：把环境代理指到一个**死端口**（连不上就立刻失败，不会挂），再对本机真 HTTP
服务发请求——必须成功。修复前：走代理 ⇒ 连接被拒；修复后：绕开 ⇒ 正常。三条最要紧的出口
各钉一次：`hub_client._request`（训练侧↔hub）、`push_dispatch._http`（hub↔GPU worker）、
`worker._request`（worker↔hub，它平时刻意用显式 ProxyHandler，只有回环该绕）。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from remote import net_http
from remote.hub_client import _request as hub_request
from remote.push_dispatch import _http as push_http
from remote.worker import _request as worker_request


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # 测试里不刷日志
        return None


@pytest.fixture
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


@pytest.fixture
def dead_proxy(monkeypatch: pytest.MonkeyPatch) -> str:
    """环境代理指向死端口，并确保 no_proxy 不替我们兜底（清掉它）。"""
    dead = "http://127.0.0.1:1"  # 本环境对回环拒绝连接是 DROP 还是 RST 都不重要：没人会真的用它
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(k, dead)
    for k in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    return dead


def test_is_loopback_forms() -> None:
    """回环判据：URL、裸 host、带端口、IPv6、*.localhost 都要认，公网/局域网不认。"""
    for yes in (
        "http://127.0.0.1:8900/x",
        "http://127.0.0.1",
        "127.0.0.1:8900",
        "127.5.6.7",
        "http://localhost:8000",
        "http://[::1]:8000/ping",
        "http://x.localhost:1",
    ):
        assert net_http.is_loopback(yes), yes
    for no in (
        "",
        "http://192.168.0.99:8900",
        "http://gpu-node.example.com/ping",
        "http://10.0.0.7:1",
        "https://tunnel.trycloudflare.com",
        "127.0.0.1.evil.com",  # 后缀冒充不是回环
    ):
        assert not net_http.is_loopback(no), no


def test_suite_env_bypasses_loopback_proxy() -> None:
    """conftest 把**精确回环主名**补进 `no_proxy`：连裸 `urlopen` 的既有用例也不走代理。

    生产侧走 `remote/net_http.py`（不靠环境变量）；测试侧这一行是兑底——否则每个用
    `urllib.request.urlopen` 打本机临时端口的用例都在外面套一层代理。
    """
    for key in ("no_proxy", "NO_PROXY"):
        entries = [x.strip() for x in os.environ.get(key, "").split(",")]
        assert "127.0.0.1" in entries and "localhost" in entries, (key, entries)
    import urllib.request

    assert urllib.request.proxy_bypass("127.0.0.1") is True
    assert urllib.request.proxy_bypass("localhost") is True


def test_hub_client_loopback_bypasses_dead_proxy(local_server: str, dead_proxy: str) -> None:
    """训练侧↔hub：代理是死的，本机请求仍要通（修复前：走代理 ⇒ 连接被拒）。"""
    status, body = hub_request(local_server, "tok", "/ping", timeout=5.0)
    assert status == 200 and json.loads(body)["ok"] is True


def test_push_dispatch_loopback_bypasses_dead_proxy(local_server: str, dead_proxy: str) -> None:
    """hub↔GPU worker：探活/推送这条腿同样绕开代理（代理挂了不能判 worker 离场）。"""
    res = push_http(local_server, "tok", "/ping", timeout=5.0)
    assert res is not None and res[0] == 200, res


def test_worker_loopback_bypasses_dead_proxy(local_server: str, dead_proxy: str) -> None:
    """worker↔hub：本机 worker 轮询本机 hub 不该被代理截走（显式 ProxyHandler 只管非回环）。"""
    status, body = worker_request(local_server, "tok", "/jobs/peek", timeout=5.0)
    assert status == 200 and json.loads(body)["ok"] is True


def test_non_loopback_still_uses_urllib_default(
    monkeypatch: pytest.MonkeyPatch, dead_proxy: str
) -> None:
    """非回环（隧道/公网 URL）**保持 urllib 默认**（读环境代理、且保留 urlopen 那条 mock 缝）。

    云机侧「显式 ProxyHandler」的需求（Colab userspace 实测）建立在默认路径上，不能被
    本模块改写。
    """
    import urllib.request

    seen: list[str] = []

    class _Resp:
        status = 200

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: object) -> None:  # 不吞异常
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(req, timeout=0):  # 与 urllib.request.urlopen 同签名
        seen.append(getattr(req, "full_url", str(req)))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    net_http.urlopen(urllib.request.Request("https://tunnel.example.com/ping"), timeout=5.0)
    assert seen == ["https://tunnel.example.com/ping"], "非回环必须仍走 urllib.request.urlopen"
