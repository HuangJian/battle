"""S0-e：训练侧冒烟 —— 梯度必须流到新头/空间塔（plan/policy-spatial-head.plan.md §4 Step 2）。

为什么单有 S0-d 不够（评审 hy5 P1-2a）：S0-d 的 10 条**全是推理侧**——前向一定经过塔，
所以它们一定绿，抓不到「**反向**没更新塔」：optimizer 漏参 / requires_grad 没开 /
warm-start 新张量没进 param_group。这类 bug 只会在 150 iter 跑完后以「塔毫无贡献」暴露，
代价 = 300 iter × 2 臂。

本测试走**真实训练路径**（`ppo_update` + `tensored_chunks`，含 extra 接线）：
  ① 新参数（spatial_proj/spatial_fc/move_head/fire_head）requires_grad 且进 optimizer param_group；
  ② 20 个 PPO 梯度步后，塔与新头的 ‖ΔW‖ > 0 且非全零（防冻结）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch

from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM
from worker.models.student import PPOStudent
from worker.ppo.engine import ppo_update
from worker.ppo.trainer import tensored_chunks

NEW_KEYS = (
    "spatial_proj.weight",
    "spatial_proj.bias",
    "spatial_fc.weight",
    "spatial_fc.bias",
    "move_head.weight",
    "fire_head.weight",
)


def _make_model() -> PPOStudent:
    torch.manual_seed(20261005)
    return PPOStudent(h=8, d=1, policy_extra=True, spatial_tower=True)


def _synth_chunks(n_chunks: int = 5, mb: int = 32, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    chunks = []
    for _ in range(n_chunks):
        obs = rng.integers(0, 256, (mb, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8)
        chunks.append(
            {
                "obs": obs,
                "scalars": rng.standard_normal((mb, SCALAR_DIM)).astype(np.float32),
                "extra": rng.random((mb, POLICY_EXTRA_DIM)).astype(np.float32) * 1.5,
                "a_move": rng.integers(0, 5, mb).astype(np.int64),
                "a_fire": rng.integers(0, 2, mb).astype(np.int64),
                "lp_move": (rng.random(mb) - 2).astype(np.float32),
                "lp_fire": (rng.random(mb) - 2).astype(np.float32),
                "adv": rng.standard_normal(mb).astype(np.float32),
                "ret": rng.standard_normal(mb).astype(np.float32),
                "mask": np.ones((mb, 7), dtype=np.int64),
            }
        )
    return chunks


def test_new_params_requires_grad_and_in_optimizer_group() -> None:
    """新张量必须在计算图里、且进 param_group（warm-start 漏进 optimizer = 冻结）。"""
    model = _make_model()
    opt = torch.optim.Adam(model.parameters(), lr=3e-4)
    group_ids = {id(p) for g in opt.param_groups for p in g["params"]}
    state = model.state_dict()
    for k in NEW_KEYS:
        assert k in state, f"新架构缺键 {k}"
        p = dict(model.named_parameters())[k]
        assert p.requires_grad, f"{k} requires_grad=False（反向到不了）"
        assert id(p) in group_ids, f"{k} 不在 optimizer param_group（会静默冻结）"


def test_20_ppo_steps_update_tower_and_heads() -> None:
    """20 个真实 PPO 梯度步后：塔与新头权重必须变化（‖ΔW‖ > 0 且非全零）。"""
    model = _make_model()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    before = {k: v.detach().clone() for k, v in model.state_dict().items()}
    chunks = _synth_chunks()
    # 5 chunks × epochs=4 = 20 个梯度步；ent_coef 显式给小值避免策略坍缩干扰（纯冒烟）。
    ppo_update(model, opt, chunks, epochs=4, device=torch.device("cpu"), ent_coef=0.01)
    state = model.state_dict()
    for k in NEW_KEYS:
        delta = float((state[k] - before[k]).abs().sum())
        assert delta > 0, f"{k} 20 步后未变化（梯度没流到）"
        assert torch.count_nonzero(state[k] - before[k]) > 0, f"{k} Δ 全零"
    # 主干也应更新（对照组：确认整个计算图在跑）
    stem_delta = float((state["stem.weight"] - before["stem.weight"]).abs().sum())
    assert stem_delta > 0


def test_missing_extra_is_refused_loudly() -> None:
    """新架构模型缺 extra 必须响亮拒绝（不静默当 0 用）。"""
    model = _make_model()
    obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    sc = torch.zeros(2, SCALAR_DIM)
    with pytest.raises(ValueError, match="POLICY_EXTRA"):
        model(obs, sc, None)
