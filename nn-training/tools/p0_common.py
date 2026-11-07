"""P0 只读探针的共享小工具。

这些函数原本在 `p0_auc_probe.py` / `p0_teacher_agreement.py` / `p0_thcost.py`
等脚本里各有一份逐字节相同的实现；复用账本（`tests/test_reuse_ledger.py`）
把它们记成欠账。探针只读、互不依赖，抽到这里不改变任何统计口径。

约定：`tools/` 是包（`tools/__init__.py`），调用方把 `nn-training` 根放进
`sys.path` 后用 `from tools.p0_common import ...`。这样运行时与 mypy 静态解析的
模块名一致（顶层裸名 `p0_common` 会让 mypy 报 import-not-found）。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import numpy as np


def sha16(path: str) -> str:
    """文件字节的 sha256 前 16 位（权重/语料身份口径）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_manifest(dirpath: str) -> dict[str, Any]:
    """读 shard 的 `manifest.json`，拒绝非对象。"""
    path = os.path.join(dirpath, "manifest.json")
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{path} 不是对象")
    return obj


def find_shards(root: str, need: tuple[str, ...]) -> list[str]:
    """所有同时含 `need` 文件的目录，按路径排序。"""
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        if all(name in files for name in need):
            out.append(dirpath)
    return sorted(out)


def select_balanced(shards: list[str], limit: int) -> list[str]:
    """按 stage 轮转取样，避免 `--max-games` 被同一关的前 N 个 seed 吃满。"""
    if limit <= 0 or limit >= len(shards):
        return shards
    groups: dict[int, list[str]] = {}
    for d in shards:
        stage = read_manifest(d).get("stage")
        if isinstance(stage, int):
            groups.setdefault(stage, []).append(d)
    out: list[str] = []
    while len(out) < limit:
        progressed = False
        for stage in sorted(groups):
            if groups[stage] and len(out) < limit:
                out.append(groups[stage].pop(0))
                progressed = True
        if not progressed:
            break
    return sorted(out)


def rankdata(a: np.ndarray) -> np.ndarray:
    """平均秩（tie-aware）。"""
    a = np.asarray(a, dtype=np.float64)
    sorter = np.argsort(a, kind="mergesort")
    sorted_a = a[sorter]
    starts = np.r_[0, np.flatnonzero(sorted_a[1:] != sorted_a[:-1]) + 1]
    ends = np.r_[starts[1:], len(a)]
    avg = 0.5 * (starts + ends + 1)
    out = np.empty(len(a), dtype=np.float64)
    out[sorter] = np.repeat(avg, ends - starts)
    return out


def fmt_float(x: float | int | None, digits: int = 2, *, finite: bool = False) -> str:
    """报告里的定长小数；`finite=True` 时非有限值也记 `n/a`。"""
    if x is None or (finite and not np.isfinite(float(x))):
        return "n/a"
    return f"{float(x):.{digits}f}"
