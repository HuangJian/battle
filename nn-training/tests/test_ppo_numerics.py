"""PPO 数值正确性回归（plan/python-refactor.md P0-3 / 附录 A）。

**KL 估计量**：旧实现 `((lp_old-lp_new)**2).mean()` 是二阶近似 ½·E[(Δlnπ)²] 的
2 倍，且阈值照有偏口径标定 → 系统内部自洽但语义漂移。新实现 `approx_kl_est`
（ppo/common.py）用 Schulman 估计量 `E[(r-1) - ln r]`，**从 π_old 采样时恰为
真实 KL 的无偏估计**（E_{π_old}[r] = Σπ_new = 1，消去 E[r-1] 项）。

本文件对解析解断言：
  1. approx_kl_est ≈ 解析 KL(p‖q)（大样本下误差 < 1e-2）；
  2. 小漂移区 est ≈ ½·E[(Δlnπ)²]（旧口径的换算依据：新 ≈ 旧/2）；
  3. GAE 对解析例子逐值核对（含 done 截断与 dt 变步长）。

**为什么必须用解析解**：现有 test_ppo_intent / test_ppo_goal 只断言早停开关
是否触发（target_kl=1e-9 / 1e9 两个极端），从不校验 KL 数值——这正是旧缺陷
长期无覆盖的原因。

2026-09-26（item 9）：GAE 那三条（手算 / done 截断 / dt≡1）已搬到 `tests/test_np_core.py`
——它们只吃 `np_core.compute_gae`（numpy 口径），本文件留下的 KL 三条的定义域是**真 torch
张量**（`approx_kl_est` 对张量做 mean），故本文件仍是需要 torch 的那一半。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 家仍在 np_core（免 torch）；这里经 ppo.common 再导出取——本文件本就要 torch。
from ppo.common import approx_kl_est


def _cat_kl(p: np.ndarray, q: np.ndarray) -> float:
    """解析 KL：Σ p ln(p/q)。"""
    eps = 1e-12
    return float(np.sum(p * np.log((p + eps) / (q + eps))))


def _sample_logprobs(p: np.ndarray, q: np.ndarray, n: int, seed: int = 7):
    """从 p 采样 n 个类别 → (lp_old, lp_new) 每样本 gathered 标量（(n,)）。

    与引擎实际用法一致：ppo/engine.py 的 lp_old/lp_new 是**按已采动作 gather 后**
    的逐 transition 标量（cat_logprob 结果），估计量对它们做 mean。
    """
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(p), size=n, p=p)
    lp_old = torch.from_numpy(np.log(p[idx] + 1e-12)).to(torch.float32)
    lp_new = torch.from_numpy(np.log(q[idx] + 1e-12)).to(torch.float32)
    return lp_old, lp_new


@pytest.mark.parametrize(
    "p,q",
    [
        # 小漂移（healthy 稳态区间）
        (np.array([0.40, 0.30, 0.20, 0.10]), np.array([0.42, 0.29, 0.19, 0.10])),
        # 中等漂移
        (np.array([0.40, 0.30, 0.20, 0.10]), np.array([0.55, 0.25, 0.15, 0.05])),
        # 较大漂移（熔断关注区）
        (np.array([0.40, 0.30, 0.20, 0.10]), np.array([0.80, 0.10, 0.07, 0.03])),
    ],
)
def test_approx_kl_est_matches_analytic(p: np.ndarray, q: np.ndarray) -> None:
    """Schulman 估计量 ≈ 解析 KL(p‖q)（n=200k，容差 1e-2）。"""
    n = 200_000
    lp_old, lp_new = _sample_logprobs(p, q, n)
    est = approx_kl_est(lp_old, lp_new).item()
    true = _cat_kl(p, q)
    assert abs(est - true) < 1e-2, f"est={est:.5f} vs analytic KL={true:.5f}"


def test_approx_kl_est_is_half_of_second_order_in_small_drift() -> None:
    """小漂移换算依据：新估计量 ≈ ½·E[(Δlnπ)²]（即旧口径的 ½）。

    P0-3 的阈值换算（×0.5）建立在此关系上；漂移增大时二阶近似失真、新估计量
    保持贴近真实 KL——这正是熔断要拦截的区间，故不能用旧估计量。
    """
    rng = np.random.default_rng(0)
    lp_old = torch.tensor(rng.normal(0, 0.05, (100_000, 8)), dtype=torch.float32)
    lp_new = lp_old + torch.tensor(rng.normal(0, 0.05, (100_000, 8)), dtype=torch.float32)
    est_new = approx_kl_est(lp_old, lp_new).item()
    est_old = ((lp_old - lp_new) ** 2).mean().item()
    assert abs(est_new - 0.5 * est_old) < 0.5 * est_old * 0.05, (
        f"新估计量 {est_new:.5f} 应 ≈ 旧口径一半 {0.5 * est_old:.5f}"
    )


def test_approx_kl_est_nonnegative_and_zero_for_identical() -> None:
    """同一策略：估计量 ≈ 0；任意漂移：估计量非负（r-1-ln r ≥ 0）。"""
    # (n,) gathered 标量语义；p 与 q 全等 → 估计量 0
    p = np.full(4, 0.25)
    lp_old, lp_new = _sample_logprobs(p, p, 8192)
    assert abs(approx_kl_est(lp_old, lp_new).item()) < 1e-6
    # 漂移情形：逐样本 r-1-ln r ≥ 0 → 均值非负
    lp2 = lp_old + torch.randn_like(lp_old) * 0.1
    assert approx_kl_est(lp_old, lp2).item() >= -1e-6


