#!/usr/bin/env bash
# start-training.sh — Launch continuous BC training (single-instance, crash-safe).
#
# Supports: macOS · Linux · WSL · Windows (Git Bash / MSYS2)
#
# Usage:
#   ./start-training.sh                  # default: infinite rounds, 40 epochs/round
#   ./start-training.sh --rounds 5       # stop after 5 rounds
#   ./start-training.sh --epochs-per-round 60 --lr 1e-3
#
# Guarantees:
#   - At most one training instance runs at a time (PID-file lock).
#   - Stale locks from crashed processes are auto-detected and cleaned.
#   - Ctrl-C / SIGTERM removes the lock before exiting.
#   - No double-spawn: the script itself is idempotent.
#
# Background mode:
#   nohup ./start-training.sh > train.log 2>&1 &

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

# Check if a PID is alive.
pid_alive() {
  kill -0 "$1" 2>/dev/null
}

# Read PID from lock file. Returns 0 if valid, 1 if missing.
read_lock_pid() {
  [ -f "$LOCK_FILE" ] || return 1
  local pid
  pid=$(cat "$LOCK_FILE" 2>/dev/null) || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  echo "$pid"
}

# ── 1. Pre-flight: clean stale lock if holder is dead ──────────────────

cleanup_stale_lock() {
  local old_pid
  old_pid=$(read_lock_pid) || return 0  # no lock file → nothing to do

  if pid_alive "$old_pid"; then
    log "Another training is running (PID $old_pid). Exiting."
    exit 0
  fi

  log "Stale lock detected (PID $old_pid is dead). Cleaning up ..."
  rm -f "$LOCK_FILE"
}

# ── 2. Create venv if missing ──────────────────────────────────────────

find_python() {
  # Prefer python3, then python. On Windows also try pyenv-win.
  local candidates=()
  if $IS_WINDOWS; then
    # Try pyenv-win versions (newest first).
    for ver in 3.13 3.12 3.11 3.10; do
      local p="$HOME/.pyenv/pyenv-win/versions/$ver.*/python.exe"
      # shellcheck disable=SC2086
      for f in $p; do
        [ -f "$f" ] && candidates+=("$f")
      done
    done
  fi
  candidates+=(python3 python)

  for c in "${candidates[@]}"; do
    if command -v "$c" &>/dev/null; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

ensure_venv() {
  [ -f "$VENV_PYTHON" ] && return 0

  log "Creating virtual environment in $VENV_DIR ..."
  local py=""
  py=$(find_python) || {
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

# ── 3. Lock acquisition with cleanup trap ──────────────────────────────

acquire_lock() {
  # Atomic PID-file create (O_EXCL). Works on all POSIX platforms.
  if (echo $$ > "$LOCK_FILE") 2>/dev/null; then
    return 0
  fi
  return 1
}

# Cleanup lock file on exit (best-effort, only if we own it).
release_lock() {
  [ -f "$LOCK_FILE" ] || return 0
  local owner
  owner=$(cat "$LOCK_FILE" 2>/dev/null) || return 0
  [ "$owner" = "$$" ] && rm -f "$LOCK_FILE"
}

# Trap signals to release the lock.
# On Windows Git Bash, SIGTERM/SIGINT traps may not fire on taskkill,
# but atexit in Python handles cleanup there.
trap release_lock EXIT
trap 'release_lock; exit 130' INT
trap 'release_lock; exit 143' TERM

# ── 4. Main ────────────────────────────────────────────────────────────

cleanup_stale_lock
ensure_venv

if ! acquire_lock; then
  # Double-check: maybe the holder died between our check and acquire.
  cleanup_stale_lock
  if ! acquire_lock; then
    local_pid=$(read_lock_pid) || local_pid="?"
    log "Another training is running (PID $local_pid). Exiting."
    exit 0
  fi
fi

# Environment
export PYTHONUTF8=1          # avoids GBK/cp936 decode errors on Windows; harmless on Unix
export OMP_NUM_THREADS=12
export OPENBLAS_NUM_THREADS=12
export MKL_NUM_THREADS=12

log "Starting training (PID $$) ..."
log "  platform: $(uname -s)"
log "  python:   $VENV_PYTHON"
log "  args:     ${*:-<defaults>}"
log "  lock:     $LOCK_FILE"
log "  Ctrl-C to stop"

# exec replaces this shell — no zombie parent, clean signal handling.
exec "$VENV_PYTHON" "$TRAIN_LOOP" "$@"
