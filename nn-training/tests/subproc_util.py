"""subproc_util — 测试侧 subprocess 统一出口：显式 utf-8 解码。

裸 ``subprocess.run(..., text=True)`` 的解码端编码 = ``locale.getpreferredencoding(False)``
（解释器**启动期**决定，运行时改不了；zh-CN Windows = cp936，coding agent 沙箱间
PYTHONUTF8 / PYTHONIOENCODING 又各异）。子进程按其它编码输出时——agent 沙箱常设
``PYTHONIOENCODING=utf-8``、bun 管道恒 UTF-8、Python 3.15 起（PEP 686）默认 UTF-8——
CPython 读线程在 ``subprocess._readerthread`` 里解码失败死亡 → ``stdout=None`` →
远处的 ``json.loads(None)`` 抛 TypeError。跨 agent/沙箱唯一确定的做法：

  * 子侧：CLI 入口 ``platform_utils.force_utf8_stdio()`` 把字节流钉成 UTF-8；
  * 父侧：本 helper 显式 ``encoding="utf-8"`` 解码，并对 ``stdout is None``
    （读线程死亡的唯一痕迹）就地报错，而不是让它漏到远处的 TypeError。

背景与复现矩阵：docs/nn.progress.md §30（2026-09-13，``test_cli_dry_run_exit_code``
在 PYTHONIOENCODING=utf-8 且无 PYTHONUTF8 的机器上必红）。
"""

from __future__ import annotations

import subprocess
from typing import Any


def run_utf8(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` 的 UTF-8 合约版（测试专用）。

    强制 ``encoding="utf-8"``（隐含 text 模式）；其余 kwargs 透传（cwd/timeout/
    env/**_POPEN_NO_WINDOW 等）。默认 ``capture_output=True``——读线程死亡时
    stdout 会是 ``None``，这里就地断言并附上 stderr，把「编码不匹配」从远处
    ``json.loads(None)`` 的 TypeError 变成本 helper 里的明确报错。
    """
    assert not ("text" in kw or "universal_newlines" in kw or "encoding" in kw), (
        "run_utf8 自带 encoding='utf-8'——不要与 text/encoding 混传（见模块 docstring）"
    )
    kw.setdefault("capture_output", True)
    out = subprocess.run(cmd, encoding="utf-8", errors="strict", **kw)
    assert out.stdout is not None, (
        f"[run_utf8] stdout 未捕获（子进程输出解码失败/读线程死亡？）stderr={out.stderr!r}"
    )
    return out
