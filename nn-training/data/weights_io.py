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

# 仓库根探测（B4，2026-09-02）：包已安装（pip install -e .）或 script-dir/cwd 在
# nn-training/ 内时直接可用；仅当探针失败才把仓库根临时加入 sys.path——
# 不无条件抢占 sys.path 前端、不遮蔽 site-packages。find_spec 不真正 import，
# 避免探针导入产生 F401。
import importlib.util as _ilu

if _ilu.find_spec("schema") is None:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import base64
import json
import os
import re
from typing import Any

import torch

from schema import OBS_SCHEMA_MAJOR


def tensor_to_b64(t: torch.Tensor) -> str:
    """Tensor → base64 of little-endian float32 bytes (weights JSON `data` 字段)。"""
    arr = t.detach().cpu().contiguous().numpy().astype("<f4")
    return base64.b64encode(arr.tobytes()).decode("ascii")


# 兼容别名：早期版本以私有名导出，外部（intent_net.export_golden 等）沿用旧名。
_tensor_to_b64 = tensor_to_b64


def _b64_to_tensor(b64: str, shape: list[int]) -> torch.Tensor:
    raw = base64.b64decode(b64)
    arr = torch.frombuffer(bytearray(raw), dtype=torch.float32).reshape(shape)
    return arr.clone()


def save_weights_json(
    model: torch.nn.Module, path: str, extra_meta: dict[str, Any] | None = None
) -> None:
    """Write the model weights in the JSON+base64 format (plan §5)."""
    params: dict[str, Any] = {}
    for name, p in model.state_dict().items():
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
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
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = abs_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    # Atomic on the same volume: a crash mid-write never leaves a truncated
    # weights file behind (matters for long unattended RL loops that overwrite
    # the same path every iteration).
    os.replace(tmp_path, abs_path)


def load_weights_json(path: str) -> tuple[dict[str, Any], dict[str, torch.Tensor]]:
    """Load a JSON+base64 weights file -> (meta, {name: tensor}).

    读入端强校验（plan/python-refactor.md P0-4，2026-09-02）：
      * 文件必须有非空 params；format 若存在必须为 "nn-weights-json"；
      * **schema_major 必须等于当前 OBS_SCHEMA_MAJOR**——schema 变更意味着 obs/
        scalar/action 布局已变（schema.py 红线：MAJOR bump 必须全量重导），旧文件
        静默加载只会把 24 维 scalar 的旧权重灌进 19 维模型，逐字段错位。
    不匹配直接 raise（fail fast）：训练前的崩溃永远比训练后的错误结论便宜。
    """
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    if not isinstance(meta, dict) or not isinstance(meta.get("params"), dict) or not meta["params"]:
        raise ValueError(f"[weights] {path}: 空或损坏权重文件（无 params）")
    fmt = meta.get("format")
    if fmt is not None and fmt != "nn-weights-json":
        raise ValueError(f"[weights] {path}: 未知 format {fmt!r}")
    sm = meta.get("schema_major")
    if sm is not None and int(sm) != OBS_SCHEMA_MAJOR:
        raise ValueError(
            f"[weights] {path}: schema_major={sm} ≠ 当前 {OBS_SCHEMA_MAJOR} —— "
            f"obs/scalar/action 布局已变更（schema.py 红线），该权重必须全量重导后使用"
        )
    params = {k: _b64_to_tensor(v["data"], v["shape"]) for k, v in meta["params"].items()}
    return meta, params


# load_state_into 的覆盖率两档门禁（2026-09-02 P0-4 修复）：
#   < COVERAGE_RAISE   → 权重与模型族严重不匹配（如 intent/goal 权重灌进 per-tick
#                        模型），raise——此前 strict=False 静默随机初始化，训练照常跑、
#                        日志照常绿，唯一线索是 stdout 一行 print。
#   < COVERAGE_WARN    → 合法架构演进（如 FC 维度变更 / StudentNet→PPOStudent
#                        value 头缺失），警告并继续。
# 实测基线：PPOStudent←StudentNet = 95.2%（合法 warm-start），PPOStudent←NNPolicy
# = 14.3%（错误族）。
COVERAGE_RAISE = 0.5
COVERAGE_WARN = 0.95


def load_state_into(model: torch.nn.Module, path: str) -> None:
    """Load exported weights into a matching NNPolicy instance.

    Tolerates architecture changes (e.g. FC layer shape mismatch): when
    ``load_state_dict`` raises on a shape mismatch, filter out the offending
    keys and load what we can — the remaining params keep their random init.
    This lets training continue from a new architecture without a manual
    weights file rename.

    P0-4（2026-09-02）：加载前先算**参数名覆盖率**（匹配键数 / 模型期望键数）——
    低于 COVERAGE_RAISE 直接 raise，杜绝"错误权重族被静默加载成随机初始化"。
    """
    meta, params = load_weights_json(path)
    expected = set(model.state_dict().keys())
    provided = set(params.keys())
    matched = expected & provided
    coverage = len(matched) / max(1, len(expected))
    if coverage < COVERAGE_RAISE:
        raise ValueError(
            f"[weights] {path}: 参数覆盖率 {coverage:.0%}（{len(matched)}/{len(expected)}）"
            f"—— 权重与模型族严重不匹配（arch={meta.get('arch')}），拒绝静默随机初始化。"
            f"请确认 --init-from/--resume 指向正确模型族的权重。"
        )
    try:
        missing, unexpected = model.load_state_dict(params, strict=False)
    except RuntimeError:
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
        print(
            f"[weights] load_state_into: loaded {len(compatible)}/{len(params)} params from {path}"
        )
    else:
        if missing or unexpected:
            level = "WARN" if coverage >= COVERAGE_WARN else "WARN(partial)"
            print(
                f"[weights] load_state_into: {level}: coverage={coverage:.0%} "
                f"missing={sorted(missing)} unexpected={sorted(unexpected)[:8]}"
            )
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
