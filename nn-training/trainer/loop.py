"""run_training —— RL 迭代主循环入口（薄包装；OO 实现见 trainer/loop_core.py）。

trainer/run_rl.py 只依赖本模块的 run_training 签名（args, ppo_backend, bun,
update_kwargs）——保持稳定；TrainingLoop 类的全部迭代状态机在 loop_core。
2026-11-16：`run_inspect` / `log_iter_error` 的再导出删掉（全仓零消费方，
`loop_core.run_inspect` 才是可替换点，测试直接替换它）。
"""

from __future__ import annotations

from trainer.loop_core import TrainingLoop


def run_training(args, ppo_backend, bun, update_kwargs) -> None:
    """RL 迭代主循环（2026-09-02 从 trainer/run_rl.py main() 拆出，OO 化后为薄包装）。"""
    TrainingLoop(args, ppo_backend, bun, update_kwargs).run()
