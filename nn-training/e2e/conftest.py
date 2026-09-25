"""e2e conftest — 集成层（tests/ 单测层的下一层，同属 python-gate）。

层 = 路径（2026-09-15）：门禁与 CI 的目标是 `tests/` + `e2e/`（见
tools/githook/nn-python-gate.sh）——本目录自 60e5f69 起 hermetic（FakeServer +
tmp 落盘，不需要 bun / 真节点 / weights fixture），所以能进门禁。
只跑这一层：

    bash tools/githook/nn-py-safe.sh -m pytest e2e/ -n 4 -q
    # 或：make test-e2e / python task.py test-e2e

（勿用裸 `python -m pytest`——AGENTS §5：沙箱删除守卫下会静默挂死。）
夹具与 tests/conftest.py 共用（tmp_path 覆盖、通过即清、失败保留）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_serve_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """e2e 层不开本机长驻池（`--serve` 会起**真 bun** 常驻 worker，见 `remote/serve_pool.py`）。

    本层是 hermetic 的（FakeServer + tmp 落盘，不需要 bun；各用例把 `run_local_rollout`
    打成桩），所以 `make_local_pool` 直接返回 None、本机腿回到逐局 spawn（正是打桩的那条路）。

    ⚠ 必须挂在**夹具**上，不能在 conftest 模块级写 `os.environ`：门禁跑的是
    `pytest tests/ e2e/` **一次进程**，模块级 setenv 会把 `tests/` 里的池用例一并关掉
    （实测：三个真 bun 池用例全红，报「长驻 worker 池」不在日志里）。
    池的真东西由与真 bun 的用例钉：`tests/test_local_rollout_pool.py`（本机腿）、
    `tests/test_remote_iter_real_bun.py` / `test_remote_serve_pool.py`（节点腿）、
    `tests/state-init.test.ts`（serve ≡ 一次性，含起始分布）。显式 `NN_SERVE_POOL=1` 仍可覆盖。
    """
    monkeypatch.setenv("NN_SERVE_POOL", "0")

# 复用单测目录的 fixtures / session hooks（tmp_path 覆盖与清理语义一致）。
from tests.conftest import (
    bp_args,
    pytest_runtest_makereport,
    pytest_sessionfinish,
    tmp,
    tmp_path,
)
