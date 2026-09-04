"""remote —— 云 hub 协同训练（远程 PPO）模块（plan/remote-ppo-architecture.md）。

子模块全部 torch-free（hub 侧免 torch，D2）；云端 worker（remote/worker.py）
的 torch 依赖延迟到 run_job 内导入。
"""
