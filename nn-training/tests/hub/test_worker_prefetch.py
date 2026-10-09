"""test_worker_prefetch.py — worker 预取状态上报面（plan/dashboard-ppo-live-rows，2026-10-09）。

控制台首页要回答「这几台机器现在正在算哪一轮、下一轮下好了没」，而「下一轮」只有 worker
自己知道（`work_dir/prefetch/` 的软持有）。本文件钉住把它抬到 hub 观测面的那一段：

  ① **纯观测**：写入面只碰一张 TTL 表（`_worker_prefetch`），不碰租约 / 游标 / 派发判据
     ——`peek_jobs` 走一趟之后那张表仍是空的（「不认领、无副作用」的契约不许被这条上报污染）；
  ② **jid → (course, it) 由 hub 解析**（`course_of` + `_manifest_summary`）：权威 manifest 在
     hub，worker 那份 `meta.json.summary` 只是 peek 时的快照 ⇒ 一处解析、一处口径；
  ③ **读数纪律**：超 TTL 剔除（worker 死后不留幽灵「已下载」）· 全空报告不出现 · 解析不出的
     jid **保留**条目但不编轮次（`course=""` / `it=None`）；
  ④ inflight 行补 `course` / `it`（首页「计算中」那一行要它；`computing_ago` 仍是分档判据）；
  ⑤ 端点形状：缺 `worker` ⇒ 400、体越界 ⇒ 413、正常 ⇒ 200 且**真的落进读面**。
"""

from __future__ import annotations

import json
import sys
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import WORKER_PREFETCH_TTL_SEC
from hub.admin import WORKER_PREFETCH_BODY_MAX, AdminRoutes
from hub.queue import _HubQueue

JID = "j" * 16
OTHER = "k" * 16


class _Clock:
    """假钟（hub 与 store 同源：`_HubQueue(now_fn=clock)` 会把时钟推给自动发现的 store）。"""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t


def _hub_with_course(tmp_path: Path, clock: _Clock, course: str = "c1") -> _HubQueue:
    root = tmp_path / "traj"
    (root / course / "remote-jobs").mkdir(parents=True)
    (root / course / "training-enabled.txt").write_text("", encoding="utf-8")
    (root / course / "training_log.jsonl").touch()
    hub = _HubQueue({}, order=[], discover_root=root, now_fn=clock)
    assert hub.discover() == [course]
    return hub


def _publish(hub: _HubQueue, course: str, jid: str, it: int) -> None:
    hub._stores[course].publish(
        jid, {"payload_sha256": "0" * 64, "runId": "r1", "it": it}, b"PK\x03\x04fake"
    )


# ────────────────────────── ① 读数形状与解析 ──────────────────────────


def test_readout_resolves_course_and_it_from_the_hub_manifest(tmp_path: Path) -> None:
    """worker 只报 jid；`course` 与 `it` 由 hub 解析（权威 manifest 只有 hub 有）。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)
    _publish(hub, "c1", OTHER, it=8)

    hub.note_worker_prefetch("kaggle-c", [JID], [OTHER])

    row = hub.queue_state()["worker_prefetch"]["kaggle-c"]
    assert row["age"] == 0.0
    assert row["held"] == [{"job_id": JID, "course": "c1", "it": 7}]
    assert row["dl"] == [{"job_id": OTHER, "course": "c1", "it": 8}]
    # 写入面与读面同源（同一次 TTL 过滤）：直接调读面也得到同一份
    assert hub.worker_prefetch_readout()["kaggle-c"] == row


def test_ttl_drops_dead_workers(tmp_path: Path) -> None:
    """worker 死后不留下幽灵「已下载」：超窗整体剔除（与 `_offline_disks` 同规）。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)
    hub.note_worker_prefetch("kaggle-c", [JID], [])

    clock.t += WORKER_PREFETCH_TTL_SEC
    assert hub.worker_prefetch_readout(), "刚好在窗口内不该被剔除"
    clock.t += 1.0
    assert hub.worker_prefetch_readout() == {}, "超窗必须剔除"


def test_reports_need_identity_and_empty_reports_clear_the_row(tmp_path: Path) -> None:
    """无身份 ⇒ 不记（编占位名只会让面板多一坨假机器）；全空报告 ⇒ 读面不出现（但能清空旧行）。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)

    hub.note_worker_prefetch("", [JID], [])
    assert hub.worker_prefetch_readout() == {}

    hub.note_worker_prefetch("kaggle-c", [JID], [])
    assert hub.worker_prefetch_readout()["kaggle-c"]["held"] == [
        {"job_id": JID, "course": "c1", "it": 7}
    ]

    # 「上一拍有、这一拍没有」是一次真实的状态变化：必须能把读面清回空
    hub.note_worker_prefetch("kaggle-c", [], [])
    assert hub.worker_prefetch_readout() == {}


def test_unknown_jid_is_kept_without_inventing_an_iteration(tmp_path: Path) -> None:
    """解析不出（未知 / 归属歧义）⇒ 保留条目、`course=""`、`it=None`——不编 0、不编 1。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    hub.note_worker_prefetch("colab-t", ["deadbeefdeadbeef"], [])

    row = hub.worker_prefetch_readout()["colab-t"]
    assert row["held"] == [{"job_id": "deadbeefdeadbeef", "course": "", "it": None}]


