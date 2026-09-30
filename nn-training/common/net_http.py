"""net_http.py — 回环地址的 HTTP **一律绕开代理**（本机 hub↔worker 通信的公共出口）。

为什么存在（2026-09-18 门禁实测）：

本机用户级环境带 `HTTP_PROXY`/`HTTPS_PROXY`（指向局域网代理），而 `no_proxy` 里写的是
`127.*` 这种通配——Python 的 `urllib.request.proxy_bypass()` **不认**它（只认
`host == entry` / `*.suffix` / `.suffix` 三种形式），实测 `proxy_bypass("127.0.0.1") is False`。
后果：每一发去 `http://127.0.0.1:<hub/worker>` 的请求都被送到外部代理再转回来——

  * 本机训练（local 预设的 hub、本机 worker、控制台的健康探测）凭空多一跳；
  * 代理抖动时回 **502**（门禁里 `tests/test_offline_deliver.py::test_offline_endpoints_require_auth`
    实测红过一次：hub 日志明明白白写了两次 401，测试侧读到的却是 502）。

回环流量在定义上就是本机的，绕开代理永远是对的；非回环（隧道/公网 URL）保持 urllib 默认
——云机侧「显式 ProxyHandler」的需求（Colab userspace 实测，见 `remote/worker.py`）不受影响。
"""

from __future__ import annotations

import urllib.request
from typing import Any
from urllib.parse import urlsplit

#: 无代理 opener（进程内复用；`ProxyHandler({})` = 任何 scheme 都不走代理）。
_NO_PROXY_OPENER: urllib.request.OpenerDirector | None = None


def is_loopback(url_or_host: str) -> bool:
    """回环判据：IPv4 `127.0.0.0/8`、IPv6 `::1`、`localhost`（含 `*.localhost`）。

    接受完整 URL（`http://127.0.0.1:8900/x`）与裸 host（`127.0.0.1:8900`）两种形态。
    """
    s = str(url_or_host or "").strip()
    if not s:
        return False
    if "//" in s:
        host = urlsplit(s).hostname or ""
    else:
        host = s.split("/", 1)[0].split("?", 1)[0]
        if host.startswith("["):  # [::1]:8900
            host = host[1:].split("]", 1)[0]
        elif host.count(":") == 1:  # 127.0.0.1:8900
            host = host.split(":", 1)[0]
    host = host.strip().lower().rstrip(".")
    if host in ("localhost", "::1"):
        return True
    if host.endswith(".localhost"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "127":
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts[1:])
    return False


def no_proxy_opener() -> urllib.request.OpenerDirector:
    """无代理 opener（单例；`build_opener` 不便宜，本机热路径每轮都要用）。"""
    global _NO_PROXY_OPENER
    if _NO_PROXY_OPENER is None:
        _NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _NO_PROXY_OPENER


def urlopen(req: Any, timeout: float = 30.0):
    """`urllib.request.urlopen` 的替身：**回环地址走无代理 opener**，其余保持默认。

    返回值与 `urlopen` 同（上下文管理器）；`HTTPError` 照旧向外抛（调用方各自处理）。
    非回环分支刻意仍用 `urllib.request.urlopen`（运行时查模块属性）——测试里
    `monkeypatch.setattr("urllib.request.urlopen", …)` 那条缝要留住。
    """
    url = getattr(req, "full_url", None) or str(req)
    if is_loopback(url):
        return no_proxy_opener().open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)
