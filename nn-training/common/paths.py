"""common/paths.py — 仓库路径锚点（唯一一份，stdlib-only）。

为什么值得单独一个模块：`REPO_ROOT` 从前住在 `common/distribution.py` 里，任何只想拼个
路径的调用点都得 import 整个分发层（`common/codehash.py` 就是这种调用方：它只读文件、不发
请求）。更贵的是上溯层数 —— `nn-training/dist_common.py` 搬进 `nn-training/common/` 之后
`REPO_ROOT` 指到了仓库的**父目录**，代码不报错，只是路径全指错（2026-09-30 刀 2）。层数
现在只有这一处定义。

2026-11-16 起这条也覆盖 `biz/ hub/ remote/ trainer/ worker/` 里各自数层数的 13 份副本：
同一个常量 N 份定义时，「层数算错」只会安静地坏掉一半调用点（`hub/store_offline.py` 那次
`parents[3]` → `parents[2]` 就是活例）。入口脚本（`trainer/eval_*_once.py` 等）例外 ——
它们要在 import `common` **之前**算出根来插 `sys.path`，那种自举只能在本地算。

`REPO_ROOT` 是 `Path`（拼路径的地方一律 `/`）；需要 posix 相对路径的地方自己 `replace`
（`common/codehash.py` 就这么做：codeHash 配方要求 posix 路径逐字节稳定）。
"""

from __future__ import annotations

from pathlib import Path

#: 仓库根（`battle/`）。本文件在 `nn-training/common/` ⇒ 上溯 3 层。改仓库布局就得改这里，且只有这里。
REPO_ROOT = Path(__file__).resolve().parents[2]
