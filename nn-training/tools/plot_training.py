#!/usr/bin/env python3
"""plot_training.py - Quick forensics over nn-training/train.log (no deps).

Reads the training log and prints:
  1. Per-round rollout / val / win-rate summary (stdout table).
  2. Optional kl/drift alert lines (if log carry them).
  3. Basic stats: total rounds, runtime, best val_loss, final win_rate.

Run from nn-training/:
    python tools/plot_training.py [train.log] [--rounds N] [--alert-kl KL_TH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_LOG = "train.log"
ROUND_RE = re.compile(r"=== ROUND (\d+) ===")
VAL_RE = re.compile(r"val[:=]\s*([0-9.]+)")
WIN_RE = re.compile(r"win[:=]\s*([0-9.]+)")
KL_RE = re.compile(r"kl[:=]\s*([0-9.]+)")
RESUME_RE = re.compile(r"resume=(\S+)")


class RoundStats:
    __slots__ = ("kls", "n", "resume", "started", "vals", "wins")

    def __init__(self, n: int):
        self.n = n
        self.vals: list[float] = []
        self.wins: list[float] = []
        self.kls: list[float] = []
        self.resume: str = ""
        self.started: str = ""

    @property
    def val_best(self) -> float:
        return min(self.vals) if self.vals else float("nan")

    @property
    def win_last(self) -> float:
        return self.wins[-1] if self.wins else float("nan")


def parse_log(path: Path) -> dict[int, RoundStats]:
    rounds: dict[int, RoundStats] = {}
    cur: RoundStats | None = None

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            rm = ROUND_RE.search(line)
            if rm:
                n = int(rm.group(1))
                cur = rounds.setdefault(n, RoundStats(n))
                cur.n = n
                continue
            if cur is None:
                continue
            m = VAL_RE.search(line)
            if m:
                cur.vals.append(float(m.group(1)))
            m = WIN_RE.search(line)
            if m:
                cur.wins.append(float(m.group(1)))
            m = KL_RE.search(line)
            if m:
                cur.kls.append(float(m.group(1)))
            m = RESUME_RE.search(line)
            if m:
                cur.resume = m.group(1)
    return rounds


def fmt(v: float, spec: str = ".4f") -> str:
    if v != v:
        return "-"
    return format(v, spec)


def main(log: Path, top_n: int | None, alert_kl: float) -> None:
    if not log.exists():
        print(f"ERROR: {log} not found", file=sys.stderr)
        sys.exit(2)

    rounds = parse_log(log)
    if not rounds:
        print(f"(no ROUND markers found in {log})")
        return

    seq = sorted(rounds)
    if top_n and top_n > 0:
        seq = seq[-top_n:]

    print(f"# training forensics - {log}")
    print(f" rounds seen: {len(rounds)} ; displayed: {len(seq)}")
    print()
    hdr = f"{'ROUND':>5} {'val_best':>10} {'win_last':>10} {'kl_last':>10} resume"
    print(hdr)
    print("-" * len(hdr))

    alerts: list[str] = []
    for n in seq:
        r = rounds[n]
        kl_last = r.kls[-1] if r.kls else float("nan")
        print(f"{n:>5} {fmt(r.val_best):>10} {fmt(r.win_last):>10} "
              f"{fmt(kl_last):>10} {r.resume}")
        if r.kls and r.kls[-1] >= alert_kl:
            alerts.append(f"!! ROUND {n} kl={r.kls[-1]:.3f} >= {alert_kl}")

    if alerts:
        print()
        print("# KL alerts")
        for a in alerts:
            print(a)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default=DEFAULT_LOG, help="path to train.log")
    ap.add_argument("--rounds", type=int, default=None,
                    help="show only last N rounds")
    ap.add_argument("--alert-kl", type=float, default=0.15,
                    help="flag any round with kl >= this threshold")
    args = ap.parse_args()
    main(Path(args.log), args.rounds, args.alert_kl)
