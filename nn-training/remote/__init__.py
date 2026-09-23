"""remote —— 云 hub 协同训练（远程 PPO）模块（plan/remote-ppo-architecture.md）。

子模块全部 torch-free（hub 侧免 torch，D2）；云端 worker（remote/worker.py）
的 torch 依赖延迟到 run_job 内导入。

**分层位置（2026-09-23，S3）**：本包是**最上层（L2）**，可以依赖 `rl/`、`ppo/`、`train/`、
`data/` 与 `common/`，**反之禁止**——`L1` 不得 import `remote`（`tests/test_layering.py` 守着）。
两个本来属于「纯逻辑」的子模块已下沉：`protocol.py` / `game_watch.py` → `common/`，
因为它们被 `rl/` 引用、与 `remote/ → rl/` 构成包级循环。

三个引导模块是**结构性例外**：`tailscale_boot.py` / `notebook_boot.py` / `offline_boot.py`
从 GitHub raw 单独拉取（cell 拿到 `code.zip` 之前就要 import），因此不得 import
其他 `remote.*`（也不得 import `common`）——其内部重复是有意为之，别顺手合并。
"""
