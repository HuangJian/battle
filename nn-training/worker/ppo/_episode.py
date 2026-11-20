"""_episode.py —— 三个流式 backend（`np_core` / `goal` / `intent`）的**公共装载骨架**。

出处：plan/nn-training-reuse-and-deps.plan.md §S3。三处的 `load_episode_from_shard` 共享同一套
机械动作 ——「空 shard → `None`」→ 算 GAE → 组 episode dict —— 各自只差**字段契约**与 loader。

为什么**不**放进 `worker/ppo/common.py`：那个模块顶层 import torch，而 `np_core.py` 是刻意保持
numpy-only 的核心（torch 只在训练侧需要）⇒ 借 common 会把 torch 拖进 np_core。

`fields` 里的 `"adv"` / `"ret"` 是**占位符**（它们不在 shard 里，由 GAE 现算）：这样每个调用方
都能按自己的原顺序声明字段，dict 的键序**逐字不变**。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

#: 骨架面（本文件 / `np_core.trim_shard_arrays` / `load_episodes_common`）看到的 shard 视图：
#: 键集随 backend 变（ppo / goal / intent 三套），值恒为 ndarray。
#:
#: 为什么是 `Mapping` 而不是 `dict`：`np_core` 交的是 `common.contracts.Shard` / `ShardV6`，
#: 而 mypy 里 `TypedDict` **不是** `dict` 的子型（且它上只认字面量键），骨架又只读不写。
#: 为什么值是 `Any` 而不是 `object`：骨架里 `d[k]` 的键是**变量**，取出来的值要能做 ndarray 的
#: 事（`.shape` / 算术）；用 `object` 会在 GAE 与归一化处集体报错（P1-4 实测过那一把）。
#: 需要严格类型的两端各有契约：`Shard`/`ShardV6`（装载后）与 `Episode`（键与序）。
ShardRow = Mapping[str, Any]

#: GAE 计算器：吃整个 shard，回 `(adv, ret)`。
GaeFn = Callable[[ShardRow], tuple[np.ndarray, np.ndarray]]


def episode_from_shard(
    d: ShardRow,
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
