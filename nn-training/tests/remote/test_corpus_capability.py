"""`replay_corpus` 能力协商回归（plan/replay-corpus.plan.md D2/B7，2026-11-16）。

覆盖三段：
  ① store 的唯一租约入口：replay job 只发给声明 `replay_corpus` 的 claimer；
     full job 对旧 claimer 逐字节不变；
  ② claim HTTP body：capabilities 只在显式给出时出现（旧调用形状不变）；
  ③ push 派发：`pick_push_worker(require=...)` 与 `/ping` 能力位落进登记表。
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_lifecycle as JL
from common.protocol import (
    CAP_REPLAY_CORPUS,
    COURSE_ENABLE_MARKER,
    PAYLOAD_NAME,
    pick_push_worker,
)
from hub.server import _HubQueue, _JobStore, make_server
from remote import push_dispatch

TOKEN = "sekret"
JID = "j" * 16


def _manifest(jid: str = JID, *, corpus_fmt: str = "full") -> dict:
    m = {
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
    if corpus_fmt != "full":
        m["corpus_fmt"] = corpus_fmt
    return m


def _store(tmp_path: Path, *, corpus_fmt: str = "replay", jid: str = JID) -> _JobStore:
    st = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    st.publish(jid, _manifest(jid, corpus_fmt=corpus_fmt), b"PK\x03\x04fake")
    assert (st._job_dir(jid) / PAYLOAD_NAME).exists()
    return st


def test_replay_job_rejects_claimer_without_capability(tmp_path: Path) -> None:
    st = _store(tmp_path)
    out = st.claim_outcome(JID, worker_id="old-worker")
    assert out.ok is False
    assert out.reason == "no_replay_corpus", out.reason
    assert st.claim(JID, worker_id="old-worker") is None


def test_replay_job_accepts_capable_claimer(tmp_path: Path) -> None:
    st = _store(tmp_path)
    out = st.claim_outcome(
        JID, worker_id="new-worker", capabilities=[CAP_REPLAY_CORPUS]
    )
    assert out.ok is True
    assert out.token


def test_full_job_claim_unchanged_without_capability(tmp_path: Path) -> None:
    st = _store(tmp_path, corpus_fmt="full")
    out = st.claim_outcome(JID, worker_id="old-worker")
    assert out.ok is True, "full job 不得被能力闸挡住（旧 worker 逐字节兼容）"


def test_replay_gate_uses_manifest_cache_and_invalidates_on_republish(tmp_path: Path) -> None:
    st = _store(tmp_path)
    assert st.job_corpus_fmt(JID) == "replay"
    # 重发成 full ⇒ 缓存必须失效，旧 replay 判定不得残留。
    st.publish(JID, _manifest(JID, corpus_fmt="full"), b"PK\x03\x04fake")
    assert st.job_corpus_fmt(JID) == "full"
    assert st.claim(JID, worker_id="old-worker") is not None


def test_claim_body_carries_capabilities_only_when_given(monkeypatch) -> None:
    bodies: list[dict] = []

    def _fake_request(_base, _token, _path, **kw):
        bodies.append(json.loads(kw["data"]))
        return 200, b'{"status":"ok","lease_token":"t","job_id":"j"}'

    monkeypatch.setattr(JL, "_request", _fake_request)
    JL.claim_job("http://hub", TOKEN, JID, capabilities=[CAP_REPLAY_CORPUS])
    JL.claim_job("http://hub", TOKEN, JID)
    assert bodies[0]["capabilities"] == [CAP_REPLAY_CORPUS]
    assert set(bodies[1]) == {"mode", "worker_id", "expected_epoch"}, "旧调用形状必须逐字节不变"


def test_pick_push_worker_require_capability() -> None:
    workers = [
        {"id": "old", "online": True, "busy": False, "concurrency": 1},
        {"id": "new", "online": True, "busy": False, "concurrency": 1, "replay_corpus": True},
    ]
    assert (pick_push_worker(workers, {}) or {})["id"] == "old"
    got = pick_push_worker(workers, {}, require=CAP_REPLAY_CORPUS)
    assert (got or {})["id"] == "new"


def test_ping_all_records_replay_corpus(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "rl-config.json"
    cfg.write_text(
        json.dumps({"nodes": [{"id": "g1", "url": "http://gpu", "gpu_push": True}]}),
        encoding="utf-8",
    )
    pw = push_dispatch.PushWorkers(cfg, log=lambda _m: None)
    assert pw.reload(force=True) is True

    def _fake_http(_url, _key, _path, **_kw):
        body = {"ok": True, "busy": False, "capabilities": [CAP_REPLAY_CORPUS]}
        return 200, json.dumps(body).encode("utf-8")

    monkeypatch.setattr(push_dispatch, "_http", _fake_http)
    assert pw.ping_all() == {"g1": "ok"}
    rec = next(w for w in pw.snapshot() if w["id"] == "g1")
    assert rec["replay_corpus"] is True


@contextmanager
def _hub(tmp_path: Path, *, corpus_fmt: str = "replay"):
    """真 HTTP hub（真路由 + 真鉴权）+ 一门已开课的在线 job。"""
    course = "c5-gae"
    job_root = tmp_path / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / course / "training_log.jsonl").touch()
    (tmp_path / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _JobStore(job_root, tmp_path / course / "training_log.jsonl").publish(
        JID, _manifest(JID, corpus_fmt=corpus_fmt), b"PK\x03\x04fake"
    )
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover()
    srv: ThreadingHTTPServer = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_claim_replay_requires_capability(tmp_path: Path) -> None:
    with _hub(tmp_path) as base:
        assert JL.claim_job(base, TOKEN, JID, worker_id="old") is None
        got = JL.claim_job(
            base,
            TOKEN,
            JID,
            worker_id="new",
            capabilities=[CAP_REPLAY_CORPUS],
        )
        assert got is not None and got.get("lease_token"), got


def test_http_claim_full_ignores_capability(tmp_path: Path) -> None:
    with _hub(tmp_path, corpus_fmt="full") as base:
        got = JL.claim_job(base, TOKEN, JID, worker_id="old")
        assert got is not None and got.get("lease_token"), got
