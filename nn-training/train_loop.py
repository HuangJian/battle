"""
train_loop.py — Continuous multi-round BC training (plan §NN-M1, user directive).

After the M1 sim evaluation, continues training on the full corpus across
rounds, automatically starting the next round when the previous one finishes
(until explicitly paused / killed). Each round fine-tunes from the latest
weights (auto-discovered) so training genuinely *continues* rather than
restarting from scratch.

Prints self-reporting status so the operator never has to poll processes /
memory / guess time:
  * a START banner with the PID,
  * a per-round line with a round-ETA estimate,
  * a per-epoch line with a rolling ETA to round end,
  * a heartbeat every `report_interval` seconds (default 600 = 10 min) with
    round/epoch progress + rolling avg round duration,
  * a loud WARNING if a round finishes in <60s with no val_loss (a *spin*),
  * a per-round done summary with val_loss and total rounds.

All status lines go to both stdout and train_loop.log via `_emit`.

Usage:
  python train_loop.py                       # infinite rounds, latest corpus
  python train_loop.py --rounds 5           # stop after 5 rounds
  python train_loop.py --epochs-per-round 60 --lr 1e-3
"""

from __future__ import annotations

import os
import sys

# Set torch thread env BEFORE any torch import (train_bc imports torch at module
# load). Pre-parse --torch-threads so the operator's choice is honored.
_TT = 12
for _i, _a in enumerate(sys.argv):
    if _a == "--torch-threads" and _i + 1 < len(sys.argv):
        try:
            _TT = int(sys.argv[_i + 1])
        except ValueError:
            pass
    elif _a.startswith("--torch-threads="):
        try:
            _TT = int(_a.split("=", 1)[1])
        except ValueError:
            pass
os.environ.setdefault("OMP_NUM_THREADS", str(_TT))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(_TT))
os.environ.setdefault("MKL_NUM_THREADS", str(_TT))

import argparse
import atexit
import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口反复弹出抢焦点。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Import train() IN-PROCESS. Because train_loop already holds a single-instance
# lock, running training here (instead of via a subprocess) removes the
# launcher's double-spawn race and keeps epoch stdout directly visible.
from train.bc import train as _train_bc

DEFAULT_DATA = os.path.join(HERE, "..", "tmp", "nn-export")
DEFAULT_WEIGHTS = os.path.join(HERE, "weights")

# A round that finishes in less than this many seconds with no val_loss is    # almost certainly a *spin* (train_bc exited early, crashed, or hit a
# PermissionError). Real rounds take minutes. We surface this loudly
# instead of silently continuing so the operator notices immediately.
SPIN_WARN_S = 60.0


def _fmt_dur(sec: float) -> str:
    """Human-readable duration: 45s, 3m20s, 1h05m."""
    sec = int(round(sec))
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


def latest_weights(weights_dir: str) -> str | None:
    sys.path.insert(0, HERE)
    from data.weights_io import latest_weights_path

    return latest_weights_path(weights_dir)


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
    old_pid, old_exe, _ts = _read_lock(lock_path)

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


