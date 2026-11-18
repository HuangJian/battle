"""remote/rollout_relay.py — 采样节点的「主动会合」通道（2026-11-18）。

**它解决的问题**（真机现场，ModelScope / 阿里云 8C32G）：

    预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail
    cloudflared 停在 "Requesting new quick Tunnel on trycloudflare.com..." 16s 后 rc=1

这台机器建不出任何 quick tunnel（建隧道那次 443 与边缘 7844 都不通），但 `github.com` /
`registry.npmmirror.com` / **hub 的 `*.trycloudflare.com`** 都通 —— 缺的只是「hub 打进来」这半条。
于是把方向翻过来：节点**出站**长轮询 hub，把本该发给它的请求在本机执行，再把回执交回来。
hub 侧实现与协议见 `hub/relay.py` 头部。

**三条纪律**：

1. **本模块只移动字节，不解释业务**：`/v1/*` 的语义、鉴权头、幂等键全部原样透传 —— 解释
   第二遍就是第二份真相（agent 与 trainer 之间的线上契约不经过这里）；
2. **本地执行只打 `127.0.0.1`，且绕开代理**（`common.net_http.urlopen`）：本机环境常带
   `HTTP_PROXY`，塞进代理会把 loopback 请求送出去再转回来（`net_http` 头部就是这条事故）；
3. **轮询失败只退避，不重注册**：这条通道的存活由 `rollout_node` 的监护循环判（线程全停 ⇒
   重建），这里不碰 `reg_state`/`url` —— 那两份状态归 supervisor。
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from common import net_http

#: 单条长轮询的挂起时长（秒）；hub 侧 `POLL_WAIT_MAX=60` 是它的上限。
POLL_WAIT_SEC = 20.0
#: 轮询线程数：agent 侧同时可能有多条请求（权重 POST / 取任务 / 取结果 / 探活），
#: 少了会出现队头阻塞（一条 20s 的 `_poll` 会把下一次 ping 排到后面）。
POLL_WORKERS = 6
#: 轮询失败后的退避（秒）：hub 短暂 5xx / 本机抖动都不该打满 CPU。
BACKOFF_SEC = 2.0
#: 单条请求体的上限（权重包 ~几 MB；超过它一定是把别的什么东西塞进来了）。
BODY_MAX = 32 * 1024 * 1024
#: 本地 agent 的执行上限（秒）：比 hub 侧最长的调用等待（weights 150s）略短，
#: 这样「agent 卡住」会变成一条 502 回执而不是让调用方等到 504。
EXEC_TIMEOUT_SEC = 140.0
#: 回执投递的预算（秒）：小请求，抖动重试留给下一轮。
REPLY_TIMEOUT_SEC = 30.0
#: 通道预检的单次预算（秒）：DNS + 每条路由一次 `_poll?wait=0`。
#: 它的读者只有一行日志，所以 = `CLOUDFLARED_PROBE_TIMEOUT` 的两倍量级足够。
PROBE_TIMEOUT_SEC = 6.0
#: 同一条失败连续刷屏的上限：6 个线程同拍超时会打出 6 行**完全一样**的话，
#: 而真因（host/reason）只需要看第一行。之后每 N 条汇总一次。
ERR_LOG_EVERY = 10

#: 不该透传给本地 agent 的逐跳头（`Host` 必须重算成本机地址；长度由 urllib 自己算）。
_HOP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "te", "trailer"}
)


class RelayUnreachable(urllib.error.URLError):
    """每条路由都失败（`reason` = `继承代理=timeout；直连=ok` 这种逐路由结论）。

    继承 `URLError`：上层 `except Exception` / `except URLError` 的语义不变
    （`rollout_node` 与 `_worker` 都按基类收），只是**读得出是什么坏了**。
    """


def _err_text(e: BaseException) -> str:
    """一条异常 → 一句话死因（host 由调用方补；这里只管「怎么坏的」）。

    为什么值得写这么长：2026-11-18 DSW 现场 6 个线程各卡 35s，日志只有
    `relay 轮询失败（URLError）`——`URLError` 同时意味着 DNS 失败、连接被拒、TLS 失败、
    代理吊死与地址黑洞，等于没写。一句 `timeout` / `refused` / `getaddrinfo` 能把人
    直接送到修法上（预检、代理、hub_url）。
    """
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    reason = getattr(e, "reason", None)
    if reason is None:
        return f"{type(e).__name__}: {e}".strip()
    text = reason if isinstance(reason, str) else f"{type(reason).__name__}: {reason}".strip()
    low = text.lower()
    if "timed out" in low or isinstance(reason, (TimeoutError,)):
        return "timeout（连接/读取全程无应答 —— 地址被黑洞、代理吊死或该端口不回话）"
    if "refused" in low:
        return "refused（对端端口没开 / 只绑了回环）"
    if "getaddrinfo" in low or "name or service not known" in low:
        return "DNS 解析失败（先查域名拼写与本机 resolver）"
    if "certificate" in low or "ssl" in low:
        return f"TLS 失败（{text[:80]}）"
    return text[:120]


def _route_open(route: str, req: Any, timeout: float):
    """按路由发一条请求，**两条路都并行竞速多地址**：`inherit` 挂进程代理，`direct` 摘掉代理。

    竞速不是优化而是修 bug：云机的隧道域名常解析出 2 个 IPv6 + 2 个 IPv4（2026-11-18 实测
    `2606:4700::6810:e784(v6),…,104.16.230.132(v4)`），而 `socket.create_connection` 串行试
    且 timeout 是**每地址**的 ⇒ 前面那几个地址黑洞时墙钟 = N×timeout
    （`common/net_http.py` 头部 2026-11-11 实测 16.24s vs 0.21s；2026-11-18 DSW 现场就是
    每条长轮询卡满 35s）。竞速把它压成一次预算，第一个成功的赢。
    """
    if route == "direct":
        return net_http.racing_opener(no_proxy=True).open(req, timeout=timeout)
    return net_http.racing_opener().open(req, timeout=timeout)


def _host_port(url: str) -> tuple[str, int]:
    """`hub_url` → `(host, port)`：**显式端口优先**，没有才按 scheme 取 80/443。

    别用「有没有 `https://` 就判 443」：自建 hub 常写成 `http://1.2.3.4:8787`，
    那样会把 `:8787` 吞掉、日志指着一个没人监听的 80 端口。
    """
    parts = urllib.parse.urlsplit(str(url or ""))
    host = parts.hostname or net_http.host_of(url) or "(空)"
    if parts.port:
        return host, int(parts.port)
    return host, 443 if (parts.scheme or "").lower() == "https" else 80


def probe_hub(
    hub_url: str, token: str, node_id: str, timeout: float = PROBE_TIMEOUT_SEC
) -> tuple[bool, str, dict[str, Any]]:
    """relay 预检：**一条**人读日志 + 一份结构化读数（`(ok, line, facts)`）。

    真机事故（2026-11-18 DSW）就是这段缺失：hub 公网地址完全正确（本机 1.2s 401），
    节点侧却每条长轮询卡满 35s 才 `URLError`，而日志既没打地址也没打 reason ——
    人只能猜是 hub 未升级、令牌错还是出网断。有了这行，猜的那一步消失。

    为什么还回 `facts`：判据要能被**结构化**消费（`dns` / `routes` 逐条结论），
    否则用例只能对着日志措辞断言 —— 改一个词就红，而红得没有意义。
    """
    host, port = _host_port(hub_url)
    dns = _dns_summary(host, port)
    facts: dict[str, Any] = {"host": host, "port": port, "dns": dns, "routes": {}}
    url = f"{str(hub_url).rstrip('/')}/relay/{node_id}/_poll?wait=0"
    ok_route: list[str] = []
    fails: list[str] = []
    # 回环 / `NO_PROXY` 点名的目标只走直连：否则本机那台 LAN 代理会把一条「本来必然通」
    # 的回环请求也吊到 timeout，日志里就多出一条吓人的 `inherit=timeout`（实测 6s/次）。
    routes = ["direct"] if net_http.bypass_proxy(hub_url) else ["inherit", "direct"]
    for route in routes:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        t0 = time.monotonic()
        try:
            with _route_open(route, req, timeout) as r:
                r.read(4096)
        except urllib.error.HTTPError as e:  # 401/404 也是**应答**：路由通了
            ms = (time.monotonic() - t0) * 1000
            facts["routes"][route] = f"http-{e.code}"
            ok_route.append(f"{route}=HTTP {e.code}（{ms:.0f}ms）")
            continue
        except Exception as e:
            facts["routes"][route] = "fail"
            fails.append(f"{route}={_err_text(e)}")
            continue
        ms = (time.monotonic() - t0) * 1000
        facts["routes"][route] = "ok"
        ok_route.append(f"{route}=ok（{ms:.0f}ms）")
    line = f"relay 预检 {host}:{port} → DNS={dns} ｜ " + " ｜ ".join(ok_route + fails)
    if not ok_route:
        line += (
            " ⇒ 两条路由都不通：hub_url 拼写/DNS、节点出网策略或代理（CFG.net_proxy）"
            "；hub 未升级只会 404，不会超时"
        )
    return bool(ok_route), line, facts


def _dns_summary(host: str, port: int) -> str:
    """DNS 读数的紧凑表示：`104.21.x.x(v4),2606:4700::1(v6)` / `fail`。

    地址族要显式标出来：云机上「解析出 IPv6 但没有可用 IPv6 路由」是最常见的假死，
    而这一点在 `URLError` 一句话里永远看不见。
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as e:
        return f"fail({type(e).__name__})"
    out: list[str] = []
    for family, _t, _p, _c, sa in infos:
        tag = "v4" if family == socket.AF_INET else ("v6" if family == socket.AF_INET6 else "?")
        item = f"{sa[0]}({tag})"
        if item not in out:
            out.append(item)
    return ",".join(out[:6]) or "empty"


