"""eval_heartbeat — EvalBoard runner 心跳（plan/evalboard-console-ux.md §5.4 R4-G1）。

单文件覆盖写（原子替换），训练侧唯一写者，console 只读。
落 `dashboard/data/evalboard/runner_state.json`（**不放 tmp/**：`tools/tmp-clean.py`
会回收 tmp ⇒ 长跑时心跳 stale）。

字段（console 读侧映射见 `dashboard/src/server/eval-board.ts:readRunnerState`）：
  window_open / updated_ts / batch_id / unit_idx / unit_of / rung /
  remaining_units / last_window_closed_ts / engine_epoch
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from common.log import default_router

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "dashboard" / "data" / "evalboard"


def data_root() -> Path:
    """EvalStore 数据根（EVALBOARD_DATA 覆盖；与 `batch_store.data_root` 同口径）。"""
    return Path(os.environ.get("EVALBOARD_DATA", str(_DEFAULT_ROOT)))


def now_ms() -> int:
    return int(time.time() * 1000)


def state_path(root: Path | None = None, /) -> Path:
    return (root or data_root()) / "runner_state.json"


def write_state(root: Path | None = None, /, **patch: object) -> None:
    """合并写心跳（读旧 → update → 原子替换）。

    任何失败都**不抛**——心跳绝不打断训练主链。但「不抛」不等于「不留痕」（2026-11-14）：
    落盘失败按落点喊一次 WARN。传 root 便于单测。
    """
    p = state_path(root)
    cur: dict = {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cur = raw
    except (OSError, json.JSONDecodeError):
        cur = {}
    cur.update(patch)
    cur["updated_ts"] = now_ms()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        # 静默的后果不是「少一行心跳」，是**console 永久停在旧值**：evalboard 那一栏读的就是
        # 这个文件，「训练卡在忙碌中」与「心跳写不进去」在屏幕上长得一模一样，而后者训练侧
        # 一切正常 —— 没有人会去查磁盘。按落点去重：窗口开/关是每轮都写的，逐次喊会刷屏。
        default_router().warn_once(
            f"eval_heartbeat:{p}",
            f"EvalBoard 心跳写不进去：{e!r}（落点 {p}）——console 的 runner_state 会停在旧值",
        )
