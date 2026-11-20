"""`ppo/demo.py` — demo bank 的字段表（PPO demonstration-BC 面唯一的数据契约）。

demo bank 是 `.npz`：`tools/build_demo_bank.py` 写、`remote.train_common.decode_demo_bank` 读、
`worker/ppo/engine.py` 与 `engine_paddle.py` 各自物化成张量。这条链原本靠**裸 `dict` + 字符串键**
串起来（`bank["obs"]` / `if "extra" in bank`），字段名写错只会在训练中段炸成 `KeyError`，
或者更糟 —— `extra` 被静默跳过、BC 项少一半而日志一切正常。

字段顺序与形状（`build_demo_bank` 头部）：
  * `obs`      (N, OBS_CHANNELS, 26, 26) uint8
  * `scalars`  (N, SCALAR_DIM) f4
  * `extra`    (N, POLICY_EXTRA_DIM) f4
  * `actions`  (N, 2) i8 `[move, fire]`
  * `masks`    (N, 7) u1 `[move5, fire2]`

**五个字段都是必需的**：`check_demo_bank` 的安全阀就是按这份名单拒收（缺 `extra` 的老 bank 要重导，
不是「降级跑」）。所以 `DEMO_BANK_FIELDS` 直接由这里的键派生，名单只有一份。
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np


class DemoBank(TypedDict):
    """`decode_demo_bank` 的产物：`{字段名: ndarray}`，键见模块 docstring。"""

    obs: np.ndarray
    scalars: np.ndarray
    extra: np.ndarray
    actions: np.ndarray
    masks: np.ndarray


#: 安全阀用的字段名单（`check_demo_bank` 的唯一来源）—— 从 `DemoBank` 派生，不许另抄一份。
DEMO_BANK_FIELDS: frozenset[str] = frozenset(DemoBank.__required_keys__)
