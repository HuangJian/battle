"""remote/seams.py — 出站线路端口（P0-8 第二代样板，2026-11-28）。

`hub/seams.py` 收的是**第三方边界**（`urlopen`）；本文件收第二类私有名：**包内传输原语**
（`remote/http._request` / `remote/wire._wire_add`）。它们在 `job_lifecycle` / `bc_job` 里
被十几处用例 `monkeypatch.setattr(…, "_request", …)` —— patch 的前提是「知道实现叫这个名字」，
于是实现改名就得改用例，重构只能退化成搬文件。

形状是两项能力，不是一份实现：

* `request(spec)` —— 一次 HTTP 往返，契约 = `remote.http._request`（`(status, body)`）；
* `add(jid, seg, nbytes, sec)` —— 一段成功传输的账，契约 = `remote.wire._wire_add`。

`PortPair` 只是**组装壳**：宿主模块用自己已经 import 的两个原语拼一个缺省端口
（`WIRE = PortPair(request=_request, add=_wire_add)`）⇒ 本模块**零仓内 import**（L0），
不改变任何模块的拓扑秩，也不新增函数内 import。

为什么不做成一个自带缺省实现的单例（像 `hub/seams.py::NET`）：`http._request` 在 L2、
`wire._wire_add` 在 L1，而这个端口要住在 `job_lifecycle`(L3) **下面**才不抬高任何读者；
自带实现就得 import 它们，秩立刻变 3 ⇒ 与读者同秩（层账本要求严格小于）。

测试怎么用：

* 跨调用替换整个出口：`monkeypatch.setattr(JL, "WIRE", PortPair(fake_request))`；
* 只想换一半：`PortPair(request=fake)`（`add` 缺省是 `_noop`，不会把账记到别处）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


def _noop(jid: str, seg: str, nbytes: int, sec: float) -> None:
    """不记账的 `add`（`bc_job` 这类只发请求、没有 job 行要写的宿主用它）。"""


class WirePort(Protocol):
    """出站线路的唯一端口。

    `request` / `add` 的类型都留成 `Callable[..., X]`：`WireRequest` 住 L2 的 `remote.http`，
    本模块若在运行期 import 它就会抬到 L3、与读者同秩。真正的参数契约由 `remote.http._request`
    的实现与各宿主的调用点保证；返回值 `(status, body)` 在这里钉死（替身最容易写错的就是它）。

    ⚠ 这里写 `def`（只读方法）而不是属性注解：`PortPair` 是 frozen dataclass，字段是**只读属性**，
    而 Protocol 里的 `x: T` 语义是「可写属性」⇒ mypy 报 `read-only attribute`（实测）。
    方法声明是可写属性的超集，两侧都成立。
    """

    def request(self, spec: object) -> tuple[int, bytes]: ...

    def add(self, jid: str, seg: str, nbytes: int, sec: float) -> None: ...


@dataclass(frozen=True)
class PortPair:
    """把「发一发」与「记账」绑成一个显式对象（字段即能力，不做转发方法）。"""

    request: Callable[..., tuple[int, bytes]]
    add: Callable[..., None] = _noop


__all__ = ["PortPair", "WirePort"]
