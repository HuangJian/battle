"""共享 trainer 心跳（写侧）—— plan/single-resident-trainer §2。

被钉住的三件：① 字段形状与原子写（控制台每拍读它，半写文件会读到坏 JSON）；
② **节流**（写点/线程都不会把盘写爆：每 interval 一次）；③ 关掉的口（`NN_TRAINER_HEARTBEAT=0`）
与「IO 失败不打断训练」。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker import trainer_heartbeat as hb


def _read(p: Path) -> dict[str, Any]:
    got: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return got


def test_tick_writes_full_payload(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "trainer-heartbeat.json"
    monkeypatch.setenv("NN_TRAINER_HEARTBEAT", str(p))
    assert hb.heartbeat_path() == str(p)
    pub = hb.HeartbeatPublisher(now=lambda: 1792.0)
    pub.publish(
        steps=4413,
        waiting=2,
        courses={"y20-far": {"state": "WAIT_EXTERNAL", "rounds_done": 137, "next_it": 138}},
    )
    assert pub.tick(force=True) is True
    got = _read(p)
    assert got["pid"] == os.getpid()
    assert got["ts"] == 1792.0
    assert got["steps"] == 4413 and got["waiting"] == 2
    assert got["courses"]["y20-far"]["state"] == "WAIT_EXTERNAL"
    assert hb.read_heartbeat(str(p)) == got


def test_tick_is_throttled(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "trainer-heartbeat.json"
    monkeypatch.setenv("NN_TRAINER_HEARTBEAT", str(p))
    clock = [1000.0]
    pub = hb.HeartbeatPublisher(now=lambda: clock[0], interval=5.0)
    pub.publish(steps=1)
    assert pub.tick(force=True) is True
    clock[0] += 1.0
    assert pub.tick() is False  # 间隔内：不写（写点密集也不会把盘写爆）
    clock[0] += 5.0
    assert pub.tick() is True
    assert pub.writes == 2


def test_disabled_by_env(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "trainer-heartbeat.json"
    monkeypatch.setenv("NN_TRAINER_HEARTBEAT", "0")
    pub = hb.HeartbeatPublisher()
    assert pub.tick(force=True) is False
    pub.start()  # 不起线程
    pub.shutdown()
    assert not p.exists()
    assert hb.heartbeat_enabled() is False


def test_io_failure_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    """心跳绝不打断训练：写不进去只告警（父路径是文件 ⇒ mkdir 必炸）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("NN_TRAINER_HEARTBEAT", str(blocker / "hb.json"))
    lines: list[str] = []
    pub = hb.HeartbeatPublisher(log_fn=lines.append)
    pub.tick(force=True)  # 不抛
    assert len(lines) == 1 and lines[0].startswith("[heartbeat] WARN")


def test_start_writes_first_beat_and_stops(tmp_path: Path, monkeypatch) -> None:
    """起线程时**立刻**写第一拍（否则启动后的宽限期内控制台只能靠宽限判据）。"""
    p = tmp_path / "trainer-heartbeat.json"
    monkeypatch.setenv("NN_TRAINER_HEARTBEAT", str(p))
    pub = hb.HeartbeatPublisher(interval=0.05)
    pub.publish(steps=3)
    pub.start()
    try:
        assert p.exists(), "start() 必须立刻落第一拍"
        first = _read(p)
        assert first["steps"] == 3
        # 等的是**谓词**（写线程又落了一拍），不是「睡够一个周期」：固定时长在老机器/负载下
        # 会假红，而这里要证明的恰好是「它自己会再写」（tests/test_no_sleep_as_sync.py 的
        # 「轮询步长」族）。
        deadline = time.monotonic() + 5.0
        while pub.writes < 2 and time.monotonic() < deadline:
            time.sleep(0.01)  # sleep-ok: 轮询步长（等的是写计数，兜底只挡挂起）
        assert pub.writes >= 2, "写线程得真的在写"
    finally:
        pub.shutdown()
    assert pub._thread is None


def test_read_heartbeat_tolerates_garbage(tmp_path: Path) -> None:
    assert hb.read_heartbeat(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert hb.read_heartbeat(str(bad)) is None
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2]", encoding="utf-8")
    assert hb.read_heartbeat(str(arr)) is None
