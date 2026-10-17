"""test_hub_stream_send.py — hub 的**递送必须流式**（2026-10-17，plan/hub-server-memory-slim L1）。

**为什么要有它**：hub 连跑 35h 后 RSS 停在 6.2GB（peak 7.7GB），而所有活状态实测为空
（7 门课、`pending_n=0`、账本 81KB）。真因是「把整个文件 `read_bytes()` 成 bytes 再递出去」
——峰值随文件大小线性增长，而 CPython/Windows 堆释放后不归还 ⇒ 只涨不落的棘轮。
打 320MB 探针 RSS 零增长（堆里全是空洞）是那次的确证实验。

本文件钉的是**机制**，不是"某次递送没超内存"：

1. `_bytes_file` / `_bytes_files` 递 4×SEND_CHUNK 的文件时，**最大单块 ≤ SEND_CHUNK**
   且 `tracemalloc` 峰值增量 ≤ 2×SEND_CHUNK（旧实现 = 文件等长 bytes，Range 还要 ×2）；
2. 区间（`start/end`）与多件分帧的**字节流逐字节等价**于旧实现（不许"流式化 = 换了格式"）；
3. 空文件 / 缺件的边界行为不变。

**打真实实现**：把 `HubHandler` 的方法绑定到最小宿主上（`_Host`），假 `wfile` 收集写入。
不用替身重写一遍流式逻辑——那样测的是替身。
"""

from __future__ import annotations

import sys
import tracemalloc
from email.message import Message
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.http_face import SEND_CHUNK, HubHandler


class _Sink:
    """假 `wfile`：记录每次 write 的块长（⇒ 峰值/分块可直接断言）。

    `keep=False` = **不累积 body**（测 `tracemalloc` 峰值时用：否则这份 bytearray 自己就
    有一份文件等长的分配，把被测实现的峰值读数污染掉——第一次跑就踩了这个坑）。
    """

    def __init__(self, keep: bool = True) -> None:
        self.buf = bytearray()
        self.chunks: list[int] = []
        self.keep = keep

    def write(self, b: Any) -> int:
        mv = memoryview(b)
        n = mv.nbytes
        self.chunks.append(n)
        if self.keep:
            self.buf += mv
        return n

    @property
    def max_chunk(self) -> int:
        return max(self.chunks) if self.chunks else 0


class _Conn:
    """假 `connection`：只实现 `_send_body` 用的 `settimeout`。"""

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    def settimeout(self, t: float | None) -> None:
        self.timeouts.append(t)


class _Host(HubHandler):
    """**真 `HubHandler` 的子类**，只把「写出去」的三件套换成记录。

    没有 socket 可写（被测的是递送逻辑，不是 HTTP 栈）⇒ `send_response` / `send_header` /
    `end_headers` 记进列表、`_json` 也记。用**子类重写**而不是往实例上挂属性/绑函数：
    后者是「替换方法」这个动作，`mypy` 的 `method-assign` 与 `ruff` 的 `B010` 会在它身上
    打架（同 `test_hub_routes_split._Probe` 的理由）；而且绑函数会让 mypy 报
    `Invalid self argument`，逼人写 `type: ignore`——那等于把类型系统关掉。
    `object.__new__` 绕开 `BaseHTTPRequestHandler.__init__`（那个会开始处理请求）。
    """

    # 自有字段（类级注解：mypy 不认 `__new__` 里的首次赋值）
    headers_list: list[tuple[str, str]]
    status: int | None
    jsons: list[dict[str, Any]]
    #: 假 `wfile` 的**强类型**视图（基类把 `wfile` 注解成 `BufferedIOBase` ⇒
    #: 读 `.buf` / `.max_chunk` 一律走本字段，不要去改基类的注解）。
    sink: _Sink

    def __new__(cls, path: str = "/jobs/x/blob", keep: bool = True) -> _Host:
        self = object.__new__(cls)
        self.path = path
        self.connection = _Conn()  # type: ignore[assignment]
        self.sink = _Sink(keep=keep)
        self.wfile = self.sink  # type: ignore[assignment]
        self.headers = Message()
        self.headers_list = []
        self.status = None
        self.jsons = []
        return self

    def __init__(self, path: str = "/jobs/x/blob", keep: bool = True) -> None:
        """空实现：一切已在 `__new__` 里装好；父类那份会**开始处理请求**，必须绕开。"""

    # ---- `BaseHTTPRequestHandler` 那三个发头方法（重写；够断言即可）----
    def send_response(self, status: int) -> None:  # type: ignore[override]
        self.status = status

    def send_header(self, k: str, v: str) -> None:
        self.headers_list.append((k, v))

    def end_headers(self) -> None:
        pass

    def _json(self, obj: Any, status: int = 200) -> None:
        self.jsons.append({"json": obj, "status": status})

    def header(self, name: str) -> str:
        for k, v in self.headers_list:
            if k.lower() == name.lower():
                return v
        return ""


