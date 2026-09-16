"""remote/notebook_boot.py — Colab/Kaggle cell 的运行时（除 CFG / 凭据 / 保活外的全部逻辑）。

为什么存在：cell 里那段「拉 code.zip / push 引导 / BC 壳」本来必须内联（**首包前拿不到
code.zip**）。但 2026-09-16 起 cell 会先从 GitHub raw 拉本文件（和同目录的
`tailscale_boot.py`），于是这些逻辑也能住进仓库——改它们不再需要重发 notebook。

拉不到远端时，cell 走自己的精简回退（Tailscale + pull），push/bc 会明确报错。

调用契约（cell 侧）：
    import notebook_boot
    raise SystemExit(notebook_boot.run(CFG, _log, _secret, _keepalive_stop))
其中 `secret(key, cfg_val)` 由 cell 提供（环境变量 → Colab/Kaggle Secrets → CFG 手填）。
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _load_tailscale_boot() -> Any:
    """随 code.zip 下发时是包内模块；从 GitHub raw 拉到 /tmp/battle-boot 时是顶层模块。"""
    for _name in ("remote.tailscale_boot", "tailscale_boot"):
        try:
            return importlib.import_module(_name)
        except ImportError:
            continue
    raise ImportError("找不到 tailscale_boot（远端引导模块拉取失败？）")


tailscale_boot = _load_tailscale_boot()

CODE_DIR = "/tmp/worker-code"


def _build_opener() -> urllib.request.OpenerDirector:
    """显式 ProxyHandler —— Colab 的 urllib.request.urlopen() 不读 HTTP_PROXY 环境变量（2026-09-16 实测），
    curl 读所以诊断显示 200，但 Python 侧 timed out。必须手动建 opener。"""
    proxies: dict[str, str] = {}
    for k in ("http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["http"] = v
            break
    for k in ("https_proxy", "HTTPS_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["https"] = v
            break
    if proxies:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


# ── Pull：GET /code → code.zip → 交给 remote.notebook_runtime ──────────────
def _pull(cfg: dict, log, secret, keepalive_stop, hub: str, hub_tok: str) -> int:
    opener = _build_opener()
    deadline = time.time() + 3600
    while True:
        try:
            req = urllib.request.Request(
                hub.rstrip("/") + "/code", headers={"Authorization": "Bearer " + hub_tok})
            with opener.open(req, timeout=120) as resp:
                raw = resp.read()
            log(f"code.zip 就绪: {len(raw)} bytes")
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"[FATAL] /code HTTP {e.code} — HUB_TOKEN 不一致") from None
            log(f"/code HTTP {e.code} —— 30s 后重试")
            time.sleep(30)
        except Exception as e:
            if time.time() > deadline:
                raise SystemExit("[FATAL] 等 code.zip 超 1h") from None
            log(f"hub 异常（{type(e).__name__}）—— 30s 重试")
            time.sleep(30)
    Path(CODE_DIR).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(CODE_DIR)
    sys.path.insert(0, CODE_DIR)
    log("移交 remote.notebook_runtime")
    from remote.notebook_runtime import run_notebook
    return run_notebook({
        "mode": cfg["rl_mode"], "hub_url": hub, "hub_token": hub_tok,
        "push_port": int(cfg["push_port"]), "push_token": secret("PUSH_TOKEN", cfg.get("push_token")),
        "device": cfg["device"], "use_multi_gpu": True,
        "max_session_hours": cfg["max_session_hours"], "poll_interval_sec": 1,
        "idle_floor_sec": 3600, "max_worker_restarts": 5,
        "keepalive_stop": keepalive_stop, "log": log, "code_dir": CODE_DIR,
    })


# ── Push-first：首包前无 code.zip，引导 HTTP 只能住这里 ─────────────────────
def _push(cfg: dict, log, secret, keepalive_stop, ip: str, tok: str) -> int:
    port = int(cfg["push_port"])
    work = Path("/tmp/remote-worker-serve")
    work.mkdir(parents=True, exist_ok=True)
    cdir = Path(CODE_DIR)
    upg: dict = {"body": None}

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _j(self, o, s=200):
            b = json.dumps(o).encode()
            self.send_response(s)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _ok(self):
            return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {tok}")

        def do_GET(self):
            if not self._ok():
                return self._j({"error": "unauthorized"}, 401)
            p = self.path.split("?", 1)[0]
            if p == "/ping":
                return self._j({"ok": True, "busy": False, "queued": 0, "done": 0, "bootstrap": True})
            if p == "/code-sha":
                return self._j({"cached": False})
            return self._j({"error": "nf"}, 404)

        def do_POST(self):
            if not self._ok():
                return self._j({"error": "unauthorized"}, 401)
            if self.path.split("?", 1)[0] != "/job":
                return self._j({"error": "nf"}, 404)
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                body = json.loads(raw.decode())
            except ValueError:
                return self._j({"error": "bad json"}, 400)
            if not body.get("code_b64"):
                return self._j({"error": "code-missing"}, 428)
            try:
                cdir.mkdir(parents=True, exist_ok=True)
                zipfile.ZipFile(io.BytesIO(base64.b64decode(body["code_b64"]))).extractall(cdir)
            except Exception as e:
                return self._j({"error": str(e)}, 400)
            upg["body"] = raw
            log(f"code.zip 已解包 -> {cdir}")
            return self._j({"status": "accepted", "upgrading": True}, 202)

    srv = ThreadingHTTPServer(("0.0.0.0", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"push bootstrap :{port}")
    log(f"★ rl-config 节点 url -> http://{ip}:{port}  (authKey=PUSH_TOKEN)")
    log("!! userspace 模式入站不通：push 需要 kernel 模式，Colab 上基本没戏——优先用 pull")
    deadline = time.time() + int(cfg["max_session_hours"]) * 3600
    try:
        while upg["body"] is None:
            if time.time() > deadline:
                raise SystemExit(0)
            time.sleep(2)
    except KeyboardInterrupt:
        log("中断")
        raise SystemExit(0) from None
    finally:
        # 必须 server_close()：shutdown() 不放监听套接字，完整 worker_server 随后要
        # bind 同一端口（2026-09-15 事故）。
        srv.shutdown()
        srv.server_close()
        time.sleep(0.5)

    sys.path.insert(0, CODE_DIR)
    from remote.notebook_runtime import resolve_device, run_notebook
    from remote.push_bootstrap import requeue_job, spawn_full_worker_server, wait_ping

    rl_cfg: dict = {
        "mode": "push", "push_port": port, "push_token": tok,
        "device": cfg["device"], "use_multi_gpu": True,
        "max_session_hours": cfg["max_session_hours"], "poll_interval_sec": 1,
        "idle_floor_sec": 3600, "max_worker_restarts": 5,
        "keepalive_stop": keepalive_stop, "log": log, "code_dir": CODE_DIR,
    }
    # 设备必须在 spawn 之前解析：spawn 传 --device，给 "auto" 会当场炸（2026-09-15 事故）
    rl_cfg["device_resolved"] = resolve_device(rl_cfg, log)
    real = spawn_full_worker_server(
        port, tok, work, str(rl_cfg["device_resolved"]), cdir, work / "serve.log")
    if not wait_ping(port, tok, 30):
        log("worker_server 30s 未就绪——serve.log 尾部:")
        try:
            lines = (work / "serve.log").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            lines = [f"(读 serve.log 失败: {e})"]
        for ln in lines[-40:]:
            log(f"  | {ln}")
        real.kill()
        raise SystemExit(-1)
    log("worker_server 就绪——重放首个 job 后守候")
    requeue_job(port, tok, upg["body"] or b"{}")
    rl_cfg["already_serving"] = {
        "serve_pid": real.pid,
        "cf_pid": None,                      # Tailscale：无 cloudflared
        "cf_url": f"http://{ip}:{port}",
    }
    return run_notebook(rl_cfg)


# ── BC：clone + 调 remote/colab_bc.py ──────────────────────────────────────
def _bc(cfg: dict, log) -> int:
    repo = Path("/content/battle2")
    branch = cfg["branch"]
    if shutil.which("git") and (repo / ".git").exists():
        log("git pull …")
        subprocess.run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", branch], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-B", branch, "FETCH_HEAD"], check=True)
    else:
        log("clone …")
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--branch", branch,
                        cfg["repo_url"], str(repo)], check=True)
    sys.path.insert(0, str(repo / "nn-training"))
    from remote.colab_bc import main as bc_main
    return bc_main([
        "--run-tag", cfg["bc_run_tag"], "--course", cfg["bc_course"],
        "--corpus-zip", cfg["bc_corpus_zip"], "--epochs", str(cfg["bc_epochs"]),
        "--seed", str(cfg["bc_seed"]), "--repo", str(repo),
    ])


def run(cfg: dict, log, secret, keepalive_stop) -> int:
    """cell 的唯一入口：起 Tailscale → 按 mode 分支。返回值交给 SystemExit。"""
    ts_cfg = {
        "ts_authkey": secret("TS_AUTHKEY", cfg.get("ts_authkey")),
        "ts_ephemeral": bool(cfg.get("ts_ephemeral", True)),
        "proxy_env": cfg.get("mode") == "rl",   # bc 要 git clone，别让它走 tailnet 代理
    }
    if not ts_cfg["ts_authkey"]:
        log("!! 未拿到 TS_AUTHKEY（环境变量 / Colab Secrets / CFG 三处都没有）")
    ts = tailscale_boot.ensure(ts_cfg, log)
    ip = ts["ip"]

    if cfg["mode"] == "rl":
        rl_mode = str(cfg.get("rl_mode") or "push").lower()
        hub = str(cfg.get("hub_url") or "").strip()
        hub_tok = secret("HUB_TOKEN", cfg.get("hub_token"))
        if rl_mode == "pull" or (rl_mode == "push" and hub):
            if not hub:
                raise SystemExit("[FATAL] pull 需要 hub_url")
            return _pull(cfg, log, secret, keepalive_stop, hub, hub_tok)
        tok = secret("PUSH_TOKEN", cfg.get("push_token"))
        if tok in ("", "YOUR_TOKEN_HERE"):
            raise SystemExit("[FATAL] push 需要 PUSH_TOKEN")
        return _push(cfg, log, secret, keepalive_stop, ip, tok)
    if cfg["mode"] == "bc":
        return _bc(cfg, log)
    raise SystemExit(f"[FATAL] 未知 mode={cfg['mode']!r}")

