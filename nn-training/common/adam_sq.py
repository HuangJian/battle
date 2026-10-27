"""common/adam_sq.py —— Adam `exp_avg_sq` 下溢消毒的 **numpy 族**（`K` 与 torch 版同源）。

规则只有一条：`exp_avg_sq <- max(exp_avg_sq, (|exp_avg| / K)^2)`，amsgrad 节点跳过，幂等，
返回被抬高的**元素数**。torch 族（`remote/opt_delta.sanitize_adam_sq`）与这里由
`tests/common/test_torch_pickle.py::test_sanitize_numpy_matches_torch` 对同一组随机状态逐项
对账 —— 任何一侧改了口径（K、amsgrad、形状判据）都会当场红。

**为什么住 `common/`**：读侧落地（`remote.opt_delta.apply_opt_delta_np`）与 Paddle worker
（`worker.ppo.checkpoint_paddle`）都要用它，而 `worker → remote` 是分层硬禁
（`tests/test_layering.py`）；`common/` 是两侧都合法的唯一交叉点。numpy 惰性 import：
hub 的启动闭包会顶层拉入 `remote.opt_standard`，那里的环境没有 numpy。
"""

from __future__ import annotations

from typing import Any

__all__ = ["ADAM_SQ_FLOOR_K", "sanitize_adam_sq_np"]

#: `exp_avg_sq` 下溢消毒的下限系数 K（torch / numpy 两族的唯一调参）。
ADAM_SQ_FLOOR_K = 10.0


def sanitize_adam_sq_np(state: Any) -> int:
    """`remote.opt_delta.sanitize_adam_sq` 的 **numpy 版**（规则逐字同源，容器不同）。

    同一份状态在 torch / numpy 两个容器里的形状不同（`torch.Tensor` vs `np.ndarray`），
    判据与读数必须一致（见模块头）。
    """
    import numpy as np

    found: list[dict] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if "exp_avg" in node and "exp_avg_sq" in node:
                m = node["exp_avg"]
                v = node["exp_avg_sq"]
                if isinstance(m, np.ndarray) and isinstance(v, np.ndarray) and m.shape == v.shape:
                    found.append(node)
                    return
            for child in node.values():
                collect(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                collect(child)

    collect(state)
    raised = 0
    for node in found:
        if node.get("max_exp_avg_sq") is not None:
            continue
        m = node["exp_avg"]
        v = node["exp_avg_sq"]
        if v.size == 0:
            continue
        floor = (np.abs(m) / np.float32(ADAM_SQ_FLOOR_K)) ** 2
        n = int((v < floor).sum())
        if n:
            np.maximum(v, floor, out=v)
            raised += n
    return raised
