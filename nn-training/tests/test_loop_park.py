"""test_loop_park.py — 正常完成停车不断进程（2026-09-12 用户定案）。

c5-ent it80 事故复盘：loop 跑满预设 iters 后直接退出进程 → console 误报意外退出。
新语义：ALL DONE 后进程**停车不断开**——本地停止采集（不再开新 it）、向云机下发
停机指示（省配额）、账本落 run_complete 事件（console 横幅派生源）。

覆盖（纯逻辑 + 注入，不起真 loop、不碰 torch）：
  - write_run_complete：账本事件 schema（event/iter/iters/reason/time）。
  - should_park_on_done：smoke 作废干净退出 / --exit-on-done → 退出（旧行为）；
    其余一律停车。
  - TrainingLoop._park_after_completion：云停机达令（PAUSE）确有下发、在飞预采
    子进程被收敛、账本事件落盘、中断干净返回（不抛、不退出进程）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.events import write_run_complete
from rl.loop_core import TrainingLoop, should_park_on_done


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "iters": 80,
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


def test_write_run_complete_schema(tmp_path: Path) -> None:
    p = tmp_path / "training_log.jsonl"
    write_run_complete(p, 80, 80, "正常收官")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["event"] == "run_complete"
    assert row["iter"] == 80 and row["iters"] == 80
    assert "收官" in row["reason"]
    assert isinstance(row["time"], str) and len(row["time"]) == 19


def test_should_park_on_done() -> None:
    assert should_park_on_done(_args(), smoke_void=False) is True
    assert should_park_on_done(_args(), smoke_void=True) is False  # --smoke 作废退出不断
    assert should_park_on_done(_args(exit_on_done=True), smoke_void=False) is False


def test_park_halts_cloud_writes_ledger_and_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _loop(tmp_path)
    halts: list[tuple[int, str]] = []
    monkeypatch.setattr(
        TrainingLoop, "_sync_cloud_halt", lambda self, it, v: halts.append((it, v))
    )
    calls = {"n": 0}

    def _sleep_once(_s: float) -> None:
        calls["n"] += 1
        raise KeyboardInterrupt  # 停车 sleep 可中断 → 干净返回

    monkeypatch.setattr("time.sleep", _sleep_once)
    loop._park_after_completion(80)
    assert halts == [(80, "PAUSE")]  # 云停机达令确有下发
    rows = [
        json.loads(line)
        for line in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and rows[-1]["event"] == "run_complete" and rows[-1]["iter"] == 80
    assert calls["n"] == 1  # 进过停车等待（没直接返回也没 hang）


def test_park_drains_live_precollect_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)
    monkeypatch.setattr("time.sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))

    class _Child:
        terminated = False

        def poll(self):
            return None  # 还活着

        def terminate(self):
            self.terminated = True

    child = _Child()
    loop._collect_child = child  # type: ignore[assignment]
    loop._park_after_completion(80)
    assert child.terminated is True  # 在飞预采子进程被收敛，不留孤儿空烧
    assert loop._collect_child is None


def test_park_without_hub_is_still_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无 hub（纯本机）时 _sync_cloud_halt 真跑也不碰网络、不抛。"""
    import rl.loop_guards as guards

    sent: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        guards, "set_cloud_halt", lambda url, tok, halt, **kw: sent.append((url, tok, halt))
    )
    loop = _loop(tmp_path, remote_hub_url="http://127.0.0.1:1", remote_token="t")
    loop._sync_cloud_halt(80, "PAUSE")
    assert sent == [("http://127.0.0.1:1", "t", True)]
    loop2 = _loop(tmp_path)  # 无 hub → 短路，零调用
    loop2._sync_cloud_halt(80, "PAUSE")
    assert len(sent) == 1


def test_park_keeps_claiming_evalboard_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停车期每轮唤醒复用 idle 窗认领 EvalBoard B 批（60s 粒度，配置热读）。"""
    import dist_common

    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)
    cfg: dict = {"nodes": [], "policy": {}}
    monkeypatch.setattr(dist_common, "load_dist_config", lambda: cfg)
    idles: list[tuple[int, object]] = []
    monkeypatch.setattr(
        TrainingLoop, "_evalboard_idle", lambda self, it, dc: idles.append((it, dc))
    )
    wakes = {"n": 0}

    def _sleep_3(_s: float) -> None:
        wakes["n"] += 1
        if wakes["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", _sleep_3)
    loop._park_after_completion(80)
    assert wakes["n"] == 3
    assert idles == [(80, cfg), (80, cfg)]  # 前两次唤醒各认领一次，第三次中断退出


def test_park_claim_failure_never_breaks_parking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """认领抛异常 → 自吞继续停车（不炸进程、不退出）。"""
    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)

    def _boom(self, it, dc):
        raise RuntimeError("queue unreadable")

    monkeypatch.setattr(TrainingLoop, "_evalboard_idle", _boom)
    monkeypatch.setattr("time.sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))
    loop._park_after_completion(80)  # 不抛即过
    rows = [
        json.loads(line)
        for line in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and rows[-1]["event"] == "run_complete"