def _file(tmp_path: Path, name: str, n: int, *, seed: int = 0) -> tuple[Path, bytes]:
    p = tmp_path / name
    body = bytes((i * 7 + seed) % 256 for i in range(n))
    p.write_bytes(body)
    return p, body


# ─────────────────────── ① `_bytes_file`：分块 + 峰值 ───────────────────────


def test_bytes_file_is_chunked_and_peak_is_one_chunk(tmp_path: Path) -> None:
    """★ 递 4×SEND_CHUNK 的文件：最大单块 ≤ SEND_CHUNK，且峰值增量 ≤ 2×SEND_CHUNK。

    旧实现（`_bytes(p.read_bytes())`）的峰值 = 文件等长 bytes（1MB），这里必须量级更小。
    """
    n = SEND_CHUNK * 4
    p, body = _file(tmp_path, "payload.zip", n)
    # ① 内容 + 分块（wfile 累积 body）
    h = _Host()
    h._bytes_file(p)
    assert bytes(h.sink.buf) == body, "内容必须与文件逐字节相同"
    assert h.sink.max_chunk <= SEND_CHUNK, f"单块 {h.sink.max_chunk} > SEND_CHUNK {SEND_CHUNK}"
    assert h.header("Content-Length") == str(n)
    # ② 峰值（wfile **不累积**，否则测的是测试自己的 bytearray）
    h2 = _Host(keep=False)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[1]
    h2._bytes_file(p)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak - base <= 2 * SEND_CHUNK, f"峰值增量 {peak - base} 超出 2×SEND_CHUNK"


def test_bytes_file_range_serves_only_the_span(tmp_path: Path) -> None:
    """`start/end`（含两端）只读那一段，**不构造整份再切片**（旧实现 ×2 峰值的那一半）。"""
    p, body = _file(tmp_path, "p.bin", SEND_CHUNK * 3 + 17)
    lo = SEND_CHUNK + 5
    h = _Host()
    h._bytes_file(p, status=206, start=lo)
    assert bytes(h.sink.buf) == body[lo:], "体必须是尾巴"
    assert h.status == 206
    assert h.header("Content-Length") == str(len(body) - lo)
    assert h.sink.max_chunk <= SEND_CHUNK

    p2, body2 = _file(tmp_path, "q.bin", 5000, seed=3)
    h2 = _Host()
    h2._bytes_file(p2, start=100, end=199)
    assert bytes(h2.sink.buf) == body2[100:200]
    assert h2.header("Content-Length") == "100"


def test_bytes_file_empty_file_is_a_zero_byte_body(tmp_path: Path) -> None:
    """空文件：0 字节 body + `Content-Length: 0`（不抛、不进读循环）。"""
    p = tmp_path / "empty"
    p.write_bytes(b"")
    h = _Host()
    h._bytes_file(p)
    assert bytes(h.sink.buf) == b""
    assert h.header("Content-Length") == "0"


# ─────────────────────── ② `_bytes_files`：多件分帧 ───────────────────────


def _frames(buf: bytes) -> list[tuple[str, bytes]]:
    """按 `name\\nlen\\n` + raw 解帧（与客户端 `download_blobs` 同一格式）。"""
    out: list[tuple[str, bytes]] = []
    off = 0
    while off < len(buf):
        nl = buf.index(b"\n", off)
        name = buf[off:nl].decode("utf-8")
        nl2 = buf.index(b"\n", nl + 1)
        n = int(buf[nl + 1 : nl2])
        out.append((name, buf[nl2 + 1 : nl2 + 1 + n]))
        off = nl2 + 1 + n
    return out


