"""test_worker_attribution.py — 承接归属账（plan/worker-contribution-view W2，2026-10-02）。

面板「云端 PPO worker 贡献度」的全部数据源都在这里钉住：

  * 结果 POST 必须带 `X-Worker-Id`（`post_result`；与 claim 同一身份）——
    否则 hub 的 409 白算与承接归属都只能记匿名；
  * 承接成功 ⇒ 课程账本一条 `job_result_accepted`（worker 定格在**承接那一刻**；
    训练侧 `job_completed` 是后来另写的，两行按 job_id join）；
  * 409 白算 ⇒ `job_rejected`（**两条路径**：早退「结果已存在」/ 首写锁定失败）；
    push 腿承接的持有人身份带 `push:` 前缀（与 claim/租约同字）。

不碰 `store_leases.mark_completed`（生产零调用者——它不是 `job_completed` 的写手）。
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_lifecycle as JL
import remote.worker as W
from common.protocol import (
    CLAIM_MODE_EXCLUSIVE,
    CLAIM_TTL_SEC,
    COURSE_ENABLE_MARKER,
    ROLE_ONLINE,
    WORKER_ID_HEADER,
    push_worker_id_of,
)
from hub.server import _HubQueue, _JobStore, make_server
from remote.push_dispatch import accept_result
from tests.helpers.push_worker import result_of

TOKEN = "sekret"
JID = "j" * 16
COURSE = "c5-gae"


def _manifest(jid: str = JID, course: str = COURSE) -> dict:
    return {
        "proto": 1,
        "runId": "test-run",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }


def _publish_course(root: Path, jid: str = JID, course: str = COURSE) -> Path:
    """盘上造一门已开课 + 一份 pending job；返回该课账本路径。"""
    job_root = root / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    ledger = root / course / "training_log.jsonl"
    ledger.touch()
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _JobStore(job_root, ledger).publish(jid, _manifest(jid, course), b"PK\x03\x04fake")
    return ledger


def _read_manifest(root: Path, jid: str = JID, course: str = COURSE) -> dict:
    raw = (root / course / "remote-jobs" / jid / "manifest.json").read_text(encoding="utf-8")
    data: object = json.loads(raw)
    assert isinstance(data, dict)
    return data


@contextmanager
def _hub(tmp_path: Path):
    """进程内真 HTTP hub（真路由 + 真鉴权）——与 test_priority_schedule 同规。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover()
    srv: ThreadingHTTPServer = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", hub
    finally:
        srv.shutdown()
        srv.server_close()


def _events(ledger: Path, event: str) -> list[dict]:
    if not ledger.exists():
        return []
    out: list[dict] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("event") == event:
            out.append(e)
    return out


def test_result_accepted_records_worker_at_settle_time(tmp_path: Path) -> None:
    """承接成功 ⇒ `job_result_accepted` 带**那一刻**的身份（与 claim 同源）。"""
    ledger = _publish_course(tmp_path)
    with _hub(tmp_path) as (base, _hubq):
        wid = JL.worker_tag()
        claimed = W.claim_job(base, TOKEN, JID, worker_id=wid)
        assert claimed is not None and claimed["lease_token"]
        rc = W.post_result(
            base,
            TOKEN,
            JID,
            result_of(_read_manifest(tmp_path)),
            lease_token=claimed["lease_token"],
            log=lambda _m: None,
        )
        assert rc == 200
    acc = _events(ledger, "job_result_accepted")
    assert len(acc) == 1, acc
    assert acc[0]["job_id"] == JID
    assert acc[0]["worker"] == wid, "归属必须定格在承接那一刻的身份"


def test_duplicate_result_is_recorded_as_rejected_with_worker(tmp_path: Path) -> None:
    """晚到者的 409（早退路径，不经过 `accept_result`）⇒ `job_rejected` 带身份。

    这是「白算」的唯一数据源：赢家已落账时，晚到者算的 GPU 时间在 hub 侧必须留痕。
    """
    ledger = _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (base, _hubq):
        wid = JL.worker_tag()
        claimed = W.claim_job(base, TOKEN, JID, worker_id=wid)
        assert claimed is not None and claimed["lease_token"]
        assert (
            W.post_result(
                base,
                TOKEN,
                JID,
                result_of(manifest),
                lease_token=claimed["lease_token"],
                log=lambda _m: None,
            )
            == 200
        )
        # 晚到者：同 job 的第二份结果（像竞速输家，无租约 token）——结果已存在 ⇒ 早退 409
        assert W.post_result(base, TOKEN, JID, result_of(manifest), log=lambda _m: None) == 409
    rej = _events(ledger, "job_rejected")
    assert len(rej) == 1, rej
    assert rej[0]["worker"] == wid
    assert "already stored" in rej[0]["reason"]


