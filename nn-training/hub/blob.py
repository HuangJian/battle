"""hub/blob.py —— 字节服务面（2026-09-24 S4 第十一刀）。

「把这份 job 的某个文件按 HTTP 递出去」的全部端点：

    GET /jobs/{id}/payload   payload.zip（M0 计量：`record_payload_sent`）
    GET /jobs/{id}/code      code.zip
    GET /jobs/{id}/ts_code   ts_code.zip（M3：kind=iter 的节点用）
    GET /jobs/{id}/blob      ?name=opt|ref（M2 B3 内容寻址）
    GET /code                共享 code.zip（colab bootstrap 用；多课程取第一份真存在的）

## 这一组的形状本来就该收成一个助手

五条端点是同一件事（定位 → 不存在就 404 说清原因 → `_bytes` 递出去），原先各写一遍。
Phase B 把它们收成 `self._serve_path(...)` + `self._job_or_404()`，404 体形状统一。
依赖只到 `common.protocol`（`find_payload` / `blob_path` / `TS_CODE_NAME`）⇒ 秩 **0**。
"""

from __future__ import annotations

import urllib.parse
from email.message import Message
from io import BufferedIOBase
from pathlib import Path
from typing import Any

from common.protocol import (
    TS_CODE_NAME,
    ProtocolError,
    blob_path,
    find_payload,
)


def parse_range(raw: str | None, total: int) -> tuple[int, int] | None:
    """解析 `Range: bytes=N-` / `bytes=N-M` → `(start, end)`（**含两端**，HTTP 语义）。

    返回 `None` = 「不带 Range 或不可满足」⇒ 调用方按**整份 200** 服务（这是 RFC 允许的：
    服务器可以忽略 Range）。只支持单区间——本仓唯一的客户端是自家 worker，它只发
    `bytes=N-`。**纯函数**：这是本仓第一条 Range 支持，判据必须能被单测直接钉住。

    为什么不返回 416：`start >= total` 时回 416 更"正确"，但客户端那边只会当成一次瞬时失败
    去退避重试（它没有「从头再来」的分支）；回整份 200 是**可续**的语义，且与旧 hub 行为一致。
    """
    if not raw:
        return None
    spec = str(raw).strip()
    if not spec.lower().startswith("bytes="):
        return None
    spec = spec[len("bytes=") :].strip()
    if "," in spec:  # 多区间：不支持（忽略 ⇒ 整份）
        return None
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    try:
        start = int(start_s) if start_s.strip() else 0
        end = int(end_s) if end_s.strip() else total - 1
    except ValueError:
        return None
    if start < 0 or total <= 0 or start >= total:
        return None
    return start, min(end, total - 1)


