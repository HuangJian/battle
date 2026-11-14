"""common/http_pool.py — 节点通信的长连接池（thread-local，stdlib-only）。

为什么存在（plan/node-throughput-and-keepalive P4，2026-11-14 实测）：本机 → gcs `/v1/ping`
**新建连接 0.77s（中位）/ 复用 0.32s**，而训练侧每个请求都新建连接（urllib）。一局 rollout
至少一次请求，一轮几百局 ⇒ 纯握手开销按轮计。边缘支持 keep-alive（curl `num_connects=0`）。

三条硬约束（违反即事故）：

1. **绝不做进程级共享**：trainer 同节点 2~8 条线程并发，共享一条连接会串包（HTTP/1.1 单流）。
   连接按线程本地缓存，键 = `(scheme, host, port, proxy)`。
2. **timeout 是每请求的**：`http.client` 的 socket timeout 在建连时定死，而任务请求可达 900s、
   ping 只有 3s。复用前必须 `sock.settimeout(timeout)`，否则 3s 建的池连接拿去跑长任务会立刻超时。
3. **重发是白名单的**（用户 2026-11-14 裁定）：只在「传输异常 ∧ 还没拿到响应头 ∧ 不在收工态」
   时丢连接重发 **1 次**；`/v1/task` **不重发**——agent 的同步路径不认重名任务，重发 = 节点再
   开一局、白烧一个槽（异步路径才有 202 幂等）。收工断连（`abort_active_requests`）也不重发，
   否则「收工即断连」会把刚收工的局重新开起来（2026-09-19 裁定）。

异常语义逐字复刻 urllib：≥400 抛 `urllib.error.HTTPError`（带 body 的 fp），传输失败抛
`urllib.error.URLError`——`common.distribution.is_transient_error` 与 dispatch 的
409/503/404 分类全压在这两个类型上。
"""

from __future__ import annotations

import http.client
import io
import os
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from common import net_http

#: 连接缓存键：(scheme, host, port, proxy)。
_Key = tuple[str, str, int, str]

_LOCAL = threading.local()
_ACTIVE_LOCK = threading.Lock()
#: 在飞响应（按 scope 分作用域）：收工断连关的就是它们。
_ACTIVE: set[tuple[str, Any]] = set()
_ABORTED: set[str] = set()
_STATS_LOCK = threading.Lock()
_STATS: dict[str, int] = {"reuse": 0, "connect": 0, "drop": 0, "retry": 0}


def enabled() -> bool:
    """逃生口：`NODE_HTTP_POOL=0` ⇒ 调用方逐字节回退到 `racing_urlopen`。"""
    return os.environ.get("NODE_HTTP_POOL", "1") != "0"


def pool_stats() -> dict[str, int]:
    """`{reuse, connect, drop, retry}`（随 `[dist] round done` 打一行，验收复用率用）。"""
    with _STATS_LOCK:
        return dict(_STATS)


def take_stats() -> dict[str, int]:
    """读并清零：`[dist] round done` 每轮一行要的是**本轮**口径，不是进程累计。"""
    with _STATS_LOCK:
        out = dict(_STATS)
        for k in _STATS:
            _STATS[k] = 0
    return out


def stats_suffix() -> str:
    """本轮复用率（接在 `[dist] round done` 行尾；池关闭 ⇒ 空串，不拿 0% 误导）。

    每轮**恒打**：P4 的 DoD 是复用率 ≥95%，不看的指标不会保持。"""
    if not enabled():
        return ""
    st = take_stats()
    total = st.get("reuse", 0) + st.get("connect", 0)
    rate = (100.0 * st.get("reuse", 0) / total) if total else 0.0
    return (
        f" pool reuse={st.get('reuse', 0)} connect={st.get('connect', 0)}"
        f" drop={st.get('drop', 0)} retry={st.get('retry', 0)} 复用率={rate:.1f}%"
    )


