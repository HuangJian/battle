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
import trainer.stream as stream


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
