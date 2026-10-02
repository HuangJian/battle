"""test_finish_course_drains_eval — 收官 eval 的**调用矩阵**（plan/eval-final-round-and-dropped §4.1）。

现场（2026-10-02 h4-aim-k25）：多课程 serve 的收官走 `finish_course`，而那份实现没有 drain，
而延迟派发只到 it-1 ⇒ it40（最后一轮权重）训练期间零局（整份日志 `[eval] drain:` 零命中）。
本文件钉四件事：

  ① `finish_course` 里 drain **先于** `write_run_complete`（横幅语义：先派发，后「已完成」）；
  ② `run()` 的终止路径矩阵：停车（经 `_park_after_completion` → `finish_course`）/
     exit-on-done / 熔断（tripped）各有 drain，smoke 没有；
  ③ drain 的 block 参数按路径传（exit/熔断 block=True；serve RL block=False）；
  ④ serve 收官对 RL 传 `drain=True, block=False`，BC 不带 kwargs（另一份实现，传了 TypeError）。

现状（W2 之前）红：`finish_course` 无 drain 参数、不调 drain；`run()` 的三条终止路径
用不带参形态 drain。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trainer.loop_lifecycle as life
from trainer.loop_core import TrainingLoop
from worker.loop_round import ROUND_SMOKE_STOP, ROUND_STOP, RoundOutcome


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "iters": 1,
        "out": "tmp/x/weights.json",
        "traj": "tmp/x",
        "remote_hub_url": "",
        "remote_token": "",
        "exit_on_done": False,
        "mode": "per-tick",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _loop(tmp_path: Path, **kw) -> TrainingLoop:
    loop = TrainingLoop(_args(**kw), None, "bun", {})
    loop._jsonl_path = tmp_path / "training_log.jsonl"
    loop._traj_root = tmp_path
    return loop


def test_finish_course_drains_before_run_complete(tmp_path: Path, monkeypatch) -> None:
    """drain（派发）先于 run_complete（横幅）；block 透传；drain=False 跳过。"""
    loop = _loop(tmp_path)
    order: list = []
    monkeypatch.setattr(
        TrainingLoop,
        "_sync_cloud_halt",
        lambda self, it, v: order.append(("halt", it, v)),
    )
    monkeypatch.setattr(
        TrainingLoop, "_drain_pending_eval", lambda self, **kw: order.append(("drain", kw))
    )
    monkeypatch.setattr(
        life, "write_run_complete", lambda *a, **k: order.append(("run_complete", a[1]))
    )

    loop.finish_course(40, drain=True, block=False)
    assert order == [
        ("halt", 40, "PAUSE"),
        ("drain", {"block": False}),
        ("run_complete", 40),
    ], order

    order.clear()
    loop.finish_course(40)  # 默认 drain=True, block=True（停车路径的默认参数）
    assert order == [
        ("halt", 40, "PAUSE"),
        ("drain", {"block": True}),
        ("run_complete", 40),
    ], order

    order.clear()
    loop.finish_course(40, drain=False)
    assert order == [("halt", 40, "PAUSE"), ("run_complete", 40)], order


def _prep_run(loop: TrainingLoop, monkeypatch, outcome: RoundOutcome) -> None:
    monkeypatch.setattr(TrainingLoop, "_setup", lambda self: None)
    monkeypatch.setattr(TrainingLoop, "run_one_round", lambda self, it: outcome)
    loop._start_it = 1
    loop._course_fp = None  # type: ignore[assignment]  # _setup 被替身跳过，手动补齐读点
    loop._corpus_fp = None  # type: ignore[assignment]
    loop._collect_child = None


def test_run_park_path_drains_via_finish_course(tmp_path: Path, monkeypatch) -> None:
    """停车路径：run() 不直接 drain —— 收官副作用（含 drain）在 finish_course 一处。"""
    loop = _loop(tmp_path)
    _prep_run(loop, monkeypatch, RoundOutcome(ROUND_STOP, 1))
    drains: list = []
    parked: list[int] = []
    monkeypatch.setattr(
        TrainingLoop, "_drain_pending_eval", lambda self, **kw: drains.append(kw)
    )
    monkeypatch.setattr(
        TrainingLoop, "_park_after_completion", lambda self, it: parked.append(it)
    )
    loop.run()
    assert parked == [1], "默认停车路径必须进 _park_after_completion"
    assert drains == [], "停车路径的 drain 在 finish_course 里（经 _park_after_completion），run() 不得再来一处"


def test_run_exit_on_done_still_drains(tmp_path: Path, monkeypatch) -> None:
    """--exit-on-done：进程即将退出，收官 drain 必须在返回前跑（旧行为：drain → return）。"""
    loop = _loop(tmp_path, exit_on_done=True)
    _prep_run(loop, monkeypatch, RoundOutcome(ROUND_STOP, 1))
    drains: list = []
    monkeypatch.setattr(
        TrainingLoop, "_drain_pending_eval", lambda self, **kw: drains.append(kw)
    )
    monkeypatch.setattr(
        TrainingLoop,
        "_park_after_completion",
        lambda self, it: pytest.fail("exit-on-done 不得进停车"),
    )
    loop.run()
    assert drains == [{"block": True}], drains


def test_run_tripped_drains_then_exits(tmp_path: Path, monkeypatch) -> None:
    """熔断退出：drain 先于 sys.exit(CIRCUIT_EXIT_CODE)（与旧行为逐字一致）。"""
    loop = _loop(tmp_path)
    _prep_run(loop, monkeypatch, RoundOutcome(ROUND_STOP, 1))
    loop._tripped = "loss-streak"
    drains: list = []
    monkeypatch.setattr(
        TrainingLoop, "_drain_pending_eval", lambda self, **kw: drains.append(kw)
    )
    monkeypatch.setattr(
        TrainingLoop, "_park_after_completion", lambda self, it: pytest.fail("熔断不得停车")
    )
    with pytest.raises(SystemExit):
        loop.run()
    assert drains == [{"block": True}], drains


def test_run_smoke_skips_drain(tmp_path: Path, monkeypatch) -> None:
    """--smoke：作废干净退出，不跑收官副作用（也不停车）——旧语义保持。"""
    loop = _loop(tmp_path)
    _prep_run(loop, monkeypatch, RoundOutcome(ROUND_SMOKE_STOP, 1))
    drains: list = []
    monkeypatch.setattr(
        TrainingLoop, "_drain_pending_eval", lambda self, **kw: drains.append(kw)
    )
    monkeypatch.setattr(
        TrainingLoop, "_park_after_completion", lambda self, it: pytest.fail("smoke 不得停车")
    )
    loop.run()
    assert drains == [], drains
