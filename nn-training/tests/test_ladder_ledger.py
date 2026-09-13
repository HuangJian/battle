"""I5（roadmap §4-I5）ladder_ledger 单测：原子写、字段级 merge、tier 边界 ack。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.ladder_ledger import TIER_BOUNDARIES, LadderLedger


@pytest.fixture
def ledger(tmp_path: Path) -> LadderLedger:
    return LadderLedger(tmp_path / "LEDGER.jsonc")


def test_load_empty_when_missing(ledger: LadderLedger) -> None:
    data = ledger.load()
    assert data == {"version": 1, "levels": {}}


def test_mark_merges_fields_without_clobber(ledger: LadderLedger) -> None:
    """双写方（TS gate / 本 CLI）字段级 merge：hypothesis 不被 lastGate 覆盖。"""
    ledger.mark("ladder-c04", hypothesis="wDmg=0 + wChip 0.03，预期 dmg/kill ↓")
    ledger.mark("ladder-c04", lastGate={"verdict": "graduate"}, status="graduated")
    entry = ledger.mark("ladder-c05", status="pending")
    levels = ledger.load()["levels"]
    c04 = levels["ladder-c04"]
    assert c04["hypothesis"].startswith("wDmg=0")  # 早前字段保留
    assert c04["status"] == "graduated"
    assert c04["lastGate"]["verdict"] == "graduate"
    assert c04["updated_at"]  # 时间戳由 mark 维护
    assert "updated_at" in entry


def test_escalate_sets_stuck_status(ledger: LadderLedger) -> None:
    entry = ledger.mark("ladder-c06", status="stuck", escalate_reason="卡门 3 周期")
    assert entry["status"] == "stuck"


def test_disk_bytes_sums_files(tmp_path: Path, ledger: LadderLedger) -> None:
    d = tmp_path / "traj"
    d.mkdir()
    (d / "a.bin").write_bytes(b"x" * 100)
    (d / "sub").mkdir()
    (d / "sub" / "b.bin").write_bytes(b"y" * 50)
    total = ledger.disk_bytes("ladder-c04", d)
    assert total == 150
    assert ledger.load()["levels"]["ladder-c04"]["disk_bytes"] == 150


def test_tier_boundaries_match_roadmap() -> None:
    """D11：c07/c14/c20 是人工放行点（ms C2 自动晋级在此停）。"""
    assert TIER_BOUNDARIES == ("ladder-c07", "ladder-c14", "ladder-c20")


def test_save_is_valid_json_roundtrip(ledger: LadderLedger) -> None:
    ledger.mark("ladder-c01", hypothesis="BC only")
    raw = ledger.path.read_text(encoding="utf-8")
    data = json.loads(raw)  # 程序生成的台账必须是合法 JSON（容错注释由读方处理）
    assert "levels" in data
