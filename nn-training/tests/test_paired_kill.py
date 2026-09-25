"""配对中点杀臂（plan/accident.plan.md 附 §5，2026-09-21）。

事故原文：中点杀臂条件（连续 2 点 <−3pp）在 it25+it30 触发，**当时无人执行**（凌晨）——
「规则 Trustee 缺席 = 规则不存在」。本文件钉的就是把这个「人」换成守卫之后的机械判据：

  ① **同 it 对齐**：只比两臂都有读数的 it（每个 it 取末条，续腿以最新为准）；对齐点不够
     ⇒ 不判（数据不足不是证据）；
  ② **Δ = 本臂 − 对端**（pp），尾部连续 2 点 < −3pp ⇒ 杀臂；中间反弹一次即清零；
  ③ 执行面：命中 ⇒ 停腿（True）+ 落 `paired_kill` 事件（可回放）+ ABORT 判决 + 按课程下发
     云端停机达令；**前提闸**——对端账本末条 run_start 不在同一把 V 上时**不比**（不同种子流
     的读数不成对，宁可不判也不用错配读数杀一条在跑的腿）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.paired as paired_mod
from rl.config import CourseConfig
from rl.loop_guards import TrainingGuards
from rl.paired_kill import (
    PAIRED_KILL_MARGIN_PP,
    PAIRED_KILL_POINTS,
    paired_kill_enabled,
    paired_kill_overrides,
    paired_kill_self_kill,
    paired_kill_verdict,
    readings_by_iter,
)

V = 20260921


def _rows(pts: dict[int, float | None]) -> list[dict]:
    """`{it: 读数}` → eval_summary 行（None = 该轮无读数）。"""
    return [{"event": "eval_summary", "iter": it, "winRate": v, "games": 50} for it, v in pts.items()]


def _own(*vs: float) -> list[dict]:
    return _rows({i: v for i, v in enumerate(vs, start=1)})


# ────────────────────────── ② 纯函数判据 ──────────────────────────


def test_readings_take_the_last_row_per_iter_and_skip_unknown() -> None:
    """同一 it 有多行（续腿/重开）⇒ 末条为准；缺读数/非数值/bool 不当 0。"""
    got = readings_by_iter(_own(1, 2) + _rows({1: 0.9}) + _rows({2: None}))
    assert got[1] == pytest.approx(0.9), "重开一腿后同 it 的读数以最新那条为准"
    assert got[2] == pytest.approx(2)  # None 不改写已有读数
    assert readings_by_iter([{"iter": 3, "winRate": True}]) == {}
    assert readings_by_iter([{"iter": True, "winRate": 0.5}]) == {}


def test_not_enough_aligned_points_is_not_a_verdict() -> None:
    """对齐点不够 ⇒ 不判、不杀（`enough=False`：数据不足不是证据）。"""
    v = paired_kill_verdict(_own(0.5), _own(0.5))
    assert v.tripped is False and v.enough is False and "对齐" in v.reason
    # 两臂 it 不重叠（一臂重开到 it1、另一臂已 it9）⇒ 交集为空 ⇒ 不判
    assert paired_kill_verdict(_rows({1: 0.1}), _rows({9: 0.9})).tripped is False


def test_trips_after_two_consecutive_points_behind_the_peer() -> None:
    """核心判据：连续 2 点 Δ < −3pp ⇒ 杀臂。C 事故的形状就在 it25+it30。"""
    v = paired_kill_verdict(_own(0.40, 0.36, 0.34), _own(0.40, 0.40, 0.39))
    assert v.tripped is True and v.streak == PAIRED_KILL_POINTS
    assert v.delta_pp == pytest.approx(-5.0) and v.it == 3
    assert "配对差" in v.reason and "中点杀臂" in v.reason


def test_single_point_is_noise_and_a_rebound_resets_the_streak() -> None:
    """单点落后不杀；中间反弹一次即清零（与 F4/kickstart-burn 的连击语义一致）。"""
    assert paired_kill_verdict(_own(0.40, 0.34), _own(0.40, 0.40)).tripped is False
    v = paired_kill_verdict(_own(0.40, 0.30, 0.40, 0.30, 0.33), _own(0.40, 0.40, 0.40, 0.40, 0.40))
    assert v.tripped is True and v.streak == 2, "尾部两点落后（中间那点反弹已把计数清零）"


def test_boundary_and_baseline_row() -> None:
    """恰好 −3.0pp 不算「低于 3pp」；it0 基线行（两臂共同起点）不参与。"""
    assert paired_kill_verdict(_own(0.40, 0.37, 0.37), _own(0.40, 0.40, 0.40)).tripped is False
    v = paired_kill_verdict(_rows({0: 0.10, 1: 0.30, 2: 0.30}), _rows({0: 0.10, 1: 0.40, 2: 0.40}))
    assert v.tripped is True and v.it == 2, "it0 是共同起点，不参与中点判据"


def test_overrides_come_from_execution_side_config() -> None:
    """阈值走执行面（rl-config 的 `courses.<课>.paired_kill`），缺席 = 常量。"""
    assert paired_kill_overrides(None, "t-own") == (PAIRED_KILL_MARGIN_PP, PAIRED_KILL_POINTS)
    cfg = {"courses": {"t-own": {"paired_kill": {"margin_pp": 8, "points": 3}}}}
    assert paired_kill_overrides(cfg, "t-own") == (8.0, 3)
    bad = {"courses": {"t-own": {"paired_kill": {"margin_pp": "x", "points": 0}}}}
    assert paired_kill_overrides(bad, "t-own") == (PAIRED_KILL_MARGIN_PP, PAIRED_KILL_POINTS)


# ────────────────────────── ③ 执行面接线 ──────────────────────────


def _guards(tmp: Path, *, declared: int | None = V, **kw) -> TrainingGuards:
    """守卫桩：本臂 traj = `<tmp>/own`，对端 = `<tmp>/<peer>`（与生产同构）。"""
    obj = TrainingGuards.__new__(TrainingGuards)
    raw: dict[str, object] = {"name": "t-own", "mode": "per-tick"}
    if declared is not None:
        raw["paired_rotate_seed"] = declared
    course = CourseConfig.model_validate(raw)
    obj.args = SimpleNamespace(
        course_obj=course,
        course="t-own",
        course_path="curricula/t-own.jsonc",
        out="tmp/out.json",
        remote_hub_url="",
        remote_token="",
    )
    obj._jsonl_path = tmp / "training_log.jsonl"
    obj._traj_root = tmp / "own"
    obj._ledger = None
    obj._pair_kill_streak = 0
    for k, v in kw.items():
        setattr(obj.args, k, v)
    return obj


def _write_eval(dir_: Path, rows: list[dict]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "eval_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def _write_curricula(tmp: Path, peer_v: int) -> Path:
    """课程目录：本课与对端各声明同一把 V（对端的 V 可变，用来构造错配场景）。"""
    d = tmp / "curricula"
    d.mkdir(parents=True, exist_ok=True)
    (d / "t-own.jsonc").write_text(json.dumps({"name": "t-own", "paired_rotate_seed": V}), "utf-8")
    (d / "t-peer.jsonc").write_text(
        json.dumps({"name": "t-peer", "paired_rotate_seed": peer_v}), "utf-8"
    )
    return d


def _write_peer_run_start(tmp: Path, seed: int | None) -> None:
    p = tmp / "t-peer"
    p.mkdir(parents=True, exist_ok=True)
    if seed is None:
        return
    (p / "training_log.jsonl").write_text(
        json.dumps({"event": "run_start", "iter": 0, "rotateSeed": seed}) + "\n", "utf-8"
    )


def _ledger_events(tmp: Path) -> list[dict]:
    p = tmp / "training_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").strip().splitlines() if x.strip()]


def test_single_leg_is_inert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未声明 `paired_rotate_seed` ⇒ 没有「对端」这回事（零行为，不落账）。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _write_curricula(tmp_path, V), raising=True)
    _write_eval(tmp_path / "own", _own(0.40, 0.10, 0.10))
    g = _guards(tmp_path, declared=None)
    assert g._paired_kill(3, None) is False
    assert _ledger_events(tmp_path) == []


def test_mispaired_peer_is_not_compared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 前提闸：对端账本末条 run_start 不在同一把 V 上 ⇒ 不比（不同种子流的读数不成对）。

    这条比「判得准」更重要：用错配的读数去杀一条**跑在正确 V 上**的腿，比漏报更贵。
    """
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _write_curricula(tmp_path, V), raising=True)
    _write_eval(tmp_path / "own", _own(0.40, 0.20, 0.20))
    _write_eval(tmp_path / "t-peer", _own(0.40, 0.40, 0.40))
    _write_peer_run_start(tmp_path, V + 82)  # 事故现场：差 82 秒的抖动量级
    g = _guards(tmp_path)
    assert g._paired_kill(3, None) is False
    assert _ledger_events(tmp_path) == []


