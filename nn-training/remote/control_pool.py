"""remote/control_pool.py — 控制面**连接池**（plan/aistudio-transfer-hardening §3.5，B2）。

为什么单独成模块（而不是塞进 `remote/http.py`）：`tests/remote/test_http_split.py` 有一条
架构护栏——**`http.py` 顶层的可变容器只能有 `_POLL_WARN_AT`**（「不该顺手多带一份共享状态」）。
连接池就是一份进程级状态，塞进去会当场踩那条护栏。同居 `http.py` 的唯一理由是「离调用点近」，
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

## 池是一个对象（P0-5，2026-11-21）

原来是模块级 `_CONTROL_CONNS: dict[...] = {}` + 四个模块函数。现在住 `ControlPool` 实例，
模块只留一个默认实例 `_POOL`：

* **所有权**：`drop` / `close` / `request` 都挂在**拥有连接的那个对象**上，读代码不用再问
  「谁在改这个模块变量」；要隔离的用例（或将来要多池的场景）自建 `ControlPool()`，不必再靠
  全局 `reset()` 这条缝。
* **关闭时机**（三档，刻意不同）：
  - **失败即作废**（`drop`）：从池里摘掉并**尽力 `close()`** 三条连接只是一条坏掉的 socket，
    立刻还 fd；旧实现靠 `pop` 之后的 refcount 隐式回收，等价但不可见。`close()` 失败只吞掉
    （假连接、已经半关的 socket 都不该把「作废」这件事变成新异常）。
  - **整池关闭**（`close()`，对外是 `reset()`）：**只在没有在飞请求时调**。池是跨线程共享的，
    在别人那条请求读响应时关它的连接，会把一次正常请求变成异常（虽然会被重试兜住，但那不是
    调用方想要的语义）⇒ 用例/热替换专用，生产路径不调。
  - **进程退出不挂 `atexit`**：OS 回收 fd；而 `atexit` 里关跨线程连接会打断正在退出的在飞请求。
* **用例隔离**：走真链路的用例仍用 `reset()`（那是唯一能碰到 `http._request` 内部默认池的缝），
  只测池行为的用例改成自建实例。

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

PoolKey = tuple[str, str, int]


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


def _close_quietly(conn: object | None) -> None:
    """尽力 `close()` 一条连接：假连接、已半关的 socket、`None` 都只当没这回事。"""
    if conn is None:
        return
    close = getattr(conn, "close", None)
    if close is None:
        return
    try:
        close()
    except OSError:
        # 半关/已断的 socket：`close()` 只会在这种时候抛，而「作废」这个结果已经成立。
        return


class ControlPool:
    """一份 `(scheme, host, port) -> 连接` 的池（懒建 + 异常即丢弃）。

    线程语义：**不是线程安全容器**，但也不需要 — 键是目标端点，同键并发时两个线程可能各建
    一条、后到的覆盖先到的（覆盖掉的那条由 refcount 关掉），这与改造前的模块 dict 逐字相同。
    真正的并发保护在 `http.client` 的连接内部（同一条连接上的并发请求本就不支持）。
    """

    #: 池体（与 `hub/store_wire` 的混入同规：类型声明在类体，`__init__` 只负责建空表）。
    _conns: dict[PoolKey, Any]

    def __init__(self) -> None:
        self._conns = {}

    def _conn(self, scheme: str, host: str, port: int, timeout: float) -> Any:
        """取（或懒建）一条控制面连接；需要时挂代理隧道。"""
        key = (scheme, host, port)
        conn = self._conns.get(key)
        if conn is not None:
            return conn
        proxy = _proxy_for(scheme)
        if proxy:
            pu = urllib.parse.urlparse(proxy if "://" in proxy else f"http://{proxy}")
            phost = pu.hostname or ""
            pport = int(pu.port or (443 if scheme == "https" else 80))
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    phost, pport, timeout=timeout, context=ssl.create_default_context()
                )
                # CONNECT 隧道：TLS 握手发生在隧道之后，SNI 用**目标**主机（http.client 的
                # `_tunnel_host` 就是这个语义），与 urllib 的 ProxyHandler 同款。
                conn.set_tunnel(host, port)
            else:
                conn = http.client.HTTPConnection(phost, pport, timeout=timeout)
        elif scheme == "https":
            conn = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        self._conns[key] = conn
        return conn

    def drop(self, key: PoolKey) -> None:
        """作废某条连接：摘掉并尽力关闭（`close()` 失败不改变「它已被作废」这个结果）。"""
        _close_quietly(self._conns.pop(key, None))

    def close(self) -> None:
        """关掉整池。**只在没有在飞请求时调**（连接可能正被别的线程读响应）。"""
        conns = list(self._conns.values())
        self._conns.clear()
        for conn in conns:
            _close_quietly(conn)

    def count(self) -> int:
        """池里现有连接数（观测/测试用）。"""
        return len(self._conns)

    def request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        data: bytes | None = None,
        timeout: float = 6.0,
        attempts: int = CONTROL_ATTEMPTS,
    ) -> tuple[int, bytes]:
        """走本池发一次控制面请求；**失败即丢连接并重建重试**，两连败才抛最后一个异常。"""
        scheme, host, port, target, _path = _split(url)
        key: PoolKey = (scheme, host, port)
        last: BaseException | None = None
        for attempt in range(max(1, int(attempts))):
            if attempt:
                self.drop(key)  # 换连接重抽（与 §22 同款判据）
            try:
                conn = self._conn(scheme, host, port, timeout)
                conn.request(method or "GET", target, body=data, headers=dict(headers))
                resp = conn.getresponse()
                return int(resp.status), bytes(resp.read())
            except Exception as e:  # 含 http.client 的所有失败态；连接一律作废
                self.drop(key)
                last = e
        raise last if last is not None else RuntimeError(f"控制面池：{url} 无结果（不应到这里）")


#: 进程级默认池（生产唯一使用者 = `remote.http._request`）。要隔离的用例自建 `ControlPool()`。
_POOL = ControlPool()


def request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    data: bytes | None = None,
    timeout: float = 6.0,
    attempts: int = CONTROL_ATTEMPTS,
) -> tuple[int, bytes]:
    """默认池的 `request`（保留模块级入口：生产调用点不必知道池对象的存在）。"""
    return _POOL.request(
        url,
        method=method,
        headers=headers,
        data=data,
        timeout=timeout,
        attempts=attempts,
    )


def drop(scheme: str, host: str, port: int) -> None:
    """丢弃默认池里的某条连接。"""
    _POOL.drop((scheme, host, port))


def reset() -> None:
    """关掉默认池（测试与热替换用）：下一请求重建，不再有「池里躺着旧 socket」。"""
    _POOL.close()


def conn_count() -> int:
    """默认池里现有连接数（观测/测试用）。"""
    return _POOL.count()


__all__ = [
    "CONTROL_ATTEMPTS",
    "CONTROL_POOL_ENABLED",
    "ControlPool",
    "conn_count",
    "drop",
    "request",
    "reset",
]
