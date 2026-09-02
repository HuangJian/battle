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

2026-09-02 拆分为：train/loop_util.py（单调工具：_emit/_fmt_dur/_TrainTee/
heartbeat/锁/auto_export_corpus） + 本文件（torch-thread 预解析 + main() 编排
+ 纯函数 re-export——tests 引用 train_loop._fmt_dur / parse_val_loss_from_output）。

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
import signal
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Import train() IN-PROCESS. Because train_loop already holds a single-instance
# lock, running training here (instead of via a subprocess) removes the
# launcher's double-spawn race and keeps epoch stdout directly visible.
from train.bc import train as _train_bc
from train.loop_util import (
    SPIN_WARN_S,
    _emit,
    _fmt_dur,
    _TrainTee,
    acquire_lock,
    auto_export_corpus,
    cleanup_lock,
    heartbeat,
    parse_val_loss_from_output,  # noqa: F401 — re-exported（tests 引用）
)

DEFAULT_DATA = os.path.join(HERE, "..", "tmp", "nn-export")
DEFAULT_WEIGHTS = os.path.join(HERE, "weights")


def latest_weights(weights_dir: str) -> str | None:
    sys.path.insert(0, HERE)
    from data.weights_io import latest_weights_path

    return latest_weights_path(weights_dir)


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
            resume_ok = prev is not None and os.path.exists(prev)
            note = f"round {state['round']}"
            if resume_ok:
                # resume_ok 已确认 prev 非空且落盘，可安全 basename。
                assert prev is not None
                note += f" resume {os.path.basename(prev)}"
            else:
                note += " from-scratch"

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
