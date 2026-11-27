"""tests/remote/test_rollout_relay.py —— 节点侧会合通道（`remote/rollout_relay.py`）。

覆盖四件事（都是真机事故的对应面）：

* **全链路**：hub 长轮询 → 本机 agent → 回执回到 hub，方法/路径/鉴权头/请求体/状态逐字透传；
* `local_port` 与注册 URL：节点只知道 hub 的**公网**地址，注册要写**本机回环**端口；
* 本地 4xx 是**回执**（agent 的 `409 wver not cached` 必须原样回给 trainer），而「端口不通」
  才是 502 —— 把两者混成一个状态码会让 trainer 的自愈路径失效；
* **连不上 hub 时日志必须点名 host + 死因**（2026-11-18 DSW：6 个线程各卡 35s，日志只有
  `relay 轮询失败（URLError）`，`URLError` 一个词同时意味着 DNS 失败/被拒/TLS 失败/代理吊死/
  地址黑洞 ⇒ 排查全靠猜）。同一处还钉「代理吊死要能自动改直连」。
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
import urllib.error
from collections.abc import Iterator
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any

import pytest

from common import net_http
from remote import rollout_relay as rr
from remote.rollout_relay import RelayChannel, RelayLink, RelayUnreachable, _forward, probe_hub
from tests.helpers.http_server import serve_fast
from tests.subproc_util import DeadPort, dead_port, free_port


class _Quiet(BaseHTTPRequestHandler):
    #: 真 hub 是 HTTP/1.1（`hub/http_face.HubHandler`）——假 hub 不跟着升，复用用例就测不到东西。
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # 静默：测试输出只留断言
        return

    def _json(self, obj: dict, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # `serve_fast`：轮询周期钉到 10ms。缺省 0.5s 时每次 `shutdown()` 最多白等半个周期 ——
    # 本文件里起两个服务的用例实测 1.03s，几乎全是两次关服的等待（见 tests/helpers/http_server.py）。
    serve_fast(srv).start()
    return srv, int(srv.server_address[1])


def _fake_agent(log: dict) -> tuple[ThreadingHTTPServer, int]:
    """假 agent：记下最后一条请求；`/v1/cancel` 回 409（模拟真实自愈路径）。"""

    class Agent(_Quiet):
        def _handle(self) -> None:
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(n) if n else b""
            log["method"] = self.command
            log["path"] = self.path
            log["auth"] = self.headers.get("Authorization", "")
            log["body"] = body
            if self.path.startswith("/v1/cancel"):
                self._json({"error": "wver not cached here"}, 409)
            else:
                self._json({"ok": True, "path": self.path})

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

    return _serve(Agent)


def _fake_hub(pending: list[dict]) -> tuple[str, dict, ThreadingHTTPServer]:
    """假 hub：`_poll` 出一发待办，`_reply` 记回执并放行 `done`。"""
    state: dict = {
        "pending": list(pending),
        "replies": {},
        "port": 0,
        "ports": set(),
        "done": threading.Event(),
    }
    lock = threading.Lock()

    class Hub(_Quiet):
        def do_GET(self) -> None:
            state["ports"].add(self.client_address[1])  # 一条道复用一个 TCP ⇒ 只应出现一个端口
            if not self.path.startswith("/relay/n1/_poll"):
                self._json({"error": "not found"}, 404)
                return
            with lock:
                req = state["pending"].pop(0) if state["pending"] else None
            self._json({"req": req, "local_port": state["port"]})

        def do_POST(self) -> None:
            state["ports"].add(self.client_address[1])
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            rid = self.path.rsplit("/", 1)[-1]
            with lock:
                state["replies"][rid] = body
            state["done"].set()
            self._json({"ok": True})

    srv, port = _serve(Hub)
    state["port"] = port
    return f"http://127.0.0.1:{port}", state, srv


def test_link_serves_request_end_to_end_and_learns_local_port() -> None:
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    req = {
        "rid": "r1",
        "method": "POST",
        "path": "/v1/weights?kind=rollout",
        "headers": {"Authorization": "Bearer nodekey", "Content-Type": "application/x-gzip"},
        "body_b64": base64.b64encode(b"\x1f\x8bbody").decode("ascii"),
    }
    hub_url, state, hub_srv = _fake_hub([req])
    logs: list[str] = []
    link = RelayLink(
        hub_url=hub_url,
        token="hubtoken",
        node_id="n1",
        agent_port=agent_port,
        log=logs.append,
        workers=1,
    )
    link.start()
    try:
        assert state["done"].wait(8.0), f"没有收到回执（logs={logs}）"
        reply = state["replies"]["r1"]
        assert int(reply["status"]) == 200
        body = json.loads(base64.b64decode(reply["body_b64"]).decode("utf-8"))
        assert body["path"] == "/v1/weights?kind=rollout"
        # 请求侧逐字透传：方法 / 路径 / 鉴权头 / 请求体
        assert agent_log["method"] == "POST"
        assert agent_log["path"] == "/v1/weights?kind=rollout"
        assert agent_log["auth"] == "Bearer nodekey"
        assert agent_log["body"] == b"\x1f\x8bbody"
        # 端口由 hub 告知（节点不知道 hub 的本地端口），注册 URL 用本机回环
        assert link.local_port == int(state["port"])
        assert link.registration_url() == f"http://127.0.0.1:{state['port']}/relay/n1"
        assert link.served >= 1
    finally:
        link.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_lane_reuses_one_tcp_connection() -> None:
    """一条道 = 一条 TCP：poll/reply 走 `common.http_pool`，不再每拍重建。

    判据取**服务端看到的客户端端口**（一条复用连接只有一个），而不是池自己的计数 ——
    计数器写错时那种用例会自己骗自己。

    这里的假 hub 说 1.1；**真 hub 恒为 1.0**（origin 面不许复用，见
    `tests/hub/test_hub_origin_close.py` / §165）—— 生产里被复用的对端是**Cloudflare
    edge**，节点到 edge 那一段才是这笔死时间的出处，两者互不影响。
    """
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, state, hub_srv = _fake_hub([])
    logs: list[str] = []
    link = RelayLink(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=logs.append, workers=1
    )
    link.start()
    try:
        deadline = time.monotonic() + 5.0
        while link.polls < 4 and time.monotonic() < deadline:
            time.sleep(0.01)  # sleep-ok: 轮询步长（等的是谓词：已跑满 4 拍）
        assert link.polls >= 4, f"道没跑起来（logs={logs}）"
        assert len(state["ports"]) == 1, f"每拍新建了连接：{state['ports']}"
    finally:
        link.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_send_budget_is_one_timeout_across_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """两条路由都黑洞时，一拍只烧**一个** timeout（2026-11-19：每拍固定 70.4s = 35s × 2）。

    假时钟 + 假 `http_pool.request`：每次尝试把给它的那段预算“烧光”再抛 timeout，
    于是断言的是**真实语义**（总墙钟 ≈ 一个 budget），而不是计数器。
    """
    # 必须是**非** bypass 目标：回环目标 `_route_order()` 只给 `direct` 一条路（无兜底可测）。
    link = RelayLink(
        hub_url="https://hub.example.invalid:2083",
        token="t",
        node_id="n1",
        agent_port=1,
        log=lambda _m: None,
    )
    clock = {"t": 1000.0}
    seen: list[float] = []

    def fake_request(url: str, **kw: Any) -> tuple[int, bytes]:
        seen.append(float(kw["timeout"]))
        clock["t"] += float(kw["timeout"])
        raise urllib.error.URLError(TimeoutError("sim"))

    monkeypatch.setattr(rr.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rr.http_pool, "request", fake_request)
    t0 = clock["t"]
    with pytest.raises(RelayUnreachable):
        link._send("GET", "http://hub/relay/n1/_poll?wait=20", {}, None, 35.0)
    spent = clock["t"] - t0
    assert len(seen) == 2, f"两条路由都要试（seen={seen}）"
    assert spent <= 35.0 + 1e-6, f"跨路由总墙钟 {spent}s 超预算（每条都拿满 = 旧行为）"
    assert all(sec >= rr.ROUTE_MIN_SLICE_SEC for sec in seen), f"兜底被饿死：{seen}"
    # 没走到「预算已用尽」的跳过分支：兜底拿到了 12s，逐路由死因两条都写了。
    assert link.last_error.count("timeout") == 2, link.last_error


def test_forward_keeps_agent_4xx_as_reply() -> None:
    """agent 的 409 是**回执**（trainer 靠它触发重发权重），不能变成 502。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    try:
        resp = _forward(
            agent_port,
            {"method": "GET", "path": "/v1/cancel", "headers": {"Authorization": "Bearer k"}},
        )
        assert resp["status"] == 409
        assert json.loads(base64.b64decode(resp["body_b64"]))["error"].startswith("wver")
    finally:
        agent_srv.shutdown()


