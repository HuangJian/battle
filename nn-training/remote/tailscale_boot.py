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

第 4 条硬事实（2026-09-17 Kaggle 事故）：**userspace 模式装上的出站代理只转发
Tailscale IP**（本地 1055 的 HTTP/SOCKS5 代理）。所以引导之后，进程里任何要访问
**公网**的调用（平台 Secrets、pip、git）都必须走 `platform_net_env()` 临时还原平台
自己的代理环境；否则请求被塞进 tailnet 代理 → 读不出凭据 → 平台侧报鉴权错。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

#: 本机 hub 的默认端口（与 `hub-server --port` 默认一致）。
HUB_DEFAULT_PORT = 8787


def resolve_hub_url(cfg_hub_url: str = "", hub_ip: str = "", hub_port: int = 0) -> str:
    """本机 hub 地址：**凭据给的 address 优先**，回落 CFG 手填的 `hub_url`。

    为什么多一个凭据：hub 跑在操作者本机，地址每开一个会话都得填一次，而值又长期不变
    （Tailscale IP 在设备重注册前是稳定的）——正是 secret 的用途。于是它走与
    TS_AUTHKEY/HUB_TOKEN 同一条取用链（环境变量 → Colab/Kaggle Secrets → CFG 手填），
    键名 `HUB_IP`（读进 CFG 的 `hub_ip`）。

    住在本模块而不是 notebook_boot：两个 notebook（训练 cell 与连接体检）都拉本文件，
    而体检那边只拉这一个——单源一份，两边不会漂。

    取值两吃（同一个键，不必记两套约定）：

      HUB_IP = 100.64.0.5                     → http://100.64.0.5:8787
      HUB_IP = 100.64.0.5:9999                → http://100.64.0.5:9999
      HUB_IP = http://hub.tailnet.ts.net:8787 → 原样（换域名/换协议/同机 127.0.0.1 都行）

    CFG 里的模板值（`http://<本地TS_IP>:8787`，含 `<`）一律视为**未填**：否则忘了改就是
    拿一个带尖括号的主机名去连，报错发生在 DNS 层，比在这里响亮地说一句难查得多。

    两者都没有 → 返空串（由调用方响亮失败并指名该填哪个键）。
    """
    ip = str(hub_ip or "").strip()
    if ip:
        if "://" in ip:
            return ip.rstrip("/")  # 整条 URL：原样（含自定义端口/域名）
        hostport = ip if ":" in ip else f"{ip}:{int(hub_port or 0) or HUB_DEFAULT_PORT}"
        return f"http://{hostport}"
    fallback = str(cfg_hub_url or "").strip()
    if "<" in fallback:  # 模板占位符 = 没填
        return ""
    return fallback.rstrip("/")


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


def start_daemon(log, order: str = "") -> str:
    """拉起 tailscaled，返回 'kernel' / 'userspace' / 'userspace-unpriv'。

    order：逗号分隔的引擎顺序（CFG `ts_engine` / env `TS_ENGINE`），默认
    `kernel,userspace`。可改成 `userspace`（或 `userspace,kernel`）用于「kernel
    模式那次 TUN/路由尝试本身就有副作用」的容器——它在失败前可能已经动过 notebook
    容器的网络命名空间（2026-09-17 Kaggle 待验证假设，E3 实验阀门；默认行为不变）。
    """
    Path(SOCK).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    fd = open(DAEMON_LOG, "ab")
    engines: dict[str, list[str]] = {
        "kernel": ["tailscaled"],
        "userspace": [
            "tailscaled",
            "--tun=userspace-networking",
            f"--socks5-server={PROXY}",
            f"--outbound-http-proxy-listen={PROXY}",
        ],
    }
    names = [n.strip() for n in (order or "kernel,userspace").split(",") if n.strip()]
    attempts: list[tuple[str, list[str]]] = [(n, engines[n]) for n in names if n in engines]
    if not attempts:
        attempts = [("kernel", engines["kernel"]), ("userspace", engines["userspace"])]
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


# ── 出站代理环境（userspace 模式）与「平台网络」临时还原 ─────────────────────
#: 本模块会改写的代理环境键（大小写各一份——requests 认大写、部分工具认小写）
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)
#: 改写前的原值（None = 原本未设）；`platform_net_env()` 靠它还原平台自己的代理
_ORIG_PROXY_ENV: dict[str, str | None] = {}


