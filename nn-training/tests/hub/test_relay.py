"""tests/hub/test_relay.py —— 反向会合面（`hub/relay.py`）的队列语义。

覆盖四条**承诺**（每一条都对应一种真机故障形态）：

* `call` ↔ `poll` 往返：方法/路径/头/**请求体**逐字透传，回执的状态/头/体逐字回；
* 空拍不报错：节点长轮询超时返回 None（不是异常）；
* 调用方超时后**迟到回执被丢弃**（rid 已不在 `calls` 里）——否则下一条请求会拿到上一条的答案；
* 待办队列**有界**：节点掉线时调用方仍会入队，超出上限丢最老的。

`Content-Type` 的选择（大小写不敏感、缺省 octet-stream）也在这里钉住：hub 用它决定响应头。
"""

from __future__ import annotations

import threading
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest

from hub.http_face import HubHandler
from hub.relay import NodeRelay, RelayTimeoutError, content_type_of


def _node_once(
    relay: NodeRelay,
    node: str,
    *,
    status: int = 200,
    body: bytes = b"ok",
    headers: dict[str, str] | None = None,
    seen: dict | None = None,
) -> threading.Thread:
    """模拟节点：取一条待办、回一条固定回执。"""

    def run() -> None:
        req = relay.poll(node, wait_sec=3.0)
        if seen is not None:
            seen["req"] = req
            seen["replied"] = (
                relay.reply(node, str(req["rid"]), status, headers or {}, body)
                if req
                else False
            )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_and_reply_roundtrip_keeps_bytes_and_headers() -> None:
    relay = NodeRelay()
    seen: dict = {}
    t = _node_once(
        relay,
        "n1",
        status=201,
        body=b"\x00payload\xff",
        headers={"Content-Type": "application/json"},
        seen=seen,
    )
    status, headers, body = relay.call(
        "n1",
        "POST",
        "/v1/weights?kind=rollout",
        {"Authorization": "Bearer k", "X-Sha": "abc"},
        b"\x01\x02body",
        timeout=5.0,
    )
    t.join(5.0)
    assert (status, body) == (201, b"\x00payload\xff")
    assert content_type_of(headers) == "application/json"
    req = seen["req"]
    assert req["method"] == "POST"
    assert req["path"] == "/v1/weights?kind=rollout"
    assert req["headers"] == {"Authorization": "Bearer k", "X-Sha": "abc"}
    # 请求体经 base64 过线，节点侧解出来必须逐字节相同
    import base64

    assert base64.b64decode(req["body_b64"]) == b"\x01\x02body"
    assert seen["replied"] is True


def test_poll_returns_none_on_empty_tick() -> None:
    relay = NodeRelay()
    assert relay.poll("nobody", wait_sec=0.05) is None
    assert relay.stats()["nobody"]["polls"] == 1


def test_late_reply_after_caller_timeout_is_dropped() -> None:
    """调用方超时 ⇒ rid 作废；迟到的回执**返回 False 且不新建条目**。"""
    relay = NodeRelay()
    out: dict = {}

    def caller() -> None:
        try:
            relay.call("n1", "GET", "/v1/result", {}, timeout=0.2)
        except RelayTimeoutError:
            out["timeout"] = True

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    req = relay.poll("n1", wait_sec=2.0)
    assert req is not None
    t.join(3.0)
    assert out.get("timeout") is True
    assert relay.reply("n1", str(req["rid"]), 200, {}, b"late") is False
    assert relay.stats()["n1"]["late"] == 1
    assert relay.stats()["n1"]["timeouts"] == 1


class _RouteStub(HubHandler):
    """`HubHandler._relay_route` 的最小宿主（不起 server）：只装配它真正读到的字段。

    继承而不是组合：`_relay_route` 是 `HubHandler` 自己的方法，组合就只能 unbound 调用，
    而 mypy 会对 `self` 的类型直接报错（本仓禁 `type: ignore` 消音）。
    """

    def __init__(
        self, *, peer: str = "127.0.0.1", cf: str = "", path: str = "/relay/n1/v1/ping"
    ) -> None:
        self.path = path
        self.headers = Message()
        if cf:
            self.headers["CF-Connecting-IP"] = cf
        self.client_address = (peer, 4321)
        # `server` / `wfile` 在 typeshed 里是 `BaseServer` / `BufferedIOBase`：这里只需要
        # `server_address` 与一个可读回的缓冲区，故显式 cast（不静默成 Any 铺开）。
        self.server = cast(Any, SimpleNamespace(server_address=("127.0.0.1", 8787)))
        self.rfile = BytesIO(b"")
        self.out = BytesIO()
        self.wfile = self.out
        self.sent: dict = {}

    def _json(self, obj: dict, status: int = 200) -> None:
        self.sent = {"json": obj, "status": status}

    def _send_headers(self, total: int, *, status: int = 200, ctype: str = "", **_kw: object) -> None:
        self.sent = {"status": status, "ctype": ctype, "total": total}

    def _read_raw_body(self) -> bytes | None:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def _auth_ok(self) -> bool:
        return True

    def _query_str(self, key: str) -> str:
        return ""


def test_route_forwards_loopback_call_to_node() -> None:
    """回环调用方 → 挂进队列 → 节点回执 → 原样回给调用方（状态码与体都不改）。"""
    from hub import relay as relay_mod  # route 读的就是这个进程级单例

    def node() -> None:
        req = relay_mod.RELAY.poll("n1", wait_sec=2.0)
        assert req is not None
        relay_mod.RELAY.reply(
            "n1", str(req["rid"]), 200, {"Content-Type": "application/json"}, b'{"ok":1}'
        )

    hub = _RouteStub()
    t = threading.Thread(target=node, daemon=True)
    t.start()
    hub._relay_route(method="GET")
    t.join(3.0)
    assert hub.sent["status"] == 200
    assert hub.sent["ctype"] == "application/json"
    assert hub.out.getvalue() == b'{"ok":1}'
    assert relay_mod.RELAY.stats()["n1"]["served"] >= 1


def test_route_rejects_non_loopback_callers() -> None:
    """公网/局域网过来的调用方/瞎写的调用方一律 403 —— relay 不能变成通用代理。"""
    hub = _RouteStub(peer="100.64.0.9")
    hub._relay_route(method="GET")
    assert hub.sent["status"] == 403

    # cloudflared 回源的对端也是 127.0.0.1：靠归因头区分，不能只看 peer
    hub = _RouteStub(peer="127.0.0.1", cf="203.0.113.7")
    hub._relay_route(method="GET")
    assert hub.sent["status"] == 403


@pytest.mark.parametrize(
    ("headers", "want"),
    [
        ({"Content-Type": "application/json"}, "application/json"),
        ({"content-type": "text/plain; charset=utf-8"}, "text/plain; charset=utf-8"),
        ({}, "application/octet-stream"),
        ({"Content-Type": ""}, "application/octet-stream"),
    ],
)
def test_content_type_of(headers: dict[str, str], want: str) -> None:
    assert content_type_of(headers) == want
