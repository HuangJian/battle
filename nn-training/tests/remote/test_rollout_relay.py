"""tests/remote/test_rollout_relay.py —— 节点侧会合通道（`remote/rollout_relay.py`）。

覆盖三件事（都是真机事故的对应面）：

* **全链路**：hub 长轮询 → 本机 agent → 回执回到 hub，方法/路径/鉴权头/请求体/状态逐字透传；
* `local_port` 与注册 URL：节点只知道 hub 的公网地址，注册要写**本机回环**端口；
* 本地 4xx 是**回执**（agent 的 `409 wver not cached` 必须原样回给 trainer），而「端口不通」
  才是 502 —— 把两者混成一个状态码会让 trainer 的自愈路径失效。
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from remote.rollout_relay import RelayChannel, RelayLink, _forward


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
