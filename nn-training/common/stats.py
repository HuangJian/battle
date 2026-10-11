"""common/stats.py —— 观测字段的**数值化口径**（plan/nn-training-reuse-and-deps.plan.md §S2）。

**为什么有它**：`worker/loop_guards_trip._num_or_none` 与 `worker/train_ledger._num` 逐字节相同。
两处都是**观测**字段的入口（熔断读数 / 账本读数），口径必须唯一 —— 观测口径漂了，分位与均值
就会跟着漂，而那种漂移在账本里是**静默**的。

`bool` 必须剔除：它是 `int` 的子类，放过去会让 `"capped": true` 这样的字段变成 `1.0`
悄悄混进统计（本仓踩过「布尔当数值」的同类问题）。

⚠ **刻意不收编**的两族（R4：有意的行为差异不得为「统一」而抹掉；详见 `docs/nn/reuse-audit.md` §4）：

* `worker/gate_inputs._num` —— **接受 bool**（`float(v) if isinstance(v, (int, float)) else None`）。
  这是「保留语义」还是「漏判」属**待判**，本模块不替它决定；
* `tools/course_compare._num` / `tools/wire_report._num` —— 签名与语义不同（行/列查找、四舍五入）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["num_or_none"]


def num_or_none(v: Any) -> float | None:
    """数值化（**bool / 非数值 → None**）—— 只用于观测字段落账，绝不参与判定。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)
