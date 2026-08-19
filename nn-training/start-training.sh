#!/usr/bin/env bash
# start-training.sh — Launch continuous BC training (single-instance, crash-safe).
#
# Supports: macOS · Linux · WSL · Windows (Git Bash / MSYS2)
#
# Usage:
#   ./start-training.sh                  # default: infinite rounds, 40 epochs/round
#   ./start-training.sh --rounds 5       # stop after 5 rounds
#   ./start-training.sh --epochs-per-round 60 --lr 1e-3
#   ./start-training.sh --force          # break stale lock and start
#
# Lock policy:
#   The shell does NOT manage the lock file — train_loop.py does.
#   The Python process manages its own lock (PID|EXE|TS format) via
#   acquire_lock() / cleanup_lock().  No shell-level lock is written,
#   eliminating the shell-PID / Python-PID mismatch that caused
#   double-spawn on Windows.
#
# Windows note:
#   On Git Bash, neither `exec` nor `pythonw.exe &` reliably detaches
#   a long-running process.  We use a companion batch file
#   (launch-training.bat) that calls `start ""` to create a detached
#   console process.  cmd.exe //C (double-slash) prevents MSYS path
#   mangling.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
TRAIN_LOOP="$SCRIPT_DIR/train_loop.py"
LOCK_FILE="$SCRIPT_DIR/.train_loop.lock"

# ── Detect platform ────────────────────────────────────────────────────

IS_WINDOWS=false
if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]] || [[ -n "${WINGET_EXE:-}" ]]; then
  IS_WINDOWS=true
fi

# Venv python path: Windows = Scripts/python.exe, Unix = bin/python
if $IS_WINDOWS; then
  VENV_PYTHON="${VENV_DIR}/Scripts/python.exe"
  PIP_FLAGS="PYTHONUTF8=1"
else
  VENV_PYTHON="${VENV_DIR}/bin/python"
  PIP_FLAGS=""
fi

# ── Helpers ────────────────────────────────────────────────────────────

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── 1. Pre-flight: reject if lock holder is alive ──────────────────────
#    FAST check — skip venv setup if training is already running.
#    Stale locks are NOT cleaned here; Python's acquire_lock() handles that.

FORCE=false
for arg in "$@"; do
  [[ "$arg" == "--force" ]] && FORCE=true
done

if ! $FORCE; then
  if [ -f "$LOCK_FILE" ]; then
    # Extract PID from lock file (new: PID|EXE|TS, legacy: bare PID)
    raw=$(cat "$LOCK_FILE" 2>/dev/null) || true
    old_pid="${raw%%|*}"
    if [[ "$old_pid" =~ ^[0-9]+$ ]]; then
      if kill -0 "$old_pid" 2>/dev/null; then
        log "Another training is running (PID $old_pid). Exiting."
        exit 0
      fi
      # Stale lock — let Python clean it up via acquire_lock()
    fi
  fi
fi

# ── 2. Create venv if missing ──────────────────────────────────────────

ensure_venv() {
  [ -f "$VENV_PYTHON" ] && return 0

  log "Creating virtual environment in $VENV_DIR ..."
  local py=""
  py=$(command -v python3 || command -v python || true) || {
    log "ERROR: No Python 3 found. Install Python 3.10+ and try again."
    exit 1
  }
  log "Using Python: $py ($($py --version 2>&1))"
  "$py" -m venv "$VENV_DIR"

  log "Installing dependencies ..."
  eval "$PIP_FLAGS" "$VENV_PYTHON" -m pip install --upgrade pip -q 2>/dev/null
  eval "$PIP_FLAGS" "$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q
  log "Setup complete."
}

# ── 3. Environment ─────────────────────────────────────────────────────

ensure_venv

export PYTHONUTF8=1
export OMP_NUM_THREADS=12
export OPENBLAS_NUM_THREADS=12
export MKL_NUM_THREADS=12

# ── 4. Launch ──────────────────────────────────────────────────────────

log "Starting training ..."
log "  platform: $(uname -s)"
log "  python:   $VENV_PYTHON"
log "  args:     ${*:-<defaults>}"
log "  lock:     $LOCK_FILE (managed by train_loop.py)"
log "  Ctrl-C to stop"

if $IS_WINDOWS; then
  # Windows: use launch-training.bat which calls `start ""` for proper
  # detachment.  cmd.exe //C (double-slash) prevents MSYS path conversion.
  # The batch file uses absolute paths to avoid CWD issues.
  # VBS wrapper: WshShell.Run with windowStyle=0 (hidden) + waitOnReturn=False
  # is the only reliable way to detach a process from Git Bash on Windows.
  wscript //B "$(cygpath -w "$SCRIPT_DIR/launch-training.vbs")"
  # Wait for the lock file to appear (up to 15s).
  # VBS returns instantly but Python needs a few seconds to import torch
  # and call acquire_lock().  Without this wait, a second start-training.sh
  # invocation would pass the pre-flight check and spawn a duplicate.
  for _w in $(seq 1 30); do
    [ -f "$LOCK_FILE" ] && break
    sleep 0.5
  done
else
  # Unix: exec replaces this shell — clean signal handling, no zombie parent.
  exec "$VENV_PYTHON" "$TRAIN_LOOP" "$@"
fi
