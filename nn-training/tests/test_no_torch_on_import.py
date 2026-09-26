"""collect-only 路径零 torch 守护（plan/python-refactor.md B7 / P1-11）。

**为什么需要**：`run_rl.py --collect-only` 是双缓冲预采子进程（每轮一个），只采样
不训练。此前 run_rl.py 顶层 `import torch` + ppo.* + models.*，子进程白白支付
3~8s 的 torch 加载（CPU），且与 run_rl.py:912 的注释「子进程无需 torch」矛盾。

**红线**：`import run_rl`（模块级）不得拉起 torch / numpy。若未来有人把 torch
import 加回顶层，本测试立即变红——子进程每轮多付 3-8s 且回归"注释撒谎"状态。

验证方式：全新子进程（避免本进程已缓存 torch）import run_rl 后检查 sys.modules。
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from tests.subproc_util import run_utf8

ROOT = Path(__file__).resolve().parent.parent


def _run_probe() -> str:
    """在干净子进程中 import run_rl，报告 torch/numpy 是否被加载。"""
    code = textwrap.dedent(
        """
        import sys
        import run_rl  # noqa: F401 — 仅验证模块级导入链
        print("torch=" + str("torch" in sys.modules))
        print("numpy=" + str("numpy" in sys.modules))
        print("ppo.engine=" + str("ppo.engine" in sys.modules))
        print("rl.modes=" + str("rl.modes" in sys.modules))
        print("rl.stream=" + str("rl.stream" in sys.modules))
        # rl.stream 显式导入后仍不得拉起 ppo.engine（其默认后端已延迟导入）
        import rl.stream  # noqa: F401
        print("torch-after-stream=" + str("torch" in sys.modules))
        """
    )
    out = run_utf8([sys.executable, "-c", code], cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, f"probe failed: {out.stderr[-2000:]}"
    return out.stdout


def test_import_run_rl_does_not_load_torch() -> None:
    """B7 红线：模块级 import 链零 torch（collect-only 子进程不付 torch 加载）。"""
    stdout = _run_probe()
    kv = dict(line.split("=") for line in stdout.splitlines() if "=" in line)
    assert kv.get("torch") == "False", f"import run_rl 触发了 torch 加载:\n{stdout}"
    assert kv.get("numpy") == "False", f"import run_rl 触发了 numpy 加载:\n{stdout}"
    assert kv.get("ppo.engine") == "False", f"import run_rl 触发了 ppo.engine（torch 链）:\n{stdout}"
    # rl.stream 顶层也不得拉起 ppo.engine（其默认后端已延迟导入）
    # 注：run_rollout_stream 的引用已下沉到 main()，模块级本就不该加载 rl.stream；
    # 显式导入它之后 torch 仍必须保持未加载。
    assert kv.get("rl.stream") == "False", f"模块级不应加载 rl.stream:\n{stdout}"
    assert kv.get("torch-after-stream") == "False", f"导入 rl.stream 触发了 torch:\n{stdout}"


def test_modes_import_does_not_load_torch() -> None:
    """rl.modes 顶层不再 import ppo.*（get_backend 延迟）——run_rl 之外的引用方同样受益。

    映射完整性用 **`importlib.util.find_spec`** 验，不用 `import_module`（2026-09-26）：
    `find_spec("ppo.engine")` 只解析模块**路径**（顺带 import 父包 `ppo`，而它是 PEP 562
    惰性的、零 torch），不执行 `ppo/engine.py` ⇒ 这条断言在**没有 torch 的机器上**也成立。
    此前用 `import_module` 验「延迟后端可解析」，结果是「torch 缺失 ⇒ 本用例必红」，而它想
    证明的只是「注册表指向真实存在的模块」。
    """
    code = textwrap.dedent(
        """
        import importlib.util
        import sys
        from rl.modes import _MODE_BACKEND_NAMES
        print("torch=" + str("torch" in sys.modules))
        assert set(_MODE_BACKEND_NAMES) == {"per-tick", "intent", "goal"}
        specs = [n for n in _MODE_BACKEND_NAMES.values() if importlib.util.find_spec(n) is None]
        print("unresolved=" + str(specs))
        print("torch-after-spec=", "torch" in sys.modules, sep="")
        """
    )
    out = run_utf8([sys.executable, "-c", code], cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, f"modes probe failed: {out.stderr[-2000:]}"
    kv = dict(line.split("=") for line in out.stdout.splitlines() if "=" in line)
    assert kv.get("torch") == "False"
    # 三个后端模块都能被解析到（注册表没写错名字），且解析本身不拖 torch
    assert kv.get("unresolved") == "[]", f"延迟后端注册表指向了不存在的模块: {out.stdout}"
    assert kv.get("torch-after-spec") == "False", f"find_spec 拖进了 torch:\n{out.stdout}"
