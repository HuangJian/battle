"""remote/tailscale_boot.py — Colab/Kaggle 侧 Tailscale 引导 + 连接诊断。

为什么单独成文件（2026-09-16）：
  * notebook 要在**连上 tailnet 之前**起 Tailscale，那时拿不到 hub 的 code.zip
    ⇒ 引导逻辑不能只住在 code.zip 里；
  * 但它又不想长在 cell 里（cell 越肥越难改）⇒ 由 notebook 从 GitHub raw 拉本文件，
    拉不到再回退 cell 内联的精简版。

两个入口：
  ensure(cfg, log) -> dict   起 tailscale 并登录，返回 {ip, mode, sock, proxy}
  diagnose(cfg, log) -> None 分层体检（daemon/引擎/登录/peer/hub 连通），只打日志不改状态（除确保 daemon 在跑）

Colab 环境三条硬事实（踩过）：
  1. 没有 systemd（PID 1 不是 init）⇒ apt 装完 tailscaled **不会自启**，必须手工 Popen；
  2. 容器无 CAP_NET_ADMIN / 常无 /dev/net/tun ⇒ kernel 模式 rc=1 秒退，要退 userspace；
  3. 老版本 root 下撞 SO_MARK 权限错误 ⇒ 兜底以非 root 用户（irc/nobody）跑。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

SOCK = "/var/run/tailscale/tailscaled.sock"
STATE_DIR = "/tmp/tailscale-state"
DAEMON_LOG = "/tmp/tailscaled.log"
PROXY = "localhost:1055"
INSTALL_SH = "https://tailscale.com/install.sh"
# 非 root 兜底用户（SO_MARK 权限错误的官方绕法）：取第一个存在的
UNPRIV_USERS = ("irc", "nobody")


def _ts(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tailscale", f"--socket={SOCK}", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def state() -> str:
    """BackendState（NeedsLogin / Running / Starting…）；守护进程不在返回 ''。"""
    r = _ts("status", "--json")
    if r.returncode != 0:
        return ""
    try:
        return str(json.loads(r.stdout).get("BackendState") or "").strip()
    except ValueError:
        return ""


def self_ip() -> str:
    """本机 Tailscale IPv4（优先 status --json，回退已趋废弃的 ip -4）。"""
    r = _ts("status", "--json")
    if r.returncode == 0:
        try:
            ips = (json.loads(r.stdout).get("Self") or {}).get("TailscaleIPs") or []
        except ValueError:
            ips = []
        for ip in ips:
            if ":" not in str(ip):
                return str(ip)
    r = _ts("ip", "-4")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().splitlines()[0].strip()
    return ""


def install(log) -> None:
    log("安装 Tailscale …（约 20s）")
    subprocess.run(f"curl -fsSL {INSTALL_SH} | sh", shell=True, check=True, timeout=180)


def _spawn(log, argv: list[str], fd) -> subprocess.Popen[bytes]:
    try:
        Path(SOCK).unlink()
    except OSError:
        pass
    log("$ " + " ".join(argv))
    return subprocess.Popen(
        [*argv, f"--socket={SOCK}", f"--statedir={STATE_DIR}"],
        stdout=fd,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _alive(p: subprocess.Popen[bytes], log, name: str, secs: int = 20) -> bool:
    for _ in range(secs):
        if Path(SOCK).exists() and state():
            log(f"tailscaled 就绪（{name} 模式，pid={p.pid}）")
            return True
        if p.poll() is not None:
            log(f"{name} 模式退出（rc={p.returncode}）——换下一种")
            return False
        time.sleep(1)
    return False


def start_daemon(log) -> str:
    """拉起 tailscaled，返回 'kernel' / 'userspace' / 'userspace-unpriv'。"""
    Path(SOCK).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    fd = open(DAEMON_LOG, "ab")
    attempts: list[tuple[str, list[str]]] = [
        ("kernel", ["tailscaled"]),
        (
            "userspace",
            [
                "tailscaled",
                "--tun=userspace-networking",
                f"--socks5-server={PROXY}",
                f"--outbound-http-proxy-listen={PROXY}",
            ],
        ),
    ]
    for name, argv in attempts:
        p = _spawn(log, argv, fd)
        if _alive(p, log, name):
            return name
        p.kill()

    # 兜底：SO_MARK / operation not permitted（root 下特有的老 bug）⇒ 换非 root 用户
    tail = _read_tail(DAEMON_LOG)
    if "SO_MARK" in tail or "operation not permitted" in tail:
        for user in UNPRIV_USERS:
            if shutil.which("sudo") is None or not _user_exists(user):
                continue
            log(f"日志含 SO_MARK/权限错误 ⇒ 试非 root 用户 {user}")
            for d in (STATE_DIR, str(Path(SOCK).parent)):
                subprocess.run(["chown", "-R", user, d], capture_output=True, timeout=30)
            p = _spawn(
                log,
                [
                    "sudo",
                    "-u",
                    user,
                    "tailscaled",
                    "--tun=userspace-networking",
                    f"--socks5-server={PROXY}",
                ],
                fd,
            )
            if _alive(p, log, f"userspace-unpriv({user})"):
                return f"userspace-unpriv({user})"
            p.kill()

    log(_read_tail(DAEMON_LOG))
    raise RuntimeError(f"tailscaled 起不来，见 {DAEMON_LOG}")


def _read_tail(path: str, n: int = 2000) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError as e:
        return f"(读 {path} 失败: {e})"


def _user_exists(user: str) -> bool:
    r = subprocess.run(["id", user], capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def up(log, authkey: str = "", ephemeral: bool = True) -> None:
    """登录。flag 先问 --help：1.10x 只剩 --auth-key，--authkey/--ephemeral 可能已移除。"""
    hh = _ts("up", "--help", timeout=30)
    help_txt = (hh.stdout or "") + (hh.stderr or "")
    args = ["up"]
    if authkey:
        if "--auth-key" in help_txt:
            args.append(f"--auth-key={authkey}")
        elif "--authkey" in help_txt:
            args.append(f"--authkey={authkey}")
        else:
            args.append("--authkey=" + authkey)
    if ephemeral and "--ephemeral" in help_txt:
        args.append("--ephemeral")
    log("tailscale " + " ".join(
        (a.split("=", 1)[0] + "=***" if a.startswith(("--auth-key=", "--authkey=")) else a)
        for a in args))
    r = _ts(*args, timeout=120)
    if r.returncode != 0:
        # Go CLI 把真错误打在**最前面**，usage 在后面 ⇒ 打头部
        log(((r.stdout or "") + (r.stderr or "")).strip()[:1500])
        raise RuntimeError("tailscale up 失败（key 用过/过期/一次性？设备数超限？看上一行首句）")


def ensure(cfg: dict, log) -> dict:
    """装 → 起 daemon → 登录 → 取 IP。返回 {ip, mode, sock, proxy}。

    cfg: {"ts_authkey": str, "ts_ephemeral": bool, "proxy_env": bool}
    proxy_env=True 且落到 userspace 时，注入 HTTP_PROXY/ALL_PROXY（否则连 tailnet 都不通）。
    """
    if not shutil.which("tailscale"):
        install(log)
    mode = "already-running" if state() else start_daemon(log)
    if state() != "Running":
        up(log, str(cfg.get("ts_authkey") or "").strip(), bool(cfg.get("ts_ephemeral", True)))
    ip = ""
    for _ in range(30):
        ip = self_ip()
        if ip:
            break
        time.sleep(1)
    if not ip:
        log(_ts("status", timeout=30).stdout[-1500:])
        raise RuntimeError("Tailscale 未能获取 IP")
    if mode.startswith("userspace"):
        if cfg.get("proxy_env", True):
            for _k, _v in (
                ("HTTP_PROXY", f"http://{PROXY}"),
                ("ALL_PROXY", f"socks5://{PROXY}"),
                ("NO_PROXY", "localhost,127.0.0.1"),
            ):
                os.environ[_k] = _v
                os.environ[_k.lower()] = _v
        log(f"!! {mode} 模式：出站只能走 {PROXY} 代理（已设 HTTP_PROXY/ALL_PROXY）；"
            f"入站到本地端口不通 ⇒ pull 可用、push 不可用")
    log(f"Tailscale IP = {ip} (mode={mode})")
    return {"ip": ip, "mode": mode, "sock": SOCK, "proxy": PROXY}


# ── 诊断（tailscale.debug.ipynb 用；只读，除确保 daemon 在跑）──────────────
def diagnose(cfg: dict, log) -> None:
    hub = str(cfg.get("hub_url") or "").strip()
    token = str(cfg.get("hub_token") or "")
    log("── 1. 二进制 ──")
    ts = shutil.which("tailscale")
    log(f"  which tailscale = {ts}")
    if not ts:
        log("  ⇒ 没装，先跑 install(log)")
        return
    try:
        log("  " + _ts("version", timeout=20).stdout.strip().replace("\n", " | "))
    except Exception as e:
        log(f"  version 失败: {type(e).__name__}: {e}")

    log("── 2. 守护进程 / 引擎 ──")
    if not state():
        log("  daemon 未在跑 ⇒ 起一次")
        try:
            start_daemon(log)
        except Exception as e:
            log(f"  起不来: {type(e).__name__}: {e}")
            return
    log(f"  socket={SOCK} 存在={Path(SOCK).exists()} BackendState={state() or '(空)'}")
    log("  " + _read_tail(DAEMON_LOG, 600).replace("\n", " | ")[-600:])

    log("── 3. 登录 / 本机 IP ──")
    log(f"  Self IPv4 = {self_ip() or '(无)'}")

    log("── 4. peer（对端在线情况）──")
    r = _ts("status", "--json", timeout=30)
    peers: dict = {}
    if r.returncode == 0:
        try:
            peers = json.loads(r.stdout).get("Peer") or {}
        except ValueError as e:
            log(f"  status --json 解析失败: {e}")
    if not peers:
        log("  没有 peer（节点还没加入 tailnet / control 未下发）")
    for v in peers.values():
        log(f"  {v.get('HostName')} online={v.get('Online')} "
            f"ips={v.get('TailscaleIPs')} lastSeen={v.get('LastSeen')}")

    log("── 5. 代理环境 ──")
    for k in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY"):
        if os.environ.get(k):
            log(f"  {k}={os.environ[k]}")

    if not hub:
        log("── 6. hub ── 未配 hub_url，跳过")
        return
    log(f"── 6. hub 连通性（{hub}）──")
    targets = [hub.rstrip("/") + "/ping"]
    host = hub.split("://", 1)[-1].split("/")[0]
    name = host.split(":")[0]
    for v in peers.values():
        if v.get("HostName") == name.split(".")[0] or name in str(v.get("DNSName") or ""):
            for c in v.get("TailscaleIPs") or []:
                if ":" not in str(c):
                    targets.append(f"http://{c}:{host.split(':')[-1]}/ping")
    for t in targets:
        for label, extra in (("直连", []), ("走代理", ["-x", f"http://{PROXY}"])):
            cmd = ["curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
                   "-H", "Authorization: Bearer " + token, *extra, t]
            try:
                c = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
                log(f"  {label} {t} -> code={c.stdout.strip()} err={c.stderr.strip()[:160]}")
            except Exception as e:
                log(f"  {label} {t} -> {type(e).__name__}: {e}")
        log("  code=000 ⇒ 连不上：hub 只绑 127.0.0.1（hub_server.py --host 默认已是 0.0.0.0，"
            "确认进程是新启的）/ 本机防火墙拦 8787 入站 / 对端离线")
    mode = "userspace（代理已注入）" if os.environ.get("HTTP_PROXY") else "kernel/未注入代理"
    log(f"（本机 IP {self_ip() or '(无)'}，模式 {mode}；诊断结束）")
