"""test_eval_timing —— P0 修复（in-loop eval 标签超前一轮）的回归锚。

旧语义：在轮 N 采集收官后派发读活指针 = W(N-1) 却标 itN。
新语义：轮 N 采集收官后为 W(N-1) 派发，标权重轮 M=N-1，读不可变归档；
收官后 drain 为最新未覆盖评估轮补派发并等收官。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_dispatch import find_archive_weights, select_delayed_eval_it
from rl.loop_steps import TrainingSteps


def _every5(m: int) -> bool:
    return m % 5 == 0


def test_select_delayed_eval_it() -> None:
    """派发轮 it → 权重轮 M=it-1（M≥1 且为评估轮，否则 None）。"""
    assert select_delayed_eval_it(6, _every5) == 5
    assert select_delayed_eval_it(1, _every5) is None  # M=0：init 由 it0 基线流覆盖
    assert select_delayed_eval_it(2, _every5) is None  # M=1 非评估轮
    assert select_delayed_eval_it(7, _every5) is None  # M=6 非评估轮
    assert select_delayed_eval_it(11, _every5) == 10


def test_find_archive_weights(tmp_path: Path) -> None:
    """归档定位：同 it 取最新、前缀隔离、缺席 None。"""
    bdir = tmp_path / "weights" / "x"
    bdir.mkdir(parents=True)
    (bdir / "x.it5.20260101-000000.json").write_text("{}", encoding="utf-8")
    new = bdir / "x.it5.20260102-000000.json"
    new.write_text("{}", encoding="utf-8")
    (bdir / "y.it5.20260103-000000.json").write_text("{}", encoding="utf-8")
    got = find_archive_weights(str(bdir), "x", 5)
    assert got == str(new)
    assert find_archive_weights(str(bdir), "x", 6) is None
    assert find_archive_weights("", "x", 5) is None
    assert find_archive_weights(str(bdir), "", 5) is None
    assert find_archive_weights(str(tmp_path / "nope"), "x", 5) is None


def _steps(tmp_path: Path, **over) -> TrainingSteps:
    ts = TrainingSteps()
    tag = str(over.pop("_tag", "d"))
    base_dir = tmp_path / tag
    bdir = base_dir / "weights"
    bdir.mkdir(parents=True, exist_ok=True)
    base = {
        "mode": "per-tick",
        "out": str(tmp_path / "weights.json"),
        "backup_dir": str(bdir),
        "backup_prefix": "x",
        "eval_games_per_stage": 2,
        "eval_stages": "0-1",
        "eval_window_sec": 60,
        "smoke": False,
    }
    base.update(over)
    ts.args = SimpleNamespace(**base)
    ts.bun = "bun-stub"
    ts._traj_dir = base_dir / "traj" / "it6"
    ts._traj_dir.mkdir(parents=True, exist_ok=True)
    ts._jsonl_path = base_dir / "traj" / "training_log.jsonl"
    ts._report = {"winRate": 0.5}
    ts._last_dist_cfg = {}
    ts._eval_on_round = _every5  # type: ignore[assignment]
    return ts


def _archive(ts: TrainingSteps, it: int, body: str) -> Path:
    p = Path(str(ts.args.backup_dir)) / f"x.it{it}.20260101-000000.json"
    p.write_text(body, encoding="utf-8")
    return p


def test_dispatch_delayed_eval_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """接线回归：第 6 轮派发标 it=5、读 it5 归档（P0 核心断言）。"""
    import rl.eval_dispatch as ed

    calls: list[dict] = []

    def _fake_bg(bun, rl_path, traj_dir, args, cfg, iter_id, it, *a, **k):
        calls.append({"rl_path": rl_path, "it": it})
        th = threading.Thread()
        return th

    monkeypatch.setattr(ed, "dispatch_eval_bg", _fake_bg)
    ts = _steps(tmp_path)
    a5 = _archive(ts, 5, '{"w":5}')
    ts._dispatch_delayed_eval(6, {})
    assert len(calls) == 1
    assert calls[0] == {"rl_path": str(a5), "it": 5}
    assert ts._eval_thread is not None and ts._eval_gate is not None


def test_dispatch_delayed_eval_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非评估权重轮不派发；intent 模式不碰 m1 线；归档缺席回落活指针。"""
    import rl.eval_dispatch as ed

    calls: list[tuple] = []

    def _fake_bg(*a, **k):  # type: ignore[no-untyped-def]
        calls.append(a)
        return threading.Thread()

    monkeypatch.setattr(ed, "dispatch_eval_bg", _fake_bg)
    ts = _steps(tmp_path)
    _archive(ts, 5, "{}")
    ts._dispatch_delayed_eval(7, {})  # M=6 非评估轮
    assert calls == [] and ts._eval_thread is None and ts._eval_gate is None

    ts2 = _steps(tmp_path, _tag="i", mode="intent")
    ts2._dispatch_delayed_eval(6, {})
    assert calls == []  # m1 流不受影响

    ts3 = _steps(tmp_path, _tag="l")  # 独立目录无归档 → 回落活指针
    Path(str(ts3.args.out)).write_text("{}", encoding="utf-8")
    ts3._dispatch_delayed_eval(6, {})
    assert len(calls) == 1 and calls[0][1] == str(ts3.args.out) and calls[0][6] == 5


def _summary(eval_log: Path, it: int, dropped: int) -> None:
    eval_log.parent.mkdir(parents=True, exist_ok=True)
    with open(eval_log, "a", encoding="utf-8") as jf:
        jf.write(json.dumps({"event": "eval_summary", "iter": it, "dropped": dropped}) + "\n")


class _DummyThread:
    def __init__(self, rec: list) -> None:
        self._rec = rec

    def join(self, timeout=None) -> None:
        self._rec.append(timeout)

    def is_alive(self) -> bool:
        return False


def test_drain_pending_eval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """drain：只收尾最新未覆盖评估轮；全覆盖/无归档则静默跳过。"""
    import rl.eval_dispatch as ed

    calls: list[dict] = []
    joins: list = []

    def _fake_bg(bun, rl_path, traj_dir, args, cfg, iter_id, it, *a, **k):
        calls.append({"rl_path": rl_path, "it": it})
        return _DummyThread(joins)

    monkeypatch.setattr(ed, "dispatch_eval_bg", _fake_bg)
    ts = _steps(tmp_path)
    elog = Path(str(ts._traj_dir).replace("it6", "")) / "eval_log.jsonl"
    _archive(ts, 5, "{}")
    a10 = _archive(ts, 10, "{}")
    _summary(elog, 5, 0)  # it5 完整
    ts._drain_pending_eval()
    assert len(calls) == 1 and calls[0] == {"rl_path": str(a10), "it": 10}
    assert len(joins) == 1  # 等收官了

    calls.clear()
    _summary(elog, 10, 0)
    ts._drain_pending_eval()
    assert calls == []  # 全覆盖 → 跳过

    ts2 = _steps(tmp_path)  # 无归档 → 跳过（elog 同文件已有完整 summary）
    ts2._traj_dir = ts._traj_dir
    ts2._drain_pending_eval()
    assert calls == []
