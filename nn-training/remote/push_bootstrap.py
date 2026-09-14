"""remote/push_bootstrap.py — push-first 引导（无 hub GET /code 时的最小服务）。

为什么单独成文件：notebook cell 只留「参数 + 保活 + 薄分支」；能进 code.zip 的
一律进 code.zip。但 **首个 job 之前还没有 code.zip**，故本模块两种形态：

  1. 仓库/测试：直接 import（完整实现）。
  2. Kaggle push-first：notebook 内联一份**压缩** bootstrap（仅升级 HTTP + cloudflared），
     解包首个 job 的 code_b64 后 sys.path 注入并调用 `run_push_after_upgrade`。

完整 worker_server / 守候日志 / 设备探测仍在 `remote/notebook_runtime.py`。
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def ensure_cloudflared(log: Callable[[str], None], cfg_path: str = "") -> str:
    """查找或安装 cloudflared；返回可执行路径。失败抛 RuntimeError。"""
    cf = cfg_path or subprocess.getoutput(
        "where cloudflared 2>nul || which cloudflared 2>/dev/null"
    ).strip()
    if cf:
        return cf
    log("cloudflared 未找到，自动安装…")
    target = "/usr/local/bin/cloudflared"
    try:
        subprocess.run(
            [
                "curl",
                "-fsSL",
                "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                "-o",
                target,
            ],
            check=True,
            timeout=60,
        )
        os.chmod(target, 0o755)
        log(f"cloudflared 已安装 -> {target}")
        return target
    except Exception as e:
        raise RuntimeError(f"cloudflared 自动安装失败: {e}") from e


def start_cloudflared(
    cf_bin: str, port: int, log_path: Path, timeout_s: int = 60
) -> tuple[subprocess.Popen | None, str | None]:
    """启动 quick tunnel；返回 (proc, url|None)。"""
    import re

    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [cf_bin, "tunnel", "--url", f"http://localhost:{port}", "--logfile", str(log_path)],
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    t0 = time.time()
    url = None
    while time.time() - t0 < timeout_s:
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
            urls = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
            if urls:
                url = urls[-1]
                break
        except OSError:
            pass
        if proc.poll() is not None:
            break
        time.sleep(2)
    return proc, url


class _BootHandler(BaseHTTPRequestHandler):
    """stdlib 升级服务：/ping、/code-sha=cached:false、POST /job 带 code_b64 则升级。"""

    # 由 serve_bootstrap 注入
    token: str = ""
    code_dir: Path = Path("/tmp/worker-code")
    on_upgrade: Callable[[bytes], None] | None = None

    def log_message(self, *args: object) -> None:
        pass

    def _json(self, obj: dict, status: int = 200) -> None:
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self) -> bool:
        got = self.headers.get("Authorization", "")
        return secrets.compare_digest(got, f"Bearer {self.token}")

    def do_GET(self) -> None:
        if not self._auth():
            return self._json({"error": "unauthorized"}, 401)
        path = self.path.split("?", 1)[0]
        if path == "/ping":
            return self._json(
                {"ok": True, "busy": False, "queued": 0, "done": 0, "bootstrap": True}
            )
        if path == "/code-sha":
            return self._json({"cached": False})
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._auth():
            return self._json({"error": "unauthorized"}, 401)
        if self.path.split("?", 1)[0] != "/job":
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        if not body.get("code_b64"):
            return self._json({"error": "code-missing", "hint": "bootstrap 需要 code_b64"}, 428)
        try:
            zbytes = base64.b64decode(body["code_b64"])
            self.code_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
                z.extractall(self.code_dir)
        except Exception as e:
            return self._json({"error": f"code unzip failed: {e}"}, 400)
        if self.on_upgrade:
            self.on_upgrade(raw)
        return self._json({"status": "accepted", "upgrading": True}, 202)


def start_bootstrap_server(
    port: int,
    token: str,
    code_dir: Path,
    on_upgrade: Callable[[bytes], None],
) -> ThreadingHTTPServer:
    h = type(
        "H",
        (_BootHandler,),
        {"token": token, "code_dir": code_dir, "on_upgrade": staticmethod(on_upgrade)},
    )
    srv = ThreadingHTTPServer(("0.0.0.0", port), h)
    return srv


def spawn_full_worker_server(
    port: int,
    token: str,
    work_dir: Path,
    device: str,
    code_dir: Path,
    log_path: Path,
) -> subprocess.Popen:
    """在已解包的 code_dir 上起完整 remote_worker_serve。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(code_dir) + os.pathsep + env.get("PYTHONPATH", "")
    log_f = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "remote_worker_serve",
            "--port",
            str(port),
            "--token",
            token,
            "--work",
            str(work_dir),
            "--device",
            device,
        ],
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
    )


