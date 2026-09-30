"""nn-training/tools —— 开发/运维脚本包（与 tests/、e2e/ 同约定，带 __init__.py）。

**为什么必须有它**（2026-09-29，加 `tools/forkdist.py` 时踩到）：没有 `__init__.py` 时
`tools/` 是 **namespace package**，同一个文件在 mypy 眼里可以同时是 `forkdist`（`tools/`
不是包 ⇒ 文件被当成顶层模块）和 `tools.forkdist`（命名空间包路径）⇒ 门禁直接红：

    tools/forkdist.py: error: Source file found twice under different module names:
    "forkdist" and "tools.forkdist"

`pyproject.toml` 的 `[tool.setuptools.packages.find] include` 早就写了 `tools*`（本意就是
把它当包装）；`tests/`、`e2e/` 也都有 `__init__.py`。补上这一个空包声明，三方（mypy /
`-p tools.<插件>` / `pip install -e .`）的模块名就一致了。

**2026-09-30（刀 7）**：五个「运维/环境」脚本从 `nn-training/` 顶层搬进来 ——
`bootstrap.py`（搭环境）· `task.py`（跨平台 task runner，= Makefile）· `smoke_test.py`
（torch 冒烟）· `weights_prune.py`（权重保留策略）· `dist_upgrade_cli.py`（节点升级 CLI）。
它们的 `HERE` **语义不变**（仍是 nn-training 根：`.venv` / `pyproject.toml` / `weights/` 都
相对它）⇒ 只把推导从 `parent` 改成 `parents[1]`；而脚本目录 `tools/` 里没有与 stdlib 同名的
模块，所以它们**不需要** `trainer/` 那套「摘掉 sys.path[0]」的 prelude（只需把 nn-training
根前置，见各自文件头）。命令行引用同步改：`python nn-training/tools/task.py <target>`。
"""
