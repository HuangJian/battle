"""SCHEMA_FINGERPRINT 双端锚（hy X4，obs spec §3.4-7 / §4）。

本测试把 Python 侧钉在与 TS 侧**同一字面量**上（`tests/nn/schema-fingerprint.test.ts`
是另一半）。任一端改常量漏同步 ⇒ 各自单测红 ⇒ 在 golden 前向对不上之前现形。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from schema import (
    BOARD,
    OBS_CHANNELS,
    OBS_SCHEMA_MAJOR,
    SCALAR_DIM,
    SCALAR_LAYOUT,
    SCALAR_X_INDICES,
    SCHEMA_FINGERPRINT,
)

# 必须与 TS 侧坐标编码同锚：BOARD 常量 + 「无 .5 平局」的取整前提（见文件末尾那条用例）。
# 新 BOARD 需在此显式登记并重生成 models/coord_golden.json。
BOARD_ANCHOR = 26

# 必须与 src/nn/obs-encoder.ts::SCHEMA_FINGERPRINT 逐字相同（两边单测共锚）。
FINGERPRINT = "06142cb1"

# 必须与 src/nn/obs-encoder.ts::SCALAR_NAMES 逐字同序（双端共锚）。只钉维度挡不住
# 「交换两个标量含义」——语义序列进指纹后这类漏同步才现形。
SCALAR_LAYOUT_NAMES = [
    "slack",
    "baseDeadline",
    "lives",
    "level",
    "fireProgress",
    "turnCooldownRemaining",
    "ringCompleteness",
    "enemiesOnField",
    "spawnQueueRemaining",
    "tier_none",
    "tier_rookie",
    "tier_soldier",
    "tier_veteran",
    "tier_commander",
    "nearestEnemyDist",
    "nearestEnemyRelX",
    "nearestEnemyRelY",
    "nearestBaseDist",
    "nearestBaseRelX",
    "playerHp",
    "playerShield",
    "freeze",
    "stuck",
    "boat",
    "baseHp",
    "fence",
    "score",
    "emp",
    "iceVy",
    "iceVx",
]


def test_fingerprint_matches_ts_anchor() -> None:
    assert SCHEMA_FINGERPRINT == FINGERPRINT


def test_fingerprint_companion_constants() -> None:
    """配套常量与指纹同版（v3：16ch / 30sc / X=[15,18,29]）。"""
    assert OBS_SCHEMA_MAJOR == 3
    assert OBS_CHANNELS == 16
    assert SCALAR_DIM == 30
    assert list(SCALAR_X_INDICES) == [15, 18, 29]


def test_scalar_layout_names_match_ts_anchor() -> None:
    """标量语义序列与 TS SCALAR_NAMES 逐字同序（指纹已含该序列）。"""
    assert [name for _, name in SCALAR_LAYOUT] == SCALAR_LAYOUT_NAMES


# ---------------------------------------------------------- 坐标量化公式的取整前提（双端同锚）


def test_coord_formula_no_half_integer_collisions() -> None:
    """`.5` 平局守护：`j×255/(BOARD-1)` 不得出现半整数。

    `models/student.py::coord_channels` 用 `(j/(BOARD-1)*255).round()`，TS 侧同式但走
    `Math.round`（half-up）——`torch.round` 是**四舍六入五取偶**，一旦公式出现 `.5`，两侧
    坐标通道就会分叉。故换 BOARD 必须显式决策（并重生成 `models/coord_golden.json`），
    而不是静默接受。

    2026-09-26（item 9）自 `tests/test_coord_golden.py` 分家：这条判据一行不碰 torch，却被那边
    三条「真渲染 golden」用例连坐（那三条要 `coord_channels` ⇒ 要 torch）⇒ 搬到本文件——
    py↔TS 同锚常量的本家（BOARD 也在 `schema.py`）。
    """
    for board in (BOARD_ANCHOR,):  # 新 BOARD 需在此显式登记并重生成 golden
        vals = np.arange(board) * 255 / (board - 1)
        assert not np.any(vals % 1 == 0.5), (
            f"BOARD={board} 下坐标公式出现 .5 平局——torch.round(四舍六入五取偶) 与 "
            f"Math.round(half-up) 将分叉，请统一两侧 round 语义并重生成 coord_golden.json"
        )


def test_the_coord_board_anchor_matches_schema() -> None:
    """坐标公式用的 board 就是 `schema.BOARD`（双锚：字面量 + 常量，与本文件其它用例同式）。"""
    assert BOARD_ANCHOR == BOARD