def wait_ping(port: int, token: str, timeout_s: float = 30.0) -> bool:
    from urllib.request import Request, urlopen

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            req = Request(
                f"http://127.0.0.1:{port}/ping",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(req, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def requeue_job(port: int, token: str, raw_body: bytes) -> None:
    from urllib.request import Request, urlopen

    req = Request(
        f"http://127.0.0.1:{port}/job",
        data=raw_body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as r:
            print(
                f"[{time.strftime('%H:%M:%S')}] [battle-rl] 首个 job 已交给 worker_server (HTTP {r.status})",
                flush=True,
            )
    except Exception as e:
        print(
            f"[{time.strftime('%H:%M:%S')}] [battle-rl] job 重放失败: {e}——hub 会重试",
            flush=True,
        )


def run_push_after_upgrade(
    cfg: dict[str, Any],
    log: Callable[[str], None],
    *,
    serve_proc: subprocess.Popen,
    cf_proc: subprocess.Popen | None,
    cf_url: str | None,
    code_dir: Path,
) -> int:
    """升级完成后：注入 code 路径 → notebook_runtime 守候循环（不重复起隧道）。"""
    sys.path.insert(0, str(code_dir))
    from remote.notebook_runtime import run_notebook

    cfg = dict(cfg)
    cfg["code_dir"] = str(code_dir)
    cfg["already_serving"] = {
        "serve_pid": serve_proc.pid,
        "cf_pid": cf_proc.pid if cf_proc else None,
        "cf_url": cf_url,
    }
    cfg["log"] = log
    return int(run_notebook(cfg) or 0)


def run_push_first(cfg: dict[str, Any], log: Callable[[str], None]) -> int:
    """完整 push-first：bootstrap HTTP + cloudflared → 升级 → 守候。"""
    port = int(cfg["push_port"])
    token = str(cfg["push_token"])
    if not token or token == "YOUR_TOKEN_HERE":
        log("FATAL: push_token 必填")
        return -2
    work = Path(str(cfg.get("work_dir") or "/tmp/remote-worker-serve"))
    work.mkdir(parents=True, exist_ok=True)
    code_dir = Path(str(cfg.get("code_dir") or "/tmp/worker-code"))
    upgrade: dict[str, bytes | None] = {"body": None}
    srv = start_bootstrap_server(
        port, token, code_dir, on_upgrade=lambda raw: upgrade.__setitem__("body", raw)
    )
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"push bootstrap 就绪 :{port}（无 hub /code 依赖）")
    cf_bin = ensure_cloudflared(log, str(cfg.get("cloudflared_path") or ""))
    cf_proc, cf_url = start_cloudflared(cf_bin, port, work / "cloudflared.log")
    if cf_url:
        log(f"★ 隧道 URL: {cf_url}")
        log("填到控制台 Push（auth key=push_token）或 rl-config gpu_push；等首个 job…")
    else:
        log(f"⚠ 隧道 URL 未取得——查 {work / 'cloudflared.log'}")
    deadline = time.time() + int(cfg.get("max_session_hours", 9)) * 3600
    try:
        while upgrade["body"] is None:
            if time.time() > deadline:
                log("会话到顶——未收到 job")
                return 0
            time.sleep(2)
    except KeyboardInterrupt:
        log("收到中断")
        return 0
    finally:
        srv.shutdown()
        time.sleep(0.5)
    device = str(cfg.get("device_resolved") or cfg.get("device") or "cpu")
    real = spawn_full_worker_server(
        port, token, work, device, code_dir, work / "serve.log"
    )
    if not wait_ping(port, token, 30):
        log("完整 worker_server 30s 未就绪")
        real.kill()
        return -1
    log("完整 worker_server 就绪——重放首个 job")
    requeue_job(port, token, upgrade["body"] or b"{}")
    return run_push_after_upgrade(
        cfg, log, serve_proc=real, cf_proc=cf_proc, cf_url=cf_url, code_dir=code_dir
    )
