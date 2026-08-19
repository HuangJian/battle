"""
Weight export/import — JSON manifest + base64 Float32 (plan §5).

Format (also consumed by the TS runtime `src/nn/load-weights.ts`):
    {
      "format": "nn-weights-json",
      "version": 1,
      "schema_major": <OBS_SCHEMA_MAJOR>,
      "arch": { ... NNPolicy.arch() ... },
      "params": {
        "<param_name>": { "shape": [..], "data": "<base64 of little-endian f32>" }
      }
    }

The TS side decodes `data` with atob -> Uint8Array -> Float32Array and feeds
the SAME conv/linear ops (see src/nn/infer.ts) so inference reproduces the
Python forward pass (plan §NN-M1 determinism ②).
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict

import torch

from schema import OBS_SCHEMA_MAJOR


def _tensor_to_b64(t: torch.Tensor) -> str:
    arr = t.detach().cpu().contiguous().numpy().astype("<f4")
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _b64_to_tensor(b64: str, shape: list[int]) -> torch.Tensor:
    raw = base64.b64decode(b64)
    arr = torch.frombuffer(bytearray(raw), dtype=torch.float32).reshape(shape)
    return arr.clone()


def save_weights_json(model: torch.nn.Module, path: str, extra_meta: Dict[str, Any] | None = None) -> None:
    """Write the model weights in the JSON+base64 format (plan §5)."""
    params: Dict[str, Any] = {}
    for name, p in model.state_dict().items():
        params[name] = {"shape": list(p.shape), "data": _tensor_to_b64(p)}
    meta = {
        "format": "nn-weights-json",
        "version": 1,
        "schema_major": OBS_SCHEMA_MAJOR,
        "arch": getattr(model, "arch", lambda: {})(),
        "num_params": sum(int(p.numel()) for p in model.parameters()),
        "params": params,
    }
    if extra_meta:
        meta.update(extra_meta)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def load_weights_json(path: str) -> tuple[Dict[str, Any], Dict[str, torch.Tensor]]:
    """Load a JSON+base64 weights file -> (meta, {name: tensor})."""
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    params = {k: _b64_to_tensor(v["data"], v["shape"]) for k, v in meta["params"].items()}
    return meta, params


def load_state_into(model: torch.nn.Module, path: str) -> None:
    """Load exported weights into a matching NNPolicy instance.

    Tolerates architecture changes (e.g. FC layer shape mismatch): when
    ``load_state_dict`` raises on a shape mismatch, filter out the offending
    keys and load what we can — the remaining params keep their random init.
    This lets training continue from a new architecture without a manual
    weights file rename.
    """
    _meta, params = load_weights_json(path)
    try:
        missing, unexpected = model.load_state_dict(params, strict=False)
    except RuntimeError as e:
        # Shape mismatch (e.g. FC layer changed): filter out mismatched keys
        # and load everything else.
        state = model.state_dict()
        compatible = {}
        skipped = []
        for k, v in params.items():
            if k in state and state[k].shape == v.shape:
                compatible[k] = v
            else:
                skipped.append(k)
        if skipped:
            print(f"[weights] load_state_into: skipped (shape mismatch) {skipped}")
        model.load_state_dict(compatible, strict=False)
        print(f"[weights] load_state_into: loaded {len(compatible)}/{len(params)} params from {path}")
    else:
        if missing or unexpected:
            print(f"[weights] load_state_into: missing={missing} unexpected={unexpected}")
    model.eval()


# --- auto-discovery of the latest weights (plan: no manual rename on restore) ---
_VERSIONED_RE = re.compile(r"^weights\.(\d{8}-\d{6})_ep\d+_val[\d.]+?\.json$")


def _stamp_from_name(name: str) -> str | None:
    m = _VERSIONED_RE.match(name)
    return m.group(1) if m else None


def latest_weights_path(directory: str) -> str | None:
    """Return the path to the newest versioned weights file in `directory`.

    Selection rule (plan: restoring from netdisk needs no manual rename):
      * Prefer the versioned archive `weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`
        with the greatest embedded timestamp.
      * Fall back to the active pointer `weights.json` if no versioned file exists.
    Returns None if the directory contains no weights at all.
    """
    if not os.path.isdir(directory):
        return None
    versioned: list[tuple[str, str]] = []
    for fn in os.listdir(directory):
        ts = _stamp_from_name(fn)
        if ts is not None:
            versioned.append((ts, fn))
    if versioned:
        versioned.sort(key=lambda x: x[0])
        newest = versioned[-1][1]
        return os.path.join(directory, newest)
    fallback = os.path.join(directory, "weights.json")
    return fallback if os.path.exists(fallback) else None
