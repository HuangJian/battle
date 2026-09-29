"""nn-training/tools —— 开发/运维脚本包（与 tests/、e2e/ 同约定，带 __init__.py）。

**为什么必须有它**（2026-09-29，加 `tools/forkdist.py` 时踩到）：没有 `__init__.py` 时
`tools/` 是 **namespace package**，同一个文件在 mypy 眼里可以同时是 `forkdist`（`tools/`
不是包 ⇒ 文件被当成顶层模块）和 `tools.forkdist`（命名空间包路径）⇒ 门禁直接红：

    tools/forkdist.py: error: Source file found twice under different module names:
    "forkdist" and "tools.forkdist"

`pyproject.toml` 的 `[tool.setuptools.packages.find] include` 早就写了 `tools*`（本意就是
把它当包装）；`tests/`、`e2e/` 也都有 `__init__.py`。补上这一个空包声明，三方（mypy /
`-p tools.<插件>` / `pip install -e .`）的模块名就一致了。
"""