def test_guard_stops_the_leg_and_writes_a_replayable_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命中 ⇒ 停腿 + `paired_kill` 事件（可回放）+ ABORT 判决 + 按课程下发云端停机。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _write_curricula(tmp_path, V), raising=True)
    calls: list[tuple[bool, str]] = []

    def _fake_set(hub, token, halt, log=None, course=""):
        calls.append((halt, course))
        return True

    monkeypatch.setattr("rl.loop_guards.set_cloud_halt", _fake_set, raising=True)
    monkeypatch.setattr("rl.loop_guards.dist_common.course_name_of", lambda: "t-own", raising=True)
    _write_eval(tmp_path / "own", _own(0.40, 0.35, 0.32))
    _write_eval(tmp_path / "t-peer", _own(0.40, 0.40, 0.40))
    _write_peer_run_start(tmp_path, V)
    g = _guards(tmp_path, remote_hub_url="http://hub", remote_token="tok")
    g._cloud_halted = False
    cfg = {"courses": {"t-own": {"paired_kill": {"enabled": True}}}}

    assert g._paired_kill(3, cfg) is True, "连续两点落后 ⇒ 该杀臂"
    events = _ledger_events(tmp_path)
    kill = [e for e in events if e["event"] == "paired_kill"][-1]
    assert kill["streak"] == PAIRED_KILL_POINTS and kill["peer"] == "t-peer"
    assert kill["delta_pp"] == pytest.approx(-8.0)
    assert calls == [(True, "t-own")], "杀臂就是停止烧钱：达令要按课程发给 hub"
    assert any(e["event"] == "gate_verdict" and e.get("verdict") == "ABORT" for e in events)
    # 回放：同一批 eval 行重算，必须得到同一份 streak（判据只有一个来源）
    assert paired_kill_verdict(_own(0.40, 0.35, 0.32), _own(0.40, 0.40, 0.40)).streak == kill["streak"]
    # 状态转移才落账：同值再判一次不重复写**计数事件**（gate_verdict 会再写一条——
    # 生产侧命中即整腿终点（`step_gate` 当轮 finish），这里连调两次是为了钉住计数口径）。
    assert g._paired_kill(3, cfg) is True
    again = _ledger_events(tmp_path)
    assert len([e for e in again if e["event"] == "paired_kill"]) == 1
    assert len(again) == len(events) + 1


