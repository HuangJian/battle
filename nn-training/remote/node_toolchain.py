"""remote/node_toolchain.py — 采样节点要用的二进制：查找、下载、持久缓存。

从 `remote/rollout_node.py` 拆出（2026-11-18，G5「建接缝」）：这些函数只碰 `cfg` /
`log` / 文件系统，不碰节点状态、隧道进程或注册面 —— 它们是「这台机器上有没有
bun / cloudflared」的**工具链契约**，而 `run()` 只负责监督。

两条与事故直接相关的规则：

1. **公网下载一律在 `public_net()` 里**（`tailscale_boot.ensure()` 会把 `HTTP_PROXY` 指到
   只转发 tailnet 的本地代理；之后平台 Secrets 与 GitHub/npmmirror 都走不通）。
   `tests/remote/test_rollout_node_notebook.py` 用 AST 钉住下载语句的包裹。
2. **缓存里的二进制要真跑 `--version` 才算数**（上个架构的残留 / 半截下载都长得像文件）。
   命不中不致命：重新下载；写不进缓存只记一行，不影响本次运行。

测试住 `tests/remote/test_node_toolchain.py`：断言的是本模块的**公开**函数，不是
`rollout_node` 的私有别名 —— 监护逻辑与「工具从哪来」因此可以各自改。
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace

from common import runtime
from common.proc import run_capture
from remote import tailscale_boot

#: bun 镜像安装的真失败形态：网络/盘（OSError）、下载重试用尽（我们自己的 RuntimeError）、
#: zip 坏了或截断（`BadZipFile` / `EOFError`）。`zip` 里没有那个目录是我们自己的
#: `FileNotFoundError`（⊂ OSError）⇒ 同样落到「镜像没装上、回落直连」。
_BUN_INSTALL_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    RuntimeError,
    zipfile.BadZipFile,
    EOFError,
)

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

def sh(
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


def public_net() -> AbstractContextManager[None]:
    """公网下载上下文：还原 ensure 之前的代理环境（没有注入时是空操作）。"""
    return tailscale_boot.platform_net_env()

#: 内置 GitHub 镜像（前缀式）：直连不通时**自动**依次尝试，不需要填任何开关。
GITHUB_MIRRORS = (
    "https://gh-proxy.com/https://github.com",
    "https://ghproxy.net/https://github.com",
)


def _explicit_github_base(cfg: dict) -> str:
    base = str(cfg.get("github_base") or os.environ.get("GITHUB_BASE") or "").strip().rstrip("/")
    return base


def github_candidates(cfg: dict, url: str) -> list[str]:
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


def speed_probe(url: str, cap_bytes: int = 262_144, timeout: int = 6) -> float:
    """拉一小段估算吞吐（B/s）；不可达/测不出 ⇒ 0。

    HEAD 只能测延迟，不代表带宽 —— 这里用 Range 取前 256KB，按 `size/time` 排。
    Range 不被支持时会退化成小文件下载，仍以实际字节/耗时计。
    """
    r = sh(
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


def rank_fastest(
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
    with runtime.Runtime(scope="speed-probe") as rt:
        speeds = rt.map(lambda it: speed_probe(it[1]), items, workers=len(items))
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
            r = sh(
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
    `ensure_bun` 里的并行测速决定（快者先装）。
    """
    base = _explicit_github_base(cfg)
    out = ([base] if base else []) + ["", *GITHUB_MIRRORS]
    return list(dict.fromkeys(out))


def bun_target(machine: str, cpuinfo: str = "") -> str:
    """bun 的发布包名（`bun-linux-x64[-baseline]` / `bun-linux-aarch64`）；不认识的架构 ⇒ ""。"""
    m = str(machine or "").lower()
    if m in ("x86_64", "amd64"):
        # 与官方 install.sh 同规则：CPU 无 AVX2 ⇒ baseline 包
        return "bun-linux-x64" + ("-baseline" if cpuinfo and "avx2" not in cpuinfo else "")
    if m in ("aarch64", "arm64"):
        return "bun-linux-aarch64"
    return ""


def bun_asset_url(base: str, target: str) -> str:
    """bun 测速用的代表产物（latest release 的同名 zip）；`base` 为空 = 直连。"""
    rest = f"/oven-sh/bun/releases/latest/download/{target or 'bun-linux-x64'}.zip"
    return (base + rest) if base else "https://github.com" + rest


def latest_bun_version(listing: str) -> str:
    """npmmirror 目录 JSON 里挑最新稳定版（`bun-v1.4.4/` → `1.4.4`）；没有 ⇒ ""。"""
    best: tuple[int, ...] = ()
    for name in re.findall(r"bun-v([0-9]+(?:\.[0-9]+)+)/", listing):
        key = tuple(int(x) for x in name.split("."))
        if key > best:
            best = key
    return ".".join(str(x) for x in best)


def install_bun_mirror(cfg: dict, log: Callable[[str], None]) -> str:
    """从 npmmirror 装 bun（`public_net()` 内调用）；返回 bun 路径，失败 ⇒ ""。"""
    base = str(
        cfg.get("bun_mirror") or os.environ.get("BUN_MIRROR") or BUN_MIRROR_DEFAULT
    ).strip().rstrip("/")
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        cpuinfo = ""
    target = bun_target(platform.machine(), cpuinfo)
    if not target:
        log(f"bun 镜像：不认识的架构 {platform.machine()} —— 跳过")
        return ""
    try:
        with urllib.request.urlopen(f"{base}/", timeout=60) as r:
            ver = latest_bun_version(r.read().decode("utf-8", "replace"))
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
    except _BUN_INSTALL_ERRORS as e:
        log(f"bun 镜像安装失败（{type(e).__name__}: {e}）")
        return ""