def _pick_route(facts: dict[str, Any]) -> str:
    """预检读数 → 该钉哪条路由（`""` = 都不可用，别钉）。

    优先 `inherit`：`HTTPS_PROXY` 被显式配好时，出网大概率要它；`direct` 只是它的兜底。
    """
    routes = facts.get("routes") if isinstance(facts, dict) else None
    if not isinstance(routes, dict):
        return ""
    for route in ("inherit", "direct"):
        verdict = str(routes.get(route) or "")
        if verdict == "ok" or verdict.startswith("http-"):
            return route
    return ""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw or b"").decode("ascii")


def _forward(port: int, req: dict[str, Any]) -> dict[str, Any]:
    """在本机 agent 上执行一条被转发的请求；**绝不抛**（失败也回一条可读的回执）。"""
    method = str(req.get("method") or "GET").upper()
    path = str(req.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    try:
        body = base64.b64decode(str(req.get("body_b64") or ""))
    except (ValueError, TypeError):
        return _reply_dict(400, {"error": "body_b64 非法"})
    if len(body) > BODY_MAX:
        return _reply_dict(413, {"error": f"请求体超上限（{len(body)} > {BODY_MAX}）"})
    headers = {str(k): str(v) for k, v in (req.get("headers") or {}).items() if v}
    target = f"http://127.0.0.1:{port}{path}"
    try:
        r = urllib.request.Request(
            target, data=(body if method not in ("GET", "HEAD") else None), method=method
        )
        for key, val in headers.items():
            if key.lower() in _HOP_HEADERS:
                continue
            r.add_header(key, val)
        with net_http.urlopen(r, timeout=EXEC_TIMEOUT_SEC) as resp:
            raw = resp.read(BODY_MAX)
            return {
                "status": int(getattr(resp, "status", 200)),
                "headers": dict(getattr(resp, "headers", {}).items()),
                "body_b64": _b64(raw),
            }
    except urllib.error.HTTPError as e:  # 4xx/5xx 是**应答**，不是转发失败
        try:
            raw = e.read(BODY_MAX)
        except Exception:
            raw = b""
        return {
            "status": int(e.code),
            "headers": dict(e.headers.items()) if e.headers else {},
            "body_b64": _b64(raw),
        }
    except Exception as e:  # agent 没起/端口不通/超时
        return _reply_dict(502, {"error": f"relay 本地执行失败: {type(e).__name__}"})


def _reply_dict(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": int(status),
        "headers": {"Content-Type": "application/json"},
        "body_b64": _b64(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
    }


class RelayLink:
    """节点侧的会合通道：N 个长轮询线程 + 一组计数器。

    `local_port` 由 hub 的 `_poll` 应答告知（节点只知道 hub 的**公网**地址，而注册 URL 要填
    **本机回环**端口 —— 那两者可以不同）。supervisor 用它拼注册 URL。
    """

    def __init__(
        self,
        *,
        hub_url: str,
        token: str,
        node_id: str,
        agent_port: int,
        log: Callable[[str], None],
        workers: int = POLL_WORKERS,
        poll_wait: float = POLL_WAIT_SEC,
    ) -> None:
        self.hub_url = str(hub_url or "").rstrip("/")
        self.token = str(token or "")
        self.node_id = str(node_id or "")
        self.agent_port = int(agent_port)
        self.log = log
        self.poll_wait = max(1.0, float(poll_wait))
        self._workers = max(1, int(workers))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.local_port = 0
        # 计数器住 dict 而不是 `setattr`：锁内用 `getattr/setattr` 的目标是静态扫描看不清的
        # 「锁内不透明调用」（`tests/test_lock_reentrancy.py` 会红）——而这里恰好每拍都在锁内加一。
        self._counters: dict[str, int] = {"polls": 0, "served": 0, "errors": 0}
        self._started_at = 0.0
        #: 上一次**成功**的路由（`inherit` = 走进程代理，`direct` = 摘掉代理）。
        #: 只用它把成功过的路由排到前面：失败一次就换另一条，两条都不行再报。
        self._route = ""
        #: 最近一次失败的逐路由结论（`RelayChannel` 放弃时把它打出来）。
        self._last_error = ""
        self._err_seen = 0

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        self._started_at = time.time()
        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker, name=f"relay-{i}", daemon=True
            )
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()

    def alive(self) -> bool:
        """至少一个轮询线程还活着（全停 = 通道已死，supervisor 据此重建）。"""
        return any(t.is_alive() for t in self._threads) and not self._stop.is_set()

    @property
    def polls(self) -> int:
        return int(self._counters.get("polls", 0))

    @property
    def served(self) -> int:
        return int(self._counters.get("served", 0))

    @property
    def errors(self) -> int:
        return int(self._counters.get("errors", 0))

    def wait_local_port(self, timeout: float = 40.0) -> int:
        """等 hub 告知本机端口；拿不到 ⇒ 0（本轮注册不可用，别拿错端口硬填）。"""
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            if self.local_port:
                return self.local_port
            if not self.alive():
                return 0
            time.sleep(0.2)
        return self.local_port

    def registration_url(self, port: int | None = None) -> str:
        """注册 URL：**本机回环 + hub 端口**（trainer 与 hub 同机，这个地址对它恒可达）。"""
        p = int(port or self.local_port)
        if not p or not self.node_id:
            return ""
        return f"http://127.0.0.1:{p}/relay/{self.node_id}"

    def stats_line(self) -> str:
        uptime = int(time.time() - self._started_at) if self._started_at else 0
        return (
            f"relay polls={self.polls} served={self.served} errors={self.errors} "
            f"route={self._route or '?'} workers={self._workers} 秒={uptime}"
        )

    @property
    def last_error(self) -> str:
        """最近一次失败的逐路由结论（空 = 还没失败过）。"""
        return self._last_error

    @property
    def hub_host(self) -> str:
        """`host:port`（日志用；不带 scheme，免得把令牌/路径混进来）。"""
        host, port = _host_port(self.hub_url)
        return f"{host}:{port}"

    # ---------------------------------------------------------------- 轮询

    def _route_order(self) -> list[str]:
        """路由尝试顺序：回环/NO_PROXY 点名的目标**只能**直连；其余先试上次成功的。

        「先代理再直连」不是保险丝而是**真机必需**：平台（PAI-DSW / 企业网关）常把
        `HTTPS_PROXY` 预置好，而 cloudflared 这种 Go 二进制不走代理 ⇒ 「隧道能建、
        python 请求全吊死」是完全可能的组合（2026-11-18 DSW 的头号嫌疑）。
        """
        if net_http.bypass_proxy(self.hub_url):
            return ["direct"]
        rest = ["inherit", "direct"]
        if self._route in rest:
            rest.remove(self._route)
            return [self._route, *rest]
        return rest

    def _open(self, req: Any, timeout: float):
        """按路由发请求：第一条成功的胜；全崩 ⇒ `RelayUnreachable`（带逐路由结论）。"""
        fails: list[str] = []
        for route in self._route_order():
            try:
                resp = _route_open(route, req, timeout)
            except urllib.error.HTTPError:
                # 4xx/5xx 是**应答**：路由通了（401 = 令牌、404 = hub 没升级），别换路由重试。
                self._note_route(route)
                raise
            except Exception as e:
                fails.append(f"{route}={_err_text(e)}")
                continue
            self._note_route(route)
            return resp
        self._last_error = "；".join(fails)
        raise RelayUnreachable(self._last_error)

    def _note_route(self, route: str) -> None:
        if self._route != route:
            self._route = route

    def pin_route(self, route: str) -> None:
        """钉住预检验过的那条路由：否则**第一拍**会先撞错的那条，白烧一整个长轮询预算
        （代理吊死的机器上 = 35s > `PORT_WAIT_SEC`，首轮注册直接判死）。"""
        if route in ("inherit", "direct"):
            self._route = route

    def _worker(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                answer = self._poll()
            except Exception as e:
                self._bump("errors")
                if self._stop.is_set():
                    return
                self._log_failure(e, time.monotonic() - started)
                self._stop.wait(BACKOFF_SEC)
                continue
            self._bump("polls")
            if not isinstance(answer, dict):
                continue
            port = int(answer.get("local_port") or 0)
            if port:
                self.local_port = port
            req = answer.get("req")
            if not isinstance(req, dict) or not req.get("rid"):
                continue  # 空拍（长轮询超时）= 正常
            resp = _forward(self.agent_port, req)
            # 计「已执行」先于投递回执：回执丢了也算服务过一次（否则观测面会把干过的活报成 0）。
            self._bump("served")
            try:
                self._post_reply(str(req["rid"]), resp)
            except Exception as e:
                self._bump("errors")
                self.log(
                    f"relay 回执投递失败（{_err_text(e)}）—— 调用方会看到超时（hub={self.hub_host}）"
                )

    def _log_failure(self, e: BaseException, elapsed: float) -> None:
        """首条打全（host + 死因 + 耗时），之后每 `ERR_LOG_EVERY` 条汇总一次。

        6 个线程同拍超时会打出 6 行一模一样的话 —— 真因只看第一行，其余是噪声。
        """
        self._err_seen += 1
        head = f"relay 轮询失败（hub={self.hub_host}，{elapsed:.1f}s）"
        if self._err_seen == 1:
            self.log(f"{head}：{_err_text(e)}；{BACKOFF_SEC:g}s 后重试")
            return
        if self._err_seen % ERR_LOG_EVERY == 0:
            self.log(f"{head}：累计 {self._err_seen} 次，最近一次 {_err_text(e)}")

    def _poll(self) -> dict[str, Any] | None:
        url = f"{self.hub_url}/relay/{self.node_id}/_poll?wait={self.poll_wait:g}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        with self._open(req, self.poll_wait + 15.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else None

    def _post_reply(self, rid: str, resp: dict[str, Any]) -> None:
        url = f"{self.hub_url}/relay/{self.node_id}/_reply/{rid}"
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with self._open(req, REPLY_TIMEOUT_SEC) as r:
            r.read()

    def _bump(self, name: str) -> None:
        with self._lock:
            self._counters[name] = int(self._counters.get(name, 0)) + 1


#: 起好后等 hub 告知本机端口的预算（秒）。hub 未升级（`/relay/` 404）或出网断 ⇒ 到点放弃。
PORT_WAIT_SEC = 40.0


class RelayChannel:
    """relay 通道的**所有者**：连接参数 + 生命周期 + 监护判据。

    `rollout_node` 只持有它、只问两件事：「起得来吗（`open`）」与「还活着吗（`ensure`）」。
    为什么不留个 `_open_relay` 在 supervisor 里：通道状态（线程 / 端口 / URL）一旦有两份写者，
    「重建后忘清注册 URL」就是下一个事故；而且 supervisor 文件本来就顶在 1000 行预算上。
    """

    def __init__(
        self,
        *,
        hub_url: str,
        token: str,
        node_id: str,
        agent_port: int,
        log: Callable[[str], None],
        wait_sec: float = PORT_WAIT_SEC,
    ) -> None:
        self._args: dict[str, Any] = {
            "hub_url": hub_url,
            "token": token,
            "node_id": node_id,
            "agent_port": agent_port,
            "log": log,
        }
        self.log = log
        self._wait_sec = max(0.0, float(wait_sec))
        self.link: RelayLink | None = None

    def open(self, reason: str) -> str:
        """起通道并等端口；成功返回注册 URL，失败返回 `""`（已 stop、已打日志）。

        起线程**之前**先跑一次 `probe_hub`：它用一条日志说清「DNS 给了什么地址 +
        哪条路由通」，把「40s 静默后才说 hub 未应答」压成「6s 内点名死因」。
        预检失败**不**短路：一次抖动不该让整台节点挂掉，通道照起（日志已经点名了）。
        """
        self.log(f"relay 通道（{reason}）：节点出站长轮询 hub，不做入站、不建隧道")
        link = RelayLink(**self._args)
        ok, line, facts = probe_hub(
            link.hub_url, link.token, link.node_id, timeout=PROBE_TIMEOUT_SEC
        )
        self.log(line)
        picked = _pick_route(facts) if ok else ""
        if picked:
            link.pin_route(picked)
        if not ok:
            self.log("★ relay 预检不通 —— 上面那行是逐路由结论；通道仍会重试，但先修它再谈注册")
        link.start()
        url = link.registration_url(link.wait_local_port(self._wait_sec))
        if not url:
            link.stop()
            detail = link.last_error or "长轮询无任何应答"
            self.log(
                f"relay 未就绪（{self._wait_sec:g}s 内 hub 未应答长轮询，hub={link.hub_host}）"
                f"：{detail}"
            )
            self.log(
                "  → hub 未升级只会 404（预检那行会写 HTTP 404）；401 = 令牌不一致；"
                "timeout/DNS = 地址或出网/代理问题（relay 不需要入站，也不需要 cloudflared）"
            )
            self.link = None
            return ""
        self.link = link
        self.log(f"relay 就绪：url={url}（hub 调本机回环，节点侧不需要任何入站端口）")
        return url

    @property
    def url(self) -> str:
        return self.link.registration_url() if self.link is not None else ""

    def alive(self) -> bool:
        return self.link is not None and self.link.alive()

    def supervise(self, reason: str = "relay 轮询线程已停") -> str:
        """监护一拍：活着 ⇒ 当前 URL；死了 ⇒ 停旧、重开（返回新 URL 或 `""`）。"""
        if self.alive():
            return self.url
        if self.link is not None:
            self.log(f"★ {reason} —— 重建通道")
            self.link.stop()
            self.link = None
        return self.open(reason)

    def stop(self) -> None:
        if self.link is not None:
            self.link.stop()
            self.link = None

    def stats_line(self) -> str:
        return self.link.stats_line() if self.link is not None else "relay 未启动"


__all__ = [
    "BODY_MAX",
    "POLL_WAIT_SEC",
    "POLL_WORKERS",
    "PORT_WAIT_SEC",
    "PROBE_TIMEOUT_SEC",
    "RelayChannel",
    "RelayLink",
    "RelayUnreachable",
    "probe_hub",
]
