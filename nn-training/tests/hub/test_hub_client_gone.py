"""tests/hub/test_hub_client_gone.py —— 「对端提前断开」不是 hub 的 ERROR。

**现场**（2026-11-18 22:59 DSW，relay 模式）：组件错误注册表一分钟收 16 条

    ERROR /relay/dsw/v1/result: [WinError 10053] An established connection was aborted
    by the software in your host machine

全是同一件事：`/relay/<node>/v1/result` 在 hub 侧要**等节点回执**（`_RELAY_TIMEOUTS` 给
45s）才写响应，而调用方（本机 trainer）自己的 urllib 预算更短 —— 它先超时走了，hub 拿着
一条已经死掉的 socket 去写，于是 10053/10054/EPIPE。

上一版把它当普通异常：先 `ERROR …` 记一笔（错），再 `_json({"error": "internal"}, 500)`
（写在死 socket 上 ⇒ **又抛一次**，那次的 traceback 才是注册表里真正那条）。本文件钉四件事：

* 对端先走 ⇒ 只落一行 `WARN … 对端提前断开（累计 N 次，非 hub 错误）`，**没有** `ERROR`；
* 那条 WARN 是**节流**的：首条 + 每 `CLIENT_GONE_LOG_EVERY` 条一条（否则刷屏红号照旧）；
* 这种请求不该把服务打挂，也不该往 stderr 吐 traceback；
* 反证：真异常（`RuntimeError`）依旧走 `ERROR` + 500 —— 别把修复做成「把所有异常都咽掉」。

**判据形态**：这里捕获的是 `HubHandler.log_message` 的入参（= 落进 hub 日志的那一行），
按**首部等值 / `startswith` / 计数**判，不做措辞子串匹配 —— 与 `probe_hub` 里
`_err_text` 走 `.startswith` 是同一条口径（文本断言护栏不抬格）。
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER
from hub import relay as relay_mod
from hub.http_face import CLIENT_GONE_LOG_EVERY, HubHandler
from hub.server import _HubQueue, make_server

TOKEN = "sekret"


def _boot(tmp_path: Path) -> ThreadingHTTPServer:
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _record_logs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把 `log_message` 换成记录器：拿到的是**落进 hub 日志的那一行**，顺序与真实一致。"""
    lines: list[str] = []

    def _log(_self: HubHandler, fmt: str, *args: object) -> None:
        lines.append(fmt % args)

    monkeypatch.setattr(HubHandler, "log_message", _log)
    return lines


