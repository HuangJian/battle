"""
platform_utils.py — 跨平台进程/文件工具（工程化抽取，行为零变化）。

历史：train_bc.py / train_loop.py / run_rl.py / run_rl_intent.py / rl/eval_dispatch.py /
rl/queue.py 各自维护了一份逐字节相同的 `_POPEN_NO_WINDOW`（Windows 下隐藏子进程
控制台窗口，避免黑窗弹窗抢焦点）。本模块统一这一份，其余文件改 import。

导出：
  POPEN_NO_WINDOW —— subprocess.run/Popen 的额外 kwargs（Windows 下含
    creationflags=CREATE_NO_WINDOW；非 Windows 为空 dict）。
  popen_kwargs(**extra) —— 便捷包装：返回 {**POPEN_NO_WINDOW, **extra}。
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口弹出抢焦点。
POPEN_NO_WINDOW: Dict[str, Any] = {}
if sys.platform == "win32":
    POPEN_NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def popen_kwargs(**extra: Any) -> Dict[str, Any]:
    """subprocess 调用 kwargs：始终带上无窗口 flags，并合并调用方参数。

    ``subprocess.run(cmd, ..., **popen_kwargs(capture_output=True))`` 等价于旧的
    ``subprocess.run(cmd, ..., **_POPEN_NO_WINDOW, capture_output=True)``。
    """
    return {**POPEN_NO_WINDOW, **extra}
