"""test_eval_replays_once — 导出 replay 编排器的纯函数单测（权重解析 + 文件映射）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from worker.eval_replays_once import (
    _FILENAME_RE,
    _manifest_for,
    _sha16,
    main,
    resolve_weights,
    write_fail_manifest,
)


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


def test_write_fail_manifest_schema_matches_success_payload(tmp_path: Path) -> None:
    """失败 manifest 与成功 manifest 同 schema（控制台是白名单式重建，缺键 = 等于没写）。"""
    p = tmp_path / "replay-export.json"
    write_fail_manifest(p, course="c1", it=7, wver="a" * 16, reason="权重未找到", requested=3)
    m = json.loads(p.read_text(encoding="utf-8"))
    assert m["ok"] is False
    assert (m["course"], m["iter"], m["wver"]) == ("c1", 7, "a" * 16)
    assert m["failReason"] == "权重未找到"
    assert m["requested"] == 3
    for k in ("files", "errors", "mismatches"):
        assert m[k] == []
    for k in ("weightsPath", "difficulty", "generatedAt"):
        assert isinstance(m[k], str)
    assert isinstance(m["maxTicks"], int) and not isinstance(m["maxTicks"], bool)
    assert isinstance(m["sec"], (int, float))


def test_write_fail_manifest_creates_missing_parent_dirs(tmp_path: Path) -> None:
    """traj 直下的 manifest：父目录不在也要建（控制台读的是同一个路径）。"""
    p = tmp_path / "tmp" / "course" / "replay-export.json"
    write_fail_manifest(p, course="c1", it=0, wver="c" * 16, reason="基线轮权重未找到")
    assert json.loads(p.read_text(encoding="utf-8"))["iter"] == 0


def test_write_fail_manifest_never_raises_on_unwritable_path(tmp_path: Path) -> None:
    """写盘失败只告警（best-effort）：父路径是文件也不抛 —— 返回码语义不得被它改掉。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    write_fail_manifest(blocker / "m.json", course="c1", it=1, wver="b" * 16, reason="x")


def test_write_fail_manifest_sets_fail_reason_and_clears_data_fields(tmp_path: Path) -> None:
    """F1 闭合：失败 manifest 的入参 `reason` 落于 `failReason`，
    data 字段（files/errors/mismatches）为空 —— 控制台将其视为“该轮无可导出行”。"""
    p = tmp_path / "replay-export.json"
    write_fail_manifest(
        p,
        course="c6",
        it=12,
        wver="1" * 16,
        reason="games 文件不可读: 找不到",
        requested=2,
    )
    m = json.loads(p.read_text(encoding="utf-8"))
    assert m["ok"] is False
    assert (m["course"], m["iter"], m["wver"]) == ("c6", 12, "1" * 16)
    assert m["failReason"] == "games 文件不可读: 找不到"
    assert m["requested"] == 2
    assert m["files"] == []
    assert m["errors"] == []
    assert m["mismatches"] == []


def test_write_fail_manifest_does_not_mutate_existing_ok_manifest(tmp_path: Path) -> None:
    """F1 闭合：同一路径上已有 ok manifest 时，write_fail_manifest 换写失败版——
    控制台读此路径时不再把前一轮的成功数据当成本轮可导出（归因按落盘的 manifest）。"""
    p = tmp_path / "replay-export.json"
    p.write_text(
        json.dumps(
            {
                "ok": True,
                "course": "old",
                "iter": 3,
                "wver": "a" * 16,
                "files": [{"stage": 1, "seed": 1, "file": "x.replay"}],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    write_fail_manifest(p, course="new", it=9, wver="b" * 16, reason="被轮改掉")
    m = json.loads(p.read_text(encoding="utf-8"))
    assert m["ok"] is False
    assert (m["course"], m["iter"], m["wver"]) == ("new", 9, "b" * 16)
    assert m["failReason"] == "被轮改掉"
    assert m["files"] == []


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """以给定 argv 调 `main()`（真解析器 + 真失败路径），返回退出码。"""
    monkeypatch.setattr(sys, "argv", ["eval_replays_once.py", *argv])
    return main()


def test_main_games_unreadable_writes_fail_manifest_and_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1 闭合（接线闸）：失败路径真的调了 `write_fail_manifest` ——
    只测写盘工具不够；这里驱动真 `main()`，验「games 不可读 ⇒ rc=2 + 盘上留失败 manifest」。"""
    manifest = tmp_path / "replay-export.json"
    rc = _run_main(
        monkeypatch,
        [
            "--course", "c6-chip",
            "--iter", "12",
            "--wver", "a" * 16,
            "--games", str(tmp_path / "缺失的-games.json"),
            "--out-dir", str(tmp_path / "out"),
            "--manifest", str(manifest),
        ],
    )
    assert rc == 2
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["ok"] is False
    assert (m["course"], m["iter"], m["wver"]) == ("c6-chip", 12, "a" * 16)
    assert "games 文件不可读" in m["failReason"]
    assert m["files"] == []


def test_main_empty_games_writes_fail_manifest_and_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空勾选（合法 JSON、空数组）也走失败 manifest（rc=2）——不静默产出空清单。"""
    games = tmp_path / "games.json"
    games.write_text("[]", encoding="utf-8")
    manifest = tmp_path / "replay-export.json"
    rc = _run_main(
        monkeypatch,
        [
            "--course", "c6-chip",
            "--iter", "7",
            "--wver", "b" * 16,
            "--games", str(games),
            "--out-dir", str(tmp_path / "out"),
            "--manifest", str(manifest),
        ],
    )
    assert rc == 2
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["ok"] is False and m["iter"] == 7
    assert "games 列表为空" in m["failReason"]


def test_main_crash_writes_fail_manifest_with_reason_prefix_and_returns_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未捕获异常也必须留痕（rc=4）：控制台只按 manifest 归因——不写就是「假导出完成」。"""
    import worker.eval_replays_once as mod

    def boom(_args: object, _manifest: Path) -> int:
        raise RuntimeError("权重文件坏了")

    monkeypatch.setattr(mod, "_export", boom)
    games = tmp_path / "games.json"
    games.write_text(json.dumps([{"stage": 1, "seed": 2}]), encoding="utf-8")
    manifest = tmp_path / "replay-export.json"
    rc = _run_main(
        monkeypatch,
        [
            "--course", "c6-chip",
            "--iter", "3",
            "--wver", "c" * 16,
            "--games", str(games),
            "--out-dir", str(tmp_path / "out"),
            "--manifest", str(manifest),
        ],
    )
    assert rc == 4
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["ok"] is False
    assert (m["course"], m["iter"], m["wver"]) == ("c6-chip", 3, "c" * 16)
    assert m["failReason"].startswith("crash: RuntimeError: 权重文件坏了")