def test_store_lock_loss_records_rejected_with_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首写锁定失败（`accept_result` 的 409，含 push 腿输家）⇒ 同样落 `job_rejected`。"""
    ledger = _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (_base, hub):
        monkeypatch.setattr(hub, "store_result", lambda _jid, _result: False)
        code, why = accept_result(hub, JID, result_of(manifest), "", worker="cloudB")
        assert code == 409 and "already stored" in why
    rej = _events(ledger, "job_rejected")
    assert [e["worker"] for e in rej] == ["cloudB"], rej


def test_push_leg_accept_records_push_holder(tmp_path: Path) -> None:
    """push 腿承接 ⇒ worker 与认领时租约同字（`push:<id>`）。"""
    ledger = _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (_base, hub):
        holder = push_worker_id_of("gpu1")
        lease = hub.claim(
            JID,
            ttl=CLAIM_TTL_SEC,
            worker_id=holder,
            mode=CLAIM_MODE_EXCLUSIVE,
            role=ROLE_ONLINE,
        )
        assert lease
        code, _why = accept_result(hub, JID, result_of(manifest), lease, worker=holder)
        assert code == 200
    acc = _events(ledger, "job_result_accepted")
    assert [e["worker"] for e in acc] == ["push:gpu1"], acc


def test_missing_worker_identity_falls_back_to_lease_holder(tmp_path: Path) -> None:
    """旧 worker（结果 POST 不带身份）⇒ 不得 500；身份回退到当前租约持有人。

    2026-10-03 全量门禁回归：`_append_attribution` 曾直接调 `hub.lease_worker`，而
    `lease_worker` 住在每课 `_JobStore`（`_HubQueue` 没有）⇒ 旧 worker 的结果面 500。
    """
    ledger = _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (_base, hub):
        lease = hub.claim(
            JID,
            ttl=CLAIM_TTL_SEC,
            worker_id="cloudOld",
            mode=CLAIM_MODE_EXCLUSIVE,
            role=ROLE_ONLINE,
        )
        assert lease
        code, _why = accept_result(hub, JID, result_of(manifest), lease, worker="")
        assert code == 200
    acc = _events(ledger, "job_result_accepted")
    assert [e["worker"] for e in acc] == ["cloudOld"], acc


def test_accepted_result_releases_lease_at_once(tmp_path: Path) -> None:
    """★ 结果落地 ⇒ 租约**当场**收（2026-10-11 用户报障）。

    缺陷：`accept_result` 收下结果只写 `result/`，**不 release**；租约要等
    `CLAIM_TTL_SEC=300s` 自然过期才回收。那 5 分钟里 `/admin/queue` 仍把这 job 报成
    inflight 且 `computing_ago` 非空 ⇒ 读面（`buildWorkerLive`）把它推进「计算中」段
    ⇒ 首页把一台盘的多份「已算完的旧活」全标成「计算中」（用户看到的
    `一台 ppo worker 同时在算多轮` 就是这个形状）。

    钉住三件事：① 承接成功 ⇒ 该 job 不再是租约持有人（`lease_worker` 空）；
    ② 不再出现在 `inflight`（观测面判据）；③ 结果仍在盘上（release 不伤产物）。
    """
    ledger = _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (_base, hub):
        holder = push_worker_id_of("gpu1")
        lease = hub.claim(
            JID,
            ttl=CLAIM_TTL_SEC,
            worker_id=holder,
            mode=CLAIM_MODE_EXCLUSIVE,
            role=ROLE_ONLINE,
        )
        assert lease
        # 观测面（= 首页正读的那一份）：承接前它在这门课的 inflight 里，且已开算。
        rows = hub.queue_state()["courses"][COURSE]["inflight"]
        assert [r["job_id"] for r in rows] == [JID], rows
        code, _why = accept_result(hub, JID, result_of(manifest), lease, worker=holder)
        assert code == 200
        rows = hub.queue_state()["courses"][COURSE]["inflight"]
        assert rows == [], "结果落地后必须当场从 inflight 摘掉（不是等 300s 过期）"
        assert (tmp_path / COURSE / "remote-jobs" / JID / "result" / "result.json").exists()
    assert len(_events(ledger, "job_result_accepted")) == 1


def test_result_without_lease_token_is_safe_noop(tmp_path: Path) -> None:
    """备份副本（`CLAIM_MODE_BACKUP`，lease_token 为空）回传的收租约**必须无副作用**。

    备份本就没占租约（`_claim_locked` 的 backup 分支返回空 token）⇒ `release(jid, "")`
    是 no-op（`release` 对空 token 直接 False）。钉住：这条路径不许因新增的 release 抛错。
    """
    _publish_course(tmp_path)
    manifest = _read_manifest(tmp_path)
    with _hub(tmp_path) as (_base, hub):
        code, _why = accept_result(hub, JID, result_of(manifest), "", worker="cloudB")
        assert code == 200


def test_post_result_sends_worker_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """`post_result` 的请求头必须带身份——没有它，输家的 409 归不到人。"""
    seen: dict[str, str] = {}

    def fake_request(_base_url: str, _token: str, _path: str, **kw: object):
        headers = kw.get("headers") or {}
        if isinstance(headers, dict):
            seen.update(headers)
        return 200, b"{}"

    monkeypatch.setattr(JL, "_request", fake_request)
    monkeypatch.setattr(JL, "_wire_add", lambda *a, **k: None)
    rc = JL.post_result("http://hub", "tok", JID, {"a": 1}, log=lambda _m: None)
    assert rc == 200
    assert seen.get(WORKER_ID_HEADER) == JL.worker_tag()