# ────────────────────────── ④ 对照臂永不自杀（2026-09-25 C-0 事故） ──────────────────────────


def test_self_kill_switch_defaults_to_on() -> None:
    """`courses.<课>.paired_kill.self_kill` 缺席/写坏 ⇒ True（现状对称自杀，不动老行为）。

    只有显式 `false` 才关——对照卷的命不能靠"没写配置"来保，也不能被手滑关掉。
    """
    assert paired_kill_self_kill(None, "t-own") is True
    assert paired_kill_self_kill({}, "t-own") is True
    assert paired_kill_self_kill({"courses": {"t-own": {"paired_kill": {}}}}, "t-own") is True
    bad = {"courses": {"t-own": {"paired_kill": {"self_kill": "no"}}}}
    assert paired_kill_self_kill(bad, "t-own") is True, "非 bool 不当 False"
    off = {"courses": {"t-own": {"paired_kill": {"self_kill": False}}}}
    assert paired_kill_self_kill(off, "t-own") is False


def test_control_leg_trips_but_does_not_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ C-0 事故复现：对照臂连续落后（判据 tripped）但 `self_kill: false` ⇒ 只记录不停车。

    判据照算、streak 事件照落账（复盘不断线），但不写 ABORT、不下发云端停机、不返回 True。
    杀对照等于撕毁终点 verdict——终点配对检验需要两条臂都活着。
    """
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _write_curricula(tmp_path, V), raising=True)
    calls: list[tuple[bool, str]] = []

    def _fake_set(hub, token, halt, log=None, course=""):
        calls.append((halt, course))
        return True

    monkeypatch.setattr("rl.loop_guards.set_cloud_halt", _fake_set, raising=True)
    monkeypatch.setattr("rl.loop_guards.dist_common.course_name_of", lambda: "t-own", raising=True)
    _write_eval(tmp_path / "own", _own(0.40, 0.35, 0.32))
    _write_eval(tmp_path / "t-peer", _own(0.40, 0.40, 0.40))
    _write_peer_run_start(tmp_path, V)
    cfg = {"courses": {"t-own": {"paired_kill": {"enabled": True, "self_kill": False}}}}
    g = _guards(tmp_path, remote_hub_url="http://hub", remote_token="tok")
    g._cloud_halted = False

    assert g._paired_kill(3, cfg) is False, "对照臂命中也不停车"
    events = _ledger_events(tmp_path)
    assert any(e["event"] == "paired_kill" and e["streak"] == 2 for e in events), "计数不断线"
    assert not any(
        e["event"] == "gate_verdict" and e.get("verdict") == "ABORT" for e in events
    ), "不落 ABORT 判决"
    assert calls == [], "不下发云端停机"


# ────────────────────────── ⑤ 默认关火（2026-09-26 state-init 事故） ──────────────────────────


def test_kill_is_opt_in_not_default() -> None:
    """★ state-init 事故：刚出生 11 轮的新腿被拿去跟**已归档**的 L1 比（同 V 只是门派同源，
    1789876303 是全屋种子流，不是配对实验），it5/it10 连跪两点 −4pp 当场被杀。

    配对杀臂是实验设计特性（配对 race + 杀规则），必须按课显式 `enabled: true` 才判；
    缺席/写坏 ⇒ 关（连 streak 落账都不写——没开火的枪不记弹道）。
    """
    assert paired_kill_enabled(None, "t-own") is False
    assert paired_kill_enabled({}, "t-own") is False
    assert paired_kill_enabled({"courses": {"t-own": {"paired_kill": {}}}}, "t-own") is False
    bad = {"courses": {"t-own": {"paired_kill": {"enabled": "yes"}}}}
    assert paired_kill_enabled(bad, "t-own") is False, "非 bool 不当 True"
    on = {"courses": {"t-own": {"paired_kill": {"enabled": True}}}}
    assert paired_kill_enabled(on, "t-own") is True


def test_disabled_guard_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没开火的守卫：判据 tripped 也返回 False，且**零落账**（不写 streak，不写 verdict）。"""
    monkeypatch.setattr(paired_mod, "CURRICULA_DIR", _write_curricula(tmp_path, V), raising=True)
    _write_eval(tmp_path / "own", _own(0.40, 0.35, 0.32))
    _write_eval(tmp_path / "t-peer", _own(0.40, 0.40, 0.40))
    _write_peer_run_start(tmp_path, V)
    g = _guards(tmp_path)

    assert g._paired_kill(3, None) is False
    assert _ledger_events(tmp_path) == []
