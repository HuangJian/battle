"""reward_context —— 加载期 holder（评审 R1-1 / LC §4.1）。

为什么需要它：`update_kwargs` 是 **update 期**参数，到不了 `compute_gae` 的
**加载**点（`ppo.engine.load_episode_from_shard` / `load_episodes`）。而奖励与
gamma/lam 恰恰是在加载期消费的——schedule 让它们每 iter 可变，就必须有一个
加载期可见的注入通道。

形态：**frozen dataclass + 模块级单例**，每 iter 整体替换（禁 in-place mutation）。
加载器无参调用时读 holder；显式传参时以显式参数为准（测试/离线脚本不受影响）。

契约：intent/goal 后端不读 holder（它们有自己的 reward.npy 与 GAE 口径），
`reward_fn is None` 时 per-tick 走旧路径（直接读 shard 里的 reward 数组）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from rl.reward_library import METRICS_VERSION


@dataclass(frozen=True)
class RewardContext:
    """加载期奖励上下文（不可变；每 iter 由 `set_current` 整体替换）。"""

    #: 已解析的奖励函数（None = 走 shard 内 reward 数组的旧路径）
    reward_fn: Any | None = None
    gamma: float = 0.995
    lam: float = 0.95
    #: 当前迭代（param_schedule 折算用；从 1 开始）
    it: int = 1
    #: shard manifest 的 metrics_version 期望值（版本不匹配 → 加载期响亮报错）
    metrics_version: int = METRICS_VERSION
    #: reward 血缘（course 名 + formula 指纹），落 training_log / metrics_stats
    identity: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return self.reward_fn is not None


_DEFAULT = RewardContext()
_current: RewardContext = _DEFAULT


def current() -> RewardContext:
    """当前 holder（加载器默认读取）。"""
    return _current


def set_current(ctx: RewardContext) -> RewardContext:
    """整体替换当前 holder，返回旧值（便于 with 式还原）。"""
    global _current
    old = _current
    _current = ctx
    return old


def update(**kwargs: Any) -> RewardContext:
    """基于当前 holder 派生新实例（`dataclasses.replace`，不改旧对象）。"""
    return set_current(replace(_current, **kwargs))


def reset() -> None:
    """还原为默认（无 reward_fn、模块常量 gamma/lam）—— 测试隔离用。"""
    global _current
    _current = _DEFAULT


class Scoped:
    """`with reward_context.scoped(reward_fn=fn, it=3): ...` —— 退出即还原。"""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._old: RewardContext | None = None

    def __enter__(self) -> RewardContext:
        self._old = update(**self._kwargs)
        return _current

    def __exit__(self, *exc: object) -> None:
        assert self._old is not None
        set_current(self._old)
