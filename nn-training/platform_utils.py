"""
platform_utils.py — 跨平台进程/文件工具（工程化抽取，行为零变化）。

历史：train_bc.py / train_loop.py / run_rl.py / run_rl_intent.py / rl/eval_dispatch.py /
rl/queue.py 各自维护了一份逐字节相同的 `_POPEN_NO_WINDOW`（Windows 下隐藏子进程
控制台窗口，避免黑窗弹窗抢焦点）。本模块统一这一份，其余文件改 import。

导出：
  POPEN_NO_WINDOW —— subprocess.run/Popen 的额外 kwargs（Windows 下含
    creationflags=CREATE_NO_WINDOW；非 Windows 为空 dict）。
  popen_kwargs(**extra) —— 便捷包装：返回 {**POPEN_NO_WINDOW, **extra}。
  rmtree_best_effort(path, ignore_errors=False) —— 递归删除目录，**沙箱删除保护
    （SystemExit）绝不外泄**；替代裸 shutil.rmtree / ignore_errors=True。
  sandbox_delete_blocked(anchor) —— 探针：当前是否正被沙箱删除保护拦截真实删除
    （门禁抖动归因用，见 docstring）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口弹出抢焦点。
POPEN_NO_WINDOW: dict[str, Any] = {}
if sys.platform == "win32":
    POPEN_NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def rmtree_best_effort(path: Any, *, ignore_errors: bool = False) -> bool:
    """递归删除 ``path``；返回值 True=已删或本就不存在，False=未删掉。

    **为什么不能直接用 shutil.rmtree(..., ignore_errors=True)**（2026-09-10 门禁事故）：
    本沙箱的 WorkBuddy 删除保护 shim 把 ``shutil.rmtree`` 改道回收站，批量删除守卫
    触发时 ``raise SystemExit(1)`` —— 那是 ``BaseException``，**不是** ``Exception``，
    所以 ``ignore_errors=True`` 完全挡不住它，调用线程会当场被打死。
    实证：门禁两条用例同源失败——``rl/dispatch.py`` 的派发线程在 shard 清理处被
    SystemExit 打死，此后该线程不再派发任何任务，于是「慢任务仍 in-flight 时被空闲槽
    重赛」的竞态日志永远不出现，``test_it_early_race_v314`` 的 raced 断言随之失败。

    语义：
      * ``ignore_errors=False``（默认）：真的删不掉（OSError 等）照旧抛出 —— 与
        ``shutil.rmtree`` 一致，只额外吞掉删除保护的 SystemExit。
      * ``ignore_errors=True``：任何异常都不外泄（等价 shutil 的 ignore_errors，
        外加 SystemExit 防护）。
      * ``KeyboardInterrupt`` 一律照常传播。

    所有清理路径都应当走本函数：删不掉只是泄漏一个临时目录，绝不该杀掉线程或训练。
    """
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return True
    except SystemExit:
        # 沙箱删除保护拦截：目录保留，调用方按 best-effort 继续。
        return False
    except Exception:
        if ignore_errors:
            return False
        raise
    return True


def sandbox_delete_blocked(anchor: Any) -> bool:
    """探针：当前环境是否正在拦截真实删除（沙箱 safe-delete 配额耗尽）。

    2026-09-12 门禁抖动根因：WorkBuddy safe-delete shim 按 turn 计批量删除配额
    （阈值 50，见 tools/githook/nn-python-gate.sh 头注）；同一会话反复跑全量门禁
    必然踩满，此后所有真实删除被拒并抛 SystemExit。rmtree_best_effort 把它转成
    False（best-effort），于是「断言删除落地」的测试（test_workdir_sweep /
    prune_job_dirs）转红——单跑（配额新鲜）又变绿，表现为偶发（实证：
    tmp/pre-commit-nn-*.log，assert 1 == 2 + [safe-delete] 行）。

    约定：**仅在删除断言已失败的路径调用**（绿路径零开销、零配额消耗）。在 anchor
    下建一个探针目录并用裸 shutil.rmtree 删除——SystemExit = 正在被拦 → True；
    删得掉 / OSError → False（失败是真回归，调用方测试应继续红）。必须用裸
    rmtree：rmtree_best_effort 会吞掉 SystemExit，探针就失灵了。
    """
    from pathlib import Path

    probe = Path(anchor) / "_shim_probe"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(probe)
    except SystemExit:
        return True
    except Exception:
        return False
    return False


def popen_kwargs(**extra: Any) -> dict[str, Any]:
    """subprocess 调用 kwargs：始终带上无窗口 flags，并合并调用方参数。

    ``subprocess.run(cmd, ..., **popen_kwargs(capture_output=True))`` 等价于旧的
    ``subprocess.run(cmd, ..., **_POPEN_NO_WINDOW, capture_output=True)``。
    """
    return {**POPEN_NO_WINDOW, **extra}
