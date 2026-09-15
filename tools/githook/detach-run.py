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

On non-Windows this is a thin Popen+wait with the same stdio redirection.
"""

from __future__ import annotations

import argparse
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
