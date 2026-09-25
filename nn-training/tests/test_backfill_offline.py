"""tests/test_backfill_offline.py —— 已落地回传轮的**一次性补做**（用户 2026-09-23 口径）。

背景：`x20-demo-mix` 的 seg-2（it49–124）产物都在盘上，但三处课程侧落位是后加的
（`hub_server._land_offline_round_extras`）⇒ 历史轮要有个补做工具，且必须**幂等**：
反复跑不得堆积归档副本、不得把活动权重顶回旧轮。

本文件钉住：镜像 / 活动权重（取最大 it）/ 归档三件事都做了，且第二次跑不改盘上结果。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.backfill_offline import backfill_course, main


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把权重归档根指到 tmp：工装用例不得往真 `nn-training/weights/` 撒归档（那些会被控制台
    的 evalA 权重选择器当成真训练轮次列出来）。"""
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


def _landed_round(run_dir: Path, it: int) -> None:
    """造一个已落地的回传轮（与 `store_offline_artifact` 的产物同形：三件齐全）。"""
    d = run_dir / f"it-{it:03d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "weights.json").write_bytes(json.dumps({"it": it, "w": it * 1.5}).encode("utf-8"))
    (d / "opt.tar").write_bytes(b"opt" + bytes([it]))
    (d / "row.json").write_text(json.dumps({"it": it, "agg": {"kl": 0.01}}), encoding="utf-8")


def _make_course(root: Path, course: str = "c4") -> Path:
    traj = root / course
    (traj / "remote-jobs" / "offline").mkdir(parents=True, exist_ok=True)
    (traj / "training_log.jsonl").write_text("", encoding="utf-8")
    return traj


def test_backfill_mirrors_advances_and_archives(tmp_path: Path) -> None:
    """三处落位都补上：`deliver/<run>/it-NNN/`、`weights.json`（= 最大 it）、归档。"""
    traj = _make_course(tmp_path)
    run = traj / "remote-jobs" / "offline" / "run-a"
    run.mkdir(parents=True, exist_ok=True)
    for it in (48, 49, 50):
        _landed_round(run, it)
    (traj / "remote-jobs" / "offline" / "run-a" / "metrics.jsonl").write_text("", encoding="utf-8")
    # 不齐的轮（缺 row.json）不算已落地
    (run / "it-051").mkdir()

    got = backfill_course(tmp_path, "c4", log=lambda _m: None)
    assert got["rounds"] == 3 and got["skipped"] == 0, got
    # ① 镜像：与导入腿同路径同文件名
    for it in (48, 49, 50):
        mir = traj / "deliver" / "run-a" / f"it-{it:03d}"
        assert (mir / "weights.json").read_bytes() == json.dumps(
            {"it": it, "w": it * 1.5}
        ).encode("utf-8")
        assert (mir / "row.json").is_file() and (mir / "opt.tar").is_file()
    assert not (traj / "deliver" / "run-a" / "it-051").exists(), "不齐的轮不补"
    # ② 活动权重 = 最大 it
    assert (traj / "weights.json").read_bytes() == json.dumps(
        {"it": 50, "w": 75.0}
    ).encode("utf-8")
    # ③ 归档（隔离根下）
    root = Path(os.environ["BCITY_WEIGHTS_ARCHIVE_ROOT"]) / "c4"
    names = sorted(p.name for p in root.glob("c4.it*.*.json"))
    assert len(names) == 3 and all(n.startswith(("c4.it48.", "c4.it49.", "c4.it50.")) for n in names)
    # 幂等：再跑一遍不改盘上结果（归档不堆积同轮副本；活动权重不动）
    before = sorted(p.name for p in root.glob("*.json"))
    got2 = backfill_course(tmp_path, "c4", log=lambda _m: None)
    assert got2["rounds"] == 3 and got2["archived"] == 0, got2
    assert sorted(p.name for p in root.glob("*.json")) == before, "归档不得堆积同轮副本"


def test_backfill_does_not_regress_active_weights(tmp_path: Path) -> None:
    """账本已有更新的轮（本机循环落的 it99）⇒ 补做不得把活动权重顶回 50。"""
    traj = _make_course(tmp_path)
    run = traj / "remote-jobs" / "offline" / "run-a"
    run.mkdir(parents=True, exist_ok=True)
    _landed_round(run, 50)
    newer = json.dumps({"it": 99, "w": 999.0}).encode("utf-8")
    (traj / "weights.json").write_bytes(newer)
    (traj / "training_log.jsonl").write_text(
        json.dumps({"event": "iteration", "iter": 99, "kl": 0.0}) + "\n", encoding="utf-8"
    )
    backfill_course(tmp_path, "c4", log=lambda _m: None)
    assert (traj / "weights.json").read_bytes() == newer, "旧轮不得覆盖活动权重"
    assert (traj / "deliver" / "run-a" / "it-050" / "weights.json").is_file(), "镜像照做"


def test_backfill_cli_without_courses_is_quiet(tmp_path: Path) -> None:
    """traj-root 下没有带 `remote-jobs/offline` 的课 ⇒ 说一声就退（不是报错）。"""
    (tmp_path / "empty").mkdir()
    assert main(["--traj-root", str(tmp_path)]) == 0
