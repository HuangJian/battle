"""test_eval_heartbeat — EvalBoard runner 心跳（R4-G1）单测。

覆盖：合并写 / updated_ts 戳 / 损坏文件恢复 / 原子替换不留 .tmp / root 覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl import eval_heartbeat as hb


def _read(root: Path) -> dict[str, Any]:
    data = json.loads(hb.state_path(root).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_merge_preserves_prior_keys_and_stamps_updated_ts(tmp_path: Path) -> None:
    hb.write_state(tmp_path, window_open=True, batch_id="b1", remaining_units=3)
    first = _read(tmp_path)
    assert first["window_open"] is True
    assert first["batch_id"] == "b1"
    assert first["remaining_units"] == 3
    assert isinstance(first["updated_ts"], int) and first["updated_ts"] > 0

    hb.write_state(tmp_path, window_open=False, last_window_closed_ts=123)
    second = _read(tmp_path)
    assert second["window_open"] is False
    assert second["last_window_closed_ts"] == 123
    assert second["batch_id"] == "b1"  # 合并保留
    assert second["remaining_units"] == 3
    assert second["updated_ts"] >= first["updated_ts"]


def test_corrupt_state_recovers(tmp_path: Path) -> None:
    hb.state_path(tmp_path).write_text("{not json", encoding="utf-8")
    hb.write_state(tmp_path, window_open=True, rung="c4l1")
    st = _read(tmp_path)
    assert st["window_open"] is True
    assert st["rung"] == "c4l1"


def test_atomic_replace_leaves_no_tmp(tmp_path: Path) -> None:
    hb.write_state(tmp_path, window_open=True)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_none_values_clear_fields(tmp_path: Path) -> None:
    """单元结束时 rung=None 必须真的清空（而非保留旧值）。"""
    hb.write_state(tmp_path, rung="c6l1", remaining_units=5)
    hb.write_state(tmp_path, rung=None, remaining_units=0)
    st = _read(tmp_path)
    assert st["rung"] is None
    assert st["remaining_units"] == 0
