"""remote/rollout_node.py — 采样节点（rollout / eval 云机）的运行时。

来源（2026-10-10，plan/rollout-node-auto-register v2）：`ipynb/rollout.ipynb`
（2026-10-10 由 `rollout.cloudflared.ipynb` 改名）的 cell 原来内联了全套逻辑；
本模块把逻辑收回仓库，cell 只留 CFG / 凭据 / 保活 / clone —— 与
`battle.tailscale.ipynb`（逻辑住 `remote/notebook_boot.py`）同构。

本模块经 **clone 导入**（不是 GitHub raw 单文件拉取），因此可以 import
`remote.tailscale_boot` 与 `common.*`（三个自包含引导模块的结构性例外**不适用**）。

三条硬纪律（写错都是事故）：

1. **凭据必须在 `tailscale_boot.ensure()` 之前读完**：ensure 落到 userspace 时会把
   `HTTP_PROXY`/`ALL_PROXY` 指到只转发 tailnet 的本地代理，之后平台 Secrets（公网 HTTPS）
   走不通（2026-09-17 Kaggle 事故；见 `tailscale_boot.py:427` docstring）。取值一律经
   cell 提供的 `secret(key, cfg_val)`，值永不进日志。源码顺序由
   `tests/remote/test_rollout_node_notebook.py` 钉住。
2. **公网下载一律在 `platform_net_env()` 里**（或发生在 ensure 之前）：bun 安装在 ensure 前，
   cloudflared 安装在 `platform_net_env()` 里（先例 `battle.offline.ipynb:408-409`）。
3. **通道自适应**：Colab 优先 tailscale；tailscale 引导失败、或注册被 hub ping 门拒（422 =
   入站不可达，userspace 模式的已知限制）⇒ 回落 cloudflared 并以隧道 URL 重注册。

自动注册（plan §2.2）：`POST /admin/nodes/register`（hub，Bearer = HUB_TOKEN）upsert
`rl-config.json` 的 `nodes[]`；本模块只是**发起方**，`enabled` 等字段语义见 hub 侧实现。
会话收线时 best-effort `POST /admin/nodes/unregister`；hub 未配置/不可达 ⇒ 打印 ★URL + ★authKey
（旧的手工路径，功能不残）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from common.env_probe import probe_cloud_env, worker_name
from common.proc import run_capture
from remote import tailscale_boot

#: 通道名（与 hub/nodes 注册载荷里的 url 形态对应）。
LINK_TAILSCALE = "tailscale"
LINK_CLOUDFLARED = "cloudflared"

#: 换名检测（quick tunnel 重连会换域名）。
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

#: 退避上限（net/server 类失败）。
RETRY_MAX_SEC = 300.0
RETRY_FIRST_SEC = 30.0

RegisterFn = Callable[..., Any]


# ---------------------------------------------------------------- 纯函数（可单测）

def resolve_link(env: str, cfg_link: str, has_ts_authkey: bool) -> str:
    """通道决策：`cfg_link` 可强压；否则 Colab 优先 tailscale、其余看有没有 TS_AUTHKEY。"""
    forced = str(cfg_link or "").strip().lower()
    if forced in (LINK_TAILSCALE, LINK_CLOUDFLARED):
        return forced
    if str(env or "").strip().lower() == "colab":
        return LINK_TAILSCALE
    return LINK_TAILSCALE if has_ts_authkey else LINK_CLOUDFLARED


def registry_url(hub_ip: str, hub_port: int, cfg_hub_url: str = "") -> str:
    """hub 注册地址 —— **直接转调** `tailscale_boot.resolve_hub_url`（单一实现，F13）。

    口径与 HUB_IP 取值链一致：裸 IP/主机名补 `http://` + 端口；整条 URL 原样；含 `<` 视为未填。
    """
    return tailscale_boot.resolve_hub_url(
        cfg_hub_url=cfg_hub_url, hub_ip=hub_ip, hub_port=hub_port
    )


def build_register_payload(
    node_id: str, url: str, auth_key: str, workers: int | str | None = 0, label: str = ""
) -> dict:
    """注册载荷。

    ★ `concurrency` **只在 `workers > 0` 时带上**（F2）：派发侧读
    `n.get("concurrency") or ping.cpus`（`trainer/dispatch.py:343`），缺省 = 按节点核数；
    写死一个 1 会把 96 核云机静默限成单核（旧 cell 口径 = 不填/0 = 不额外限流）。
    """
    payload: dict[str, Any] = {
        "id": str(node_id or ""),
        "url": str(url or ""),
        "authKey": str(auth_key or ""),
        "managed": True,
    }
    try:
        w = int(workers or 0)
    except (TypeError, ValueError):
        w = 0
    if w > 0:
        payload["concurrency"] = w
    lab = str(label or "").strip()
    if lab:
        payload["label"] = lab
    return payload


def should_register(state: dict | None, url: str, auth_key: str) -> bool:
    """去重：只有 url / authKey 变化才值得重发（心跳每 60s 一次，不许周期性重写文件）。"""
    st = state or {}
    return str(url or "") != str(st.get("url") or "") or str(auth_key or "") != str(
        st.get("authKey") or ""
    )


def classify_error(exc: BaseException) -> str:
    """错误分类（驱动退避）：auth → 立即放弃；net/server → 退避重试。

    ⚠ `HTTPError` 是 `URLError` **也是** `OSError` 的子类 ⇒ 必须先判它。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return "auth" if exc.code in (401, 403) else "server"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return "net"
    return "unknown"


