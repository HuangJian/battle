"""tests/test_gate_check.py —— 课程结束门求值器（M1，plan/course-exit-and-shutdown.md §9）。

覆盖：禁 torch/numpy · 9 种 kind 的合成 fixture · 优先级 lattice · sustain 去重 ·
确定性（now 注入，含预算门）· override 最高优先级与非法报错 · 跨课门休眠不挡路 ·
数据缺失 = unknown 不伪装成 0 · 1 万行性能 · CLI 薄壳 exit 码。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rl.config import GatesSpec
from rl.events import write_gate_verdict
from rl.gate_check import (
    EXIT_CODES,
    BudgetInfo,
    GateOverrideError,
    evaluate,
    first_run_start_ts,
    load_override,
    normalize_rows,
    read_trend_rows,
)

ROOT = Path(__file__).resolve().parent.parent

#: 教师基线：20 局 12 胜（胜率 0.60）、场均杀 2.95、被击中 0.40。
TEACHER = {
    "corpus": "EVAL_SEEDS:860001-860020",
    "games": 20,
    "wins": 12,
    "kills": 2.95,
    "phits": 0.40,
}


def _spec(rules: list[dict], **kw: object) -> GatesSpec:
    d: dict = {"teacher": dict(TEACHER), "rules": rules}
    d.update(kw)
    return GatesSpec.model_validate(d)


def _row(
    i: int,
    *,
    wr: float = 0.60,
    kills: float = 3.0,
    phits: float = 0.40,
    pu: float = 1.0,
    timeout: float = 0.0,
    zkf: float = 0.05,
    wver: str | None = None,
    seed: str | None = None,
) -> dict:
    r: dict = {
        "event": "eval_summary",
        "iter": i,
        "wver": wver or f"w{i:03d}",
        "games": 100,
        "wins": round(wr * 100),
        "winRate": wr,
        "kills_mean": kills,
        "zero_kill_frac": zkf,
        "phits_mean": phits,
        "pickup_mean": pu,
        "timeout_frac": timeout,
    }
    if seed:
        r["seed_fp"] = seed
    return r


def _rows(n: int, **kw: object) -> list[dict]:
    return [_row(i + 1, **kw) for i in range(n)]  # type: ignore[arg-type]


FROZEN_NOW = 1_700_000_000.0


# --------------------------------------------------------------------------- 红线


def test_module_import_has_no_torch_numpy() -> None:
    """§3.3/§4.1 红线：求值器禁 torch/numpy（eval 线程与 CLI 都要轻量）。"""
    code = (
        "import sys, rl.gate_check; "
        "print('torch=' + str('torch' in sys.modules)); "
        "print('numpy=' + str('numpy' in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    kv = dict(line.split("=") for line in out.stdout.splitlines() if "=" in line)
    assert kv["torch"] == "False"
    assert kv["numpy"] == "False"


def test_no_gates_block_holds() -> None:
    """无 gates 块（老课程）→ HOLD，且读数为零（零行为变化）。"""
    res = evaluate(object(), _rows(3))
    assert res.verdict == "HOLD"
    assert res.readings == ()
    assert res.exit_code == 0


# --------------------------------------------------------------------------- G1 / sustain


def test_wins_mastery_advance_after_sustain() -> None:
    """G1：连续 sustain 轮达标才放行；少一轮 = HOLD（迟滞）。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=3,
    )
    thr = 0.6 * 0.6  # 0.36
    hold = evaluate(spec, _rows(2, wr=0.40), now=FROZEN_NOW)
    assert hold.verdict == "HOLD"  # 窗口仅 2/3
    assert "数据不足" in hold.readings[0].reason
    adv = evaluate(spec, _rows(3, wr=0.40), now=FROZEN_NOW)
    assert adv.verdict == "ADVANCE"
    assert adv.readings[0].completion == 1.0
    # 阈值下方（0.35 < 0.36）不放行
    assert evaluate(spec, _rows(3, wr=0.35), now=FROZEN_NOW).verdict == "HOLD"
    del thr


