"""remote/tailscale_boot.py — Colab/Kaggle 侧 Tailscale 引导 + 连接诊断 + 引导期传输护栏。

为什么单独成文件（2026-09-16）：
  * notebook 要在**连上 tailnet 之前**起 Tailscale，那时拿不到 hub 的 code.zip
    ⇒ 引导逻辑不能只住在 code.zip 里；
  * 但它又不想长在 cell 里（cell 越肥越难改）⇒ 由 notebook 从 GitHub raw 拉本文件，
    拉不到再回退 cell 内联的精简版。

三个入口：
  ensure(cfg, log) -> dict   起 tailscale 并登录，返回 {ip, mode, sock, proxy}
  diagnose(cfg, log) -> None 分层体检（daemon/引擎/登录/peer/hub 连通），只打日志不改状态（除确保 daemon 在跑）
  fetch_guarded(url, ...) -> bytes  ⤵

**为什么护栏也住这里**（2026-09-20，plan/minimize-payload.plan.md §4.0）：引导期两个**大 body**
GET（hub `/code` 的 code.zip、离线盘的 `task-pack`）都发生在 **code.zip 之前**，那时
`remote.worker` 的停滞/低速重抽（`BODY_IDLE_TIMEOUT_SEC` / `WireSlowError`）根本不存在——
而 code.zip 正是被下载的那个东西，鸡生蛋的问题。三个 notebook（训练 cell / 连接体检 /
离线盘）都拉本文件，所以共享工具住这儿与 `resolve_hub_url` 同一条理由：单源一份，两边不会漂。

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
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
#: 官方静态包文件名模式（Linux amd64；`pkgs.tailscale.com/stable/#static`）
_OFFLINE_TARBALL_GLOBS = (
    "tailscale_*_amd64.tgz",
    "tailscale_*_amd64.tar.gz",
    "tailscale_latest_amd64.tgz",
)
#: 离线包搜索根：Colab / Drive / Kaggle / AI Studio / 本机上传目录
DEFAULT_OFFLINE_ROOTS = (
    "/content",
    "/content/drive/MyDrive",
    "/kaggle/working",
    "/kaggle/input",
    "/tmp",
    "/home/aistudio",
)
# 非 root 兜底用户（SO_MARK 权限错误的官方绕法）：取第一个存在的
UNPRIV_USERS = ("irc", "nobody")


def _ts(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    # encoding/errors 必须**就地**写全：本模块从 GitHub raw 单独拉取，拿不到
    # `common.proc.run_capture`（见 common/__init__.py「谁不能用本包」）。
    # 裸 text=True 按 locale 解码（zh-CN Windows = cp936），tailscale 的输出可能带
    # 非 ASCII ⇒ 读线程抛 UnicodeDecodeError、stdout 变 None（docs/nn/engineering.md §19）。
    return subprocess.run(
        ["tailscale", f"--socket={SOCK}", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def find_offline_tarball(explicit: str = "", search_roots: list | tuple | None = None) -> str:
    """定位已上传的官方静态 tarball；找不到返回空串。

    explicit：CFG/环境变量给的绝对路径优先。search_roots：每根下匹配
    `tailscale_*_amd64.tgz`，并再下探一层（Kaggle Dataset 目录名一层）。
    """
    import glob

    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.is_file() else ""
    if search_roots is not None:
        roots = [r for r in search_roots if r]
    else:
        roots = [*DEFAULT_OFFLINE_ROOTS, str(Path.home())]
    for root in roots:
        if not root:
            continue
        for name in _OFFLINE_TARBALL_GLOBS:
            hits = sorted(glob.glob(os.path.join(root, name)))
            if not hits:
                hits = sorted(glob.glob(os.path.join(root, "*", name)))
            if hits:
                return hits[0]
    return ""


def install(log, tarball: str = "", prefix: str = "", search_roots=None, run=None) -> None:
    """装 tailscale：**离线包优先**，找不到再 `curl install.sh`。

    tarball/CFG `ts_tarball` 可显式指定；否则在 DEFAULT_OFFLINE_ROOTS 里搜
    官方 amd64 静态包（用户事先上传到 Drive/Kaggle Dataset/AI Studio 数据集）。
    prefix：解压到该目录并前插 PATH（默认 ``~/tailscale``）——云容器往往无
    root/无 systemd，不必跑 install.sh 的系统包路径。
    """
    _run = run if run is not None else subprocess.run
    tb = find_offline_tarball(tarball, search_roots=search_roots)
    if tb:
        pfx = Path(prefix or os.environ.get("TS_INSTALL_PREFIX") or (Path.home() / "tailscale"))
        log(f"离线安装包: {tb} → {pfx}")
        try:
            pfx.mkdir(parents=True, exist_ok=True)
            _run(
                ["tar", "-xzf", tb, "-C", str(pfx), "--strip-components=1"],
                check=True,
                timeout=60,
            )
            for name in ("tailscale", "tailscaled"):
                f = pfx / name
                if f.is_file():
                    f.chmod(f.stat().st_mode | 0o111)
            path = os.environ.get("PATH") or ""
            if str(pfx) not in path.split(os.pathsep):
                os.environ["PATH"] = f"{pfx}{os.pathsep}{path}"
            if shutil.which("tailscale"):
                log(f"tailscale 就绪: {shutil.which('tailscale')}（离线包）")
                return
            log("离线解压后 PATH 仍找不到 tailscale — 回退在线安装")
        except Exception as e:  # 离线失败必须能落到在线，不中断引导（捕获面就是「什么都可能失败」）
            log(f"离线安装失败（{type(e).__name__}: {e}）— 回退在线安装")
    else:
        log("未找到离线安装包（ts_tarball / Drive / Dataset / 默认搜索根）→ 在线 install.sh")
    log("安装 Tailscale …（约 20s）")
    _run(f"curl -fsSL {INSTALL_SH} | sh", shell=True, check=True, timeout=180)


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
    # 编码必填：同上（本模块独立拉取，不能走 common.proc）。
    r = subprocess.run(
        ["id", user], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
    )
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
        install(
            log,
            tarball=str(cfg.get("ts_tarball") or os.environ.get("TS_TARBALL") or ""),
            prefix=str(cfg.get("ts_prefix") or ""),
        )
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


# ── 引导期大 body 传输护栏（2026-09-20；plan/minimize-payload.plan.md §4.0）─────
#
# 现场（同一台 V100 / 同一 hub / 同一隧道，2026-09-20）：传输速率是 **44× 双峰** ——
# payload 22.7 KB/s ↔ 990 KB/s；boot 的 code GET 13.5 KB/s，而 **3 秒后**的 blob GET ≥120 KB/s。
# 慢的不是端点，是**每条连接抽签**（urllib 每请求新建连接，没有 keep-alive 池）。所以：
# 停滞要能看见（引导期原来一行进度都没有）、慢得离谱就**换一条连接**。
#
# 与 `remote/worker.py` 的同名判据是**孪生实现**（那边跑 code.zip 之后，这边跑之前）：
# 阈值口径、在首块判一次、上限 3 次、最后一次不重抽，全部一致——改一边要同步另一边。

#: 读块大小（与 worker 的 `BODY_CHUNK` 同值）。
BOOT_CHUNK = 256 * 1024
#: 停滞判据：这么久没有新字节 ⇒ 判停滞（与 worker 的 `BODY_IDLE_TIMEOUT_SEC` 同值）。
BOOT_IDLE_TIMEOUT_SEC = 45.0
#: 进度行最小间隔（秒）。
BOOT_PROGRESS_MIN_SEC = 5.0
#: 低速判据的绝对下限。好签实测 ≈1 MB/s、坏签 13–23 KB/s，取两者之间。
BOOT_MIN_RATE = 80 * 1024.0
#: 判据所需的最小观测：样本太小不下结论（免得拿 2 KB 判一条链路）。
BOOT_PROBE_BYTES = 128 * 1024
BOOT_PROBE_SEC = 3.0
#: 按当前速率**预计剩余**超过它才值得折腾（快跑完了就不动——重抽也有建连成本）。
BOOT_REROLL_BUDGET_SEC = 20.0
#: 单次下载的重抽上限（只给幂等 GET）；**最后一次尝试永不重抽**（慢链路不会变成「永远下不完」）。
BOOT_REROLL_MAX = 3
#: 会话最好速率的采样最小体量（小 body 的瞬时速率不代表链路）。
BOOT_RATE_SAMPLE_MIN_BYTES = 256 * 1024

#: 本会话已观测到的最好大 body 速率（bytes/s）——相对判据的参照。
_BEST_RATE = 0.0


def _note_rate(rate: float, nbytes: int) -> None:
    """记下本会话的**大 body** 最好速率（相对判据的参照）。"""
    global _BEST_RATE
    if nbytes >= BOOT_RATE_SAMPLE_MIN_BYTES and rate > _BEST_RATE:
        _BEST_RATE = rate


def _min_rate() -> float:
    """坏签判据 = max(绝对下限, 本会话最好速率 / 4)——只在「明显偏离」时动手。"""
    return max(BOOT_MIN_RATE, _BEST_RATE / 4.0)


def _reroll_decision(
    got: int,
    total: int,
    elapsed: float,
    *,
    floor_rate: float | None = None,
    budget_sec: float = BOOT_REROLL_BUDGET_SEC,
    probe_bytes: int = BOOT_PROBE_BYTES,
    probe_sec: float = BOOT_PROBE_SEC,
) -> tuple[bool, float, float]:
    """是否该断开重抽 → `(决定, 实测速率, 按此速率的预计剩余秒数)`。

    **纯函数**：判据只有一处实现（改阈值只改这里），也就能被单测直接钉住。
    """
    if total <= 0 or got <= 0 or got >= total:
        return False, 0.0, 0.0  # 长度未知 / 已收完 / 零字节：都不判
    if elapsed < probe_sec and got < probe_bytes:
        return False, 0.0, 0.0  # 样本太小，不下结论
    rate = got / elapsed if elapsed > 0 else float("inf")
    floor = _min_rate() if floor_rate is None else floor_rate
    remain_sec = (total - got) / rate if rate > 0 else float("inf")
    return (rate < floor and remain_sec > budget_sec), rate, remain_sec


class BootBodyError(Exception):
    """引导期大 body 传输失败——**正文带已收字节/速率/原因**。

    为什么要有正文：上游只把 `repr(e)` 或 `type(e).__name__` 写进日志，裸
    `TimeoutError()` 打出来等于什么都没说（与 worker 侧 2026-09-20 事故同一条教训）。
    """

    def __init__(
        self,
        what: str,
        bytes_read: int = 0,
        total: int = 0,
        elapsed: float = 0.0,
        rate: float = 0.0,
    ) -> None:
        detail = f"已收 {bytes_read} bytes" + (f" / 共 {total}" if total else "")
        if elapsed:
            detail += f"，{elapsed:.0f}s（{rate / 1024:.0f} KB/s）"
        super().__init__(f"{what}：{detail}")
        self.bytes_read = bytes_read
        self.total = total
        self.elapsed = elapsed
        self.rate = rate


class BootSlowError(BootBodyError):
    """慢连接（速率远低于阈值）⇒ 放弃本次传输、换连接重抽。"""

    def __init__(
        self, bytes_read: int, total: int, elapsed: float, rate: float, remain_sec: float
    ) -> None:
        super().__init__("慢连接（换连接重抽）", bytes_read, total, elapsed, rate)
        self.remain_sec = remain_sec


def _progress_logger(label: str, log: Any):
    """进度行工厂：`code.zip 下载中 0.75 MB / 1.37 MB (54%) 用时 61s（13 KB/s）`。"""

    def _report(got: int, total: int, elapsed: float) -> None:
        mb = 1024.0 * 1024.0
        rate = (got / elapsed / 1024.0) if elapsed > 0 else 0.0
        pct = f" ({got * 100 // total}%)" if total > 0 else ""
        of = f" / {total / mb:.2f} MB" if total > 0 else ""
        log(f"{label} 下载中 {got / mb:.2f} MB{of}{pct} 用时 {elapsed:.0f}s（{rate:.0f} KB/s）")

    return _report


def _read_body(
    resp: Any,
    *,
    idle_timeout: float,
    total_timeout: float | None,
    progress: Any = None,
    allow_reroll: bool = False,
) -> bytes:
    """分块读 body：报进度 + **停滞/超预算即抛**（异常正文带已收字节数与原因）。"""
    total = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
    except Exception:  # 假响应（测试）/ 非标准 headers：拿不到长度照样读
        total = 0
    buf = bytearray()
    t0 = time.time()
    last = t0
    probed = False
    while True:
        try:
            block = resp.read(BOOT_CHUNK)
        except (TimeoutError, OSError) as e:
            raise BootBodyError(
                f"body 停滞（{idle_timeout:.0f}s 内没有新字节）",
                len(buf),
                total,
                time.time() - t0,
            ) from e
        if not block:
            return bytes(buf)
        buf += block
        now = time.time()
        if allow_reroll and not probed:
            # 只在**首块**判一次（probed 一次性）：每次重抽的浪费 ≤ 一块，
            # 绝不会退化成「再整份重传一遍」。
            probed = True
            should, rate, remain = _reroll_decision(len(buf), total, now - t0)
            if should:
                raise BootSlowError(len(buf), total, now - t0, rate, remain)
        if total_timeout is not None and now - t0 > total_timeout:
            raise BootBodyError(
                f"body 超预算（>{total_timeout:.0f}s）", len(buf), total, now - t0
            )
        if progress is not None and now - last >= BOOT_PROGRESS_MIN_SEC:
            last = now
            progress(len(buf), total, now - t0)


def fetch_guarded(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    log: Any = None,
    label: str = "",
    idle_timeout: float = BOOT_IDLE_TIMEOUT_SEC,
    total_timeout: float | None = None,
    attempts: int = 3,
    reroll: bool = True,
    opener: Any = None,
) -> bytes:
    """GET 一个**大 body**：分块读 + 进度行 + 停滞/超预算即断 + 低速**有界重抽**。

    幂等 GET 专用（引导期取 code.zip / 任务包都是「再取一次不改变服务端状态」）。
    重抽**不退避**（它的价值就在快）；`attempts` 的**最后一次必然老老实实传完**——
    6 KB/s 的坏签确实存在，重抽是赌，不能把赌注全压在赌上。

    `HTTPError` 原样上抛：401/404 是**确定性的答案**，不是传输抖动，由调用方按状态码处置
    （引导期那两处要能区分「token 不对」与「网络抽签」）。

    成功时打一行可 grep、可对账的传输账（与 worker 侧 `job X: wire …` 同族形状）：

        wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0
    """
    do_log = log or (lambda _m: None)
    open_fn = opener or urllib.request.build_opener()
    name = label or url
    rerolls = 0
    last_err: BootBodyError | None = None
    for attempt in range(1, attempts + 1):
        allow_reroll = reroll and attempt < attempts and rerolls < BOOT_REROLL_MAX
        req = urllib.request.Request(url, headers=headers or {})
        t0 = time.time()
        try:
            with open_fn.open(req, timeout=idle_timeout) as resp:
                body = _read_body(
                    resp,
                    idle_timeout=idle_timeout,
                    total_timeout=total_timeout,
                    progress=_progress_logger(name, do_log),
                    allow_reroll=allow_reroll,
                )
        except BootSlowError as e:
            rerolls += 1
            do_log(
                f"wire: re-roll #{rerolls}/{BOOT_REROLL_MAX} {name}: "
                f"实测 {e.rate / 1024:.0f} KB/s < 阈值 {_min_rate() / 1024:.0f} KB/s"
                f"（按此速率剩余 {e.remain_sec:.0f}s）——断开重发（已收 {e.bytes_read} bytes 作废）"
            )
            continue  # 立即换连接重抽（不退避）
        except BootBodyError as e:
            last_err = e
        else:
            elapsed = max(time.time() - t0, 1e-9)
            _note_rate(len(body) / elapsed, len(body))
            mb = 1024.0 * 1024.0
            do_log(
                f"wire: {name} {len(body) / mb:.2f}MB/{elapsed:.1f}s"
                f"({len(body) / elapsed / 1024.0:.0f}KB/s) attempts={attempt} rerolls={rerolls}"
            )
            return body
        if attempt < attempts:
            backoff = min(2**attempt, 8)
            do_log(f"{name}: 传输失败（{last_err}）— {backoff}s 后第 {attempt + 1}/{attempts} 次重试")
            time.sleep(backoff)
    if last_err is not None:
        raise last_err
    raise BootBodyError(f"{name} 重抽 {rerolls} 次后仍无可用的尝试")


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
                # 编码必填：同上（本模块独立拉取，不能走 common.proc）。
                c = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=40,
                )
                log(f"  {label} {t} -> code={c.stdout.strip()} err={c.stderr.strip()[:160]}")
            except Exception as e:
                log(f"  {label} {t} -> {type(e).__name__}: {e}")
        log("  code=000 ⇒ 连不上：hub 只绑 127.0.0.1（hub_server.py --host 默认已是 0.0.0.0，"
            "确认进程是新启的）/ 本机防火墙拦 8787 入站 / 对端离线")
    mode = "userspace（代理已注入）" if os.environ.get("HTTP_PROXY") else "kernel/未注入代理"
    log(f"（本机 IP {self_ip() or '(无)'}，模式 {mode}；诊断结束）")
