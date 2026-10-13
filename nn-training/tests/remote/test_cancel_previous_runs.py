"""M2（plan/ppo-job-lifecycle-ownership §3）：**重启 = 按 runId 整体作废**（并兑现到磁盘）。

被测 = `remote/hub_client.py::cancel_jobs_of_previous_runs` + 它复用的 `reap_job_dir` 护栏。

要治的病（§0 #4）：`cancel_stale_jobs` 只写账本行、不删目录、只在**发布新活那一刻**跑 ⇒
旧 runId 的 job 目录永久残留，而人清理的唯一工具是 `rm`——2026-10-13 事故里那把 `rm`
**误删了刚发布的新 job**（人在一堆同名残留里挑错了）。

四条判据：

1. 旧 runId 的 pending ⇒ 账本 `job_cancelled` + 目录删（兑现到磁盘，不是只写一行）；
2. 本 runId 的活**一个字不动**（RUN_ID 是进程级常量 ⇒ 同进程内停/开课不触发本清理）；
3. 护栏：`manifest.json` 缺失 / `job_id` 与目录名不符 / `runId` 与账本不符 ⇒ **拒删** + WARN
   （不校验就删是**新**的事故源——那正是要防「误删新 job」的形状）；
4. hub 不可达 ⇒ 只降级（账本照写、目录照删），**永不抛**（清不掉不能阻断开关课）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub_http as hub_http
from remote.hub_client import cancel_jobs_of_previous_runs, reap_job_dir

RUN_NOW = "r-current"


def _mk_job(job_root: Path, jid: str, *, it: int, run_id: str, job_id: str | None = None) -> Path:
    jd = job_root / jid
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "manifest.json").write_text(
        json.dumps({"job_id": job_id if job_id is not None else jid, "it": it, "runId": run_id}),
        encoding="utf-8",
    )
    (jd / "payload.tar.xz").write_bytes(b"x")
    return jd


def _ledger(jsonl: Path, rows: list[dict]) -> None:
    jsonl.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def _events(jsonl: Path) -> list[dict]:
    return [
        json.loads(x)
        for x in jsonl.read_text(encoding="utf-8").strip().splitlines()
        if x.strip()
    ]


def _pending_row(jid: str, *, it: int, run_id: str, ts: float) -> dict:
    return {"event": "job_pending", "job_id": jid, "it": it, "runId": run_id, "ts": ts}


def test_previous_run_is_cancelled_and_removed_while_current_is_untouched(tmp_path: Path) -> None:
    """★ 主判据：旧 runId 的活整体作废 + 目录删；本 runId 的活一个字不动。"""
    job_root = tmp_path / "remote-jobs"
    jsonl = tmp_path / "training_log.jsonl"
    old = _mk_job(job_root, "j" * 16, it=5, run_id="r-old")
    now = _mk_job(job_root, "k" * 16, it=6, run_id=RUN_NOW)
    _ledger(
        jsonl,
        [
            _pending_row("j" * 16, it=5, run_id="r-old", ts=1.0),
            _pending_row("k" * 16, it=6, run_id=RUN_NOW, ts=2.0),
        ],
    )
    logs: list[str] = []
    out = cancel_jobs_of_previous_runs(jsonl, job_root, RUN_NOW, log=logs.append)
    assert out == ["j" * 16]
    assert not old.exists()  # 兑现到磁盘（不是只写账本一行——那正是旧行为）
    assert now.exists()
    ev = _events(jsonl)
    assert [e["job_id"] for e in ev if e["event"] == "job_cancelled"] == ["j" * 16]
    # 本 runId 的活没有被动过：账本里它仍只有那一条 pending。
    assert [e["event"] for e in ev if e["job_id"] == "k" * 16] == ["job_pending"]
    # 响亮一行（计数判据：单据不钉措辞，见 `test_source_text_assert_budget`）。
    assert len(logs) == 1


def test_guard_refuses_to_delete_a_dir_whose_manifest_disagrees_with_the_ledger(
    tmp_path: Path,
) -> None:
    """★ 护栏（DoD#2）：账本与目录**错位**时拒删 —— 这正是「误删新 job」的形状。"""
    job_root = tmp_path / "remote-jobs"
    jsonl = tmp_path / "training_log.jsonl"
    jd = _mk_job(job_root, "j" * 16, it=5, run_id="r-manifest")  # 目录说 r-manifest
    _ledger(jsonl, [_pending_row("j" * 16, it=5, run_id="r-old", ts=1.0)])  # 账本说 r-old
    logs: list[str] = []
    out = cancel_jobs_of_previous_runs(jsonl, job_root, RUN_NOW, log=logs.append)
    assert out == ["j" * 16]  # 账本照写（作废是账本事实）
    assert jd.exists()  # 但目录**拒删**
    assert len(logs) == 2  # 护栏 WARN 一行 + 末尾汇总一行（言辞不钉，只钉「响了」）
    # 账本净态已作废（重发视角下它已不在池里）。
    assert [e["event"] for e in _events(jsonl) if e["job_id"] == "j" * 16] == [
        "job_pending",
        "job_cancelled",
    ]


