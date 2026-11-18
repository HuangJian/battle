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
import errno
import json
import socket
import ssl
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
#: 预检失败后逐 IP 的裸 TCP 探测预算（秒）：只分「连得上 / 连不上」，2s 足够；
#: 它和 `PROBE_TIMEOUT_SEC` 分开，是因为这层要的是**区分**地址而不是等应答。
TCP_PROBE_SEC = 2.0
#: 裸 TLS 握手预算（秒）：CF 边缘握手正常 ~150ms，3s 还不回 ServerHello 就是被中间层丢了。
#: 比 HTTPS 的 6s 短，因为这一层只需要「握手能不能起来」。
TLS_PROBE_SEC = 3.0
#: TLS 对照主机 ①：**同一个 CF 边缘、但不在 `trycloudflare.com` 这个域名下**。
#:
#: 2026-11-18 第三次真机暴露出旧取值的盲点：旧对照写成 `api.trycloudflare.com`，与 hub 域名**同属
#: 一个后缀** ⇒ 封锁若是「按 `trycloudflare.com` 这一段掐」的，对照必然一起卡，判词就落到
#: `tls-blocked`（「整家 CF 边缘被掐 ⇒ 走代理 / 换机器」），把**最便宜那档（换个域名、同一边缘）**
#: 从选项里抹掉 —— 恰恰是把人往最贵的修法上推。`cloudflare.com` 与 hub 同边缘、不同注册域，
#: 才问得出「按域名掐 还是 整家 CF 被掐」。
CTRL_HOST = "cloudflare.com"
#: TLS 对照主机 ②：**完全不在 CF 上**（阿里云 CDN；DSW 上装 bun 就走它）。
#: 它通 ⇒ 本机 TLS 出网是好的，故障只在 CF 这一家；它也卡 ⇒ 本机 TLS 出网整体被中间层掐。
CTRL_HOST_NET = "registry.npmmirror.com"
#: CF 边缘的**备用 HTTPS 端口**（官方支持 443 / 2053 / 2083 / 2087 / 2096 / 8443，无其他）。
#:
#: 2026-11-18 DSW 实测（用户在该机直接跑 curl）：`443` 的握手被中间层丢弃（`000`），
#: 而 `2053 / 2083 / 2087 / 2096` 全部回 **401** —— TLS + HTTP 整条链都通、hub 自己的鉴权
#: 在回话；`8443` 也被掐。⇒ 那个中间层只盯 443（和 8443）这两个「标准 HTTPS 端口」。
#: 这比换域名便宜一个量级：**同一台机器、同一个域名、hub 一个字节都不用动**。
#: 顺序按实测快慢排（2083/2096 最快），不是按大小。
ALT_HTTPS_PORTS = (2083, 2096, 2053, 2087, 8443)
#: 值得再试一遍备用端口的判词：共同点是**只在 hub 那个端口上失败**（换端口可能就有救）。
#: `app-stall`（握手成功、只有 HTTP 不回）故意不在内：那时 hub 自己有问题，换端口一样不回。
_SWEEP_HINTS = frozenset({"blackhole", "sni-blocked", "tls-blocked", "cf-blocked", "tls-error"})
#: 网关类状态码：**中间层（代理 / Cloudflare 边缘）到不了上游**，因此不算「路由通」。
#: 502/504 来自代理或 CF；52x 是 CF 的 origin 不可达族；530 是 CF 隧道/DNS 解析失败。
#: `503` 故意不在内：hub 自己的 503（磁盘熔断）说明链路是通的，那是业务拒绝。
GATEWAY_CODES = frozenset({502, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530})
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


