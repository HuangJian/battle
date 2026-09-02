"""BC 掩码损失回归（plan/python-refactor.md P1-8）。

旧实现 `_masked_ce`/`_masked_acc` 是恒等式：`(per*m)/m.clamp(min=1)` 在 m≥1 时
= per——mask 100% 无效，非法类 logits 照样进 softmax 分母（训练目标与 TS 推理
argmax(z+mask) 不一致），"masked accuracy" 实为普通 accuracy（指标名误导）。

P1-8 修复：非法类 logit 置 -1e9（softmax 分母只含合法类）；合法类数 <2 的样本
（如 fire 冷却期 [release,1]）无决策信息、跳过。

本文件用解析例子断言修复后的行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from train.bc import _masked_acc, _masked_ce


def test_masked_ce_ignores_illegal_class() -> None:
    """非法类 logit 不参与 softmax 分母——只对合法类归一。"""
    # move 头：mask 禁 up(1)/down(2) → 只剩 none/left/right
    logits = torch.tensor([[10.0, 100.0, 100.0, 0.0, 0.0]])  # up/down 极高（非法）
    target = torch.tensor([0])  # none（合法）
    mask = torch.tensor([[1, 0, 0, 1, 1]])
    # 修复前：CE over 全部 5 类（up/down 拉低 softmax → loss 大）
    # 修复后：分母只含合法 3 类 → loss = -log(exp(10)/Σ合法) ≈ 0（none 是最高合法）
    loss = _masked_ce(logits, target, mask)
    assert loss.item() < 1e-3, f"非法类不应参与分母，loss 应≈0，实际 {loss.item()}"


def test_masked_ce_skips_single_valid_class() -> None:
    """合法类数 <2（fire 冷却期 [release,1]）：无决策信息，贡献 0。"""
    logits = torch.tensor([[5.0, 100.0]])  # hold 非法
    target = torch.tensor([0])  # release
    mask = torch.tensor([[1, 0]])  # 只剩 release → 单合法类
    loss = _masked_ce(logits, target, mask)
    assert loss.item() == 0.0, "单合法类应跳过（loss 0）"


def test_masked_ce_normal_ce_when_all_valid() -> None:
    """全合法时退化为普通 CE（与 F.cross_entropy 一致）。"""
    logits = torch.tensor([[1.0, 2.0, 3.0, 0.0, 0.0]])
    target = torch.tensor([2])
    mask = torch.ones(1, 5, dtype=torch.int64)
    loss = _masked_ce(logits, target, mask)
    ref = torch.nn.functional.cross_entropy(logits, target)
    assert abs(loss.item() - ref.item()) < 1e-6


def test_masked_acc_uses_masked_argmax() -> None:
    """accuracy 用掩码后 argmax（与 TS 推理同语义）；非法类预测不计。"""
    logits = torch.tensor([[0.0, 100.0, 0.0, 0.0, 0.0]])  # up 极高但非法
    target = torch.tensor([0])
    mask = torch.tensor([[1, 0, 0, 1, 1]])  # 禁 up
    acc = _masked_acc(logits, target, mask)
    assert acc == 1.0, f"非法类 up 不应参与 argmax，应预测 none → acc=1，实际 {acc}"
