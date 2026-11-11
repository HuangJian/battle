"""stream 的采集收官回调（`on_collect_end`）。

背景：旧 `step_rollout` 用 `with eval_gate().rollout(...)` 包住整个
`_rollout_phase`，而 stream 的 `run_rollout_stream` 把采集 + PPO + 尾部 drain
放在同一个调用里 ⇒ eval 闸在 PPO 期间仍然冻着，采集结束后也不解冻。

修法：stream 在 collector 线程真正退出后、尾部 drain 之前调 `on_collect_end`
一次；`step_rollout` 把它接到 `end_rollout`。本用例钉住两件事：

1. 回调**晚于** collector 退出（不能在采集还在跑时就解冻）；
2. 回调恰好触发一次（尾部 drain 不得重复触发）。
"""

from __future__ import annotations

import threading
import types

import common.distribution
import trainer.loop_round_steps as rsteps
import trainer.stream as stream
from trainer.loop_round_steps import RoundSteps
from worker.loop_round import COLLECT_LOCAL, RoundContext


def test_on_collect_end_fires_once_after_collector_exits(tmp_path, monkeypatch) -> None:
    order: list[str] = []
    collector_done = threading.Event()

    def fake_queue(*_a, **_kw):
        # 模拟 collector：真正退出前打一个可观测标记。
        order.append("collector")
        collector_done.set()
        return {"games": 0}

    monkeypatch.setattr(stream, "run_rollout_queue", fake_queue)
    monkeypatch.setattr(stream, "completed_pairs", lambda *_a, **_kw: set())
    monkeypatch.setattr(common.distribution, "weights_fingerprint", lambda _p: "w0")

    class _Backend:
        def _ppo_load(self, *_a, **_kw) -> int:
            return 1  # 视为 PPO 已在先前进程跑完，跳过空波更新

    def on_collect_end() -> None:
        assert collector_done.is_set(), "collector 还没退出就解冻了 eval"
        order.append("end")

    args = types.SimpleNamespace(
        workers=1,
        local_slots=0,
        mb=1,
        epochs=1,
        gamma=0.99,
        lam=0.95,
        adv_norm="none",
        stream=1,
        max_ticks=1,
        difficulty="hard",
        intent_rollout=False,
        goal_rollout=False,
        _kl_cap=None,
        course_name="",
    )
    report = stream.run_rollout_stream(
        "bun",
        str(tmp_path / "w.json"),
        tmp_path,
        [],
        args,
        {"policy": {}},
        "rid.1",
        None,
        None,
        None,
        backend=_Backend(),
        on_collect_end=on_collect_end,
    )

    assert order == ["collector", "end"], order
    assert report["games"] == 0


def test_dispatch_gate_opens_on_drain_and_refreezes_on_refill(tmp_path, monkeypatch) -> None:
    """P0-2 的 stream 动态闸：队列清空 → 解冻；回队 → 重冻；收官再兜底解冻。"""
    order: list[str] = []

    def fake_queue(*_a, on_queue_drained=None, on_queue_active=None, **_kw):
        on_queue_drained()
        order.append("drained")
        on_queue_active()
        order.append("active")
        on_queue_drained()
        order.append("drained")
        return {"games": 0}

    monkeypatch.setattr(stream, "run_rollout_queue", fake_queue)
    monkeypatch.setattr(stream, "completed_pairs", lambda *_a, **_kw: set())
    monkeypatch.setattr(common.distribution, "weights_fingerprint", lambda _p: "w0")

    class _Backend:
        def _ppo_load(self, *_a, **_kw) -> int:
            return 1

    args = types.SimpleNamespace(
        workers=1,
        local_slots=0,
        mb=1,
        epochs=1,
        gamma=0.99,
        lam=0.95,
        adv_norm="none",
        stream=1,
        max_ticks=1,
        difficulty="hard",
        intent_rollout=False,
        goal_rollout=False,
        _kl_cap=None,
        course_name="",
    )
    stream.run_rollout_stream(
        "bun",
        str(tmp_path / "w.json"),
        tmp_path,
        [],
        args,
        {"policy": {}},
        "rid.1",
        None,
        None,
        None,
        backend=_Backend(),
        on_dispatch_drained=lambda: order.append("release"),
        on_dispatch_active=lambda: order.append("freeze"),
        on_collect_end=lambda: order.append("end"),
    )

    assert order == [
        "release",
        "drained",
        "freeze",
        "active",
        "release",
        "drained",
        "end",
    ], order


def test_step_rollout_gate_refcount_survives_drain_refill_cycles(monkeypatch) -> None:
    """drain/refill 交错时闸计数不得记反：每次 release 只 end 一次，refill 只 begin 一次。"""

    class _Gate:
        def __init__(self) -> None:
            self.depth = 0
            self.trace: list[tuple[str, int]] = []

        def begin_rollout(self, _fp=None) -> None:
            self.depth += 1
            self.trace.append(("begin", self.depth))

        def end_rollout(self, _fp=None) -> None:
            if self.depth > 0:
                self.depth -= 1
                self.trace.append(("end", self.depth))

    gate = _Gate()
    monkeypatch.setattr(rsteps, "eval_gate", lambda: gate)
    monkeypatch.setattr("worker.cmd.course_fp_for_args", lambda _args: "fp")

    steps = RoundSteps()
    steps.args = types.SimpleNamespace(course_path="c.jsonc")
    steps._greedy_plan = None
    monkeypatch.setattr(steps, "_volume_active", lambda: False)

    def fake_rollout(
        _it: int,
        _pairs: list[tuple[int, int]],
        _cfg: dict | None,
        _eval: bool,
        on_collect_end=None,
        on_dispatch_drained=None,
        on_dispatch_active=None,
    ) -> None:
        assert on_collect_end and on_dispatch_drained and on_dispatch_active
        on_dispatch_drained()
        on_dispatch_drained()  # 重复 drain：不得二次 end
        on_dispatch_active()
        on_collect_end()
        on_dispatch_drained()  # 收官后重复 drain：不得把计数打成负数

    monkeypatch.setattr(steps, "_rollout_phase", fake_rollout)
    ctx = RoundContext(it=1, pairs=[], collect_mode=COLLECT_LOCAL, eval_on_round=False)

    assert steps.step_rollout(ctx) is None
    assert gate.depth == 0, gate.trace
    assert gate.trace == [("begin", 1), ("end", 0), ("begin", 1), ("end", 0)], gate.trace
