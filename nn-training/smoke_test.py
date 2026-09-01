"""
End-to-end smoke test for the NN training engineering.

Generates small SYNTHETIC shards (random but structurally valid), then runs the
real BC trainer (`train_bc.train`) on them and asserts:
  * training completes and produces a weights JSON,
  * validation loss improved vs the first epoch (the pipeline learns *something*),
  * exported weights reload and the architecture matches.

This verifies the Python side of the engineering (npy IO, dataset, augmentation,
model, masked-CE training, weight export) WITHOUT needing the game-side exporter
or a GPU. Run:  python smoke_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import NNPolicy, param_count
from npyio import save_shard, scan_shards
from schema import (
    BOARD,
    FIRE_DIM,
    MASK_DIM,
    MOVE_DIM,
    OBS_CHANNELS,
    OBS_SCHEMA_MAJOR,
    SCALAR_DIM,
)
from train_bc import train
from weights_io import load_state_into, load_weights_json


def _make_synthetic_shard(n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    obs = rng.integers(0, 4, size=(n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8)
    scalars = rng.random((n, SCALAR_DIM)).astype(np.float32)
    actions = np.stack(
        [
            rng.integers(0, MOVE_DIM, n).astype(np.uint8),
            rng.integers(0, FIRE_DIM, n).astype(np.uint8),
        ],
        axis=1,
    )
    masks = np.ones((n, MASK_DIM), dtype=np.uint8)
    # occasionally drop a class to exercise the mask machinery
    drop = rng.random(n) < 0.1
    masks[drop, :MOVE_DIM] = rng.integers(0, 2, (drop.sum(), MOVE_DIM)).astype(np.uint8)
    conditions = rng.integers(0, 4, n).astype(np.uint8)
    returns = (rng.random(n) * 2).astype(np.float32)  # v2: returns 可选字段
    return {
        "obs": obs,
        "scalars": scalars,
        "actions": actions,
        "masks": masks,
        "conditions": conditions,
        "returns": returns,
    }


def main():
    tmp = tempfile.mkdtemp(prefix="nn_smoke_")
    data_dir = os.path.join(tmp, "synthetic")
    for i in range(4):
        sd = os.path.join(data_dir, f"shard_{i:03d}")
        save_shard(sd, _make_synthetic_shard(n=200, seed=i), {"schema_major": OBS_SCHEMA_MAJOR})

    print(f"[smoke] synthetic shards: {len(scan_shards(data_dir))} @ {data_dir}")

    out = os.path.join(tmp, "hard-v0", "weights.json")
    res = train(__arg_proxy(data_dir, out))

    # Assertions
    assert os.path.exists(out), "weights JSON not written"
    meta, params = load_weights_json(out)
    assert meta["schema_major"] == OBS_SCHEMA_MAJOR
    assert len(params) > 0

    # reload into a fresh model and confirm it runs a forward pass
    m = NNPolicy()
    load_state_into(m, out)
    import torch

    dummy = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    mv, fr = m(dummy, torch.zeros(2, SCALAR_DIM))
    assert tuple(mv.shape) == (2, MOVE_DIM)

    hl = res["history"]["val_loss"]
    improved = hl[-1] < hl[0]
    print(f"[smoke] val_loss {hl[0]} -> {hl[-1]}  improved={improved}  params={res['params']}")

    # Strict pass: with 200*4=800 random samples the model should overfit a bit
    # (val loss < first epoch). If it can't, the pipeline is broken.
    if not improved:
        print("[smoke] FAIL: validation loss did not improve")
        sys.exit(1)
    print(
        "[smoke] PASS: end-to-end pipeline works (npy IO -> dataset -> train -> export -> reload)"
    )


def __arg_proxy(data_dir: str, out: str):
    class A:  # minimal argparse stand-in
        pass

    a = A()
    a.data_dir = data_dir
    a.out = out
    a.checkpoint = None
    a.arch = "bc"
    a.epochs = 12
    a.batch = 128
    a.lr = 3e-3
    a.val_split = 0.15
    a.mirror_p = 0.5
    a.seed = 7
    a.num_workers = 0
    a.notes = "smoke"
    a.resume = None
    return a


if __name__ == "__main__":
    main()
