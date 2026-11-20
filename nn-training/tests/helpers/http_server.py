"""测试用 HTTP 服务的快速轮询线程（`python.refactor.md` §P0-8 第五项）。

`socketserver.BaseServer.serve_forever()` 的 `poll_interval` 缺省 **0.5s**，而
`shutdown()` 会无条件等 `__is_shut_down` ⇒ 关服最坏要等一整个轮询周期。仓库里几十处
`threading.Thread(target=srv.serve_forever, daemon=True)` 因此每条用例白付 0~0.5s
（实测 `test_hub_push_dispatch::test_push_workers_admin_endpoint` 的 1.09s 里 1.005s
全在两次 `shutdown()`）。

此前靠根 `conftest.py` **全局改写标准库** `BaseServer.serve_forever` 的缺省值来省这笔钱
—— 那让「关服慢」这类生产问题在测试里永远不可复现。现在改成显式使用本模块：

    self.thread = serve_fast(srv)   # 只构造，不启动
    self.thread.start()

返回的线程**未启动**（保持旧的 `Thread(...)` + `.start()` 形状不变），只把轮询周期
钉到 `FAST_POLL_SEC`。没有用例依赖 0.5s 的粒度：判据全挂在请求/事件上。
"""

from __future__ import annotations

import socketserver
import threading
from typing import TypeVar

_SERVER = TypeVar("_SERVER", bound=socketserver.BaseServer)

#: 服务循环多久醒一次。与旧 conftest 补丁同值（10ms）：只影响关服延迟。
FAST_POLL_SEC = 0.01


def serve_fast(server: _SERVER) -> threading.Thread:
    """返回一个以 10ms 轮询 `serve_forever` 的 daemon 线程（**未启动**）。"""
    port = getattr(server, "server_port", "?")
    return threading.Thread(
        target=server.serve_forever,
        args=(FAST_POLL_SEC,),
        daemon=True,
        name=f"test-http-{port}",
    )
