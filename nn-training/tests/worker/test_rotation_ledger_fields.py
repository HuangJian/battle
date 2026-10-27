"""tests/worker/test_rotation_ledger_fields.py —— 窗口/分关账进事件（plan/it-stage-ratation §9.1）。

读面（控制台「指标」页两列 + 采集窗口面板）读的就是这两行事件里的 `rotation` 与
`volume_stage_stats`。本文件钉**键的存在性**（升级为行为判据的那一半）：

  1. 轮换激活 ⇒ `iteration` / `rollout_end` 都带 `rotation`（含 window/all/mode）；
     **未激活 ⇒ 键不存在**（老账本行逐字节不变 —— 回归基线/门禁/读面都靠这条）；
  2. `rollout_end` 也带 `volume_stage_stats`：窗口与分关进度是**采集侧**事实，采集一收官就
     该上屏（不等 PPO 回传，与 2026-10-19「采集收官即多一点」同一口径）；
  3. 两个落账点（`_record_rollout` / `_record_iteration`）真的把它传下去（不是「算完丢掉」）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_steps import TrainingSteps
from worker.events import write_iteration, write_rollout_end

#: 最小 `m`（字段契约见 `worker/events.py::write_iteration`）。
M: dict[str, object] = {
    "rollout_sec": 1.0,
    "ppo_sec": 2.0,
    "total_steps": 3,
    "chunks_n": 1,
    "agg": None,
    "kl_cum": None,
    "halted": False,
    "dropped_games": None,
    "waves": None,
    "load_sec": None,
    "tail_drain_sec": None,
    "eval_join_sec": None,
}

#: 分关账（本机连续配额链收官时写的形状，只留本文件关心的键）。
VOLUME_STATS: dict[str, Any] = {
    "rule": "per-stage-v3",
    "quota": 17,
    "stages": {"0": {"collected": 17, "games": 2, "cap_games": 4}},
    "shortfall": {},
}


def _args(**over: Any) -> types.SimpleNamespace:
    base: dict[str, Any] = {
        "mode": "per-tick",
        "lr": 1e-4,
        "mb": 2,
        "epochs": 1,
        "reward": "",
        "dodge": "",
        "rotation_mode": "even",
        "rotation_per_it": 12,
        "rotation_pinned": "",
        "stages": "0-34",
        "rotate_seed": 4242,
    }
    base.update(over)
    return types.SimpleNamespace(**base)


def _rotation() -> dict[str, Any]:
    return {"mode": "even", "per_it": 12, "pinned": [], "all": 35, "window": [3, 7]}


def test_iteration_writes_rotation_only_when_present(tmp_path: Path) -> None:
    """轮换激活才写 `rotation`；未激活的行**没有这个键**（不是 None，是缺键）。"""
    p = tmp_path / "training_log.jsonl"
    plain = write_iteration(p, _args(rotation_mode=""), 1, {"winRate": 0.5}, dict(M))
    assert "rotation" not in plain, "未配置轮换的课程一个键都不该多"
    row = write_iteration(p, _args(), 2, {"winRate": 0.5}, {**M, "rotation": _rotation()})
    assert row["rotation"]["window"] == [3, 7] and row["rotation"]["all"] == 35
    # 落盘即一行 JSON（读面读的是盘上那份）
    tail = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert tail["rotation"]["mode"] == "even"


def test_rollout_end_carries_rotation_and_stage_stats(tmp_path: Path) -> None:
    """采集收官行也带窗口与分关账（两者都是采集侧事实）；缺省不写键。"""
    p = tmp_path / "training_log.jsonl"
    plain = write_rollout_end(p, _args(), 3, {"winRate": 0.5}, 12.0)
    for key in ("rotation", "volume_stage_stats"):
        assert key not in plain, key
    row = write_rollout_end(
        p,
        _args(),
        4,
        {"winRate": 0.5},
        12.0,
        rotation=_rotation(),
        volume_stage_stats=VOLUME_STATS,
    )
    assert row["rotation"]["window"] == [3, 7]
    assert row["volume_stage_stats"]["quota"] == 17
    # PPO 侧键照旧一个都不写（缺键 = 空，不冒充 0）
    for key in ("kl", "entropy", "ppo_sec", "wire"):
        assert key not in row, key


def _host(tmp_path: Path, **over: Any) -> Any:
    """最小宿主（`Any` 而不是 `SimpleNamespace`：两个落账点以 unbound 形式驱动，
    宿主只提供它们真正读的那些属性——与 `test_iteration_collect_fallback.py` 同规）。"""
    base: dict[str, Any] = {
        "_jsonl_path": tmp_path / "training_log.jsonl",
        "args": _args(),
        "_report": {"winRate": 0.5},
        "_rollout_sec": 12.0,
        "_ppo_sec": 2.0,
        "_ppo_cloud_sec": 2.0,
        "_total_steps": 3,
        "_chunks_n": 1,
        "_agg": None,
        "_kl_cum": None,
        "_halted_flag": False,
        "_dropped_games": None,
        "_waves_n": None,
        "_load_sec": None,
        "_tail_drain_sec": None,
        "_eval_join_sec": None,
        "_volume_target": None,
        "_volume_collected": None,
        "_volume_capped": False,
        "_volume_stage_stats": None,
        "_collect_fallback": "",
        "_rotate_seed": 4242,
        "_ledger_apply": lambda _ev: None,
    }
    base.update(over)
    return types.SimpleNamespace(**base)


def _last(jsonl: Path) -> Any:
    return json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])


def test_record_iteration_forwards_the_window_readout(tmp_path: Path) -> None:
    """`_record_iteration` 把窗口读数落进 `iteration`（读面「每轮窗口」那一列的来源）。"""
    host = _host(tmp_path)
    TrainingSteps._record_iteration(host, 3)
    row = _last(tmp_path / "training_log.jsonl")
    assert row["event"] == "iteration"
    window = row["rotation"]["window"]
    # 与采集侧同一个窗口（同 args/同 it/同 rotate_seed ⇒ 同一个 ceil(35/12) 窗口；
    # it3 = 一个 epoch 的最后一窗 ⇒ 11 关，不是 12——覆盖算术，不是 bug）
    assert len(window) == 11 and window == sorted(window)
    assert row["rotation"] == {
        "mode": "even",
        "per_it": 12,
        "pinned": [],
        "all": 35,
        "window": window,
    }


def test_record_iteration_keeps_plain_rows_byte_identical(tmp_path: Path) -> None:
    """`rotation_mode` 缺席 ⇒ 事件里没有 `rotation` 键（老课程逐字节不变）。"""
    host = _host(tmp_path, args=_args(rotation_mode=""))
    TrainingSteps._record_iteration(host, 3)
    assert "rotation" not in _last(tmp_path / "training_log.jsonl")


def test_record_rollout_forwards_both(tmp_path: Path) -> None:
    """`_record_rollout` 同时带窗口与分关账（采集收官即可见，不等 PPO）。"""
    host = _host(tmp_path, _volume_stage_stats=VOLUME_STATS)
    TrainingSteps._record_rollout(host, 4)
    row = _last(tmp_path / "training_log.jsonl")
    assert row["event"] == "rollout_end"
    assert row["rotation"]["all"] == 35
    assert row["volume_stage_stats"]["stages"]["0"]["collected"] == 17
    assert "ppo_sec" not in row  # PPO 侧照旧不写键


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
