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

import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote import tailscale_boot
from remote.push_tunnel import tunnel_url_of

#: 隧道预检的单次 HTTPS/TCP 预算（秒）。两个探针共用一个值：它们的读者是同一行日志。
CLOUDFLARED_PROBE_TIMEOUT = 3.0

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


def direct_env() -> dict[str, str]:
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


def probe_https(
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


def probe_tcp(host: str, port: int, timeout: float = CLOUDFLARED_PROBE_TIMEOUT) -> bool:
    """原始 TCP 可达性（不看代理；只作边缘 7844 的诊断，不参与姿势选择）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def net_probe(log: Callable[[str], None]) -> tuple[str, str]:
    """一次预检：用代理感知的 HTTPS 选首选姿势，原始 TCP 只诊断边缘 7844。"""
    inherit = probe_https("inherit")
    direct = probe_https("direct")
    edge = probe_tcp("region1.v2.argotunnel.com", 7844)
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
