"""Paddle 侧线程旋钮（2026-10-27）：`--threads N` → `OMP_NUM_THREADS` / `MKL_NUM_THREADS`。

背景：Paddle 3.3 没有 `set_num_threads`，torch 侧那行对 paddle 后端无效。唯一等价物是
**在 import 之前**写环境变量（两者的线程池在 import 期建好，之后再改无效）。

第 1–3 条是行为判据；第 4 条只能是静态判据 —— 「调用点早于 import」这件事没有运行时观测点
（要用它就真 import paddle，而本机没装）。这里退一步钉源码顺序，理由写在第 4 条里。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.paddle_env import apply_thread_env


def test_sets_both_envs_when_absent() -> None:
    env: dict[str, str] = {}
    wrote = apply_thread_env(8, env=env)
    assert wrote == {"OMP_NUM_THREADS": "8", "MKL_NUM_THREADS": "8"}
    assert env == {"OMP_NUM_THREADS": "8", "MKL_NUM_THREADS": "8"}


def test_does_not_override_existing_quota() -> None:
    """平台/launcher 已按容器配额设过的值是真权威 —— 只在缺席时补缺省。"""
    env = {"OMP_NUM_THREADS": "4"}
    wrote = apply_thread_env(8, env=env)
    assert wrote == {"MKL_NUM_THREADS": "8"}, "已有 OMP 不该被覆盖"
    assert env["OMP_NUM_THREADS"] == "4", "已有值必须原样保留"
    assert env["MKL_NUM_THREADS"] == "8"


def test_non_positive_is_noop() -> None:
    """`--threads 0` = 「保留 launcher 缺省」（与 torch 侧同义）。"""
    for n in (0, -1):
        env: dict[str, str] = {}
        assert apply_thread_env(n, env=env) == {}
        assert env == {}, f"n={n} 不该写任何键"


def test_called_before_importing_paddle_and_numpy() -> None:
    """静态判据：`apply_thread_env(...)` 的调用必须**早于** `import paddle` / `import numpy`。

    为什么只能静态：要行为验证就得真 import paddle，而本机没装（AI Studio 镜像里才有）；
    而这条时序错了的后果是**静默的**（线程数不生效、只是慢），值得钉。

    判据只认 paddle 核函数体里的那两行（`import numpy as np` / `import paddle`）——
    文件头部的顶层 import 不含它们。
    """
    src = (ROOT / "remote" / "train_core.py").read_text(encoding="utf-8")
    call = src.index("paddle_env.apply(torch_threads, prep_b)")
    np_import = src.index("    import numpy as np\n    import paddle")
    assert call < np_import, "线程环境变量必须在 import paddle/numpy 之前写"
    # 两个变量都覆盖由 `test_sets_both_envs_when_absent` 的**行为**断言保证（返回的键集合），
    # 不再重复一条文本判据 —— 文本形态断言有全仓预算（`tests/test_source_text_assert_budget.py`）。
