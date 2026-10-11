"""_common.py —— 模型族的共用小面（plan/nn-training-reuse-and-deps.plan.md §S3）。

**为什么有它**：`param_count` 曾逐字节存在两份（`models/core.py` / `models/student.py`）。
它是**公开名**——`worker/train/bc.py`、`worker/scripts/init_spatial_leg.py`、测试都直接
`from worker.models.core import param_count` / `from worker.models.student import param_count`
⇒ 收敛方式是**一份实现 + 两个转发名**（名字是契约，位置不是；调用点一行不改）。

`forward` 等**有意差异**不在此列：六个模型各自的网络形状是合法差异（R4 的例外成立）。
"""

from __future__ import annotations

import torch.nn as nn


def param_count(model: nn.Module) -> int:
    """可训练参数总数（`int` 逐项累加 ⇒ 不把 tensor 类型泄给调用方的账本字段）。"""
    return sum(int(p.numel()) for p in model.parameters())
