"""remote/hub/queue_auth.py — 鉴权面与 `halt_workers` 旧名的委派。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。它是**覆写簇**：四个方法覆写 `_AuthGuard`
的同名实现，就为了加一层「单课程借 store」的岔路。

为什么这四个必须委派而不能用自己那份：鉴权面是**进程级一份**，而单课程 hub 的计数/封禁
历史就住在那一份 `_JobStore` 里——既有用例会在 store 上预热 5 次失败再断言下一个请求拿到
403，也会在 HTTP 请求后断言 `store._auth_fail` 被更新。各存一份副本会让这两类断言全部反，
且是对生产行为的真实偏离（两个计数器算 5 次阈值）。

`halt_workers` 是 `all_halted()` 的**旧名**（既有测试/调用方直接读写它，单课程时语义是
「那一份 store 的旗标」）。名字是契约：属性对原样保留，两个方向都转发到 `set_halt`。

## 依赖方向

`queue_auth → {remote.hub.auth, remote.hub.store}`（向下）。它调 `self.all_halted()` /
`self.set_halt()`（`queue_scope`）与 `self._solo`（组合类的状态）——**都经 `self`**。
"""

from __future__ import annotations

from remote.hub.auth import _AuthGuard
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore


class QueueAuthMixin(_AuthGuard, QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _solo: _JobStore | None

    # ---- 进程级状态（单课程借 store，多课程用自己那份） ----
    #: 进程级读写（`all_halted()` 的旧名）：既有测试/调用方直接读写它。
    @property
    def halt_workers(self) -> bool:
        return self.all_halted()

    @halt_workers.setter
    def halt_workers(self, v: bool) -> None:
        self.set_halt(bool(v))

    # ---- 鉴权面：同样「单课程借 store」。
    # 为什么这四个必须委派而不能用自己那份：鉴权面是**进程级一份**，而单课程 hub 的
    # 计数/封禁历史就住在那一份 `_JobStore` 里——既有用例会在 store 上预热 5 次失败
    # 再断言下一个请求拿到 403，也会在 HTTP 请求后断言 `store._auth_fail` 被更新。
    # 各存一份副本会让这两类断言全部反，且是对生产行为的真实偏离（两个计数器）。
    def auth_failure(self, ip: str) -> int:
        if self._solo is not None:
            return self._solo.auth_failure(ip)
        return _AuthGuard.auth_failure(self, ip)

    def auth_success(self, ip: str) -> None:
        if self._solo is not None:
            self._solo.auth_success(ip)
            return
        _AuthGuard.auth_success(self, ip)

    def is_blocked(self, ip: str) -> bool:
        if self._solo is not None:
            return self._solo.is_blocked(ip)
        return _AuthGuard.is_blocked(self, ip)

    def blocked_remaining(self, ip: str) -> float:
        if self._solo is not None:
            return self._solo.blocked_remaining(ip)
        return _AuthGuard.blocked_remaining(self, ip)

