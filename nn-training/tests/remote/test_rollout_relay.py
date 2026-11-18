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
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from common import net_http
from remote import rollout_relay as rr
from remote.rollout_relay import RelayChannel, RelayLink, RelayUnreachable, _forward, probe_hub


class _Quiet(BaseHTTPRequestHandler):
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
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
    state: dict = {"pending": list(pending), "replies": {}, "port": 0, "done": threading.Event()}
    lock = threading.Lock()

    class Hub(_Quiet):
        def do_GET(self) -> None:
            if not self.path.startswith("/relay/n1/_poll"):
                self._json({"error": "not found"}, 404)
                return
            with lock:
                req = state["pending"].pop(0) if state["pending"] else None
            self._json({"req": req, "local_port": state["port"]})

        def do_POST(self) -> None:
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
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
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
        assert not old._threads
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


# ---------------------------------------------------------------- 连不上时说得清（2026-11-18 DSW）

def _dead_port() -> int:
    """一个**没人监听**的本机端口（连上必 ECONNREFUSED，且是秒级，不靠墙钟）。"""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


@pytest.fixture
def dead_link() -> Iterator[tuple[RelayLink, list[str]]]:
    """指向「没人监听的端口」的通道 + 它自己的日志表。"""
    logs: list[str] = []
    link = RelayLink(
        hub_url=f"http://127.0.0.1:{_dead_port()}",
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


def test_unreachable_hub_raises_with_reason_and_host(
    dead_link: tuple[RelayLink, list[str]],
) -> None:
    """`_poll` 全崩时抛的是**带 reason 的** `RelayUnreachable`，不再是裸 `URLError`。"""
    link, _logs = dead_link
    with pytest.raises(RelayUnreachable) as ei:
        link._poll()
    text = link.last_error
    assert text.startswith("direct="), f"last_error 必须是逐路由结论：{text!r}"
    assert any(k in text for k in ("refused", "timeout"))
    assert isinstance(ei.value, urllib.error.URLError)  # 上层 except URLError 语义不变


def test_failure_log_names_host_and_reason(dead_link: tuple[RelayLink, list[str]]) -> None:
    """首条失败日志 = `hub:port` + 死因 + 耗时；后续连续失败**不许**刷屏。"""
    link, logs = dead_link
    with pytest.raises(RelayUnreachable) as ei:
        link._poll()
    link._log_failure(ei.value, 35.0)
    assert len(logs) == 1
    assert logs[0].startswith(f"relay 轮询失败（hub={link.hub_host}，35.0s）"), logs[0]
    assert any(k in logs[0] for k in ("refused", "timeout"))
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
    assert rr._err_text(
        urllib.error.URLError(ConnectionRefusedError(61, "refused"))
    ).startswith("refused")
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
    ok, line, facts = probe_hub(f"http://127.0.0.1:{_dead_port()}", "t", "n1", timeout=2.0)
    assert ok is False
    assert set(facts["routes"].values()) == {"fail"}
    assert "hub_url" in line  # 判词要直接指向可改的那个开关


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
    real_racing = net_http.racing_opener

    def _hang(*, no_proxy: bool = False):
        if no_proxy:  # 「直连」那条腿必须还是真的（否则这条用例只是在测桩）
            return real_racing(no_proxy=True)
        raise urllib.error.URLError(TimeoutError("timed out"))

    logs: list[str] = []
    link = RelayLink(
        hub_url=hub_url, token="t", node_id="n1", agent_port=agent_port, log=logs.append, workers=1
    )
    try:
        # 回环默认 `bypass_proxy` ⇒ 只有一条路由；这里人为放开，才验得到「代理吊死 → 直连」
        monkeypatch.setattr(net_http, "bypass_proxy", lambda _u: False)
        monkeypatch.setattr(net_http, "racing_opener", _hang)
        assert link._route_order() == ["inherit", "direct"]
        answer = link._poll()
        assert answer is not None and answer.get("local_port")
        assert link._route == "direct", "成功的路由要记住，下一拍别再去撞代理"
        assert link._route_order() == ["direct", "inherit"]
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
        assert ch.link._route == "direct", "回环只有直连一条腿，预检就该把结论传下去"
    finally:
        ch.stop()
        hub_srv.shutdown()
        agent_srv.shutdown()


def test_channel_open_failure_names_preflight_and_host() -> None:
    """放弃时人读面必须可用：预检行 + 未就绪行（带 host）、并且不再误导去看 cloudflared。"""
    agent_log: dict = {}
    agent_srv, agent_port = _fake_agent(agent_log)
    port = _dead_port()
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
    assert any(ln.startswith(f"relay 预检 127.0.0.1:{port} ") for ln in logs), logs
    joined = "\n".join(logs)
    assert f"hub=127.0.0.1:{port}" in joined and "cloudflared" in joined
