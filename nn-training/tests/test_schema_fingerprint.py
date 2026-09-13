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

from schema import (
    OBS_CHANNELS,
    OBS_SCHEMA_MAJOR,
    SCALAR_DIM,
    SCALAR_X_INDICES,
    SCHEMA_FINGERPRINT,
)

# 必须与 src/nn/obs-encoder.ts::SCHEMA_FINGERPRINT 逐字相同（两边单测共锚）。
FINGERPRINT = "ccf8bfab"


def test_fingerprint_matches_ts_anchor() -> None:
    assert SCHEMA_FINGERPRINT == FINGERPRINT


def test_fingerprint_companion_constants() -> None:
    """配套常量与指纹同版（v3：16ch / 30sc / X=[15,18,29]）。"""
    assert OBS_SCHEMA_MAJOR == 3
    assert OBS_CHANNELS == 16
    assert SCALAR_DIM == 30
    assert list(SCALAR_X_INDICES) == [15, 18, 29]
