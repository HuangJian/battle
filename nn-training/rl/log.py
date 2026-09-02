"""统一日志基础设施 —— 训练日志必须可按时间轴复盘。

log():   带时间戳的 stdout 行（run_rl / queue / ppo 共用）。
Tee:     多流转发（控制台 + 文件）——run_rl._Tee 的通用化（P2-6d，2026-09-02）。
         train_loop._TrainTee 因带时间戳落盘 + [epoch N/M] 进度解析保留在本地
         （语义特定，重复度低），如需迁移可从本类继承。
"""

from __future__ import annotations

import time
from collections.abc import Callable


def log(msg: str) -> None:
    """Timestamped stdout line — the training log must be analyzable over time."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Tee:
    """同时写多个流（控制台 + 文件），供长训日志持久化且终端仍可见。

    源自 run_rl_intent（per-tick 模式借此获得 out_log/err_log 落盘能力）。
    on_line：可选逐行回调（train_loop 用它解析 [epoch N/M] 更新心跳进度）。
    """

    def __init__(self, *streams, on_line: Callable[[str], None] | None = None):
        self._streams = streams
        self._on_line = on_line

    def write(self, s: str) -> None:
        for st in self._streams:
            try:
                st.write(s)
            except Exception:
                pass
        if self._on_line is not None:
            try:
                self._on_line(s)
            except Exception:
                pass

    def flush(self) -> None:
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False
