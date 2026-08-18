"""
Evaluation bridge (plan §5 `eval_bridge.py`).

Two modes:
  1. Python-side quick eval: loads exported weights + a held-out npy shard dir
     and reports per-head masked accuracy. Fast sanity check that training
     produced a working policy before invoking the heavy sim.
  2. Full sim eval: shells out to the bun headless simulator with the NN
     policy. The `--policy nn` flag on `tools/sim` is wired in milestone
     NN-M1/M3 (not yet present), so this mode currently EMITS the command to
     run rather than executing it. The exact command is pinned here so the
     evaluation protocol cannot drift (plan §NN-M1).

Usage:
  python eval_bridge.py --weights hard-v0/weights.json --data-dir <held-out>
  python eval_bridge.py --weights hard-v0/weights.json --emit-bun-cmd

  # auto-select the latest weights.*.json in nn-training/ (no manual rename):
  python eval_bridge.py --data-dir <held-out>
  python eval_bridge.py --emit-bun-cmd --weights-dir .
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import NNPolicy  # noqa: E402
from weights_io import load_state_into, latest_weights_path  # noqa: E402
from npyio import load_dataset  # noqa: E402
from schema import MOVE_DIM, FIRE_DIM, ITEM_DIM  # noqa: E402


def quick_eval(weights_path: str, data_dir: str) -> dict:
    meta, _ = load_state_into.__wrapped__ if False else (None, None)  # noop guard
    model = NNPolicy()
    load_state_into(model, weights_path)
    model.eval()
    data = load_dataset(data_dir)
    obs = torch.from_numpy(data["obs"].astype(np.uint8))
    sc = torch.from_numpy(data["scalars"].astype(np.float32))
    mv = data["actions"][:, 0]
    fr = data["actions"][:, 1]
    it = data["actions"][:, 2]
    masks = data["masks"].astype(np.float32)

    with torch.no_grad():
        lm, lf, li = model(obs, sc)
    pm = lm.argmax(-1).numpy()
    pf = lf.argmax(-1).numpy()
    pi = li.argmax(-1).numpy()

    def acc(pred, gold, m, k):
        m = m[:, :k]
        valid = m.sum(-1) > 0
        if valid.sum() == 0:
            return float("nan")
        correct = (pred == gold) * m.sum(-1)
        return float((correct[valid].sum()) / (m.sum(-1)[valid].sum()))

    out = {
        "move_acc": acc(pm, mv, masks, MOVE_DIM),
        "fire_acc": acc(pf, fr, masks, FIRE_DIM),
        "item_acc": acc(pi, it, masks, ITEM_DIM),
        "n": int(obs.shape[0]),
    }
    return out


def emit_bun_cmd(weights_path: str) -> str:
    """
    Pin the full sim-eval command (plan §NN-M1). Once `tools/sim` accepts
    `--policy nn`, this becomes an executable call instead of a printed hint.
    """
    return (
        "bun tools/sim/batch-sim.ts --policy nn "
        f"--weights {weights_path} "
        "--stages 0-34 --seeds 1-60 --max-ticks 3600 "
        "--out tmp/nn-eval-hard-train.json"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=None,
                    help="explicit weights JSON path; if omitted, the latest weights.*.json in --weights-dir is auto-selected")
    ap.add_argument("--weights-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights"),
                    help="directory to auto-discover the latest weights when --weights is omitted")
    ap.add_argument("--data-dir", default=None, help="held-out npy shards for quick eval")
    ap.add_argument("--emit-bun-cmd", action="store_true", help="print the full sim-eval command")
    args = ap.parse_args()

    # Auto-discover the latest weights unless an explicit path is given.
    weights_path = args.weights
    if weights_path is None:
        weights_path = latest_weights_path(args.weights_dir)
        if weights_path is None:
            print(f"ERROR: no weights found in {args.weights_dir} (pass --weights <path> to specify)")
            sys.exit(2)
    print(f"[eval] using weights: {weights_path}")

    if args.emit_bun_cmd:
        print(emit_bun_cmd(weights_path))
        return

    if not args.data_dir:
        print("ERROR: provide --data-dir for quick eval, or --emit-bun-cmd for the sim command")
        sys.exit(2)

    res = quick_eval(weights_path, args.data_dir)
    print(json.dumps(res, indent=2))
    print("\nFull sim eval command:")
    print("  " + emit_bun_cmd(weights_path))


if __name__ == "__main__":
    main()
