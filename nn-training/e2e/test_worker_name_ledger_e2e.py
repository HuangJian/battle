"""e2e/test_worker_name_ledger_e2e.py —— 云机身份端到端落账（plan/worker-name-readable W3）。

两个（模拟的）云机 worker 各领一份 job 并把结果回传 ⇒ 课程账本里
`job_result_accepted.worker` **逐字**是 `kaggle-c` / `colab-t`。

身份注入方式（评审修订 R6）：按**生产下传路径**直接喂 `worker_id=`——worker_loop 启动时把
`<env>-<link>` 算一次，随后经 claim / `UploadTask.worker_id` / `post_result(worker_id=)`
显式下传。本用例证明「名字一旦算出来，端到端一路原样落到账本」；判据本身（平台包可导入性、
link 三态）由 `tests/common/test_env_probe.py` 与 `tests/remote/test_worker_name.py` 钉。

真 hub 服务器（真路由 + 真鉴权）+ tmp 落盘，与 `tests/hub/test_worker_attribution.py` 同规。
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from common.protocol import COURSE_ENABLE_MARKER
from hub.server import _HubQueue, _JobStore, make_server
from tests.helpers.push_worker import result_of

TOKEN = "sekret"


def _manifest(jid: str, course: str) -> dict:
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


def _publish_course(root: Path, jid: str, course: str) -> Path:
    """盘上造一门已开课 + 一份 pending job；返回该课账本路径。"""
    job_root = root / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    ledger = root / course / "training_log.jsonl"
    ledger.touch()
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _JobStore(job_root, ledger).publish(jid, _manifest(jid, course), b"PK\x03\x04fake")
    return ledger


def _read_manifest(root: Path, jid: str, course: str) -> dict:
    raw = (root / course / "remote-jobs" / jid / "manifest.json").read_text(encoding="utf-8")
    data: object = json.loads(raw)
    assert isinstance(data, dict)
    return data


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


@contextmanager
def _hub(tmp_path: Path):
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover()
    srv: ThreadingHTTPServer = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_two_worker_names_land_verbatim_in_ledger(tmp_path: Path) -> None:
    alpha, beta = ("c-alpha", "a" * 16), ("c-beta", "b" * 16)
    ledgers = {course: _publish_course(tmp_path, jid=jid, course=course) for course, jid in (alpha, beta)}
    with _hub(tmp_path) as base:
        for (course, jid), worker in ((alpha, "kaggle-c"), (beta, "colab-t")):
            claimed = W.claim_job(base, TOKEN, jid, worker_id=worker)
            assert claimed is not None and claimed["lease_token"], (course, worker)
            rc = W.post_result(
                base,
                TOKEN,
                jid,
                result_of(_read_manifest(tmp_path, jid, course)),
                lease_token=claimed["lease_token"],
                worker_id=worker,
                log=lambda _m: None,
            )
            assert rc == 200, (course, worker, rc)
    assert [e["worker"] for e in _events(ledgers["c-alpha"], "job_result_accepted")] == ["kaggle-c"]
    assert [e["worker"] for e in _events(ledgers["c-beta"], "job_result_accepted")] == ["colab-t"]