def test_forward_reports_unreachable_agent_as_502() -> None:
    """端口不通 = 转发失败（502），与上面那条的可重试语义分开。"""
    # 端口不通用 `dead_port()`（接受即 RST）：Windows 上连闭端口是 timeout 而非 refused，
    # 旧写法让本条白等 2s（见 `tests/subproc_util.py::DeadPort`）。
    with dead_port() as closed_port:
        resp = _forward(closed_port, {"method": "GET", "path": "/v1/ping", "headers": {}})
    assert resp["status"] == 502
    # 体里必须是一条可读的 failure 说明（结构判据，不抠字面）
    assert isinstance(json.loads(base64.b64decode(resp["body_b64"])).get("error"), str)


def test_channel_open_returns_registration_url() -> None:
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, state, hub_srv = _fake_hub([])
    logs: list[str] = []
    ch = RelayChannel(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=logs.append
    )
    try:
        url = ch.open("测试")
        assert url == f"http://127.0.0.1:{state['port']}/relay/n1"
        assert ch.alive() and ch.url == url
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()
    assert not ch.alive()


def test_channel_open_gives_up_when_hub_never_reports_port() -> None:
    """老 hub（没有 `/relay/` 或忘了回 `local_port`）⇒ 放弃并清空 link，不拿错端口硬填。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, state, hub_srv = _fake_hub([])
    state["port"] = 0
    logs: list[str] = []
    ch = RelayChannel(
        hub_url=hub_url,
        token="t",
        node_id="n1",
        agent_port=agent_port,
        log=logs.append,
        wait_sec=0.6,
    )
    try:
        assert ch.open("测试") == ""
        assert ch.link is None and not ch.alive() and ch.url == ""
        assert ch.stats_line() == "relay 未启动"
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_channel_ensure_rebuilds_after_workers_die() -> None:
    """监护判据：轮询线程全停 = 通道死；`supervise` 重建并回新 URL（端口可能变）。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, state, hub_srv = _fake_hub([])
    logs: list[str] = []
    ch = RelayChannel(
        hub_url=hub_url,
        token="t",
        node_id="n1",
        agent_port=agent_port,
        log=logs.append,
        wait_sec=5.0,
    )
    try:
        first = ch.open("测试")
        assert first and ch.alive()
        assert ch.supervise() == first  # 活着 ⇒ 不重建
        old = ch.link
        assert old is not None
        old._stop.set()  # 模拟线程全退（hub 被墙 / 重组）
        assert not ch.alive()
        again = ch.supervise("relay 线程死亡")
        assert again and ch.link is not None and ch.link is not old
        assert old.thread_count() == 0
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


# ---------------------------------------------------------------- 连不上时说得清（2026-11-18 DSW）


