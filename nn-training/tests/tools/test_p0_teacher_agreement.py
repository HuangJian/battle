"""`tools/p0_teacher_agreement.py` 的契约（plan/classic35-path.plan.md §5 C3）。

钉住：
  · 掩码 argmax 并列取首、全掩码退末位（TS `argmaxCat` 同语义）；
  · `hold` 只在 move 相同且等于执行方向时可比，不拿未知掩码猜；
  · 状态层用 t 时刻已有列，且互不替代；
  · 报告阈值 60% 与按局 bootstrap 的可复算性。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_p0_teacher_agreement_under_test", NN_ROOT / "tools" / "p0_teacher_agreement.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load()


def test_masked_argmax_tie_keeps_first() -> None:
    logits = np.array([[1.0, 1.0, 0.0]])
    mask = np.ones((1, 3), dtype=bool)
    assert C.masked_argmax(logits, mask).tolist() == [0]


def test_masked_argmax_all_masked_falls_to_last() -> None:
    logits = np.array([[5.0, 4.0, 3.0]])
    mask = np.zeros((1, 3), dtype=bool)
    assert C.masked_argmax(logits, mask).tolist() == [2]


def test_masked_argmax_respects_mask() -> None:
    logits = np.array([[5.0, 4.0, 3.0]])
    mask = np.array([[False, True, False]])
    assert C.masked_argmax(logits, mask).tolist() == [1]


def test_classify_states_uses_current_columns_only() -> None:
    n = 3
    metrics = np.zeros((n, C.METRICS_DIM), dtype=np.float64)
    extra = np.zeros((n, C.POLICY_EXTRA_DIM), dtype=np.float64)
    metrics[:, C.METRIC_INDEX["pickupDist"]] = [3.0, -1.0, 7.0]
    extra[1, C.LEAD_HIT_COLS[0]] = 1.0
    metrics[2, C.METRIC_INDEX["thDirect"]] = 1.0
    st = C.classify_states(metrics, extra, pickup_near=6.0)
    assert st["pickup_near"].tolist() == [True, False, False]
    assert st["enemy_fire_lane"].tolist() == [False, True, False]
    assert st["base_threatened"].tolist() == [False, False, True]
    assert st["tactical_any"].tolist() == [True, True, True]
    assert st["none"].tolist() == [False, False, False]


def test_action_flags_hold_only_when_direction_matches() -> None:
    flags = C.action_flags(
        move_a=np.array([1, 2, 3]),
        fire_a=np.array([0, 0, 1]),
        hold_a=np.array([0, 0, 0]),
        move_b=np.array([1, 3, 3]),
        fire_b=np.array([0, 0, 1]),
        hold_b=np.array([0, 0, 1]),
        behavior_move=np.array([1, 2, 2]),
    )
    assert flags["primary"].tolist() == [True, False, True]
    assert flags["hold_known"].tolist() == [True, False, False]
    # 第 2 行主头不一致 ⇒ 完整动作必然不一致，不需要 hold 掩码。
    assert flags["full_known"].tolist() == [True, True, False]
    assert flags["full_same"].tolist() == [True, False, False]


def test_report_rejects_below_threshold_and_counts_strata(tmp_path: Path) -> None:
    def game(stage: int, seed: int, primary: int, full: int, full_n: int) -> Any:
        stats = {name: C.empty_stat() for name in C.STRATA}
        stats[C.ALL].update(
            {"n": 10, "move": 8, "fire": 8, "primary": primary, "full_n": full_n, "full": full}
        )
        stats["pickup_near"].update({"n": 4, "primary": 2, "full_n": 4, "full": 2})
        return C.GameStat(stage, seed, 10, stats)

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    games = [game(16, 0, 7, 6, 10), game(16, 1, 7, 6, 10)]
    res = C.build_report(
        games,
        shards_root="fake",
        n_shards_total=2,
        teacher_a=str(a),
        teacher_b=str(b),
        pickup_near=6.0,
        n_boot=50,
        boot_seed=7,
    )
    assert res["rows"][C.ALL]["primary"] == pytest.approx(0.7)
    assert res["rows"][C.ALL]["full"] == pytest.approx(0.6)
    assert res["rows"][C.ALL]["move"] == pytest.approx(0.8)
    assert res["verdict"]["global_mix"] == "candidate"
    assert res["rows"]["pickup_near"]["n"] == 8
    md = C.render_markdown(res)
    assert "# C3" in md
    assert "pickup_near" in md


def test_report_rejects_hold_lowered_full_rate(tmp_path: Path) -> None:
    def game(stage: int, seed: int) -> Any:
        stats = {name: C.empty_stat() for name in C.STRATA}
        stats[C.ALL].update({"n": 10, "primary": 9, "full_n": 10, "full": 5})
        return C.GameStat(stage, seed, 10, stats)

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    res = C.build_report(
        [game(16, 0), game(16, 1)],
        shards_root="fake",
        n_shards_total=2,
        teacher_a=str(a),
        teacher_b=str(b),
        pickup_near=6.0,
        n_boot=0,
        boot_seed=7,
    )
    assert res["rows"][C.ALL]["primary"] == pytest.approx(0.9)
    assert res["rows"][C.ALL]["full"] == pytest.approx(0.5)
    assert res["verdict"]["global_mix"] == "reject_global_mix"
