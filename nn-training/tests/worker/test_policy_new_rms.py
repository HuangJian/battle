"""policy 接线遥测单测（x27 冻结判决 §2026-10-13-goalnn-x27-frozen：行为由 policy 解释）。

钉住三件：
  ① `policy_new_rms_of`：move/fire 双头在 `value_anchor_slices()` 列片上的 rms
     （三头吃同一份 pf，新列是输入属性不是 value 属性；不许写 165/189 字面量）；
  ② 纯只读：永不写权重（policy 同拴已在 §2026-10-12-goalnn-value-gating 否决，
     本函数无 ρ 参数，两次调用逐位等）；
  ③ 缺片/缺头 → 0.0（legacy／旧 worker 恒 0——精确零连击 = 旧 worker 味，
     另有精确零告警欠账）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import POLICY_EXTRA_DIM, SCALAR_DIM
from worker.models.student import SPATIAL_TOWER_FC_OUT, PPOStudent
from worker.ppo.engine import policy_new_rms_of


def test_leg_a_covers_both_heads() -> None:
    """腿 A：返回值 = move+fire 双头新列片联合 rms（列片与 value 同一片 [128,165)）。"""
    torch.manual_seed(0)
    m = PPOStudent(policy_extra=True)
    with torch.no_grad():
        m.move_head.weight.fill_(2.0)
        m.fire_head.weight.fill_(4.0)
    got = policy_new_rms_of(m)
    want = float(
        torch.cat(
            [m.move_head.weight[:, 128:].reshape(-1), m.fire_head.weight[:, 128:].reshape(-1)]
        )
        .pow(2)
        .mean()
        .sqrt()
    )
    assert got == want
    # move 5×POLICY_EXTRA_DIM 个 2.0、fire 2×POLICY_EXTRA_DIM 个 4.0：pin 住量级（旧列不参与）。
    assert abs(got - 2.7256) < 1e-4


def test_read_only_never_writes() -> None:
    """纯只读：调用前后双头权重逐位等；两次调用返回值相等（无 RNG、无状态）。"""
    torch.manual_seed(1)
    m = PPOStudent(policy_extra=True)
    before_move = m.move_head.weight.clone()
    before_fire = m.fire_head.weight.clone()
    r1 = policy_new_rms_of(m)
    assert torch.equal(m.move_head.weight, before_move)
    assert torch.equal(m.fire_head.weight, before_fire)
    assert policy_new_rms_of(m) == r1


def test_legacy_and_missing_heads_return_zero() -> None:
    """legacy（空片）→ 0.0；缺头（无 move/fire）→ 0.0；权重逐位不动。"""
    legacy = PPOStudent()
    before = legacy.move_head.weight.clone()
    assert policy_new_rms_of(legacy) == 0.0
    assert torch.equal(legacy.move_head.weight, before)
    naked = SimpleNamespace(value_anchor_slices=lambda: [(0, 8)])
    assert policy_new_rms_of(naked) == 0.0


def test_tower_covers_whole_head() -> None:
    """塔：整头相对旧 128 全是新的 ⇒ 双头全宽进 rms（与 value 同一片语义）。"""
    torch.manual_seed(2)
    m = PPOStudent(policy_extra=True, spatial_tower=True)
    dim = m.policy_input_dim
    assert dim == SPATIAL_TOWER_FC_OUT + SCALAR_DIM + POLICY_EXTRA_DIM
    with torch.no_grad():
        m.move_head.weight.fill_(1.0)
        m.fire_head.weight.fill_(1.0)
    assert policy_new_rms_of(m) == 1.0