@pytest.fixture
def dead_link() -> Iterator[tuple[RelayLink, list[str]]]:
    """指向「这个端口没有服务」的通道 + 它自己的日志表。

    死端口是 `DeadPort`（接受即 RST），不是「bind 后 close」：Windows 上连**没人监听**的回环口
    是 2s timeout 而不是 refused（见 `tests/subproc_util.py::DeadPort`），旧写法让每条用到它的
    用例都按 timeout 计费。
    """
    logs: list[str] = []
    dead = DeadPort()
    link = RelayLink(
        hub_url=f"http://127.0.0.1:{dead.port}",
        token="t",
        node_id="n1",
        agent_port=1,
        log=logs.append,
        workers=1,
    )
    try:
        yield link, logs
    finally:
        link.stop()
        dead.close()


def test_unreachable_hub_raises_with_reason_and_host(
    dead_link: tuple[RelayLink, list[str]],
) -> None:
    """`_poll` 全崩时抛的是**带 reason 的** `RelayUnreachable`，不再是裸 `URLError`。"""
    link, _logs = dead_link
    with pytest.raises(RelayUnreachable) as ei:
        link._poll()
    text = link.last_error
    assert text.startswith("direct="), f"last_error 必须是逐路由结论：{text!r}"
    # `reset` = `DeadPort` 那种「接受即 RST」的死因（原文 `ConnectionResetError`，故比小写）。
    assert any(k in text.lower() for k in ("refused", "timeout", "reset"))
    assert isinstance(ei.value, urllib.error.URLError)  # 上层 except URLError 语义不变


def test_failure_log_names_host_and_reason(dead_link: tuple[RelayLink, list[str]]) -> None:
    """首条失败日志 = `hub:port` + 死因 + 耗时；后续连续失败**不许**刷屏。"""
    link, logs = dead_link
    with pytest.raises(RelayUnreachable) as ei:
        link._poll()
    link._log_failure(ei.value, 35.0)
    assert len(logs) == 1
    assert logs[0].startswith(f"relay 轮询失败（hub={link.hub_host}，35.0s）"), logs[0]
    assert any(k in logs[0].lower() for k in ("refused", "timeout", "reset"))
    for _ in range(ERR_SILENT_BURST):
        link._log_failure(ei.value, 35.0)
    assert len(logs) == 1, "同一条失败连续刷屏（6 线程 × 每轮 1 行）必须被压掉"


#: 少于这个条数都不该再出日志（`ERR_LOG_EVERY` 的语义：每 N 条汇总一次）。
ERR_SILENT_BURST = 8


def test_err_text_distinguishes_timeout_from_dns_and_refused() -> None:
    """一句话死因要能分辨 timeout / DNS / refused —— 这正是 `URLError` 一个词缺的信息。"""
    assert rr._err_text(urllib.error.URLError(TimeoutError("timed out"))).startswith("timeout")
    assert rr._err_text(
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    ).startswith("DNS")
    assert rr._err_text(urllib.error.URLError(ConnectionRefusedError(61, "refused"))).startswith(
        "refused"
    )
    assert rr._err_text(urllib.error.HTTPError("u", 404, "nf", {}, None)) == "HTTP 404"  # type: ignore[arg-type]


def test_probe_hub_reports_routes_against_live_hub() -> None:
    """预检给的是**结构化**读数（DNS + 逐路由结论），人读那行由它拼出来。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, state, hub_srv = _fake_hub([])
    try:
        ok, line, facts = probe_hub(hub_url, "t", "n1", timeout=3.0)
        assert ok is True
        assert (facts["host"], facts["port"]) == ("127.0.0.1", int(state["port"]))
        assert set(facts["routes"].values()) <= {"ok", "http-401", "http-404"}
        assert facts["dns"] and line.startswith(f"relay 预检 127.0.0.1:{state['port']}")
    finally:
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_probe_hub_flags_unreachable_with_actionable_hint() -> None:
    with dead_port() as port:
        ok, line, facts = probe_hub(f"http://127.0.0.1:{port}", "t", "n1", timeout=2.0)
    assert ok is False
    assert set(facts["routes"].values()) == {"fail"}
    assert "hub_url" in line  # 判词要直接指向可改的那个开关


def test_probe_hub_sweeps_every_resolved_ip_when_https_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS 全不通时必须补逐 IP 的裸 TCP —— 这是「黑洞 / TLS 被掐 / 没有 v6 路由」的分叉。

    2026-11-18 DSW 现场只有一句 `inherit=timeout ｜ direct=timeout`，看不出是整组 anycast IP
    被挡还是 TCP 通、掐在 SNI 上；两者的修法完全不同（换承载 vs 换域名/走代理）。
    """
    # 这条要的是**TCP 层**连不上（下面钉的是 `_tcp_verdict` 的结论）⇒ 不能用 `dead_port()`：
    # 它接受连接，TCP 是「通」的。闭端口在 Windows 上是 timeout 而非 refused，所以把探测预算
    # 压到 0.3s —— 判词只看「连不上」，与预算大小无关，用例不该为此付 2s 墙钟。
    monkeypatch.setattr(rr, "TCP_PROBE_SEC", 0.3)
    port = free_port()
    monkeypatch.setattr(net_http, "bypass_proxy", lambda _u: False)
    monkeypatch.setattr(
        rr, "_probe_one_route", lambda route, url, token, timeout: ("fail", f"{route}=stub")
    )
    monkeypatch.setattr(rr, "_resolve_ips", lambda _h, _p: ["127.0.0.1", "::1"])
    ok, _line, facts = probe_hub(f"http://127.0.0.1:{port}", "t", "n1", timeout=1.0)
    assert ok is False
    # 两个地址各探一次、各给一个**具体**结论（Windows 沙箱里连闭合的回环口是 timeout 而不是
    # refused，所以这里只钉「有结论」，不钉具体措辞 —— 措辞分叉由下一条用例结构化钉住）
    assert set(facts["tcp"]) == {"127.0.0.1", "::1"}, facts["tcp"]
    assert facts["tcp"]["127.0.0.1"] in ("refused", "timeout"), facts["tcp"]
    assert facts["hint"] in ("blackhole", "unroutable"), facts


