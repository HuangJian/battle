"""P′-4a 前置：option 映射与探针口径（与预注册 JSON 同表）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from worker.train.option_probe import (
    INTENT_IDS,
    OPTION3_MAP,
    OPTION6_DIM,
    OPTION6_MAP,
    OPTION6_MASK,
    OPTION6_SLOT_IDS,
    OPTION_DIM,
    OPTION_IDS,
    SLOT_TO_OPTION3,
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
    if not path.exists():
        pytest.skip("预注册 JSON 不在（未跟踪文件；CI 上跳过）")
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


def test_option6_map_is_option3_plus_pathmode() -> None:
    """6 槽 = option3 折叠一致的联合槽；CLEAR 是唯一 break 源，HOLD_break 恒掩码。"""
    assert OPTION6_MAP.shape == (len(INTENT_IDS),)
    assert OPTION6_SLOT_IDS[0] == "HUNT_direct" and OPTION6_SLOT_IDS[1] == "HUNT_break"
    # option3 折叠与 3 槽表逐项一致。
    assert (SLOT_TO_OPTION3[OPTION6_MAP] == OPTION3_MAP).all()
    # break 只来自 CLEAR（idx 4）；HOLD_break 无标签。
    assert OPTION6_MAP[4] == 1
    assert 3 not in OPTION6_MAP.tolist() and 5 not in OPTION6_MAP.tolist()
    assert OPTION6_MASK.tolist() == [1, 1, 1, 1, 1, 0]
    assert OPTION6_DIM == 6


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


def test_report_option6_collapses_to_option3() -> None:
    """6 槽：pathMode 错不罚 option 层；恒空槽 recall=None；门按折叠口径。"""
    y = np.array([0, 0, 1, 2, 4, 4])
    pred = np.array([0, 1, 0, 2, 0, 4])
    bucket = np.zeros_like(y)
    report = _build_option_report(
        y,
        pred,
        bucket,
        _Args(),
        ["s1"],
        n_train=6,
        ids=OPTION6_SLOT_IDS,
        slots=OPTION6_DIM,
    )
    # option3 折叠：y3=[0,0,0,1,2,2]，pred3=[0,0,0,1,0,2] ⇒ 5/6。
    assert report["option3Acc"] == 5 / 6
    assert report["overall"]["acc"] == 3 / 6  # 槽级 3/6（0,2,4 命中）
    assert report["gatePass"] is True
    assert report["perClassRecall"]["HOLD_break"] is None
    assert report["perClassRecall"]["INTERCEPT_BASE_break"] is None
    assert report.get("confusion6x6") is not None
    assert report.get("confusion3x3") is None
