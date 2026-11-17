"""PPO 的三条随机流：`chunk` / `epoch` / `demo` —— 显式、可存、互不干扰（§P1-7 / §10-B6）。

## 为什么不再用全局 `np.random`

训练序列原先住在**全局 `np.random`** 上：入口 `np.random.seed(args.seed)` 播种，
`np_core.chunk_episodes` 的切块重排、每 epoch 的 minibatch 顺序、每 minibatch 的 demo 抽样
**共用同一本账**。三个后果：

1. **任何第三方抽一次随机数就改动了训练序列** —— 「同一 seed ⇒ 同一条轨迹」变成一个
   不可执行的前提（而两处注释把它写成了保证）；
2. **加一处抽样就推歪别处**（demo 从 0 改成 >0 ⇒ 之后每个 epoch 的重排全变），于是
   「只改了 demo 系数」的实验实际上动了两个变量；
3. ckpt 只能存**全局 MT19937 状态**（`state.json["rng"]`），续跑的精确性依赖
   「这段时间没别人碰全局」，而这正是第 1 条说不可执行的。

## 做法

三条流各自 `default_rng(派生种子)`，种子由 `Runtime.substream(name)` 给（L0 的 blake2b 派生，
`common/runtime.py`，不引 numpy）。同一个 `seed` 下三条流同源而独立 —— 加/删任何一条的抽法
都不会移动另外两条。

ckpt 存每条流的 `bit_generator.state`（纯 JSON：位生成器名 + 内部整数状态），续跑灌回去即可
逐位接上。旧 ckpt 里没有这个字段 ⇒ `restore()` 返回 `False`，调用方**响亮**记一行
（不静默从种子重来）。

⚠ **数值断代**：PCG64 ≠ 旧 MT19937，切换后同一 seed 的轨迹与历史不可逐位对照。这是 §P1-7
要求的代价，写在这里免得下一个人当成 bug；统计意义上的等价由「同 seed 两遍逐位一致」
（`tests/worker/test_ppo_rng_streams.py`）守，而不是靠与旧轨迹对数字。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from common import runtime

#: 流名（闭集）：`chunk` = 语料切块重排；`epoch` = 每 epoch 的 minibatch 顺序；`demo` = demo bank 抽样。
STREAMS: tuple[str, ...] = ("chunk", "epoch", "demo")


@dataclass
class PPORng:
    """一次 PPO 运行的三条随机流（各自一个 `np.random.Generator`）。"""

    chunk: np.random.Generator
    epoch: np.random.Generator
    demo: np.random.Generator

    @classmethod
    def from_runtime(cls, rt: runtime.Runtime) -> PPORng:
        """从运行时域派生（`rt` 必须带 seed；派生规则只有 `Runtime.substream` 一份）。"""
        return cls(**{name: np.random.default_rng(list(rt.substream(name))) for name in STREAMS})

    @classmethod
    def from_seed(cls, seed: int, *, scope: str = "ppo") -> PPORng:
        """一行从种子拿三条流（入口/工具/测试用；同一个 seed ⇒ 同一组流）。"""
        return cls.from_runtime(runtime.Runtime(scope=scope, seed=seed))

    def state(self) -> dict[str, dict[str, object]]:
        """三条流 → JSON 可序列化（`bit_generator.state` 本身就是 dict of int/str）。"""
        return {name: dict(getattr(self, name).bit_generator.state) for name in STREAMS}

    def restore(self, state: Mapping[str, object] | None) -> bool:
        """把 ckpt 里的流状态灌回去；缺项 / 坏项 / 换了位生成器 ⇒ **整份**拒收并返回 False。

        为什么不逐流尽力恢复：半份续跑会在「以为精确」的前提下得到另一条序列 —— 比
        「明确不精确」危险得多（这也是 `_ppo_load` 对缺三件套就返回 0 的同一条纪律）。
        """
        if not isinstance(state, Mapping):
            return False
        for name in STREAMS:
            blob = state.get(name)
            if not isinstance(blob, Mapping):
                return False
            gen = getattr(self, name)
            if str(blob.get("bit_generator")) != str(gen.bit_generator.state["bit_generator"]):
                return False
            gen.bit_generator.state = dict(blob)
        return True