def test_fail_hint_separates_blackhole_from_tls_block() -> None:
    """判词是给人看的分叉：全 timeout = IP 黑洞（重试无用）；有 ok = 掐在 TLS/SNI。

    钉的是 `kind`（结构化那一半），不是措辞 —— 改文案不该把用例弄红。
    """
    assert rr._fail_hint({"1.1.1.1": "timeout", "2606:4700::1": "timeout"})[0] == "blackhole"
    assert rr._fail_hint({"1.1.1.1": "ok", "2606:4700::1": "timeout"})[0] == "tls"
    assert rr._fail_hint({"1.1.1.1": "unreachable"})[0] == "unroutable"
    assert rr._fail_hint({})[0] == "dns"


def test_gateway_codes_are_not_treated_as_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """代理 / CF 边缘的 502、530 ≠ 路由通：它回话很快，但上游根本没到。

    2026-11-18 实测：本机 LAN 代理对黑洞地址回 **502 / 5047ms** —— 旧实现「HTTPError 一律算
    应答」会据此判 `ok=True` 并把那条腿钉住，接着每一拍长轮询都拿 502。
    """
    code = {"v": 502}

    class _Gateway:
        def __enter__(self) -> object:
            raise urllib.error.HTTPError("u", code["v"], "gw", {}, None)  # type: ignore[arg-type]

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(rr, "_route_open", lambda route, req, timeout: _Gateway())
    verdict, _human = rr._probe_one_route("inherit", "http://x/", "t", 1.0)
    assert verdict == "gateway-502", verdict
    assert rr._pick_route({"routes": {"inherit": "gateway-502"}}) == "", "网关腿不能被钉住"
    # 401（真对端应用回了话）仍然是**通**：这条不能一起改坏
    code["v"] = 401
    verdict, _human = rr._probe_one_route("inherit", "http://x/", "t", 1.0)
    assert verdict == "http-401"
    assert rr._pick_route({"routes": {"inherit": "http-401"}}) == "inherit"


def test_poll_falls_back_to_direct_when_proxy_route_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台预置的 `HTTPS_PROXY` 吊死时，通道自动改直连（DSW 头号嫌疑场景）。

    真机组合：cloudflared 是 Go 二进制**不读**代理 ⇒ 隧道建得出；而 python 的 urllib
    读代理 ⇒ 每条长轮询卡满 35s。这条用例把「只看代理路径」的实现钉死。
    """
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, _state, hub_srv = _fake_hub([])
    real_send = rr.http_pool.request

    def _hang(url: str, **kw: Any) -> tuple[int, bytes]:
        if kw.get("no_proxy"):  # 「直连」那条腿必须还是真的（否则这条用例只是在测桩）
            return real_send(url, **kw)
        raise urllib.error.URLError(TimeoutError("timed out"))

    logs: list[str] = []
    link = RelayLink(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=logs.append, workers=1
    )
    try:
        # 回环默认 `bypass_proxy` ⇒ 只有一条路由；这里人为放开，才验得到「代理吊死 → 直连」
        monkeypatch.setattr(net_http, "bypass_proxy", lambda _u: False)
        monkeypatch.setattr(rr.http_pool, "request", _hang)
        assert link.route_order() == ["inherit", "direct"]
        answer = link._poll()
        assert answer is not None and answer.get("local_port")
        assert link.route == "direct", "成功的路由要记住，下一拍别再去撞代理"
        assert link.route_order() == ["direct", "inherit"]
    finally:
        link.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_open_pins_route_from_preflight() -> None:
    """预检通了就直接钉住那条路 —— 否则第一拍先撞错的腿，35s 就超了 `PORT_WAIT_SEC`。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, _state, hub_srv = _fake_hub([])
    ch = RelayChannel(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=lambda _m: None
    )
    try:
        assert ch.open("测试")
        assert ch.link is not None
        assert ch.link.route == "direct", "回环只有直连一条腿，预检就该把结论传下去"
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_channel_open_failure_names_preflight_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """放弃时人读面必须可用：预检行 + 未就绪行（带 host）、并且不再误导去看 cloudflared。"""
    # 失败路径现在要多花一次逐 IP 的 TCP 扫描（`TCP_PROBE_SEC`）——用例里把两段预算都压小，
    # 免得这条「只是要看日志措辞」的用例去吃掉 5s 的耗时阈值。
    monkeypatch.setattr(rr, "PROBE_TIMEOUT_SEC", 0.6)
    monkeypatch.setattr(rr, "TCP_PROBE_SEC", 0.6)
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    dead = DeadPort()
    port = dead.port
    logs: list[str] = []
    ch = RelayChannel(
        hub_url=f"http://127.0.0.1:{port}",
        token="t",
        node_id="n1",
        agent_port=agent_port,
        log=logs.append,
        wait_sec=0.8,
    )
    try:
        assert ch.open("测试") == ""
        assert ch.link is None
    finally:
        ch.stop()
        agent_srv.shutdown()
        dead.close()
    assert any(ln.startswith(f"relay 预检 127.0.0.1:{port} ") for ln in logs), logs
    joined = "\n".join(logs)
    assert f"hub=127.0.0.1:{port}" in joined and "cloudflared" in joined


# ------------------------------------------------- cell 重跑：同进程遗留轮询线程（2026-11-18 DSW）


