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
from typing import Literal

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER, RetryableError
from hub.server import _JobStore, make_server
from remote import http as http_mod
from remote import worker as worker_mod


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


def test_truncated_body_raises_named_timeout_with_byte_count() -> None:
    """★ 2026-10-03（与引导期护栏同一处缺陷，孪生实现）：声明 4.12MB 却在 14×256KB 处
    干净 FIN——旧行为把半截当成功返回（下载方拿到不完整 zip/body）；现在必须有正文地
    抛，让 `_get_with_retry` 当瞬时失败退避重取。

    机制：`http.client` 对 `read(amt)` 的提前 EOF **故意不抛** IncompleteRead（兼容），
    所以「收满 Content-Length」这道校验只能由护栏自己做。
    """
    resp = _FakeResp([b"x" * (256 * 1024)] * 14, headers={"Content-Length": "4322911"})
    with pytest.raises(TimeoutError) as ei:
        worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=None)
    msg = str(ei.value)
    assert "提前结束" in msg and "3670016" in msg and "4322911" in msg


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


def test_hub_big_send_is_bounded_and_loud(tmp_path, monkeypatch) -> None:
    """对端半开（读端不读）时，hub 必须在发送超时内**断开并打印**已发字节数。

    修复前：`wfile.write()` 永久阻塞 ⇒ handler 线程永久卡在写里、日志一个字没有
    （= 云机侧「claim 后零日志」的服务器半边）。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：原来「故意不读 + `sleep(2.5)`"
    然后去读 `capfd`——那个 2.5s 是押在「发送超时 0.5s 一定先到」上的，且它既不是同步
    手段也白花 2.5s 墙钟。现在抳住**那行日志本身**（print 探针置位事件）：得到的就是
    「hub 已经在超时内放弃并响亮打印」这个契约事件；`wait` 的 10s 只是挂起兜底。
    ⚠ patch 点是**实现所在的模块**（S4 第十六刀）：`SEND_TIMEOUT_SEC` 的唯一读者是
    `hub/http_face.py::HubHandler._bytes`，它在函数体里读的是 **http_face 的模块全局**。
    而 `hub.server.SEND_TIMEOUT_SEC` 只是同一个对象的 re-export ⇒ 对它 patch 是
    **静默空操作**（名字还在、没人读它）：本用例第一版正是这么挂的（hub 一个字都没打）。
    `tests/hub/test_hub_entry_split.py` 把「谁读它」机械钉住。
    """
    # 0.5 → 0.2（2026-09-29，§43）：判据是「在发送超时内断开并**响亮打印**已发字节」这个**事件**，
    # 不是那个 0.5s 本身；而半开是在第一块写进去之后才发生的，0.2s 与 0.5s 走的是同一条路径。
    monkeypatch.setattr("hub.http_face.SEND_TIMEOUT_SEC", 0.2)
    payload = b"P" * (4 * 1024 * 1024)
    srv, port, jid = _payload_server(tmp_path, payload)
    lines: list[str] = []
    stalled = threading.Event()
    real_print = print

    def _spy(*a, **kw):
        msg = " ".join(str(x) for x in a)
        if "发送**停滞**" in msg:
            lines.append(msg)
            stalled.set()
        real_print(*a, **kw)

    monkeypatch.setattr("builtins.print", _spy)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # 小读缓冲：写端很快填满
        s.sendall(
            (
                f"GET /jobs/{jid}/payload HTTP/1.0\r\n"
                f"Host: 127.0.0.1\r\n{AUTH_HEADER}: Bearer sekret\r\n\r\n"
            ).encode()
        )
        # 故意不读（否则写端不会被填满、停滞永远不会发生），等**停滞行**出现。
        assert stalled.wait(10.0), f"hub 未在超时内断掉并打印停滞行；至今输出：{lines!r}"
        stall_line = [ln for ln in lines if f"/jobs/{jid}/payload" in ln]
        assert stall_line, f"停滞行没带路径（无法归因到 /jobs/{jid}/payload）：{lines!r}"
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


# ──────────────── 上传让路（2026-10-12，plan/transfer-jitter-tolerance B） ────────────────


