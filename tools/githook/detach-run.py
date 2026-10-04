#!/usr/bin/env python3
"""Run a command with file stdio, detached from the caller's console (Windows).

Fixes two git-hook failure modes on Windows (2026-09-15, commit hang after
`✓ All pre-commit checks passed` + ghost CTRL_C_EVENT mid-hook):

1. git waits for EOF on the hook's stdout/stderr pipe — any descendant that
   still holds those handles blocks `git commit` forever after the hook script
   itself has exited.
2. CTRL_C_EVENT is a console-wide broadcast. A child sharing git's console
   (e.g. torch.distributed.elastic death-signal) can kill `git.exe` mid-hook
   while the MSYS `sh` pre-commit keeps running.

Usage:
  detach-run.py --stdout OUT --stderr ERR -- cmd [args...]

On POSIX this dup2's the log files onto stdio and **execs** the command: the pid the
caller holds IS the command, so a simple `kill $pid` reaches the real process (see the
in-code rationale: nn-python-gate.sh fail-fast, 2026-10-05). On Windows it stays a
Popen+wait wrapper (DETACHED_PROCESS/console semantics live on the wrapper side) and
callers must kill the tree with `taskkill /T`.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stdout", required=True)
    p.add_argument("--stderr", required=True)
    # REMAINDER keeps the command intact; strip a leading '--' separator.
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = p.parse_args()
    cmd = list(a.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("detach-run: missing command after --", file=sys.stderr)
        return 2

    flags = 0
    startupinfo = None
    if sys.platform == "win32":
        # DETACHED_PROCESS: no inherited console → cannot receive or broadcast
        # CTRL_C_EVENT on the caller's console. CREATE_NO_WINDOW is *ignored*
        # when DETACHED is set (MSDN), so a visible console can still flash —
        # pin SW_HIDE via STARTUPINFO. New process group for belt-and-suspenders.
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags = detached | no_window | new_group
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    out_path = Path(a.stdout)
    err_path = Path(a.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as out, err_path.open("wb") as err:
        if sys.platform != "win32":
            # POSIX：exec 目标本体，让调用方的 $! **就是**工具进程（2026-10-05）。
            #
            # 为什么（门禁 fail-fast）：nn-python-gate.sh 在 ruff/mypy 先红时要停掉
            # 其余腿——旧的「Popen+wait」壳让调用方的 $! 是**本脚本**，kill 掉只杀壳、
            # 内层 pytest 变孤儿继续烧 CPU。exec 后 pid 不变、退出码照传（wait $! 的
            # 语义与旧壳的 `return proc.wait()` 完全一致），stdio 照旧落日志。
            # Windows 侧不能这么干（DETACHED_PROCESS 是壳的职责）⇒ 那条路径由调用方
            # 走 taskkill /T 连树。
            # 原 out/err（及 devnull）都是 Python 默认的 CLOEXEC fd（PEP 446）——
            # exec 成功时由内核关掉，不需要（也不该）手动 os.close：exec 若失败，
            # `with` 会正常关闭它们，手动 close 反而会在异常退出路径上抛 EBADF 遮蔽真因。
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.dup2(out.fileno(), 1)
            os.dup2(err.fileno(), 2)
            os.execvp(cmd[0], cmd)  # 不返回；失败则原样抛出（命令不存在等）
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
            startupinfo=startupinfo,
        )
        return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