# ---------------------------------------------------------------- 注册 HTTP（urlopen 注入）

def _hub_base(hub: str) -> str:
    return str(hub or "").strip().rstrip("/")


def register(
    urlopen: RegisterFn,
    hub: str,
    token: str,
    payload: dict,
    log: Callable[[str], None],
    timeout: float = 15.0,
) -> dict:
    """`POST /admin/nodes/register`；返回 `{ok, status, action, error, kind}`（绝不抛）。

    `status == 422` = hub 的 ping 门不过（它从 hub 侧连不到本节点）——调用方据此做
    tailscale → cloudflared 的回落判定。
    """
    base = _hub_base(hub)
    if not base or not token:
        return {"ok": False, "status": 0, "action": None, "error": "hub/token 未配置", "kind": "net"}
    req = urllib.request.Request(
        base + "/admin/nodes/register",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        return {
            "ok": True,
            "status": int(getattr(resp, "status", 200) or 200),
            "action": str(body.get("action") or ""),
            "error": None,
            "kind": "ok",
        }
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # 读体失败不改判定
            pass
        kind = classify_error(e)
        log(f"注册被拒: HTTP {e.code}（{kind}）{detail}")
        return {"ok": False, "status": int(e.code), "action": None, "error": detail, "kind": kind}
    except Exception as e:  # 网络/超时/其它
        kind = classify_error(e)
        log(f"注册失败: {type(e).__name__}: {e}（{kind}）")
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "error": f"{type(e).__name__}: {e}",
            "kind": kind,
        }