def test_paced_upload_slices_the_body_and_yields_between_pieces() -> None:
    """★ 大 body 上传要**分片 + 片间让路**：`_paced_body` 是那条让路点的唯一实现。

    病根：result POST 的 body 原先由 urllib **一次 sendall** ⇒ 整个上传窗口里 bulk 侧零让路
    点，控制面小包只能硬等（真机：21.6s 的上传窗口内 `acquire failed` 12s 超时）。
    """
    data = b"x" * (http_mod.BODY_CHUNK * 2 + 10)
    calls: list[int] = []

    def pace() -> float:
        calls.append(1)
        return 0.0

    got = b"".join(http_mod._paced_body(data, pace))
    assert got == data, "分片拼不回原 body"
    assert len(calls) == 3, f"片间让路次数不对（应 = 片数）：{len(calls)}"


def test_big_upload_carries_an_explicit_content_length(monkeypatch) -> None:
    """分片迭代器**必须显式带 `Content-Length`**：否则 `http.client` 改用 chunked 编码，而 hub
    只认 Content-Length（它会一直等满声明的字节数）。

    这条是「用了迭代器但没设长度」那类静默失败的**唯一**拦路判据（真链路上表现为 job 卡死）。
    """
    seen: dict = {}

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> Literal[False]:
            return False

    class _Opener:
        def open(self, req, timeout=None):
            seen["cl"] = req.get_header("Content-length")
            seen["body"] = req.data
            return _Resp()

    monkeypatch.setattr(http_mod, "_get_opener", lambda: _Opener())
    data = b"y" * (http_mod.BODY_CHUNK + 1)
    status, _body = http_mod._request(
        "http://hub.invalid",
        "tok",
        "/jobs/x/result",
        timeout=5.0,
        data=data,
        method="POST",
        pace=lambda: 0.0,
    )
    assert status == 200
    assert seen["cl"] == str(len(data)), f"没显式给 Content-Length：{seen['cl']}"
    assert not isinstance(seen["body"], (bytes, bytearray)), "body 没交成分片迭代器"


def test_small_upload_keeps_the_byte_body(monkeypatch) -> None:
    """小 body（≤ 一块）**不包装**：逐字保持改造前的行为（省一次生成器，也让 fake 好写）。"""
    seen: dict = {}

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> Literal[False]:
            return False

    class _Opener:
        def open(self, req, timeout=None):
            seen["body"] = req.data
            return _Resp()

    monkeypatch.setattr(http_mod, "_get_opener", lambda: _Opener())
    data = b"y" * 128
    http_mod._request(
        "http://hub.invalid",
        "tok",
        "/jobs/x/result",
        timeout=5.0,
        data=data,
        method="POST",
        pace=lambda: 0.0,
    )
    assert isinstance(seen["body"], (bytes, bytearray)) and bytes(seen["body"]) == data


# ════════════════════════════════════════════════════════════════════════════
# M1/M2/M3（2026-10-13，plan/body-transfer-guard-v2.plan.md）
#
# 现场：2,608,740 B 的 payload 下了 343.5 s，而 3 分钟后同一条链路 947 KB/s。根因是**读数粒度**：
# `resp.read(256KB)` 是**定长读**（循环 `recv` 凑满 256KB 才返回）⇒ 隧道只要每 <45s 吐 1 字节，
# 它既不返回也不超时，`BODY_IDLE_TIMEOUT_SEC=45` **永不触发**；而重抽判据的**唯一一次**采样
# 又被绑在这块上，且首块一到就永久置位（`probed`）。
#
# 下面三条钉住修法：① 收字节口改 `read1`（至多一次 `recv`）；② 判据按**时间窗**采样、
# **连续两窗**慢才断；③ 超时异常带已收字节（账里要能归因）。
# ════════════════════════════════════════════════════════════════════════════