def test_inflight_rows_carry_course_and_it(tmp_path: Path) -> None:
    """inflight 行补 `course` / `it`：首页那一行要用它；`computing_ago` 仍是「开算没有」的判据。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=121)
    assert hub.claim(JID, worker_id="kaggle-c")

    row = hub.queue_state()["courses"]["c1"]["inflight"][0]
    assert row["job_id"] == JID
    assert row["course"] == "c1"
    assert row["it"] == 121
    # 认领 ≠ 开算：读面据这两个字段分「下载中 / 计算中」，本用例只钉事实面
    assert row["claimed_ago"] == 0.0 and row["computing_ago"] is None


def test_peek_does_not_touch_the_prefetch_table(tmp_path: Path) -> None:
    """★ 契约回归：`peek` 仍无副作用——走它一趟不许在这张表上留任何痕迹。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)

    assert [p["job_id"] for p in hub.peek_jobs(worker_id="w1")] == [JID]
    assert hub.queue_state()["worker_prefetch"] == {}


# ────────────────────────── ② 端点形状 ──────────────────────────


class _Stub(AdminRoutes):
    """最小宿主：真的 `_HubQueue` + 假请求（与 `test_hub_admin_split.py::_Stub` 同规）。"""

    def __init__(self, hub: _HubQueue, body: bytes = b"", content_length: str | None = None):
        self.hub = hub
        self.path = "/admin/worker-prefetch"
        self.headers = Message()
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        elif body:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = BytesIO(body)
        self.sent: list[tuple[Any, int]] = []

    def _auth_ok(self) -> bool:
        return True

    def _json(self, obj: object, status: int = 200) -> None:
        self.sent.append((obj, status))


def test_endpoint_requires_worker_identity(tmp_path: Path) -> None:
    """缺 `worker` ⇒ 400（无处归属就别记），且**不落**任何行。"""
    hub = _hub_with_course(tmp_path, _Clock())
    st = _Stub(hub, json.dumps({"held": [JID], "dl": []}).encode("utf-8"))
    st._post_worker_prefetch()
    assert st.sent[-1][1] == 400 and "worker" in st.sent[-1][0]["error"]
    assert hub.worker_prefetch_readout() == {}


def test_endpoint_accepts_a_report_and_lands_it_in_the_readout(tmp_path: Path) -> None:
    """正常上报 ⇒ 200，并且**真的落进** `/admin/queue` 的读面（端到端接线）。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)
    st = _Stub(
        hub,
        json.dumps({"worker": "kaggle-c", "held": [JID], "dl": [], "ignored": 1}).encode("utf-8"),
    )
    st._post_worker_prefetch()
    assert st.sent == [({"ok": True, "worker": "kaggle-c"}, 200)]
    assert hub.queue_state()["worker_prefetch"]["kaggle-c"]["held"][0]["it"] == 7


def test_endpoint_rejects_oversized_body(tmp_path: Path) -> None:
    """体越界 ⇒ 413（不把任意大的体读进内存）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    st = _Stub(hub, content_length=str(WORKER_PREFETCH_BODY_MAX + 1))
    st._post_worker_prefetch()
    assert st.sent[-1][1] == 413 and "越界" in st.sent[-1][0]["error"]


def test_endpoint_rejects_bad_json_and_non_object_bodies(tmp_path: Path) -> None:
    """坏 JSON / 非对象体 ⇒ 400（响亮，不静默当成空报告）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    st = _Stub(hub, b"{not json")
    st._post_worker_prefetch()
    assert st.sent[-1][1] == 400 and "bad json" in st.sent[-1][0]["error"]

    st = _Stub(hub, json.dumps([1, 2]).encode("utf-8"))
    st._post_worker_prefetch()
    assert st.sent[-1][1] == 400 and "对象" in st.sent[-1][0]["error"]


def test_report_is_observation_only(tmp_path: Path) -> None:
    """★ 上报**不进任何派发判据**：报完之后可领取池 / 游标 / 租约一字不变。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _publish(hub, "c1", JID, it=7)
    before: dict[str, Any] = {
        "claimable": hub.claimable_job_ids("c1"),
        "cursor": hub.queue_state()["cursor"],
        "active": hub.active_worker_count(),
    }
    hub.note_worker_prefetch("kaggle-c", [JID], [JID])
    assert hub.claimable_job_ids("c1") == before["claimable"] == [JID]
    assert hub.queue_state()["cursor"] == before["cursor"]
    assert hub.active_worker_count() == before["active"]