def unregister(
    urlopen: RegisterFn,
    hub: str,
    token: str,
    node_id: str,
    log: Callable[[str], None],
    timeout: float = 15.0,
) -> dict:
    """`POST /admin/nodes/unregister`（会话收线 best-effort；返回形状同 `register`）。"""
    base = _hub_base(hub)
    if not base or not token or not node_id:
        return {"ok": False, "status": 0, "action": None, "error": "未配置", "kind": "net"}
    req = urllib.request.Request(
        base + "/admin/nodes/unregister",
        data=json.dumps({"id": str(node_id)}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        return {
            "ok": True,
            "status": int(getattr(resp, "status", 200) or 200),
            "action": str(body.get("action") or ""),
            "error": None,
            "kind": "ok",
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": int(e.code), "action": None, "error": f"HTTP {e.code}", "kind": classify_error(e)}
    except Exception as e:
        return {"ok": False, "status": 0, "action": None, "error": f"{type(e).__name__}: {e}", "kind": classify_error(e)}


# ---------------------------------------------------------------- 进程/网络小工具

def _sh(cmd: list[str], *, cwd: str | None = None, timeout: float | None = None):
    """跑一条命令；启动失败/超时 ⇒ rc=127 的哑结果（与旧 cell 同语义，不抛）。

    走 `common.proc.run_capture`：**不写裸 `text=True`**（控制台代码页会解码失败 ⇒ 输出变
    `None`，`tests/common/test_common_layer.py::test_every_production_capture_site_pins_encoding` 钉住）。
    """
    try:
        return run_capture(cmd, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return SimpleNamespace(returncode=127, stdout="", stderr=str(e))


def _public_net() -> Any:
    """公网下载上下文：还原 ensure 之前的代理环境（没有注入时是空操作）。"""
    return tailscale_boot.platform_net_env()


def _terminate(p: Any) -> None:
    if p is None or getattr(p, "poll", None) is None or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _dump_tail(path: Path, log: Callable[[str], None], n: int = 40) -> None:
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
            log("  | " + ln)
    except OSError:
        pass


def _slot_count(explicit: int) -> tuple[int, str]:
    """并发槽位：显式值 > `cpu_worker_slots(effective_cores())` > 同规则内联回退。"""
    cores = os.cpu_count() or 1
    try:
        from common.platform_utils import cpu_worker_slots, effective_cores

        cores = effective_cores()
        slots = cpu_worker_slots(cores)
        note = f"platform_utils cores={cores}"
    except Exception as e:  # 拉不起 platform_utils 时的兜底（与旧 cell 同规则）
        slots = cores if cores <= 4 else max(1, min(cores, max(cores - 2, int(cores * 0.8))))
        note = f"内联回退({type(e).__name__}) cores={cores}"
    if int(explicit or 0) > 0:
        return int(explicit), note + "（CFG.workers 显式）"
    return int(slots), note


def _ensure_bun(code_dir: Path, cfg: dict, log: Callable[[str], None]) -> str:
    """找/装 bun（★ 公网安装在 ensure 之前发生；这里仍包 `_public_net()` 兜底）。"""
    cands = ("bun", "/root/.bun/bin/bun", str(Path.home() / ".bun" / "bin" / "bun"))
    bun = ""
    for cand in cands:
        if _sh([cand, "--version"], timeout=120).returncode == 0:
            bun = cand
            break
    if not bun:
        log("安装 bun…（公网下载）")
        with _public_net():
            _sh(["bash", "-lc", "curl -fsSL https://bun.sh/install | bash"], timeout=600)
        for cand in ("/root/.bun/bin/bun", str(Path.home() / ".bun" / "bin" / "bun"), "bun"):
            if _sh([cand, "--version"], timeout=120).returncode == 0:
                bun = cand
                break
    if not bun:
        return ""
    bin_dir = str(Path(bun).parent)
    if bin_dir and bin_dir != "." and bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    if cfg.get("bun_install_deps"):
        r = _sh([bun, "install", "--frozen-lockfile"], cwd=str(code_dir), timeout=900)
        log(f"bun install rc={r.returncode}（agent 零第三方依赖，失败不致命）")
    return bun


def _start_cloudflared(
    cfg: dict, port: int, cf_log: Path, log: Callable[[str], None]
) -> Any:
    """起 cloudflared quick tunnel；二进制缺失时**在 `_public_net()` 里**下载（F5）。"""
    cf = str(cfg.get("cloudflared_path") or "").strip()
    if not cf:
        w = _sh(["bash", "-lc", "command -v cloudflared || true"], timeout=60).stdout.strip()
        cf = w.splitlines()[-1].strip() if w else ""
    if not cf:
        cf = "/usr/local/bin/cloudflared"
        log("安装 cloudflared…（公网下载，代理已还原）")
        with _public_net():
            r = _sh(
                [
                    "curl",
                    "-fsSL",
                    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
                    "-o",
                    cf,
                ],
                timeout=180,
            )
        if r.returncode == 0:
            os.chmod(cf, 0o755)
        else:
            log("cloudflared 安装失败")
            return None
    try:
        return subprocess.Popen(
            [cf, "tunnel", "--url", f"http://localhost:{port}", "--logfile", str(cf_log)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except OSError as e:
        log(f"cloudflared 启动失败: {e}")
        return None


def _tunnel_url(cf_log: Path) -> str:
    """隧道 URL：取日志里**最后一个**（quick tunnel 重连会换域名；旧 URL 会撞 HTTP 530）。"""
    try:
        txt = cf_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = _URL_RE.findall(txt)
    return m[-1] if m else ""


def _wait_tunnel_url(cf_log: Path, cfp: Any, log: Callable[[str], None], timeout: float = 90.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        url = _tunnel_url(cf_log)
        if url:
            return url
        if cfp is not None and cfp.poll() is not None:
            break
        time.sleep(2)
    log(f"⚠ 没拿到隧道 URL —— 下面是 {cf_log} 尾部：")
    _dump_tail(cf_log, log, 25)
    return ""


def _agent_entry(code_dir: Path) -> Path | None:
    for rel in ("tools/agent/sampler-agent.ts", "tools/dist/sampler-agent.ts"):
        p = code_dir / rel
        if p.is_file():
            return p
    return None


def _start_agent(
    bun: str, entry: Path, code_dir: Path, port: int, workers: int, cfg: dict, agent_log: Path
) -> Any:
    cmd = [
        bun,
        str(entry),
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--cache-mb",
        str(int(cfg.get("cache_mb") or 2048)),
    ] + [str(a) for a in (cfg.get("agent_extra") or [])]
    fh = open(agent_log, "a", encoding="utf-8", errors="replace")  # 交给 Popen 持有（本进程不关）
    return subprocess.Popen(
        cmd, cwd=str(code_dir), stdout=fh, stderr=subprocess.STDOUT
    )


def _wait_auth(agent: Any, auth_path: Path, timeout: float = 90.0) -> str:
    t = time.time()
    while time.time() - t < timeout:
        try:
            v = auth_path.read_text(encoding="utf-8").strip()
        except OSError:
            v = ""
        if v:
            return v
        if agent.poll() is not None:
            return ""
        time.sleep(2)
    return ""


def _ping(port: int, auth: str = "", timeout: float = 5.0, urlopen: RegisterFn | None = None) -> dict | None:
    """本地探活。`/v1/ping` **要鉴权**（`sampler-agent.ts`，只有 `/pool` 公开）。"""
    opener = urlopen or urllib.request.urlopen
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/ping")
        if auth:
            req.add_header("Authorization", f"Bearer {auth}")
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- 主流程（cell 调用）

def run(cfg: dict, log: Callable[[str], None], secret: Callable[[str, Any], Any]) -> int:
    """采样节点主流程。返回进程退出码（0 = 正常收线）。

    cell 契约：CFG / `_secret` / 保活线程 / clone / `sys.path` 都已就绪；本函数**不再碰
    clone**（它需要 GITHUB_TOKEN，住 cell 的 clone 段）。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    code_dir = Path(str(cfg.get("code_dir") or "")).expanduser()
    if not code_dir.is_dir():
        log(f"FATAL: code_dir 不存在: {code_dir}")
        return 2

    # ── 1. 凭据（★ ensure 之前读完；值永不进日志）────────────────────────
    ts_authkey = str(secret("TS_AUTHKEY", cfg.get("ts_authkey")) or "").strip()
    hub_ip = str(secret("HUB_IP", cfg.get("hub_ip")) or "").strip()
    hub_token = str(secret("HUB_TOKEN", cfg.get("hub_token")) or "").strip()
    hub_port = int(cfg.get("hub_port") or 0)
    cfg_hub_url = str(cfg.get("hub_url") or "").strip()

    env = probe_cloud_env()
    has_ts = bool(ts_authkey)
    link = resolve_link(env, str(cfg.get("link") or "auto"), has_ts)
    port = int(cfg.get("agent_port") or 8443)
    workers, slot_note = _slot_count(int(cfg.get("workers") or 0))
    node_id = str(cfg.get("node_id") or "").strip() or f"rollout-{env}"
    hub_url = registry_url(hub_ip, hub_port, cfg_hub_url) if (hub_ip or cfg_hub_url) else ""
    hub_configured = bool(hub_url and hub_token)
    log(
        f"环境={env} 通道={link} workers={workers}（{slot_note}）node_id={node_id} "
        f"hub={'已配置' if hub_configured else '未配置（降级为手工登记）'}"
    )

    # ── 2. bun（公网安装在 ensure 之前）───────────────────────────────────
    bun = _ensure_bun(code_dir, cfg, log)
    if not bun:
        log("FATAL: bun 不可用（也装不上）")
        return 2

    # ── 3. 通道：tailscale 优先；失败回落 cloudflared ─────────────────────
    agent = None
    cfp = None
    cf_log = Path("/tmp/rollout-cloudflared.log")
    url = ""

    def _open_cloudflared(reason: str) -> None:
        nonlocal link, url, cfp
        log(f"cloudflared 通道（{reason}）")
        if cfp is None:
            cfp = _start_cloudflared(cfg, port, cf_log, log)
        if cfp is not None:
            url = _wait_tunnel_url(cf_log, cfp, log)
        link = LINK_CLOUDFLARED

    if link == LINK_TAILSCALE:
        ts_cfg = {
            "ts_authkey": ts_authkey,
            "ts_ephemeral": bool(cfg.get("ts_ephemeral", True)),
            "proxy_env": bool(cfg.get("ts_proxy_env", True)),
            "engine": str(cfg.get("ts_engine") or ""),
            "no_proxy_extra": str(cfg.get("ts_no_proxy_extra") or ""),
            "ts_tarball": str(cfg.get("ts_tarball") or ""),
            "ts_prefix": str(cfg.get("ts_prefix") or ""),
        }
        try:
            info = tailscale_boot.ensure(ts_cfg, log)
            url = f"http://{info['ip']}:{port}"
            mode = str(info.get("mode") or "")
            log(f"tailscale 就绪: url={url} mode={mode}")
            if mode.startswith("userspace"):
                log("⚠ userspace 模式：入站到本地端口不通（注册会被 hub ping 门拒 422）——"
                    "注册失败会自动回落 cloudflared")
        except Exception as e:
            log(f"tailscale 引导失败（{type(e).__name__}: {e}）—— 回落 cloudflared")
            link = LINK_CLOUDFLARED
    if link != LINK_TAILSCALE:
        _open_cloudflared("通道决策=cloudflared")

    # ── 4. sampler-agent ─────────────────────────────────────────────────
    entry = _agent_entry(code_dir)
    if entry is None:
        log(f"FATAL: 找不到 sampler-agent.ts（{code_dir / 'tools'}）")
        return 2
    agent_log = Path("/tmp/rollout-agent.log")
    auth_path = entry.parent / "agent.auth"
    agent = _start_agent(bun, entry, code_dir, port, workers, cfg, agent_log)
    auth = _wait_auth(agent, auth_path)
    ping = _ping(port, auth)
    if ping is None:
        log("agent 30–60s 未就绪 —— 下面是 agent 日志尾部（真因通常在这）：")
        _dump_tail(agent_log, log)

    # ── 5. 注册（首拍；422 ⇒ tailscale 入站不可达 ⇒ 回落重注册）───────────
    reg_state: dict[str, str] = {"url": "", "authKey": ""}
    backoff = 0.0
    next_retry = 0.0

    def _try_register() -> dict:
        payload = build_register_payload(
            node_id, url, auth, workers, worker_name(env, "t" if link == LINK_TAILSCALE else "c")
        )
        out = register(urllib.request.urlopen, hub_url, hub_token, payload, log)
        if out["ok"]:
            reg_state.update({"url": url, "authKey": auth})
            log(f"已注册节点 id={node_id} url={url} action={out['action']}")
        return out

    if hub_configured:
        out = _try_register()
        if not out["ok"] and int(out.get("status") or 0) == 422 and link == LINK_TAILSCALE:
            log("hub ping 门 422：tailscale 入站不可达 —— 回落 cloudflared 并重注册")
            _open_cloudflared("ping 门 422")
            out = _try_register()
        if not out["ok"]:
            if out["kind"] == "auth":
                hub_configured = False
                log("HUB_TOKEN 被拒（auth）——注册放弃：token 不对，重试无意义（检查 secret 后重跑）")
            else:
                backoff = RETRY_FIRST_SEC
                next_retry = time.time() + backoff
                log(f"首拍注册失败（{out['kind']}）——{int(backoff)}s 后重试")

    # ── 6. 横幅（注册成功 = 自动；否则保留手工路径）───────────────────────
    log("=" * 68)
    if hub_configured and reg_state["url"]:
        log(f"★ 已自动注册 : id={node_id}（rl-config.nodes[] 每轮热读，已生效）")
    else:
        log("★ 未注册 —— 如需手工：写入本机 nn-training/rl-config.json：")
        _u, _a = url or "", auth or ""
        log(f'  {{"nodes":[{{"id":"{node_id}","url":"{_u}","authKey":"{_a}"}}]}}')
    log(f"★ URL     : {url or '(未取到)'}")
    log(f"★ authKey : {auth or '(未生成)'}")
    log(
        f"  workers : {workers}  bun {_sh([bun, '--version']).stdout.strip()}  "
        f"codeHash={str((ping or {}).get('codeHash', ''))[:12]}"
    )
    log("=" * 68)

    # ── 7. 监护：agent 死重启 / 隧道换名重注册 / 会话到点收线 ─────────────
    deadline = time.time() + max(0.0, float(cfg.get("max_session_hours") or 0)) * 3600
    restart_limit = int(cfg.get("restart_limit") or 5)
    restarts, tick, miss = 0, 0, 0
    try:
        while True:
            if time.time() > deadline:
                log("会话上限到点 —— 收线")
                break
            dead, why = False, ""
            if agent.poll() is not None:
                dead, why = True, f"rc={agent.returncode}"
            else:
                tick += 1
                if tick % 6 == 0:
                    if _ping(port, auth) is None:
                        miss += 1
                        if miss >= 3:
                            dead, why = True, "HTTP 探活连续 3 次失败"
                    else:
                        miss = 0
            if dead:
                if restarts >= restart_limit:
                    log(f"agent 已重启 {restarts} 次仍不健康 —— 停止（看 {agent_log}）")
                    break
                restarts += 1
                log(f"agent 判定死亡（{why}）—— 第 {restarts} 次重启")
                _terminate(agent)
                agent = _start_agent(bun, entry, code_dir, port, workers, cfg, agent_log)
                miss = 0
                auth2 = _wait_auth(agent, auth_path, 30)
                if auth2 and auth2 != auth:
                    auth = auth2
                    log("★ authKey 变了 —— 将重注册")
            if cfp is not None:
                cur = _tunnel_url(cf_log)
                if cur and cur != url:
                    url = cur
                    log(f"★ 隧道换名（quick tunnel 重连）：{url} —— 将重注册")
            if hub_configured and should_register(reg_state, url, auth) and time.time() >= next_retry:
                out = _try_register()
                if out["ok"]:
                    backoff, next_retry = 0.0, 0.0
                elif out["kind"] == "auth":
                    hub_configured = False
                    log("HUB_TOKEN 被拒（auth）——注册放弃")
                else:
                    backoff = RETRY_FIRST_SEC if backoff <= 0 else min(RETRY_MAX_SEC, backoff * 2)
                    next_retry = time.time() + backoff
                    log(f"重注册失败（{out['kind']}）——{int(backoff)}s 后重试")
            time.sleep(10)
    except KeyboardInterrupt:
        log("收到中断")
    finally:
        if hub_configured and reg_state["url"]:
            out = unregister(urllib.request.urlopen, hub_url, hub_token, node_id, log)
            log(f"unregister id={node_id}: {'ok' if out['ok'] else out['error']}")
        _terminate(agent)
        _terminate(cfp)
    log("采样节点结束")
    return 0
