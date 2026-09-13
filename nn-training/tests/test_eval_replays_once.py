"""test_eval_replays_once — 导出 replay 编排器的纯函数单测（权重解析 + 文件映射）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rl.eval_replays_once import _FILENAME_RE, _manifest_for, _sha16, resolve_weights


def _write_weights(path: Path, payload: bytes) -> str:
    """写一个假权重文件，返回其 sha256[:16]（= 账本 wver 口径）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()[:16]


def test_resolve_weights_prefers_frozen_snapshot(tmp_path: Path) -> None:
    """快照与归档同时命中同一 wver 时，冻结快照优先（评估派发时刻的字节）。"""
    traj = tmp_path / "course"
    payload = b'{"w": "snap"}'
    wver = _write_weights(traj / "it3" / "_eval_frozen_weights.json", payload)
    _write_weights(traj / "weights.json", b'{"w": "active"}')

    hit = resolve_weights(traj, "course", wver, 3, weights_root=tmp_path / "weights")
    assert hit is not None
    assert hit.name == "_eval_frozen_weights.json"


def test_resolve_weights_baseline_snapshot_and_archive_fallback(tmp_path: Path) -> None:
    """it0 基线走 -baseline 变体；无快照时按 iter 落到归档腿。"""
    traj = tmp_path / "course"
    base_wver = _write_weights(
        traj / "it1" / "_eval_frozen_weights-baseline.json", b'{"w": "baseline"}'
    )
    assert resolve_weights(traj, "course", base_wver, 0, weights_root=tmp_path / "weights")

    leg = tmp_path / "weights" / "course"
    arch_wver = _write_weights(leg / "course.it7.20260913-000000.json", b'{"w": "arch7"}')
    hit = resolve_weights(traj, "course", arch_wver, 7, weights_root=tmp_path / "weights")
    assert hit is not None and "it7" in hit.name


def test_resolve_weights_none_when_no_match(tmp_path: Path) -> None:
    """全候选不命中 → None（fail loud，绝不静默回退到错误版本权重）。"""
    traj = tmp_path / "course"
    _write_weights(traj / "it3" / "_eval_frozen_weights.json", b'{"w": "snap"}')
    _write_weights(traj / "weights.json", b'{"w": "active"}')
    _write_weights(
        tmp_path / "weights" / "course" / "course.it3.x.json", b'{"w": "arch"}'
    )
    assert (
        resolve_weights(traj, "course", "0" * 16, 3, weights_root=tmp_path / "weights") is None
    )


def test_manifest_for_maps_canonical_filenames(tmp_path: Path) -> None:
    """canonical 文件名（stage 段 1-based）→ 0-based (stage, seed) 映射；未产出局进 errors。"""
    out = tmp_path / "replay-export"
    out.mkdir()
    (out / "hard-s08-clear-l1-t40-seed111.replay").write_text("{}")  # stage 7
    (out / "hard-s2004-died-l0-t30-seed222.replay").write_text("{}")  # stage 2003
    (out / "_stray.json").write_text("{}")
    games = [
        {"stage": 7, "seed": 111},
        {"stage": 2003, "seed": 222},
        {"stage": 9, "seed": 333},
    ]
    files, errors = _manifest_for(games, out)
    keys = {(f["stage"], f["seed"]) for f in files}
    assert keys == {(7, 111), (2003, 222)}
    assert errors == [{"stage": 9, "seed": 333, "error": "未产出 .replay"}]


def test_filename_regex_handles_custom_and_real_stages() -> None:
    """真实关（2 位）与自定义关（4 位）stage 段都可解析。"""
    m = _FILENAME_RE.search("hard-s01-timeout-l1-t40-seed860001.replay")
    assert m and (int(m.group(1)), int(m.group(2))) == (1, 860001)
    m2 = _FILENAME_RE.search("hard-s2004-died-l1-t40-seed104008.replay")
    assert m2 and (int(m2.group(1)), int(m2.group(2))) == (2004, 104008)


def test_sha16_matches_fingerprint_prefix(tmp_path: Path) -> None:
    p = tmp_path / "w.json"
    wver = _write_weights(p, b"payload")
    assert _sha16(p) == wver == hashlib.sha256(b"payload").hexdigest()[:16]
    assert _sha16(tmp_path / "missing.json") is None


def test_manifest_json_roundtrip(tmp_path: Path) -> None:
    """manifest 序列化无 nan/inf（控制台 JSON.parse 直读）。"""
    data = {"ok": True, "files": [{"stage": 1, "seed": 2, "file": "x.replay"}], "sec": 1.5}
    (tmp_path / "m.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8")) == data
