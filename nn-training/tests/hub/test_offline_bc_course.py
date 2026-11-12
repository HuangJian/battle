"""tests/hub/test_offline_bc_course.py — BC 课程在 `/offline/tasks` 里是**一等公民**。

2026-11-12 用户口径：BC 课程与 RL 课程在自主云机执行过程中同等对待（不再只是空档兜底）。
hub 侧要保证：

  * 每行带 `kind`（`bc` / `rl`）；BC 行没有 task pack，但照样可领；
  * BC 课程**不**发课程租约（`claim_offline` ⇒ `bc_course`）——它的互斥是
    `bc-exclusive.json`（课程级独占锁），走 `/jobs/{id}/claim`；
  * 账本收官（`run_complete` 晚于最后一次 `run_start`）⇒ `completed`、不可领；
  * 别的盘持着 BC 独占锁 ⇒ 行里可见 `bc_owner` / `bc_job`，`claimable=false`。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from common.protocol import COURSE_ENABLE_MARKER
from hub.server import _HubQueue
from hub.task_pack import TASK_STATE_CLAIMED, TASK_STATE_COMPLETED


def _boot_bc(tmp_path: Path, course: str = "bc-demo") -> tuple[_HubQueue, Path]:
    """造一门 BC 课：课程目录在 `<root>/<course>`，课程文件在 `<root>/../curricula`。"""
    root = tmp_path / "tmp"
    root.mkdir()
    (tmp_path / "curricula").mkdir()
    (tmp_path / "curricula" / f"{course}.bc.jsonc").write_text("{}", encoding="utf-8")
    d = root / course
    (d / "remote-jobs").mkdir(parents=True)
    (d / "training_log.jsonl").touch()
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub = _HubQueue({}, discover_root=root)
    hub.discover(force=True)
    return hub, d


def test_bc_course_is_a_claimable_kind_bc_row_without_a_pack(tmp_path: Path) -> None:
    hub, _d = _boot_bc(tmp_path)
    row = hub.offline_tasks()[0]
    assert row["course"] == "bc-demo"
    assert row["kind"] == "bc"
    assert row["pack"] is None
    assert row["state"] == "ready"
    assert row["claimable"] is True
    assert row["bc_owner"] == "" and row["bc_job"] == ""
    assert row["hold"] == {} and row["pending_export"] == {}


def test_bc_course_refuses_a_course_lease(tmp_path: Path) -> None:
    """BC 走课程级独占锁，不领课程 hold（否则 hold 闸会挡住自己的 BC job）。"""
    hub, _d = _boot_bc(tmp_path)
    assert hub.claim_offline("bc-demo", "w1") == ({}, "bc_course")
    assert hub.hold_of("bc-demo") == {}


def test_bc_course_completed_from_the_ledger_is_not_claimable(tmp_path: Path) -> None:
    hub, d = _boot_bc(tmp_path)
    log = d / "training_log.jsonl"
    log.write_text('{"event": "run_start"}\n', encoding="utf-8")
    row = hub.offline_tasks()[0]
    assert row["state"] == "ready" and row["claimable"] is True

    log.write_text(
        '{"event": "run_start"}\n{"event": "run_complete"}\n', encoding="utf-8"
    )
    row = hub.offline_tasks()[0]
    assert row["state"] == TASK_STATE_COMPLETED
    assert row["claimable"] is False
    assert row["reason"].startswith("completed:")


def test_bc_exclusive_owner_is_visible_and_blocks_claimable(tmp_path: Path) -> None:
    hub, d = _boot_bc(tmp_path)
    now = time.time()
    # 课程级锁住 `_JobStore.job_root`（= `<course>/remote-jobs`），与 `_bc_lock_path` 同源。
    (d / "remote-jobs" / "bc-exclusive.json").write_text(
        json.dumps(
            {
                "job_id": "j1",
                "worker_id": "w2",
                "claimed_at": now,
                "progress_at": now,
            }
        ),
        encoding="utf-8",
    )
    row = hub.offline_tasks()[0]
    assert row["kind"] == "bc"
    assert row["bc_owner"] == "w2" and row["bc_job"] == "j1"
    assert row["state"] == TASK_STATE_CLAIMED
    assert row["claimable"] is False
    assert row["reason"].startswith("held:")
    # 自家锁也要照常出现在清单里（云机据此续领本课的 BC job）
    mine = hub.offline_tasks(worker="w2")[0]
    assert mine["bc_owner"] == "w2" and mine["claimable"] is False
