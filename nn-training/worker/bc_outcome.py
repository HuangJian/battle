"""BC shard outcome 分类（dispatch 与语料选择共用的纯函数）。

独立成 L0 模块，避免 `bc_dispatch`（生产者）反向依赖 `bc_corpus`（选择策略）。
只读 manifest，不碰 npy/torch；`classify_outcome` 的返回值是 `win/loss/timeout`
三分类，`error/aborted/未知` 一律 `None`（不保留为训练样本）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

WIN = "win"
LOSS = "loss"
TIMEOUT = "timeout"
OUTCOME_CLASSES = (WIN, LOSS, TIMEOUT)


def classify_outcome(manifest: Mapping[str, Any]) -> str | None:
    """manifest.outcome → win/loss/timeout；error/aborted/未知 = None（不保留）。

    `max_ticks` 是超时；`win/cleared` 标志优先于原始 outcome（BONUS TIME 截断的
    已全歼局仍算胜局）。
    """
    raw = str(manifest.get("outcome") or "").strip().lower()
    if raw in (WIN, "stage_clear", "cleared") or manifest.get("win") is True:
        return WIN
    if manifest.get("cleared") is True:
        return WIN
    if raw in ("timeout", "max_ticks"):
        return TIMEOUT
    if raw in ("loss", "lives_exhausted", "base_destroyed", "gameover", "dead"):
        return LOSS
    return None
