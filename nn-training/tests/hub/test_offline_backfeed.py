"""tests/hub/test_offline_backfeed.py —— 撤销后的回传处置（★P1-7 / R3-f，2026-10-05）。

两个洞（plan/offline-online-status-switch §5 P1-7）：

  ① **切在线后旧会话的回传不得推进活动权重**：`pinned_online` = 人固定在线（§4.2 半回摆），
     而离线盘的旧会话还在跑；它回传的轮不是「课程现在的进度」——把 `weights.json` 拉回旧轮
     等于人切了在线、起点却倒退（报障一的另一半）。镜像/归档照落（算过什么的证据）。
  ② **段末自报要对得上当前包才盖章**：`end_it_reached` 是唯一让当前包变 completed 的判据
     （U6）；旧包/旧会话的自报若把**新包**封住，重导之后依旧不可领（U6 反着生效）。

判据全在 hub 侧（`store_offline.store_offline_artifact` / `hub/offline._end_seal_ok`）；
本文件走真 HTTP（进程内 hub），落盘事实与读面各断言一次。
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESULT_PATH,
    encode_weights_json,
)
from hub.server import _HubQueue, _JobStore, make_server

TOKEN = "sekret"
COURSE = "c5-gae"


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """权重归档根指到 tmp（理由同 `test_offline_resume_anchor`：别往真 `nn-training/weights/` 撒）。"""
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    d = tmp_path / COURSE
    (d / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    store = _JobStore(d / "remote-jobs", d / "training_log.jsonl")
    hub = _HubQueue({COURSE: store}, order=[COURSE])
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={AUTH_HEADER: f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    else:
        status = 200
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except ValueError:
        loaded = {}
    return status, loaded if isinstance(loaded, dict) else {}


def _round_body(it: int, weights: bytes, *, run_id: str) -> dict:
    """一轮补传体的最小形状（weights + 逐轮行；指纹两侧一致，先过入口校验）。"""
    fp = hashlib.sha256(weights).hexdigest()
    return {
        "course": COURSE,
        "run_id": run_id,
        "it": it,
        "weights_json": encode_weights_json(weights),
        "weights_fp": fp,
        "row": {"it": it, "weights_fp": fp},
    }


def _export_pack(tmp_path: Path, *, run_id: str, plan: bytes, monkeypatch) -> Path:
    """真导出器写一份包（判据的输入 = 索引里的 `run_id` / `parts[plan.json].sha256`）。"""
    from remote import bundle as bundle_mod
    from remote.artifacts import sha256_bytes

    monkeypatch.setattr(bundle_mod, "normalize_manifest", lambda m: m)
    src = tmp_path / "_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "init_weights.json").write_bytes(b'{"format":"nn-weights-json","params":{"w":1}}')
    (src / "code.zip").write_bytes(b"PK\x03\x04code")
    with zipfile.ZipFile(src / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/x.ts", "// ts\n")
    out = tmp_path / COURSE / f"task-{COURSE}.zip"
    bundle_mod.export_bundle(
        out,
        manifest={
            "kind": "run",
            "runId": run_id,
            "it": 1,
            "plan_sha256": sha256_bytes(plan),
            "commit": "c" * 40,
        },
        plan_bytes=plan,
        init_weights_path=src / "init_weights.json",
        code_zip_path=src / "code.zip",
        ts_code_zip_path=src / "ts_code.zip",
    )
    return out


# ───────────────── ① 撤销后的回传：不推进活动权重（镜像/归档照落） ─────────────────


def test_revoked_backfeed_does_not_advance_active_weights(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        active = b'{"format":"nn-weights-json","params":{"w":1}}'
        (tmp_path / COURSE / "weights.json").write_bytes(active)
        assert hub.set_mode_pinned(COURSE, "online", True)[0] is True  # 人切「固定在线」
        assert hub.authority_of(COURSE) == "pinned_online"

        newer = b'{"format":"nn-weights-json","params":{"w":999}}'
        st, doc = _post(
            base, OFFLINE_ARTIFACT_PATH, _round_body(7, newer, run_id="seg-old")
        )
        assert st == 200 and doc.get("status") == "accepted", doc
        # 镜像照落（「算过什么」的证据面不受归属影响）
        mirror = (
            tmp_path / COURSE / "remote-jobs" / "offline" / "seg-old" / "it-007" / "weights.json"
        )
        assert mirror.is_file()
        assert mirror.read_bytes() == newer
        # 活动权重**不动**：旧会话的回传不是「课程现在的进度」
        assert (tmp_path / COURSE / "weights.json").read_bytes() == active

        # 对照组：交还自动（pin=0）⇒ 同形状的回传照旧推进活动权重
        assert hub.set_mode_pinned(COURSE, "online", False)[0] is True
        assert hub.authority_of(COURSE) == "auto"
        st2, doc2 = _post(
            base, OFFLINE_ARTIFACT_PATH, _round_body(8, newer, run_id="seg-new")
        )
        assert st2 == 200 and doc2.get("status") == "accepted", doc2
        assert (tmp_path / COURSE / "weights.json").read_bytes() == newer
    finally:
        srv.shutdown()


# ───────────────── ② 段末自报的身份校验：旧包身份不得封住新包 ─────────────────


def test_end_it_reached_must_match_the_current_pack_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = json.dumps({"start_it": 1, "end_it": 5}).encode("utf-8")
    _export_pack(tmp_path, run_id="run-A", plan=plan, monkeypatch=monkeypatch)
    plan_sha = hashlib.sha256(plan).hexdigest()
    base, hub, srv = _boot(tmp_path)
    try:
        # 旧包 / 旧会话的身份自报 ⇒ 200 如实回显 `end_it_reached`，但**不盖章**
        st, doc = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "run-B",
                "it_end": 5,
                "state": "complete",
                "end_it_reached": True,
                "plan_sha256": "f" * 64,
            },
        )
        assert st == 200, doc
        assert doc.get("end_it_reached") is True, doc
        assert doc.get("completed_sealed") is False, doc
        row = next(r for r in hub.offline_tasks() if r["course"] == COURSE)
        assert row["state"] != "completed" and row["claimable"] is True, row
        assert row["run_id"] == "run-A", row  # ★ 索引的 snake_case `run_id` 也读得到

        # 当前包身份（run_id + plan sha 都对上）⇒ 盖章 ⇒ 不可再领（U6）
        st2, doc2 = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "run-A",
                "it_end": 5,
                "state": "complete",
                "end_it_reached": True,
                "plan_sha256": plan_sha,
            },
        )
        assert st2 == 200 and doc2.get("completed_sealed") is True, doc2
        row2 = next(r for r in hub.offline_tasks() if r["course"] == COURSE)
        assert row2["state"] == "completed" and row2["claimable"] is False, row2

    finally:
        srv.shutdown()


def test_unreadable_pack_keeps_the_old_seal_behaviour(tmp_path: Path) -> None:
    """包读不到（占位/旧形状）⇒ **不判**、照旧盖章：不制造新的失败态（兼容面）。"""
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    (tmp_path / COURSE / f"task-{COURSE}.zip").write_bytes(b"PK-test-placeholder")
    base, _hub, srv = _boot(tmp_path)
    try:
        st, doc = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "seg-any",
                "it_end": 3,
                "state": "complete",
                "end_it_reached": True,
            },
        )
        assert st == 200 and doc.get("end_it_reached") is True, doc
        assert doc.get("completed_sealed") is True, doc
    finally:
        srv.shutdown()
