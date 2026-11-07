"""B9：BC 部署熵探针（on-policy 动作直方图）。

报告 §3 的教训：off-policy 教师状态探针不能当部署熵；1.3777 ≈ ln4 是**标签**熵。
这里钉住 `action_entropy` 的算术与「样本不足只是哨兵」的分界。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

#: 直接 import（不经 `.py` 路径）：行为断言不该被源码面文本口径计成欠账。
from tools import bc_entropy_probe as mod


def _row(move_hist: list[int]) -> dict:
    return {"moveHist": move_hist}


def test_uniform_direction_distribution_is_ln4() -> None:
    out = mod.action_entropy([_row([0, 1, 1, 1, 1])])
    assert out["entropy_nats"] == round(math.log(4), 4)
    assert out["entropy_norm"] == 1.0
    assert out["in_band"] is False  # ln4 明显高于期望带


def test_deterministic_policy_has_zero_entropy() -> None:
    out = mod.action_entropy([_row([0, 10, 0, 0, 0]), _row([0, 5, 0, 0, 0])])
    assert out["entropy_nats"] == 0.0
    assert out["p50"] == 0.0
    assert out["stop_frac"] == 0.0


def test_stop_slot_excluded_from_entropy_but_reported() -> None:
    out = mod.action_entropy([_row([90, 1, 1, 1, 1])])
    assert out["entropy_nats"] == round(math.log(4), 4)
    assert out["stop_frac"] == round(90 / 94, 4)


def test_no_movement_game_counts_as_zero_entropy() -> None:
    out = mod.action_entropy([_row([8, 0, 0, 0, 0]), _row([0, 1, 1, 1, 1])])
    assert out["games"] == 2
    assert out["p10"] == 0.0
    assert out["entropy_nats"] < math.log(4)


def test_missing_or_bad_hist_skipped_not_fatal() -> None:
    out = mod.action_entropy([{}, {"moveHist": None}, _row([]), _row([0, 0, 0, 0, 0])])
    assert out["games"] == 0
    assert out["skipped"] == 4
    assert out["entropy_nats"] is None


def test_probe_marks_insufficient_samples(tmp_path: Path) -> None:
    p = tmp_path / "eval_log.jsonl"
    p.write_text(
        "\n".join(json.dumps(_row([0, 1, 1, 1, 0])) for _ in range(3)) + "\nbad json\n",
        encoding="utf-8",
    )
    res = mod.probe(p, min_games=200)
    assert res["games"] == 3
    assert res["sufficient"] is False
    assert res["note"].startswith("on-policy")
