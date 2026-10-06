"""e2e conftest — 集成层（tests/ 单测层的下一层，同属 python-gate）。

层 = 路径（2026-09-15）：门禁与 CI 的目标是 `tests/` + `e2e/`（见
tools/githook/nn-python-gate.sh）——本目录自 60e5f69 起 hermetic（FakeServer +
tmp 落盘，不需要 bun / 真节点 / weights fixture），所以能进门禁。
只跑这一层：

    bash tools/githook/nn-py-safe.sh -m pytest e2e/ -n 4 -q
    # 或：make test-e2e / python tools/task.py test-e2e

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
    """e2e 层不开本机长驻池（`--serve` 会起**真 bun** 常驻 worker，见 `worker/serve_pool.py`）。

    本层是 hermetic 的（FakeServer + tmp 落盘，不需要 bun；各用例把 `run_local_rollout`
    打成桩），所以 `make_local_pool` 直接返回 None、本机腿回到逐局 spawn（正是打桩的那条路）。

    ⚠ 必须挂在**夹具**上，不能在 conftest 模块级写 `os.environ`：门禁跑的是
    `pytest tests/ e2e/` **一次进程**，模块级 setenv 会把 `tests/` 里的池用例一并关掉
    （实测：三个真 bun 池用例全红，报「长驻 worker 池」不在日志里）。
    池的真东西由与真 bun 的用例钉：`tests/worker/test_local_rollout_pool.py`（本机腿）、
    `tests/worker/test_remote_iter_real_bun.py` / `test_remote_serve_pool.py`（节点腿）、
    `tests/state-init.test.ts`（serve ≡ 一次性，含起始分布）。显式 `NN_SERVE_POOL=1` 仍可覆盖。
    """
    monkeypatch.setenv("NN_SERVE_POOL", "0")

# 复用单测目录的 fixtures / session hooks（tmp_path 覆盖与清理语义一致）。
from tests.conftest import (
    bp_args,
    pin_production_env,
    pytest_runtest_makereport,
    pytest_sessionfinish,
    tmp,
    tmp_path,
)


@pytest.fixture(autouse=True)
def _production_isolation(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """e2e 层的隔离（**必须两份**：e2e 是兄弟目录，不继承 `tests/conftest.py` 的夹具）。

    2026-10-06 两起事故都在本层：① 本目录起的真 `hub.server` 子进程（`test_auto_handoff_e2e.py`
    的 `_Hub`）有一半没传 `console_url` ⇒ 控制台地址回落到 `hub/task_pack.DEFAULT_CONSOLE_URL`
    （`127.0.0.1:8900` = 开发机上正在跑的 dashboard，而那个动作会写本机配置）；
    ② `test_cloud_iter_e2e.py` 跑真 `TrainingLoop` ⇒ 心跳（`EVALBOARD_DATA` 没钉）写进了
    `dashboard/data/evalboard/runner_state.json`（操作员面板读它）。

    钉名单与实现在**一处**（`tests/conftest.py::pin_production_env` / `PRODUCTION_STATE_PINS`）；
    本层只负责挂上。守卫 `tests/test_production_isolation.py`（两层都挂没挂由它钉）。

    `tmp_path` 走 `request.getfixturevalue` 而不是形参：本模块顶部 import 了同名夹具函数
    （extend 语义），形参会撞 ruff F811（2026-10-06 门禁实测）。
    """
    pin_production_env(monkeypatch, request.getfixturevalue("tmp_path"))