def test_reap_guardrails_directly(tmp_path: Path) -> None:
    """`reap_job_dir` 的护栏逐条（M1/M2/M3 共用同一实现，不各写一份）。"""
    job_root = tmp_path / "remote-jobs"
    logs: list[str] = []
    # ① 目录不存在：不是错误面（重发视角下等价于「已清场」）。
    assert reap_job_dir(job_root, "missing", log=logs.append) is False

    # ② 缺 manifest.json ⇒ 拒删（不是我们的目录 / 半发布残留）。
    jd = job_root / "j1"
    jd.mkdir(parents=True)
    assert reap_job_dir(job_root, "j1", log=logs.append) is False
    assert jd.exists()

    # ③ job_id 与目录名不符 ⇒ 拒删。
    (jd / "manifest.json").write_text(json.dumps({"job_id": "j2"}), encoding="utf-8")
    assert reap_job_dir(job_root, "j1", log=logs.append) is False
    assert jd.exists()

    # ④ 全部对上 ⇒ 删。
    (jd / "manifest.json").write_text(
        json.dumps({"job_id": "j1", "it": 5, "runId": "r-old"}), encoding="utf-8"
    )
    assert reap_job_dir(job_root, "j1", run_id="r-old", it=5, log=logs.append) is True
    assert not jd.exists()
    assert len(logs) == 2  # ② 缺件、③ 错位各一条 WARN（成功的删除不喊）


def test_inflight_old_job_keeps_its_dir_for_the_worker_upload(
    tmp_path: Path, monkeypatch
) -> None:
    """在飞不撤（2026-10-03 裁决）：hub 回 409 ⇒ 账本已作废、目录**留给回传**。"""
    job_root = tmp_path / "remote-jobs"
    jsonl = tmp_path / "training_log.jsonl"
    jd = _mk_job(job_root, "j" * 16, it=5, run_id="r-old")
    _ledger(jsonl, [_pending_row("j" * 16, it=5, run_id="r-old", ts=1.0)])
    monkeypatch.setattr(
        hub_http, "admin_cancel_job", lambda *a, **k: hub_http.ADMIN_CANCEL_INFLIGHT
    )
    # 解冻同属 best-effort 网络面：本用例不打真网络（判据是「在飞 409 保留目录」）。
    monkeypatch.setattr(hub_http, "unfreeze_job", lambda *a, **k: False)
    logs: list[str] = []
    out = cancel_jobs_of_previous_runs(
        jsonl, job_root, RUN_NOW, hub_url="http://hub.invalid", log=logs.append
    )
    assert out == ["j" * 16]
    assert jd.exists()  # 不硬杀在算的 worker：目录留给它回传
    assert len(logs) == 2  # 「在飞」一行 + 末尾汇总一行（单指「有没有响」）
    assert any(e["event"] == "job_cancelled" for e in _events(jsonl))


def test_rows_without_run_id_are_never_touched(tmp_path: Path) -> None:
    """缺 `runId` 的历史行**不碰**（歧义绝不猜：没有 runId 就没有「上一代」这个判据）。"""
    job_root = tmp_path / "remote-jobs"
    jsonl = tmp_path / "training_log.jsonl"
    jd = _mk_job(job_root, "j" * 16, it=5, run_id="r-unknown")
    _ledger(jsonl, [{"event": "job_pending", "job_id": "j" * 16, "it": 5, "ts": 1.0}])
    assert cancel_jobs_of_previous_runs(jsonl, job_root, RUN_NOW, log=lambda _m: None) == []
    assert jd.exists()
    assert [e["event"] for e in _events(jsonl)] == ["job_pending"]
