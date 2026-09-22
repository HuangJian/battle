"""demo 混 batch 回归（ppo/engine.py::ppo_update demo BC 项）。

守护四件事：
  1. 缺省路径不变：demo_bank 到位但 coef=0（或三者任一缺席）⇒ 权重与纯 PPO
     逐位相同、demo_bc 恒 0（数学逐字节不变）；
  2. 开启时 demo_bc 非零有限（BC 项确实进了 loss）；
  3. 同种子两次运行聚合 + 权重逐位相同（np RNG 抽样确定性，ckpt 可复现）；
  4. 全单合法类 mask 的 bank ⇒ demo_bc 恒 0（_masked_ce 跳过语义，不炸）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

from schema import OBS_CHANNELS, SCALAR_DIM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.student import PPOStudent
from ppo.engine import ppo_update


def _chunks(n_chunks: int = 2, b: int = 8, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [
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
        for _ in range(n_chunks)
    ]


def _bank(n: int = 64, seed: int = 1, single_mask: bool = False) -> dict:
    rng = np.random.default_rng(seed)
    masks = np.ones((n, 7), dtype=np.uint8)
    if single_mask:
        masks[:] = 0
        masks[:, 0] = 1  # 每行仅 1 个合法类 ⇒ _masked_ce 全跳过
    return {
        "obs": rng.integers(0, 256, (n, OBS_CHANNELS, 26, 26), dtype=np.uint8),
        "scalars": rng.standard_normal((n, SCALAR_DIM)).astype(np.float32),
        "actions": np.stack(
            [rng.integers(0, 5, (n,)), rng.integers(0, 2, (n,))], axis=1
        ).astype(np.int64),
        "masks": masks,
    }


def _fresh(seed: int = 1234):
    torch.manual_seed(seed)
    model = PPOStudent(h=8, d=1)
    opt = torch.optim.Adam(model.parameters(), lr=1.5e-4)
    return model, opt


def _run(epochs: int = 1, seed: int = 7, **kw: Any):
    np.random.seed(seed)
    model, opt = _fresh()
    agg = ppo_update(model, opt, _chunks(), epochs, torch.device("cpu"), kl_coef=0.0, **kw)
    state = {k: v.clone() for k, v in model.state_dict().items()}
    return agg, state


def test_demo_off_is_bitwise_pure_ppo() -> None:
    """bank 到位但 coef=0 ⇒ 与纯 PPO 逐位相同、demo_bc 恒 0。"""
    bank = _bank()
    a, sa = _run(demo_bank=bank, demo_bc_coef=0.0, demo_per_mb=4)
    b, sb = _run()
    assert a["demo_bc"] == 0.0
    assert set(a) == set(b), f"聚合键漂移：{set(a) ^ set(b)}"
    for k in sorted(a):
        assert a[k] == b[k], f"{k}: {a[k]!r} != {b[k]!r}"
    for k in sa:
        assert torch.equal(sa[k], sb[k]), f"权重 {k} 漂移（demo 关闭路径非恒等）"


def test_demo_on_reports_nonzero_bc() -> None:
    """开启时 demo_bc 非零有限（BC 项进了 loss）。"""
    agg, _ = _run(demo_bank=_bank(), demo_bc_coef=0.02, demo_per_mb=8)
    assert np.isfinite(agg["demo_bc"])
    assert agg["demo_bc"] > 0.0, f"demo_bc={agg['demo_bc']}，BC 项疑似未生效"


def test_demo_determinism() -> None:
    """同种子两次 demo 运行聚合 + 权重逐位相同。"""
    kw: dict[str, Any] = {"demo_bank": _bank(), "demo_bc_coef": 0.02, "demo_per_mb": 8}
    a, sa = _run(epochs=2, **kw)
    b, sb = _run(epochs=2, **kw)
    for k in sorted(a):
        assert a[k] == b[k], f"{k}: {a[k]!r} != {b[k]!r}（demo 抽样不确定）"
    for k in sa:
        assert torch.equal(sa[k], sb[k]), f"权重 {k} 不确定"


def test_demo_single_valid_mask_skips() -> None:
    """全单合法类 mask ⇒ demo_bc 恒 0、不炸（_masked_ce 跳过语义）。"""
    agg, _ = _run(demo_bank=_bank(single_mask=True), demo_bc_coef=0.02, demo_per_mb=8)
    assert agg["demo_bc"] == 0.0


def test_demo_per_mb_larger_than_bank() -> None:
    """per_mb > N 不炸（有放回抽样，语义正常）。"""
    agg, _ = _run(demo_bank=_bank(n=16), demo_bc_coef=0.02, demo_per_mb=64)
    assert np.isfinite(agg["demo_bc"])
