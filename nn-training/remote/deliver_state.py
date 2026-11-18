"""remote/deliver_state.py —— 补传腿的**执行面只读快照**（2026-11-18，G5 接缝）。

为什么单独成文件：`remote/offline_deliver.py` 是 L2 的腿本体，LOC 预算 1000 行
（`tests/test_python_loc_budget.py`）。这份读数是**独立所有者**（记账水位 + 统计开关 + 档位机）
+ **独立触发条件**（送达之后才有人问「现在到哪了」），与「探活 / 投递 / 降级」那条链没有共享
写者 —— 口径同 `deliver_ledger` / `deliver_parts`：腿本体只留「怎么发」。

**为什么是 mixin 而不是函数库**：它读的全是交付器自己的字段（`_delivered` / `_metrics_sent` /
`_metrics_stat` / `_metrics_unsupported` / `_modes`）。字段仍由 `OfflineDeliverer.__init__`
持有（单写者纪律不变），本模块只提供**读**面。

依赖面：`remote.deliver_mode`(L0) 只用于类型 ⇒ 本模块 **L1**。
"""

from __future__ import annotations

from dataclasses import dataclass

from remote.deliver_mode import ModeTracker


@dataclass(frozen=True)
class DeliverState:
    """补传腿的只读执行面：已送达轮次、统计件销账与档位（判据本体不在这里）。"""

    delivered: tuple[int, ...]
    metrics_sent: tuple[int, ...]
    metrics_stat: tuple[int, int]
    metrics_unsupported: bool
    mode: str


class DeliverStateMixin:
    """宿主（`OfflineDeliverer`）提供的面：记账水位 + 统计批开关 + 档位机。"""

    _delivered: set[int]
    _metrics_sent: set[int]
    _metrics_stat: tuple
    _metrics_unsupported: bool
    _modes: ModeTracker

    def deliver_state(self) -> DeliverState:
        """这一腿的公开读数（用例 / 诊断共用；不改变任何发送与销账语义）。"""
        return DeliverState(
            delivered=tuple(sorted(self._delivered)),
            metrics_sent=tuple(sorted(self._metrics_sent)),
            metrics_stat=(int(self._metrics_stat[0]), int(self._metrics_stat[1])),
            metrics_unsupported=bool(self._metrics_unsupported),
            mode=str(self._modes.mode),
        )
