"""hub/seams.py — hub 包的第三方边界端口（P0-8 单包样板）。

为什么是 Protocol + 默认实例，而不是模块级私有函数：

* 测试注入替身时不再 `monkeypatch.setattr(task_pack, "_net_urlopen", …)`；打的是公开端口，
  实现改名不会再把用例一起改；
* 端口按调用点传入（`net=`），替身只属于当前调用，不共享进程级状态；
* `NET` 只是生产缺省实现，正常调用点不必显式构造。

层号 **L0**：只碰 stdlib 与 `common.net_http`；`hub.task_pack` 站在它上面。
"""

from __future__ import annotations

import urllib.request
from typing import Protocol

from common import net_http


class NetResponse(Protocol):
    """`urlopen` 返回值里本模块真正用到的两项（只读响应体 + 上下文管理）。"""

    def read(self, amt: int | None = None) -> bytes: ...

    def __enter__(self) -> NetResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...


class NetPort(Protocol):
    """hub 出站 HTTP 的唯一端口；替身只需实现 `open`。"""

    def open(
        self, req: urllib.request.Request, timeout: float = 30.0
    ) -> NetResponse: ...


class _NetHTTP:
    def open(
        self, req: urllib.request.Request, timeout: float = 30.0
    ) -> NetResponse:
        resp: NetResponse = net_http.urlopen(req, timeout=timeout)
        return resp


#: 生产缺省端口；测试用 `net=` 传自己的替身，不 patch 这里。
NET: NetPort = _NetHTTP()
