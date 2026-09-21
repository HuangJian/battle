"""test_eval_timing —— P0 修复（in-loop eval 标签超前一轮）的回归锚。

旧语义：在轮 N 采集收官后派发读活指针 = W(N-1) 却标 itN。
新语义：轮 N 采集收官后为 W(N-1) 派发，标权重轮 M=N-1，读不可变归档；
收官后 drain 为最新未覆盖评估轮补派发并等收官。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_dispatch import find_archive_weights, select_delayed_eval_it
from rl.eval_local import (
    EVAL_JOIN_SOFT_SEC_DEFAULT,
    EVAL_LOCAL_EARLY_EPOCHS_DEFAULT,
    early_epoch_reached,
    eval_join_soft_sec,
    eval_local_early_epochs,
    eval_tail_overran,
    local_gate_release_plan,
)
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


# ---- in-loop eval 墙钟（2026-09-17）：软等可配 + 本机份额提前放行 ----------------


def test_eval_policy_knobs() -> None:
    """policy 旋钮读数：缺省/坏值回落安全默认（配置写错不得崩或荒谬时长）。"""
    assert eval_join_soft_sec(None) == EVAL_JOIN_SOFT_SEC_DEFAULT
    assert eval_join_soft_sec({}) == EVAL_JOIN_SOFT_SEC_DEFAULT
    assert eval_join_soft_sec({"evalJoinSoftSec": 0}) == 0.0  # 显式 0 = 不站等
    assert eval_join_soft_sec({"evalJoinSoftSec": 12.5}) == 12.5
    assert eval_join_soft_sec({"evalJoinSoftSec": -5}) == 0.0  # 负值夹到 0，不倒扣
    assert eval_join_soft_sec({"evalJoinSoftSec": "junk"}) == EVAL_JOIN_SOFT_SEC_DEFAULT
    assert eval_join_soft_sec({"evalJoinSoftSec": float("nan")}) == EVAL_JOIN_SOFT_SEC_DEFAULT

    assert eval_local_early_epochs(None) == EVAL_LOCAL_EARLY_EPOCHS_DEFAULT
    assert eval_local_early_epochs({"evalLocalEarlyEpochs": 0}) == 0  # 0 = 严格 R6
    assert eval_local_early_epochs({"evalLocalEarlyEpochs": 3}) == 3
    assert (
        eval_local_early_epochs({"evalLocalEarlyEpochs": "junk"}) == EVAL_LOCAL_EARLY_EPOCHS_DEFAULT
    )


def test_local_gate_release_plan() -> None:
    """放行档：本机不跑 PPO（远端/上云/stream）⇒ 立刻；本机 PPO ⇒ 末 epoch / on_join。"""
    assert (
        local_gate_release_plan(
            ppo_remote=True, node_rollout=False, stream_round=False, early_epochs=1
        )
        == "immediate"
    )
    assert (
        local_gate_release_plan(
            ppo_remote=False, node_rollout=True, stream_round=False, early_epochs=1
        )
        == "immediate"
    )
    assert (
        local_gate_release_plan(
            ppo_remote=False, node_rollout=False, stream_round=True, early_epochs=0
        )
        == "immediate"  # stream 轮 PPO 已在轮内跑完，即使 early=0 也立刻放
    )
    assert (
        local_gate_release_plan(
            ppo_remote=False, node_rollout=False, stream_round=False, early_epochs=1
        )
        == "last_epoch"
    )
    assert (
        local_gate_release_plan(
            ppo_remote=False, node_rollout=False, stream_round=False, early_epochs=0
        )
        == "on_join"
    )


def test_early_epoch_reached() -> None:
    """末 early 个 epoch（1 基）完成即放行；early=0 永不放；epochs<early 夹到下界。"""
    assert early_epoch_reached(1, 4, 0) is False
    assert early_epoch_reached(1, 4, 1) is False
    assert early_epoch_reached(3, 4, 1) is True  # 最后 1 个 epoch 开始
    assert early_epoch_reached(2, 4, 2) is True
    assert early_epoch_reached(1, 4, 9) is True  # 提前量 > epochs 也不得为负
    assert early_epoch_reached(4, 4, 1) is True


def test_dispatch_always_immediate_in_single_ppo_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """派发期本机份额**恒**立即放行（§3 单一 PPO 路径：本机没有 PPO 窗口可争）。

    历史（HEAD 之前）：这里分两档——远端 PPO ⇒ 立刻放行；本机 PPO ⇒ 让位到末 epoch
    （`_local_gate_epoch_hook`）/ `_join_eval`。PPO 恒在 worker 上跑之后本机永不自己
    训练，两档合并为恒 immediate（配套的 `_regate_local_eval` 也删了）。

    为什么用**裸 args**（连 `ppo` 键都没有）钉：旧实现读的就是 `args.ppo`，那条读路径
    一旦复活，这个用例当场红。
    """
    import rl.eval_dispatch as ed

    monkeypatch.setattr(ed, "dispatch_eval_bg", lambda *a, **k: threading.Thread())
    ts = _steps(tmp_path, epochs=4)
    _archive(ts, 5, "{}")
    ts._dispatch_delayed_eval(6, {})
    assert ts._eval_gate is not None and ts._eval_gate.is_set()
    # ★ 两个本机 PPO 专属方法随 §3 删除：复活即红（它们是「本机核心留给 PPO」的残留）
    assert not hasattr(ts, "_regate_local_eval")
    assert not hasattr(ts, "_local_gate_epoch_hook")


class _AliveThread:
    """挂着的 eval 线程替身：只记 join 收到的超时。"""

    def __init__(self, rec: list) -> None:
        self._rec = rec

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        self._rec.append(timeout)


def test_eval_tail_overran() -> None:
    """尾巴是否跑过自己的窗口（唯一时间基准；window<=0 = 不做这个判定）。"""
    assert eval_tail_overran(100.0, 1500.0, 200.0) is False
    assert eval_tail_overran(100.0, 1500.0, 1601.0) is True
    assert eval_tail_overran(100.0, 0.0, 99999.0) is False


def test_join_eval_does_not_wait_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省不站等：`_join_eval` 一次 join 都不发，尾巴整根交棒给下一步。"""
    joins: list = []
    ts = _steps(tmp_path)
    ts._eval_gate = threading.Event()
    ts._eval_thread = _AliveThread(joins)  # type: ignore[assignment]
    ts._eval_tail_start = 100.0
    ts._join_eval(2)
    assert joins == []  # 零固定秒数
    assert ts._eval_gate.is_set()  # 收官必放行（本机份额不得被卡死）
    assert ts._eval_tail is not None  # 交棒
    assert ts._eval_tail[0] is ts._eval_thread and ts._eval_tail[1] == 100.0


