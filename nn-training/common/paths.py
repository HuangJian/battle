"""common/paths.py — 仓库路径锚点（唯一一份，stdlib-only）。

为什么值得单独一个模块：`REPO_ROOT` 从前住在 `common/distribution.py` 里，任何只想拼个
路径的调用点都得 import 整个分发层（`common/codehash.py` 就是这种调用方：它只读文件、不发
请求）。更贵的是上溯层数 —— `nn-training/dist_common.py` 搬进 `nn-training/common/` 之后
`REPO_ROOT` 指到了仓库的**父目录**，代码不报错，只是路径全指错（2026-09-30 刀 2）。层数
现在只有这一处定义。

`REPO_ROOT` 用本机分隔符（`os.path.dirname`）；需要 posix 相对路径的地方自己 replace（
`common/codehash.py` 就这么做：codeHash 配方要求 posix 路径逐字节稳定）。
"""

from __future__ import annotations

import os

#: 仓库根。本文件在 `nn-training/common/` ⇒ 上溯 3 层。改仓库布局就得改这里，且只有这里。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
