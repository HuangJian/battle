"""pill 精确化的 hub 观测面（2026-10-02，plan/course-pill-precision §4.1）。

纯观测加法，三条钉子：

① `/admin/queue` 的 inflight 行补 `claimed_ago` / `computing_ago`（缺失 → `null`，**不编 0**：
   「刚认领」与「没有这条记录」是两件事）；
② 顶层补 `peeked_courses`：最近 `PEEKED_WINDOW_SEC` 内被 `peek_jobs` **返回过候选**的课程集，
   窗口外自动消失；旧路径 `claim_next` **不**记（它不是预取）；
③ 这些字段只被观测面读、不进任何派发判据——由 `queue_state` 的调用图与评审保证，
   本文件钉住形状与窗口语义。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.queue import _HubQueue
from hub.queue_observe import PEEKED_WINDOW_SEC


class _Clock:
    """假钟（hub 与 store 同源：`_HubQueue(now_fn=clock)` 会把时钟推给自动发现的 store）。"""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


def _hub_with_course(tmp_path: Path, clock: _Clock, course: str = "c1") -> tuple[_HubQueue, str]:
    root = tmp_path / "traj"
    (root / course / "remote-jobs").mkdir(parents=True)
    (root / course / "training-enabled.txt").write_text("", encoding="utf-8")
    (root / course / "training_log.jsonl").touch()
    hub = _HubQueue({}, order=[], discover_root=root, now_fn=clock)
    assert hub.discover() == [course]
    return hub, "a" * 16


def _publish(hub: _HubQueue, course: str, jid: str) -> None:
    hub._stores[course].publish(jid, {"payload_sha256": "0" * 64}, b"PK\x03\x04fake")


def test_inflight_rows_expose_claim_and_compute_ages(tmp_path: Path) -> None:
    """认领 30s、开算 5s ⇒ 两个龄各说各的；没 `POST /start` ⇒ `computing_ago=None`。"""
    clock = _Clock()
    hub, jid = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", jid)
    assert hub.claim(jid, worker_id="w1"), "认领没成功（夹具问题）"

    clock.t += 30.0
    row = hub.queue_state()["courses"]["c1"]["inflight"][0]
    assert row["job_id"] == jid and row["worker"] == "w1"
    assert row["claimed_ago"] == 30.0
    assert row["computing_ago"] is None  # 还没开算：不编 0（0 会把卡死读成刚刚开始）

    assert hub.start_job(jid, "w1")
    clock.t += 5.0
    row = hub.queue_state()["courses"]["c1"]["inflight"][0]
    assert row["claimed_ago"] == 35.0
    assert row["computing_ago"] == 5.0


def test_peeked_courses_is_a_rolling_window(tmp_path: Path) -> None:
    """只在 `peek_jobs` 返回候选时记；窗口外自动消失（= 轮转扫不到它）。"""
    clock = _Clock()
    hub, jid = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", jid)

    assert hub.queue_state()["peeked_courses"] == []  # 还没人预取
    assert [p["job_id"] for p in hub.peek_jobs(worker_id="w1")] == [jid]
    assert hub.queue_state()["peeked_courses"] == ["c1"]

    clock.t += PEEKED_WINDOW_SEC + 1.0
    assert hub.queue_state()["peeked_courses"] == []


def test_claim_next_does_not_count_as_prefetch(tmp_path: Path) -> None:
    """旧路径 `claim_next` 不是预取——不写 `_peeked`，否则「排队·无人取」永远点不亮。"""
    clock = _Clock()
    hub, jid = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", jid)
    got = hub.claim_next(worker_id="w1")
    assert got is not None and got[1] == jid
    assert hub.queue_state()["peeked_courses"] == []
