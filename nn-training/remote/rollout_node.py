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
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from common.env_probe import probe_cloud_env, worker_name
from common.proc import run_capture
from remote import tailscale_boot

# 注册客户端住在 `remote/push_register.py`（2026-10-23，plan/push-mode-notebooks §4.3）：
# 采样、GPU、离线三条腿共用同一份退避梯子/错误分类/★片段实现。这里 import 进来并**保留同名
# 绑定**（`X as X` = 显式 re-export），`tests/remote/test_rollout_node.py` 的
# `rn.build_register_payload(...)` / `rn.classify_error(...)` 等用法不变。
from remote.push_register import build_register_payload as build_register_payload
from remote.push_register import classify_error as classify_error
from remote.push_register import register as register
from remote.push_register import should_register as should_register
from remote.push_register import unregister as unregister
from remote.push_tunnel import tunnel_url_of as _tunnel_url
from remote.rollout_tunnel import PROBE_SEC as TUNNEL_PROBE_SEC
from remote.rollout_tunnel import dead_reason as _dead_reason
from remote.rollout_tunnel import dump_tail as _dump_tail
from remote.rollout_tunnel import public_ping as _public_ping
from remote.rollout_tunnel import reset_log as _reset_cf_log
from remote.rollout_tunnel import retry_due as _tunnel_retry_due
from remote.rollout_tunnel import tunnel_dead as _tunnel_dead
from remote.rollout_tunnel import wait_url as _wait_tunnel_url

#: 通道名（与 hub/nodes 注册载荷里的 url 形态对应）。
LINK_TAILSCALE = "tailscale"
LINK_CLOUDFLARED = "cloudflared"

#: 退避上限（net/server 类失败）。
RETRY_MAX_SEC = 300.0
RETRY_FIRST_SEC = 30.0

#: cloudflared 自救（plan/rollout-tunnel-url-retry）：每次 attempt 是新进程，最多 3 次；
#: 第 3 次回到首选姿势并开 debug，把「死因」打出来。
CLOUDFLARED_ATTEMPTS = 3
CLOUDFLARED_RETRY_SEC = 5.0
CLOUDFLARED_PROBE_TIMEOUT = 3.0
#: 首拍失败后的「补拿」节拍与监护循环里的等待上限（不能每拍阻塞 90s）。
TUNNEL_RETRY_SEC = 60.0
TUNNEL_RETRY_WAIT_SEC = 30.0

#: 「近 30 分钟产出」摘要的打印节拍（用户 2026-10-18：「rollout.ipynb 及 sampler-agent 的 log
#: 无法观察最近贡献情况，请持续统计半小时滑动窗口产出，每十分钟打印一行」）。
#: 窗口计算在 agent 侧（`tools/agent/output-window.ts`，那里才有逐局完成时刻）——本模块
#: **只负责把那一行搬进 notebook/rollout-node 日志**，第二个数字源就是第二个真相。
OUTPUT_STATUS_EVERY_SEC = 600.0

#: 受监护启动标记：与 `tools/agent/restart-guard.ts` 的 `SUPERVISED_ENV` 逐字对齐。
#: 有它时 agent 的 `/v1/restart` 只退出、不 spawn detached child —— 由本模块的监护循环
#: 从盘上重新拉起（notebook 启动链没有控制台那套自交接 owner，抢端口就会把节点打崩）。
AGENT_SUPERVISED_ENV = "SAMPLER_SUPERVISED"
#: 受监护重启的退出码（EX_TEMPFAIL）：计划内重启，不计入 `restart_limit` 的崩溃预算。
AGENT_RESTART_EXIT_CODE = 75

#: bun 二进制镜像（npmmirror）。GitHub Releases 直连不通的环境（阿里云 DSW 等）的兜底：
#: 目录布局 `.../bun/bun-v<版本>/bun-linux-x64.zip`，与官方 install.sh 下载的产物同名。
BUN_MIRROR_DEFAULT = "https://registry.npmmirror.com/-/binary/bun"

#: 各云平台**跨会话持久化**的根目录（按优先级探测，第一个存在者胜）。系统盘上的
#: `~/.bun/bin`、`/usr/local/bin`、PATH 在 DSW 停机/换对话后会被重置 ⇒ 下载成功的二进制
#: 落在这里才免得了下次重下（用户 2026-11-07：DSW 下 bun/cloudflared 反复下载甚至超时）。
#:   DSW=/mnt/workspace（NAS，停机/释放仍在）· AI Studio=/home/aistudio ·
#:   Kaggle=/kaggle/working · Colab=/content（运行时重连内保留）。
PERSISTENT_ROOTS: tuple[str, ...] = (
    "/mnt/workspace",
    "/home/aistudio",
    "/kaggle/working",
    "/content",
)
#: 持久根下的缓存子目录名（bun / cloudflared 两个二进制平铺在里面）。
BIN_CACHE_SUBDIR = ".battle-bin"

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


# ---------------------------------------------------------------- 进程/网络小工具

