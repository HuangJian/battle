"""remote/control_pool.py — 控制面**连接池**（plan/aistudio-transfer-hardening §3.5，B2）。

为什么单独成模块（而不是塞进 `remote/http.py`）：`tests/remote/test_http_split.py` 有一条
架构护栏——**`http.py` 顶层的可变容器只能有 `_POLL_WARN_AT`**（「不该顺手多带一份共享状态」）。
连接池就是一个进程级 dict，塞进去会当场踩那条护栏。同居 `http.py` 的唯一理由是「离调用点近」，
而 HTTP 面本就该**零模块级玩法状态**（AGENTS §2.2）——所以另开一个模块才是正解。

## 三条边界（都有用例钉住）

1. **只服务控制面**。bulk（payload/code/ts_code/blob 的 GET 与 result 的 POST）**永不进池**——
   「换连接 = 重抽一次签」是 bulk 侧对抗慢链路的唯一手段（§22），池化会把那条能力抹掉。
   是否控制面由 `remote.bulk_sched.control_path` 判，调用方（`http._request`）负责判。
2. **复刻 `_get_opener` 的代理判据**。池化若绕开代理，非回环的控制请求（aistudio 走
   Cloudflare quick tunnel）在 Colab userspace 这类「`urlopen()` 不读 HTTP_PROXY」的环境里
   会直接连不通。所以 `_proxy_for` 与 `http._get_opener` 用**同一个 env 名表**。
3. **异常即丢弃重建**。池化的另一半是失败处理：连接坏了就作废，`attempts` 用完把最后一个
   异常抛给调用方（绝不返回「半份成功」）。

## 依赖方向

`control_pool → {stdlib}`（**零仓内依赖**）：判据（`control_path`）与装配（`_request`）都在外面。
"""

from __future__ import annotations

import http.client
import os
import ssl
import urllib.parse
from typing import Any

#: 总开关（逃生口：`REMOTE_CONTROL_POOL=0`）。
CONTROL_POOL_ENABLED: bool = os.environ.get("REMOTE_CONTROL_POOL", "1").strip() not in (
    "0",
    "",
    "false",
    "no",
)
#: 幂等控制请求的尝试次数（B3：**失败不退避**，立刻换连接；2 = 原发一次 + 重抽一次）。
#: 住本模块是因为重试发生在**传输层**，不该由每个调用方各循环一遍。
CONTROL_ATTEMPTS = 2

#: 池里的连接（`(scheme, host, port) -> HTTP(S)Connection`）。**懒建 + 异常即丢弃**。
_CONTROL_CONNS: dict[tuple[str, str, int], Any] = {}


def _proxy_for(scheme: str) -> str:
    """按 scheme 取代理（**与 `http._get_opener` 同一套 env 判据**，不许分叉成两个口径）。"""
    for k in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY"):
        v = os.environ.get(k)
        if v:
            return str(v)
    return ""


def _split(url: str) -> tuple[str, str, int, str, str]:
    """`(scheme, host, port, 请求目标, 池键用的 host)`。

    `目标`：HTTP 正向代理要**绝对形式**的请求行（代理不知道目标主机），直连/HTTPS 隧道用相对路径。
    """
    u = urllib.parse.urlsplit(url)
    scheme = u.scheme or "http"
    host = u.hostname or ""
    port = int(u.port or (443 if scheme == "https" else 80))
    path = u.path or "/"
    if u.query:
        path = f"{path}?{u.query}"
    target = url if (_proxy_for(scheme) and scheme == "http") else path
    return scheme, host, port, target, path


def _control_conn(scheme: str, host: str, port: int, timeout: float) -> Any:
    """懒建（或复用）一条控制面连接；需要时挂代理隧道。"""
    proxy = _proxy_for(scheme)
    if proxy:
        pu = urllib.parse.urlparse(proxy if "://" in proxy else f"http://{proxy}")
        phost = pu.hostname or ""
        pport = int(pu.port or (443 if scheme == "https" else 80))
        conn = _CONTROL_CONNS.get((scheme, host, port))
        if conn is not None:
            return conn
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                phost, pport, timeout=timeout, context=ssl.create_default_context()
            )
            # CONNECT 隧道：TLS 握手发生在隧道之后，SNI 用**目标**主机（http.client 的
            # `_tunnel_host` 就是这个语义），与 urllib 的 ProxyHandler 同款。
            conn.set_tunnel(host, port)
        else:
            conn = http.client.HTTPConnection(phost, pport, timeout=timeout)
        _CONTROL_CONNS[(scheme, host, port)] = conn
        return conn
    conn = _CONTROL_CONNS.get((scheme, host, port))
    if conn is None:
        conn = (
            http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ssl.create_default_context()
            )
            if scheme == "https"
            else http.client.HTTPConnection(host, port, timeout=timeout)
        )
        _CONTROL_CONNS[(scheme, host, port)] = conn
    return conn


def drop(scheme: str, host: str, port: int) -> None:
    """丢弃（不复用）某条连接——**异常即丢弃重建**是池化的另一半。"""
    _CONTROL_CONNS.pop((scheme, host, port), None)


def reset() -> None:
    """清空整池（测试与热替换用）。**不**关闭 socket：调用方要的是「下一请求重建」。"""
    _CONTROL_CONNS.clear()


def conn_count() -> int:
    """池里现有连接数（观测/测试用）。"""
    return len(_CONTROL_CONNS)


def request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    data: bytes | None = None,
    timeout: float = 6.0,
    attempts: int = CONTROL_ATTEMPTS,
) -> tuple[int, bytes]:
    """走池发一次控制面请求；**失败即丢连接并重建重试**，两连败才抛最后一个异常。"""
    scheme, host, port, target, _path = _split(url)
    last: BaseException | None = None
    for attempt in range(max(1, int(attempts))):
        if attempt:
            drop(scheme, host, port)  # 换连接重抽（与 §22 同款判据）
        try:
            conn = _control_conn(scheme, host, port, timeout)
            conn.request(method or "GET", target, body=data, headers=dict(headers))
            resp = conn.getresponse()
            return int(resp.status), bytes(resp.read())
        except Exception as e:  # 含 http.client 的所有失败态；连接一律作废
            drop(scheme, host, port)
            last = e
    raise last if last is not None else RuntimeError(f"控制面池：{url} 无结果（不应到这里）")


__all__ = [
    "CONTROL_ATTEMPTS",
    "CONTROL_POOL_ENABLED",
    "conn_count",
    "drop",
    "request",
    "reset",
]
