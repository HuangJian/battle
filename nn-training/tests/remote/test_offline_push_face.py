"""tests/remote/test_offline_push_face.py — worker 侧 HTTP 面（W4）的契约判据。

这里跑的是**真 HTTP**（`ThreadingHTTPServer` + urllib）：端点是跨端契约，用函数直调测不到
「谁该回 202 / 409 / 404」「Bearer 有没有生效」「body 是不是逐字节」这些只有线上才成立的判据。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.offline_push_face as face
from common.protocol import OFFLINE_PUSH_PROTO

TOKEN = "t-0k"


# ────────────────────────── 夹具 ──────────────────────────


def _pack_bytes(course: str = "c5-gae") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.json", json.dumps({"run_id": "r1", "it": 0, "end_it": 4}))
        z.writestr("code.zip", b"PK-not-really")
    return buf.getvalue()


def _task_zip(
    tmp_path: Path,
    *,
    task_id: str = "0123456789abcdef",
    course: str = "c5-gae",
    pack_name: str | None = None,
    pack_sha: str | None = None,
    extra: dict[str, bytes] | None = None,
    proto: str = OFFLINE_PUSH_PROTO,
) -> Path:
    name = pack_name or f"task-{course}.zip"
    pack = _pack_bytes(course)
    task = {
        "proto": proto,
        "task_id": task_id,
        "course": course,
        "run_id": "r1",
        "pack_name": name,
        "pack_sha256": pack_sha or hashlib.sha256(pack).hexdigest(),
        "it": 0,
        "end_it": 4,
        "resume_it": 0,
    }
    out = tmp_path / f"task-{task_id}.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("task.json", json.dumps(task))
        z.writestr(f"pack/{name}", pack)
        z.writestr("resume/it-000/weights.json", b'{"w":0}')
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return out


class _Server:
    def __init__(self, tmp_path: Path, *, starter=None, **state_kw) -> None:
        self.state = face.PushFaceState(
            work_dir=tmp_path / "face", log=lambda _m: None, **state_kw
        )
        self.state.set_starter(starter or (lambda _t, _d, _o: None))
        self.srv = face.make_face_server(self.state, 0, TOKEN, host="127.0.0.1")
        self.port = int(self.srv.server_address[1])
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def req(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        token: str = TOKEN,
        headers: dict | None = None,
    ) -> tuple[int, bytes]:
        h = {}
        if token:
            h["Authorization"] = f"Bearer {token}"
        h.update(headers or {})
        req = urllib.request.Request(self.url(path), data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return int(r.status), r.read()
        except urllib.error.HTTPError as e:
            return int(e.code), e.read()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def srv(tmp_path: Path):
    s = _Server(tmp_path)
    try:
        yield s
    finally:
        s.close()


def _json(raw: bytes) -> dict:
    doc = json.loads(raw.decode("utf-8"))
    assert isinstance(doc, dict)
    return doc


def _submit(s: _Server, zip_path: Path) -> tuple[int, bytes]:
    return s.req(
        "/offline/push/task",
        method="POST",
        data=zip_path.read_bytes(),
        headers={"Content-Type": "application/zip"},
    )


# ────────────────────────── 鉴权 / 探活 / 版本门 ──────────────────────────


def test_every_endpoint_requires_the_bearer_token(srv: _Server) -> None:
    status, _ = srv.req("/offline/push/state", token="")
    assert status == 401
    status, _ = srv.req("/ping", token="wrong")
    assert status == 401


def test_ping_and_state_carry_the_proto_version_gate(srv: _Server) -> None:
    """评审 P1-7：GPU 的 `/ping` 没有 `proto` —— 版本门只能落在 `/state`（这里两处都给）。"""
    st, raw = srv.req("/ping")
    assert st == 200
    doc = _json(raw)
    assert doc["proto"] == OFFLINE_PUSH_PROTO and doc["offline_push"] is True
    assert doc["busy"] is False

    st, raw = srv.req("/offline/push/state")
    assert st == 200
    doc = _json(raw)
    assert doc["proto"] == OFFLINE_PUSH_PROTO
    assert doc["phase"] == "idle"
    assert doc["busy"] is False


# ────────────────────────── 收任务 ──────────────────────────


def test_task_zip_is_accepted_safely_and_duplicates_are_idempotent(
    tmp_path: Path, srv: _Server
) -> None:
    z = _task_zip(tmp_path)
    st, raw = _submit(srv, z)
    assert st == 202, raw
    doc = _json(raw)
    assert doc["status"] == "accepted" and doc["task_id"] == "0123456789abcdef"
    tdir = srv.state.task_dir
    assert tdir is not None and (tdir / "pack" / "task-c5-gae.zip").is_file()
    assert (tdir / "resume" / "it-000" / "weights.json").is_file()

    # 同一单重投（hub 超时重试）：202 duplicate，且**不**重跑、不动目录
    st, raw = _submit(srv, _task_zip(tmp_path))
    assert st == 202 and _json(raw)["status"] == "duplicate"


def test_a_second_task_while_busy_is_409(tmp_path: Path, srv: _Server) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    other = _task_zip(tmp_path, task_id="fedcba9876543210", course="c6-gae")
    st, raw = _submit(srv, other)
    assert st == 409, raw
    assert _json(raw)["error"] == "busy"


def test_zip_slip_member_is_rejected_and_nothing_is_written(tmp_path: Path, srv: _Server) -> None:
    z = _task_zip(tmp_path, extra={"../evil.txt": b"pwn"})
    st, raw = _submit(srv, z)
    assert st == 400, raw
    assert _json(raw)["error"].startswith("任务包成员名非法")
    assert not (tmp_path / "face" / "tasks").exists() or not any(
        (tmp_path / "face" / "tasks").iterdir()
    )
    assert not (tmp_path / "evil.txt").exists()


def test_pack_sha_mismatch_is_rejected(tmp_path: Path, srv: _Server) -> None:
    z = _task_zip(tmp_path, pack_sha="0" * 64)
    st, raw = _submit(srv, z)
    assert st == 400 and _json(raw)["error"].startswith("任务包 sha256 不符")


def test_pack_name_course_mismatch_is_rejected(tmp_path: Path, srv: _Server) -> None:
    z = _task_zip(tmp_path, pack_name="task-c6-gae.zip", course="c5-gae")
    st, raw = _submit(srv, z)
    assert st == 400 and _json(raw)["error"].endswith("不一致")


def test_wrong_proto_is_rejected_with_a_pointed_message(tmp_path: Path, srv: _Server) -> None:
    z = _task_zip(tmp_path, proto="offline_push_v0")
    st, raw = _submit(srv, z)
    assert st == 400 and _json(raw)["error"].startswith("任务包协议不符")


def test_oversize_body_is_413(tmp_path: Path, srv: _Server, monkeypatch) -> None:
    monkeypatch.setattr(face, "OFFLINE_PUSH_TASK_BODY_MAX", 100)
    st, raw = _submit(srv, _task_zip(tmp_path))
    assert st == 413, raw


def test_outbox_capacity_gate_stops_accepting_new_tasks(tmp_path: Path) -> None:
    """F6：历史单的 outbox 已到上限 ⇒ 新单 413（**不删旧的**，当前任务照跑）。"""
    s = _Server(tmp_path, outbox_cap_bytes=1)
    try:
        old = tmp_path / "face" / "tasks" / "old" / "outbox" / "rounds"
        old.mkdir(parents=True)
        (old / "it0001.json").write_bytes(b"x" * 32)  # 一个“没人来拉”的历史单
        st, raw = _submit(s, _task_zip(tmp_path))
        assert st == 413, raw
        assert _json(raw)["error"].startswith("outbox 已用")
        assert s.state.task is None and s.state.outbox is None, "拒收后不许留下半接的状态"
    finally:
        s.close()


# ────────────────────────── 读面：round / result / progress ──────────────────────────


def _write_outbox(state: face.PushFaceState, *, it: int = 1, rev: int = 1) -> str:
    tdir = state.task_dir
    assert tdir is not None
    out = tdir / "outbox"
    (out / "rounds").mkdir(parents=True, exist_ok=True)
    (out / "metrics").mkdir(parents=True, exist_ok=True)
    body = json.dumps({"run_id": "r1", "it": it, "weights_b64": "x"}, separators=(",", ":")).encode()
    (out / "rounds" / f"it{it:04d}.json").write_bytes(body)
    (out / "metrics" / f"it{it:04d}.json").write_text("{}", encoding="utf-8")
    (out / "state.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "rounds": {
                    str(it): {
                        "rev": rev,
                        "file": f"rounds/it{it:04d}.json",
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "acked_rev": 0,
                        "oversize": False,
                    }
                },
                "final_ready": True,
            }
        ),
        encoding="utf-8",
    )
    (out / "final.json").write_text(json.dumps({"run_id": "r1", "it_end": it}), encoding="utf-8")
    (out / "progress.json").write_text(
        json.dumps({"seq": 7, "at": 1.0, "kind": "round-done", "it": it, "done": 1, "total": 2}),
        encoding="utf-8",
    )
    return body.decode()


def test_state_rounds_progress_and_result_read_back_the_outbox(
    tmp_path: Path, srv: _Server
) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    body = _write_outbox(srv.state)

    st, raw = srv.req("/offline/push/state")
    doc = _json(raw)
    assert st == 200 and doc["final_ready"] is True
    assert doc["rounds"]["1"]["rev"] == 1

    st, raw = srv.req("/offline/push/round?it=1")
    assert st == 200 and raw.decode() == body

    st, raw = srv.req("/offline/push/round?it=9")
    assert st == 404

    st, raw = srv.req("/offline/push/result")
    assert st == 200 and _json(raw)["it_end"] == 1

    st, raw = srv.req("/offline/push/progress?since=6")
    assert st == 200 and _json(raw)["seq"] == 7 and _json(raw)["unchanged"] is False

    st, raw = srv.req("/offline/push/progress?since=7")
    assert st == 200 and _json(raw)["unchanged"] is True


def test_result_is_202_until_the_final_body_lands(tmp_path: Path, srv: _Server) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    st, _raw = srv.req("/offline/push/result")
    assert st == 202


# ────────────────────────── ack / cancel ──────────────────────────


def test_ack_with_the_seen_rev_removes_body_and_final(tmp_path: Path, srv: _Server) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    _write_outbox(srv.state)
    srv.state.note_child_exit(0)  # 真流程：final 落盘 ⇔ 子进程已收尾（run_loop 返回后才写 final）
    out = srv.state.outbox
    assert out is not None

    # 落后的 rev ⇒ 不删（hub 看到的是旧版，下次必须按新 rev 重拉）
    body = json.dumps(
        {"task_id": "0123456789abcdef", "result": False, "upto_it": 1, "revs": {"1": 0}}
    ).encode()
    st, raw = srv.req("/offline/push/ack", method="POST", data=body)
    assert st == 200 and _json(raw)["kept"] == [1]
    assert (out / "rounds" / "it0001.json").exists()

    body = json.dumps(
        {"task_id": "0123456789abcdef", "result": True, "upto_it": 1, "revs": {"1": 1}}
    ).encode()
    st, raw = srv.req("/offline/push/ack", method="POST", data=body)
    doc = _json(raw)
    assert st == 200 and doc["removed"] == [1] and doc["final_removed"] is True
    assert not (out / "rounds" / "it0001.json").exists()
    assert not (out / "final.json").exists()

    st, raw = srv.req("/ping")
    assert _json(raw)["busy"] is False  # final 已 ack ⇒ hub 可以派下一单


def test_ack_for_another_task_is_409(tmp_path: Path, srv: _Server) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    body = json.dumps({"task_id": "ffffffffffffffff", "result": True, "upto_it": 0}).encode()
    st, raw = srv.req("/offline/push/ack", method="POST", data=body)
    assert st == 409 and _json(raw)["error"].startswith("task_id 不符")


def test_cancel_writes_the_stop_file_and_reports_the_phase(
    tmp_path: Path, srv: _Server
) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    st, raw = srv.req("/offline/push/cancel", method="POST", data=b"")
    assert st == 202 and _json(raw)["status"] == "cancelling"
    tdir = srv.state.task_dir
    assert tdir is not None and (tdir / "stop").exists()

    srv.state.note_child_exit(0)
    assert srv.state.phase == "cancelled"


def test_child_exit_marks_done_or_failed(tmp_path: Path, srv: _Server) -> None:
    assert _submit(srv, _task_zip(tmp_path))[0] == 202
    srv.state.note_child_exit(3)
    assert srv.state.phase == "failed" and srv.state.rc == 3


# ────────────────────────── 启动清扫 ──────────────────────────


def test_stale_incoming_dirs_are_swept(tmp_path: Path) -> None:
    work = tmp_path / "face"
    stale = work / "incoming" / "in-old"
    stale.mkdir(parents=True)
    os.utime(stale, (1, 1))  # 上次进程被杀留下的（mtime 远早于清扫窗）
    state = face.PushFaceState(work_dir=work, log=lambda _m: None, clock=lambda: 10**9)
    assert state.task is None
    assert not stale.exists()


def test_course_parser_matches_the_export_convention() -> None:
    """面里的 `task-<课>.zip` 解析与 `offline_boot` 同一条约定（两处必须同改）。"""
    import remote.offline_boot as boot

    for name in ("task-c5-gae.zip", "task-Y31_null.zip", "not-a-pack.zip"):
        assert face._course_from_pack_name(name) == boot.course_from_pack_name(name)
