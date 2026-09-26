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

2026-09-26（item 6d）：**JSON 清单那一半**（format/schema_major/params 强校验 +
「最新版本化权重」发现 + 覆盖率门禁常量）搬到免 torch 的 `data/weights_meta.py`
——那边被「坏文件响亮拒绝」一族的用例盯着，却因同住本模块被 torch 拖进测试路径。
本模块从那里再导出，既有 `from data.weights_io import COVERAGE_RAISE /
latest_weights_path / …` 的调用点一行不改。
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
from typing import Any

import torch

from data.weights_meta import (  # noqa: F401  (re-export：既有调用点不变)
    COVERAGE_RAISE,
    COVERAGE_WARN,
    FORMAT,
    latest_weights_path,
    validate_weights_meta,
)
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
    """Write the model weights in the JSON+base64 format (plan §5).

    P2-6c（2026-09-02）：序列化前检查**非有限值**（NaN/Inf）。权重文件是 TS 运行时
    的逐字节消费对象，NaN 会让浏览器端推理静默跑歪（且经 base64 编码后无任何告警）。
    训练发散产 NaN 是严重信号——fail fast 优于写出坏文件。
    """
    params: dict[str, Any] = {}
    for name, p in model.state_dict().items():
        if not torch.isfinite(p).all():
            raise ValueError(
                f"[weights] 参数 {name} 含非有限值（NaN/Inf）——拒绝写出坏权重；"
                f"请检查训练是否发散（loss/gnorm 爆炸），不要用坏权重续跑"
            )
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
    meta = {
        "format": FORMAT,
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

    读入端强校验在 `data.weights_meta.validate_weights_meta`（免 torch，2026-09-26
    抽出的那一半）：非空 params / 未知 format / schema_major ≠ 当前 OBS_SCHEMA_MAJOR
    一律 raise（fail fast：训练前的崩溃永远比训练后的错误结论便宜）。本函数只负责
    「校验通过后的 base64 → 张量」这一步。
    """
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    validate_weights_meta(meta, path)
    params = {k: _b64_to_tensor(v["data"], v["shape"]) for k, v in meta["params"].items()}
    return meta, params


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