class BlobRoutes:
    """blob 路由 mixin（`HubHandler(…, BlobRoutes, BaseHTTPRequestHandler)`）。"""

    # 由组合类（HubHandler）提供的运行时状态与通用助手——混入不继承 BaseHTTPRequestHandler，
    # 故需自行声明类型（否则 mypy 报 attr-defined）。⚠ 类型必须与 typeshed 逐字一致
    # （`headers: email.message.Message` / `rfile: BufferedIOBase` / `path: str`）：本混入在 MRO 里
    # 早于 BaseHTTPRequestHandler，声明成 Any 会把组合类里 `self.headers.get(...)` 的推断拓成 Any。
    hub: Any
    headers: Message
    path: str
    rfile: BufferedIOBase

    # 由组合类提供（实现都在 `hub.server.HubHandler`）——混入只声明类型。
    _auth_ok: Any
    _bytes: Any
    _bytes_file: Any
    _bytes_files: Any
    _job_or_404: Any
    _json: Any
    _serve_path: Any



    # ---- GET /jobs/{id}/payload ----
    def _get_payload(self) -> None:
        """递 payload；**支持 `Range: bytes=N-` 断点续传**（2026-10-12，plan/transfer-jitter-tolerance A）。

        病根：worker 的低速重抽是"丢掉已收、从零重来"（真机每次白扔 256KB + 一次建连）。hub 侧
        给一个 206 之后，重抽的代价趋零 —— 抖动是常态，重试的代价必须接近 0。
        `Accept-Ranges: bytes` 由 `_bytes` 无条件带上（那个头是「本端点支持续传」的**声明**，
        客户端据此决定要不要在重抽时带 `Range`）。计账仍按**实际服务出去的字节**记
        （`record_payload_sent`），续传时它自然小于文件长度 —— 那正是省下来的量。
        """
        jid = self._job_or_404()
        if jid is None:
            return
        p = find_payload(self.hub._job_dir(jid))
        if p is None:
            self._json({"error": "no payload"}, 404)
            return
        # ★ 流式（2026-10-17 L1）：旧实现先 `read_bytes()` 整份、Range 分支再切一份
        # `data[start:end+1]` ⇒ 峰值 = 2× payload。现在两种分支都从盘上按区间读，
        # 峰值 = SEND_CHUNK（与 payload 大小无关）。
        size = p.stat().st_size
        span = parse_range(self.headers.get("Range"), size)
        if span is None:
            # M0 统一计量：传输层实测（服务出去的 payload 字节）——iteration 事件对账用。
            self.hub.record_payload_sent(jid, size)
            self._bytes_file(p)
            return
        start, end = span
        self.hub.record_payload_sent(jid, end - start + 1)
        self._bytes_file(
            p,
            status=206,
            start=start,
            end=end,
            extra_headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
            },
        )


    # ---- GET /jobs/{id}/code ----
    def _get_code(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        self._serve_path(self.hub._job_dir(jid) / "code.zip", missing="no code zip")


    # ---- GET /jobs/{id}/ts_code（M3：TS 运行时 zip，kind=iter 的节点用）----
    def _get_ts_code(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        self._serve_path(self.hub._job_dir(jid) / TS_CODE_NAME, missing="no ts_code zip")


    # ---- GET /jobs/{id}/blob?name=opt|ref（M2 B3：内容寻址 opt/ref 载荷）----
    # ---- 批量形态 ---- `?name=init,ref,opt`（plan/aistudio-transfer-hardening §3.4）----
    # 三个 blob 原本是三次 GET = 三次建连 + 三次 TCP 慢启动。慢链路上「建连」本身就是
    # 一笔税（现场 aistudio：控制面 p95 30s 而 p50 1.1s），所以把三件并成**一次 GET**。
    #
    # 帧格式刻意选「`name\nlen\n` + raw」而**不用 base64**：base64 的 +33% 膨胀会吃掉
    # 省下的字节（这本来就是 minimize-payload 那一刀砍掉的东西）。
    #
    # 兼容：单名（`?name=opt`）逐字走旧分支；**旧 hub** 收到逗号名会走 `blob_path` 的
    # 未知名 400 ⇒ worker 侧自动逐个回退单取（回退链在 `remote/download.download_blobs`）。
    def _get_blob(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        names = [s for s in (str(name).split(",")) if s.strip()]
        if len(names) > 1:
            self._serve_blob_batch(jid, [s.strip() for s in names])
            return
        try:
            bp = blob_path(self.hub._job_dir(jid), name)
        except ProtocolError as e:
            self._json({"error": str(e)}, 400)
            return
        self._serve_path(bp, missing="no blob")

    def _serve_blob_batch(self, jid: str, names: list[str]) -> None:
        """分帧拼出多件 blob 的响应体；**任一环节不可得就整笔 404**（让 worker 去逐个回退）。

        为什么是「整笔失败」而不是「能给的先给」：worker 侧的安全阀要求 `sha` 非空时只认
        内容寻址、缺一件就响亮失败（`_resolve_blob`）。半份响应会让调用方把「部分成功」
        误读成「全部拿到」，那是静默 warm-start 那一类事故的形状。
        """
        # 先定位**全部**件（任一件不可得就整笔失败，语义不变），再一次性流式递出。
        # ★ 旧实现逐件 `read_bytes()` 后 `b"".join(parts)` ⇒ 峰值 ≈ 2× 三件之和（2026-10-17 L1）。
        entries: list[tuple[str, Path]] = []
        for n in names:
            try:
                bp = blob_path(self.hub._job_dir(jid), n)
            except ProtocolError as e:
                self._json({"error": str(e)}, 400)
                return
            if not bp.exists():
                self._json({"error": f"no blob {n}"}, 404)
                return
            entries.append((n, bp))
        self._bytes_files(entries)


    # ---- GET /code（共享 code.zip，colab bootstrap 用） ----
    def _get_shared_code(self) -> None:
        if not self._auth_ok():
            return
        # 2026-10-10（plan/cluster-code-snapshot）：**集群代码快照优先**——会话内所有课程共用
        # 同一份 code.zip（`hub/queue_observe.py::shared_code_zip`），`?course=` 只在快照
        # 缺失/损坏时才影响结果（回落旧行为：那门课的 / 第一份真存在的）。
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        p = self.hub.shared_code_zip((qs.get("course") or [""])[0])
        if p is None:
            # 措辞要点名「快照没建起来」：只写「training loop 尚未启动」会把排障指向错方向
            # （hub 与 trainer 是两个进程，快照由**先起来的那个**建，与有没有课在跑无关）。
            self._json(
                {
                    "error": "no shared code zip — 集群代码快照未建立"
                    "（hub/trainer 重启一次就会建；见 remote/code_snapshot.py），"
                    "且各课 job_root 里也没有 code.zip"
                },
                404,
            )
            return
        self._serve_path(p, missing="shared code zip 在两次调用之间消失了（训练循环刚重启？）")
