"""run_training —— RL 迭代主循环入口（薄包装；OO 实现见 rl/loop_core.py）。

run_rl.py 只依赖本模块的 run_training 签名（args, ppo_backend, bun,
update_kwargs）——保持稳定；TrainingLoop 类的全部迭代状态机在 loop_core。
"""

from __future__ import annotations

from rl.events import log_iter_error  # noqa: F401 — re-exported（旧模块成员）
from rl.loop_core import TrainingLoop, run_inspect  # noqa: F401 — re-exported


def run_training(args, ppo_backend, bun, update_kwargs) -> None:
    """RL 迭代主循环（2026-09-02 从 run_rl.py main() 拆出，OO 化后为薄包装）。"""
    TrainingLoop(args, ppo_backend, bun, update_kwargs).run()
