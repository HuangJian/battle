"""test_dual_track_eval —— 双轨日常评估纯函数 / 落账 / 报警边界（plan/dual-track-eval-seeds）。

覆盖（不起真 loop、不发网络）：
  - 轮转周期 it=1,2,3,4 → 段 0,1,2,0；锚点恒定；无重叠；总量 100。
  - should_dual_track：仅日常 A-eval（n_seeds==50）；it0 基线与 100/200 前缀不动。
  - 报警谓词：4.9pp 静默 / 5.0pp 持续 3 轮响 / 仅 2 轮不响 / 缺读数不响。
  - settle_eval_summary 落盘 anchor_wr / rotor_wr / overfit_gap_pp。
  - 续跑：双轨 (stage,seed) 去重键与既有账本语义一致。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_local import (
    DUAL_TRACK_ANCHOR,
    DUAL_TRACK_ROTOR,
    EVAL_SEEDS,
    OVERFIT_GAP_PP,
    OVERFIT_PERSIST_ROUNDS,
    dual_track_seeds,
    eval_done_keys,
    is_anchor_seed,
    is_rotor_seed,
    overfit_fires,
    overfit_gap_pp,
    rotor_offset,
    rotor_span,
    settle_eval_summary,
    should_dual_track,
    split_anchor_rotor,
)

WVER = "b" * 16


def test_rotor_offset_period3() -> None:
    assert rotor_offset(1) == 50
    assert rotor_offset(2) == 100
    assert rotor_offset(3) == 150
    assert rotor_offset(4) == 50
    assert rotor_offset(7) == 50
    assert rotor_offset(5) == 100
    assert rotor_offset(6) == 150


def test_rotor_span_and_dual_track_seeds() -> None:
    assert list(rotor_span(1)) == list(range(50, 100))
    assert list(rotor_span(3)) == list(range(150, 200))
    for it in (1, 2, 3, 4):
        seeds = dual_track_seeds(it)
        assert len(seeds) == DUAL_TRACK_ANCHOR + DUAL_TRACK_ROTOR
        assert seeds[:50] == EVAL_SEEDS[:50], "锚点轨与今日逐字节一致"
        assert seeds[50:] == tuple(EVAL_SEEDS[i] for i in rotor_span(it))
        assert len(set(seeds)) == 100, "锚点/轮转无重叠"
        assert set(seeds[50:]).isdisjoint(set(dual_track_seeds(it + 1)[50:])), "相邻轮轮转段互斥"


def test_anchor_rotor_membership() -> None:
    assert is_anchor_seed(860001) and is_anchor_seed(860050)
    assert not is_anchor_seed(860051)
    assert is_rotor_seed(860051) and is_rotor_seed(860200)
    assert not is_rotor_seed(860001)
    a, r = split_anchor_rotor([(2000, 860001), (2000, 860051), (2001, 860100), (0, 999)])
    assert a == [(2000, 860001)]
    assert r == [(2000, 860051), (2001, 860100)]


def test_segment_membership_is_index_based_not_numeric_range() -> None:
    """P2-7：段归属按**池内下标集合**判，不按数值区间猜。

    数值区间版在池子有洞/乱序时比真实成员集更宽——会把不属于锚点的局算进锚点轨。
    """
    from rl.eval_local import _ANCHOR_SEED_SET, _ROTOR_SEED_SET

    assert len(_ANCHOR_SEED_SET) == DUAL_TRACK_ANCHOR
    assert len(_ROTOR_SEED_SET) == len(EVAL_SEEDS) - DUAL_TRACK_ANCHOR
    # 池内精确成员
    assert set(EVAL_SEEDS[:DUAL_TRACK_ANCHOR]) == _ANCHOR_SEED_SET
    assert set(EVAL_SEEDS[DUAL_TRACK_ANCHOR:]) == _ROTOR_SEED_SET
    # 池外的值（数值上"夹在"锚点区间或轮转区间之内/之间）一律不属于任何段
    for out_of_pool in (860000, 860201, 400000, 0, 999):
        assert not is_anchor_seed(out_of_pool)
        assert not is_rotor_seed(out_of_pool)


def test_eval_seeds_pool_must_be_contiguous_ascending() -> None:
    """P2-7 前提：段成员集按下标切分 ⇒ 池子必须严格递增无重复（否则加载即炸）。"""
    import rl.eval_local as m

    assert tuple(sorted(set(m.EVAL_SEEDS))) == m.EVAL_SEEDS
    assert len(m.EVAL_SEEDS) == 200
    assert m.EVAL_SEEDS[0] == 860001 and m.EVAL_SEEDS[-1] == 860200


def test_should_dual_track_gates() -> None:
    assert should_dual_track(50, baseline=False) is True
    assert should_dual_track(50, baseline=True) is False
    assert should_dual_track(100, baseline=False) is False
    assert should_dual_track(200, baseline=False) is False
    assert should_dual_track(2, baseline=False) is False
    assert should_dual_track(0, baseline=False) is False


def test_overfit_gap_and_alarm_boundaries() -> None:
    assert overfit_gap_pp(None, 0.5) is None
    assert overfit_gap_pp(0.5, None) is None
    assert overfit_gap_pp(0.55, 0.50) == 5.0
    assert overfit_gap_pp(0.549, 0.50) == 4.9

    # 4.9pp × 3 轮：静默
    a = [0.549] * 3
    r = [0.500] * 3
    assert overfit_fires(a, r) is False
    # 5.0pp × 3 轮：响
    a5 = [0.550] * 3
    r5 = [0.500] * 3
    assert overfit_fires(a5, r5) is True
    # 仅 2 轮：不响
    assert overfit_fires(a5[:2], r5[:2]) is False
    # 缺读数：不响
    assert overfit_fires([0.55, None, 0.55], r5) is False
    # 阈值/持续轮数可覆写
    assert OVERFIT_GAP_PP == 5.0 and OVERFIT_PERSIST_ROUNDS == 3
    assert overfit_fires([0.54, 0.54, 0.54], r5, threshold_pp=4.0) is True


def _write_eval_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as jf:
        for r in rows:
            jf.write(json.dumps(r) + "\n")


def _eval_row(it: int, stage: int, seed: int, win: int) -> dict:
    return {
        "event": "eval",
        "iter": it,
        "wver": WVER,
        "stage": stage,
        "seed": seed,
        "node": "local",
        "win": win,
        "cleared": win,
        "outcome": "stage_clear" if win else "timeout",
    }


def test_settle_summary_dual_track_fields(tmp_path: Path) -> None:
    elog = tmp_path / "eval_log.jsonl"
    # it=1：锚点 2/2 胜，轮转 0/2 → gap=100pp；写台账后 settle。
    _write_eval_rows(
        elog,
        [
            _eval_row(1, 2000, 860001, 1),
            _eval_row(1, 2000, 860002, 1),
            _eval_row(1, 2000, 860051, 0),
            _eval_row(1, 2000, 860052, 0),
        ],
    )
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=1,
        pairs=[(2000, s) for s in dual_track_seeds(1)[:4]],
        total=4,
        seen={(2000, 860001), (2000, 860002), (2000, 860051), (2000, 860052)},
        wins=[2],
        cleared_total=[2],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
        course_fp="fpA",
    )
    rows = [
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    summ = next(r for r in rows if r.get("event") == "eval_summary")
    assert summ["games"] == 4 and summ["wins"] == 2
    assert summ["anchor_wr"] == 1.0
    assert summ["rotor_wr"] == 0.0
    assert summ["overfit_gap_pp"] == 100.0
    # 单轮不触发 3 轮报警（无 WARN 依赖；报警谓词已单测）


def test_settle_summary_ignores_bc_evalboard_rows(tmp_path: Path) -> None:
    """P2-7：B/C evalboard 行（同为 `event:"eval"`）不得混进本臂胜率与双轨拆段。

    病根：台账过滤只有 (event, wver, iter)，而 `batch_eval.py:703` 落的 B/C 行
    同样满足这三条（畸形批 iter=0 能撞上同 (iter,wver)）⇒ 混进来既抬高胜率分母、
    又把 B/C 的种子算进锚点/轮转段。
    """
    elog = tmp_path / "eval_log.jsonl"
    _write_eval_rows(
        elog,
        [
            _eval_row(3, 2000, 860001, 1),
            _eval_row(3, 2000, 860002, 1),
            # B 层行：同 iter / 同 wver，但带 source 字段（且是败局，会拉低读数）
            {**_eval_row(3, 2000, 860001, 0), "source": "B"},
            {**_eval_row(3, 2000, 860002, 0), "source": "C"},
        ],
    )
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=3,
        pairs=[(2000, 860001), (2000, 860002)],
        total=2,
        seen={(2000, 860001), (2000, 860002)},
        wins=[2],
        cleared_total=[2],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
    )
    summ = next(
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if '"eval_summary"' in ln
    )
    assert summ["games"] == 2, "B/C 行不得计入分母"
    assert summ["wins"] == 2, "B/C 的败局不得污染胜场"
    assert summ["winRate"] == 1.0
    assert summ["anchor_wr"] == 1.0


def test_rotation_notes_fires_only_on_real_degeneracy() -> None:
    """P2-a：eval 间隔是 3 的倍数 ⇒ 轮转轨退化为固定段（warn-only）。

    `rotor_offset` 周期 3，两次 eval 的 iter 间隔若恒为 3 的倍数，每轮落同一段。
    """
    from rl.gate_check import _rotation_notes

    def _rows(its: list[int]) -> list[dict]:
        return [{"event": "eval_summary", "iter": i, "rotor_wr": 0.5} for i in its]

    # 退化：全落同段（下标恒 150）⇒ 报警
    notes = _rotation_notes(_rows([3, 6, 9, 12]))
    assert len(notes) == 1 and "轮转退化" in notes[0]
    # 健康：it5,10,15,20 → 段 1,0,2,1 ⇒ 静默
    assert _rotation_notes(_rows([5, 10, 15, 20])) == []
    # 不足 3 个点 ⇒ 静默（看不出病灶就不报）
    assert _rotation_notes(_rows([3, 6])) == []
    # 非双轨腿（summary 无 rotor_wr）⇒ 静默
    assert _rotation_notes([{"event": "eval_summary", "iter": i} for i in (3, 6, 9)]) == []
    # 非 eval_summary 行不参与
    assert _rotation_notes([{"event": "eval", "iter": i, "rotor_wr": 0.5} for i in (3, 6, 9)]) == []


def test_settle_summary_non_dual_track_rotor_none(tmp_path: Path) -> None:
    elog = tmp_path / "eval_log.jsonl"
    _write_eval_rows(elog, [_eval_row(2, 0, 860001, 1), _eval_row(2, 0, 860002, 0)])
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=2,
        pairs=[(0, 860001), (0, 860002)],
        total=2,
        seen={(0, 860001), (0, 860002)},
        wins=[1],
        cleared_total=[1],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
    )
    summ = next(
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if '"eval_summary"' in ln
    )
    assert summ["anchor_wr"] == 0.5
    assert summ["rotor_wr"] is None
    assert summ["overfit_gap_pp"] is None


def test_resume_dedup_dual_track_seeds(tmp_path: Path) -> None:
    """双轨续跑：同 (stage,seed) 去重；跨轮换段后新 todo 非空。"""
    elog = tmp_path / "eval_log.jsonl"
    it1 = dual_track_seeds(1)
    _write_eval_rows(elog, [_eval_row(1, 2000, s, 1) for s in it1[:3]])
    done = eval_done_keys(elog, WVER, min_iter=1)
    todo1 = [(2000, s) for s in it1 if (2000, s) not in done]
    assert len(todo1) == 100 - 3
    # it=2 换轮转段：锚点 3 局已在 done，轮转 50 局全是新的
    it2 = dual_track_seeds(2)
    todo2 = [(2000, s) for s in it2 if (2000, s) not in done]
    assert len(todo2) == 50 + 47
    assert all(s[1] >= 860101 for s in todo2 if not is_anchor_seed(s[1]))


def test_dispatcher_local_dual_track_round(tmp_path: Path, monkeypatch) -> None:
    """本地 runner 注入：n_seeds=50 → 100 局双轨 + summary 三字段 + 续跑/换段。"""
    import types

    import rl.eval_dispatch as ed

    work = tmp_path / "dt"
    work.mkdir()
    rl = work / "w.json"
    rl.write_text('{"arch":{}}', encoding="utf-8")

    calls: list[tuple[int, int]] = []

    def fake_runner(
        bun,
        snap,
        stage,
        seed,
        out_dir,
        max_ticks,
        difficulty,
        timeout_sec,
        wver,
        stage_json="",
        lives_override=None,
        player_level=None,
        **kw,
    ):
        calls.append((stage, seed))
        win = 1 if seed in EVAL_SEEDS[:50] else 0  # 锚点全胜、轮转全败 → gap=100pp
        return {
            "stage": stage,
            "seed": seed,
            "outcome": "stage_clear" if win else "timeout",
            "ticks": 10,
            "win": win,
            "score": 0.1,
            "quality": 0.2,
            "dims": {},
            "elapsedSec": 0.001,
            "wver": wver,
            "mode": "eval",
        }

    monkeypatch.setattr(ed, "run_local_eval_game", fake_runner)
    args = types.SimpleNamespace(
        eval_games_per_stage=50,
        total_stages=1,
        eval_stages="2000",
        eval_window_sec=60,
        max_ticks=10,
        difficulty="hard",
    )
    cfg = {"nodes": [], "policy": {"evalLocalSlots": 4}}
    gate = threading.Event()
    gate.set()
    traj_a = work / "trajA"
    ed.dispatch_eval_round("bun", str(rl), traj_a, args, cfg, "rid.dt", 1, local_gate=gate)
    assert len(calls) == 100, f"dual-track doubles daily 50 → 100 (got {len(calls)})"
    expect = {(2000, s) for s in dual_track_seeds(1)}
    assert set(calls) == expect
    rows = [
        json.loads(ln)
        for ln in (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    summ = [r for r in rows if r.get("event") == "eval_summary"][-1]
    assert summ["games"] == 100 and summ["wins"] == 50
    assert summ["anchor_wr"] == 1.0 and summ["rotor_wr"] == 0.0
    assert summ["overfit_gap_pp"] == 100.0
    # 续跑：同 wver 再派 → already evaluated
    n_before = len(calls)
    ed.dispatch_eval_round("bun", str(rl), traj_a, args, cfg, "rid.dt", 1, local_gate=gate)
    assert len(calls) == n_before
    # it=2：轮转段换成 860101-860150，todo = 轮转 50（锚点已评过）
    ed.dispatch_eval_round(
        "bun", str(rl), work / "trajB", args, cfg, "rid.dt2", 2, local_gate=gate
    )
    assert len(calls) == n_before + 50
    rotor2 = {s for s in dual_track_seeds(2)[50:]}
    assert rotor2.issubset({c[1] for c in calls})


def test_dispatcher_prefix_when_not_daily_n(tmp_path: Path, monkeypatch) -> None:
    """n_seeds=2（单测/冒烟）仍走前缀切片，不被双轨扩成 100。"""
    import types

    import rl.eval_dispatch as ed

    work = tmp_path / "pre"
    work.mkdir()
    rl = work / "w.json"
    rl.write_text('{"arch":{"n":2}}', encoding="utf-8")
    calls: list[tuple[int, int]] = []

    def fake_runner(bun, snap, stage, seed, out_dir, max_ticks, difficulty, timeout_sec, wver, **kw):
        calls.append((stage, seed))
        return {
            "stage": stage,
            "seed": seed,
            "outcome": "timeout",
            "ticks": 1,
            "win": 0,
            "score": 0,
            "quality": 0,
            "dims": {},
            "elapsedSec": 0.001,
            "wver": wver,
            "mode": "eval",
        }

    monkeypatch.setattr(ed, "run_local_eval_game", fake_runner)
    args = types.SimpleNamespace(
        eval_games_per_stage=2,
        total_stages=1,
        eval_stages="2000",
        eval_window_sec=30,
        max_ticks=10,
        difficulty="hard",
    )
    cfg = {"nodes": [], "policy": {"evalLocalSlots": 2}}
    gate = threading.Event()
    gate.set()
    ed.dispatch_eval_round(
        "bun", str(rl), work / "traj", args, cfg, "rid.pre", 3, local_gate=gate
    )
    assert set(calls) == {(2000, 860001), (2000, 860002)}