def _wait_gone(n: int = 1, *, timeout: float = 5.0) -> bool:
    """等「对端先走」那条分支真的跑过（计数是它唯一的机器可读出口）。

    ⚠ 别拿「日志条数」当信号：`send_response()` 会先走一次 `log_request → log_message`
    （那一行是请求行，写在响应头**之前**），于是「已有一条日志」比「写失败」早一拍。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if int(HubHandler.state.client_gone.get("n", 0)) >= n:
            return True
        # sleep-ok: 轮询步长（等的是「计数已涨」这个谓词，兜底只挡挂起）
        time.sleep(0.02)
    return False


def _hang_up(sock: socket.socket) -> None:
    """带着未读响应直接关 ⇒ 立刻发 **RST**（不是 FIN），此后对端任何写都必然报错。

    `SO_LINGER` 的 `linger` 结构在 Windows 是 `HH`（两个 u_short）、POSIX 是 `ii`
    （两个 int）—— 写错格式在 Windows 上会 `setsockopt` 失败，测出来的就是「没发 RST、
    那次写成功了」，用例会假绿。
    """
    linger = struct.pack("HH", 1, 0) if os.name == "nt" else struct.pack("ii", 1, 0)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
    sock.close()


def test_client_gone_is_warn_not_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """调用方等不及先走了 ⇒ 一行 WARN（无 ERROR），且 hub 照常服务下一条请求。"""
    monkeypatch.setattr(HubHandler.state, "client_gone", {"n": 0})
    lines = _record_logs(monkeypatch)
    srv = _boot(tmp_path)
    port = int(srv.server_address[1])
    waiting = threading.Event()

    def node() -> None:
        req = relay_mod.RELAY.poll("gone", wait_sec=5.0)
        if req is None:  # 没取到待办：让主线程的 `wait` 超时暴露，别在这里静默过
            return
        # 走到这里证明 hub **已读完请求体、正阻塞在等回执** —— 此时再掐连接，
        # 才一定撞在「写响应」那一步（而不是撞在半路读 body 的 400 分支上）。
        waiting.set()
        # sleep-ok: 夹具模拟的工作量：留出 RST 到达对端栈的窗口（模拟「调用方先走」这一刻）
        time.sleep(0.2)
        relay_mod.RELAY.reply("gone", str(req["rid"]), 200, {}, b"late-reply")

    t = threading.Thread(target=node, daemon=True)
    t.start()
    body = b"{}"
    sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    sock.sendall(
        b"POST /relay/gone/v1/result HTTP/1.1\r\nHost: h\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    assert waiting.wait(5.0), "hub 没进入「等回执」状态（relay 队列里没看到这条 call）"
    _hang_up(sock)
    t.join(5.0)
    assert _wait_gone(1), f"没走到「对端先走」分支：{HubHandler.state.client_gone} / {lines}"

    # 结构面：那一族异常被**单独计数**了（不是走 `except Exception`），且只记了一次
    assert HubHandler.state.client_gone["n"] == 1, HubHandler.state.client_gone
    # 日志面：只有一行 WARN，且点名了路径与累计次数
    # （`send_response` 自己会打一行请求行，这里只看非请求行）
    notes = [ln for ln in lines if not ln.startswith('"')]
    assert len(notes) == 1, lines
    assert notes[0].startswith("WARN /relay/gone/v1/result: 对端提前断开（累计 1 次"), notes[0]

    # 服务没被打挂：同一台 hub 下一条请求照常 200
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ping", headers={AUTH_HEADER: f"Bearer {TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 200
    # 而 `socketserver` 的 `handle_error` 一次都没被惊动（惊动就会往 stderr 打整段栈）
    assert capsys.readouterr().err == ""


def test_real_error_still_logs_error_and_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反证：真异常不许被 `except ConnectionError` 顺手咽掉 —— 仍要 ERROR + 500。"""
    monkeypatch.setattr(HubHandler.state, "client_gone", {"n": 0})
    lines = _record_logs(monkeypatch)
    srv = _boot(tmp_path)

    class _Boom:
        # 探活短路会先问「通道心跳」与「上次载荷」：这里是**真调用**路径的反证用例，
        # 两个都回「没有」，好让它照旧走到 `call`（否则 AttributeError 先炸，测的就不是它了）。
        def channel_age(self, _node: str) -> None:
            return None

        def ping_snapshot(self, _node: str) -> tuple[None, float]:
            return None, 0.0

        def call(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom-for-test")

    monkeypatch.setattr("hub.http_face.NODE_RELAY", _Boom())
    url = f"http://127.0.0.1:{srv.server_address[1]}/relay/n1/v1/ping"
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(url, timeout=5.0)
    assert ei.value.code == 500

    errors = [ln for ln in lines if ln.startswith("ERROR")]
    assert errors == ["ERROR /relay/n1/v1/ping: boom-for-test"], lines
    # 真异常**不进**「对端先走」那个计数 —— 两件事不能被合成一件事
    assert HubHandler.state.client_gone["n"] == 0, HubHandler.state.client_gone


def test_warn_is_throttled_to_first_and_every_nth(monkeypatch: pytest.MonkeyPatch) -> None:
    """首条一定打（当场可见），此后每 N 条一条（15 连发不会刷 15 行红号）。"""
    monkeypatch.setattr(HubHandler.state, "client_gone", {"n": 0})
    err = ConnectionAbortedError(10053, "An established connection was aborted")
    notes = [
        HubHandler.note_client_gone("/relay/dsw/v1/result", err)
        for _ in range(CLIENT_GONE_LOG_EVERY * 2)
    ]
    kept = [(i + 1, note) for i, note in enumerate(notes) if note]
    assert [n for n, _ in kept] == [1, CLIENT_GONE_LOG_EVERY, CLIENT_GONE_LOG_EVERY * 2], kept
    assert kept[0][1].startswith("WARN /relay/dsw/v1/result: 对端提前断开（累计 1 次"), kept[0][1]
    assert kept[-1][1].startswith("WARN /relay/dsw/v1/result: 对端提前断开（累计 20 次"), kept[-1][
        1
    ]
    assert HubHandler.state.client_gone["n"] == CLIENT_GONE_LOG_EVERY * 2
