"""BN-free 硬约束守护（plan/python-refactor.md P2-4）。

**背景**：`models/student.py:2-7` 声明 BN-free 是**硬约束**——纯 TS 运行时
`src/nn/infer.ts` 要逐字节复现前向，BatchNorm 的 running stats 依赖训练时数据分布，
无法在导出权重中固化。此前全仓测试对 BatchNorm 零命中：有人在 student 族网络里加
一个 `nn.BatchNorm2d`，不会有任何测试失败，只会在导出后于浏览器里静默跑歪。

**守护范围**：TS 运行时消费的所有模型族（StudentNet / PPOStudent / IntentNet /
GoalNet）必须零 BatchNorm / SyncBatchNorm / Dropout。教师网 `rl_model.py`（ResNet）
只在 Python 侧蒸馏用、不进 TS，允许 BN（RL-Net-Selection 评审时教师有意用 BN 提容量）。

**Dropout 同样禁止**：eval 模式下 Dropout 是恒等，测试期"看似正常"，但 TS 复现时
训练/推理分布不一致——与 BN-free 同一理由，一并禁止。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 允许出现在守护模型中的模块类型白名单——`models.core.NNPolicy` 是 BC 基座
# （也被 TS 消费），一并守护。
from models.core import NNPolicy
from models.goal_net import GoalNet
from models.intent_net import IntentNet
from models.student import PPOStudent, StudentNet

# TS 消费的模型族（新增导出给 TS 的网络必须登记到这里）
TS_CONSUMED_FACTORIES = {
    "StudentNet": lambda: StudentNet(h=8, d=2),
    "PPOStudent": lambda: PPOStudent(h=8, d=2),
    "IntentNet": lambda: IntentNet(h=8, d=2),
    "GoalNet": lambda: GoalNet(h=8, d=2),
    "NNPolicy": lambda: NNPolicy(),
}

_FORBIDDEN = (torch.nn.BatchNorm1d, torch.nn.BatchNorm2d, torch.nn.BatchNorm3d,
              torch.nn.SyncBatchNorm, torch.nn.Dropout, torch.nn.Dropout1d,
              torch.nn.Dropout2d, torch.nn.Dropout3d)


@pytest.mark.parametrize("name,factory", TS_CONSUMED_FACTORIES.items())
def test_no_batchnorm_or_dropout_in_ts_models(name: str, factory) -> None:
    """TS 消费的模型必须零 BatchNorm / SyncBatchNorm / Dropout（P2-4 守护）。"""
    model = factory()
    found = []
    for m in model.modules():
        if isinstance(m, _FORBIDDEN):
            found.append(f"{type(m).__name__}: {m}")
    assert not found, (
        f"{name} 含 TS 无法逐字节复现的层 {found}——"
        f"BN 的 running stats 依赖训练数据分布、Dropout 的训练/推理不一致，"
        f"二者都会让 src/nn/infer.ts 的前向静默跑歪。"
    )
