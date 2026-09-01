"""Rollout 后端契约（Protocol）—— stream.py 的 duck typing 约束为可执行断言。

背景（2026-09-02 审查，plan/python-refactor.md P0-1 / P1-2）：

`rl/stream.py` 通过 `backend` 参数复用三套 PPO 后端（per-tick / intent / goal），
但契约只存在于 `rl/modes.py` 与 `rl/stream.py` 的**注释**里，没有任何类型约束。
后果：goal 后端的 `ppo_update_goal` 缺少 `on_epoch_done` 形参，而
`stream.py:123-124` 无条件注入它 —— 该缺陷直到训练中途第一个 wave 才以
`TypeError` 爆炸，表现为"每轮卡 30 秒、5 连败退出"，极易被误读为节点故障。

本模块把契约固化为 `typing.Protocol`，并在 `tests/test_backend_contract.py` 中对
`rl.modes._MODE_BACKENDS` 的每个成员做**启动期断言**——签名不匹配在测试期即暴露，
而非训练中途。

注意：`@runtime_checkable` 的 `isinstance()` **只检查属性存在，不检查签名**。
因此签名级约束（`update` 必须接受 `on_epoch_done`）由 `REQUIRED_UPDATE_KWARGS`
+ `inspect.signature` 单独断言，见测试模块。

新增后端时：实现下列 5 个成员即可，测试会自动校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # 避免 Protocol 模块被导入时拉起 torch
    import torch

__all__ = ["REQUIRED_UPDATE_KWARGS", "RolloutBackend"]


@runtime_checkable
class RolloutBackend(Protocol):
    """流式迭代对 PPO 后端的要求（rl/stream.py 的调用点即契约来源）。

    调用点对照：
      load_episode_from_shard → stream.py:243（逐局装载 + GAE）
      chunk_episodes          → stream.py:274（切 minibatch）
      update                  → stream.py:286（梯度更新，可附加 **update_kwargs）
      load_episodes           → stream.py:381（断点续跑的全盘回退路径）
      _ppo_load               → stream.py:371（epoch 粒度 checkpoint 续跑）
    """

    def load_episode_from_shard(self, dirpath: str) -> dict | None:
        """单个 shard 目录 → 可训练 episode dict（adv/ret 未归一）；空/坏 shard 返回 None。"""
        ...

    def chunk_episodes(self, episodes: list[dict], mb: int) -> list[dict]:
        """episodes → 固定大小 minibatch chunks（末尾 ragged）。"""
        ...

    def update(
        self,
        model: Any,
        opt: Any,
        chunks: list[dict],
        epochs: int,
        device: torch.device | str,
        **kwargs: Any,
    ) -> dict:
        """跑 epochs 遍 PPO，返回聚合指标 dict。

        必须含键：policy / value / entropy / kl / gnorm / mean_ret
        （stream.py:294-296 与 run_rl.py 的 jsonl 落盘直接读这些键）。
        """
        ...

    def load_episodes(self, data_root: str) -> list[dict]:
        """目录 → 全量 episodes（adv 已归一）。断点续跑零结算时的回退路径。"""
        ...

    def _ppo_load(self, ckpt_path: str | None, model: Any, opt: Any) -> int:
        """返回已完成 epoch 数（0 = 无 checkpoint / 无法加载）。"""
        ...


# stream.py 会无条件注入的关键字参数（见 run_rollout_stream 的 update_kwargs 装配）。
# 后端必须全部接受，否则流式模式下途 TypeError —— P0-1 的直接教训。
REQUIRED_UPDATE_KWARGS: frozenset[str] = frozenset(
    {
        "ckpt_path",  # 每 epoch 落盘 checkpoint（stream.py:390 的回退路径）
        "on_epoch_done",  # 双缓冲提前预采的触发点（stream.py:123-124 无条件注入）
    }
)