def _sh(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
):
    """跑一条命令；启动失败/超时 ⇒ rc=127 的哑结果（与旧 cell 同语义，不抛）。

    走 `common.proc.run_capture`：**不写裸 `text=True`**（控制台代码页会解码失败 ⇒ 输出变
    `None`，`tests/common/test_common_layer.py::test_every_production_capture_site_pins_encoding` 钉住）。
    `env` 只给需要单次改写环境变量的下载用（例：bun 安装器认 `GITHUB` 基址）。
    """
    try:
        return run_capture(cmd, cwd=cwd, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return SimpleNamespace(returncode=127, stdout="", stderr=str(e))


def _public_net() -> Any:
    """公网下载上下文：还原 ensure 之前的代理环境（没有注入时是空操作）。"""
    return tailscale_boot.platform_net_env()


def _direct_env() -> dict[str, str]:
    """给 cloudflared 子进程的「直连」环境：摘掉代理键，但保留 NO_PROXY 的本地回环。

    为什么不能用 `platform_net_env()`：它只在 tailscale `set_proxy_env()` 跑过之后才有
    还原对象；cf-only 路径（没有 TS_AUTHKEY）里它是空操作，平台代理仍在。
    为什么不能连 `NO_PROXY` 一起清：cloudflared 还要访问 `http://localhost:<port>`，
    没有 bypass 条目时它可能被塞进代理。
    """
    env = dict(os.environ)
    for key in tailscale_boot.PROXY_ENV_KEYS:
        if key.lower() != "no_proxy":
            env.pop(key, None)
    bypass = env.get("NO_PROXY") or env.get("no_proxy") or ""
    parts = [p for p in ("localhost", "127.0.0.1", "::1", bypass) if p]
    env["NO_PROXY"] = ",".join(dict.fromkeys(parts))
    env["no_proxy"] = env["NO_PROXY"]
    return env


def _probe_https(
    posture: str,
    host: str = "api.trycloudflare.com",
    timeout: float = CLOUDFLARED_PROBE_TIMEOUT,
) -> bool:
    """代理感知的 HTTPS 可达性：`direct` 显式不挂代理，`inherit` 用进程当前代理。

    为什么不用 `socket.create_connection`：原始 socket 不读 `HTTP(S)_PROXY`，
    `inherit` 与 `direct` 的结果必然相同（plan/rollout-tunnel-url-retry B3）。
    """
    handlers = [urllib.request.ProxyHandler({})] if posture == "direct" else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(f"https://{host}/", method="HEAD")
    try:
        with opener.open(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # 404/405 也说明 DNS/TLS/代理链通了
    except Exception:
        return False


def _probe_tcp(
    host: str, port: int, timeout: float = CLOUDFLARED_PROBE_TIMEOUT
) -> bool:
    """原始 TCP 可达性（不看代理；只作边缘 7844 的诊断，不参与姿势选择）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _net_probe(log: Callable[[str], None]) -> tuple[str, str]:
    """一次预检：用代理感知的 HTTPS 选首选姿势，原始 TCP 只诊断边缘 7844。"""
    inherit = _probe_https("inherit")
    direct = _probe_https("direct")
    edge = _probe_tcp("region1.v2.argotunnel.com", 7844)
    log(
        "预检: api.trycloudflare.com:443 "
        f"inherit={'ok' if inherit else 'fail'} direct={'ok' if direct else 'fail'} "
        f"｜ 边缘7844 tcp={'ok' if edge else 'fail'}"
    )
    if inherit:
        log("⇒ 采用 inherit 姿势")
        return "inherit", "direct"
    if direct:
        log("⇒ inherit 不通、direct 可达 ⇒ 先试 direct 姿势")
        return "direct", "inherit"
    log(
        "⇒ 公网不可达：api.trycloudflare.com:443 两种姿势都不通（检查出网/DNS）；"
        "仍按 inherit 尝试"
    )
    return "inherit", "direct"


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


def _slot_count(explicit: int) -> tuple[int, str]:
    """并发槽位：显式值 > `physical_cores()` **全核**（采样节点不预留，用户 2026-11-04）。

    与训练侧 `cpu_worker_slots()`（留 2 核给数据回传）分家：采样节点只跑采样/评估，
    满核才够用；8 物理核机器起 8 个 worker。

    ★ 2026-11-11 用**物理核**（不是 `effective_cores()`）：口径必须与 agent 自己的
      `CPUS = physicalCores()`（`tools/lib/cores.ts`）一致——同一个节点两套口径就会
      出现「笔记本按 4 开池、agent 自报 2 核」的超额认购（kaggle 实测，父进程 HTTP
      被饿到 30~60s）。HT sibling 只共享执行单元与 L1/L2，每个 worker 是 ~75–100MB
      的重型进程，按物理核铺才是「满核」。
    """
    cores = os.cpu_count() or 1
    try:
        from common.platform_utils import effective_cores, physical_cores

        cores = min(effective_cores(), physical_cores())
        note = f"platform_utils 物理核={cores} 全核"
    except Exception as e:  # 拉不起 platform_utils 时的兜底：同样用满
        note = f"内联回退({type(e).__name__}) cores={cores}"
    if int(explicit or 0) > 0:
        return int(explicit), note + "（CFG.workers 显式）"
    return int(cores), note


#: 内置 GitHub 镜像（前缀式）：直连不通时**自动**依次尝试，不需要填任何开关。
GITHUB_MIRRORS = (
    "https://gh-proxy.com/https://github.com",
    "https://ghproxy.net/https://github.com",
)


def _explicit_github_base(cfg: dict) -> str:
    base = str(cfg.get("github_base") or os.environ.get("GITHUB_BASE") or "").strip().rstrip("/")
    return base


def _github_candidates(cfg: dict, url: str) -> list[str]:
    """`url` 的候选地址：显式镜像 → 直连 → 内置镜像（去重保序）。

    没配 `github_base` 也含内置镜像 —— 「填开关」不该是联网的前提（阿里云 DSW 现场）。
    镜像只对公开仓/公开产物有效（第三方加速不转发认证）；私有仓走 cell 的 `code_zip`。
    """
    if not url.startswith("https://github.com"):
        return [url]
    rest = url[len("https://github.com"):]
    base = _explicit_github_base(cfg)
    out = [base + rest] if base else [url]
    if base:
        out.append(url)
    out.extend(m + rest for m in GITHUB_MIRRORS)
    return list(dict.fromkeys(out))


def _speed_probe(url: str, cap_bytes: int = 262_144, timeout: int = 6) -> float:
    """拉一小段估算吞吐（B/s）；不可达/测不出 ⇒ 0。

    HEAD 只能测延迟，不代表带宽 —— 这里用 Range 取前 256KB，按 `size/time` 排。
    Range 不被支持时会退化成小文件下载，仍以实际字节/耗时计。
    """
    r = _sh(
        [
            "curl",
            "-fsSL",
            "--range",
            f"0-{cap_bytes - 1}",
            "--connect-timeout",
            "4",
            "--max-time",
            str(timeout),
            "-o",
            os.devnull,
            "-w",
            "%{size_download} %{time_total}",
            url,
        ],
        timeout=timeout + 3,
    )
    if getattr(r, "returncode", 1) != 0:
        return 0.0
    try:
        size_s, sec_s = str(getattr(r, "stdout", "")).split()[-2:]
        size, secs = float(size_s), float(sec_s)
    except (AttributeError, IndexError, ValueError):
        return 0.0
    return size / secs if size > 0 and secs > 0 else 0.0


def _rank_fastest(
    items: list[tuple[str, str]], label: str, log: Callable[[str], None]
) -> list[str]:
    """并行测速并按吞吐降序返回候选键；测不出的保持原序殿后。

    `items` = `[(键, 测速 URL)]` —— 键用于后续定位候选（cloudflared 是 URL 本身，
    bun 是 `GITHUB` 基址）。并行是契约：墙掉的链路不该让后面的候选排队白等。
    """
    if not items:
        return []
    if len(items) == 1:
        return [items[0][0]]
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        speeds = list(pool.map(lambda it: _speed_probe(it[1]), items))
    order = sorted(range(len(items)), key=lambda i: (-speeds[i], i))
    log(
        f"{label} 测速: "
        + " · ".join(f"{items[i][0] or '直连'} {speeds[i] / 1e6:.1f}MB/s" for i in order)
    )
    return [items[i][0] for i in order]


#: 下载重试：每轮把候选按序全试一遍，全挂则退避后重来（DSW 出网抖动是常态）。
DOWNLOAD_ROUNDS = 3
DOWNLOAD_BACKOFF: tuple[float, ...] = (2.0, 5.0)


def _retry_delay(rnd: int) -> float:
    return DOWNLOAD_BACKOFF[min(rnd, len(DOWNLOAD_BACKOFF) - 1)] if DOWNLOAD_BACKOFF else 0.0


def _download_retry(
    urls: list[str],
    dest: str | Path,
    log: Callable[[str], None],
    *,
    label: str = "下载",
    rounds: int | None = None,
    max_time: int = 180,
) -> str:
    """按候选 curl 下载到 `dest`：失败顺延，一轮全挂退避重试；成功返回所用 URL。"""
    total = DOWNLOAD_ROUNDS if rounds is None else max(1, rounds)
    for rnd in range(total):
        for url in urls:
            r = _sh(
                [
                    "curl",
                    "-fsSL",
                    "--retry",
                    "2",
                    "--retry-delay",
                    "1",
                    "--retry-connrefused",
                    "--connect-timeout",
                    "8",
                    "--max-time",
                    str(max_time),
                    url,
                    "-o",
                    str(dest),
                ],
                timeout=max_time + 20,
            )
            if r.returncode == 0:
                return url
            log(f"{label} 下载失败（第 {rnd + 1}/{total} 轮）: {url}")
        if rnd + 1 < total:
            delay = _retry_delay(rnd)
            log(f"{label} 本轮全部源失败 —— {delay:g}s 后重试（第 {rnd + 2}/{total} 轮）")
            if delay > 0:
                time.sleep(delay)
    return ""


def _bun_install_bases(cfg: dict) -> list[str]:
    """官方 install.sh 可用的 `GITHUB` 基址（"" = 直连），**未测速排序**。

    显式 `github_base` 最优先；其余 = 直连 + 内置镜像。真正的先后由
    `_ensure_bun` 里的并行测速决定（快者先装）。
    """
    base = _explicit_github_base(cfg)
    out = ([base] if base else []) + ["", *GITHUB_MIRRORS]
    return list(dict.fromkeys(out))


def _bun_target(machine: str, cpuinfo: str = "") -> str:
    """bun 的发布包名（`bun-linux-x64[-baseline]` / `bun-linux-aarch64`）；不认识的架构 ⇒ ""。"""
    m = str(machine or "").lower()
    if m in ("x86_64", "amd64"):
        # 与官方 install.sh 同规则：CPU 无 AVX2 ⇒ baseline 包
        return "bun-linux-x64" + ("-baseline" if cpuinfo and "avx2" not in cpuinfo else "")
    if m in ("aarch64", "arm64"):
        return "bun-linux-aarch64"
    return ""


def _bun_asset_url(base: str, target: str) -> str:
    """bun 测速用的代表产物（latest release 的同名 zip）；`base` 为空 = 直连。"""
    rest = f"/oven-sh/bun/releases/latest/download/{target or 'bun-linux-x64'}.zip"
    return (base + rest) if base else "https://github.com" + rest


def _latest_bun_version(listing: str) -> str:
    """npmmirror 目录 JSON 里挑最新稳定版（`bun-v1.4.4/` → `1.4.4`）；没有 ⇒ ""。"""
    best: tuple[int, ...] = ()
    for name in re.findall(r"bun-v([0-9]+(?:\.[0-9]+)+)/", listing):
        key = tuple(int(x) for x in name.split("."))
        if key > best:
            best = key
    return ".".join(str(x) for x in best)


def _install_bun_mirror(cfg: dict, log: Callable[[str], None]) -> str:
    """从 npmmirror 装 bun（`_public_net()` 内调用）；返回 bun 路径，失败 ⇒ ""。"""
    base = str(
        cfg.get("bun_mirror") or os.environ.get("BUN_MIRROR") or BUN_MIRROR_DEFAULT
    ).strip().rstrip("/")
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        cpuinfo = ""
    target = _bun_target(platform.machine(), cpuinfo)
    if not target:
        log(f"bun 镜像：不认识的架构 {platform.machine()} —— 跳过")
        return ""
    try:
        with urllib.request.urlopen(f"{base}/", timeout=60) as r:
            ver = _latest_bun_version(r.read().decode("utf-8", "replace"))
        if not ver:
            raise ValueError("目录里没有 bun-v<版本>/")
        url = f"{base}/bun-v{ver}/{target}.zip"
        log(f"bun 镜像下载: {url}")
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "bun.zip"
            if not _download_retry([url], zp, log, label="bun 镜像", rounds=2, max_time=600):
                raise RuntimeError("镜像二进制下载失败")
            with zipfile.ZipFile(zp) as z:
                z.extractall(td)
            src = Path(td) / target / "bun"
            if not src.is_file():
                raise FileNotFoundError(f"zip 里没有 {target}/bun")
            dest = Path.home() / ".bun" / "bin"
            dest.mkdir(parents=True, exist_ok=True)
            final = dest / "bun"
            shutil.copyfile(src, final)
            final.chmod(0o755)
            return str(final)
    except Exception as e:
        log(f"bun 镜像安装失败（{type(e).__name__}: {e}）")
        return ""


def _bin_cache_dir(cfg: dict, *, isdir: Callable[[str], bool] | None = None) -> Path | None:
    """持久二进制缓存目录：显式 `CFG.bin_cache_dir` / `BATTLE_BIN_CACHE` 优先；否则按
    `PERSISTENT_ROOTS` 自动探测（第一个存在者）。都没有 ⇒ None（= 无缓存，行为同今天）。

    零配置（用户 2026-11-07）：DSW/Kaggle/Colab/AI Studio 各自探测自己的持久根，不需要填开关；
    显式口子留给持久盘挂在别处（如 /mnt/data）或想共用一份缓存的人。`isdir` 是单测注入点。
    """
    explicit = str(cfg.get("bin_cache_dir") or os.environ.get("BATTLE_BIN_CACHE") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    check = os.path.isdir if isdir is None else isdir
    for root in PERSISTENT_ROOTS:
        if check(root):
            return Path(root) / BIN_CACHE_SUBDIR
    return None


def _cached_bin(cache: Path | None, name: str, log: Callable[[str], None]) -> str:
    """缓存里**可执行且 `--version` 通过**的二进制路径；无缓存/坏档/架构不符 ⇒ ""。

    校验靠真跑 `--version`：缓存可能是上个架构的机器留下的、或半截下载 ⇒ 不能只看存在就用。
    """
    if cache is None:
        return ""
    cand = cache / name
    if not (cand.is_file() and os.access(cand, os.X_OK)):
        return ""
    if _sh([str(cand), "--version"], timeout=60).returncode != 0:
        log(f"缓存 {name} 校验失败（坏档或架构不符）—— 忽略，重新下载")
        return ""
    log(f"复用持久缓存 {name}: {cand}（跳过下载）")
    return str(cand)


def _store_bin(cache: Path | None, name: str, src: str | Path, log: Callable[[str], None]) -> None:
    """把下载/安装成功的二进制复制进持久缓存（best-effort，**绝不致命**）。

    `src` 可能只是 PATH 上的名字（"bun"）⇒ 先 `shutil.which` 解析成真实文件。源就是缓存自身
    （命中复用）时跳过，避免 SameFileError。写不进（只读盘/配额）只记一行，不影响本次运行。
    """
    if cache is None:
        return
    try:
        real = Path(shutil.which(str(src)) or str(src))
        if not real.is_file():
            return
        dest = cache / name
        if real.resolve() == dest.resolve():
            return
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real, dest)
        dest.chmod(0o755)
        log(f"已缓存 {name} → {dest}（下次对话直接复用）")
    except OSError as e:
        log(f"缓存 {name} 写入失败（{type(e).__name__}）—— 不影响本次运行")


def _ensure_bun(code_dir: Path, cfg: dict, log: Callable[[str], None]) -> str:
    """找/装 bun（★ 公网安装在 ensure 之前发生；这里仍包 `_public_net()` 兜底）。

    GitHub 直连不通时**自动**换路（不需要任何配置）：官方 install.sh 带
    `GITHUB=<镜像>` 重跑（它认这个环境变量），仍失败 ⇒ 回落 npmmirror 二进制。
    候选先用 256KB 小样**并行测速**，官方安装按实测快慢依次试。
    """

    cache = _bin_cache_dir(cfg)

    def _find() -> str:
        cached = _cached_bin(cache, "bun", log)
        if cached:
            return cached
        for cand in ("bun", "/root/.bun/bin/bun", str(Path.home() / ".bun" / "bin" / "bun")):
            if _sh([cand, "--version"], timeout=120).returncode == 0:
                return cand
        return ""

    bun = _find()
    if not bun:
        log("安装 bun…（公网下载；候选并行测速后按快慢排序）")
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            cpuinfo = ""
        target = _bun_target(platform.machine(), cpuinfo)
        with _public_net():
            bases = _rank_fastest(
                [(b, _bun_asset_url(b, target)) for b in _bun_install_bases(cfg)],
                "bun",
                log,
            )
        for rnd in range(DOWNLOAD_ROUNDS):
            for base in bases:
                env = None
                if base:
                    log(f"bun 安装经镜像: {base}")
                    env = os.environ.copy()
                    env["GITHUB"] = base
                with _public_net():
                    _sh(
                        ["bash", "-lc", "curl -fsSL https://bun.sh/install | bash"],
                        timeout=600,
                        env=env,
                    )
                bun = _find()
                if bun:
                    break
            if bun:
                break
            if rnd + 1 < DOWNLOAD_ROUNDS:
                delay = _retry_delay(rnd)
                log(f"bun 官方安装全部源失败 —— {delay:g}s 后重试（第 {rnd + 2}/{DOWNLOAD_ROUNDS} 轮）")
                if delay > 0:
                    time.sleep(delay)
    if not bun:
        log("bun 官方安装失败 —— 回落 npmmirror 二进制镜像")
        with _public_net():
            bun = _install_bun_mirror(cfg, log)
    if not bun:
        return ""
    _store_bin(cache, "bun", bun, log)
    bin_dir = str(Path(bun).parent)
    if bin_dir and bin_dir != "." and bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    if cfg.get("bun_install_deps"):
        r = _sh([bun, "install", "--frozen-lockfile"], cwd=str(code_dir), timeout=900)
        log(f"bun install rc={r.returncode}（agent 零第三方依赖，失败不致命）")
    return bun


def _find_cloudflared(cfg: dict, log: Callable[[str], None]) -> str:
    """已安装的 cloudflared（显式 → PATH → 常见目录）。

    不走 `bash -lc 'command -v ...'`：DSW 的登录 shell 会先打一屏横幅，stdout 非空 ≠ 找到了
    二进制 —— 旧实现把横幅末行当路径，Popen 报 `OSError: File name too long`（2026-11-04）。
    """
    explicit = str(cfg.get("cloudflared_path") or "").strip()
    cands = [os.path.expanduser(explicit)] if explicit else []
    cached = _cached_bin(_bin_cache_dir(cfg), "cloudflared", log)
    if cached:
        cands.append(cached)
    found = shutil.which("cloudflared")
    if found:
        cands.append(found)
    cands += [
        "/usr/local/bin/cloudflared",
        str(Path.home() / ".local" / "bin" / "cloudflared"),
        str(Path.home() / "bin" / "cloudflared"),
    ]
    for cand in dict.fromkeys(cands):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    if explicit:
        log(f"cloudflared_path 不是可执行文件: {explicit}")
    return ""


def _start_cloudflared(
    cfg: dict,
    port: int,
    cf_log: Path,
    log: Callable[[str], None],
    *,
    protocol: str | None = "http2",
    edge_ip: str | None = "4",
    debug: bool = False,
    env_posture: str = "inherit",
) -> Any:
    """起 cloudflared quick tunnel；二进制缺失时**在 `_public_net()` 里**下载（F5）。

    `protocol` / `edge_ip` 为 None = 不传，交给 cloudflared 自选（第 3 次 attempt 用）；
    参数顺序与 `hub/tunnel_ab_probe.py::_start_tunnel` 同形，避免两套写法漂移。
    """
    cf = _find_cloudflared(cfg, log)
    if not cf:
        cf = "/usr/local/bin/cloudflared"
        log("安装 cloudflared…（公网下载，代理已还原）")
        with _public_net():
            direct = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            # 自动换路（不填开关）：候选并行测速后按实测快慢依次下载；全挂退避重试
            got = _download_retry(
                _rank_fastest(
                    [(u, u) for u in _github_candidates(cfg, direct)], "cloudflared", log
                ),
                cf,
                log,
                label="cloudflared",
            )
        if got:
            if got != direct:
                log(f"cloudflared 已从镜像下载: {got}")
            os.chmod(cf, 0o755)
            _store_bin(_bin_cache_dir(cfg), "cloudflared", cf, log)
        else:
            log("cloudflared 安装失败（直连与镜像都不通）—— 可预置二进制后用 CFG.cloudflared_path 指路")
            return None
    cmd = [cf, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"]
    if protocol:
        cmd += ["--protocol", protocol]
    if edge_ip:
        cmd += ["--edge-ip-version", edge_ip]
    if debug:
        cmd += ["--loglevel", "debug"]
    cmd += ["--logfile", str(cf_log)]
    env = _direct_env() if env_posture == "direct" else None
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except OSError as e:
        log(f"cloudflared 启动失败: {e}")
        return None


def _ensure_cloudflared_url(
    cfg: dict,
    port: int,
    cf_log: Path,
    log: Callable[[str], None],
    *,
    attempts: int = CLOUDFLARED_ATTEMPTS,
    postures: tuple[str, ...] | None = None,
    wait_timeout: float = 90.0,
) -> tuple[str, Any]:
    """起 cloudflared 并拿 URL；每次 attempt 都是**新进程**，失败带 rc/死因。

    阶梯（plan/rollout-tunnel-url-retry §2.6-B）：
    1. 预检首选姿势 + http2 + v4；
    2. 另一姿势 + http2 + v4；
    3. **回到首选姿势** + 不指定 protocol/edge（交给 cloudflared 自选）+ debug。

    第 3 次回到首选而不是重复第 2 次，是为了让 debug 落在「最可能成功」的姿势上。
    URL 出现但进程已退出 ⇒ 不采用（死域名会被注册成 530/422，见 §2.2）。
    """
    prefs = tuple(postures or ("inherit", "direct"))
    preferred = prefs[0] or "inherit"
    other = prefs[1] if len(prefs) > 1 else ("direct" if preferred == "inherit" else "inherit")
    ladder: tuple[tuple[str, str | None, str | None, bool], ...] = (
        (preferred, "http2", "4", False),
        (other, "http2", "4", False),
        (preferred, None, None, True),
    )
    total = max(1, int(attempts))
    for i in range(1, total + 1):
        posture, protocol, edge_ip, debug = ladder[min(i - 1, len(ladder) - 1)]
        _reset_cf_log(cf_log, i)
        t0 = time.time()
        cfp = _start_cloudflared(
            cfg,
            port,
            cf_log,
            log,
            protocol=protocol,
            edge_ip=edge_ip,
            debug=debug,
            env_posture=posture,
        )
        if cfp is None:
            log("cloudflared 不可用（二进制缺失/启动失败）—— 不再重试")
            return "", None
        url = _wait_tunnel_url(
            cf_log, cfp, log, timeout=wait_timeout, attempt=(i, total)
        )
        alive = cfp.poll() is None
        if url and alive:
            flag = protocol or "auto"
            log(f"cloudflared 第 {i}/{total} 次就绪（{posture} + {flag}）")
            return url, cfp
        elapsed = time.time() - t0
        rc = cfp.poll()
        if rc is None:
            log(f"cloudflared 第 {i}/{total} 次未就绪（进程仍在连接，{elapsed:.0f}s）")
        else:
            log(
                f"cloudflared 第 {i}/{total} 次未就绪"
                f"（进程已退出 rc={rc}，存活 {elapsed:.0f}s）"
            )
        if url:
            log(f"  日志里有 URL 但进程已退出：{url}（不采用）")
        log(f"  死因: {_dead_reason(cf_log)}")
        _dump_tail(cf_log, log, 6)
        _terminate(cfp)
        if i < total:
            time.sleep(CLOUDFLARED_RETRY_SEC * i)
    return "", None


def _open_cloudflared_once(
    *,
    current_url: str,
    cfp: Any,
    ensure: Callable[[], tuple[str, Any]],
) -> tuple[str, Any, bool]:
    """已有**存活** cf 进程则复用；否则跑一轮。返回 `(url, cfp, started)`。

    ★ 判据是 `cfp` 而不是 `url`：422 回落时 `current_url` 是**非空的 tailscale URL**，
    但此刻必须起 cloudflared（plan/rollout-tunnel-url-retry B1）。
    ★ 死进程对象（`poll()` 非 None）必须当作「没有」：否则静默死后再也建不起新隧道
    （plan/rollout-node-tunnel-death-blindspot §3.4）。
    """
    if cfp is not None and cfp.poll() is None:
        return current_url, cfp, False
    url, new_cfp = ensure()
    if url:
        return url, new_cfp, True
    return current_url, None, False


def _open_tailscale(
    cfg: dict, ts_authkey: str, port: int, log: Callable[[str], None]
) -> tuple[str, bool]:
    """tailscale 引导；成功返回 `(url, True)`，失败返回 `("", False)`（不抛）。"""
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
            log(
                "⚠ userspace 模式：入站到本地端口不通（注册会被 hub ping 门拒 422）——"
                "注册失败会自动回落 cloudflared"
            )
        return url, True
    except Exception as e:
        log(f"tailscale 引导失败（{type(e).__name__}: {e}）")
        return "", False


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
    env = dict(os.environ)
    env[AGENT_SUPERVISED_ENV] = "1"
    return subprocess.Popen(
        cmd, cwd=str(code_dir), stdout=fh, stderr=subprocess.STDOUT, env=env
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


def _status(
    port: int, auth: str = "", timeout: float = 5.0, urlopen: RegisterFn | None = None
) -> dict | None:
    """读本地 agent 的 `/v1/status`（同 `/v1/ping` 要鉴权）。**绝不抛**：拿不到就 None。

    为什么与 `_ping` 分开：探活是监护的关键路径（它决定「agent 判定死亡/重启」），
    而摘要是**可丢**的观测——两者共一个函数会让「摘要报错」被当成「节点不健康」。
    """
    opener = urlopen or urllib.request.urlopen
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/status")
        if auth:
            req.add_header("Authorization", f"Bearer {auth}")
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def emit_output_summary(port: int, auth: str, log: Callable[[str], None]) -> None:
    """打一行「近 30 分钟产出」（监护循环每 10 分钟调一次）。**绝不抛**。

    读 `/v1/status` 失败 ⇒ `output_line(None)` 那一档「不可知」：可丢的观测不该让监护循环炸，
    也不该被读成「节点不健康」（判活是 `_ping` 的职责）。
    """
    log(output_line(_status(port, auth)))


def output_line(status: dict | None) -> str:
    """把 `/v1/status` 的产出摘要拼成**一行**（空/旧 agent 如实说不可知）。

    分母、窗口、分列全在 agent 侧算好（`outputLine`）——这里不重算任何数字：
    notebook 日志与 `sampler-agent.log` 必须逐字相同，否则两个日志对账时先要怀疑口径。
    """
    if not isinstance(status, dict):
        return "近30min产出 不可知（agent 未应答）"
    line = status.get("outputLine")
    if isinstance(line, str) and line.strip():
        return f"近30min产出 {line.strip()}"
    # 旧 agent（没随本仓同步升级）不能编一个「窗口」出来：累计数冒充 30 分钟窗口是静默错数。
    return "近30min产出 不可知（旧 agent 无 outputLine）"


def _try_register_once(
    *,
    url: str,
    auth: str,
    node_id: str,
    workers: int,
    label: str,
    hub_url: str,
    hub_token: str,
    log: Callable[[str], None],
    urlopen: RegisterFn | None = None,
) -> dict:
    """注册一次；空 URL 是第一行硬闸（零 HTTP，也不写 `reg_state`）。

    `kind="nourl"` 是**本地**失败，调用方据此走「等隧道」而不是注册退避。
    """
    if not url:
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "no tunnel url",
            "kind": "nourl",
        }
    payload = build_register_payload(node_id, url, auth, workers, label)
    return register(urlopen or urllib.request.urlopen, hub_url, hub_token, payload, log)


def _banner_lines(
    *,
    node_id: str,
    url: str,
    auth: str,
    workers: int,
    bun_version: str,
    code_hash: str,
    hub_configured: bool,
    reg_state: dict[str, str],
) -> list[str]:
    """启动横幅的纯函数：空 URL 时**不**打印粘进去必 400 的 `nodes[]` 片段。"""
    lines = ["=" * 68]
    if hub_configured and reg_state.get("url"):
        lines.append(f"★ 已自动注册 : id={node_id}（rl-config.nodes[] 每轮热读，已生效）")
    elif url:
        lines.append("★ 未注册 —— 如需手工：写入本机 nn-training/rl-config.json：")
        lines.append(
            f'  {{"nodes":[{{"id":"{node_id}","url":"{url}","authKey":"{auth}"}}]}}'
        )
    else:
        lines.append(
            "★ 未注册：隧道 URL 未取到 —— 不要手工登记（空 url 会被 hub 400 拒）。"
        )
        lines.append("   节点每 60s 重试建隧道；真因看上面的 cloudflared 日志尾部。")
    lines.append(f"★ URL     : {url or '(未取到)'}")
    lines.append(f"★ authKey : {auth or '(未生成)'}")
    lines.append(
        f"  workers : {workers}  bun {bun_version}  codeHash={code_hash[:12]}"
    )
    lines.append("=" * 68)
    return lines


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
    forced_cf = str(cfg.get("link") or "").strip().lower() == LINK_CLOUDFLARED
    probe_postures: tuple[str, str] | None = None

    def _get_postures() -> tuple[str, str]:
        nonlocal probe_postures
        if probe_postures is None:
            probe_postures = _net_probe(log)
        return probe_postures

    def _open_cloudflared(reason: str) -> bool:
        nonlocal link, url, cfp
        log(f"cloudflared 通道（{reason}）")
        url, cfp, started = _open_cloudflared_once(
            current_url=url,
            cfp=cfp,
            ensure=lambda: _ensure_cloudflared_url(
                cfg,
                port,
                cf_log,
                log,
                attempts=CLOUDFLARED_ATTEMPTS,
                postures=_get_postures(),
            ),
        )
        if started:
            link = LINK_CLOUDFLARED
        return started

    if link == LINK_TAILSCALE:
        url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if not ok:
            log("—— 回落 cloudflared")
            link = LINK_CLOUDFLARED
    if link != LINK_TAILSCALE:
        _open_cloudflared("通道决策=cloudflared")
    if not url and not forced_cf and has_ts:
        log("cloudflared 未就绪 —— 回落 tailscale")
        url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if ok:
            link = LINK_TAILSCALE
    elif not url and forced_cf:
        log("强制通道=cloudflared，跳过 tailscale 回落")
    elif not url and not has_ts:
        log("无 ts_authkey ⇒ 本会话无法回落 tailscale")
    next_tunnel_try = time.time() + TUNNEL_RETRY_SEC if not url else 0.0

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
        out = _try_register_once(
            url=url,
            auth=auth,
            node_id=node_id,
            workers=workers,
            label=worker_name(env, "t" if link == LINK_TAILSCALE else "c"),
            hub_url=hub_url,
            hub_token=hub_token,
            log=log,
            urlopen=urllib.request.urlopen,
        )
        if out["ok"]:
            reg_state.update({"url": url, "authKey": auth})
            log(f"已注册节点 id={node_id} url={url} action={out['action']}")
        return out

    if hub_configured:
        out = _try_register()
        if not out["ok"] and int(out.get("status") or 0) == 422 and link == LINK_TAILSCALE:
            log("hub ping 门 422：tailscale 入站不可达 —— 回落 cloudflared 并重注册")
            if not _open_cloudflared("ping 门 422"):
                # B1：cf 全败时**不能**拿旧 tailscale URL 再发一次必然 422 的注册。
                hub_configured = False
                url = ""
                log(
                    "两种通道都不可用（tailscale 入站不通 + cloudflared 未建起）"
                    "—— 本会话不再自动注册"
                )
            else:
                out = _try_register()
                if not out["ok"] and int(out.get("status") or 0) == 422:
                    hub_configured = False
                    log(
                        "两种通道都不可用（tailscale 入站不通 + cloudflared 未建起）"
                        "—— 本会话不再自动注册"
                    )
        if not out["ok"] and hub_configured:
            if out["kind"] == "auth":
                hub_configured = False
                log("HUB_TOKEN 被拒（auth）——注册放弃：token 不对，重试无意义（检查 secret 后重跑）")
            elif out["kind"] == "nourl":
                next_tunnel_try = time.time() + TUNNEL_RETRY_SEC
                log("首拍未注册：隧道 URL 未取到 —— 等待补拿（不进入注册退避）")
            else:
                backoff = RETRY_FIRST_SEC
                next_retry = time.time() + backoff
                log(f"首拍注册失败（{out['kind']}）——{int(backoff)}s 后重试")

    # ── 6. 横幅（注册成功 = 自动；否则保留手工路径）───────────────────────
    for line in _banner_lines(
        node_id=node_id,
        url=url,
        auth=auth,
        workers=workers,
        bun_version=_sh([bun, "--version"]).stdout.strip(),
        code_hash=str((ping or {}).get("codeHash", "")),
        hub_configured=hub_configured,
        reg_state=reg_state,
    ):
        log(line)

    # ── 7. 监护：agent 死重启 / 隧道换名重注册 / 会话到点收线 ─────────────
    deadline = time.time() + max(0.0, float(cfg.get("max_session_hours") or 0)) * 3600
    restart_limit = int(cfg.get("restart_limit") or 5)
    restarts, tick, miss = 0, 0, 0
    # 「近 30 分钟产出」摘要：按**墙钟**而不是 tick 计数（sleep 漂移不会把 10 分钟变成 11 分钟）
    next_output_at = time.time() + OUTPUT_STATUS_EVERY_SEC
    # 隧道健康：进程退出每拍判；公网 URL 探针按墙钟节拍跑（给新隧道一个完整的传播窗口）。
    next_tunnel_probe = time.time() + TUNNEL_PROBE_SEC
    tunnel_probe_fails = 0
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
                planned = agent.returncode == AGENT_RESTART_EXIT_CODE
                if planned:
                    # 远控 /v1/restart：agent 已 pull 完并按受监护模式退出。重新拉起即加载新代码；
                    # 这是计划内重启，不能吃崩溃预算（否则几次正常升级就把节点停了）。
                    log("agent 按远控重启请求退出（受监护）—— 重新拉起加载新代码")
                else:
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
                    next_retry = 0.0
                    log("★ authKey 变了 —— 将重注册")
            now = time.time()
            # 隧道死亡检测（plan/rollout-node-tunnel-death-blindspot）：静默死的隧道 URL 不变、
            # 日志尾部仍是旧域名，既有「补拿/换名」两道闸都看不见。判死后归一成「无隧道」
            # （cfp=None + url=""），复用补拿闸重建、复用 should_register 语义强制重注册。
            agent_ok = agent.poll() is None
            if not agent_ok:
                tunnel_probe_fails = 0  # 本机不健康时连败数不作数（问题不在隧道）
            if (
                link == LINK_CLOUDFLARED
                and agent_ok
                and url
                and cfp is not None
                and cfp.poll() is None
                and now >= next_tunnel_probe
            ):
                probe_ok = _public_ping(url, auth) is not None
                tunnel_probe_fails = 0 if probe_ok else tunnel_probe_fails + 1
                next_tunnel_probe = now + TUNNEL_PROBE_SEC
            if _tunnel_dead(
                is_cloudflared=link == LINK_CLOUDFLARED,
                url=url,
                cfp=cfp,
                agent_ok=agent_ok,
                probe_fails=tunnel_probe_fails,
            ):
                why = (
                    "cloudflared 进程已退出"
                    if cfp is None or cfp.poll() is not None
                    else f"公网探针连续 {tunnel_probe_fails} 次失败"
                )
                log(f"★ 隧道判定死亡（{why}）—— 重建 cloudflared 并重注册")
                _terminate(cfp)
                cfp = None
                url = ""
                # 绕过 should_register 的去重（重建后即便拿到同名 URL 也要重注册）。
                # 副作用：重新注册成功前，收线时不会发 unregister（hub 侧由 ping 门兜底）。
                reg_state["url"] = ""
                tunnel_probe_fails = 0
                next_tunnel_try = now
            if _tunnel_retry_due(url, now, next_tunnel_try):
                log("隧道补拿：尝试重新建 cloudflared…")
                url, cfp, started = _open_cloudflared_once(
                    current_url=url,
                    cfp=cfp,
                    ensure=lambda: _ensure_cloudflared_url(
                        cfg,
                        port,
                        cf_log,
                        log,
                        attempts=1,
                        postures=_get_postures(),
                        wait_timeout=TUNNEL_RETRY_WAIT_SEC,
                    ),
                )
                if started:
                    link = LINK_CLOUDFLARED
                    log(f"★ 隧道就绪（补拿）：{url} —— 将重注册")
                    next_retry = 0.0
                    tunnel_probe_fails = 0
                    next_tunnel_probe = now + TUNNEL_PROBE_SEC
                next_tunnel_try = now + TUNNEL_RETRY_SEC
            if url and cfp is not None:
                cur = _tunnel_url(cf_log)
                if cur and cur != url:
                    url = cur
                    next_retry = 0.0
                    tunnel_probe_fails = 0
                    next_tunnel_probe = now + TUNNEL_PROBE_SEC
                    log(f"★ 隧道换名（quick tunnel 重连）：{url} —— 将重注册")
            if hub_configured and url and should_register(reg_state, url, auth) and now >= next_retry:
                out = _try_register()
                if out["ok"]:
                    backoff, next_retry = 0.0, 0.0
                elif out["kind"] == "auth":
                    hub_configured = False
                    log("HUB_TOKEN 被拒（auth）——注册放弃")
                else:
                    backoff = RETRY_FIRST_SEC if backoff <= 0 else min(RETRY_MAX_SEC, backoff * 2)
                    next_retry = now + backoff
                    log(f"重注册失败（{out['kind']}）——{int(backoff)}s 后重试")
            # 产出摘要（每 10 分钟一行）：先于 sleep 判定，保证第一行不会因重启/重注册被推迟。
            # 取不到就如实说不可知（不等于「没产出」）——观测面坏不得让监护循环抛异常。
            if time.time() >= next_output_at:
                emit_output_summary(port, auth, log)
                next_output_at = time.time() + OUTPUT_STATUS_EVERY_SEC
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
