"""nproc.py —— 打印本机**物理核数**（门禁 / CI 的并行度口径）。

用法：``python tools/nproc.py`` → 一行整数（本机 8）。

为什么单列一个 CLI，而不是在 workflow 里写 ``uv run python -c '...'``：
``nn-training/tests/test_ci_workflow_paths.py`` 会把 workflow 里 ``uv run python <path>`` 的
``<path>`` 当**文件路径**校验存在性（正则 ``_UV_RUN_PY``），``-c`` 会被当成一个名叫 ``-c``
的路径 ⇒ 那条门禁红。给它一个真文件最省事，也让 CI 的核数口径与门禁脚本/task.py/Makefile
共用同一个函数（``common.platform_utils.physical_cores``）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 脚本目录是 tools/，`common` 在 nn-training/ 下 ⇒ 手动补 sys.path（与 tools/task.py 同款）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.platform_utils import physical_cores

if __name__ == "__main__":
    print(physical_cores())