# Repo root is one level above HERE (nn-training/).
REPO_ROOT = os.path.dirname(HERE)
NN_DEMO_DIR = os.path.join(REPO_ROOT, "nn-demo")
EXPORT_SCRIPT = os.path.join(REPO_ROOT, "tools", "replay", "export-observations.ts")


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous multi-round BC training")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--weights-dir", default=DEFAULT_WEIGHTS)
    ap.add_argument("--rounds", type=int, default=0, help="0 = run indefinitely")
    ap.add_argument("--epochs-per-round", type=int, default=40)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--mirror-p", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--report-interval", type=int, default=600, help="progress heartbeat seconds")
    ap.add_argument(
        "--torch-threads", type=int, default=12, help="OMP/OpenBLAS threads for training"
    )
    ap.add_argument(
        "--num-workers",
        type=int,
        default=0,
        help="DataLoader workers (parallel prefetch). NOTE: >0 uses Windows 'spawn' "
        "which hangs this training (workers fail to start); keep 0 on Windows.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Break any existing lock and start (for manual restart after a crash).",
    )
    ap.add_argument(
        "--no-auto-export",
        action="store_true",
        help="Disable auto-export of new NN replay corpus before each round.",
    )
    args = ap.parse_args()

    os.makedirs(args.weights_dir, exist_ok=True)

    # Single-instance guard: PID-file based, stale-lock auto-cleanup.
    # Anchor the lock to the SCRIPT directory (HERE), NOT to cwd-relative
    # args.weights_dir — the launcher may dispatch from different working dirs.
    lock_path = os.path.join(HERE, ".train_loop.lock")
    if not acquire_lock(lock_path, force=args.force):
        sys.exit(0)
    atexit.register(cleanup_lock, lock_path)

    # Also clean up on SIGTERM / SIGINT (Ctrl-C) so the lock is released
    # even if atexit doesn't run (e.g. killed by taskkill / task manager).
    def _signal_cleanup(signum, frame):
        cleanup_lock(lock_path)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_cleanup)
    signal.signal(signal.SIGINT, _signal_cleanup)

    active = os.path.join(args.weights_dir, "weights.json")
    log_path = os.path.join(args.weights_dir, "train_loop.log")
    log = open(log_path, "a", encoding="utf-8")

    state = {
        "start": time.time(),
        "round": 0,
        "epoch_in_round": 0,
        "epochs_per_round": args.epochs_per_round,
        "last_val": None,
        "last_round_done": None,
        "round_durations": [],
    }
    stop = threading.Event()
    hb = threading.Thread(
        target=heartbeat, args=(stop, args.report_interval, state, log), daemon=True
    )
    hb.start()

    _emit(
        f"[loop] START pid={os.getpid()} data={args.data_dir} weights={args.weights_dir} "
        f"rounds={'inf' if args.rounds == 0 else args.rounds} "
        f"epochs/round={args.epochs_per_round} lr={args.lr} torch_threads={args.torch_threads}",
        log,
    )

    # Track per-epoch progress by watching train_bc stdout lines.
    epoch_re = re.compile(r"\[epoch\s+(\d+)/(\d+)\]")

    try:
        while True:
            state["round"] += 1
            if args.rounds and state["round"] > args.rounds:
                break

            # Auto-export: scan nn-demo/ for new NDJSON replays and export
            # them to npy shards before training.  Takes ~9s for 101 replays.
            if not getattr(args, "no_auto_export", False):
                auto_export_corpus(args.data_dir, log)

            prev = latest_weights(args.weights_dir)
            resume_arg = ["--resume", prev] if prev and os.path.exists(prev) else []
            note = f"round {state['round']}" + (
                f" resume {os.path.basename(prev)}" if resume_arg else " from-scratch"
            )

            # ---- In-process training (no subprocess / lock / launcher double-spawn) ----
            # train_loop already holds a single-instance lock, so exactly one
            # training runs. Running train_bc.train() IN THIS PROCESS means epoch
            # stdout flows straight to the console + log (live + captured) and
            # there is no second, untracked trainer spawned by the launcher that
            # we cannot see — which was the root cause of the silent "spin".
            bc_args = argparse.Namespace(
                data_dir=args.data_dir,
                out=active,
                epochs=args.epochs_per_round,
                lr=args.lr,
                batch=args.batch,
                val_split=args.val_split,
                mirror_p=args.mirror_p,
                seed=args.seed,
                num_workers=args.num_workers,
                resume=prev,
                notes=note,
                checkpoint=None,
            )

            eta = ""
            if state["round_durations"]:
                avg = sum(state["round_durations"]) / len(state["round_durations"])
                eta = f" (avg {_fmt_dur(avg)}/round, eta to round-end ~{_fmt_dur(avg)})"
            _emit(f"\n[loop] === ROUND {state['round']} === resume={prev} ==={eta}", log)

            t0 = time.time()
            real_stdout = sys.stdout
            tee = _TrainTee(log, state, real_stdout)
            sys.stdout = tee
            val = None
            crashed = False
            try:
                result = _train_bc(bc_args)
                val = result.get("best_val_loss")
            except Exception as e:  # never let one bad round kill the loop
                import traceback as _tb

                crashed = True
                _emit(f"[loop] !! round {state['round']} CRASHED: {e!r}", log)
                if log:
                    try:
                        log.write(_tb.format_exc() + "\n")
                        log.flush()
                    except Exception:
                        pass
            finally:
                sys.stdout = real_stdout
            dt = time.time() - t0
            if val is not None:
                state["last_val"] = val
            state["last_round_done"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            state["round_durations"].append(dt)
            # Spin detection: a round that ends in seconds with no val_loss is
            # almost certainly a crash (not a real ~25 min training round).
            if dt < SPIN_WARN_S and (val is None or crashed):
                _emit(
                    f"[loop] !! WARNING: round {state['round']} finished in {dt:.1f}s "
                    f"with val_loss={val} — likely a SPIN/CRASH (no real training). "
                    f"NOT auto-stopping; watch the next round.",
                    log,
                )
            done_line = (
                f"[loop] round {state['round']} done in {_fmt_dur(dt)} "
                f"val_loss={val} active={os.path.basename(active)} "
                f"total_rounds={state['round']}"
            )
            _emit(done_line, log)
            # After a round, verify the active pointer is the newest (train_bc copies it).
    except KeyboardInterrupt:
        print("[loop] interrupted by operator", flush=True)
    finally:
        stop.set()
        if log:
            log.close()
        print(
            f"[loop] stopped after {state['round']} round(s); last_val={state['last_val']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
