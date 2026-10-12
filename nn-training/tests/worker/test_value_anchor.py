"""value 门控单测（x26-obs it90 诊断：冷 critic 在新列上 35 倍爆炸）。

钉住三件：
  ① `value_anchor_slices`：新列区间是架构派生（腿 A [128,165)／塔整头／legacy 空），
     不许写 165/189 字面量（与 `policy_input_dim` 同一纪律）；
  ② `apply_value_anchor_pull`：后步乘性收缩 `w *= (1-ρ)`，只碰新列片，
     ρ≤0 或空片 = 逐位不动（对照腿 anchor=0 的数学恒等前提）；
  ③ 监控 rms：返回值 = 新列片 rms（训练日志 `value_new_rms` 的源）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.models.student import PPOStudent
from worker.ppo.engine import apply_value_anchor_pull


def test_slices_leg_a_is_hidden_tail() -> None:
    """腿 A：value 头 165 列里旧 hidden 占前 128，新列 = [128,165)。"""
    m = PPOStudent(policy_extra=True)
    assert m.value_anchor_slices() == [(128, 128 + 37)]
    assert m.policy_input_dim == 165


def test_slices_tower_is_whole_head() -> None:
    """塔：value 整头相对旧 128 全是新的（§2.7：无对应前缀，全重 init）。"""
    m = PPOStudent(policy_extra=True, spatial_tower=True)
    assert m.value_anchor_slices() == [(0, m.policy_input_dim)]
    assert m.policy_input_dim == 112 + 40 + 37


def test_slices_legacy_is_empty() -> None:
    """legacy：value 全是旧 hidden，无新列 ⇒ 空片（ recognizer 不碰）。"""
    assert PPOStudent().value_anchor_slices() == []


def test_pull_shrinks_only_new_slices() -> None:
    """ρ=0.1：新列片精确乘 0.9，旧列逐位不动；rms 返回值 = 片 rms（收缩后）。"""
    torch.manual_seed(0)
    m = PPOStudent(policy_extra=True)
    with torch.no_grad():
        m.value_head.weight.fill_(2.0)
    rms = apply_value_anchor_pull(m, 0.1)
    w = m.value_head.weight
    assert torch.equal(w[:, :128], torch.full((1, 128), 2.0))
    assert torch.equal(w[:, 128:], torch.full((1, 37), 1.8))
    assert rms == float(w[:, 128:].pow(2).mean().sqrt())


def test_pull_zero_reads_but_never_writes() -> None:
    """ρ=0：权重逐位等（对照腿 anchor=0 与无此功能数学恒等，这是 ablation 有效的前提），
    但监控照样读数（无绳腿炸了能发现）；空片模型返回 0.0 且逐位等。"""
    torch.manual_seed(1)
    m = PPOStudent(policy_extra=True)
    before = m.value_head.weight.clone()
    assert apply_value_anchor_pull(m, 0.0) == float(before[:, 128:].pow(2).mean().sqrt())
    assert torch.equal(m.value_head.weight, before)
    legacy = PPOStudent()
    before_legacy = legacy.value_head.weight.clone()
    assert apply_value_anchor_pull(legacy, 1.0) == 0.0
    assert torch.equal(legacy.value_head.weight, before_legacy)


def test_pull_is_deterministic() -> None:
    """同 seed 两次：收缩结果逐位等（可复现；扩展 §8 确定性）。"""
    outs = []
    for _ in range(2):
        torch.manual_seed(2)
        m = PPOStudent(policy_extra=True)
        apply_value_anchor_pull(m, 0.001)
        outs.append(m.value_head.weight.clone())
    assert torch.equal(outs[0], outs[1])
