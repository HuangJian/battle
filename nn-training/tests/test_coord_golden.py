"""CoordConv 坐标通道 golden 双向校验（plan/python-refactor.md P2-5）。

**背景**：Python 侧 `models/student.py:coord_channels` 用 `torch.round()`（banker's
rounding，四舍六入五取偶），TS 侧 `src/nn/infer.ts:428-429` 用 `Math.round()`
（half-up）。BOARD=26 时 `j×255/25 = j×10.2` 恰好不产生 `.5` 平局，两侧侥幸一致；
**一旦 BOARD 变更，两侧会静默分叉**，而 TS 运行时逐字节复现前向是硬约束
（BN-free 的同一原因）。

本文件把坐标通道固化为 golden（`models/coord_golden.json`）：
  * Python 侧：`coord_channels(26)` 必须与 golden 逐值相等；
  * TS 侧（tests/nn/coord-golden.test.ts）：infer.ts 的坐标公式必须与 golden 逐值相等。

任一方向改动公式或 BOARD 都会破坏对应侧的测试——把"靠注释自觉"变成"靠测试守护"。

**`.5` 平局守护（已分家）**：`j×255/25` 无 `.5` 值这条判据本身一行不碰 torch，2026-09-26
（item 9）搬到 `tests/test_schema_fingerprint.py`（py↔TS 同锚常量的本家，BOARD 在
`schema.py`）——否则它被本文件那三条「真渲染 golden」用例连坐 torch。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.student import coord_channels

GOLDEN_PATH = ROOT / "models" / "coord_golden.json"
GOLDEN_BOARD = 26


def _load_golden() -> np.ndarray:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert data["board"] == GOLDEN_BOARD, f"golden board={data['board']} ≠ 当前 {GOLDEN_BOARD}"
    return np.asarray(data["coords"], dtype=np.uint8).reshape(data["shape"])


def test_coord_channels_match_golden() -> None:
    """Python coord_channels 与 golden 逐值相等（P2-5 守护）。"""
    golden = _load_golden()
    ch = coord_channels(GOLDEN_BOARD, torch.device("cpu")).numpy()
    np.testing.assert_array_equal(ch, golden)


def test_coord_golden_is_committed_fixture() -> None:
    """golden 文件存在且可读（防误删导致测试静默跳过）。"""
    assert GOLDEN_PATH.is_file(), f"golden 缺失: {GOLDEN_PATH}"
    ch = coord_channels(GOLDEN_BOARD, torch.device("cpu")).numpy()
    assert ch.shape == (2, GOLDEN_BOARD, GOLDEN_BOARD)
    assert int(ch[0, 0, 0]) == 0 and int(ch[0, -1, -1]) == 255  # 端点语义


def test_coord_channels_shape_and_range() -> None:
    """坐标通道基本语义：x 沿列变化、y 沿行变化、值域 0..255。"""
    ch = coord_channels(GOLDEN_BOARD, torch.device("cpu")).numpy()
    # x 通道：同一行内列方向递增；y 通道：同一列内行方向递增
    assert (np.diff(ch[0], axis=1) >= 0).all(), "x 通道应沿列递增"
    assert (np.diff(ch[1], axis=0) >= 0).all(), "y 通道应沿行递增"
    assert ch.min() >= 0 and ch.max() <= 255
