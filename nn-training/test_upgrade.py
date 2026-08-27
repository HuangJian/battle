"""test_upgrade.py — dist_common 主动升级机制（M8）单元测试。

编排层 ping 发现 agent codeHash stale → POST /v1/restart 指示 git pull + 重启。
用真实本地 HTTP mock agent 验证：
  1) stale 节点被指示升级（pullBranch 正确、auth 头正确）；
  2) codeHash 一致的节点不被重启；
  3) 不可达节点优雅跳过（不崩、不阻塞）；
  4) request_upgrade 失败（非 200/202）返回 False。

运行（经统一启动器）：-Script test_upgrade.py；全过 0，否则 1。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "nn-training"))

import dist_common  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


class MockAgent:
    """可配置的 mock agent：codeHash 按构造参数区分（stale/current 行为）。"""

    def __init__(self, code_hash: str = "stale-hash-abc") -> None:
        self.code_hash = code_hash
        self.restart_calls: list[dict] = []
        self._handler_cls = self._make_handler()
        self.server = HTTPServer(("127.0.0.1", 0), self._handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _make_handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静音
                pass

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/v1/ping":
                    self._send_json({
                        "agentVersion": "test",
                        "codeHash": mock.code_hash,
                        "cpus": 4,
                        "bunVersion": "1.4.0",
                        "evalSupport": True,
                    })
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                u = urlparse(self.path)
                if u.path == "/v1/restart":
                    ln = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(ln).decode("utf-8") if ln else "{}"
                    mock.restart_calls.append({
                        "auth": self.headers.get("Authorization", ""),
                        "body": json.loads(body),
                    })
                    self._send_json({"ok": True})
                else:
                    self.send_response(404)
                    self.end_headers()

            def _send_json(self, obj):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return H

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def make_cfg(mock_url: str, enabled: bool = True) -> dict:
    return {"nodes": [
        {"id": "stale", "url": mock_url, "authKey": "KEY_STALE", "enabled": enabled},
        # current 节点：单独 mock（codeHash 匹配 expected）——用第二个 MockAgent。
    ]}


def test_request_upgrade() -> None:
    mock = MockAgent()
    try:
        ok = dist_common.request_upgrade(mock.url(), "KEY_X", "intent-ai")
        check(ok, "request_upgrade stale node → True")
        check(len(mock.restart_calls) == 1, "POST /v1/restart 恰好一次")
        if mock.restart_calls:
            c = mock.restart_calls[0]
            check(c["body"].get("pullBranch") == "intent-ai",
                  f"pullBranch=intent-ai, got {c['body']!r}")
            check(c["auth"] == "Bearer KEY_X", "Authorization: Bearer KEY_X")
        # 非 200/202 → False。
        ok2 = dist_common.request_upgrade(f"http://127.0.0.1:{mock.port + 1}", "K", "x",
                                          timeout=1.0)
        check(ok2 is False, "request_upgrade 失败节点 → False（不抛）")
    finally:
        mock.close()


def test_upgrade_stale_nodes() -> None:
    EXPECTED = "current-hash-xyz"
    stale = MockAgent(code_hash="stale-hash-abc")  # ≠ expected → 应升级
    current = MockAgent(code_hash=EXPECTED)  # == expected → 不升级
    cfg = {
        "nodes": [
            {"id": "stale", "url": stale.url(), "authKey": "K1", "enabled": True},
            {"id": "current", "url": current.url(), "authKey": "K2", "enabled": True},
            {"id": "down", "url": "http://127.0.0.1:1", "authKey": "K3", "enabled": True},
            {"id": "disabled", "url": stale.url(), "authKey": "K4", "enabled": False},
        ]
    }
    try:
        res = dist_common.upgrade_stale_nodes(cfg, expected_hash=EXPECTED,
                                              branch="intent-ai", status_timeout=1.0,
                                              restart_timeout=3.0)
        by_id = {r["id"]: r for r in res}
        # stale 节点（codeHash 与 expected 不同）→ 被升级。
        check(by_id["stale"]["upgraded"] is True, "stale 节点 → 指示升级")
        check(by_id["stale"]["reason"] == "stale", f"stale reason, got {by_id['stale']['reason']}")
        # current 节点（codeHash == expected）→ 不升级。
        check(by_id["current"]["upgraded"] is False
              and by_id["current"]["reason"] == "current", "current 节点不升级")
        # 不可达节点 → 跳过。
        check(by_id["down"]["upgraded"] is False
              and by_id["down"]["reason"] == "unreachable", "不可达节点优雅跳过")
        # disabled 节点不参与。
        check("disabled" not in by_id, "disabled 节点不参与")
        # stale 收到 /v1/restart；current 未收到。
        check(len(stale.restart_calls) == 1, f"stale 收到 1 次 restart，got {len(stale.restart_calls)}")
        check(len(current.restart_calls) == 0, "current 未收到 restart")
    finally:
        stale.close()
        current.close()


def main() -> None:
    print("== test_upgrade ==")
    test_request_upgrade()
    test_upgrade_stale_nodes()
    print(f"== {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failures) ==")
    for m in FAILS:
        print("  -", m)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
