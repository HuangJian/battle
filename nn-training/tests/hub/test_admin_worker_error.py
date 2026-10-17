"""test_admin_worker_error.py — 云机错误上报面（plan/component-error-registry §4.3-S3b，2026-10-16）。

云机的两类失败**只在云机**（pull 的机器级停滞/瞬时失败、离线段的失败），控制台的错误登记表
看不到它们。本文件钉住把「只在云机」抬到 hub 中继面的那一段：

  ① **纯观测**：写入面只进一条 TTL ring（`_worker_errors`），不碰租约 / 游标 / 派发判据；
  ② **游标口径 = hub 的到达时钟**（`at`），不是上报方的事件时钟（`ts`）——跨机器时钟不可比，
     拿 `ts` 当游标会让「钟快的机器」永远被过滤掉。`cursor` 由 hub 给（控制台原样回传）；
  ③ **读数纪律**：TTL 剔除 · 条数上限 · `severity` 只认两档 · `reason`/`detail` 长度封顶；
  ④ **端点形状**：缺 `worker` 或 `reason` ⇒ 400、体越界 ⇒ 413、正常 ⇒ 200 且**真的落进读面**。

夹具与 `tests/hub/test_worker_prefetch.py` **同源**（那条腿就是本条腿的形状来源：`_Clock` 假钟 +
`_hub_with_course` 自动发现 + `_Stub` 最小宿主）——直接复用那三个，不再抄第二份。
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

from common.protocol import WORKER_ERROR_TTL_SEC
from hub.admin import WORKER_ERROR_BODY_MAX, AdminRoutes
from hub.queue import _HubQueue
from hub.queue_observe import WORKER_ERROR_RING_MAX
from tests.hub.test_worker_prefetch import _Clock, _hub_with_course  # type: ignore

REPORT = {
    "worker": "kaggle-c:1",
    "component": "ppo-worker",
    "course": "c1",
    "job_id": "j" * 16,
    "it": 7,
    "severity": "warn",
    "reason": "UnreapableChildError: 子进程收不了尸",
    "detail": "Traceback (most recent call last):\n  ...\nUnreapableChildError",
    "ts": 1_700_000_000.0,
}


class _Stub(AdminRoutes):
    """最小宿主：真的 `_HubQueue` + 假请求（与 `test_worker_prefetch.py::_Stub` 同规）。"""

    def __init__(
        self,
        hub: _HubQueue,
        body: bytes = b"",
        content_length: str | None = None,
        path: str = "/admin/worker-error",
    ):
        self.hub = hub
        self.path = path
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


def _post(hub: _HubQueue, payload: dict, **kw: Any) -> _Stub:
    st = _Stub(hub, json.dumps(payload).encode("utf-8"), **kw)
    st._post_worker_error()
    return st


def _get(hub: _HubQueue, since: float = 0.0) -> dict:
    st = _Stub(hub, path=f"/admin/errors?since={since}")
    st._admin_errors()
    assert st.sent[-1][1] == 200
    body: dict = st.sent[-1][0]
    return body


# ────────────────────────── ① 端点形状 ──────────────────────────


def test_endpoint_requires_worker_and_reason(tmp_path: Path) -> None:
    """缺 `worker` 或缺 `reason` ⇒ 400，且**不落**任何行（无归属/无原因的行没法渲染）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    st = _post(hub, {**REPORT, "worker": ""})
    assert st.sent[-1][1] == 400
    # 400 的正文必须点名**缺哪个字段**（排障者不必去读源码）——用前缀而不是子串：
    # 判据是「这行字以那儿开头」，不是「里面恰好有这三个字母」
    assert st.sent[-1][0]["error"].startswith("需要 worker=")
    st = _post(hub, {**REPORT, "reason": "   "})
    assert st.sent[-1][1] == 400
    assert st.sent[-1][0]["error"].startswith("需要 worker=")
    assert hub.worker_errors(0.0)["errors"] == []


def test_endpoint_rejects_oversized_body_and_bad_json(tmp_path: Path) -> None:
    """体越界 ⇒ 413（不读进内存）；坏 JSON / 非对象 ⇒ 400（响亮，不当空报告）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    st = _Stub(hub, content_length=str(WORKER_ERROR_BODY_MAX + 1))
    st._post_worker_error()
    assert st.sent[-1][1] == 413
    assert st.sent[-1][0]["error"].startswith("请求体越界")

    st = _Stub(hub, b"{not json")
    st._post_worker_error()
    assert st.sent[-1][1] == 400
    assert st.sent[-1][0]["error"].startswith("bad json")

    st = _Stub(hub, json.dumps([1]).encode("utf-8"))
    st._post_worker_error()
    assert st.sent[-1][1] == 400
    assert st.sent[-1][0]["error"] == "体必须是对象"


def test_endpoint_accepts_a_report_and_lands_it_in_the_readout(tmp_path: Path) -> None:
    """正常上报 ⇒ 200，并且**真的落进**读面（端到端接线），字段逐项归一。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    st = _post(hub, {**REPORT, "ignored": 1})
    assert st.sent == [({"ok": True, "worker": "kaggle-c:1"}, 200)]

    read = hub.worker_errors(0.0)
    assert len(read["errors"]) == 1
    rec = read["errors"][0]
    assert (rec["worker"], rec["component"], rec["course"], rec["job_id"]) == (
        "kaggle-c:1",
        "ppo-worker",
        "c1",
        "j" * 16,
    )
    assert (rec["it"], rec["severity"]) == (7, "warn")
    assert rec["reason"] == REPORT["reason"] and rec["detail"] == REPORT["detail"]
    # `at` 是 hub 的到达时钟（游标用它），`ts` 是上报方的事件时钟（只用于展示）
    assert rec["at"] == clock.t and rec["ts"] == REPORT["ts"]


