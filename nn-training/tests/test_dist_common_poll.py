"""test_dist_common_poll.py — _poll_result 放弃语义回归（竞速收尾洞修复，2026-09-06）。

场景：fetch_task 的竞速输家副本在 all_settled 置位后必须立即放弃轮询——实测输家副本
在慢节点上跑完注定被丢弃的局，拖住 PPO job 发布 4.5 分钟（trainer 等 slow-node）。
"""

import threading
import time

import pytest

import dist_common


def test_poll_result_abandon_fires_immediately(monkeypatch) -> None:
    """abandon_event 置位后必须立刻抛出放弃异常，不得继续轮询到 budget 耗尽。"""

    def fake_request(url: str, auth_key: str, timeout: float = 30.0, **kw):
        time.sleep(0.4)  # 模拟每次 HTTP 往返
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(dist_common, "_request", fake_request)
    monkeypatch.setattr(dist_common.time, "sleep", lambda _s: None)

    ev = threading.Event()
    threading.Timer(0.9, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(dist_common.DistError) as ei:
        dist_common._poll_result(
            "http://node",
            "tok",
            {"iterId": "r.1", "stage": 0, "seed": 1},
            budget=600.0,
            poll_s=0.2,
            abandon_event=ev,
        )
    dt = time.monotonic() - t0
    assert "abandoned" in str(ei.value)
    assert dt < 5.0, f"放弃应在 ~1s 内发生，实际 {dt:.1f}s（budget=600 远未耗尽）"


def test_poll_result_no_abandon_keeps_polling(monkeypatch) -> None:
    """未置位时行为不变：持续轮询直到 budget 耗尽抛 deadline exceeded。"""

    def fake_request(url: str, auth_key: str, timeout: float = 30.0, **kw):
        return 202, b'{"status": "running"}'

    monkeypatch.setattr(dist_common, "_request", fake_request)
    monkeypatch.setattr(dist_common.time, "sleep", lambda _s: None)

    with pytest.raises(dist_common.DistError) as ei:
        dist_common._poll_result(
            "http://node",
            "tok",
            {"iterId": "r.1", "stage": 0, "seed": 1},
            budget=1.0,
            poll_s=0.2,
        )
    assert "deadline exceeded" in str(ei.value)
