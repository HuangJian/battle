"""`iteration` 事件的降级归因（2026-10-23，plan/offline-hold-stale-fallback §3-S1e）。

为什么需要这一行：§6 风险行写着「本机已采的那几轮会留在产物目录 ⇒ 需要一行说明『这几轮是
本机采的』（`status.collect_mode` 可取）」——但那条通**道根本不存在**（`_node_rollout` 是内存
量，`worker/train_ledger.py` 明写账本重算不出它）。账本是**本地事实**，不涉及
trainer→hub→dashboard 三层，所以落在这里。

本文件钉两条：

1. 普通轮**不写**该键（既有账本行逐字节不变——回归基线/门禁/读面都靠这条）；
2. 降级轮写原因（`hold_stale` / `node_unavailable`，取值域在 `worker/loop_round.py`）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.events import write_iteration


#: `write_iteration` 只读这几个 args 字段（其余走 `m`）。
def _args() -> types.SimpleNamespace:
    return types.SimpleNamespace(mode="per-tick", lr=1e-4, mb=2, epochs=1, reward="", dodge="")


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


def test_normal_round_keeps_the_event_byte_identical(tmp_path: Path) -> None:
    """普通轮**没有** `collect_fallback` 键（旧账本/回归基线逐字节不变）。"""
    row = write_iteration(tmp_path / "training_log.jsonl", _args(), 1, {"winRate": 0.5}, dict(M))
    # 键**不存在**（不是 None）：既有账本行逐字节不变。`get` 兼作「不该有」的行为判据。
    assert row.get("collect_fallback") is None


def test_fallback_round_carries_its_reason(tmp_path: Path) -> None:
    """降级轮把原因落盘（事后能回答「这一轮是谁采的」）。"""
    row = write_iteration(
        tmp_path / "training_log.jsonl",
        _args(),
        2,
        {"winRate": 0.5},
        {**M, "collect_fallback": "hold_stale"},
    )
    assert row["collect_fallback"] == "hold_stale"


def test_record_iteration_forwards_the_engine_fact(monkeypatch) -> None:
    """`_record_iteration` 从引擎属性取值（不是第二轮计算）——与 `step_course_iter` 同源。"""
    import trainer.loop_steps as ls

    seen: dict[str, object] = {}

    def _write(_p: object, _a: object, _it: int, _rep: dict, m: dict) -> dict:
        seen.update(m)
        return {"event": "iteration"}

    monkeypatch.setattr(ls, "write_iteration", _write)
    host: Any = types.SimpleNamespace(
        _jsonl_path="unused.jsonl",
        args=_args(),
        _report={},
        _rollout_sec=1.0,
        _ppo_sec=2.0,
        _ppo_cloud_sec=2.0,
        _total_steps=3,
        _chunks_n=1,
        _agg=None,
        _kl_cum=None,
        _halted_flag=False,
        _dropped_games=None,
        _waves_n=None,
        _load_sec=None,
        _tail_drain_sec=None,
        _eval_join_sec=None,
        _volume_target=None,
        _volume_collected=None,
        _volume_capped=False,
        _volume_stage_stats=None,
        _collect_fallback="node_unavailable",
        _ledger_apply=lambda _ev: None,
    )
    ls.TrainingSteps._record_iteration(host, 3)
    assert seen["collect_fallback"] == "node_unavailable"
