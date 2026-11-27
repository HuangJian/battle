"""common/ports.py — 出站线路端口（P0-8 第二代，2026-11-28）。

`hub/seams.py` 收的是**第三方边界**（`urlopen`）；本文件收第二类私有名：**包内传输原语**
（`remote/http._request` / `remote/wire._wire_add` / `remote/hub_http._request`）。它们在
`job_lifecycle` / `bc_job` / `hub_http` / `push_client` 里被几十处用例
`monkeypatch.setattr(…, "_request", …)` —— patch 的前提是「知道实现叫这个名字」，于是实现
改名就得改用例，重构只能退化成搬文件。

形状是两项能力，不是一份实现：

* `request(...)` —— 一次 HTTP 往返；契约由各宿主自己的实现决定（返回值恒为 `(status, body)`）；
* `add(jid, seg, nbytes, sec)` —— 一段成功传输的账（契约 = `remote.wire._wire_add`）。

`PortPair` 只是**组装壳**：宿主模块用自己已经 import 的原语拼一个缺省端口
（`WIRE = PortPair(request=_request, add=_wire_add)`）⇒ 本模块**零仓内 import**，
不改变任何模块的拓扑秩，也不新增函数内 import。

## 为什么住 `common/` 而不是 `remote/seams.py`

两个宿主分属不同层：`job_lifecycle` 是 L3，而 `hub_http` 是 **L0**（stdlib + `common.protocol`）。
端口若住 `remote/`，L0 的 `hub_http` import 它就会撞「remote 内部边必须严格向下」的层账本
（同层不算向下，实测报红）。住 `common/`（L0 外部层）则两侧都成立：`common` 由上向下被依赖，
且它本身仍满足「只依赖 stdlib」。

## 测试怎么用

* 跨调用替换整个出口：`monkeypatch.setattr(JL, "WIRE", PortPair(fake_request))`；
* 只想换一半：`PortPair(request=fake)`（`add` 缺省是 `_noop`，不会把账记到别处）；
* 需要真实记账的用例：`PortPair(fake, add=JL.WIRE.add)` —— 只换发送面。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


def _noop(jid: str, seg: str, nbytes: int, sec: float) -> None:
    """不记账的 `add`（`bc_job` 这类只发请求、没有 job 行要写的宿主用它）。"""


class WirePort(Protocol):
    """出站线路的唯一端口。

    `request` 的类型留成 `Callable[..., tuple[int, bytes]]`：各宿主的请求形状不同
    （`WireRequest` 一个 spec / hub 控制面的 7 个位置参数），把签名钉死反而会逼出一个
    第二套协议。真正的参数契约由各宿主的实现与调用点保证；返回值 `(status, body)`
    在这里钉死（替身最容易写错的就是它）。

    ⚠ 这里写 `def`（只读方法）而不是属性注解：`PortPair` 是 frozen dataclass，字段是**只读
    属性**，而 Protocol 里的 `x: T` 语义是「可写属性」⇒ mypy 报 `read-only attribute`（实测）。
    方法声明是可写属性的超集，两侧都成立。
    """

    def request(self, *args: object, **kwargs: object) -> tuple[int, bytes]: ...

    def add(self, jid: str, seg: str, nbytes: int, sec: float) -> None: ...


@dataclass(frozen=True)
class PortPair:
    """把「发一发」与「记账」绑成一个显式对象（字段即能力，不做转发方法）。"""

    request: Callable[..., tuple[int, bytes]]
    add: Callable[..., None] = _noop


__all__ = ["PortPair", "WirePort"]