def with_port(url: str, port: int) -> str:
    """换成另一个端口的同一个地址（scheme / host / path 原样保留）。

    单独一个函数是因为「拼 URL」最容易出的错是**裸 IPv6 少了方括号**（`::1:2083` 不是地址），
    而那正好会发生在自建 hub 用 `http://[::1]:8787` 的场合。
    """
    parts = urllib.parse.urlsplit(str(url or ""))
    host = parts.hostname or net_http.host_of(url) or ""
    netloc = f"[{host}]" if ":" in host else host
    return urllib.parse.urlunsplit(
        (parts.scheme, f"{netloc}:{port}", parts.path, parts.query, parts.fragment)
    )


def _port_sweep(
    host: str, ports: tuple[int, ...], token: str, node_id: str, timeout: float
) -> dict[int, str]:
    """并行对备用端口各发**一次真的 HTTPS 预检**（不是裸握手）。

    只有真请求才算数：`gateway-5xx` 说明 CF 边缘够不到上游（端口通但 hub 不在），
    那和「端口被掐」是两件事 —— 换过去只会从「超时」变成「530」。
    """
    out: dict[int, str] = {}

    def _sweep_port(port: int) -> None:
        url = f"https://{host}:{port}/relay/{node_id}/_poll?wait=0"
        verdict, _human = _probe_one_route("direct", url, token, timeout)
        out[port] = verdict

    threads = [threading.Thread(target=_sweep_port, args=(p,), daemon=True) for p in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {p: out.get(p, "fail") for p in ports}


def _probe_one_route(route: str, url: str, token: str, timeout: float) -> tuple[str, str]:
    """单条路由的一次 HTTPS 预检 → `(verdict, 人读片段)`。

    `verdict ∈ {"ok", "http-<code>", "gateway-<code>", "fail"}`。**4xx 算通**（对端应用回了话），
    而 **502/504/52x/530 算不通**：那是代理或 CF 边缘「我连不上上游」的应答 —— 它回话很快，
    但路由并没有通。把两者混成「HTTPError ⇒ 通」会让预检在代理吊死时给出 `ok=True`，
    接着每一拍长轮询都拿 502（2026-11-18 实测：LAN 代理对黑洞地址回 502/5047ms）。
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    t0 = time.monotonic()
    try:
        with _route_open(route, req, timeout) as r:
            r.read(4096)
    except urllib.error.HTTPError as e:  # 401/404 是**应答**：路由通了；502/530 不是
        ms = (time.monotonic() - t0) * 1000
        if e.code in GATEWAY_CODES:
            return f"gateway-{e.code}", f"{route}=网关 {e.code}（{ms:.0f}ms）"
        return f"http-{e.code}", f"{route}=HTTP {e.code}（{ms:.0f}ms）"
    except Exception as e:
        return "fail", f"{route}={_err_text(e)}"
    return "ok", f"{route}=ok（{(time.monotonic() - t0) * 1000:.0f}ms）"


def _tcp_verdict(ip: str, port: int, timeout: float) -> str:
    """对单个解析地址做一次裸 TCP connect，回一个**短**结论。

    这一层是 `_err_text` 看不清的地方：HTTPS 超时可能是 ①IP 被黑洞 ②TCP 通但 TLS/SNI 被掐
    ③本机没有该地址族的路由（立刻 ENETUNREACH）。三者的修法完全不同，而都只表现为
    `timeout` 或 `URLError`。
    """
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((ip, port))
    except TimeoutError:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except OSError as e:
        if e.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL):
            return "unreachable"
        return f"errno{e.errno}"
    return "ok"


def _tcp_sweep(ips: list[str], port: int, timeout: float) -> dict[str, str]:
    """并行探所有解析地址（**串行 4×timeout 就是又一份 8s 的空白**）。"""
    out: dict[str, str] = {}

    def _sweep_addr(ip: str) -> None:
        out[ip] = _tcp_verdict(ip, port, timeout)

    threads = [threading.Thread(target=_sweep_addr, args=(ip,), daemon=True) for ip in ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {ip: out.get(ip, "?") for ip in ips}


def _tls_verdict(ip: str, port: int, sni: str, timeout: float) -> tuple[str, str]:
    """对单个地址做一次**裸 TLS 握手**（不发 HTTP），返回 `(verdict, 人读片段)`。

    为什么还要这一层（2026-11-18 DSW 第二次真机）：逐 IP 的 TCP 已经全 `ok`、HTTPS 却全超时 ——
    但「TCP 通 + HTTPS 超时」仍然同时意味着三件事，而三件事的修法完全不同：
      ① 握手就被丢（DPI 按 SNI 过滤 / 路径 MTU 黑洞：ClientHello 出去，ServerHello 回不来）；
      ② 握手成功、HTTP 请求无应答（hub/隧道那一侧不回话）；
      ③ 握手被中间人顶掉（证书校验失败）。
    这一层只花一条握手的钱，就把 ①②③ 分开。`verdict ∈ {"ok:<TLS版本>", "timeout", "err:<原因>"}`。
    """
    ctx = ssl.create_default_context()
    t0 = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            raw.settimeout(timeout)
            with ctx.wrap_socket(raw, server_hostname=sni) as tls:
                ms = (time.monotonic() - t0) * 1000
                ver = tls.version() or "?"
                return f"ok:{ver}", f"{ver}（{ms:.0f}ms）"
    except TimeoutError:
        return "timeout", "握手无应答"
    except ssl.SSLCertVerificationError as e:  # 子类先判：中间人/证书链不对
        return f"err:cert:{e.verify_message or 'verify failed'}", "证书校验失败（中间人？）"
    except ssl.SSLError as e:
        return f"err:tls:{e.reason or e.__class__.__name__}", f"TLS 错误（{e.reason or e}）"
    except OSError as e:  # 连接期失败（连不上/被拒/不可路由）
        return f"err:conn:{e.errno}", f"连接失败（errno={e.errno}）"


def _tls_sweep(ips: list[str], port: int, sni: str, timeout: float) -> dict[str, tuple[str, str]]:
    """并行对每个地址做裸 TLS（有多个 IP 时串行 = N×timeout，没意义）。"""
    out: dict[str, tuple[str, str]] = {}

    def _sweep_tls(ip: str) -> None:
        out[ip] = _tls_verdict(ip, port, sni, timeout)

    threads = [threading.Thread(target=_sweep_tls, args=(ip,), daemon=True) for ip in ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {ip: out.get(ip, ("timeout", "?")) for ip in ips}


def _fail_hint(
    tcp: dict[str, str],
    tls: dict[str, str] | None = None,
    ctrl: dict[str, str] | None = None,
    net: dict[str, str] | None = None,
) -> tuple[str, str]:
    """逐 IP 的 TCP（+ TLS、+ 两条对照）结论 → `(kind, 一句该改什么)`。

    `kind` 是**判据**，措辞只是给人看的：用例钉 `kind`，改文案不会把用例弄红
    （否则红得没有意义）。`kind ∈ {dns,blackhole,unroutable,tls-blocked,sni-blocked,
    cf-blocked,tls-error,app-stall}`；`probe_hub` 在备用端口验通后把它改写成
    `port-blocked`（那是「换端口就够」，与这里每一条的修法都不一样）。

    两条对照问的是**两个互不相同**的问题（2026-11-18 第三次 DSW 现场）：

    · `ctrl` = CF 边缘的**别的域名** ⇒ 通 ⇒ 封锁按域名/SNI，换个域名（同一边缘）就够；
    · `net`  = **完全不在 CF 上**的站点 ⇒ 通 ⇒ 本机 TLS 出网是好的，故障只在 CF 这一家
      （`cf-blocked`：换非 CF 入口，**不必**换机器）；它也卡 ⇒ 本机 TLS 出网整体被掐。
    """
    if not tcp:
        return "dns", "DNS 没给出任何地址：hub_url 拼写或节点 DNS 不通"
    vals = set(tcp.values())
    if vals == {"timeout"}:
        return "blackhole", (
            "所有地址连 TCP 都不通（不只是 HTTPS）⇒ 对端是黑洞，重试无用："
            "先排 hub_url 拼写/DNS，再查节点出网策略与 CFG.net_proxy，否则换承载（非 CF 入口）或机器"
        )
    if "ok" not in vals:
        return (
            "unroutable",
            "按上面逐 IP 的结论：refused/unreachable 说明该地址段不可路由（不是「慢」）"
            "—— 查 hub_url 的 DNS 与出网策略",
        )
    # TCP 通了才轮到 TLS 这一层说话（`tls` 为空 = 没探，只能给最保守的那句）。
    if not tls:
        return "tls", "TCP 通而 HTTPS 不通 ⇒ 掐在 TLS 之上的一层：换 hub_url 的域名或走代理"
    tls_vals = set(tls.values())
    if any(v.startswith("ok:") for v in tls_vals):
        return "app-stall", (
            "TCP 与 TLS 都通、只有 HTTPS 请求无应答 ⇒ 问题在 hub 那一侧（进程没在跑 / "
            "quick tunnel 已死但域名还在 / 反向代理挂住）—— 去 hub 机器看服务与隧道，不要再查节点出网"
        )
    if any(v.startswith("err:cert") or v.startswith("err:tls") for v in tls_vals):
        return "tls-error", (
            "TCP 通但握手被回绝（证书/TLS 错误）⇒ 中间层在顶替对端（企业代理 MITM / 证书链不对）："
            "走代理并确认其根证书，或换成不经过该中间层的入口"
        )
    ctrl_vals = list(ctrl.values()) if ctrl else []
    net_vals = list(net.values()) if net else []
    ctrl_ok = any(v.startswith("ok:") for v in ctrl_vals)
    net_ok = any(v.startswith("ok:") for v in net_vals)
    if ctrl_ok:
        return "sni-blocked", (
            f"TCP 通、握手全无应答，而对照 {CTRL_HOST}（同边缘、不同域名）握手成功 ⇒ 按**域名/SNI** 掐："
            "最便宜的一档是**换个域名**（自有域名 + 命名隧道，同一边缘换个名字就够，hub 一个字节都不用动），"
            "其次换入口；这不是「这台机器上不了网」"
        )
    if net_ok:
        # 非 CF 站点握得起来 ⇒ 本机 TLS 出网是好的，被掐的只是 CF 这一家。
        if ctrl:
            return "cf-blocked", (
                f"TCP 通、握手全无应答，对照 {CTRL_HOST}（CF 边缘另一个域名）也卡、而 {CTRL_HOST_NET}"
                "（非 CF）握手成功"
                " ⇒ **只有 CF 这一家被掐**（不是本机没网）：hub 换**非 CF 入口**（自有域名反代 / 内网直连 / "
                "tailnet），或给节点配 CFG.net_proxy；换机器无用"
            )
        # 对照①问不出来（DNS 都没给地址）⇒ 只知道 hub 这个域名被掐。
        return "sni-blocked", (
            f"TCP 通、握手全无应答，而 {CTRL_HOST_NET}（非 CF）握手成功"
            " ⇒ 本机 TLS 出网正常、被掐的是这个域：换域名或换入口；重试无用"
        )
    return "tls-blocked", (
        "TCP 通、握手全无应答（连不在 CF 上的对照主机也一样）⇒ 本机 TLS 出网整体被中间层掐："
        "配 CFG.net_proxy 走出站代理 / 换机器；重试无用"
    )


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
    ips = _resolve_ips(host, port)  # 只解析**一次**：DNS 轮转会让两条日志自相矛盾
    dns = _dns_summary(host, port, ips)
    facts: dict[str, Any] = {"host": host, "port": port, "dns": dns, "routes": {}}
    url = f"{str(hub_url).rstrip('/')}/relay/{node_id}/_poll?wait=0"
    # 回环 / `NO_PROXY` 点名的目标只走直连：否则本机那台 LAN 代理会把一条「本来必然通」
    # 的回环请求也吊到 timeout，日志里就多出一条吓人的 `inherit=timeout`（实测 6s/次）。
    routes = ["direct"] if net_http.bypass_proxy(hub_url) else ["inherit", "direct"]
    got: dict[str, tuple[str, str]] = {}

    def _run(route: str) -> None:
        got[route] = _probe_one_route(route, url, token, timeout)

    if len(routes) == 1:
        _run(routes[0])
    else:
        # 两条腿**并行**：串行就是 2×timeout（DSW 实测 12s），而预检要的只是「通不通」，
        # 两条路之间没有先后依赖；并行还顺带修掉「前一条把预算烧完、后一条没机会说话」。
        ths = [threading.Thread(target=_run, args=(r,), daemon=True) for r in routes]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    ok_route: list[str] = []
    fails: list[str] = []
    for route in routes:  # 保序：人读行的字段顺序不随线程完成顺序漂
        verdict, human = got.get(route, ("fail", f"{route}=fail"))
        facts["routes"][route] = verdict
        # `gateway-*` 也归到「不通」那一栏：它只是回话快，路由并没有通
        if verdict == "ok" or verdict.startswith("http-"):
            ok_route.append(human)
        else:
            fails.append(human)
    line = f"relay 预检 {host}:{port} → DNS={dns} ｜ " + " ｜ ".join(ok_route + fails)
    if not ok_route:
        gw = [f"{r}={v}" for r, v in facts["routes"].items() if str(v).startswith("gateway-")]
        if gw:
            line += (
                f"（{','.join(gw)}：中间层回话快但到不了 hub —— 代理/CF 边缘那一跳断了，不算通）"
            )
        # HTTPS 全不通时补一层**逐 IP 的裸 TCP**：把「IP 黑洞 / TLS 被掐 / 没有 v6 路由」
        # 分开 —— 三者都只表现为 timeout，却对应完全不同的修法（2026-11-18 DSW 现场）。
        tcp = _tcp_sweep(ips, port, TCP_PROBE_SEC)
        facts["tcp"] = tcp
        if tcp:
            line += " ｜ TCP=" + ",".join(f"{ip}:{port} {verdict}" for ip, verdict in tcp.items())
        # TCP 通了才值得再往下切一刀：**裸 TLS 握手**。同一次真机实测（TCP 全 ok、HTTPS 全超时）
        # 就卡在这一层的三种可能之间，而「握手被丢 / 握手成功但 hub 不回话 / 中间人」的修法
        # 互不相干。对照主机（同边缘、不同域名）一起探，用**同一个支付一次**的预算。
        tls: dict[str, str] = {}
        ctrl: dict[str, str] = {}
        net: dict[str, str] = {}
        ok_ips = [ip for ip, verdict in tcp.items() if verdict == "ok"]
        # TLS 这一层只对 `https://` 有意义：`http://1.2.3.4:8787` 上的裸握手必然失败，
        # 那不是「被掐」，把它的失败写成 `tls-blocked` 只会把人送去换机器。
        scheme = (urllib.parse.urlsplit(str(hub_url)).scheme or "").lower()
        if ok_ips and scheme == "https":
            ctl_out: dict[str, dict[str, str]] = {}

            def _ctl_probe(ctl_host: str) -> None:
                # 对照恒用 443：要问的是「本机到那边的 TLS 起不起得来」，与 hub 的端口无关。
                ctl_ips = _resolve_ips(ctl_host, 443)
                if ctl_ips:
                    swept = _tls_sweep(ctl_ips, 443, ctl_host, TLS_PROBE_SEC)
                    ctl_out[ctl_host] = {ip: v for ip, (v, _h) in swept.items()}

            # 两条对照与 hub 那轮 TLS **并行**：多问一家不该再加一份预算。
            ctl_threads = [
                threading.Thread(target=_ctl_probe, args=(h,), daemon=True)
                for h in (CTRL_HOST, CTRL_HOST_NET)
            ]
            for t in ctl_threads:
                t.start()
            tls_out = _tls_sweep(ok_ips, port, host, TLS_PROBE_SEC)
            for t in ctl_threads:
                t.join()
            tls = {ip: v for ip, (v, _h) in tls_out.items()}
            ctrl = ctl_out.get(CTRL_HOST, {})
            net = ctl_out.get(CTRL_HOST_NET, {})
            facts["tls"] = tls
            if ctrl:
                facts["tls_ctrl"] = ctrl
            if net:
                facts["tls_net"] = net
            line += " ｜ TLS=" + ",".join(
                f"{ip} {v}（{human}）" for ip, (v, human) in tls_out.items()
            )
            tails: list[str] = []
            for ctl_host, ctl_got in ((CTRL_HOST, ctrl), (CTRL_HOST_NET, net)):
                if not ctl_got:
                    continue
                vals = list(ctl_got.values())
                # 四个地址各不相同会把这行淹掉；一致时只说一次 + 地址数。
                tails.append(
                    f"{ctl_host}="
                    + (
                        f"{vals[0]}（{len(vals)} 个地址一致）"
                        if len(set(vals)) == 1
                        else ",".join(vals)
                    )
                )
            if tails:
                line += "；对照 " + ",".join(tails)
        kind, hint = _fail_hint(tcp, tls, ctrl, net)
        # 最后一张牌：**换端口**。2026-11-18 DSW 用户在机器上直接验过 —— 同一个域名 443 握手被丢
        # （`000`），而 2053/2083/2087/2096 全回 401（TLS+HTTP 整链通、hub 在回话），8443 也被掐。
        # ⇒ 那个中间层只盯标准 HTTPS 端口。它比「换域名 / 换机器 / 配代理」便宜一个数量级，
        # 所以放在所有判词之后、只在**端口级失败**时问一次。
        if scheme == "https" and kind in _SWEEP_HINTS and port not in ALT_HTTPS_PORTS:
            swept = _port_sweep(
                host, ALT_HTTPS_PORTS, token, node_id, min(timeout, PROBE_TIMEOUT_SEC)
            )
            facts["alt_ports"] = {str(p): v for p, v in swept.items()}
            alt = next(
                (p for p in ALT_HTTPS_PORTS if str(swept.get(p, "")).startswith(("ok", "http-"))),
                0,
            )
            if alt:
                facts["alt_port"] = alt
                kind = "port-blocked"
                hint = (
                    f"{port} 的 TLS 被中间层掐，但**同一域名走 :{alt} 整链通**（hub 回了 HTTP）"
                    f" ⇒ 换端口就够：hub_url 写成 https://{host}:{alt} —— 本次已自动改，CFG 不用动，"
                    "也不用换域名/换机器/配代理"
                )
                line += f" ｜ 备用端口 :{alt} 通（{swept[alt]}）"
            else:
                line += " ｜ 备用端口全不通（" + ",".join(str(p) for p in ALT_HTTPS_PORTS) + "）"
        facts["hint"] = kind
        line += " ⇒ " + hint
        line += "；hub 未升级只会 404，不会超时"
    return bool(ok_route), line, facts


def _dns_summary(host: str, port: int, ips: list[str] | None = None) -> str:
    """DNS 读数的紧凑表示：`104.21.x.x(v4),2606:4700::1(v6)` / `fail`。

    地址族要显式标出来：云机上「解析出 IPv6 但没有可用 IPv6 路由」是最常见的假死，
    而这一点在 `URLError` 一句话里永远看不见。
    """
    ips = _resolve_ips(host, port) if ips is None else ips
    if not ips:
        return "fail"
    return ",".join(f"{ip}({'v6' if ':' in ip else 'v4'})" for ip in ips[:6])


def _resolve_ips(host: str, port: int) -> list[str]:
    """域名 → **去重后**的地址表（v4/v6 混排，保持解析顺序）。

    为什么单独一个函数：`_dns_summary` 要给人读的一行，逐 IP 的 TCP 探测要原始地址表，
    两者必须来自**同一次**解析 —— 各自 `getaddrinfo` 会在轮转 DNS 上给出不同答案，
    日志里就会自相矛盾（「列了 4 个地址」但「只探了 3 个」）。
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError:
        return []
    out: list[str] = []
    for _family, _t, _p, _c, sa in infos:
        ip = str(sa[0])
        if ip not in out:
            out.append(ip)
    return out


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
        # 同一进程里上一次 cell 留下的轮询线程先收掉（跨进程那半在 `common.node_report`）：
        # 两条链同时轮询同一个 node ⇒ hub 的任务随机分给两边、回执各走各的 rid，表面「成功」，
        # 实际一半结果来自旧代码 —— 这正是 2026-11-18 DSW「旧日志格式又出现了」的根源。
        stop_stale_links(self.log)
        for i in range(self._workers):
            t = threading.Thread(target=self._worker, name=f"relay-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        _LIVE_LINKS.append(self)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        if self in _LIVE_LINKS:
            _LIVE_LINKS.remove(self)

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


#: 本进程里**起过**的链接（`start()` 进、`stop()` 出）。cell 重跑时必须先清场：上一次若被
#: 第二个 Ctrl-C 打在 `finally` 上，`stop()` 就没跑完，轮询线程作为 daemon 继续替同一个 node
#: 干活 —— 两份日志混流、任务被旧代码吃掉一半。daemon 线程**只能在同进程内**收，够不着它
#: 就只能靠这张表；跨进程那半在 `common.node_report.reclaim_node_id`。
_LIVE_LINKS: list[RelayLink] = []


def stop_stale_links(log: Callable[[str], None], timeout: float = 1.0) -> int:
    """叫停本进程里**其它**还活着的 relay 链接，返回叫停的条数（幂等）。

    只设停止位、不硬等：`timeout` 是每条链接 join 的上限 —— 线程正卡在 20s 长轮询里时，
    它会等这一拍回来才退，硬等下去只会把新通道的启动一起拖住。
    """
    stale = [lk for lk in list(_LIVE_LINKS) if lk.alive()]
    for lk in stale:
        lk.stop(timeout=timeout)
    if stale:
        log(
            f"⚠ 上一次 cell 留下 {len(stale)} 条 relay 轮询还活着（同一 kernel 重跑）"
            " —— 已叫停，它们会在当前那一拍长轮询返回后退出"
        )
    return len(stale)


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
        alt = facts.get("alt_port")
        if alt:
            # 443 被中间层掐、备用端口整链通 ⇒ **当场换**，不让人回去改 CFG 再重跑
            # （一次 cell 重跑 ≈ 3 分钟；而这条路上 20s 就能判完）。换完的 URL 留在 `_args` 里，
            # 下一次 `open`（线程死亡重建）直接用新端口，不会再花一次预检的钱。
            self._args["hub_url"] = with_port(link.hub_url, int(alt))
            link = RelayLink(**self._args)
            facts["routes"] = {}  # 旧路由结论属于 443，换端口后作废（不能拿去 pin 路由）
            ok = True
            self.log(f"  → 本次改走 :{alt}（{link.hub_url}）")
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
    "ALT_HTTPS_PORTS",
    "BODY_MAX",
    "CTRL_HOST",
    "POLL_WAIT_SEC",
    "POLL_WORKERS",
    "PORT_WAIT_SEC",
    "PROBE_TIMEOUT_SEC",
    "TCP_PROBE_SEC",
    "TLS_PROBE_SEC",
    "RelayChannel",
    "RelayLink",
    "RelayUnreachable",
    "probe_hub",
    "stop_stale_links",
    "with_port",
]