def bin_cache_dir(cfg: dict, *, isdir: Callable[[str], bool] | None = None) -> Path | None:
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


def cached_bin(cache: Path | None, name: str, log: Callable[[str], None]) -> str:
    """缓存里**可执行且 `--version` 通过**的二进制路径；无缓存/坏档/架构不符 ⇒ ""。

    校验靠真跑 `--version`：缓存可能是上个架构的机器留下的、或半截下载 ⇒ 不能只看存在就用。
    """
    if cache is None:
        return ""
    cand = cache / name
    if not (cand.is_file() and os.access(cand, os.X_OK)):
        return ""
    if sh([str(cand), "--version"], timeout=60).returncode != 0:
        log(f"缓存 {name} 校验失败（坏档或架构不符）—— 忽略，重新下载")
        return ""
    log(f"复用持久缓存 {name}: {cand}（跳过下载）")
    return str(cand)


def store_bin(cache: Path | None, name: str, src: str | Path, log: Callable[[str], None]) -> None:
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


def ensure_bun(code_dir: Path, cfg: dict, log: Callable[[str], None]) -> str:
    """找/装 bun（★ 公网安装在 ensure 之前发生；这里仍包 `public_net()` 兜底）。

    GitHub 直连不通时**自动**换路（不需要任何配置）：官方 install.sh 带
    `GITHUB=<镜像>` 重跑（它认这个环境变量），仍失败 ⇒ 回落 npmmirror 二进制。
    候选先用 256KB 小样**并行测速**，官方安装按实测快慢依次试。
    """

    cache = bin_cache_dir(cfg)

    def _find() -> str:
        cached = cached_bin(cache, "bun", log)
        if cached:
            return cached
        for cand in ("bun", "/root/.bun/bin/bun", str(Path.home() / ".bun" / "bin" / "bun")):
            if sh([cand, "--version"], timeout=120).returncode == 0:
                return cand
        return ""

    bun = _find()
    if not bun:
        log("安装 bun…（公网下载；候选并行测速后按快慢排序）")
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            cpuinfo = ""
        target = bun_target(platform.machine(), cpuinfo)
        with public_net():
            bases = rank_fastest(
                [(b, bun_asset_url(b, target)) for b in _bun_install_bases(cfg)],
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
                with public_net():
                    sh(
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
        with public_net():
            bun = install_bun_mirror(cfg, log)
    if not bun:
        return ""
    store_bin(cache, "bun", bun, log)
    bin_dir = str(Path(bun).parent)
    if bin_dir and bin_dir != "." and bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    if cfg.get("bun_install_deps"):
        r = sh([bun, "install", "--frozen-lockfile"], cwd=str(code_dir), timeout=900)
        log(f"bun install rc={r.returncode}（agent 零第三方依赖，失败不致命）")
    return bun


def find_cloudflared(cfg: dict, log: Callable[[str], None]) -> str:
    """已安装的 cloudflared（显式 → PATH → 常见目录）。

    不走 `bash -lc 'command -v ...'`：DSW 的登录 shell 会先打一屏横幅，stdout 非空 ≠ 找到了
    二进制 —— 旧实现把横幅末行当路径，Popen 报 `OSError: File name too long`（2026-11-04）。
    """
    explicit = str(cfg.get("cloudflared_path") or "").strip()
    cands = [os.path.expanduser(explicit)] if explicit else []
    cached = cached_bin(bin_cache_dir(cfg), "cloudflared", log)
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

#: cloudflared 官方直连产物（镜像候选由 `github_candidates` 生成）。
CLOUDFLARED_ASSET_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)


def ensure_cloudflared(cfg: dict, log: Callable[[str], None]) -> str:
    """找/装 cloudflared：显式/PATH/缓存命中即用；否则公网下载并写持久缓存。

    下载整段在 `public_net()` 里（F5：`ensure()` 之后平台代理已被改写）；候选先并行
    测速、按实测快慢依次试，全挂退避重试。返回可执行路径；装不上 ⇒ `""`（不抛）。
    进程的启动与监护理所当然不在这里 —— 这里只回答「这台机器上有没有 cloudflared」。
    """
    cf = find_cloudflared(cfg, log)
    if cf:
        return cf
    dest = "/usr/local/bin/cloudflared"
    log("安装 cloudflared…（公网下载，代理已还原）")
    with public_net():
        # 自动换路（不填开关）：候选并行测速后按实测快慢依次下载；全挂退避重试
        got = _download_retry(
            rank_fastest(
                [(u, u) for u in github_candidates(cfg, CLOUDFLARED_ASSET_URL)],
                "cloudflared",
                log,
            ),
            dest,
            log,
            label="cloudflared",
        )
    if not got:
        log(
            "cloudflared 安装失败（直连与镜像都不通）"
            "—— 可预置二进制后用 CFG.cloudflared_path 指路"
        )
        return ""
    if got != CLOUDFLARED_ASSET_URL:
        log(f"cloudflared 已从镜像下载: {got}")
    os.chmod(dest, 0o755)
    store_bin(bin_cache_dir(cfg), "cloudflared", dest, log)
    return dest
