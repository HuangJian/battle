"""`/jobs/{id}/status` 与**派发池**同源：账本已撤的活不许再报 `pending`（2026-10-16 事故）。

事故形态（`plan/export-must-not-cancel-live-job` §0）：导出进程触发 M2 腿启动清理，把在线腿
正在等的那份 job 在账本里写成 `job_cancelled` ⇒ 派发池（按账本净态算）里它**不可派**，
而 `/status`（按目录 + hub 内存判）一路答 `state=pending` ⇒ 等待侧永远等下去（3h+）。

一条关键前提（先前被误读为「两份事实源」）：hub 的 `_JobStore.jsonl_path` 与 trainer 的
`self._jsonl_path` 是**同一份文件**（`hub/http_face.py:55` / `hub/queue.py:95`），trainer 侧
M2 落的那条 `job_cancelled` 就是 hub 池与 `/status` 要读的那一行。本文件钉这条共读：

  ① 账本已撤 + 无活租约 ⇒ `cancelled`（今天答 `pending` 的那一档）；
  ② 有活租约 ⇒ 仍 `leased`（2026-10-03 裁决「不管在算的，只管没领的」）；
  ③ 账本里没有它 / 未撤 ⇒ `pending`（缺省值不许变，旧读方零影响）；
  ④ 两种净态实现（`worker.bc_ledger.job_net_event` 与 `hub.store_ledger.LedgerMixin.ledger_net_state`）
     对同一条序列给同一个答案——同口径只有一份，别让它漂成两份。
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

from common.protocol import AUTH_HEADER, normalize_manifest
from hub.server import _JobStore, make_server
from remote.hub_client import cancel_job
from tests.remote.test_remote_ppo import _mini_manifest  # type: ignore

TOKEN = "sekret"


def _boot(tmp_path: Path) -> tuple[str, _JobStore, ThreadingHTTPServer, threading.Thread, Path]:
    """起 hub-server（随机端口）；返回 (base, store, srv, th, ledger 路径)。"""
    ledger = tmp_path / "training_log.jsonl"
    store = _JobStore(tmp_path / "jobs", ledger)
    srv = make_server(store, 0, TOKEN, host="127.0.0.1")
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", store, srv, th, ledger


def _publish(store: _JobStore) -> str:
    manifest = normalize_manifest(_mini_manifest())
    jid = str(manifest["job_id"])
    store.publish(jid, manifest, b"PK\x03\x04fake")
    return jid


def _status(base: str, jid: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + f"/jobs/{jid}/status", headers={AUTH_HEADER: f"Bearer {TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def test_ledger_cancelled_job_reports_cancelled_not_pending(tmp_path: Path) -> None:
    """★ 主判据：账本已撤（trainer 侧 M2 会写在同一份文件里）+ 无租约 ⇒ `cancelled`。"""
    base, store, srv, th, ledger = _boot(tmp_path)
    try:
        jid = _publish(store)
        st, body = _status(base, jid)
        assert st == 200 and body["state"] == "pending", body  # 撤之前：正常排队

        # trainer 侧那条腿（M2 腿启动清理）写的就是这一行——本用例用同一个写入面
        assert cancel_job(ledger, jid, reason="上一 runId 77a9a90a… 遗留（腿启动清理）")

        st2, body2 = _status(base, jid)
        assert st2 == 200 and body2["state"] == "cancelled", body2
        # 调度面摘要照旧在场（`landed` 是 cancel-watcher 的判据，旧读方零影响）……
        assert {"landed", "ready", "computing_at"} <= set(body2), body2
        # ……但 `priority` 只在 pending/leased 档给（旧契约不变）：撤单不是「优先级问题」
        assert not ({"priority", "priority_reason"} & set(body2)), body2
        # 池与 status 现在同源：它确实不可派
        assert store.claimable_job_ids() == []
    finally:
        srv.shutdown()
        th.join()


def test_live_lease_still_wins_over_a_ledger_cancel(tmp_path: Path) -> None:
    """★ 有活租约 = 真有人在算 ⇒ 仍报 `leased`（撤销在飞不属于本刀；用户裁决 2026-10-03）。"""
    base, store, srv, th, ledger = _boot(tmp_path)
    try:
        jid = _publish(store)
        assert store.claim(jid, worker_id="w1")
        assert cancel_job(ledger, jid, reason="上一 runId x… 遗留（腿启动清理）")
        st, body = _status(base, jid)
        assert st == 200 and body["state"] == "leased", body
        assert body["lease_expires_in"] > 0.0, body
    finally:
        srv.shutdown()
        th.join()


def test_uncancelled_and_unknown_jobs_keep_reporting_pending(tmp_path: Path) -> None:
    """缺省值不许变：账本里没有它（未撤）⇒ `pending`；未发布的 jid ⇒ 404。"""
    base, store, srv, th, _ledger = _boot(tmp_path)
    try:
        jid = _publish(store)
        st, body = _status(base, jid)
        assert st == 200 and body["state"] == "pending", body
        st2, _ = _status(base, "z" * 16)
        assert st2 == 404
    finally:
        srv.shutdown()
        th.join()


def test_the_two_net_state_folders_agree(tmp_path: Path) -> None:
    """★ 同一条序列（pending → cancelled → pending）⇒ 两个实现同答。

    hub 侧那份走增量缓存（热路径），训练侧那份是共享读面；口径漂了就是第二个事实源——
    这正是本事故的成因模式（`plan/export-must-not-cancel-live-job` §4）。
    """
    from worker.bc_ledger import JOB_CANCELLED_EVENT, JOB_PENDING_EVENT, job_net_event

    ledger = tmp_path / "training_log.jsonl"
    store = _JobStore(tmp_path / "jobs", ledger)
    jid, other = "j" * 16, "k" * 16
    rows = [
        {"event": JOB_PENDING_EVENT, "job_id": jid, "it": 1, "ts": 1.0},
        {"event": JOB_PENDING_EVENT, "job_id": other, "ts": 2.0},
        {"event": "iteration", "it": 1, "ts": 3.0},  # 无关事件
        {"event": JOB_CANCELLED_EVENT, "job_id": jid, "reason": "admin cancel-job", "ts": 4.0},
        {"event": JOB_PENDING_EVENT, "job_id": jid, "it": 1, "ts": 5.0},  # 重发（复活）
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    for j in (jid, other, "z" * 16):
        mine = job_net_event(ledger, j)
        theirs = store.ledger_net_state(j)
        assert (mine["event"] if mine else "") == theirs, (j, mine, theirs)
    assert store.ledger_net_state(jid) == JOB_PENDING_EVENT  # 重发 ⇒ 净态回 pending
    assert store.ledger_net_state(other) == JOB_PENDING_EVENT
    assert store.ledger_net_state("z" * 16) == ""
