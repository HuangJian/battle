"""e2e conftest — 集成/e2e 测试目录（不在 python-gate 的 pytest 路径内）。

门禁只跑 `pytest tests/`（见 tools/githook/nn-python-gate.sh 与 pyproject
testpaths）。本目录需显式指定：

    cd nn-training && .venv/Scripts/python.exe -m pytest e2e/ -q

夹具与 tests/conftest.py 共用（tmp_path 覆盖、通过即清、失败保留）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 复用单测目录的 fixtures / session hooks（tmp_path 覆盖与清理语义一致）。
from tests.conftest import (
    bp_args,
    pytest_runtest_makereport,
    pytest_sessionfinish,
    tmp,
    tmp_path,
)
