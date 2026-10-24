"""remote/push_tunnel.py — quick tunnel（cloudflared）的三件小事（2026-10-23，W4）。

GPU push（`remote/notebook_runtime.run_push_worker`）与离线 push（`remote/offline_push`）
都要做同样的三件事：找到 cloudflared（没有就装）、起隧道、从它的日志里读回 quick tunnel URL。
这三件事以前只住 `run_push_worker` 里 —— 离线腿要再用一次时，复制一份就是两条会漂开的安装
逻辑（一次 GitHub release 改名就能让其中一条静默失效）。

**为什么是独立模块（L0）**：只用 `re` / `Path` / `common.proc`（**没有** `remote` 内的依赖）；读者是
notebook 侧的 supervisor，写错一行就会把「隧道没起来」伪装成「hub 不派活」。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.logutil import tag_logger
from common.proc import POPEN_NO_WINDOW, run_capture

#: `trycloudflare` 的 quick tunnel URL（cloudflared 日志里的唯一形状）。
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
#: 自动安装的落点（Linux 云机）；已存在且可执行就复用它。
CF_INSTALL_PATH = "/usr/local/bin/cloudflared"
#: linux-amd64 的官方 latest 直链（cloudflared 的 release 命名多年未变）。
CF_DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)


#: 缺省日志（与 `remote/*` 其余模块同一个工厂：`tag_logger` + 本模块自己的 `time` 引用，
#: 假钟才拦得到）。名字刻意**不叫** `_log_default`：那个名字已在几个模块里各写一份，
#: 同名铺开会被复用账本记上一笔。
_log_tunnel = tag_logger("push-tunnel", lambda: time)


def tunnel_url_of(path: str | Path) -> str:
    """从 cloudflared 日志里取当前 quick tunnel URL（取不到 ⇒ 空串）。

    取**最后**一个：隧道重连后日志里会留下多个 URL，只有最后那个是活的。
    """
    try:
        urls = _URL_RE.findall(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return urls[-1] if urls else ""


def find_cloudflared(
    hint: str = "", *, log: Callable[[str], None] = _log_tunnel
) -> str:
    """找到 cloudflared（`hint` > PATH > 自动安装）；都给不出 ⇒ 空串。

    **绝不抛**：调用方据此决定「push 模式跑不了」并给一行可操作的提示。
    """
    if hint:
        return str(hint)
    # `shutil.which` 而不是 `where`/`which`：三条平台各跑一个子进程只为看一眼 PATH，
    # 且 Windows 下还会弹一次黑窗（tests/test_no_console_window_py.py 的主判据）。
    found = shutil.which("cloudflared") or ""
    if found:
        return found
    log("cloudflared 未找到，自动安装…")
    try:
        target = CF_INSTALL_PATH
        run_capture(["curl", "-fsSL", CF_DOWNLOAD_URL, "-o", target], check=True, timeout=60)
        os.chmod(target, 0o755)
        log(f"cloudflared 已安装 -> {target}")
        return target
    except Exception as e:  # 网络/权限/curl 缺失：都归「没有 cloudflared」
        log(f"cloudflared 自动安装失败: {e}——push 模式需要它暴露端口；或改用 pull 模式")
        return ""


def start_quick_tunnel(
    cf_bin: str,
    port: int,
    cf_log: str | Path,
    *,
    wait_sec: float = 60.0,
    log: Callable[[str], None] = _log_tunnel,
) -> tuple[Any | None, str]:
    """起 `cloudflared tunnel --url http://localhost:<port>` → `(proc, url)`。

    返回的 proc 由调用方负责收线（`stop_proc`）。URL 在 `wait_sec` 内没出现 ⇒
    `(proc, "")`（进程可能还活着、在重试；日志里有原因）——不把它当异常，隧道偶发失败
    是云机的常态，调用方打一行 WARN 后人肉看见即可。
    """
    cf_path = Path(cf_log)
    cf_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cf_path, "w", encoding="utf-8") as log_f:
        proc = subprocess.Popen(
            [
                cf_bin,
                "tunnel",
                "--url",
                f"http://localhost:{port}",
                "--logfile",
                str(cf_path),
            ],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            **POPEN_NO_WINDOW,
        )
    deadline = time.time() + float(wait_sec)
    while time.time() < deadline:
        url = tunnel_url_of(cf_path)
        if url:
            return proc, url
        if proc.poll() is not None:
            log(f"cloudflared 提前退出 (code {proc.returncode})")
            return proc, ""
        time.sleep(2)
    return proc, ""


def stop_proc(proc: Any | None) -> None:
    """best-effort 收掉一个子进程（已退出/None ⇒ 空操作）。"""
    if proc is None:
        return
    if getattr(proc, "poll", lambda: 0)() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass


__all__ = [
    "CF_DOWNLOAD_URL",
    "CF_INSTALL_PATH",
    "find_cloudflared",
    "start_quick_tunnel",
    "stop_proc",
    "tunnel_url_of",
]