def _drip_http_server(count: int, gap: float):
    """本机 HTTP 服务：在 `gap` 秒的间隔里每次只吐 1 字节（共 `count` 字节）。

    这是「慢但不停」的确定性复刻（不是停滞：任何单次 `recv` 都没超时）。
    返回 `(server, url)`；调用方负责 `shutdown()/server_close()`。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Drip(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(count))
            self.end_headers()
            for _ in range(count):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()  # 逐字节落线 = 每 <gap 一次 recv 就有数据
                except OSError:  # 客户端已断开（用例结束）：正常收场
                    return
                # sleep-ok: 夹具模拟的「慢链路」本身：滴流的间隔就是被测语义
                time.sleep(gap)

        def log_message(self, *_a) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Drip)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/payload"


class _DripResp:
    """`read1` 形状的假响应：每次只吐 `size` 字节（= 一次 `recv`）。

    `read` 故意抛 —— 定长读是本次事故的根因，守卫再走回去必须当场炸出来。
    """

    def __init__(self, chunks: int, size: int = 1024, total: int | None = None):
        self.chunks = chunks
        self.size = size
        self.headers = {"Content-Length": str(total if total is not None else chunks * size)}
        self.status = 200

    def read1(self, _n: int) -> bytes:
        if self.chunks <= 0:
            return b""
        self.chunks -= 1
        return b"x" * self.size

    def read(self, _n: int) -> bytes:
        raise AssertionError("body 读回到了定长 read（M1：必须走 read1）")


class _FakeClock:
    """确定性墙钟：每次调用前进 `step` 秒。

    判据按**时间窗**采样 ⇒ 测试不能靠真等（块是瞬间到的，真时钟下窗永远不开）。
    """

    def __init__(self, start: float = 1000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _stub_decision(monkeypatch, verdicts: list[bool]) -> list[tuple]:
    """把 `_reroll_decision` 换成按顺序出牌的替身；返回它每次收到的参数（供断言窗口径）。"""
    seen: list[tuple] = []
    it = iter(verdicts)

    def fake(got: int, total: int, elapsed: float, **kw: object):
        seen.append((got, total, elapsed, kw))
        return next(it, False), 6.4 * 1024, 1180.0

    monkeypatch.setattr(http_mod, "_reroll_decision", fake)
    return seen


def test_recv_some_prefers_read1_and_keeps_read_only_doubles_usable() -> None:
    """M1：真响应走 `read1`；只实现 `read` 的测试替身仍可用（回退不改字节流）。"""
    assert http_mod._recv_some(_DripResp(chunks=1, size=8), 4096) == b"x" * 8

    class _ReadOnly:
        def read(self, _n: int) -> bytes:
            return b"y" * 3

    assert http_mod._recv_some(_ReadOnly(), 4096) == b"y" * 3


def test_recv_some_does_not_wait_for_the_full_amount() -> None:
    """★ M1 的判据（真 socket）：`read1` 至多一次 `recv` ⇒ 服务端滴一点，客户端就回头一次。

    4 字节 / 每 0.15s 一滴 ⇒ 定长 `read(4)` 要 ~0.6s 才返回；`_recv_some` 的第一次调用
    必须在**前两滴之内**返回（0.45s 是 3× 间隔的松上界），否则「读粒度」还是块粒度。
    """
    import urllib.request

    count, gap = 4, 0.15
    srv, url = _drip_http_server(count, gap)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            t0 = time.time()
            first = http_mod._recv_some(resp, count)
            dt_first = time.time() - t0
            got = first
            while len(got) < count:
                block = http_mod._recv_some(resp, count)
                if not block:
                    break
                got += block
        # timing-ok: 夹具模拟（上界 = 夹具自己配置的滴流间隔 ×3，量的是「有没有等满整份 body」）
        assert dt_first < 3 * gap, f"第一次返回用了 {dt_first:.2f}s（= 等满了整份 body）"
        assert got == b"x" * count  # 分多次读，字节流仍然一致（parity）
    finally:
        srv.shutdown()
        srv.server_close()


def test_guard_fires_inside_two_idle_timeouts_on_a_dripping_link(monkeypatch) -> None:
    """★ 本次 DoD：链路**慢但不停**（每 0.1s 1 字节）时判据必须动手。

    现状：定长读凑不满一块 ⇒ 空闲判据结构上永不触发，一路烧到 `total_timeout`。
    修后：判据按时间窗（压到 0.3s）+ 连续 2 窗迟滞 ⇒ 在 2×空闲超时内抛出。
    """
    import urllib.request

    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 0.3)
    monkeypatch.setattr(http_mod, "WIRE_REROLL_PROBES", 2)
    count, gap, idle = 1000, 0.1, 1.0
    srv, url = _drip_http_server(count, gap)
    try:
        req = urllib.request.Request(url)
        t0 = time.time()
        with (
            urllib.request.urlopen(req, timeout=idle) as resp,
            pytest.raises(worker_mod.WireSlowError) as ei,
        ):
            http_mod._read_body(resp, idle_timeout=idle, total_timeout=30.0, allow_reroll=True)
        dt = time.time() - t0
        # timing-ok: 相对判据（上界由场景推出：2 × 空闲超时，不是绝对墙钟常数）
        assert dt <= 2 * idle + 0.5, f"判据没在 2×空闲超时内动手（用了 {dt:.2f}s）"
        assert ei.value.bytes_read >= 1
        assert ei.value.remain_sec > worker_mod.WIRE_REROLL_BUDGET_SEC
    finally:
        srv.shutdown()
        srv.server_close()


def test_read_body_needs_two_consecutive_slow_windows(monkeypatch) -> None:
    """★ R2′：判据按**时间窗**采样，且要**连续两窗**都慢才断。

    序列「快、快、快、慢、慢」：现状（一次性首块 + 首块一到就置位）在第 1 次就永久关掉自己
    （现场那条 343.5 s 的行就是这么来的）；新实现必须走到**第 5 窗**才动手。
    """
    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 1.0)
    monkeypatch.setattr(http_mod, "WIRE_REROLL_PROBES", 2)
    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(http_mod.time, "time", clock)
    seen = _stub_decision(monkeypatch, [False, False, False, True, True])
    resp = _DripResp(chunks=8, size=1024)
    with pytest.raises(worker_mod.WireSlowError) as ei:
        worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True)
    assert len(seen) == 5, f"快窗把判据关掉了（只判了 {len(seen)} 次）"
    # 样本是**窗内**字节（不是累计），且真实剩余显式传入
    assert [s[0] for s in seen] == [1024] * 5
    assert all(s[3].get("remaining", 0) > 0 for s in seen)
    # 作废的只有「到第二窗判慢」那一刻收到的字节
    assert ei.value.bytes_read == 5 * 1024


def test_a_single_slow_window_does_not_reroll(monkeypatch) -> None:
    """迟滞的反面：一个窗口的坑（抖动 / 重连后的 TCP 慢启动）不该触发换连接。"""
    monkeypatch.setattr(http_mod, "WIRE_PROBE_SEC", 1.0)
    monkeypatch.setattr(http_mod, "WIRE_REROLL_PROBES", 2)
    monkeypatch.setattr(http_mod.time, "time", _FakeClock(step=1.0))
    seen = _stub_decision(monkeypatch, [True, False, True, False])  # 从不连续两窗慢
    resp = _DripResp(chunks=10, size=1024)
    out = worker_mod._read_body(resp, idle_timeout=45.0, total_timeout=300.0, allow_reroll=True)
    assert len(out) == 10 * 1024
    assert len(seen) >= 3, f"判据又只跑了一次就关（{len(seen)} 次）"


def test_timeout_error_carries_the_bytes_already_received() -> None:
    """M3/R3：超时与停滞抛的异常必须能读出「已收多少」（失败账与日志都要用它）。

    类型挂在 `TimeoutError` 之下（既有的捕获面不变），载荷是 `got/partial/total`。
    """
    with pytest.raises(http_mod.BodyTimeoutError) as ei:
        worker_mod._read_body(
            _FakeResp([b"x" * 700], headers={"Content-Length": "1000"}),
            idle_timeout=45.0,
            total_timeout=-1.0,
        )
    assert ei.value.got == 700 and ei.value.total == 1000
    assert ei.value.partial == b"x" * 700
    assert isinstance(ei.value, TimeoutError), "既有按 TimeoutError 捕获的调用面不许破"

    with pytest.raises(http_mod.BodyTimeoutError) as ei2:
        worker_mod._read_body(
            _FakeResp([b"x" * 300, TimeoutError()], headers={"Content-Length": "1000"}),
            idle_timeout=45.0,
            total_timeout=None,
        )
    assert ei2.value.got == 300 and len(ei2.value.partial) == 300