def set_proxy_env(cfg: dict | None = None) -> None:
    """把出站代理指向 userspace tailscaled（`PROXY`），并**合并**而非覆盖 NO_PROXY。

    NO_PROXY 必须保留平台原有条目（localhost 通道、平台内网服务），否则引导后平台
    自己的 API 会被塞进 tailnet 代理——而它只转发 Tailscale IP。同时记下改写前的
    原值，供 `platform_net_env()` 还原。
    """
    cfg = cfg or {}
    for k in PROXY_ENV_KEYS:
        _ORIG_PROXY_ENV.setdefault(k, os.environ.get(k))
    old = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = ["localhost", "127.0.0.1", "::1", str(cfg.get("no_proxy_extra") or ""), old]
    for k, v in (
        ("HTTP_PROXY", f"http://{PROXY}"),
        ("ALL_PROXY", f"socks5://{PROXY}"),
        ("NO_PROXY", ",".join(p for p in parts if p)),
    ):
        os.environ[k] = v
        os.environ[k.lower()] = v


@contextlib.contextmanager
def platform_net_env() -> Iterator[None]:
    """临时还原「引导前」的代理环境——只给仍要访问**公网**的进程内调用用。

    为什么：userspace 引导后 HTTP_PROXY/ALL_PROXY 指向的本地代理只转发 Tailscale
    IP，公网 HTTPS（Kaggle/Colab Secrets、pip、git）一律走不通；而 cell 的 `_secret`
    与 notebook_boot 的凭据读取恰恰是公网调用（2026-09-17 Kaggle 事故：HUB_TOKEN
    读失败被吞 → /code 401 → 会话终结）。子进程继承的是**当前**环境，所以出这个
    with 之后 tailnet 代理照旧可用。
    """
    saved = {k: os.environ.get(k) for k in PROXY_ENV_KEYS}
    for k in PROXY_ENV_KEYS:
        orig = _ORIG_PROXY_ENV.get(k)
        if orig is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = orig
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def ensure(cfg: dict, log) -> dict:
    """装 → 起 daemon → 登录 → 取 IP。返回 {ip, mode, sock, proxy}。

    cfg: {"ts_authkey": str, "ts_ephemeral": bool, "proxy_env": bool,
          "engine": str, "no_proxy_extra": str}
    proxy_env=True 且落到 userspace 时，注入 HTTP_PROXY/ALL_PROXY（否则连 tailnet 都不通）。
    ★ 调用方必须在**进本函数之前**把凭据全部读完：本函数会改写进程的代理环境，
      之后平台 Secrets（公网 HTTPS）就走不通了。
    """
    if not shutil.which("tailscale"):
        install(log)
    already = bool(state())
    engine = str(cfg.get("engine") or os.environ.get("TS_ENGINE") or "")
    mode = "already-running" if already else start_daemon(log, engine)
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
    # already-running 时 mode 不含 "userspace"，靠探测 SOCKS 端口判断
    is_userspace = mode.startswith("userspace") or (already and _port_listening(PROXY))
    if is_userspace:
        if cfg.get("proxy_env", True):
            set_proxy_env(cfg)
        log(f"!! {mode} 模式：出站只能走 {PROXY} 代理（已设 HTTP_PROXY/ALL_PROXY）；"
            f"入站到本地端口不通 ⇒ pull 可用、push 不可用")
        log(f"   NO_PROXY={os.environ.get('NO_PROXY') or '(空)'}"
            f"（公网调用需 `platform_net_env()` 还原平台代理）")
    log(f"Tailscale IP = {ip} (mode={mode})")
    return {"ip": ip, "mode": mode, "sock": SOCK, "proxy": PROXY}


def _port_listening(addr: str) -> bool:
    """addr 形如 'localhost:1055'——探测 TCP 端口是否在监听。"""
    import socket

    host, _, port_s = addr.rpartition(":")
    try:
        with socket.create_connection((host or "localhost", int(port_s)), timeout=2):
            return True
    except OSError:
        return False


# ── 诊断（tailscale.debug.ipynb 用；只读，除确保 daemon 在跑）──────────────
def diagnose(cfg: dict, log) -> None:
    # hub 地址与运行时同口径（HUB_IP 凭据优先、CFG 模板值视为未填）——否则会出现
    # 「体检说连不上、真跑却连得上」（或反过来）这种最难信的一种诊断结论。
    hub = resolve_hub_url(
        str(cfg.get("hub_url") or ""),
        str(cfg.get("hub_ip") or ""),
        int(cfg.get("hub_port") or 0),
    )
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
        log("── 6. hub ── 未配地址（HUB_IP 凭据 / CFG hub_url），跳过")
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
