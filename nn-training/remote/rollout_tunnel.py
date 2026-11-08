"""remote/rollout_tunnel.py — 采样节点 quick tunnel 的日志与健康工具。

从 `remote/rollout_node.py` 拆出（LOC 预算 <1000；plan/rollout-node-tunnel-death-blindspot）：
日志轮转、死因提取、URL 等待、灾后判死与公网回环探针。cloudflared 的启动/重试阶梯与进程
所有权仍归 `rollout_node`（监护循环持有 `cfp`）。

★ 两条与 2026-11-08 事故直接相关的约束：

1. `public_ping` 必须在 `tailscale_boot.platform_net_env()` 里跑：userspace tailscale 把
   `HTTP_PROXY` 指到只转发 tailnet 的代理，不还原就会把健康隧道连续判死。
2. 本地 `/v1/ping` 成功**不是**隧道健康证据（它打 127.0.0.1）；这里只做公网回环。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote import tailscale_boot
from remote.push_tunnel import tunnel_url_of

#: 公网 URL 探针节拍与判死阈值：一次 530/超时是常态，连续失败才是证据。
PROBE_SEC = 60.0
DEAD_STREAK = 3
#: cloudflared 日志里出现这些片段 ⇒ 当作一行「死因」（只用于观测，不参与判死）。
DEAD_HINTS = (
    "failed to",
    "no such host",
    "connection refused",
    "context deadline",
    "i/o timeout",
    "tls handshake",
    "error",
)


def dump_tail(path: Path, log: Callable[[str], None], n: int = 40) -> None:
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
            log("  | " + ln)
    except OSError:
        pass


def reset_log(cf_log: Path, attempt: int) -> None:
    """每次 attempt 前轮转并清空日志：否则 `tunnel_url_of` 会读到上一次的死域名。"""
    try:
        old = cf_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        old = ""
    if old:
        try:
            cf_log.with_name(f"{cf_log.name}.{attempt}").write_text(old, encoding="utf-8")
        except OSError:
            pass
    try:
        cf_log.write_text("", encoding="utf-8")
    except OSError:
        pass


def dead_reason(cf_log: Path) -> str:
    """从 cloudflared 日志尾部提一行死因；提不到就如实说提不到，不编。"""
    try:
        lines = cf_log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return "未能从日志提取（读不到日志）"
    for line in lines:
        low = line.lower()
        if any(h in low for h in DEAD_HINTS):
            return line.strip()[:300]
    return "未能从日志提取"


def wait_url(
    cf_log: Path,
    cfp: Any,
    log: Callable[[str], None],
    timeout: float = 90.0,
    attempt: tuple[int, int] | None = None,
) -> str:
    """等 quick tunnel URL；进程退出或超时都返回 `""`。

    `attempt` 只改日志形状：首拍之外的 attempt 由 `_ensure_cloudflared_url` 统一打
    `rc` / 死因 / 尾部，这里不再重复 dump。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        url = tunnel_url_of(cf_log)
        if url:
            return url
        if cfp is not None and cfp.poll() is not None:
            break
        time.sleep(2)
    if attempt is not None:
        return ""  # 重试阶梯由 _ensure 统一打 rc / 存活秒数 / 尾部，这里不重复
    log(f"⚠ 没拿到隧道 URL —— 下面是 {cf_log} 尾部：")
    dump_tail(cf_log, log, 25)
    return ""


def retry_due(url: str, now: float, next_tunnel_try: float) -> bool:
    """空 URL 才补拿；到点才试（墙钟，不用 tick 计数）。"""
    return not url and now >= next_tunnel_try


def tunnel_dead(
    *, is_cloudflared: bool, url: str, cfp: Any, agent_ok: bool, probe_fails: int
) -> bool:
    """cloudflared 通道是否已死（纯函数，可单测）。

    - 进程退出是确定性证据（2026-11-08 现场：进程没了、url 仍非空、日志全绿）；
    - 进程活着但公网 URL 连续 N 次不可达、且本地 agent 健康 ⇒ 判死（覆盖「进程在、到边缘
      的连接断了」）；本地 agent 不健康时**不判**——那说明问题在本机，不在隧道；
    - tailscale 通道（`cfp is None` 是正常态）/ 空 URL 一律不判。
    """
    if not is_cloudflared or not url:
        return False
    if cfp is None or cfp.poll() is not None:
        return True
    return bool(agent_ok) and int(probe_fails) >= DEAD_STREAK


def public_ping(
    url: str, auth: str, ping: Callable[..., dict | None] | None = None
) -> dict | None:
    """节点自己经**公网 URL** 回环一次 `/v1/ping`（与 hub ping 门 / dashboard 同口径）。

    为什么不用 `_ping` 当隧道证据：它打的是 `127.0.0.1`，隧道死了照答（本次事故的全部盲区）。
    `ping` 是单测注入点；默认与 hub ping 门同一实现，避免口径漂移。
    """
    if ping is None:
        from common.distribution import node_ping

        ping = node_ping
    try:
        with tailscale_boot.platform_net_env():
            return ping(url, auth, 3.0)
    except Exception:
        return None
