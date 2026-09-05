"""remote/worker_server.py — push 模式 GPU 侧服务端（2026-09-05，DECISIONS §340）。

HUB 推架构（方向翻转）：cloudflared 跑在 GPU 机器上，HUB 作为出站客户端把 PPO
job（manifest + payload.zip + code.zip）POST 到本服务端；本机后台跑 run_job 全套
（sha 校验 → 解包 → D14 → PPO → 结果），HUB 轮询取结果。弱链路（edge↔cloudflared）
落在 Kaggle 网络，HUB 只做出站 HTTPS——不再依赖 HUB 侧隧道。

端点（全部 Bearer token 鉴权）：
  GET  /ping                 存活 + 忙闲
  GET  /code-sha?sha=X       本机 code 缓存是否已有 sha=X 的代码（HUB 决定是否随 job 上传）
  POST /job                  {manifest, payload_b64, code_b64?} → 202 受理（后台 PPO）
                             409 busy（单 GPU 串行）| 428 code-missing（请重传带 code）| 400 校验失败
  GET  /job/{id}/status      {"state": "running" | "done" | "failed" | "unknown"}
  GET  /job/{id}/result      200 结果 JSON（幂等可重复读）| 202 在跑 | 500 失败 | 404 未知

运行（GPU 机器，venv 含 torch）：
  python -m remote_worker_serve --port 8790 --token <token>
  cloudflared tunnel --url http://localhost:8790
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from remote.protocol import ProtocolError, normalize_manifest
from remote.worker import run_job

AUTH_HEADER = "Authorization"


class WorkerServerState:
    """job 状态表（单 GPU 串行：同一时刻至多一个在跑）。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.jobs: dict[str, dict] = {}  # jid -> {"state", "result"?, "error"?}
        self.done_total = 0

    def code_cached(self, code_sha256: str) -> bool:
        return (self.work_dir / "code_cache" / code_sha256).exists()

    def set_state(self, jid: str, state: str) -> None:
        with self._lock:
            self.jobs[jid] = {"state": state}

    def set_result(self, jid: str, result: dict) -> None:
        with self._lock:
            self.jobs[jid] = {"state": "done", "result": result}
            self.done_total += 1

    def set_error(self, jid: str, error: str) -> None:
        with self._lock:
            prev = self.jobs.get(jid, {})
            self.jobs[jid] = {"state": "failed", "error": error, **{k: v for k, v in prev.items() if k == "result"}}

    def get(self, jid: str) -> dict | None:
        with self._lock:
            return self.jobs.get(jid)

    def busy(self) -> bool:
        with self._lock:
            return any(j["state"] == "running" for j in self.jobs.values())


def _execute_job(state: WorkerServerState, manifest: dict, payload_zip: bytes, code_zip: bytes | None,
                 work_dir: Path, device: str, torch_threads: int, echo: bool,
                 log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker-serve] {msg}", flush=True)) -> None:
    """后台执行：run_job 全套（preloaded push 路径）→ 状态落表。异常进 failed（HUB 可见）。"""
    jid = manifest["job_id"]
    state.set_state(jid, "running")
    try:
        result = run_job(
            "", "", {"job_id": jid, "manifest": manifest},
            work_dir=work_dir, device=device, torch_threads=torch_threads,
            echo=echo, preloaded={"payload_zip": payload_zip, "code_zip": code_zip},
            log=log,
        )
        state.set_result(jid, result)
        log(f"job {jid} done — result ready for pickup")
    except Exception as e:
        state.set_error(jid, f"{type(e).__name__}: {e}")
        log(f"job {jid} FAILED: {e}")


def make_worker_server(
    state: WorkerServerState,
    port: int,
    token: str,
    *,
    device: str = "cpu",
    torch_threads: int = 0,
    host: str = "0.0.0.0",
) -> ThreadingHTTPServer:
    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] [worker-serve] {msg}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def _auth_ok(self) -> bool:
            auth = self.headers.get(AUTH_HEADER, "")
            return secrets.compare_digest(auth, f"Bearer {token}")

        def _json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志
            pass

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if not self._auth_ok():
                    self._json({"error": "unauthorized"}, 401)
                    return
                if path == "/ping":
                    self._json({"ok": True, "pid": os.getpid(), "busy": state.busy(), "done": state.done_total})
                elif path == "/code-sha":
                    qs = parse_qs(urlparse(self.path).query)
                    sha = (qs.get("sha") or [""])[0]
                    self._json({"sha": sha, "cached": state.code_cached(sha)})
                elif path.startswith("/job/") and path.endswith("/status"):
                    jid = path[len("/job/"):-len("/status")]
                    rec = state.get(jid)
                    self._json({"state": rec["state"] if rec else "unknown"})
                elif path.startswith("/job/") and path.endswith("/result"):
                    jid = path[len("/job/"):-len("/result")]
                    rec = state.get(jid)
                    if rec is None:
                        self._json({"error": "unknown job"}, 404)
                    elif rec["state"] == "done":
                        self._json(rec["result"])
                    elif rec["state"] == "failed":
                        self._json({"error": rec.get("error", "?")}, 500)
                    else:
                        self._json({"status": "running"}, 202)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # 单请求不崩服务
                log(f"GET {path} ERROR: {e}")
                self._json({"error": "internal"}, 500)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if not self._auth_ok():
                    self._json({"error": "unauthorized"}, 401)
                    return
                if path != "/job":
                    self._json({"error": "not found"}, 404)
                    return
                if state.busy():
                    self._json({"error": "busy — 单 GPU 串行，稍后重试"}, 409)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw.decode("utf-8"))
                manifest = normalize_manifest(body["manifest"])
                jid = manifest["job_id"]
                payload_zip = base64.b64decode(body["payload_b64"])
                if hashlib.sha256(payload_zip).hexdigest() != manifest["payload_sha256"]:
                    self._json({"error": "payload_sha256 不匹配——上传损坏（重传）"}, 400)
                    return
                code_b64 = body.get("code_b64")
                code_zip = base64.b64decode(code_b64) if code_b64 else None
                if code_zip is None and not state.code_cached(manifest["code_sha256"]):
                    self._json({"error": "code-missing", "code_sha256": manifest["code_sha256"]}, 428)
                    return
                echo = self.headers.get("X-Smoke-Echo", "") == "1"
                state.set_state(jid, "running")
                threading.Thread(
                    target=_execute_job,
                    args=(state, manifest, payload_zip, code_zip, state.work_dir, device, torch_threads, echo, log),
                    daemon=True,
                    name=f"job-{jid[:8]}",
                ).start()
                log(f"job {jid} accepted（echo={echo}）— PPO 后台执行")
                self._json({"job_id": jid, "status": "accepted"}, 202)
            except (ProtocolError, ValueError, KeyError) as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                log(f"POST {path} ERROR: {e}")
                self._json({"error": "internal"}, 500)

    return ThreadingHTTPServer((host, port), Handler)


def serve_forever(port: int, token: str, work_dir: Path, *, device: str = "cpu",
                  torch_threads: int = 0) -> None:
    """阻塞运行（调用方负责进程生命周期）；token 缺失响亮报错。"""
    if not token:
        raise SystemExit("[worker-serve] ERROR: 需要 --token（与 HUB 共享密钥）")
    state = WorkerServerState(work_dir)
    srv = make_worker_server(state, port, token, device=device, torch_threads=torch_threads)
    print(f"[{time.strftime('%H:%M:%S')}] [worker-serve] listening on 0.0.0.0:{port} "
          f"work={state.work_dir} pid={os.getpid()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
