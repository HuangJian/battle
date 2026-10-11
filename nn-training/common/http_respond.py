"""common/http_respond.py —— stdlib 裸 HTTP 服务的**响应形状**（plan/nn-training-reuse-and-deps.plan.md §S2）。

**为什么有它**：`hub/http_face.HubHandler._json` 与 `remote/worker_server` 里那个 Handler 的
`_json` 逐字节相同，`tests/helpers/push_worker` 的假 worker 作为**逐字节镜像**又抄了第三份。
响应形状（头部顺序、字符编码、长度算法）是**跨端线协议**的一部分：三份各自漂一格，就是
「hub 发得出、节点解不开」那类最贵的故障。

`ensure_ascii=False` 是**刻意的**（不是随手取的默认值）：中文错误消息按 UTF-8 原样出线，
与 `Content-Type: application/json` 的隐含编码一致；ASCII 转义会让同一串中文在不同端长得不一样。

⚠ **不为「统一」而收编**（R4 + `docs/nn/reuse-audit.md` §口径差，逐条都是实测）：

* `remote/push_bootstrap._BootHandler._json` —— 同形，但它属于**首个 job 之前**的压缩内联形态
  （notebook 在 `sys.path` 注入 code.zip **之前**就已在跑）⇒ 不得 import `common`；且它的口径
  是 `ensure_ascii=True`（本模块改不动它，也不该改）。
* `e2e/test_{run_rl,volume_e2e}.py` 里的假 `_json` —— 只发 Content-Length、**不发 Content-Type**：
  那是各自用例的最小服务，不是 worker 契约的镜像。
* `hub/http_face._bytes` —— 分片 + 有界 + 停滞日志的**专用**实现（2026-09-20 事故的产物），
  不是「发一块字节」的口径。
* `rfile.read(int(headers.get("Content-Length", "0")))` 读体惯用法 —— 是一行 stdlib 惯用法，
  各调用方的**错误处理与上限策略各不相同**（有的 400、有的 413、有的不设界）⇒ 抽出来只会
  把「谁负责界」从读者眼前移走。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["respond_json"]


def respond_json(handler: Any, obj: Any, status: int = 200) -> None:
    """按 UTF-8 JSON 整体发出：`send_response` → 两个头 → 空行 → body。

    `handler` = `BaseHTTPRequestHandler`，或任何提供 `send_response` / `send_header` /
    `end_headers` / `wfile` 的同形对象（测试替身实现这四件即可，见
    `tests/common/test_http_respond.py`）。
    """
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
