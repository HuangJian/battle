"""net_http.py — 回环地址的 HTTP **一律绕开代理**（本机 hub↔worker 通信的公共出口）。

为什么存在（2026-09-18 门禁实测）：

本机用户级环境带 `HTTP_PROXY`/`HTTPS_PROXY`（指向局域网代理），而 `no_proxy` 里写的是
`127.*` 这种通配——Python 的 `urllib.request.proxy_bypass()` **不认**它（只认
`host == entry` / `*.suffix` / `.suffix` 三种形式），实测 `proxy_bypass("127.0.0.1") is False`。
后果：每一发去 `http://127.0.0.1:<hub/worker>` 的请求都被送到外部代理再转回来——

  * 本机训练（local 预设的 hub、本机 worker、控制台的健康探测）凭空多一跳；
  * 代理抖动时回 **502**（门禁里 `tests/remote/test_offline_deliver.py::test_offline_endpoints_require_auth`
    实测红过一次：hub 日志明明白白写了两次 401，测试侧读到的却是 502）。

回环流量在定义上就是本机的，绕开代理永远是对的；非回环（隧道/公网 URL）保持 urllib 默认
——云机侧「显式 ProxyHandler」的需求（Colab userspace 实测，见 `remote/worker.py`）不受影响。

第三件事（2026-11-14）：**operator 在 `NO_PROXY` 里点名的网段也要绕开代理**（`env_no_proxy`）。
上面那条坑只补了回环：本机 `NO_PROXY` 写的是 `192.168.0.0/16`（LAN 的三台采样节点），
`proxy_bypass()` 在 Windows 读注册表、**不读 env**，实测 `proxy_bypass("192.168.0.97") is False`
⇒ a96/a97/a98 的每一发请求都被送进局域网代理，而代理对每条响应回 `Connection: close`
⇒ 长连接池白建（2026-11-14 实测：同一 `/v1/status` 经代理 3 发 0.282s / `will_close=True`，
直连 3 发 0.109s / `will_close=False`）。判据是**读 operator 自己写的 `NO_PROXY`**（含 CIDR），
不是硬编码私网段——§149「不做 LAN 例外」按这条实现才不冲突。

第二件事（2026-11-11）：**多地址竞速 connect**（`racing_urlopen`，节点通信的出口）。
`socket.create_connection` 串行试 `getaddrinfo` 的每个结果，且 `timeout` 是**每地址**的
⇒ 前 N 个地址黑洞时墙钟 = N×timeout。本机没有到 Cloudflare 的可用 IPv6 路由，kaggle
隧道主机名解析出 2 个 IPv6 + 2 个 IPv4，一次 ping 的 tcp 段实测 **16.24s**（IPv4 只要
0.21s）。`urllib` 每请求新建连接 ⇒ ping / 权重探针 / POST / 取任务 / 取结果各交一遍，
每轮还派发 2~3 波：rollout 63s 里 43s 是节点门，真采集只有 16s。
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import threading
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

#: 无代理 opener（进程内复用；`ProxyHandler({})` = 任何 scheme 都不走代理）。
_NO_PROXY_OPENER: urllib.request.OpenerDirector | None = None


def host_of(url_or_host: str) -> str:
    """从 URL / `host:port` / 裸 host 取出规范化 host（小写、去尾点）；拿不到 ⇒ 空串。

    回环判据与 `env_no_proxy` 共用这一份解析（两条政策对「host 是什么」不能有两套答案）。
    """
    s = str(url_or_host or "").strip()
    if not s:
        return ""
    if "//" in s:
        host = urlsplit(s).hostname or ""
    else:
        host = s.split("/", 1)[0].split("?", 1)[0]
        if host.startswith("["):  # [::1]:8900
            host = host[1:].split("]", 1)[0]
        elif host.count(":") == 1:  # 127.0.0.1:8900
            host = host.split(":", 1)[0]
    return host.strip().lower().rstrip(".")


def is_loopback(url_or_host: str) -> bool:
    """回环判据：IPv4 `127.0.0.0/8`、IPv6 `::1`、`localhost`（含 `*.localhost`）。

    接受完整 URL（`http://127.0.0.1:8900/x`）与裸 host（`127.0.0.1:8900`）两种形态。
    """
    host = host_of(url_or_host)
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    if host.endswith(".localhost"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "127":
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts[1:])
    return False


def env_no_proxy(url_or_host: str) -> bool:
    """`no_proxy`/`NO_PROXY` 里是否点名了这个 host（`*` / 精确 / `.suffix` / IPv4 CIDR）。

    为什么不用 `urllib.request.proxy_bypass()`：它**优先读 Windows 注册表**，env 里的 CIDR
    （本机是 `192.168.0.0/16`）一律不认（见模块头第三件事）。这里按 operator 写下的字面判定，
    政策来源单一：谁写 `NO_PROXY`，谁决定哪一跳不过代理。
    """
    host = host_of(url_or_host)
    if not host:
        return False
    raw = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    for item in raw.split(","):
        ent = item.strip().lower().rstrip(".")
        if not ent:
            continue
        if ent == "*":
            return True
        if "/" in ent:  # CIDR（只对 IP 字面量有意义）
            try:
                net = ipaddress.ip_network(ent, strict=False)
                addr = ipaddress.ip_address(host)
            except ValueError:
                continue
            if addr.version == net.version and addr in net:
                return True
            continue
        if ent.startswith("*."):
            ent = ent[2:]
        ent = ent.lstrip(".")
        if host == ent or host.endswith("." + ent):
            return True
    return False


def bypass_proxy(url_or_host: str) -> bool:
    """该目标**必须**绕开环境代理：回环 + operator 在 `NO_PROXY` 里点名的网段/域名。"""
    return is_loopback(url_or_host) or env_no_proxy(url_or_host)


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
    if bypass_proxy(url):
        return no_proxy_opener().open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


# ------------------------------------------------------------------ 竞速 connect（2026-11-11）

#: 一次竞速最多同时开拨几个地址（隧道主机名通常 2~4 个；上限只防 DNS 抽风）。
_RACE_MAX = 8

#: 竞速 opener（按「是否绕代理」各一份；`build_opener` 不便宜，节点热路径每轮几百发）。
_RACING_OPENERS: dict[bool, urllib.request.OpenerDirector] = {}


def racing_create_connection(
    address: tuple[str, int],
    timeout: Any = None,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """`socket.create_connection` 的替身：多地址**并行竞速**，`timeout` 是**总预算**。

    单地址（本机回环、LAN 的 IP 字面量）直接走 stdlib 原路 ⇒ 零行为差、零线程开销。
    多地址时同时开拨，第一个成功的赢；输家（含主线程已放弃后才到的成功者）一律关掉。
    线程一律 daemon：解释器退出不许被黑洞连接挂住。
    """
    host, port = address
    budget = float(timeout) if isinstance(timeout, (int, float)) else None
    cands: list[tuple[int, int, int, Any]] = []
    seen: set[tuple[int, Any]] = set()
    for family, socktype, proto, _canon, sa in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        key = (family, sa)
        if key in seen:
            continue
        seen.add(key)
        cands.append((family, socktype, proto, sa))
    if not cands:
        raise OSError(f"getaddrinfo({host!r}) returned no addresses")
    if len(cands) == 1:
        return socket.create_connection(address, timeout, source_address)
    cands = cands[:_RACE_MAX]

    deadline = None if budget is None else time.monotonic() + budget
    lock = threading.Lock()
    done = threading.Event()
    state: dict[str, Any] = {"left": len(cands), "winner": None, "err": None, "settled": False}

    def _close_quiet(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass

    def _attempt(family: int, socktype: int, proto: int, sa: Any) -> None:
        sock: socket.socket | None = None
        try:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise TimeoutError("connect budget exhausted")
            sock = socket.socket(family, socktype, proto)
            if left is not None:
                sock.settimeout(left)
            elif budget is not None:
                sock.settimeout(budget)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
        except BaseException as exc:
            if sock is not None:
                _close_quiet(sock)
            with lock:
                if state["err"] is None:
                    state["err"] = exc
                state["left"] -= 1
                last = state["left"] == 0
            if last:
                done.set()
            return
        with lock:
            state["left"] -= 1
            lost = state["settled"] or state["winner"] is not None
            if not lost:
                state["winner"] = sock
        if lost:
            _close_quiet(sock)
        else:
            done.set()

    threads = [
        threading.Thread(target=_attempt, args=c, name="race-connect", daemon=True) for c in cands
    ]
    for t in threads:
        t.start()
    done.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
    with lock:
        # 置位后晚到的成功者会自关（见 _attempt）——否则主线程放弃 = 漏一个 fd。
        state["settled"] = True
        winner: socket.socket | None = state["winner"]
        err = state["err"]
    if winner is not None:
        if budget is not None:
            winner.settimeout(budget)  # 连接超时交回调用方语义（后续 read/write 用）
        return winner
    if err is not None:
        raise err
    raise TimeoutError(f"connect to {host}:{port} timed out")


class _RacingHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # CPython 把 _create_connection 存成**实例**属性，源码注释明写「to allow unit
        # tests to replace it with a suitable mockup」——这就是留好的缝，不必重写 connect()
        # （重写要自己复刻 TCP_NODELAY / _tunnel() / TLS wrap，漏一条就是隐性回归）。
        self._create_connection = racing_create_connection


class _RacingHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = racing_create_connection


def racing_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    """建一条**竞速 connect** 的（未发起请求）连接：长连接池与测试共用这一处。

    为什么不自建 `http.client` 子类：竞速 connect 的修复（§149 的 IPv6 税）只住在这两个
    子类里，第二份实现迟早漂移成两种 connect 行为。
    """
    if str(scheme).lower() == "https":
        return _RacingHTTPSConnection(host, port, timeout=timeout)
    return _RacingHTTPConnection(host, port, timeout=timeout)


class _RacingHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: Any):
        return self.do_open(_RacingHTTPConnection, req)


class _RacingHTTPSHandler(urllib.request.HTTPSHandler):
    #: typeshed 没声明它，但 `HTTPSHandler.__init__` 运行时一定设（urllib 自己也这么读）。
    _context: Any

    def https_open(self, req: Any):
        return self.do_open(_RacingHTTPSConnection, req, context=self._context)


def racing_opener(no_proxy: bool = False) -> urllib.request.OpenerDirector:
    """带竞速 connect 的 opener；其余 handler 与 `urlopen` 的缺省 opener 同款。

    `no_proxy=False` 时**不**塞 ProxyHandler ⇒ `build_opener` 自己按环境装一个，
    代理语义与 `urllib.request.urlopen` 逐字一致（云机侧的显式代理需求不受影响）。
    """
    cached = _RACING_OPENERS.get(no_proxy)
    if cached is None:
        handlers: list[Any] = [_RacingHTTPHandler, _RacingHTTPSHandler]
        if no_proxy:
            handlers.append(urllib.request.ProxyHandler({}))
        cached = urllib.request.build_opener(*handlers)
        _RACING_OPENERS[no_proxy] = cached
    return cached


def racing_urlopen(req: Any, timeout: float = 30.0):
    """节点通信的出口：**竞速 connect** + 回环绕代理（`urlopen` 的多地址版）。

    与 `urlopen` 的分工：hub↔worker 的本机回环面继续用 `urlopen`（单地址，竞速无收益，
    且那条 `monkeypatch.setattr("urllib.request.urlopen", …)` 的缝要留住）；trainer→采样
    节点（`common.distribution._request`）走这里——云机隧道主机名才是多地址的那个。
    """
    url = getattr(req, "full_url", None) or str(req)
    return racing_opener(no_proxy=bypass_proxy(url)).open(req, timeout=timeout)
