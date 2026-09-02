"""loop_util —— train_loop 的单调工具（2026-09-02 从 train_loop.py 拆出）。

纯 stdlib 工具（无 torch 依赖，可被 train_loop.py 在任何 import 顺序下加载）：
状态行输出（_emit/_ts/_fmt_dur）、epoch 进度 Tee（_TrainTee）、心跳线程、
单实例锁（PID 文件）、语料自动导出（auto_export_corpus）。train_loop.py
保留 torch-thread 预解析 + main() 编排 + 纯函数 re-export（tests 引用
train_loop._fmt_dur / parse_val_loss_from_output）。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口反复弹出抢焦点。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

NN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(NN_ROOT)
NN_DEMO_DIR = os.path.join(REPO_ROOT, "nn-demo")
EXPORT_SCRIPT = os.path.join(REPO_ROOT, "tools", "replay", "export-observations.ts")

# A round that finishes in less than this many seconds with no val_loss is
# almost certainly a *spin* (train_bc exited early, crashed, or hit a
# PermissionError). Real rounds take minutes. We surface this loudly
# instead of silently continuing so the operator notices immediately.
SPIN_WARN_S = 60.0


def _fmt_dur(sec: float) -> str:
    """Human-readable duration: 45s, 3m20s, 1h05m."""
    sec = round(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _ts() -> str:
    """Current time as [HH:MM:SS]."""
    return time.strftime("%H:%M:%S")


def _emit(line: str, log) -> None:
    """Print to stdout (flush) AND append to the log file (flush).

    Every log line is prefixed with a timestamp [HH:MM:SS] so the operator
    can trace training progress and diagnose stalls without external tools."""
    print(line, flush=True)
    if log:
        try:
            log.write(f"[{_ts()}] {line}\n")
            log.flush()
        except Exception:
            pass


class _TrainTee:
    """Tee ``train_bc.train()`` stdout to the console AND the log file, and update
    epoch progress in ``state`` so the heartbeat reflects live training.

    Running training in-process means ``train()`` prints directly to
    ``sys.stdout``; swapping ``sys.stdout`` to this tee captures every
    ``[epoch N/M]`` line live (console + log) without a subprocess pipe that the
    launcher's double-spawn could spawn a bypassing, untracked trainer around.
    """

    _RE = re.compile(r"\[epoch\s+(\d+)/(\d+)\]")

    def __init__(self, log, state, real_stdout):
        self._log = log
        self._state = state
        self._out = real_stdout

    def write(self, s):
        if self._out:
            try:
                self._out.write(s)
                self._out.flush()
            except Exception:
                pass
        if self._log:
            try:
                # Prefix each line from train_bc with a timestamp.
                ts = _ts()
                for line in s.splitlines(True):
                    self._log.write(f"[{ts}] {line}")
                self._log.flush()
            except Exception:
                pass
        m = self._RE.search(s)
        if m:
            self._state["epoch_in_round"] = int(m.group(1))

    def flush(self):
        if self._out:
            try:
                self._out.flush()
            except Exception:
                pass
        if self._log:
            try:
                self._log.flush()
            except Exception:
                pass


def parse_val_loss_from_output(text: str) -> float | None:
    # The versioned archive name embeds _val<loss>.json. A round's output can
    # contain TWO such occurrences: the --resume path (older weights) and the
    # freshly written archive (this round's result). The LAST one is the
    # round's own result, so we prefer it over the resume value (otherwise the
    # loop would report the resume val and hide whether this round improved).
    matches = re.findall(r"_val([\d.]+)\.json", text)
    if matches:
        return float(matches[-1])
    m = re.search(r"val_loss=([\d.]+)", text)
    if m:
        return float(m.group(1))
    return None


def heartbeat(stop: threading.Event, interval: int, state: dict, log) -> None:
    """Daemon thread: print a progress line every `interval` seconds.

    Shows round/epoch progress AND a rolling estimate of round duration from
    recent history, so even between epochs the operator can see pace + ETA
    without inspecting the process."""
    while not stop.wait(interval):
        elapsed = time.time() - state["start"]
        ep = state["epochs_per_round"] or 1
        pct = int(100 * state["epoch_in_round"] / ep)
        eta = ""
        if state["round_durations"]:
            avg = sum(state["round_durations"]) / len(state["round_durations"])
            eta = f" avg_round={_fmt_dur(avg)}"
        line = (
            f"[loop] heartbeat t+{_fmt_dur(elapsed)} round={state['round']} "
            f"epoch {state['epoch_in_round']}/{state['epochs_per_round']} ({pct}%) "
            f"last_val={state['last_val']} last_round_done={state['last_round_done']}{eta}"
        )
        _emit(line, log)


def _pid_alive(pid: int) -> bool:
    """Cross-platform check: is a process with this PID still running?

    Catches ``Exception`` rather than specific types because Windows
    ``os.kill(pid, 0)`` can raise ``SystemError``, ``OSError``, or other
    unexpected exceptions depending on the PID / Python build / MSYS
    translation layer.  Any failure → treat as "not alive" so stale
    locks are always cleaned up."""
    try:
        os.kill(pid, 0)  # signal 0 = no signal sent, just permission check
        return True
    except Exception:
        return False


def _write_lock(lock_path: str) -> None:
    """Write our lock info: ``PID|EXE|START_TS``."""
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        payload = f"{os.getpid()}|{sys.executable}|{int(time.time())}"
        os.write(fd, payload.encode())
    finally:
        os.close(fd)


def _read_lock(lock_path: str) -> tuple[int | None, str | None, int | None]:
    """Read lock file → (pid, exe_path, start_ts).  Any parse error → Nones."""
    try:
        with open(lock_path) as f:
            raw = f.read().strip()
    except OSError:
        return None, None, None
    parts = raw.split("|")
    if len(parts) >= 3:
        try:
            return int(parts[0]), parts[1], int(parts[2])
        except (ValueError, IndexError):
            pass
    # Legacy format: bare PID integer.
    try:
        return int(raw), None, None
    except ValueError:
        return None, None, None


def acquire_lock(lock_path: str, *, force: bool = False) -> bool:
    """PID-file based single-instance lock with stale lock auto-cleanup.

    Lock file format: ``PID|EXE_PATH|START_TIMESTAMP`` (pipe-delimited).
    Legacy bare-PID files are also accepted for backward compat.

    When *force* is True, any existing lock is broken regardless of
    whether the holder is alive (operator-initiated restart).
    """
    # --- Force mode: destroy any existing lock first. ---
    if force:
        try:
            os.remove(lock_path)
        except OSError:
            pass

    # Try atomic create.
    try:
        _write_lock(lock_path)
        return True
    except FileExistsError:
        pass

    # File exists — check liveness.
    old_pid, old_exe, _ts_ = _read_lock(lock_path)

    if old_pid is not None and _pid_alive(old_pid):
        exe_note = f" ({old_exe})" if old_exe else ""
        print(
            f"[loop] another train_loop is running (PID {old_pid}{exe_note}); exiting.",
            flush=True,
        )
        return False

    # Stale lock — old process is dead. Clean up and retry.
    try:
        os.remove(lock_path)
    except OSError:
        pass

    try:
        _write_lock(lock_path)
        return True
    except FileExistsError:
        return False  # lost a race with another starter


def cleanup_lock(lock_path: str) -> None:
    """Remove our lock file on exit (best-effort, only if we own it)."""
    try:
        our_pid, _, _ = _read_lock(lock_path)
        if our_pid == os.getpid():
            os.remove(lock_path)
    except (ValueError, OSError):
        pass


def auto_export_corpus(data_dir: str, log=None) -> int:
    """Scan nn-demo/*.ndjson, export any new replays to *data_dir*.

    Returns the number of NDJSON files that were (re-)exported.
    Skips if nn-demo/ does not exist or contains no NDJSON files.
    Uses ``--skip-verify`` because human-recorded replays were already
    verified on first export; re-verification is redundant and slow.

    The export script is idempotent: re-exporting the same NDJSON file
    overwrites its shards with identical data.
    """
    ndjson_files = sorted(glob.glob(os.path.join(NN_DEMO_DIR, "*.ndjson")))
    if not ndjson_files:
        _emit("[export] no NDJSON files in nn-demo/ — skipping corpus export", log)
        return 0

    if not os.path.exists(EXPORT_SCRIPT):
        _emit("[export] WARNING: export-observations.ts not found — skipping", log)
        return 0

    n = len(ndjson_files)
    _emit(f"[export] exporting {n} NDJSON file(s) from nn-demo/ ...", log)
    t0 = time.time()

    # Find bun executable.  Pythonw.exe (no console) does not inherit the
    # Git Bash PATH, so shutil.which() may fail.  Fall back to common
    # Windows install locations.
    bun_exe = shutil.which("bun")
    if bun_exe is None:
        # npm global install: %APPDATA%/npm/node_modules/bun/bin/bun.exe
        appdata = os.environ.get("APPDATA", "")
        candidate = os.path.join(appdata, "npm", "node_modules", "bun", "bin", "bun.exe")
        if os.path.isfile(candidate):
            bun_exe = candidate
    if bun_exe is None:
        _emit("[export] WARNING: 'bun' not found — skipping corpus export", log)
        return 0

    cmd = [
        bun_exe,
        "tools/replay/export-observations.ts",
        "--skip-verify",
        "--out",
        data_dir,
        *ndjson_files,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min budget — 101 replays take ~9s
            **_POPEN_NO_WINDOW,
        )
        dt = time.time() - t0
        # Print last few lines of output (summary)
        lines = (result.stdout + result.stderr).strip().splitlines()
        for line in lines[-5:]:
            _emit(f"[export] {line}", log)
        if result.returncode != 0:
            _emit(f"[export] WARNING: export exited with code {result.returncode}", log)
        else:
            _emit(f"[export] done in {dt:.1f}s", log)
        return n
    except FileNotFoundError:
        _emit("[export] WARNING: 'bun' not found on PATH — skipping", log)
        return 0
    except subprocess.TimeoutExpired:
        _emit("[export] WARNING: export timed out after 300s — skipping", log)
        return 0
    except Exception as e:
        _emit(f"[export] WARNING: {e!r} — skipping", log)
        return 0
