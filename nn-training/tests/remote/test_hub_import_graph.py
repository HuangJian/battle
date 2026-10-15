"""hub 进程的 import 闭包**不含** numpy/torch —— 在零三方包的环境里也能起来。

hub 只是队列/租约 broker：reward 由 trainer 在打包端算（`remote.payload_slim`），
delta 重建由 trainer 落位时做（`verify_and_land` 的两个 `_land_*_delta`）。hub 之所以会碰
numpy/torch，纯粹是 import 图的连坐 —— `hub.queue_observe(L3) → remote.code_snapshot(L2)`
只借 `pack_code_zip`/`pack_ts_code_zip`，而 `remote.code_snapshot → remote.hub_client(L1)` 又在
**顶层**把两个容器模块（`remote.weights_delta` 吃 numpy、`remote.opt_delta` 吃 torch）拉了进来。

判据只能起**独立解释器**跑（本进程的 `sys.modules` 早被测试套件污染），且只挂 `nn-training/`
（= hub-server 在 dashboard 下的真实形状：`venv` 的 site-packages 不在 PYTHONPATH 上）。
`sys.modules["numpy"/"torch"] = None` 让任何 import 它们当场炸 —— 那是「本机没装三方包」的
等价物（2026-10-14 事故：hub-server 因顶层 `import numpy` 启动即退）。

两条断言（任一不符即红）：
  ① `import hub.server` 成功（hub 能在没装 numpy/torch 的环境里起来）；
  ② `remote.weights_delta` / `remote.opt_delta` / `remote.payload_slim` **不在** `sys.modules`
     （hub 的闭包不碰这些吃 numpy/torch 的模块）。

② 是本次改动的直接判据：改前 `remote.hub_client` 顶层 import 两个容器模块 ⇒ 它们在闭包里 ⇒ 红；
改后（收进函数体）⇒ 不在 ⇒ 绿。死的正是 2026-10-14 那条事故链。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 子进程的全部可见面 = `nn-training/`（`parents[2]`：本文件在 `nn-training/tests/remote/` 下）。
#: ⚠ 不能写 `parents[1]`：那是 `nn-training/tests/`，而它下面也有一个 `tests/hub/` 包 ⇒ PYTHONPATH
#: 会把真 `hub` 遮住，子进程报「No module named 'hub.server'」（测试恒红，与 hub 的 import 图无关）。
ROOT = Path(__file__).resolve().parents[2]

#: 子进程里跑：把重依赖悬空，再拉整个 hub 入口（hub → boot → http_face → queue →
#: queue_observe → code_snapshot → hub_client 这条真实链）。
_CHILD = """import sys
for _m in ("numpy", "torch"):
    sys.modules[_m] = None  # 缺 numpy/torch 的真实环境：任何 import 它们都当场炸
import hub.server  # noqa: F401
assert "numpy" not in sys.modules or sys.modules["numpy"] is None
for _m in ("remote.weights_delta", "remote.opt_delta", "remote.payload_slim"):
    assert _m not in sys.modules, f"hub 的 import 闭包不该含 {_m}（它吃 numpy/torch）"
"""


def test_hub_import_graph_needs_no_numpy_or_torch() -> None:
    """★ hub-server 的 import 闭包在**没有 numpy/torch** 的解释器里也必须完好。"""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True, env=env, timeout=120
    )
    assert r.returncode == 0, r.stderr
