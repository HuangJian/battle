"""tests/hub/test_offline_bc_course.py — BC 课程在 `/offline/tasks` 里是**一等公民**。

2026-11-12 用户口径：BC 课程与 RL 课程在自主云机执行过程中同等对待（不再只是空档兜底）。
hub 侧要保证：

  * 每行带 `kind`（`bc` / `rl`）；BC 行没有 task pack，但照样可领；
  * BC 课程**不**发课程租约（`claim_offline` ⇒ `bc_course`）——它的互斥是
    `bc-exclusive.json`（课程级独占锁），走 `/jobs/{id}/claim`；
  * 账本收官（`run_complete` 晚于最后一次 `run_start`）⇒ `completed`、不可领；
  * 别的盘持着 BC 独占锁 ⇒ 行里可见 `bc_owner` / `bc_job`，`claimable=false`。

★ 2026-11-13（用户报障「自主云机领不到 BC 课程」）：上面这条「BC 课不发课程租约」在
HTTP 面必须**先于缺包腿**生效，且 kind 判据不能依赖 `--traj-root` 的相邻目录（控制台起的
hub 是 `--traj-root <repo>/tmp`，curricula 在 `<repo>/nn-training/curricula`）。本文件下半
部分钉住这两个回归。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import hub.queue_offline as queue_offline
from common.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    OFFLINE_CLAIM_PATH,
)
from hub.server import _HubQueue, make_server
from hub.task_pack import TASK_STATE_CLAIMED, TASK_STATE_COMPLETED

TOKEN = "sekret"


def _mk_course(root: Path, course: str) -> Path:
    """造一门被 hub 发现的课（课程目录 + 账本 + 开课标记）。"""
    d = root / course
    (d / "remote-jobs").mkdir(parents=True)
    (d / "training_log.jsonl").touch()
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    return d


def _boot_bc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, course: str = "bc-demo"
) -> tuple[_HubQueue, Path]:
    """造一门 BC 课：课程目录在 `<root>/<课>`，课程文件在 `CURRICULA_DIR/<课>.bc.jsonc`。"""
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / f"{course}.bc.jsonc").write_text("{}", encoding="utf-8")
    # hub 的 kind 判据读模块常量（与 `hub.store_offline.CURRICULA_DIR` 同源）——
    # 用例把它指到临时目录，不依赖机器上真实课程表。
    monkeypatch.setattr(queue_offline, "CURRICULA_DIR", curricula, raising=False)
    root = tmp_path / "tmp"
    root.mkdir()
    d = _mk_course(root, course)
    hub = _HubQueue({}, discover_root=root)
    hub.discover(force=True)
    return hub, d


def _req(base: str, path: str, *, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(
        base + path, headers={AUTH_HEADER: f"Bearer {TOKEN}"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_bc_course_is_a_claimable_kind_bc_row_without_a_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, _d = _boot_bc(tmp_path, monkeypatch)
    row = hub.offline_tasks()[0]
    assert row["course"] == "bc-demo"
    assert row["kind"] == "bc"
    assert row["pack"] is None
    assert row["state"] == "ready"
    assert row["claimable"] is True
    assert row["bc_owner"] == "" and row["bc_job"] == ""
    assert row["hold"] == {} and row["pending_export"] == {}


def test_bc_course_refuses_a_course_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC 走课程级独占锁，不领课程 hold（否则 hold 闸会挡住自己的 BC job）。"""
    hub, _d = _boot_bc(tmp_path, monkeypatch)
    assert hub.claim_offline("bc-demo", "w1") == ({}, "bc_course")
    assert hub.hold_of("bc-demo") == {}


def test_bc_course_completed_from_the_ledger_is_not_claimable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, d = _boot_bc(tmp_path, monkeypatch)
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


def test_bc_exclusive_owner_is_visible_and_blocks_claimable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, d = _boot_bc(tmp_path, monkeypatch)
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


def test_bc_course_is_detected_when_curricula_is_not_next_to_the_traj_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-11-13 回归：控制台布局（`--traj-root <repo>/tmp`，课程文件在
    `<repo>/nn-training/curricula`）。

    旧实现取 `<discover_root>/../curricula`，控制台起的 hub 那里是 `<repo>/curricula`
    （不存在）⇒ **每门 BC 课都被判成 rl** ⇒ claim 走「缺包 ⇒ pending_export」腿，
    BC 作业永远领不到（现场：g2b-classic35）。
    """
    curricula = tmp_path / "nn-training" / "curricula"
    curricula.mkdir(parents=True)
    (curricula / "bc-demo.bc.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(queue_offline, "CURRICULA_DIR", curricula, raising=False)
    root = tmp_path / "tmp"
    root.mkdir()
    _mk_course(root, "bc-demo")
    hub = _HubQueue({}, discover_root=root)
    hub.discover(force=True)
    row = hub.offline_tasks()[0]
    assert row["kind"] == "bc"
    assert row["claimable"] is True
    assert row["pending_export"] == {}


def test_claim_endpoint_refuses_a_bc_course_without_writing_pending_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC 课没有任务包 ⇒ claim 必须走 `bc_course`，不能走「缺包 ⇒ 请导包」腿。

    旧顺序（缺包腿在 `claim_offline` 的 BC 门之前）会让 BC 课被写一笔
    `pending_export` 并请控制台导一份永远不会存在的包 —— 云机此后每拍都读回
    `pending_export`，永远领不到（用户现场日志的形状）。
    """
    hub, _d = _boot_bc(tmp_path, monkeypatch)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, raw = _req(
            base,
            f"{OFFLINE_CLAIM_PATH}?proto=2&course=bc-demo&worker=w1",
            method="POST",
        )
    finally:
        srv.shutdown()
        srv.server_close()
    doc = json.loads(raw.decode("utf-8"))
    assert code == 409 and doc.get("bc_course") is True, doc
    assert hub.pending_export_of("bc-demo") == {}


def test_begin_pending_export_refuses_a_bc_course(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pending_export` 是 RL 的软态：BC 课在任何入口都不该写出它（写入口的唯一守卫）。"""
    hub, _d = _boot_bc(tmp_path, monkeypatch)
    verdict, _why = hub.begin_pending_export("bc-demo", "w1")
    assert verdict == "bc_course"
    assert hub.pending_export_of("bc-demo") == {}


def test_legacy_pending_export_on_a_bc_course_does_not_raise_a_stall_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """历史误判可能在盘上留下 BC 课的 `pending_export` 记录 —— 读面不得据此报停滞。"""
    clock = {"t": 1_000_000.0}
    curricula = tmp_path / "curricula"
    curricula.mkdir()
    (curricula / "bc-demo.bc.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(queue_offline, "CURRICULA_DIR", curricula, raising=False)
    root = tmp_path / "tmp"
    root.mkdir()
    _mk_course(root, "bc-demo")
    hub = _HubQueue({}, discover_root=root, now_fn=lambda: clock["t"])
    hub.discover(force=True)
    hub.note_pending_export("bc-demo", by="w9")
    clock["t"] += queue_offline.auto_handoff_pending_sec() + 60.0
    assert hub.offline_stalled() == []
