"""_episode.py —— 三个流式 backend（`np_core` / `goal` / `intent`）的**公共装载骨架**。

出处：plan/nn-training-reuse-and-deps.plan.md §S3。三处的 `load_episode_from_shard` 共享同一套
机械动作 ——「空 shard → `None`」→ 算 GAE → 组 episode dict —— 各自只差**字段契约**与 loader。

为什么**不**放进 `worker/ppo/common.py`：那个模块顶层 import torch，而 `np_core.py` 是刻意保持
numpy-only 的核心（torch 只在训练侧需要）⇒ 借 common 会把 torch 拖进 np_core。

`fields` 里的 `"adv"` / `"ret"` 是**占位符**（它们不在 shard 里，由 GAE 现算）：这样每个调用方
都能按自己的原顺序声明字段，dict 的键序**逐字不变**。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

#: GAE 计算器：吃整个 shard dict，回 `(adv, ret)`。
GaeFn = Callable[[dict[str, Any]], tuple[np.ndarray, np.ndarray]]


def episode_from_shard(
    d: dict[str, Any],
    gae: GaeFn,
    *,
    fields: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """`{field: d[field]}` + `adv`/`ret`（float32）；空 shard（`obs.shape[0] == 0`）→ `None`。

    `optional` 里的字段**存在才带**（旧 shard 缺列时保持老行为，例如 goal 的 `engage_label`）。
    """
    if d["obs"].shape[0] == 0:
        return None
    adv, ret = gae(d)
    computed = {"adv": adv.astype(np.float32), "ret": ret.astype(np.float32)}
    ep = {k: computed[k] if k in computed else d[k] for k in fields}
    for k in optional:
        if k in d:
            ep[k] = d[k]
    return ep
