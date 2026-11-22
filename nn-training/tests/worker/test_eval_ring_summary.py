"""环内拦截四读数进 eval_summary（2026-11-22，experiments §161）。

为什么单独一条：`settle_eval_summary` 的均值必须是**有该字段的局**做分母（旧账本整键缺席
⇒ None = unknown）。四个读数里 `ring_kill_rate` 是**总和比**（Σ击杀/Σ进环），不是"均值比"
——后者会被局长度加权成另一个量。本用例把这两条都钉住。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from worker.eval_track import EvalSettleSpec, settle_eval_summary


def _row(stage: int, seed: int, *, intr: int, kills: int, ttk: float, bit: int, ticks: int) -> dict:
    return {
        "event": "eval",
        "iter": 7,
        "wver": "abc",
        "stage": stage,
        "seed": seed,
        "outcome": "stage_clear",
        "win": 1,
        "cleared": 1,
        "kills": kills,
        "ticks": ticks,
        "ringIntruders": intr,
        "ringKills": kills,
        "ringTtkMean": ttk,
        "baseIntruderTicks": bit,
    }


def test_ring_readings_land_in_summary(tmp_path: Path) -> None:
    led = tmp_path / "eval_log.jsonl"
    rows = [
        _row(2000, 1, intr=4, kills=2, ttk=100.0, bit=100, ticks=1000),
        _row(2001, 2, intr=8, kills=2, ttk=200.0, bit=400, ticks=1000),
    ]
    led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out: list[dict] = []
    settle_eval_summary(
        EvalSettleSpec(
            eval_jsonl=led,
            key16="abc",
            it=7,
            pairs=[(2000, 1), (2001, 2)],
            total=2,
            landed={(2000, 1), (2001, 2)},
            wins=[2],
            cleared_total=[2],
            outcomes={},
            node_games={},
            append_row=out.append,
            t_eval_start=0.0,
            rollout_winrate=None,
        )
    )
    s = [r for r in out if r.get("event") == "eval_summary"][-1]
    assert s["ring_intr_mean"] == 6.0  # (4+8)/2
    # 总和比：4/12，不是 (2/4 + 2/8)/2 = 0.375
    assert s["ring_kill_rate"] == round(4 / 12, 4)
    assert s["ring_ttk_mean"] == 150.0  # (100+200)/2
    assert s["inner_press_frac"] == 0.25  # (0.1 + 0.4)/2


def test_missing_ring_fields_are_unknown_not_zero(tmp_path: Path) -> None:
    """旧账本没有这些列 ⇒ None（unknown），绝不写成 0（否则读成"一个都没漏进来"）。"""
    led = tmp_path / "eval_log.jsonl"
    led.write_text(
        json.dumps(
            {
                "event": "eval",
                "iter": 7,
                "wver": "abc",
                "stage": 2000,
                "seed": 1,
                "outcome": "stage_clear",
                "win": 1,
                "cleared": 1,
                "kills": 3,
                "ticks": 900,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out: list[dict] = []
    settle_eval_summary(
        EvalSettleSpec(
            eval_jsonl=led,
            key16="abc",
            it=7,
            pairs=[(2000, 1)],
            total=1,
            landed={(2000, 1)},
            wins=[1],
            cleared_total=[1],
            outcomes={},
            node_games={},
            append_row=out.append,
            t_eval_start=0.0,
            rollout_winrate=None,
        )
    )
    s = [r for r in out if r.get("event") == "eval_summary"][-1]
    assert s["ring_intr_mean"] is None
    assert s["ring_kill_rate"] is None
    assert s["ring_ttk_mean"] is None
    assert s["inner_press_frac"] is None