def test_severity_only_two_levels_and_lengths_capped(tmp_path: Path) -> None:
    """`severity` 只认 `err`/`warn`（其余归 warn）；`reason`/`detail` 长度封顶（不把体撑爆）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    _post(hub, {**REPORT, "severity": "critical", "reason": "x" * 5000, "detail": "y" * 9000})
    rec = hub.worker_errors(0.0)["errors"][0]
    assert rec["severity"] == "warn"
    assert len(rec["reason"]) == 2000 and len(rec["detail"]) == 4000

    hub2 = _hub_with_course(tmp_path / "b", _Clock())
    _post(hub2, {**REPORT, "severity": "err"})
    assert hub2.worker_errors(0.0)["errors"][0]["severity"] == "err"


def test_mixin_guard_needs_identity_even_when_called_directly(tmp_path: Path) -> None:
    """直调 `note_worker_error` 也要过同一个门（HTTP 层不是唯一入口）。"""
    hub = _hub_with_course(tmp_path, _Clock())
    assert hub.note_worker_error({"worker": "", "reason": "boom"}) == {}
    assert hub.note_worker_error({"worker": "w1", "reason": "  "}) == {}
    assert hub.worker_errors(0.0)["errors"] == []


# ────────────────────────── ② 游标（增量读法） ──────────────────────────


def test_since_cursor_is_incremental_and_hub_clocked(tmp_path: Path) -> None:
    """★ `?since=` 是**增量**：给过的游标原样回传 ⇒ 没有新条目；新上报 ⇒ 只有它回来。

    游标口径是 hub 的到达时钟（`at`），所以「上报方钟快/钟慢」都不影响增量性。
    """
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    _post(hub, {**REPORT, "ts": 1e12})  # 事件钟离谱地"未来"——不得污染游标
    first = _get(hub, 0.0)
    assert len(first["errors"]) == 1
    assert first["cursor"] == clock.t

    again = _get(hub, first["cursor"])
    assert again["errors"] == [] and again["cursor"] == first["cursor"]

    clock.t += 5.0
    _post(hub, {**REPORT, "reason": "RetryableError: 传输损坏"})
    third = _get(hub, first["cursor"])
    assert [r["reason"] for r in third["errors"]] == ["RetryableError: 传输损坏"]
    assert third["cursor"] == clock.t

    # 旧条目仍可重读（游标是「从哪之后」，不是「已消费」）
    assert len(_get(hub, 0.0)["errors"]) == 2


def test_readout_is_bounded_by_ttl_and_ring(tmp_path: Path) -> None:
    """TTL 剔除（超窗不再服务）+ 条数上限（失败风暴不吃光内存）。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    for i in range(3):
        clock.t += 1.0
        hub.note_worker_error({**REPORT, "reason": f"boom-{i}"})

    # 边界用**年龄**算清楚：三条的 at 分别是 t0 / t0+1 / t0+2
    clock.t += WORKER_ERROR_TTL_SEC - 3.0  # 最老那条正好 120s ⇒ 仍在窗内（`<=`）
    assert [r["reason"] for r in hub.worker_errors(0.0)["errors"]] == ["boom-0", "boom-1", "boom-2"]
    clock.t += 2.0  # 最老那条 121s（超窗）⇒ 只剩它后面的两条
    assert [r["reason"] for r in hub.worker_errors(0.0)["errors"]] == ["boom-1", "boom-2"]
    clock.t += 3.0  # 剩下两条也超窗
    assert hub.worker_errors(0.0)["errors"] == [], "超窗必须剔除"

    # 条数上限：塞满上限 + 5，读面只留最后 WORKER_ERROR_RING_MAX 条（最老的 5 条被挤掉）
    hub2 = _hub_with_course(tmp_path / "ring", _Clock())
    for i in range(WORKER_ERROR_RING_MAX + 5):
        hub2.note_worker_error({**REPORT, "reason": f"r{i}"})
    rows = hub2.worker_errors(0.0)["errors"]
    assert len(rows) == WORKER_ERROR_RING_MAX
    assert rows[0]["reason"] == "r5" and rows[-1]["reason"] == f"r{WORKER_ERROR_RING_MAX + 4}"


def test_report_does_not_touch_dispatch_state(tmp_path: Path) -> None:
    """★ 契约回归：上报**不进任何派发判据**——可领取池 / 游标 / 在飞一字不变。"""
    clock = _Clock()
    hub = _hub_with_course(tmp_path, clock)
    hub._stores["c1"].publish(
        "j" * 16, {"payload_sha256": "0" * 64, "runId": "r1", "it": 7}, b"PK\x03\x04fake"
    )
    before = (hub.claimable_job_ids("c1"), hub.queue_state()["cursor"], hub.active_worker_count())
    _post(hub, REPORT)
    assert (hub.claimable_job_ids("c1"), hub.queue_state()["cursor"], hub.active_worker_count()) == (
        before
    )