def test_start_stops_leftover_links_from_previous_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 kernel 里重跑 cell：上一次的轮询线程（daemon）可能没跟着 `finally` 退出。

    两条链同时轮询同一个 node ⇒ hub 的任务随机分给两边、回执各走各自的 rid，表面「成功」，
    一半结果其实来自旧代码 —— 这正是 DSW 现场「旧日志格式又出现了」的根源。daemon 线程
    只能在同进程内收，够不着它就只能靠 `LiveLinks` 这张登记表（P0-5，2026-11-21 对象化）。
    """
    stopped: list[float] = []

    class _Stale:
        alive_now = True

        def alive(self) -> bool:
            return self.alive_now

        def stop(self, timeout: float = 5.0) -> None:
            stopped.append(timeout)
            self.alive_now = False

    stale, fresh = _Stale(), _Stale()
    fresh.alive_now = False
    # 替换的是**公开读面** `live_links()`（不是模块私有变量）：登记表本身就是可注入的对象。
    monkeypatch.setattr(rr, "live_links", lambda: rr.LiveLinks([stale, fresh]))
    logs: list[str] = []
    assert rr.stop_stale_links(logs.append) == 1, "只收还活着的那条"
    assert stopped and stale.alive_now is False
    assert len(logs) == 1, logs


def test_new_link_sweeps_before_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """接线：`RelayLink.start()` 必须先清场再起线程（不然这张表永远只是个装饰）。"""
    swept: list[int] = []

    def _count_sweep(_log: object, timeout: float = 1.0) -> int:
        swept.append(1)
        return 0

    monkeypatch.setattr(rr, "stop_stale_links", _count_sweep)
    monkeypatch.setattr(rr.RelayLink, "_worker", lambda self: None)  # 线程空转即退，不碰网络
    link = RelayLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9, log=lambda _m: None
    )
    try:
        link.start()
        assert swept == [1]
    finally:
        link.stop()


# ------------------------------------------------- 预检第三层：TCP 通了再切 TLS（2026-11-18 DSW 第二次）


def _stub_layers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tls: tuple[str, str],
    ctrl: tuple[str, str] | None = None,
    net: tuple[str, str] | None = None,
) -> None:
    """把预检的三层全部打桩：只验判词分叉，不碰网络（真机行为由 DSW 现场日志背书）。

    只收 `(verdict, 人读)` 双元组（同一条结论对每个地址成立）—— 这一层要判的是
    「哪一类失败」，不是「哪个地址特殊」，多地址差异由真机日志背书。

    `None` = **该主机解析不出地址**（连问都问不到）—— 这时不该出现对应的 `tls_ctrl` /
    `tls_net` 键，判词也不许借它分叉；用它同时钉住「对照缺失时退回保守判词」这条边界。
    两条对照都**必须显式给**（没有默认值）：它们的结论正是判词要分开的东西，
    默认成「通」会让用例在假设里偷偷少探一家。
    """
    monkeypatch.setattr(net_http, "bypass_proxy", lambda _u: False)
    monkeypatch.setattr(
        rr, "_probe_one_route", lambda route, url, token, timeout: ("fail", f"{route}=stub")
    )
    ips = {
        rr.CTRL_HOST: ["104.16.230.132"],
        rr.CTRL_HOST_NET: ["203.119.0.1"],
    }

    def _ips(host: str, _port: int) -> list[str]:
        if host in ips:
            return ips[host] if (ctrl if host == rr.CTRL_HOST else net) is not None else []
        return ["104.16.231.132"]

    def _sweep(ips_: list[str], _port: int, sni: str, _t: float) -> dict[str, tuple[str, str]]:
        # 按 SNI 分叉：两条对照与 hub 拿到的结论必须能各不相同，否则分不出是哪一层被掐。
        if sni == rr.CTRL_HOST and ctrl is not None:
            spec = ctrl
        elif sni == rr.CTRL_HOST_NET and net is not None:
            spec = net
        else:
            spec = tls
        return {ip: spec for ip in ips_}

    monkeypatch.setattr(rr, "_resolve_ips", _ips)
    monkeypatch.setattr(rr, "_tcp_sweep", lambda ips_, port, t: {ip: "ok" for ip in ips_})
    monkeypatch.setattr(rr, "_tls_sweep", _sweep)


def test_hint_names_cf_block_when_cf_stalls_but_non_cf_handshakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两条对照分工：CF 另一个域名也卡、非 CF 站点握手成功 ⇒ **只有 CF 这一家被掐**。

    判据是 DSW 2026-11-18 第三次现场：旧的单对照就是 `api.trycloudflare.com`，与 hub 同属一个
    后缀 ⇒ 按 `trycloudflare.com` 这一段掐时对照必然一起卡，判词落到 `tls-blocked`
    （「走代理/换机器」），把最便宜的「换个非 CF 入口」抹掉。`cf-blocked` 把这两档分开。
    """
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=("timeout", "握手无应答"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert ok is False
    assert facts["hint"] == "cf-blocked", facts
    # 三层读数都要进 `facts`：人读行只给现场看，机器判据不能靠子串。
    assert facts["tls"] == {"104.16.231.132": "timeout"}, facts
    assert facts["tls_ctrl"] == {"104.16.230.132": "timeout"}, facts
    assert facts["tls_net"] == {"203.119.0.1": "ok:TLSv1.3"}, facts


def test_hint_names_tls_block_when_nothing_handshakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连不在 CF 上的对照主机也握不上手 ⇒ 本机 TLS 出网整体被中间层掐（代理 / 换机器）。"""
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=("timeout", "握手无应答"),
        net=("timeout", "握手无应答"),
    )
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "tls-blocked", facts
    assert facts["tls_net"] == {"203.119.0.1": "timeout"}, facts


def test_hint_names_sni_block_when_control_host_handshakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CF 边缘的另一个域名握手成功 ⇒ 按域名/SNI 掐：最便宜的一档是换个域名（同一边缘）。"""
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=("ok:TLSv1.3", "TLSv1.3（120ms）"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "sni-blocked", facts


def test_hint_keeps_sni_block_when_cf_control_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CF 对照解析不出地址、而非 CF 对照通 ⇒ 只说得了「这个域被掐」（不许升格成「整家 CF」）。"""
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=None,
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "sni-blocked", facts
    assert {"tls_ctrl"}.isdisjoint(facts), "问不到就别编读数"
    assert facts["tls_net"] == {"203.119.0.1": "ok:TLSv1.3"}, facts


def test_hint_points_at_hub_when_tls_handshake_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """握手成功、只有 HTTPS 无应答 ⇒ 别再查节点出网，去 hub 那侧看进程与隧道。"""
    _stub_layers(
        monkeypatch,
        tls=("ok:TLSv1.3", "TLSv1.3（150ms）"),
        ctrl=("ok:TLSv1.3", "TLSv1.3（120ms）"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "app-stall", facts
    assert facts["tls"]["104.16.231.132"] == "ok:TLSv1.3"


def test_probe_falls_back_to_a_working_alternate_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """443 的握手被掐、备用端口整链通 ⇒ 判词直接给「换端口」，并回 `alt_port` 供通道自救。

    读数来自 2026-11-18 DSW 实机（用户在该机直接 curl）：`443=000` 而
    `2053/2083/2087/2096=401`（**hub 自己的鉴权在回话**，说明 TLS+HTTP 整条链都通）、
    `8443=000`。⇒ 中间层只盯标准 HTTPS 端口，换端口是同机零成本的修法。
    """
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=("ok:TLSv1.3", "TLSv1.3（120ms）"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    swept = {2083: "http-401", 2096: "fail", 2053: "fail", 2087: "fail", 8443: "fail"}
    monkeypatch.setattr(rr, "_port_sweep", lambda host, ports, token, node_id, t: dict(swept))
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "port-blocked", facts
    assert facts["alt_port"] == 2083, facts
    assert facts["alt_ports"]["2083"] == "http-401", facts


def test_probe_re_sweeps_when_the_configured_port_is_already_an_alt_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置就在备用端口上（DSW 现场的 `:2083`）时也必须再扫——旧写法整段跳过。

    2026-11-19 现场：节点 `hub_url=…:2083` 的 TLS 全无应答，而预检的守卫是
    `port not in ALT_HTTPS_PORTS` ⇒ 一个候选都没试，直接判成「按域名/SNI 掐」、建议换域名。
    真因可能只是这个端口当天被掐（前一天 443 被掐、2083 是通的）。
    """
    _stub_layers(
        monkeypatch,
        tls=("timeout", "握手无应答"),
        ctrl=("ok:TLSv1.3", "TLSv1.3（120ms）"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )
    swept = {443: "fail", 2096: "fail", 2053: "http-401", 2087: "fail", 8443: "fail"}
    seen: dict[str, tuple[int, ...]] = {}

    def fake_sweep(host: str, ports: object, token: str, node_id: str, t: float) -> dict[int, str]:
        seen["ports"] = tuple(ports)  # type: ignore[arg-type]
        return dict(swept)

    monkeypatch.setattr(rr, "_port_sweep", fake_sweep)
    _ok, _line, facts = probe_hub("https://hub.example.com:2083", "t", "n1", timeout=0.2)
    assert facts["hint"] == "port-blocked", facts
    assert facts["alt_port"] == 2053, facts
    assert 2083 not in seen["ports"], "当前端口不必再扫"
    assert 443 in seen["ports"], "从备用端口回退时，443 也是候选（可能只是当天被掐）"


def test_probe_skips_the_port_sweep_when_the_hub_side_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """握手成功、只有 HTTP 不回（隧道死了 / hub 进程没跑）⇒ **别**去换端口。

    这条是上一条的反证：`app-stall` 时换端口只会把「超时」变成「530 / 一样不回」，
    白花 5 条请求的钱，还会把人的注意力引到错误的开关上。
    """
    _stub_layers(
        monkeypatch,
        tls=("ok:TLSv1.3", "TLSv1.3（150ms）"),
        ctrl=("ok:TLSv1.3", "TLSv1.3（120ms）"),
        net=("ok:TLSv1.3", "TLSv1.3（60ms）"),
    )

    def _boom(*_args: object, **_kwargs: object) -> dict[int, str]:
        raise AssertionError("app-stall 不该触发端口扫描")

    monkeypatch.setattr(rr, "_port_sweep", _boom)
    _ok, _line, facts = probe_hub("https://hub.example.com", "t", "n1", timeout=0.2)
    assert facts["hint"] == "app-stall", facts
    assert {"alt_port", "alt_ports"}.isdisjoint(facts), facts


def test_with_port_keeps_scheme_path_and_ipv6_brackets() -> None:
    """换端口只动端口：scheme / path 原样，裸 IPv6 必须补方括号（`::1:2083` 不是地址）。"""
    assert rr.with_port("https://hub.example.com/x/", 2083) == "https://hub.example.com:2083/x/"
    assert rr.with_port("http://[::1]:8787", 2083) == "http://[::1]:2083"


def test_channel_switches_hub_url_to_the_alternate_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """预检说「只有 :2083 通」时通道**自己改地址** —— 不让人回去改 CFG 再重跑 cell（≈3 分钟）。

    对端用**拒绝替身**而不是死端口（AGENTS：`tests/conftest.py` 的 `BCITY_CONSOLE_URL`
    注释、docs/nn/engineering.md §81）：没人听的端口在防火墙/沙箱里会把 SYN 黑洞化，
    `connect` 要挂到长轮询预算，`stop()` 再等满 5s join（本机实测 5.4s/次）——
    与环境相关且不测任何判据。抛 `ConnectionRefusedError` 与「本机没有监听」在 `_send` 里
    走的是同一条 `except URLError` 分支，但墙钟是 µs 级。断言只多一条「桩真的被调到」：
    将来通道不再发轮询时，这条用例不会靠「什么也没发生」而假绿。
    """
    monkeypatch.setattr(rr, "probe_hub", lambda *_a, **_k: (False, "预检行", {"alt_port": 2083}))
    calls = {"n": 0}

    def _refuse(*_a, **_k):
        calls["n"] += 1
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(rr.http_pool, "request", _refuse)
    logs: list[str] = []
    ch = RelayChannel(
        hub_url="https://127.0.0.1",
        token="t",
        node_id="n1",
        agent_port=18999,
        log=logs.append,
        wait_sec=0.3,
    )
    try:
        assert ch.open("测试") == ""  # 换过去也连不上（本机没有监听），但地址必须已经改了
        assert ch.hub_url == "https://127.0.0.1:2083"
        assert calls["n"] >= 1, f"通道根本没去 :2083 轮询（只改了字符串？）logs={logs}"
    finally:
        ch.stop()


def test_tls_verdict_reports_timeout_and_handshake_error() -> None:
    """`_tls_verdict` 的两条真路径：对端**不说话** = timeout；对端**接了又关** = TLS 错误。"""
    silent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    port = int(silent.getsockname()[1])
    accepted: list[socket.socket] = []

    def _accept_once() -> None:
        conn, _addr = silent.accept()
        accepted.append(conn)  # 收下但不回任何字节：TLS 侧只能等 → timeout

    threading.Thread(target=_accept_once, daemon=True).start()
    try:
        verdict, _human = rr._tls_verdict("127.0.0.1", port, "hub.example.com", 0.4)
        assert verdict == "timeout", verdict
    finally:
        for c in accepted:
            c.close()
        silent.close()

    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    closed.listen(1)
    port2 = int(closed.getsockname()[1])

    def _accept_and_close() -> None:
        conn, _addr = closed.accept()
        conn.close()  # 立刻关：握手拿不到 ServerHello，直接 EOF

    threading.Thread(target=_accept_and_close, daemon=True).start()
    try:
        verdict2, _h2 = rr._tls_verdict("127.0.0.1", port2, "hub.example.com", 2.0)
        assert verdict2.startswith("err:tls") or verdict2.startswith("err:conn"), verdict2
    finally:
        closed.close()


# ------------------------------------------------- 传输层不许比算力层更窄（2026-11-19 DSW）

def test_relay_lanes_follow_agent_workers() -> None:
    """通道的道数 = agent 的 worker 数（下限 `POLL_WORKERS`）。

    现场（hub 日志 + `[dist] node dsw`）：agent workers=16、trainer concurrency=12，而道数
    固定 6 ⇒ 单局 3.7s 的节点被卡在 97 局/分，trainer 侧 wall 翻倍到 7.9s/局。道数少于
    worker 数时，传输层就是算力天花板。
    """
    logs: list[str] = []

    def chan(workers: int) -> RelayChannel:
        return RelayChannel(
            hub_url="http://127.0.0.1:9",
            token="t",
            node_id="n1",
            agent_port=9,
            log=logs.append,
            workers=workers,
        )

    assert chan(16).workers == 16
    # 小机器不缩水到 6 以下：控制面（ping/weights）也要有道走
    assert chan(2).workers == rr.POLL_WORKERS


def test_reply_delivery_retries_gateway_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF 偶发 502/530 时回执要重发：丢一条 = 一整局白跑（hub 侧等到超时再回队重算）。"""
    logs: list[str] = []
    link = RelayLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9,
        log=logs.append, workers=1,
    )
    calls: list[str] = []

    def _flaky(rid: str, resp: dict) -> None:
        calls.append(rid)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 502, "bad gateway", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(rr, "REPLY_RETRY_SEC", 0.0)
    monkeypatch.setattr(link, "_post_reply", _flaky)
    link._deliver("r1", {"status": 200, "headers": {}, "body_b64": ""})
    assert len(calls) == 3, calls
    assert link.errors == 0
    assert not logs, logs


def test_reply_delivery_does_not_retry_permanent_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """401/404 是确定性失败（令牌不对 / hub 没升级）：重发只会把同一条错误刷三遍。"""
    logs: list[str] = []
    link = RelayLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9,
        log=logs.append, workers=1,
    )
    calls: list[str] = []

    def _denied(rid: str, resp: dict) -> None:
        calls.append(rid)
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(rr, "REPLY_RETRY_SEC", 0.0)
    monkeypatch.setattr(link, "_post_reply", _denied)
    link._deliver("r1", {"status": 200, "headers": {}, "body_b64": ""})
    assert len(calls) == 1, calls
    assert link.errors == 1
    assert len(logs) == 1, logs  # 只响一次：不把同一条确定性失败刷三遍


def test_reply_delivery_gives_up_after_budget_and_logs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真断了（三次都失败）⇒ 一次错误 + 一行日志，不刷屏；调用方那一侧的超时语义不变。"""
    logs: list[str] = []
    link = RelayLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9,
        log=logs.append, workers=1,
    )
    calls: list[str] = []

    def _down(rid: str, resp: dict) -> None:
        calls.append(rid)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(rr, "REPLY_RETRY_SEC", 0.0)
    monkeypatch.setattr(link, "_post_reply", _down)
    link._deliver("r1", {"status": 200, "headers": {}, "body_b64": ""})
    assert len(calls) == rr.REPLY_TRIES, calls
    assert link.errors == 1
    assert len(logs) == 1, logs


#: 一份 CF 边缘 5xx 的特征头（`server` + `cf-ray`）：证明死因行**没有**把「谁回的」丢掉。
def _cf_headers() -> Message:
    m = Message()
    m["server"] = "cloudflare"
    m["cf-ray"] = "a5cf302d0babae00-SJC"
    return m


def test_gateway_route_does_not_stop_the_other_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """502/530 是**传输失败**，必须换下一条路由，且不能把这条坏路由记成「上次成功」。

    2026-11-19 DSW 现场（`relay 轮询失败 … HTTP 502` 连刷 170 次）：旧实现把 HTTPError 一律当
    「路由通了」⇒ 第一拍拿到 CF 的 502 就整条道判死、`_note_route` 还把坏腿钉成首选，
    下一条腿（明明是好的）永远轮不到。
    """
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    hub_url, _state, hub_srv = _fake_hub([])
    real_send = rr.http_pool.request
    seen: list[str] = []

    def _gateway_on_proxy(url: str, **kw: Any) -> tuple[int, bytes]:
        if kw.get("no_proxy"):
            seen.append("direct")
            return real_send(url, **kw)
        seen.append("inherit")
        raise urllib.error.HTTPError(
            url, 502, "Bad Gateway", _cf_headers(), BytesIO(b'{"error":1033}')
        )

    logs: list[str] = []
    link = RelayLink(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=logs.append, workers=1
    )
    try:
        monkeypatch.setattr(net_http, "bypass_proxy", lambda _u: False)
        monkeypatch.setattr(rr.http_pool, "request", _gateway_on_proxy)
        assert link.route_order() == ["inherit", "direct"]
        answer = link._poll()
        assert answer is not None and answer.get("local_port")
        assert seen == ["inherit", "direct"], seen
        assert link.route == "direct", "拿到网关码的那条腿不能当成「上次成功」"
    finally:
        link.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_gateway_failure_names_its_author(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一句 `HTTP 502` 有三个作者（代理 / CF 边缘 / hub 反代）——死因行必须带证据。

    CF 的特征是 `server: cloudflare` + `cf-ray`，体里还有机读码（`error code: 1033`）。
    把这些丢掉（旧实现读了 4KB 就扔），人只能在    「换域名 / 换机器 / 查 hub」之间瞎猜。
    """

    def _always_gateway(url: str, **kw: Any) -> tuple[int, bytes]:
        raise urllib.error.HTTPError(
            url, 530, "Origin DNS error", _cf_headers(), BytesIO(b'{"error":"1033"}')
        )

    link = RelayLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9,
        log=lambda _m: None, workers=1,
    )
    monkeypatch.setattr(rr.http_pool, "request", _always_gateway)
    with pytest.raises(rr.RelayUnreachable) as exc:
        link._poll()
    text = str(exc.value)
    # 按**证据字段**切（`；` / `（）` 是分隔符），不靠子串碰运气：
    #   · 状态码与 CF 特征必须在场 —— 死因行不点名作者，人就只能在「代理/CF/hub」间瞎猜；
    #   · 体前 120 字原样带上（`1033` 这个机读码在体里，别再让人去问 CF 要）。
    fields = [f for f in text.replace("；", " ").replace("（", " ").replace("）", " ").split()]
    assert {"530", "server=cloudflare"} <= set(fields), text
    assert [f for f in fields if f.startswith("body=")] == ['body={"error":"1033"}'], text


def test_channel_hub_url_follows_the_port_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """备用端口改写必须**交得出去**：注册打的就是这个地址。

    2026-11-19 DSW：443 的 TLS 被中间层掐，通道自己改走 `:2096` 后长轮询在跑，而注册仍打
    `CFG.hub_url` 的 `:443` ⇒ 恒 `handshake operation timed out`，连续多轮「★ 未注册」。
    """
    monkeypatch.setattr(
        rr, "probe_hub", lambda *_a, **_k: (True, "预检行", {"alt_port": 2096, "routes": {}})
    )
    monkeypatch.setattr(rr.RelayLink, "start", lambda self: None)
    monkeypatch.setattr(rr.RelayLink, "wait_local_port", lambda self, timeout=40.0: 8787)
    ch = RelayChannel(
        hub_url="https://hub.example.com", token="t", node_id="n1", agent_port=1,
        log=lambda _m: None,
    )
    try:
        assert ch.open("测试") == "http://127.0.0.1:8787/relay/n1"
        assert ch.hub_url == "https://hub.example.com:2096", ch.hub_url
        assert ch.link is not None and ch.link.route == "direct", "扫描只走过直连，就该钉直连"
    finally:
        ch.stop()


def test_live_links_registry_is_idempotent_and_isolated() -> None:
    """登记表是一个对象：重复登记 / 重复注销都幂等，两份登记互不相见。

    旧写法是模块级 `list`：`if self in _LIVE_LINKS: _LIVE_LINKS.remove(self)` 在两条线程同时
    收同一链接时会在检查与删除之间被抢先 ⇒ `ValueError`；用例也只能 `monkeypatch.setattr`
    换掉一个模块变量（类型都对不上）。
    """

    class _Fake:
        def __init__(self) -> None:
            self.stops: list[float] = []

        def alive(self) -> bool:
            return True

        def stop(self, timeout: float = 5.0) -> None:
            self.stops.append(timeout)

    a, b = _Fake(), _Fake()
    reg, other = rr.LiveLinks(), rr.LiveLinks()
    reg.add(a)
    reg.add(a)
    assert reg.count() == 1, "重复登记必须幂等"
    assert other.count() == 0, "两份登记互不相见"
    reg.discard(b)  # 没登记过：不是错误
    reg.discard(a)
    reg.discard(a)  # 双重注销：幂等（旧写法在这里 ValueError）
    assert reg.count() == 0
    reg.add(a)
    assert reg.snapshot() == [a], "snapshot 返回按登记顺序的副本"


def test_start_registers_and_stop_discards(monkeypatch: pytest.MonkeyPatch) -> None:
    """接线：`start()` 把自己登进表、`stop()` 自己注销（走公开读面 `live_links()`）。"""

    class _QuietLink(rr.RelayLink):
        """跑一轮就退的链接（不碰网络，也不必打桩私有 `_worker`）。"""

        def _worker(self) -> None:
            return None

    monkeypatch.setattr(rr, "stop_stale_links", lambda *_a, **_k: 0)
    reg = rr.LiveLinks()
    monkeypatch.setattr(rr, "live_links", lambda: reg)
    link = _QuietLink(
        hub_url="http://127.0.0.1:9", token="t", node_id="n1", agent_port=9, log=lambda _m: None
    )
    link.start()
    assert reg.snapshot() == [link], "start() 之后必须在登记表里"
    link.stop()
    assert reg.count() == 0, "stop() 必须自己注销"
