"""test_body_transfer_guard.py — 大 body 传输的**停滞判据**（2026-09-20 云机卡死事故）。

事故现场（用户报障）：「云机 claim 第二个 job 后几分钟一直没有动静，也没有 log 打出」。

两侧同时沉默，各自的原因：

  * **worker 侧**：`download_payload` 只有一把 `timeout=300` 的**整读**——
    `resp.read()` 中途停在隧道/代理上（同机 cloudflared 当时每 5min 一条
    `lookup region1.v2.argotunnel.io: i/o timeout`，小 POST 心跳照常），
    客户端静默等满 5 分钟；而 socket 超时抛出的是**没有正文**的 `TimeoutError()`，
    连日志里那句「瞬时失败」都写不出原因。**过程没有任何进度输出**。
  * **hub 侧**：`wfile.write()` 没有发送超时——对端半开时永久阻塞，`/payload` 的
    访问行又被高频静默规则吃掉 ⇒ hub 的日志里一个字都没有。

本文件钉住三件事：停滞**有名字**（带已收字节数）、过程**可见**（进度行）、
hub 发送**有界**（超时即断且响亮）。免 torch（只碰 http 客户端/服务端）。
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER, RetryableError
from remote import http as http_mod
from remote import worker as worker_mod
from remote.hub_server import _JobStore, make_server


class _FakeResp:
    """假响应：逐块吐数据，块可以是异常（模拟停滞/断开）。"""

    def __init__(self, chunks: list[bytes | BaseException], headers: dict | None = None):
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.status = 200

    def read(self, _n: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk


# ---------------------------------------------------------------- worker 侧


def test_body_stall_raises_named_timeout_with_byte_count() -> None:
    """停在半路的读必须以**有正文的** TimeoutError 结束（不是裸 `TimeoutError()`）。"""
    resp = _FakeResp([b"x" * 1000, TimeoutError()], headers={"Content-Length": "9999"})
    with pytest.raises(TimeoutError) as ei:
        worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=None)
    msg = str(ei.value)
    assert "停滞" in msg and "1000 bytes" in msg and "9999" in msg


def test_body_progress_is_reported_and_budget_enforced(monkeypatch) -> None:
    """每片都报进度（节流值置 0）；总预算到点即抛（治「永远在滴水」）。"""
    # 注入点 = `remote.http`：`_read_body` 已搬进 http（S4 第五步），它读的是本模块的全局。
    monkeypatch.setattr(http_mod, "BODY_PROGRESS_MIN_SEC", 0.0)
    seen: list[tuple[int, int, float]] = []
    resp = _FakeResp([b"a" * 700, b"b" * 700], headers={"Content-Length": "1400"})
    out = worker_mod._read_body(
        resp,
        idle_timeout=45.0,
        total_timeout=None,
        progress=lambda got, total, el: seen.append((got, total, el)),
    )
    assert out == b"a" * 700 + b"b" * 700
    assert [s[0] for s in seen] == [700, 1400] and all(s[1] == 1400 for s in seen)

    # 预算已用尽（负数 = 必定超）⇒ 收到第一片就该抛（不依赖时钟分辨率）。
    with pytest.raises(TimeoutError, match="body 超时"):
        worker_mod._read_body(
            _FakeResp([b"a" * 10]), idle_timeout=45.0, total_timeout=-1.0
        )


def test_progress_logger_line_shape() -> None:
    """进度行必须能一眼看出「在下」：字节数 / 总量 / 百分比 / 速率。"""
    lines: list[str] = []
    report = worker_mod._progress_logger("job j1: payload", lines.append)
    report(3 * 1024 * 1024, 4 * 1024 * 1024, 12.0)
    assert lines and "3.00 MB" in lines[0] and "4.00 MB" in lines[0]
    assert "75%" in lines[0] and "256 KB/s" in lines[0]


def test_download_payload_passes_idle_guard(monkeypatch) -> None:
    """下载路径必须真的把空闲超时与进度回调交给 `_request`（接线即契约）。"""
    seen: dict[str, object] = {}

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        seen.update(kw)
        seen["timeout"] = timeout
        return 200, b"payload"

    monkeypatch.setattr(http_mod, "_request", fake_request)
    assert worker_mod.download_payload("http://hub", "t", "jid1", log=lambda _m: None) == b"payload"
    assert seen["idle_timeout"] == worker_mod.BODY_IDLE_TIMEOUT_SEC
    assert seen["total_timeout"] == worker_mod.BODY_TOTAL_TIMEOUT_SEC
    assert callable(seen["progress"])


def test_stalled_download_is_loud_and_retried(monkeypatch) -> None:
    """事故回归：停滞 → 一条带原因的日志 + 退避重试（不再静默等到天荒地老）。"""
    monkeypatch.setattr(worker_mod.time, "sleep", lambda _s: None)
    logs: list[str] = []
    calls: list[str] = []

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        calls.append(path)
        if len(calls) == 1:
            raise TimeoutError("body 停滞：45s 内没有新字节（已收 131072 bytes / 共 4848536）")
        return 200, b"payload-bytes"

    monkeypatch.setattr(http_mod, "_request", fake_request)
    out = worker_mod.download_payload("http://hub", "t", "jid1", log=logs.append)
    assert out == b"payload-bytes" and len(calls) == 2
    retry = [line for line in logs if "瞬时失败" in line and "停滞" in line]
    assert retry and "2s 后第 2/3 次重试" in retry[0]


def test_stalled_download_exhausts_into_retryable(monkeypatch) -> None:
    """停滞耗尽重试 → RetryableError（释放租约、重新领取；语义与旧行为一致）。"""
    monkeypatch.setattr(worker_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        http_mod,
        "_request",
        lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError("body 停滞：45s 无新字节")),
    )
    with pytest.raises(RetryableError, match="重试 3 次"):
        worker_mod.download_payload("http://hub", "t", "jid1", attempts=3, log=lambda _m: None)


# ---------------------------------------------------------------- hub 侧


def _payload_server(tmp_path: Path, payload: bytes):
    """起一个 hub（随机端口）+ 一份 pad 好的 job，返回 (port, jid)。"""
    store = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    srv = make_server(store, 0, "sekret", host="127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    jid = "f" * 16
    store.publish(
        jid,
        {
            "job_id": jid,
            "it": 1,
            "runId": "r",
            "commit": "c" * 40,
            "code_sha256": "z" * 64,
            "course_fp": "f" * 64,
            "payload_sha256": "p" * 64,
        },
        payload,
    )
    return srv, port, jid


def test_hub_big_send_is_bounded_and_loud(tmp_path, monkeypatch, capfd) -> None:
    """对端半开（读端不读）时，hub 必须在发送超时内**断开并打印**已发字节数。

    修复前：`wfile.write()` 永久阻塞 ⇒ handler 线程永久卡在写里、日志一个字没有
    （= 云机侧「claim 后零日志」的服务器半边）。
    """
    monkeypatch.setattr("remote.hub_server.SEND_TIMEOUT_SEC", 0.5)
    payload = b"P" * (4 * 1024 * 1024)
    srv, port, jid = _payload_server(tmp_path, payload)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # 小读缓冲：写端很快填满
        s.sendall(
            (
                f"GET /jobs/{jid}/payload HTTP/1.0\r\n"
                f"Host: 127.0.0.1\r\n{AUTH_HEADER}: Bearer sekret\r\n\r\n"
            ).encode()
        )
        # 故意不读：等发送超时（0.5s）触发。留 3s 富余。
        time.sleep(2.5)
        logged = capfd.readouterr()
        stall_line = [
            line
            for line in (logged.out + logged.err).splitlines()
            if "发送**停滞**" in line and f"/jobs/{jid}/payload" in line
        ]
        assert stall_line, f"hub 未打印发送停滞行；实际输出：{logged.out!r}{logged.err!r}"
        assert "已发" in stall_line[0]
        # 连接已断开：继续读到 EOF，且总量 < 整份 payload（截断 = 真的没写完）
        s.shutdown(socket.SHUT_WR)
        s.settimeout(5.0)
        got = b""
        try:
            while True:
                block = s.recv(65536)
                if not block:
                    break
                got += block
        except OSError:  # 对端 RST 也算断开
            pass
        assert len(got) < len(payload)
        s.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_hub_small_send_has_no_completion_noise(tmp_path, capfd) -> None:
    """小 body（JSON）不打「发送完成」——高频端点不能刷日志。"""
    import urllib.request

    store = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    srv = make_server(store, 0, "sekret", host="127.0.0.1")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/admin/queue", headers={AUTH_HEADER: "Bearer sekret"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
        logged = capfd.readouterr()
        assert "响应发送完成" not in (logged.out + logged.err)
    finally:
        srv.shutdown()
        srv.server_close()
