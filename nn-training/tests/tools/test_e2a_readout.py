"""`tools/e2a_readout.py` 的纯函数真值表：ablate 聚合闸 + stem 新列 L2。

实际跑局（spawn export-eval-game）不在单测里做（慢、且需要权重文件）；这里钉住两条
判决数学：ch16 move 翻转率 ≥ ch0 的 1/3（ch0=0 ⇒ invalid），以及 v7 stem 第 16 列 L2。
"""

from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path

import numpy as np

NN_ROOT = Path(__file__).resolve().parents[2]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))
sys.path.insert(0, str(NN_ROOT / "tools"))

TOOL = importlib.import_module("e2a_readout")


def _rep(decisions: int, ch0_move: int, ch16_move: int) -> dict:
    def g(n: int) -> dict:
        return {
            "flipsMove": n,
            "flipsFire": 0,
            "flipsHold": 0,
            "dMove": 0.0,
            "dFire": 0.0,
            "dValue": 0.0,
        }

    return {
        "ablate": {
            "decisions": decisions,
            "groups": {"ch0": g(ch0_move), "ch16": g(ch16_move)},
        }
    }


def test_gate_passes_at_one_third() -> None:
    agg = TOOL.aggregate_ablate([_rep(100, 30, 12), _rep(100, 30, 8)])
    # 200 拍里 ch0 60 翻、ch16 20 翻 ⇒ 0.10/0.30 = 1/3 ⇒ pass
    assert agg["verdict"]["result"] == "pass"
    assert agg["groups"]["ch0"]["moveFlipRate"] == 0.3
    assert agg["groups"]["ch16"]["moveFlipRate"] == 0.1


def test_gate_fails_below_one_third() -> None:
    agg = TOOL.aggregate_ablate([_rep(100, 30, 9)])
    assert agg["verdict"]["result"] == "fail"


def test_gate_invalid_without_positive_control() -> None:
    agg = TOOL.aggregate_ablate([_rep(100, 0, 10)])
    assert agg["verdict"]["result"] == "invalid"


def test_rows_without_ablate_are_ignored() -> None:
    agg = TOOL.aggregate_ablate([{"outcome": "gameover"}, _rep(10, 3, 1)])
    assert agg["games_with_ablate"] == 1 and agg["decisions"] == 10


def test_stem_new_col_l2_reads_channel_16() -> None:
    h, c, k1, k2 = 1, 19, 1, 1
    arr = np.zeros((h, c, k1, k2), dtype="<f4")
    arr[:, 16, :, :] = 3.0
    data = base64.b64encode(arr.tobytes()).decode("ascii")
    meta = {"params": {"stem.weight": {"shape": [h, c, k1, k2], "data": data}}}
    got = TOOL.stem_new_col_l2(meta)
    assert got["new_col_index"] == 16
    assert got["new_col_l2"] == 3.0
    assert got["old_obs_cols_mean_l2"] == 0.0
