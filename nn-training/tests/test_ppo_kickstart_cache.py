"""kickstart ref 前向缓存回归（2026-09-10，ppo/engine.py::ppo_update）。

背景：ref_model 冻结且 StudentNet BN-free ⇒ 无状态；ref 输出只依赖
(obs, scalars, mask)，而三者逐 chunk 固定、**跨 epoch 不变**（epoch 只重排 chunk
顺序）。旧实现在最内层每梯度步重算一次完整前向 = epochs× 白烧（本机 CPU 实测占
单步 ~44%，tools/tpu-probe.py 的 D1-D2 差）。

本测试守护三件事：
  1. ref 前向次数 == chunk 数（不是 chunk 数 x epochs）——缓存确实生效；
  2. 同种子两次运行聚合指标逐位相同——优化没引入不确定性；
  3. ref_model 缺席时 kickstart 恒 0 且不炸——旧护栏语义不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.student import PPOStudent
from ppo.engine import ppo_update

AGG_KEYS = {"policy", "value", "entropy", "kl", "kickstart", "mean_ret", "gnorm"}


class _CountingRef(torch.nn.Module):
    """记录 forward 调用次数的 ref 包装（不改变数值）。"""

    def __init__(self, inner: torch.nn.Module) -> None:
        super().__init__()
        self.inner = inner
        self.calls = 0

    def forward(self, obs, scalars):
        self.calls += 1
        return self.inner(obs, scalars)


def _chunks(n_chunks: int = 3, b: int = 8, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_chunks):
        out.append(
            {
                "obs": rng.integers(0, 256, (b, 14, 26, 26), dtype=np.uint8),
                "scalars": rng.standard_normal((b, 19)).astype(np.float32),
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


def _fresh(seed: int = 1234):
    torch.manual_seed(seed)
    model = PPOStudent(h=8, d=1)
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    return model, opt


def _run(epochs: int, *, ref=None, kickstart_kl: float = 1.0, seed: int = 7):
    np.random.seed(seed)
    model, opt = _fresh()
    return ppo_update(
        model,
        opt,
        _chunks(),
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
