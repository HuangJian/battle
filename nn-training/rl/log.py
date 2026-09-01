"""带时间戳的统一日志行 —— 训练日志必须可按时间轴复盘。"""

from __future__ import annotations

import time


def log(msg: str) -> None:
    """Timestamped stdout line — the training log must be analyzable over time."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
