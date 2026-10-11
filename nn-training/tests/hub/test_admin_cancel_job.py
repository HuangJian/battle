"""`POST /admin/cancel-job` —— 单 job 定向作废的人工口（2026-10-11 事故；plan/aborted-course-revival M3/W7）。

治什么：毒 job（三重校验必拒收的那类）以前没有任何人工把手——`/admin/unfreeze` 只解冻、
`release_hold` 只解接管，而 `cancel_unsettled_jobs` 是全池撤单且无入口。本文件钉：

  * 在池 job ⇒ 200 `cancelled:true` + 账本 `job_cancelled`（含可读 reason）+ 退出可领池；
  * **幂等重放** ⇒ 200 `cancelled:false`（不报错、不重复记账）；
  * 未知 job ⇒ 404；缺参 ⇒ 400；
  * **在飞不撤**（2026-10-03 用户裁决）⇒ 409 `inflight:true`（判据与全池版同源，评审 F4）；
  * 结果已落盘（池里本就看不见）⇒ 200 `cancelled:false` + why。

（全池版 `cancel_unsettled_jobs` 的行为回归在 `tests/hub/test_auto_handoff.py:1017/:1034`，
本刀只把循环体抽成 `_cancel_job_locked` 复用，那份契约原样有效。）
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    ADMIN_CANCEL_JOB_PATH,
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
)
from hub.server import _HubQueue, make_server

TOKEN = "sekret"
COURSE = "c-cancel"


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _req(base: str, path: str, *, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(
        base + path, headers={AUTH_HEADER: f"Bearer {TOKEN}"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(raw: bytes) -> dict:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _course(tmp_path: Path, hub: _HubQueue) -> None:
    (tmp_path / COURSE / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / COURSE / "training_log.jsonl").touch()
    (tmp_path / COURSE / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)


def _publish(hub: _HubQueue, jid: str) -> None:
    """往课 store 发一份未结算 job（payload 落盘即进可领池）。"""
    hub._stores[COURSE].publish(jid, {"job_id": jid, "it": 3, "role": "online"}, b"PK\x03\x04fake")


def _cancelled_ids(hub: _HubQueue) -> list[str]:
    return [
        str(e.get("job_id"))
        for e in hub._stores[COURSE]._read_ledger()
        if e.get("event") == "job_cancelled"
    ]


def test_cancel_job_endpoint_cancels_claimable_job(tmp_path: Path) -> None:
    """在池 job：200 cancelled:true + 账本落 job_cancelled（含 reason）+ 退出可领池。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub)
        _publish(hub, "poison-1")
        assert hub._stores[COURSE].claimable_job_ids() == ["poison-1"]

        st, raw = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=poison-1", method="POST")
        body = _json(raw)
        assert st == 200 and body["cancelled"] is True, raw[:200]
        assert hub._stores[COURSE].claimable_job_ids() == []
        assert _cancelled_ids(hub) == ["poison-1"]
        ev = next(
            e
            for e in hub._stores[COURSE]._read_ledger()
            if e.get("event") == "job_cancelled" and e.get("job_id") == "poison-1"
        )
        assert ev.get("reason") == "admin cancel-job"  # 可读原因（人一眼能对上动作）
    finally:
        srv.shutdown()


def test_cancel_job_endpoint_is_idempotent(tmp_path: Path) -> None:
    """幂等重放：已撤/已终局 ⇒ 200 cancelled:false + why（不报错、不重复记账）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub)
        _publish(hub, "poison-1")
        st1, _ = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=poison-1", method="POST")
        st2, raw2 = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=poison-1", method="POST")
        assert st1 == 200 and st2 == 200
        body2 = _json(raw2)
        assert body2["cancelled"] is False and body2["why"].startswith("不在可领取池"), raw2[:200]
        assert _cancelled_ids(hub) == ["poison-1"]  # 只记一条
    finally:
        srv.shutdown()


def test_cancel_job_endpoint_404_unknown_and_400_missing_param(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub)
        st, raw = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=ghost-1", method="POST")
        assert st == 404 and _json(raw).get("error", "").startswith("未知 job"), raw[:200]
        st2, raw2 = _req(base, ADMIN_CANCEL_JOB_PATH, method="POST")
        assert st2 == 400, raw2[:200]
    finally:
        srv.shutdown()


def test_cancel_job_endpoint_refuses_inflight(tmp_path: Path) -> None:
    """在飞不撤（2026-10-03 裁决）：409 cancelled:false inflight:true；账本一个字不动。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub)
        _publish(hub, "busy-1")
        store = hub._stores[COURSE]
        assert store.claim("busy-1", worker_id="w1") is not None  # 认领 ⇒ 在飞

        st, raw = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=busy-1", method="POST")
        body = _json(raw)
        assert st == 409 and body["cancelled"] is False and body["inflight"] is True, raw[:200]
        assert _cancelled_ids(hub) == []
    finally:
        srv.shutdown()


def test_cancel_job_endpoint_refuses_job_with_result(tmp_path: Path) -> None:
    """结果已落盘的 job 本就出池（`_unfinished_pending` 的 result 闸）⇒ 200 false + why。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub)
        _publish(hub, "landed-1")
        jd = hub._stores[COURSE]._job_dir("landed-1")
        (jd / "result").mkdir()  # 首写已分胜负：池里看不见它
        assert hub._stores[COURSE].claimable_job_ids() == []

        st, raw = _req(base, f"{ADMIN_CANCEL_JOB_PATH}?job_id=landed-1", method="POST")
        body = _json(raw)
        assert st == 200 and body["cancelled"] is False, raw[:200]
        assert _cancelled_ids(hub) == []  # 拒撤（它已是终局，不该再写撤单事件）
    finally:
        srv.shutdown()


def test_store_cancel_job_predicate_matches_the_batch_version(tmp_path: Path) -> None:
    """判据同源（评审 F4）：单 job 版只在「可领取池 ∧ 不在飞」时撤——与全池版同一对原语。"""
    from hub.store import _JobStore

    store = _JobStore(tmp_path / "jobs", tmp_path / "ledger.jsonl")
    store.publish("only-1", {"job_id": "only-1", "it": 3, "role": "online"}, b"PK\x03\x04fake")
    assert store.cancel_job("only-1", reason="unit")["cancelled"] is True
    # 全池版此时无可撤（同一个池判据）——两条腿不分叉。
    assert store.cancel_unsettled_jobs(reason="unit") == []
    # 未知 jid：单版本拒答明细（形状恒定）。
    info = store.cancel_job("nope-1", reason="unit")
    assert info == {
        "job_id": "nope-1",
        "cancelled": False,
        "inflight": False,
        "why": "不在可领取池（已终局/结果已落盘/无 payload/已冻结）",
    }
