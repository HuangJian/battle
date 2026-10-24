"""hub/offline_push_workers.py — 离线 push 的登记表 + **协议版本门**（2026-10-24 拆出）。

为什么单独一块：`hub/offline_push.py` 顶到 LOC 预算（1000 行，`tests/test_python_loc_budget.py`）。
本类是**独立所有者**（「哪台机器能接离线单」）且触发条件独立（每拍探活/探协议），与调度拍、
pump 线程没有共享状态 —— 除了基类给的 `snapshot()`/`note_fields()`。

在线/忙闲/热重载/退避全部复用基类（第三份登记表会立刻漏掉一个修复）；这里只加一件基类不该
知道的事：`/state` 的 `proto`。GPU 的 `/ping` 体里没有这个字段，所以版本门不能挂在探活上
（评审 P1-7）——它要问那个**只有新 worker 才有的端点**。

探测结果记在每个条目的 `proto_ok` 上（`None` = 还没问过）：派发只看 `True`，不知道就不推
（与「从没答过 = 不在线」同一条保守方向）。
"""

from __future__ import annotations

import time
from typing import Any

from common.logutil import tag_logger
from common.protocol import CAP_OFFLINE_PUSH, OFFLINE_PUSH_PROTO, OFFLINE_PUSH_STATE_PATH
from remote import offline_push_client as client
from remote.push_dispatch import PushWorkers

#: proto 探测**失败**后的重试间隔（秒）：旧 worker 升级代码后不必等 hub 重启（评审 F11）。
PROTO_RETRY_SEC = 60.0

_log_default = tag_logger("hub-offline-push", lambda: time)


class OfflinePushWorkers(PushWorkers):
    """离线 push 的登记表（`capability="offline_push"`）+ 协议版本门（模块头有 why）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("capability", CAP_OFFLINE_PUSH)
        super().__init__(*args, **kwargs)

    def probe_proto(self, *, timeout: float = 5.0) -> dict[str, bool]:
        """对在线且还没结论的条目问一次 `/state`；返回 `id -> 兼容`。

        **失败也会重试**（评审 F11）：`proto_ok=False` 只挡 `PROTO_RETRY_SEC`，不是终审 ——
        云机上换个 notebook 代码就跑起来了，没有理由非得重启 hub 才认它。
        """
        out: dict[str, bool] = {}
        now = self._now()
        for w in self.snapshot():
            wid = str(w.get("id") or "")
            if not wid or not w.get("online"):
                continue
            ok = w.get("proto_ok")
            if ok is True:
                continue
            at = float(w.get("proto_at") or 0.0)
            if ok is False and at and now - at < PROTO_RETRY_SEC:
                continue
            res = client.call_json(
                str(w.get("url") or ""),
                str(w.get("key") or ""),
                OFFLINE_PUSH_STATE_PATH,
                timeout=timeout,
            )
            if res is None:
                continue  # 这一次没答：探活已经记了 miss，别把「没答」判成「协议不符」
            status, doc = res
            got = str((doc if isinstance(doc, dict) else {}).get("proto") or "")
            # ★ 有答且不是「200 + 版本键相符」才算不兼容：旧 worker 的 404 也是一种**有答**的
            # 「没有这个端点」（评审 P1-7 的版本门），而连不上不该在这里落结论。
            ok = status == 200 and got == OFFLINE_PUSH_PROTO
            self.note_fields(wid, proto_ok=bool(ok), proto=got, proto_at=now)
            out[wid] = bool(ok)
            if not ok:
                self._log(
                    f"WARN worker {wid} 不支持离线 push（/offline/push/state 的 proto={got!r}）"
                    f"——升级它的 notebook 代码后再启用；本拍不向它派单"
                )
        return out

    def eligible(self) -> list[dict]:
        """可接单的 worker：在线、不忙、`proto_ok=True`。"""
        return [
            w
            for w in self.snapshot()
            if bool(w.get("online")) and not bool(w.get("busy")) and w.get("proto_ok") is True
        ]


__all__ = ["PROTO_RETRY_SEC", "OfflinePushWorkers"]
