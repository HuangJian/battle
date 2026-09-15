"""e2e conftest — 集成层（tests/ 单测层的下一层，同属 python-gate）。

层 = 路径（2026-09-15）：门禁与 CI 的目标是 `tests/` + `e2e/`（见
tools/githook/nn-python-gate.sh）——本目录自 60e5f69 起 hermetic（FakeServer +
tmp 落盘，不需要 bun / 真节点 / weights fixture），所以能进门禁。
只跑这一层：

    bash tools/githook/nn-py-safe.sh -m pytest e2e/ -n 4 -q
    # 或：make test-e2e / python task.py test-e2e

（勿用裸 `python -m pytest`——AGENTS §0.1-13：沙箱删除守卫下会静默挂死。）
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
