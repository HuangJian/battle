"""test_payload_range.py — `/jobs/{id}/payload` 的 **Range 断点续传**（2026-10-12，抖动容忍 A）。

**为什么要有它**：worker 的低速重抽原本是「丢掉已收、从零重来」——真机每次白扔 256KB + 一次
建连，而重抽并没换来任何恢复（79 → 51 KB/s）。hub 侧回一个 `206` 之后，重抽变成**续传** ⇒
抖动是常态的前提下，重试的代价趋近 0。

本文件钉两件事：① `parse_range` 的纯函数判据（开区间 / 闭区间 / 越界 / 多区间 / 不可满足）；
② `_get_payload` 的三条分派（整份 200 / 续传 206 + `Content-Range` / 不可满足回落整份），
以及 M0 计账记的是**实际服务出去的字节**（续传时它小于文件长度 —— 那正是省下来的量）。

免 socket：用 `HubHandler` 的最小替身（只装 `headers` / `hub` / `_bytes` / `_job_or_404`）。
"""

from __future__ import annotations

import sys
from email.message import Message
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import PAYLOAD_NAME
from hub import blob as blob_mod
from hub.blob import parse_range

# ─────────────────────────── ① 纯函数：parse_range ───────────────────────────


def test_parse_range_table() -> None:
    """单区间的六种形态 + 四类拒绝。`None` = 按整份服务（RFC 允许忽略 Range）。"""
    assert parse_range(None, 1000) is None, "不带 Range"
    assert parse_range("", 1000) is None
    assert parse_range("bytes=0-", 1000) == (0, 999), "开区间"
    assert parse_range("bytes=256-", 1000) == (256, 999)
    assert parse_range("bytes=256-511", 1000) == (256, 511), "闭区间"
    assert parse_range("bytes=900-4096", 1000) == (900, 999), "末端越界 ⇒ 截到尾部"
    assert parse_range("bytes=1000-", 1000) is None, "起点 = 长度 ⇒ 不可满足"
    assert parse_range("bytes=99999-", 1000) is None
    assert parse_range("bytes=0-,5-", 1000) is None, "多区间不支持（回整份）"
    assert parse_range("items=0-", 1000) is None, "单位不是 bytes"
    assert parse_range("bytes=abc-", 1000) is None, "非法数字"
    assert parse_range("bytes=0-", 0) is None, "空文件"


# ─────────────────────── ② 路由分派：整份 / 续传 / 回落 ───────────────────────


class _Hub:
    """只装 `_get_payload` 用到的两面：找 job 目录 + M0 计账。"""

    def __init__(self, jd: Path) -> None:
        self.jd = jd
        self.sent: list[tuple[str, int]] = []

    def _job_dir(self, _jid: str) -> Path:
        return self.jd

    def record_payload_sent(self, jid: str, n: int) -> None:
        self.sent.append((jid, int(n)))


class _Handler:
    """`BlobRoutes` 的最小宿主：只要 `headers` / `hub` / `_bytes` / `_job_or_404`。"""

    def __init__(self, jd: Path, rng: str | None = None) -> None:
        self.hub = _Hub(jd)
        self.headers = Message()
        if rng is not None:
            self.headers["Range"] = rng
        self.calls: list[dict[str, Any]] = []

    def _job_or_404(self) -> str:
        return "j" * 16

    def _json(self, obj: object, status: int = 200) -> None:
        self.calls.append({"json": obj, "status": status})

    def _bytes(
        self,
        data: bytes,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.calls.append(
            {
                "n": len(data),
                "status": status,
                "extra": dict(extra_headers or {}),
                "data": data,
            }
        )

    def _bytes_file(
        self,
        path: Path,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
        *,
        extra_headers: dict[str, str] | None = None,
        start: int = 0,
        end: int | None = None,
    ) -> None:
        """`_get_payload` 2026-10-17 起走流式（plan/hub-server-memory-slim L1）。

        替身只做「按区间读出字节」，不复制传输语义——流式本身由
        `tests/hub/test_hub_stream_send.py` 打真实实现钉住。
        """
        size = path.stat().st_size
        lo = max(0, int(start))
        hi = size - 1 if end is None else min(int(end), size - 1)
        with open(path, "rb") as f:
            f.seek(lo)
            data = f.read(max(0, hi - lo + 1))
        self.calls.append(
            {
                "n": len(data),
                "status": status,
                "extra": dict(extra_headers or {}),
                "data": data,
            }
        )

    def get(self) -> dict[str, Any]:
        blob_mod.BlobRoutes._get_payload(self)  # type: ignore[arg-type]
        return self.calls[-1]


def _payload(tmp_path: Path, n: int = 10_240) -> tuple[Path, bytes]:
    d = tmp_path / "jobdir"
    d.mkdir(parents=True, exist_ok=True)
    body = bytes(range(256)) * (n // 256)
    (d / PAYLOAD_NAME).write_bytes(body)
    return d, body


def test_no_range_serves_the_whole_file(tmp_path: Path) -> None:
    """不带 Range ⇒ 逐字旧行为：整份 200 + 计满。"""
    d, body = _payload(tmp_path)
    h = _Handler(d)
    last = h.get()
    assert last["status"] == 200 and last["n"] == len(body)
    assert last["extra"] == {}, "整份响应不该带 Content-Range"
    assert h.hub.sent == [("j" * 16, len(body))]


def test_a_range_request_is_served_as_206_with_content_range(tmp_path: Path) -> None:
    """★ `bytes=N-` ⇒ `206` + `Content-Range: bytes N-(len-1)/len`，体是**尾巴**。"""
    d, body = _payload(tmp_path)
    n = len(body) - 240
    h = _Handler(d, f"bytes={n}-")
    last = h.get()
    assert last["status"] == 206, last
    assert last["n"] == 240 and last["data"] == body[n:], "体必须是尾巴"
    assert last["extra"]["Content-Range"] == f"bytes {n}-{len(body) - 1}/{len(body)}"
    assert last["extra"]["Accept-Ranges"] == "bytes"
    assert h.hub.sent == [("j" * 16, 240)], "计账按**实际服务出去**的字节（续传时更少）"


def test_a_unsatisfiable_range_falls_back_to_the_whole_file(tmp_path: Path) -> None:
    """起点 ≥ 文件长度 ⇒ 回落整份 200（而不是 416）：客户端没有「从头再来」的分支，416 会被它
    当成一次瞬时失败去退避重试 —— 回落整份是**可续**的语义，也与旧 hub 行为一致。"""
    d, body = _payload(tmp_path)
    h = _Handler(d, "bytes=99999-")
    last = h.get()
    assert last["status"] == 200 and last["n"] == len(body)
    assert h.hub.sent == [("j" * 16, len(body))]
