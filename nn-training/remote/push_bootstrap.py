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


def close_bootstrap_server(srv: ThreadingHTTPServer) -> None:
    """关掉引导服务并**释放监听端口**（升级到完整 worker_server 前的必经一步）。

    为什么必须 `server_close()`（2026-09-15 Colab push-first 全链路卡死事故）：
    `socketserver.BaseServer.shutdown()` 只让 `serve_forever()` 退出循环，
    **不关监听套接字**——那是 `server_close()` 的职责（它还会把 `self.socket` 置 None）。
    而 push-first 的设计是引导服务与升级后的完整 worker_server **共用同一个
    `push_port`**：`run_push_first` 先 bind 该端口，收到首个 job 后再起
    `remote_worker_serve` 绑**同一个端口**。只 `shutdown()` 不 `server_close()` ⇒
    父进程一直占着端口 ⇒ 子进程被 `remote/_port_guard.py` 拒绝
    （"端口 ... 已被占用——拒绝启动（禁止双监听）"）⇒ `wait_ping` 30s 超时 ⇒
    `SystemExit: -1` ⇒ cell 挂、隧道下线 ⇒ 控制台侧只看到「推送成功但状态查询 530」，
    而真因藏在 `<work>/serve.log` 里，极难定位。
    本仓库其它处（`tests/test_upgrade.py`、`tests/test_port_guard.py`、
    `e2e/test_push_mode_integration.py`）都是 shutdown+server_close 成对写，此处曾遗漏。
    """
    srv.shutdown()
    srv.server_close()


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


def tail_text_lines(path: Path, n: int = 40) -> list[str]:
    """读文件末尾 n 行；缺失/不可读则返回一行说明（**不抛异常**）。

    为什么需要（2026-09-15 Colab 事故复盘）：`spawn_full_worker_server` 把子进程的
    stdout/stderr 重定向到 `<work>/serve.log`，因此 30s 就绪检查失败时 cell 里
    **只有干巴巴一句「未就绪」**，真因（当时是 `端口 ... 已被占用——拒绝启动`）
    全藏在那个文件里，操作员得自己知道去 `!cat` 它。把尾部直接摊到 cell 输出里，
    失败就自解释。
    """
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [f"(无法读取 {path}：{e})"]
    lines = data.splitlines()
    if len(lines) > n:
        return [f"...(以上略去前 {len(lines) - n} 行)", *lines[-n:]]
    return lines


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
        # 必须连监听套接字一起释放：随后完整 worker_server 要 bind **同一个端口**
        # （见 close_bootstrap_server docstring 的 2026-09-15 事故）。
        close_bootstrap_server(srv)
        time.sleep(0.5)
    device = str(cfg.get("device_resolved") or cfg.get("device") or "cpu")
    real = spawn_full_worker_server(
        port, token, work, device, code_dir, work / "serve.log"
    )
    if not wait_ping(port, token, 30):
        # 失败必须自解释：真因在子进程的 stdout/stderr 里（被重定向到 serve.log），
        # 不摊出来操作员在 cell 里什么都看不到（2026-09-15 事故）。
        log("完整 worker_server 30s 未就绪——下面是 serve.log 尾部（真因通常在这）：")
        for line in tail_text_lines(work / "serve.log", 40):
            log(f"  | {line}")
        real.kill()
        return -1
    log("完整 worker_server 就绪——重放首个 job")
    requeue_job(port, token, upgrade["body"] or b"{}")
    return run_push_after_upgrade(
        cfg, log, serve_proc=real, cf_proc=cf_proc, cf_url=cf_url, code_dir=code_dir
    )
