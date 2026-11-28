"""P′-4a 前置：option3 映射与探针口径（与预注册 JSON 同表）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from worker.train.option_probe import (
    INTENT_IDS,
    OPTION3_MAP,
    OPTION_DIM,
    OPTION_IDS,
    TOP1_GATE,
    _build_option_report,
)


class _Args:
    epochs = 1
    class_weight = "balanced"
    h = 64
    d = 8


def _prereg() -> dict:
    # parents: [0]=worker [1]=tests [2]=nn-training [3]=repo root
    path = Path(__file__).resolve().parents[3] / "plan" / "hier-options-prereg.v1.json"
    data: dict = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_option3_map_matches_prereg() -> None:
    p4 = _prereg()["p4"]
    assert p4["top1Acc"] == TOP1_GATE
    mapping = p4["option3Mapping"]
    assert list(mapping.keys()) == OPTION_IDS
    for i, intent in enumerate(INTENT_IDS):
        expected = [name for name, members in mapping.items() if intent in members]
        assert len(expected) == 1, f"{intent} 映射不唯一/缺失: {expected}"
        assert OPTION_IDS.index(expected[0]) == int(OPTION3_MAP[i])


def test_labels_cover_all_intents() -> None:
    assert OPTION3_MAP.shape == (len(INTENT_IDS),)
    assert set(OPTION3_MAP.tolist()) == set(range(OPTION_DIM))


def test_report_gate_uses_absolute_top1() -> None:
    y = np.array([0, 0, 1, 2, 2, 2])
    pred = np.array([0, 0, 1, 2, 1, 2])
    bucket = np.zeros_like(y)
    report = _build_option_report(y, pred, bucket, _Args(), ["s1"], n_train=6)
    assert report["overall"]["acc"] == 5 / 6
    assert report["gatePass"] is True
    assert report["confusion3x3"][2][1] == 1
    # 只预测多数类（2）：accuracy = majority，绝对门按 0.65 判（此例 0.5 → fail）。
    pred_majority = np.full_like(y, 2)
    report2 = _build_option_report(y, pred_majority, bucket, _Args(), ["s1"], n_train=6)
    assert report2["overall"]["acc"] == report2["overall"]["majorityAcc"]
    assert report2["gatePass"] is False
