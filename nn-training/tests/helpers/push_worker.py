"""推腿用例共用的假 GPU worker server（worker_server 三件套契约，不跑真实运算）。

放在 `tests/helpers/` 而不是留在某个测试文件里：`worker_factory` 夹具住在
`tests/conftest.py`，任何用例都能直接用参数拿到它——**不用**跨测试模块
`from tests.test_x import fixture_name`（那条路会让 ruff 把夹具名判成
`F811 Redefinition of unused name`，因为同名参数遮蔽了模块级 import）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common.protocol import WIRE_JOB_MAGIC, normalize_manifest, unpack_job_v2

PAYLOAD = b"PK\x03\x04fake-payload-bytes"


def result_of(manifest: dict, *, tamper: bool = False) -> dict:
    """假 worker 的「PPO 结果」：形状照 `validate_result` 的契约（agg 五键齐全）。"""
    return {
        "job_id": manifest["job_id"],
        "data_fp": "X" * 64 if tamper else manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "commit_echo": manifest["commit"],
        "weights_json": base64.b64encode(b"fake-weights-json").decode("ascii"),
        "agg": {"policy": 0.1, "value": 0.2, "entropy": 3.0, "kl": 0.01, "mean_ret": 0.5},
        "wire": {"payload_bytes": len(PAYLOAD)},
    }


class FakeWorker:
    """假 GPU worker server（worker_server 的三件套契约，不跑真实运算）。

    可调旋钮：`busy`（/ping 自报在忙）、`fail_ping`（探活失败）、`refuse`（/job 回 409）、
    `complete`（收下就出结果 / 永远 202 吊着）、`tamper`（出一份对不上账的结果）。
    """

    def __init__(
        self,
        *,
        busy: bool = False,
        fail_ping: bool = False,
        refuse: bool = False,
        complete: bool = True,
        tamper: bool = False,
    ) -> None:
        self.busy = busy
        self.fail_ping = fail_ping
        self.refuse = refuse
        self.complete = complete
        self.tamper = tamper
        self.received: list[str] = []
        self.results: dict[str, dict] = {}
        self._manifests: dict[str, dict] = {}
        self._lock = threading.Lock()
        srv = self._build()
        self.srv = srv
        self.url = f"http://127.0.0.1:{srv.server_address[1]}"
        self.thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)

    def _build(self) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # 静默
                pass

            def _json(self, obj: dict, status: int = 200) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/ping":
                    if outer.fail_ping:
                        self._json({"error": "down"}, 500)
                        return
                    self._json(
                        {
                            "ok": True,
                            "busy": outer.busy,
                            "queued": 0,
                            "done": len(outer.results),
                        }
                    )
                    return
                if path.startswith("/code-sha") or path.startswith("/ts-code-sha"):
                    # 缓存命中：让 push_client 不必随 body 传 code/TS 运行时（测试里没有那些字节）。
                    self._json({"cached": True})
                    return
                if path.startswith("/blob-sha"):
                    self._json({"cached": True})
                    return
                if path.startswith("/job/") and path.endswith("/result"):
                    jid = path[len("/job/") : -len("/result")]
                    with outer._lock:
                        res = outer.results.get(jid)
                        known = jid in outer._manifests
                    if res is not None:
                        self._json(res)
                    elif known:
                        self._json({"status": "running"}, 202)
                    else:
                        self._json({"error": "unknown job"}, 404)
                    return
                if path.startswith("/job/") and path.endswith("/status"):
                    jid = path[len("/job/") : -len("/status")]
                    with outer._lock:
                        state = "done" if jid in outer.results else "running"
                    self._json({"state": state})
                    return
                self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0]
                if path != "/job":
                    self._json({"error": "not found"}, 404)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if outer.refuse:
                    self._json({"error": "busy"}, 409)
                    return
                if raw.startswith(WIRE_JOB_MAGIC):
                    body = unpack_job_v2(raw)
                else:
                    body = json.loads(raw.decode("utf-8"))
                manifest = normalize_manifest(body["manifest"])
                jid = manifest["job_id"]
                payload = base64.b64decode(body["payload_b64"])
                # 真契约校验：body 里的 payload 必须与 manifest 对账（假 worker 也照查，
                # 否则「hub 推了一份坏 body」这类回归会被假 worker 放过去）。
                if hashlib.sha256(payload).hexdigest() != manifest["payload_sha256"]:
                    self._json({"error": "payload_sha256 不匹配"}, 400)
                    return
                with outer._lock:
                    outer._manifests[jid] = manifest
                    outer.received.append(jid)
                    if outer.complete:
                        outer.results[jid] = result_of(manifest, tamper=outer.tamper)
                self._json({"job_id": jid, "status": "accepted"}, 202)

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
