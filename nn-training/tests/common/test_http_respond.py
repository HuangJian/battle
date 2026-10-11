"""`common/http_respond.py` 的**行为**回归（plan/nn-training-reuse-and-deps.plan.md §S2 第三刀）。

三件事各有理由：

1. **线形状逐字节钉死**——头部顺序 / `Content-Length` 按**字节**算 / 非 ASCII 不转义：这三条
   漂一格就是跨端解不开（hub 发得出、节点解不开最贵）。
2. **真 socket 往返**——装配顺序（头必须先于 `end_headers`）只有走真
   `BaseHTTPRequestHandler` 路径才被证明；同形替身录不到这类错。
3. **hub 侧是同一实现**（不是又一份拷贝）——同名 ≠ 同物：这里比的是**录到的字节流相等**。
   worker 侧的两处（`remote/worker_server` 里的 Handler / 假 worker）是同名嵌套类，
   由既有 push 腿用例覆盖，本文件不重复造。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any, cast
from urllib.request import urlopen

from common.http_respond import respond_json
from hub.http_face import HubHandler


class _Recorder:
    """最小同形 handler：照 `BaseHTTPRequestHandler` 的四件套录流。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.wfile = BytesIO()

    def send_response(self, code: int, message: str | None = None) -> None:
        self.sent.append(("response", code))

    def send_header(self, keyword: str, value: str) -> None:
        self.sent.append(("header", (keyword, value)))

    def end_headers(self) -> None:
        self.sent.append(("end_headers", None))

    def headers(self) -> dict[str, str]:
        return dict(v for k, v in self.sent if k == "header")


def test_respond_json_pins_the_wire_shape() -> None:
    rec = _Recorder()
    respond_json(rec, {"ok": True}, 201)
    expected = b'{"ok": true}'
    assert rec.sent == [
        ("response", 201),
        ("header", ("Content-Type", "application/json")),
        ("header", ("Content-Length", str(len(expected)))),
        ("end_headers", None),
    ]
    assert rec.wfile.getvalue() == expected


def test_respond_json_keeps_chinese_and_counts_bytes() -> None:
    """非 ASCII 原样出线；长度按**字节**算（按字符算会让对端少读/多读）。"""
    rec = _Recorder()
    obj = {"error": "请求体越界（0..4096）"}
    respond_json(rec, obj)
    raw = rec.wfile.getvalue()
    assert raw.decode("utf-8") == json.dumps(obj, ensure_ascii=False)
    assert b"\\u" not in raw
    # 转义形态（`ensure_ascii=True` 的默认）在字节上更长 ⇒ 拿它当反例
    escaped = json.dumps(obj).encode("utf-8")
    assert len(raw) < len(escaped)
    assert rec.headers()["Content-Length"] == str(len(raw))


def test_respond_json_over_a_real_socket() -> None:
    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            respond_json(self, {"msg": "中文", "n": 1}, 201)

        def log_message(self, *args: object) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urlopen(f"http://127.0.0.1:{srv.server_port}/x", timeout=5) as resp:
            assert resp.status == 201
            assert resp.headers["Content-Type"] == "application/json"
            assert resp.headers["Content-Length"] == str(resp.length)
            assert json.loads(resp.read().decode("utf-8")) == {"msg": "中文", "n": 1}
    finally:
        srv.shutdown()
        srv.server_close()


def test_hub_side_sends_through_the_shared_responder() -> None:
    """`HubHandler._json` 与 `common.http_respond.respond_json` 录出的字节流必须相等。"""
    mine, hub = _Recorder(), _Recorder()
    respond_json(mine, {"error": "unauthorized", "中文": True}, 401)
    HubHandler._json(cast("HubHandler", hub), {"error": "unauthorized", "中文": True}, 401)
    assert hub.sent == mine.sent
    assert hub.wfile.getvalue() == mine.wfile.getvalue()
