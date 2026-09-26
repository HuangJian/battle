"""data/weights_meta.py — 权重 JSON 的**免 torch** 半边（2026-09-26，item 6d）。

历史：`data/weights_io.py` 混装两类东西——

  * **JSON 清单**：`format` / `schema_major` / 非空 `params` 的强校验（P0-4），
    以及「最新版本化权重文件」的发现——纯 stdlib + json；
  * **张量编解码**：base64 ↔ `torch.Tensor`（`tensor_to_b64` / `_b64_to_tensor`）、
    `save_weights_json` 的非有限值检查、`load_state_into` 的覆盖率门禁。

前者被「坏文件必须响亮拒绝」一族的用例盯着（schema 不匹配 / 未知 format / 空 params），
却因同住一个模块而被 `import torch` 拖进测试路径。2026-09-26 抽取本模块
（与 `data/mirror.py` / `ppo/np_core.py` 同一手法）；`data/weights_io.py` 从这里再导出，
既有 `from data.weights_io import COVERAGE_RAISE / latest_weights_path …` 一行不改。
"""

from __future__ import annotations

import os
import re
from typing import Any

from schema import OBS_SCHEMA_MAJOR

#: 权重文件格式标识（TS 运行时 `src/nn/load-weights.ts` 按同一串认）。
FORMAT = "nn-weights-json"

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


def validate_weights_meta(meta: Any, path: str) -> None:
    """读入端强校验（plan/python-refactor.md P0-4，2026-09-02）：坏文件必须响亮拒绝。

      * 文件必须有非空 params；format 若存在必须为 "nn-weights-json"；
      * **schema_major 必须等于当前 OBS_SCHEMA_MAJOR**——schema 变更意味着 obs/
        scalar/action 布局已变（schema.py 红线：MAJOR bump 必须全量重导），旧文件
        静默加载只会把 24 维 scalar 的旧权重灌进 19 维模型，逐字段错位。
    不匹配直接 raise（fail fast）：训练前的崩溃永远比训练后的错误结论便宜。
    """
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("params"), dict)
        or not meta["params"]
    ):
        raise ValueError(f"[weights] {path}: 空或损坏权重文件（无 params）")
    fmt = meta.get("format")
    if fmt is not None and fmt != FORMAT:
        raise ValueError(f"[weights] {path}: 未知 format {fmt!r}")
    sm = meta.get("schema_major")
    if sm is not None and int(sm) != OBS_SCHEMA_MAJOR:
        raise ValueError(
            f"[weights] {path}: schema_major={sm} ≠ 当前 {OBS_SCHEMA_MAJOR} —— "
            f"obs/scalar/action 布局已变更（schema.py 红线），该权重必须全量重导后使用"
        )


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
