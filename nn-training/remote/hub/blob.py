"""remote/hub/blob.py —— 字节服务面（2026-09-24 S4 第十一刀）。

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
from typing import Any

from common.protocol import (
    TS_CODE_NAME,
    ProtocolError,
    blob_path,
    find_payload,
)


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

    # 由组合类提供（实现都在 `remote/hub_server.HubHandler`）——混入只声明类型。
    _auth_ok: Any
    _bytes: Any
    _job_or_404: Any
    _json: Any
    _serve_path: Any



    # ---- GET /jobs/{id}/payload ----
    def _get_payload(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        p = find_payload(self.hub._job_dir(jid))
        if p is None:
            self._json({"error": "no payload"}, 404)
            return
        data = p.read_bytes()
        # M0 统一计量：传输层实测（服务出去的 payload 字节）——iteration 事件对账用。
        self.hub.record_payload_sent(jid, len(data))
        self._bytes(data)


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
    def _get_blob(self) -> None:
        jid = self._job_or_404()
        if jid is None:
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        try:
            bp = blob_path(self.hub._job_dir(jid), name)
        except ProtocolError as e:
            self._json({"error": str(e)}, 400)
            return
        self._serve_path(bp, missing="no blob")


    # ---- GET /code（共享 code.zip，colab bootstrap 用） ----
    def _get_shared_code(self) -> None:
        if not self._auth_ok():
            return
        # 多课程：code.zip 是**每课程一份**（各课的训练循环往自己的 job_root 写）。
        # `?course=` 指定就取那门课的；不指定（旧 colab bootstrap）取第一份真存在的
        # ——代码区份份同源（同一个仓、同一支），取哪门课的都一样。
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        p = self.hub.shared_code_zip((qs.get("course") or [""])[0])
        if p is None:
            self._json({"error": "no shared code zip — training loop 尚未启动"}, 404)
            return
        self._serve_path(p, missing="shared code zip 在两次调用之间消失了（训练循环刚重启？）")
