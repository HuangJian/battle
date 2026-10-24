"""kickstart ref 前向缓存回归（2026-09-10，ppo/engine.py::ppo_update）。

背景：ref_model 冻结且 StudentNet BN-free ⇒ 无状态；ref 输出只依赖
(obs, scalars, mask)，而三者逐 chunk 固定、**跨 epoch 不变**（epoch 只重排 chunk
顺序）。旧实现在最内层每梯度步重算一次完整前向 = epochs× 白烧（本机 CPU 实测占
单步 ~44%，tools/tpu-probe.py 的 D1-D2 差）。

本测试守护三件事：
  1. ref 前向次数 == chunk 数（不是 chunk 数 x epochs）——缓存确实生效；
  2. 同种子两次运行聚合指标逐位相同——优化没引入不确定性；
  3. ref_model 缺席时 kickstart 恒 0 且不炸——旧护栏语义不变。

★ 2026-10-23 追加（v6）：ref 缓存段曾写死 `_rm, _rf, _ = ref_model(...)` 与 legacy
掩码切片（`MOVE_DIM`/`FIRE_DIM`），B1 v6 落地时只迁移了主循环。v6 起点 +
`kickstart_ref: true` 的腿（y32-nohp / y32-tempo）在 grad 段连炸
`ValueError: too many values to unpack (expected 3)`。下面两条 v6 用例钉住：
ref 不得再崩、hold 头必须进 kickstart KL、ref 与语料不同代必须响亮拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch

from common.schema import (
    FIRE3_DIM,
    HOLD_DIM,
    MASK_V6_DIM,
    MOVE4_DIM,
    OBS_CHANNELS,
    SCALAR_DIM,
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.models.student import PPOStudent
from worker.ppo.engine import ppo_update

AGG_KEYS = {"policy", "value", "entropy", "kl", "kickstart", "mean_ret", "gnorm"}


class _CountingRef(torch.nn.Module):
    """记录 forward 调用次数的 ref 包装（不改变数值）。"""

    def __init__(self, inner: torch.nn.Module) -> None:
        super().__init__()
        self.inner = inner
        self.calls = 0

    def forward(self, obs, scalars, extra=None):
        self.calls += 1
        return self.inner(obs, scalars, extra)


def _chunks(n_chunks: int = 3, b: int = 8, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_chunks):
        out.append(
            {
                "obs": rng.integers(0, 256, (b, OBS_CHANNELS, 26, 26), dtype=np.uint8),
                "scalars": rng.standard_normal((b, SCALAR_DIM)).astype(np.float32),
                "a_move": rng.integers(0, 5, (b,), dtype=np.int64),
                "a_fire": rng.integers(0, 2, (b,), dtype=np.int64),
                "lp_move": rng.standard_normal(b).astype(np.float32),
                "lp_fire": rng.standard_normal(b).astype(np.float32),
                "adv": rng.standard_normal(b).astype(np.float32),
                "ret": rng.standard_normal(b).astype(np.float32),
                "mask": np.ones((b, 7), dtype=np.float32),
            }
        )
    return out


def _fresh(seed: int = 1234, *, v6: bool = False):
    torch.manual_seed(seed)
    model = PPOStudent(h=8, d=1, action_v6=v6)
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    return model, opt


def _v6_chunks(n_chunks: int = 3, b: int = 8, seed: int = 0) -> list[dict]:
    """v6 三头动作空间的 chunk：mask 9 宽 + `a_hold`/`lp_hold`（判据字段）。"""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_chunks):
        out.append(
            {
                "obs": rng.integers(0, 256, (b, OBS_CHANNELS, 26, 26), dtype=np.uint8),
                "scalars": rng.standard_normal((b, SCALAR_DIM)).astype(np.float32),
                "a_move": rng.integers(0, MOVE4_DIM, (b,), dtype=np.int64),
                "a_fire": rng.integers(0, FIRE3_DIM, (b,), dtype=np.int64),
                "a_hold": rng.integers(0, HOLD_DIM, (b,), dtype=np.int64),
                "lp_move": rng.standard_normal(b).astype(np.float32),
                "lp_fire": rng.standard_normal(b).astype(np.float32),
                "lp_hold": rng.standard_normal(b).astype(np.float32),
                "adv": rng.standard_normal(b).astype(np.float32),
                "ret": rng.standard_normal(b).astype(np.float32),
                "mask": np.ones((b, MASK_V6_DIM), dtype=np.float32),
            }
        )
    return out


def _run(
    epochs: int,
    *,
    ref=None,
    kickstart_kl: float = 1.0,
    seed: int = 7,
    v6: bool = False,
):
    np.random.seed(seed)
    model, opt = _fresh(v6=v6)
    return ppo_update(
        model,
        opt,
        _v6_chunks() if v6 else _chunks(),
        epochs,
        torch.device("cpu"),
        kl_coef=0.0,
        ref_model=ref,
        kickstart_kl=kickstart_kl,
    )


def test_ref_forward_called_once_per_chunk() -> None:
    """epochs=4、3 个 chunk -> ref 前向恰好 3 次（旧行为是 12 次）。"""
    ref = _CountingRef(PPOStudent(h=8, d=1).eval())
    for p in ref.parameters():
        p.requires_grad_(False)
    _run(epochs=4, ref=ref)
    assert ref.calls == 3, f"ref forward 调用 {ref.calls} 次，期望 3（= chunk 数）"


def test_determinism_with_cached_ref() -> None:
    """同种子两次运行聚合指标完全一致（缓存不引入不确定性）。"""
    # 两个 ref 必须同种子——否则比的是两份不同权重的 ref，不是缓存的不确定性。
    torch.manual_seed(99)
    ref_a = PPOStudent(h=8, d=1).eval()
    torch.manual_seed(99)
    ref_b = PPOStudent(h=8, d=1).eval()
    a = _run(epochs=2, ref=ref_a)
    b = _run(epochs=2, ref=ref_b)
    assert set(a) >= AGG_KEYS, f"聚合缺键：{AGG_KEYS - set(a)}"
    for k in sorted(AGG_KEYS):
        assert a[k] == b[k], f"{k}: {a[k]!r} != {b[k]!r}（缓存路径不确定）"


def test_kickstart_zero_without_ref() -> None:
    """kickstart_kl>0 但无 ref_model -> kickstart 恒 0、不抛异常（旧护栏语义）。"""
    agg = _run(epochs=1, ref=None, kickstart_kl=1.0)
    assert agg["kickstart"] == 0.0


def test_kickstart_active_with_ref() -> None:
    """有 ref 时 kickstart 必须是非零有限值（否则说明 KL 项根本没进 loss）。"""
    ref = PPOStudent(h=8, d=1).eval()
    for p in ref.parameters():
        p.requires_grad_(False)
    agg = _run(epochs=1, ref=ref, kickstart_kl=1.0)
    assert np.isfinite(agg["kickstart"])
    assert agg["kickstart"] > 0.0, f"kickstart={agg['kickstart']}，KL 项疑似未生效"


# ---------------- v6（B1 三头）----------------


def test_v6_ref_forward_is_4_tuple_and_survives() -> None:
    """v6 ref 返回 4 头，缓存段不得再按 3 元解包（现场：y32 两腿 grad 段连炸）。"""
    ref = _CountingRef(PPOStudent(h=8, d=1, action_v6=True).eval())
    for p in ref.parameters():
        p.requires_grad_(False)
    agg = _run(epochs=2, ref=ref, v6=True)
    assert ref.calls == 3, f"ref forward 调用 {ref.calls} 次，期望 3（= chunk 数）"
    assert np.isfinite(agg["kickstart"])
    assert agg["kickstart"] > 0.0, f"kickstart={agg['kickstart']}，v6 KL 项未生效"


def test_v6_kickstart_anchors_the_hold_head() -> None:
    """hold 头必须进 kickstart KL：只改 ref 的 hold 头 ⇒ 读数必须变。

    旧实现只算 move+fire（`ref_move, ref_fire = _ref`），hold 是 v6 唯一新增的
    自由度，漏锚等于 kickstart 对它完全不生效。漏锚时下面两次读数**逐位相同**。
    """
    torch.manual_seed(11)
    ref_a = PPOStudent(h=8, d=1, action_v6=True).eval()
    torch.manual_seed(11)
    ref_b = PPOStudent(h=8, d=1, action_v6=True).eval()
    with torch.no_grad():
        assert ref_b.hold_head is not None
        ref_b.hold_head.bias.add_(3.0)  # 只有 hold 头不同（move/fire 逐位相同）
    for r in (ref_a, ref_b):
        for p in r.parameters():
            p.requires_grad_(False)
    ka = _run(epochs=1, ref=ref_a, v6=True)["kickstart"]
    kb = _run(epochs=1, ref=ref_b, v6=True)["kickstart"]
    assert kb != ka, "kickstart 对 hold 头不敏感 ⇒ hold KL 未进 loss"


def test_v6_ref_with_legacy_data_is_rejected_loudly() -> None:
    """ref 与语料不同代 ⇒ 响亮拒绝（不能靠掩码切片静默错位）。"""
    ref = PPOStudent(h=8, d=1, action_v6=True).eval()
    for p in ref.parameters():
        p.requires_grad_(False)
    with pytest.raises(ValueError, match="不同源"):
        _run(epochs=1, ref=ref, v6=False)


def test_legacy_ref_with_v6_data_is_rejected_loudly() -> None:
    """反方向同一条判据（legacy ref + v6 语料）。"""
    ref = PPOStudent(h=8, d=1).eval()
    for p in ref.parameters():
        p.requires_grad_(False)
    with pytest.raises(ValueError, match="不同源"):
        _run(epochs=1, ref=ref, v6=True)