def test_same_wver_replay_does_not_inflate_streak() -> None:
    """§4.4 去重键：同 wver 重跑（崩溃恢复）只算一次，凑不满 sustain。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=3,
    )
    rows = [_row(1, wr=0.5), _row(2, wr=0.5, wver="w001"), _row(3, wr=0.5, wver="w001")]
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "数据不足" in res.readings[0].reason  # 去重后只有 2 个 wver


def test_seeds_insufficient_blocks_advance() -> None:
    """§4.4：ADVANCE 另需 ≥2 个不同 seed 集；全窗口同 seed → HOLD。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=2,
    )
    same = [_row(1, wr=0.5, seed="s0"), _row(2, wr=0.5, seed="s0")]
    res = evaluate(spec, same, now=FROZEN_NOW)
    assert res.seeds == "insufficient"
    assert res.verdict == "HOLD"
    two = [_row(1, wr=0.5, seed="s0"), _row(2, wr=0.5, seed="s1")]
    assert evaluate(spec, two, now=FROZEN_NOW).verdict == "ADVANCE"


def test_seeds_unknown_does_not_block() -> None:
    """历史语料无 seed 标识 → unknown，放行（否则 ADVANCE 永久不可达）。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=2,
    )
    res = evaluate(spec, _rows(2, wr=0.5), now=FROZEN_NOW)
    assert res.seeds == "unknown"
    assert res.verdict == "ADVANCE"


# --------------------------------------------------------------------------- G2 / G10


def test_skill_floor_three_subconditions() -> None:
    """G2：0 杀占比 / 场均杀（教师相对）/ 被击中 三项全过才计一轮。"""
    spec = _spec(
        [
            {
                "id": "G2",
                "kind": "skill_floor",
                "max_zero_kill_frac": 0.15,
                "min_kills_rel": 0.7,  # ≥ 2.065
                "max_phits_rel": 1.5,  # ≤ 0.60
                "verdict": "ADVANCE",
            }
        ],
        sustain=2,
    )
    good = _rows(2, kills=2.5, phits=0.5, zkf=0.10)
    assert evaluate(spec, good, now=FROZEN_NOW).verdict == "ADVANCE"
    bad_kills = _rows(2, kills=1.5, phits=0.5, zkf=0.10)
    assert evaluate(spec, bad_kills, now=FROZEN_NOW).verdict == "HOLD"
    bad_phits = _rows(2, kills=2.5, phits=0.9, zkf=0.10)
    assert evaluate(spec, bad_phits, now=FROZEN_NOW).verdict == "HOLD"


def test_skill_floor_missing_metric_is_unknown_not_zero() -> None:
    """缺 kills_mean（旧行/旧 agent）→ unknown 不判，绝不伪装成 0 蒙混过关。"""
    spec = _spec(
        [{"id": "G2", "kind": "skill_floor", "min_kills_rel": 0.7, "verdict": "ADVANCE"}],
        sustain=2,
    )
    rows = [{k: v for k, v in _row(i).items() if k != "kills_mean"} for i in (1, 2)]
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.readings[0].unknown is True
    assert res.verdict == "HOLD"
    assert "缺 kills_mean" in res.readings[0].reason


def test_teacher_parity() -> None:
    spec = _spec(
        [{"id": "G10", "kind": "teacher_parity", "tol_pp": 5.0, "verdict": "ADVANCE"}],
        sustain=2,
    )
    assert evaluate(spec, _rows(2, wr=0.60), now=FROZEN_NOW).verdict == "ADVANCE"
    assert evaluate(spec, _rows(2, wr=0.50), now=FROZEN_NOW).verdict == "HOLD"  # −10pp


# --------------------------------------------------------------------------- effect size / duty（§12.4 评审新增）


def test_min_gain_pp_requires_baseline() -> None:
    """min_gain_pp 的参照系 = baseline_win_rate；缺参照系 → 解析期响亮报错。"""
    with pytest.raises(Exception, match="baseline_win_rate"):
        _spec(
            [
                {
                    "id": "G1",
                    "kind": "wins_mastery",
                    "rel_teacher": 0.5,
                    "min_gain_pp": 5.0,
                    "verdict": "ADVANCE",
                }
            ],
            sustain=2,
        )


def test_effect_size_blocks_meaningless_gain() -> None:
    """§12.4：400 局下 1pp 也能"显著"，但无意义——ADVANCE 须相对起点 ≥+5pp。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.5,
                "min_gain_pp": 5.0,
                "verdict": "ADVANCE",
            }
        ],
        sustain=2,
        baseline_win_rate=0.30,
    )
    # 教师线 0.5×0.6=0.30 达标，但相对起点 0.30 只 +0.5pp（≈噪声）→ 不放行
    res = evaluate(spec, _rows(2, wr=0.32), now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "起点0.30+5.0pp" in res.readings[0].reason
    assert evaluate(spec, _rows(2, wr=0.36), now=FROZEN_NOW).verdict == "ADVANCE"


def test_require_rising_blocks_declining_window() -> None:
    """同向性：窗口胜率斜率 < 0 → 不放行（哪怕末点绝对值达标）。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.5,
                "min_gain_pp": 5.0,
                "require_rising": True,
                "verdict": "ADVANCE",
            }
        ],
        sustain=3,
        baseline_win_rate=0.20,
    )
    down = [_row(1, wr=0.45), _row(2, wr=0.42), _row(3, wr=0.38)]
    res = evaluate(spec, down, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "非同向" in res.readings[0].reason
    up = [_row(1, wr=0.38), _row(2, wr=0.42), _row(3, wr=0.45)]
    assert evaluate(spec, up, now=FROZEN_NOW).verdict == "ADVANCE"


def test_duty_gate_trips_on_accident_burn() -> None:
    """G13：c6 病灶形态——6.5h 墙钟只有 1h 训练（占空比 0.15 < 0.35）→ REMEDIATE。"""
    spec = _spec(
        [{"id": "G13", "kind": "duty", "min_train_frac": 0.35, "verdict": "REMEDIATE"}],
        sustain=1,
    )
    burned = BudgetInfo(started_at=FROZEN_NOW - 6.5 * 3600, train_sec=1.0 * 3600)
    res = evaluate(spec, _rows(1), budget=burned, now=FROZEN_NOW)
    assert res.verdict == "REMEDIATE"
    assert "烧事故" in res.reason
    healthy = BudgetInfo(started_at=FROZEN_NOW - 6.5 * 3600, train_sec=4.0 * 3600)
    assert evaluate(spec, _rows(1), budget=healthy, now=FROZEN_NOW).verdict == "HOLD"
    # 无墙钟基线 → unknown 不误停
    assert evaluate(spec, _rows(1), budget=BudgetInfo(), now=FROZEN_NOW).verdict == "HOLD"


def test_sum_train_sec_sums_iteration_events(tmp_path: Path) -> None:
    """分子跨重启从账本重算（内存累计重启归零 → 占空比被低估 → 误报烧事故）。"""
    from rl.gate_check import sum_train_sec

    p = tmp_path / "training_log.jsonl"
    rows = [
        {"event": "iteration", "ppo_sec": 70},
        {"event": "iter_error", "error": "boom"},  # 事故轮不计入
        {"event": "iteration", "ppo_sec": 50},
        {"event": "run_start"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert sum_train_sec(p) == 120.0
    assert sum_train_sec(tmp_path / "nope.jsonl") == 0.0


# --------------------------------------------------------------------------- G4 / G5 分流


def test_plateau_route_by_completion() -> None:
    """G4 分流：advance_if 最弱一环完成度 ≥ advance_frac → ADVANCE，否则 REMEDIATE。"""
    rules = [
        {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
        {"id": "G2", "kind": "skill_floor", "min_kills_rel": 0.7, "verdict": "ADVANCE"},
        {
            "id": "G4",
            "kind": "plateau",
            "window_rounds": 4,
            "tol_pp": 8.0,
            "tol_kills": 0.5,
            "metrics": ["win_rate", "kills_mean"],
            "advance_if": ["G1", "G2"],
            "advance_frac": 0.7,
            "verdict": "ADVANCE|REMEDIATE",
        },
    ]
    spec = _spec(rules, sustain=2)
    # 前后半几乎无变化 → 枯竭；G1/G2 双绿 → ADVANCE
    flat = [
        _row(1, wr=0.50, kills=2.5),
        _row(2, wr=0.51, kills=2.5),
        _row(3, wr=0.50, kills=2.5),
        _row(4, wr=0.51, kills=2.5),
    ]
    res = evaluate(spec, flat, now=FROZEN_NOW)
    assert res.verdict == "ADVANCE"
    assert res.route == "ADVANCE"
    # 仍在爬升 → 未枯竭：G4 不放行（HOLD 的那一门），但 G1 单门仍可 ADVANCE
    # （advance_requires 为空 = 无联判要求，见 §3.4-1）。
    rising = [
        _row(1, wr=0.20, kills=1.0),
        _row(2, wr=0.30, kills=1.6),
        _row(3, wr=0.40, kills=2.2),
        _row(4, wr=0.50, kills=2.8),
    ]
    up = evaluate(spec, rising, now=FROZEN_NOW)
    assert {r.rule_id: r.fired for r in up.readings}["G4"] is False
    assert up.verdict == "ADVANCE"
    # 枯竭但 G2 不达标（击杀 1.5 < 2.065）→ 完成度不足 → REMEDIATE
    stalled_bad = [
        _row(1, wr=0.50, kills=1.5),
        _row(2, wr=0.51, kills=1.5),
        _row(3, wr=0.50, kills=1.5),
        _row(4, wr=0.51, kills=1.5),
    ]
    rem = evaluate(spec, stalled_bad, now=FROZEN_NOW)
    assert rem.verdict == "REMEDIATE"
    assert rem.route == "REMEDIATE"


def test_plateau_skips_missing_metrics_but_needs_one() -> None:
    """缺 kills_mean（旧语料）时只判 win_rate，不把平台门焊死。

    c6 回溯实测：只看 win_rate 时 G4 在 it30 判枯竭 → REMEDIATE（腿省下 ~20 轮）；
    若"缺任一 metric 就 unknown"，门全程 HOLD，等于没装。
    """
    rules = [
        {"id": "G1", "kind": "wins_mastery", "rel_teacher": 1.2, "verdict": "ADVANCE"},
        {
            "id": "G4",
            "kind": "plateau",
            "window_rounds": 6,
            "tol_pp": 8.0,
            "tol_kills": 0.5,
            "metrics": ["win_rate", "kills_mean"],
            "advance_if": ["G1"],
            "advance_frac": 0.7,
            "verdict": "ADVANCE|REMEDIATE",
        },
    ]
    spec = _spec(rules, sustain=3)
    flat_no_kills = [
        {k: v for k, v in _row(i, wr=wr).items() if k != "kills_mean"}
        for i, wr in enumerate((0.28, 0.27, 0.24, 0.24, 0.23, 0.26), start=1)
    ]
    res = evaluate(spec, flat_no_kills, now=FROZEN_NOW)
    # G1（rel 1.2 × 0.60 = 0.72）远未达标 → route REMEDIATE，且 REMEDIATE 不需
    # advance_requires 背书（否则平台期永远放不出判决）
    assert res.verdict == "REMEDIATE", res.reason
    assert res.route == "REMEDIATE"


def test_plateau_all_metrics_missing_is_unknown() -> None:
    spec = _spec(
        [
            {
                "id": "G4",
                "kind": "plateau",
                "window_rounds": 4,
                "tol_pp": 8.0,
                "metrics": ["kills_mean"],
                "advance_if": [],
                "advance_frac": 0.7,
                "verdict": "ADVANCE|REMEDIATE",
            }
        ],
        sustain=1,
    )
    rows = [{k: v for k, v in _row(i).items() if k != "kills_mean"} for i in (1, 2, 3, 4)]
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.readings[0].unknown is True
    assert res.verdict == "HOLD"


def test_budget_stop_with_frozen_now() -> None:
    """G5：确定性（now 注入）+ 两条上限（max_hours / iters）。"""
    # advance_if 引用 G1 → 门必须存在（解析期强校验，§3.4-1）
    spec = _spec(
        [
            {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
            {
                "id": "G5",
                "kind": "budget",
                "advance_if": ["G1"],
                "advance_frac": 0.7,
                "verdict": "STOP",
            },
        ],
        sustain=1,
    )
    early = BudgetInfo(started_at=FROZEN_NOW - 60, max_hours=10.0, iters=0)
    over = BudgetInfo(started_at=FROZEN_NOW - 11 * 3600, max_hours=10.0, iters=0)
    # 未达标的行（wr 0.10 < 0.36）：G1 不放行，只有预算门能说话
    weak = _rows(1, wr=0.10)
    assert evaluate(spec, weak, budget=early, now=FROZEN_NOW).verdict == "HOLD"
    res = evaluate(spec, weak, budget=over, now=FROZEN_NOW)
    assert res.verdict == "STOP"
    assert res.route == "REMEDIATE"  # G1 完成度 0 < 0.7
    # 达标的行：STOP 的 route 指 ADVANCE（已完成度够，只是时间到了）
    strong = _rows(1, wr=0.60)
    res2 = evaluate(spec, strong, budget=over, now=FROZEN_NOW)
    assert res2.verdict == "STOP"  # STOP(3) > ADVANCE(1)
    assert res2.route == "ADVANCE"
    iters_done = BudgetInfo(started_at=None, max_hours=0.0, iters=10, cur_iter=10)
    assert evaluate(spec, weak, budget=iters_done, now=FROZEN_NOW).verdict == "STOP"
    # 基线不可知（无 run_start）→ 预算门 unknown，不误停
    unknown = BudgetInfo(started_at=None, max_hours=1.0, iters=0)
    assert evaluate(spec, weak, budget=unknown, now=FROZEN_NOW).verdict == "HOLD"


# --------------------------------------------------------------------------- G7 / G9 / G12


def test_course_valid_needs_rising_timeout() -> None:
    """G7：超时超限**且**斜率>0 才算课程失效（严格单调改斜率，ds-P2-1）。"""
    spec = _spec(
        [
            {
                "id": "G7",
                "kind": "course_valid",
                "max_timeout_frac": 0.15,
                "rising_rounds": 3,
                "verdict": "REMEDIATE",
            }
        ],
        sustain=1,
    )
    rising = [_row(1, timeout=0.20), _row(2, timeout=0.26), _row(3, timeout=0.32)]
    assert evaluate(spec, rising, now=FROZEN_NOW).verdict == "REMEDIATE"
    flat = [_row(1, timeout=0.30), _row(2, timeout=0.30), _row(3, timeout=0.30)]
    assert evaluate(spec, flat, now=FROZEN_NOW).verdict == "HOLD"  # 超限但不恶化
    low = [_row(1, timeout=0.05), _row(2, timeout=0.10), _row(3, timeout=0.12)]
    assert evaluate(spec, low, now=FROZEN_NOW).verdict == "HOLD"  # 上升但未超限


def test_hack_is_pause_and_beats_advance() -> None:
    """§4.2 lattice 回归：G1 绿 + G9 红 → PAUSE（G9 已由 ABORT 降级）。"""
    rules = [
        {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
        {
            "id": "G9",
            "kind": "hack",
            "window_rounds": 4,
            "pickup_up_rel": 0.5,
            "kills_down_rel": 0.2,
            "verdict": "PAUSE",
        },
    ]
    spec = _spec(rules, sustain=3)
    rows = [
        _row(1, wr=0.50, kills=3.0, pu=1.0),
        _row(2, wr=0.50, kills=3.0, pu=1.0),
        _row(3, wr=0.50, kills=2.0, pu=2.0),
        _row(4, wr=0.50, kills=2.0, pu=2.0),
    ]
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.verdict == "PAUSE", res.reason
    by_id = {r.rule_id: r for r in res.readings}
    # 两门都放行，但 lattice 取最高优先级（PAUSE > ADVANCE）
    assert by_id["G1"].released is True
    assert by_id["G9"].released is True
    assert by_id["G9"].fired is True
    assert res.exit_code == EXIT_CODES["PAUSE"]
    # 单向（只拾取涨、击杀不跌）不触发
    one_way = [
        _row(1, wr=0.50, kills=3.0, pu=1.0),
        _row(2, wr=0.50, kills=3.0, pu=1.0),
        _row(3, wr=0.50, kills=3.2, pu=2.0),
        _row(4, wr=0.50, kills=3.2, pu=2.0),
    ]
    assert evaluate(spec, one_way, now=FROZEN_NOW).verdict == "ADVANCE"


def test_override_is_highest_priority() -> None:
    """G12：override 覆盖一切（lattice 最高），reason 记入结果供审计。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=2,
    )
    res = evaluate(
        spec,
        _rows(2, wr=0.60),
        now=FROZEN_NOW,
        override={"verdict": "STOP", "reason": "人工改向：换课程"},
    )
    assert res.verdict == "STOP"
    assert "人工改向" in res.reason
    assert res.override == {"verdict": "STOP", "reason": "人工改向：换课程"}