def test_bytes_files_matches_the_wire_format_and_stays_chunked(tmp_path: Path) -> None:
    """三件（含一件 > SEND_CHUNK）：解帧**逐字节**等于旧 `join` 结果，且单块 ≤ SEND_CHUNK。"""
    a, ba = _file(tmp_path, "init", 4096, seed=1)
    b, bb = _file(tmp_path, "ref", SEND_CHUNK * 2 + 33, seed=2)
    c, bc = _file(tmp_path, "opt", 777, seed=3)
    entries = [("init", a), ("ref", b), ("opt", c)]
    expect = b"".join(
        n.encode("utf-8") + b"\n" + str(len(d)).encode("ascii") + b"\n" + d
        for n, d in (("init", ba), ("ref", bb), ("opt", bc))
    )
    hp = _Host(keep=False)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[1]
    hp._bytes_files(entries)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    h = _Host()
    h._bytes_files(entries)
    got = bytes(h.sink.buf)
    assert got == expect, "分帧字节流必须与旧实现逐字节一致"
    assert _frames(got) == [("init", ba), ("ref", bb), ("opt", bc)]
    assert h.header("Content-Length") == str(len(expect))
    assert h.sink.max_chunk <= SEND_CHUNK
    assert peak - base <= 2 * SEND_CHUNK, f"峰值增量 {peak - base} 超出 2×SEND_CHUNK"


# ─────────────────────── ③ 端点侧：404 语义不变 ───────────────────────


def test_serve_path_missing_file_is_still_404(tmp_path: Path) -> None:
    """`_serve_path` 改流式后，缺件仍是 404 **且原因措辞不变**（那是给排障的）。"""
    h = _Host()
    h._serve_path(tmp_path / "nope.zip", missing="no code zip")
    assert h.jsons and h.jsons[-1]["status"] == 404
    assert h.jsons[-1]["json"] == {"error": "no code zip"}
    assert bytes(h.sink.buf) == b"", "404 不该发 body"


class _Hub:
    def __init__(self, jd: Path) -> None:
        self.jd = jd

    def _job_dir(self, _jid: str) -> Path:
        return self.jd


def test_blob_batch_serves_all_parts_streamed(tmp_path: Path) -> None:
    """两件都在（`blob.init` / `blob.opt`）：端点侧**端到端**走流式，解帧逐字节正确。"""
    jd = tmp_path / "jd"
    jd.mkdir()
    init = bytes(range(256)) * 8  # 2KB
    opt = bytes((i * 13) % 256 for i in range(SEND_CHUNK + 111))
    (jd / "blob.init").write_bytes(init)
    (jd / "blob.opt").write_bytes(opt)
    h = _Host()
    h.hub = _Hub(jd)  # type: ignore[assignment]
    h._serve_blob_batch("j" * 16, ["init", "opt"])
    got = bytes(h.sink.buf)
    assert _frames(got) == [("init", init), ("opt", opt)]
    assert h.sink.max_chunk <= SEND_CHUNK, "端点侧也必须分块（不能整份 join）"
    assert not h.jsons


def test_blob_batch_missing_one_blob_fails_the_whole_request(tmp_path: Path) -> None:
    """批量 blob 缺任一件 ⇒ **整笔 404**（半份响应会让 worker 把「部分成功」读成「全拿到」）。"""
    jd = tmp_path / "jd"
    jd.mkdir()
    (jd / "blob.init").write_bytes(b"init-bytes")
    h = _Host()
    h.hub = _Hub(jd)  # type: ignore[assignment]
    h._serve_blob_batch("j" * 16, ["init", "opt"])  # blob.opt 不在盘上
    assert h.jsons and h.jsons[-1]["status"] == 404, h.jsons
    assert bytes(h.sink.buf) == b"", "缺件时不发任何字节"


def test_blob_batch_unknown_name_is_400(tmp_path: Path) -> None:
    """未知 blob 名 ⇒ 400（协议层 `ProtocolError`），与缺失（404）分得开。"""
    jd = tmp_path / "jd"
    jd.mkdir()
    h = _Host()
    h.hub = _Hub(jd)  # type: ignore[assignment]
    h._serve_blob_batch("j" * 16, ["nope"])  # 未知名 ⇒ 协议层先拒（与"在册但没落盘"的 404 分开）
    assert h.jsons and h.jsons[-1]["status"] == 400
    assert bytes(h.sink.buf) == b""
