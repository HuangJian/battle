"""test_worker_queue.py — P3b 共享 worker 排队（push）+ 多 hub 轮询（pull）。

plan: `plan/multi-course-parallel-training.md`（P3b-W1/W2/W5、§3.8）。

push（worker_server 有界 FIFO）：
- 在跑时提交 → 202 queued（position）；队满（WORKER_QUEUE_MAX）才 409；
- 同 jid 重发幂等（不重复 spawn）；失败不堵队；/ping 带 queued。
pull（worker 多 hub round-robin）：
- --poll 可重复/逗号分隔（同 token）；串行 run_job；work 按 hub 索引分区；
- code_cache 留共享根；单 hub 默认行为零变化。
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import sys
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.worker_server import WORKER_QUEUE_MAX, WorkerServerState, make_worker_server


def _mini_manifest(jid: str, payload: bytes) -> dict:
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
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


# ────────────────────────── push：状态机（确定性） ──────────────────────────


def test_fifo_order_and_positions(tmp_path: Path) -> None:
    """FIFO：先入先跑，position 从 1 起；kick 按序拉起。"""
    state = WorkerServerState(tmp_path / "work")
    launched: list[str] = []
    state.set_starter(lambda jid, item: launched.append(jid))
    assert state.submit("a", {})["action"] == "run"
    assert launched == ["a"]
    assert state.submit("b", {}) == {"action": "queue", "position": 1}
    assert state.submit("c", {}) == {"action": "queue", "position": 2}
    assert state.queued() == 2
    state.set_result("a", {})
    state.kick()
    assert launched == ["a", "b"]  # 队首先顶上
    state.set_result("b", {})
    state.kick()
    assert launched == ["a", "b", "c"]


def test_duplicate_submit_is_idempotent(tmp_path: Path) -> None:
    """同 jid 重发（超时重试）不重复 spawn。"""
    state = WorkerServerState(tmp_path / "work")
    launched: list[str] = []
    state.set_starter(lambda jid, item: launched.append(jid))
    state.submit("a", {})
    state.submit("b", {})
    assert state.submit("a", {})["action"] == "duplicate"
    assert state.submit("b", {})["action"] == "duplicate"
    assert launched == ["a"]  # 只起过一次


def test_failure_does_not_block_queue(tmp_path: Path) -> None:
    """失败不堵队：error 后 kick 照样拉起队首。"""
    state = WorkerServerState(tmp_path / "work")
    launched: list[str] = []
    state.set_starter(lambda jid, item: launched.append(jid))
    state.submit("a", {})
    state.submit("b", {})
    state.set_error("a", "boom")
    state.kick()
    assert launched == ["a", "b"]


def test_queue_full_returns_full(tmp_path: Path) -> None:
    """队满才 409（上界 WORKER_QUEUE_MAX）。"""
    state = WorkerServerState(tmp_path / "work")
    state.set_starter(lambda jid, item: None)
    state.submit("run", {})
    for i in range(WORKER_QUEUE_MAX):
        r = state.submit(f"q{i}", {})
        assert r["action"] == "queue", r
    assert state.submit("overflow", {})["action"] == "full"


# ────────────────────────── push：HTTP 线 ──────────────────────────


class _Srv:
    def __init__(self, state: WorkerServerState, token: str = "tok") -> None:
        self.srv = make_worker_server(state, 0, token)
        self.port = self.srv.server_address[1]
        self.token = token
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def post_job(self, manifest: dict, payload: bytes, code: bytes | None = b"fake-code"):
        body: dict = {
            "manifest": manifest,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        }
        if code is not None:
            body["code_b64"] = base64.b64encode(code).decode("ascii")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/job",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def get_ping(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ping",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def close(self):
        self.srv.shutdown()


def test_http_bad_payload_rejected(tmp_path: Path) -> None:
    state = WorkerServerState(tmp_path / "work")
    state.set_starter(lambda jid, item: None)
    srv = _Srv(state)
    try:
        m = _mini_manifest("j1", b"real-payload")
        m["payload_sha256"] = "0" * 64  # 故意对不上
        status, _ = srv.post_job(m, b"real-payload")
        assert status == 400
    finally:
        srv.close()


def test_http_queue_duplicate_and_full(tmp_path: Path) -> None:
    state = WorkerServerState(tmp_path / "work")
    launched: list[str] = []
    srv = _Srv(state)
    # make_worker_server 在构造时注入真 starter（会起真线程跑真 run_job）——
    # 覆盖为 fake，只记录拉起顺序，不执行。
    state.set_starter(lambda jid, item: launched.append(jid))
    try:
        payload = b"p"
        # 占位：在跑（无线程，确定性排队）
        state.set_state("blocker", "running")
        status, body = srv.post_job(_mini_manifest("j1", payload), payload)
        assert status == 202 and body["status"] == "queued" and body["position"] == 1
        assert srv.get_ping()["queued"] == 1
        # 重发幂等
        status, body = srv.post_job(_mini_manifest("j1", payload), payload)
        assert status == 202 and body["status"] == "queued"
        assert launched == []
        # 填满
        for i in range(2, WORKER_QUEUE_MAX + 1):
            status, body = srv.post_job(_mini_manifest(f"j{i}", payload), payload)
            assert status == 202 and body["status"] == "queued", (i, status, body)
        status, _ = srv.post_job(_mini_manifest("overflow", payload), payload)
        assert status == 409  # 满才 409
        # 放行：队首先跑（FIFO）
        state.set_result("blocker", {})
        state.kick()
        assert launched[0] == "j1"
    finally:
        srv.close()


# ────────────────────────── pull：多 hub 轮询 ──────────────────────────


def test_single_hub_unchanged_layout(tmp_path: Path) -> None:
    """单 hub：work 根直用（默认行为零变化），code_cache 走默认。"""
    import remote.worker as W

    seen: dict = {}
    job = {"job_id": "j1", "manifest": {}}
    with (
        patch.object(W, "poll_job", return_value=job),
        patch.object(W, "run_job", side_effect=lambda *a, **k: seen.update(k) or {}) as _r,
        patch.object(W, "post_result", return_value=None),
    ):
        n = W.worker_loop(
            "http://h0", "tok", work_dir=tmp_path, once=True, poll_sec=0.01, echo=True
        )
    assert n == 1
    assert seen["work_dir"] == tmp_path  # 无 hub0 子目录
    assert seen["code_cache_dir"] is None  # 默认派生


def test_multi_hub_round_robin_and_partition(tmp_path: Path) -> None:
    """多 hub：轮询顺序 round-robin；job 落 hub1 分区；code_cache 共享根。"""
    import remote.worker as W

    polls: list = []
    seen: dict = {}

    def fake_poll(url, token):
        polls.append(url)
        if len(polls) == 2:
            return {"job_id": "j1", "manifest": {}}
        return None

    with (
        patch.object(W, "poll_job", side_effect=fake_poll),
        patch.object(W, "run_job", side_effect=lambda *a, **k: seen.update(k) or {}),
        patch.object(W, "post_result", return_value=None),
    ):
        n = W.worker_loop(
            "http://h0",
            "tok",
            work_dir=tmp_path,
            hub_urls=["http://h0", "http://h1"],
            max_idle_sec=0.3,
            poll_sec=0.01,
            echo=True,
        )
    assert n == 1
    assert polls[0] == "http://h0" and polls[1] == "http://h1"  # round-robin
    assert seen["work_dir"] == tmp_path / "hub1"  # 按源分区
    assert seen["code_cache_dir"] == tmp_path / "code_cache"  # 共享根
    assert (tmp_path / "hub1").is_dir()