def test_load_override_invalid_raises(tmp_path: Path) -> None:
    """§4.6：override 非法响亮报错（CLI exit 2），绝不静默忽略。"""
    p = tmp_path / "GATE_OVERRIDE.json"
    p.write_text('{"verdict": "MAYBE"}', encoding="utf-8")
    with pytest.raises(GateOverrideError):
        load_override(p)
    (tmp_path / "G2.json").write_text("[1,2]", encoding="utf-8")
    with pytest.raises(GateOverrideError):
        load_override(tmp_path / "G2.json")
    assert load_override(tmp_path / "nope.json") is None


def test_advance_requires_all_enabled_gates() -> None:
    """§3.4-1：advance_requires 里休眠门不计，启用门全绿才放行 ADVANCE。"""
    rules = [
        {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
        {"id": "G2", "kind": "skill_floor", "min_kills_rel": 0.7, "verdict": "ADVANCE"},
    ]
    spec = _spec(rules, sustain=2, advance_requires=["G1", "G2"])
    good = _rows(2, wr=0.60, kills=2.5)
    assert evaluate(spec, good, now=FROZEN_NOW).verdict == "ADVANCE"
    bad = _rows(2, wr=0.60, kills=1.0)  # G2 不达标
    res = evaluate(spec, bad, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "advance_requires 未全绿" in res.reason


def test_dormant_cross_course_gate_is_unknown_not_blocking() -> None:
    """G3/G8 首期休眠：无该课行 = 值班缺勤 → unknown，不触发任何反向判决。"""
    rules = [
        {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
        {
            "id": "G3",
            "kind": "transfer",
            "course": "c5-margin",
            "min_wins": 5,
            "min_kills": 150,
            "verdict": "ADVANCE",
            "enabled": False,
        },
    ]
    spec = _spec(rules, sustain=2)
    res = evaluate(spec, _rows(2, wr=0.60), now=FROZEN_NOW)
    by_id = {r.rule_id: r for r in res.readings}
    assert by_id["G3"].dormant is True
    assert by_id["G3"].released is False
    assert res.verdict == "ADVANCE"  # 休眠门不挡 ADVANCE


# --------------------------------------------------------------------------- IO 助手


def test_normalize_rows_dedup_and_filter() -> None:
    rows = [
        _row(1, wr=0.1),
        _row(2, wr=0.9, wver="w001"),  # 同 wver 覆盖第 1 条
        {"event": "iteration", "iter": 3},  # 非 summary 行丢弃
    ]
    out = normalize_rows(rows)
    assert [r.wver for r in out] == ["w001"]
    assert out[0].win_rate == pytest.approx(0.9)
    # course_fp 过滤：异课行剔除，旧行（无字段）按全匹配保留
    rows2 = [
        {**_row(1), "course_fp": "aa"},
        {**_row(2), "course_fp": "bb"},
        _row(3),
    ]
    assert len(normalize_rows(rows2, course_fp="aa")) == 2


def test_read_trend_rows_and_first_run_start(tmp_path: Path) -> None:
    log = tmp_path / "eval_log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(_row(1)),
                "not-json",
                json.dumps({**_row(2), "course_fp": "xx"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert len(read_trend_rows(log)) == 2
    assert len(read_trend_rows(log, course_fp="aa")) == 1  # 无 fp 的旧行保留
    assert read_trend_rows(tmp_path / "nope.jsonl") == ()

    tl = tmp_path / "training_log.jsonl"
    t0 = "2026-09-01 10:00:00"
    t1 = "2026-09-05 10:00:00"
    tl.write_text(
        json.dumps({"event": "run_start", "time": t0})
        + "\n"
        + json.dumps({"event": "run_start", "time": t1})
        + "\n",
        encoding="utf-8",
    )
    got = first_run_start_ts(tl)
    assert got is not None
    # §7：预算基线读**首条**（最后一条 = 每次重启续命）
    assert got == time.mktime(time.strptime(t0, "%Y-%m-%d %H:%M:%S"))


def test_write_gate_verdict_event_schema(tmp_path: Path) -> None:
    """§4.3：gate_verdict 事件进 training_log.jsonl（读盘面只读末个该事件）。"""
    p = tmp_path / "training_log.jsonl"
    write_gate_verdict(p, 7, "STOP", "预算到顶", route="REMEDIATE", decider="loop")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["event"] == "gate_verdict"
    assert row["iter"] == 7
    assert row["verdict"] == "STOP"
    assert row["route"] == "REMEDIATE"
    assert row["decider"] == "loop"
    assert row["readings"] == []


# --------------------------------------------------------------------------- 性能 / CLI


def test_evaluate_10k_rows_is_fast() -> None:
    """§9 M1：倒序窗口截断——1 万行 fixture 求值必须在秒级完成。"""
    spec = _spec(
        [
            {"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"},
            {
                "id": "G4",
                "kind": "plateau",
                "window_rounds": 10,
                "tol_pp": 8.0,
                "tol_kills": 0.5,
                "metrics": ["win_rate", "kills_mean"],
                "advance_if": ["G1"],
                "advance_frac": 0.7,
                "verdict": "ADVANCE|REMEDIATE",
            },
            {
                "id": "G7",
                "kind": "course_valid",
                "max_timeout_frac": 0.15,
                "rising_rounds": 5,
                "verdict": "REMEDIATE",
            },
        ],
        sustain=3,
    )
    rows = [_row(i, wr=0.5, timeout=0.05 * (i % 3)) for i in range(10_000)]
    t0 = time.perf_counter()
    res = evaluate(spec, rows, now=FROZEN_NOW)
    dt = time.perf_counter() - t0
    assert res.verdict in EXIT_CODES
    assert dt < 3.0, f"10k 行求值耗时 {dt:.2f}s（窗口截断失效？）"


def test_cli_dry_run_exit_code(tmp_path: Path) -> None:
    """CLI 薄壳：无 gates 块的课程 → HOLD → exit 0，且零写盘。"""
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rl.gate_check",
            "--course",
            "c6-margin",
            "--traj",
            str(tmp_path),
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    payload = json.loads(out.stdout)
    assert payload["verdict"] == "HOLD"
    assert list(tmp_path.iterdir()) == []  # dry-run 零写盘


# --------------------------------------------------------------------- 判决力（2026-09-11）

def test_pooled_window_survives_a_single_noisy_dip() -> None:
    """池化 vs 逐点：真实效果达标时，单点抖动不该把整条腿判成"没爬坡"。

    实测背景：100 局/点的 SE≈4.3pp，而门要求 5pp 的效果量——逐点判 = 用噪声判噪声
    （真 +7pp 也只有约 12% 概率三连过）。池化 3 点（300 局，SE≈2.6pp）后同一条曲线
    能被正确判出。这里固定 thr=0.36：窗口 (0.35, 0.42, 0.43) 池化 400/1000=0.40。
    """
    rule = {
        "id": "G1",
        "kind": "wins_mastery",
        "rel_teacher": 0.6,
        "verdict": "ADVANCE",
    }
    wrs = (0.35, 0.42, 0.43)

    per_point = _spec([dict(rule)], sustain=3)
    hold = evaluate(per_point, [_row(i + 1, wr=w) for i, w in enumerate(wrs)], now=FROZEN_NOW)
    assert hold.verdict == "HOLD"  # 第一个点 0.35 < 0.36 → 2/3，判不出爬坡
    assert hold.readings[0].completion == 2 / 3

    pooled = _spec([dict(rule, pool_window=3)], sustain=3)
    adv = evaluate(pooled, [_row(i + 1, wr=w) for i, w in enumerate(wrs)], now=FROZEN_NOW)
    assert adv.verdict == "ADVANCE"
    assert adv.readings[0].completion == 1.0
    assert "池化 3 轮 300 局 120 胜 = 0.400" in adv.readings[0].reason
    assert "SE 2.8pp" in adv.readings[0].reason  # 噪声带进 reason（审计可见）


def test_pooled_window_still_blocks_a_genuinely_low_policy() -> None:
    """池化只压噪声、不放水：窗口聚合仍低于门槛就是 HOLD（不许"某一点高就放行"）。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.6,
                "pool_window": 3,
                "verdict": "ADVANCE",
            }
        ],
        sustain=3,
    )
    rows = [_row(1, wr=0.50), _row(2, wr=0.20), _row(3, wr=0.20)]  # 池化 0.30 < 0.36
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert res.readings[0].completion == 0.0
    assert "未达标" in res.readings[0].reason


def test_pooled_conf_z_requires_effect_above_noise() -> None:
    """conf_z：效应必须高于 1σ 噪声带，不接受"点估计刚好压线"。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.0,  # 教师线让位，只看 effect size
                "min_gain_pp": 5.0,
                "pool_window": 3,
                "conf_z": 1.0,
                "verdict": "ADVANCE",
            }
        ],
        sustain=3,
        baseline_win_rate=0.30,
    )
    # 池化 p=0.35 恰等于 floor=0.35：点估计达标，但 0.35-1×SE < 0.35 → 噪声里，不放行
    edge = [_row(1, wr=0.33), _row(2, wr=0.35), _row(3, wr=0.37)]
    assert evaluate(spec, edge, now=FROZEN_NOW).verdict == "HOLD"
    # p=0.38 > floor 且高于噪声带 → 放行
    clear = [_row(1, wr=0.36), _row(2, wr=0.38), _row(3, wr=0.40)]
    res = evaluate(spec, clear, now=FROZEN_NOW)
    assert res.verdict == "ADVANCE"
    assert "效应未高于噪声" not in res.readings[0].reason


def test_pooled_insufficient_window_is_unknown_not_negative() -> None:
    """窗口不足 pool_window：unknown（阻塞 ADVANCE）而非"策略不行"（§4.5）。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.6,
                "pool_window": 3,
                "verdict": "ADVANCE",
            }
        ],
        sustain=3,
    )
    res = evaluate(spec, _rows(2, wr=0.9), now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert res.readings[0].unknown is True
    assert "数据不足" in res.readings[0].reason


def test_pooled_require_rising_blocks_downtrend() -> None:
    """池化窗口内仍要求同向（sustain 的复现语义由窗口 + 斜率承担）。"""
    spec = _spec(
        [
            {
                "id": "G1",
                "kind": "wins_mastery",
                "rel_teacher": 0.6,
                "pool_window": 3,
                "require_rising": True,
                "verdict": "ADVANCE",
            }
        ],
        sustain=3,
    )
    rows = [_row(1, wr=0.45), _row(2, wr=0.42), _row(3, wr=0.39)]  # 池化 0.42 ≥ 0.36 但下降
    res = evaluate(spec, rows, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "非同向" in res.readings[0].reason


def test_without_pool_window_behaviour_is_unchanged() -> None:
    """回归护栏：不写 pool_window = 逐点历史行为（其它课程的 gates 零变化）。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=3,
    )
    assert evaluate(spec, _rows(3, wr=0.40), now=FROZEN_NOW).verdict == "ADVANCE"
    res = evaluate(spec, _rows(3, wr=0.35), now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "轮达标" in res.readings[0].reason  # 逐点口径的 reason


# ------------------------------------------- min_train_samples（2026-09-11 重做）

def _write_iterations(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_sum_train_samples_counts_sample_passes(tmp_path: Path) -> None:
    """样本通过量 = Σ(samples × epochs)；缺 epochs 按 1 计（旧行兼容）。"""
    from rl.gate_check import sum_train_samples

    p = tmp_path / "training_log.jsonl"
    _write_iterations(
        p,
        [
            {"event": "iteration", "iter": 1, "samples": 1000, "epochs": 4},
            {"event": "iteration", "iter": 2, "samples": 2000, "epochs": 4},
            {"event": "iteration", "iter": 3, "samples": 500},  # 无 epochs
            {"event": "iter_error", "iter": 4, "samples": 999999, "epochs": 4},
            {"event": "iteration", "iter": 5},  # 无 samples
        ],
    )
    assert sum_train_samples(p) == 1000 * 4 + 2000 * 4 + 500 * 1
    assert sum_train_samples(tmp_path / "missing.jsonl") == 0.0


def test_min_train_samples_blocks_advance_with_note() -> None:
    """样本通过量不足 → ADVANCE 不放行，且 note 说清差多少（观测自带牙齿）。"""
    spec = _spec(
        [{"id": "G1", "kind": "wins_mastery", "rel_teacher": 0.6, "verdict": "ADVANCE"}],
        sustain=3,
        min_train_samples=4_000_000,
    )
    budget = BudgetInfo(train_sec=99999.0, train_samples=3_000_000.0)
    res = evaluate(spec, _rows(3, wr=0.5), budget=budget, now=FROZEN_NOW)
    assert res.verdict == "HOLD"
    assert "样本通过量" in res.reason and "3.00M < 4.00M" in res.reason
    ok = evaluate(
        spec, _rows(3, wr=0.5), budget=BudgetInfo(train_sec=1.0, train_samples=4_000_000.0),
        now=FROZEN_NOW,
    )
    assert ok.verdict == "ADVANCE"


def test_sum_train_sec_prefers_cloud_reported_seconds(tmp_path: Path) -> None:
    """真训练秒优先（ppo_cloud_sec），旧行回落 ppo_sec —— 传输/排队不再冒充训练。"""
    from rl.gate_check import sum_train_sec

    p = tmp_path / "training_log.jsonl"
    _write_iterations(
        p,
        [
            {"event": "iteration", "iter": 1, "ppo_sec": 600.0, "ppo_cloud_sec": 100.0},
            {"event": "iteration", "iter": 2, "ppo_sec": 200.0},  # 旧行：回落
            {"event": "iter_error", "iter": 3, "ppo_sec": 9999.0},
        ],
    )
    assert sum_train_sec(p) == 300.0
