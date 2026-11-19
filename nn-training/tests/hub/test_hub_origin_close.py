"""test_hub_origin_close — hub 的 origin 面**必须**每条响应后关连接（别升 HTTP/1.1）。

2026-11-19 事故：`HubHandler.protocol_version = "HTTP/1.1"` 上线后，cloudflared 复用它的
origin 连接；只要有一条响应被截断/半写，那条连接就永久失同步（现场日志：
`malformed HTTP status code "HTML>"`），此后 20 分钟 relay 的请求一条都不再到达 hub。
1.0 的「响应完就关」是这条失同步的天然兜底 —— 这条用例把它钉住：

- 响应仍带 `Content-Length`（定长是任何协议下都必须的品质）；
- 读完响应后 `conn.sock is None`（服务端主动关）且 `resp.will_close` 为真。

改回 1.1 时第一条断言红；§165 有完整的因果链。
"""

from __future__ import annotations

import http.client
import threading
from pathlib import Path

from hub.boot import make_server
from hub.store import _JobStore


def test_origin_closes_after_each_response(tmp_path: Path) -> None:
    store = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    srv = make_server(store, 0, "sekret", host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", int(srv.server_address[1]), timeout=5.0)
    try:
        conn.request("GET", "/ping", headers={"Authorization": "Bearer sekret"})
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200, body
        assert resp.getheader("Content-Length") == str(len(body))
        assert resp.will_close, "origin 说了长连接 ⇒ 一条坏响应就能毒死隧道（§165）"
        assert conn.sock is None, "响应后连接没关 ⇒ origin 面在复用"
    finally:
        conn.close()
        srv.shutdown()
        srv.server_close()