def test_join_eval_soft_wait_is_opt_in_escape_hatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """应急旋钮：policy.evalJoinSoftSec>0 才回到旧语义；上限仍受窗口预算夹住。"""
    joins: list = []
    ts = _steps(tmp_path, _tag="z")
    ts._last_dist_cfg = {"policy": {"evalJoinSoftSec": 12.5}}
    ts._eval_gate = threading.Event()
    ts._eval_thread = _AliveThread(joins)  # type: ignore[assignment]
    ts._eval_tail_start = 5.0
    ts._join_eval(2)
    assert joins == [12.5]
    assert ts._eval_tail is not None and ts._eval_tail[1] == 5.0

    # 显式值超过窗口预算 ⇒ 仍以预算为上限（不得等超过 eval_window_sec+60）
    joins.clear()
    ts3 = _steps(tmp_path, _tag="y", eval_window_sec=30)
    ts3._last_dist_cfg = {"policy": {"evalJoinSoftSec": 9999}}
    ts3._eval_gate = threading.Event()
    ts3._eval_thread = _AliveThread(joins)  # type: ignore[assignment]
    ts3._join_eval(2)
    assert joins == [90.0]


def test_sweep_eval_tail_at_rollout_boundary(tmp_path: Path) -> None:
    """收拢点（下一轮 rollout 收官）：已收官的尾巴只清账；在跑的零等待，只记日志。"""
    joins: list = []
    ts = _steps(tmp_path)
    ts._eval_tail = (_FinishedThread(joins), time.time() - 120.0)  # type: ignore[assignment]
    ts._eval_join_sec = 0.0
    ts._sweep_eval_tail()
    assert joins == [] and ts._eval_tail is None

    # 仍在跑：不 join、不计入 eval_join_sec（缺省 0 ⇒ 不补等）
    ts2 = _steps(tmp_path, _tag="running")
    ts2._eval_tail = (_AliveThread(joins), time.time() - 30.0)  # type: ignore[assignment]
    ts2._sweep_eval_tail()
    assert joins == [] and ts2._eval_tail is None
    assert ts2._eval_join_sec == 0.0

    # 应急旋钮 >0：边界处补等，秒数计入 eval_join_sec
    ts3 = _steps(tmp_path, _tag="soft")
    ts3._last_dist_cfg = {"policy": {"evalJoinSoftSec": 5}}
    ts3._eval_tail = (_AliveThread(joins), time.time())  # type: ignore[assignment]
    ts3._sweep_eval_tail()
    assert joins == [5]

    # 无尾巴 = 纯空转（非评估轮也会走这里）
    ts4 = _steps(tmp_path, _tag="none")
    ts4._sweep_eval_tail()
    assert ts4._eval_tail is None


def test_tail_harvested_when_next_rollout_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """集成：上一轮尾巴在下一轮采集落幕时被收拢（_dispatch_delayed_eval 入口）。"""
    import rl.eval_dispatch as ed

    monkeypatch.setattr(ed, "dispatch_eval_bg", lambda *a, **k: threading.Thread())
    ts = _steps(tmp_path)
    ts._eval_tail = (_FinishedThread([]), time.time() - 300.0)  # type: ignore[assignment]
    _archive(ts, 5, "{}")
    ts._dispatch_delayed_eval(6, {})  # 下一轮 rollout 收官
    assert ts._eval_tail is None  # 已收拢

    # 非评估轮（m=None）也要收拢：不是等评估轮才清账
    ts2 = _steps(tmp_path, _tag="none")
    ts2._eval_tail = (_FinishedThread([]), time.time())  # type: ignore[assignment]
    ts2._dispatch_delayed_eval(7, {})
    assert ts2._eval_tail is None


class _FinishedThread:
    """已收官的尾巴替身。"""

    def __init__(self, rec: list) -> None:
        self._rec = rec

    def is_alive(self) -> bool:
        return False

    def join(self, timeout=None) -> None:
        self._rec.append(timeout)


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
