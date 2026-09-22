"""test_eval_local_capture — 本机评估子进程的输出捕获契约（2026-09-22 回归测试）。

缺口：`run_local_eval_game` 的 `subprocess.run(..., text=True)` 没给 encoding —— win32 中文机
locale = gbk（cp936），而 bun 的输出带 UTF-8 字节 ⇒ 解码在 `subprocess` 的 **reader 线程**
里抛 UnicodeDecodeError：
  ① 父进程日志被 `Exception in thread ... _readerthread` traceback 刷屏（evalA 实测 65 次/100 局）；
  ② **captured stdout/stderr 直接丢成 None**（异常死在读线程，`communicate` 不重抛）
     ⇒ 失败时的 `RuntimeError(f"rc={rc} ({stderr[-160:]})")` 只剩 rc，诊断信息全没。

本文件用**真子进程**写不可解码字节复现（不是读源码断言）：解码必须成功、输出必须还在。
"""

from __future__ import annotations

import subprocess
import sys

from rl.eval_local import run_eval_runner_capture

#: 故意混字节：UTF-8 的中文 + 单独的 0xaf（在 gbk 与 utf-8 下都不是合法序列）。
_CHILD = (
    "import sys\n"
    "sys.stdout.buffer.write(b'ok-\\xe4\\xb8\\xad\\xe6\\x96\\x87-\\xaf\\n')\n"
    "sys.stderr.buffer.write(b'boom-\\xe4\\xb8\\xad\\xe6\\x96\\x87-\\xaf\\n')\n"
    "sys.exit(3)\n"
)


def test_capture_decodes_utf8_and_keeps_output() -> None:
    """回归：不可解码字节不得变成「输出丢成 None」+ 日志刷屏。"""
    proc = run_eval_runner_capture([sys.executable, "-c", _CHILD], 30.0)

    assert proc.returncode == 3
    # ① 输出必须还在（旧实现两条都是 None：异常死在 reader 线程）
    assert proc.stdout is not None and proc.stderr is not None
    assert "ok-" in proc.stdout
    assert "boom-" in proc.stderr
    # ② 坏字节走 errors="replace"，不抛（UTF-8 的部分正常解出中文）
    assert "中文" in proc.stdout
    assert "\ufffd" in proc.stderr  # 0xaf 变成替换符，而不是异常


def test_capture_helper_is_what_the_runner_uses() -> None:
    """接线：本机局的输出捕获必须走这个 helper（否则 encoding 又悄悄丢了）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "rl" / "eval_local.py").read_text(
        encoding="utf-8"
    )
    assert "proc = run_eval_runner_capture(cmd, timeout_sec)" in src
    # 不许再有裸的 text=True 捕获（那正是缺陷形态）
    assert "capture_output=True,\n        text=True,\n        timeout=timeout_sec" not in src


def test_capture_is_single_subprocess_invocation() -> None:
    """helper 本身只 spawn 一次（别把重试/缓冲塞进来）。"""
    proc = run_eval_runner_capture([sys.executable, "-c", "print('hi')"], 30.0)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.stdout.strip() == "hi"