def _bump(key: str, n: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + n


def reset() -> None:
    """关掉本线程的连接并清统计（测试与运维用；不动别的线程的连接）。"""
    _close_all()
    with _STATS_LOCK:
        for k in _STATS:
            _STATS[k] = 0


def _conns() -> dict[_Key, Any]:
    box = getattr(_LOCAL, "conns", None)
    if box is None:
        box = _LOCAL.conns = {}
    return box


def _close_all() -> None:
    box = getattr(_LOCAL, "conns", None)
    if not box:
        return
    for conn in list(box.values()):
        _close_quiet(conn)
    box.clear()


def _close_quiet(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 代理（urllib 语义）


def _proxy_for(scheme: str, host: str) -> str:
    """该 URL 的代理（空串 = 直连）。判据与 urllib 同源：`proxy_bypass` + `getproxies`。

    回环一律绕开代理（`net_http.is_loopback`）：本仓用户级环境带 `HTTP_PROXY`，而
    `no_proxy` 里写的 `127.*` 通配 urllib **不认**（见 `common/net_http.py` 的头注释）。
    """
    if net_http.is_loopback(host):
        return ""
    try:
        if urllib.request.proxy_bypass(host):
            return ""
        proxies = urllib.request.getproxies()
    except Exception:
        return ""
    return str(proxies.get(scheme) or proxies.get("all") or "").strip()


def _proxy_addr(proxy: str) -> tuple[str, str, int]:
    """代理串 → `(scheme, host, port)`；**连代理用代理自己的 scheme**（http 代理不能做 TLS）。"""
    s = proxy if "//" in proxy else f"//{proxy}"
    parts = urlsplit(s)
    scheme = (parts.scheme or "http").lower()
    default = 443 if scheme == "https" else 80
    return scheme, str(parts.hostname or ""), int(parts.port or default)


# ---------------------------------------------------------------- 连接生命周期


def _conn_for(
    scheme: str, host: str, port: int, proxy: str, timeout: float
) -> tuple[Any, _Key]:
    """取（或建）本线程的该目标连接；返回 `(conn, 请求目标)`。

    `http` 走代理时请求行用绝对 URI（http.client 的要求），其余用 path?query。
    """
    key = (scheme, host, port, proxy)
    conn = _conns().get(key)
    if conn is None:
        if proxy:
            pscheme, phost, pport = _proxy_addr(proxy)
            conn = net_http.racing_connection(pscheme, phost, pport, timeout)
            if scheme == "https":
                conn.set_tunnel(host, port)
        else:
            conn = net_http.racing_connection(scheme, host, port, timeout)
        _conns()[key] = conn
        _bump("connect")
    else:
        _bump("reuse")
    return conn, key


def _drop_conn(key: _Key) -> None:
    conn = _conns().pop(key, None)
    if conn is not None:
        _close_quiet(conn)
        _bump("drop")


# ---------------------------------------------------------------- abort（收工断连）


def close_async(targets: list[Any], name: str) -> None:
    """交 daemon 线程逐个关响应（唯一一份：`distribution.abort_active_requests` 也用它）。

    **关连接必须异步**（2026-09-19 实测：`HTTPResponse.close()` 在别人读 body 时会阻塞，
    主线程卡过 81 秒）。收工路径「立即马上」是硬要求 ⇒ 置位 + 尽力关，绝不等。
    """
    if not targets:
        return

    def _closer() -> None:
        for r in targets:
            try:
                r.close()
            except Exception:
                pass

    threading.Thread(target=_closer, daemon=True, name=name).start()


def abort_scope(scope: str) -> int:
    """停用该作用域并异步关掉它的在飞响应（返回待关条数；与 distribution 同一套语义）。"""
    with _ACTIVE_LOCK:
        _ABORTED.add(scope)
        targets = [r for s, r in _ACTIVE if s == scope]
    close_async(targets, "pool-abort-close")
    return len(targets)


def clear_abort(scope: str = "") -> None:
    with _ACTIVE_LOCK:
        if scope:
            _ABORTED.discard(scope)
        else:
            _ABORTED.clear()


def abort_scope_active(scope: str) -> bool:
    with _ACTIVE_LOCK:
        return scope in _ABORTED


def active_count(scope: str = "") -> int:
    """该作用域的在飞响应数（观测/测试用；不暴露锁内结构）。"""
    with _ACTIVE_LOCK:
        return sum(1 for s, _r in _ACTIVE if s == scope)


# ---------------------------------------------------------------- 请求


def _may_retry(url: str, scope: str) -> bool:
    """白名单：收工态不重发（含全局收工，scope=""）；`/v1/task` 不重发（同步路径不幂等）。"""
    if abort_scope_active(scope):
        return False
    return "/v1/task" not in (urlsplit(url).path or "")


def _request_target(url: str, scheme: str, proxy: str) -> str:
    parts = urlsplit(url)
    path_qs = parts.path or "/"
    if parts.query:
        path_qs += "?" + parts.query
    # http 经代理：请求行必须是绝对 URI（RFC 7230 §5.3.2）；https 走 tunnel，仍是 path。
    return url if (proxy and scheme == "http") else path_qs


def request(
    url: str,
    *,
    timeout: float = 30.0,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    scope: str = "",
) -> tuple[int, bytes]:
    """发一个请求，返回 `(status, body)`。≥400 抛 `HTTPError`，传输失败抛 `URLError`。"""
    parts = urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    host = parts.hostname or ""
    if not host:
        raise urllib.error.URLError(ValueError(f"invalid url: {url!r}"))
    port = parts.port or (443 if scheme == "https" else 80)
    proxy = _proxy_for(scheme, host)
    target = _request_target(url, scheme, proxy)
    verb = method or ("POST" if data is not None else "GET")
    hdrs = dict(headers or {})

    attempts = 0
    while True:
        conn, key = _conn_for(scheme, host, port, proxy, timeout)
        # 复用连接的 socket timeout 必须按**本次**请求刷新（见模块头第 2 条）。
        try:
            conn.timeout = timeout
            if getattr(conn, "sock", None) is not None:
                conn.sock.settimeout(timeout)
            conn.request(verb, target, body=data, headers=hdrs)
            resp = conn.getresponse()
        except (OSError, ValueError, http.client.HTTPException) as e:
            _drop_conn(key)
            if attempts == 0 and _may_retry(url, scope):
                attempts += 1
                _bump("retry")
                continue
            raise urllib.error.URLError(e) from e

        status = int(resp.status)
        try:
            # 与 distribution._request 同规：不分 scope 空/非空一律登记（全局收工要关得到）。
            with _ACTIVE_LOCK:
                _ACTIVE.add((scope, resp))
            body = resp.read()
        except Exception as e:  # 读到一半的任何失败都是传输失败
            # 已经拿到响应头 ⇒ 不在白名单（重发会把一局重新跑一遍）；收工关闭也走这里。
            # 为什么宽到 Exception：`abort_scope` 在**另一条线程**关响应，CPython 的
            # `HTTPResponse._close_conn` 双关时会抛 AttributeError('NoneType' has no
            # attribute 'close')——那不是调用方 bug，而是并发关闭的已知形态。
            _drop_conn(key)
            raise urllib.error.URLError(e) from e
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard((scope, resp))

        if status in (301, 302, 303, 307, 308):
            # 不自己跟重定向（urllib 会跟）：这条交给调用方的回退路径，连接作废，
            # 避免"池里跟重定向"与 urllib 语义分叉。
            _drop_conn(key)
        elif bool(getattr(resp, "will_close", False)):
            _drop_conn(key)
        # 否则：body 已读满 ⇒ 连接可复用，留在池里。

        if status >= 400:
            raise urllib.error.HTTPError(
                url, status, str(resp.reason or ""), resp.headers, io.BytesIO(body)
            )
        return status, body
