"""离线任务包端点 + 段内进度读面（2026-09-19，离线训练模式）。

用户口径：云端 notebook「先尝试连接 hub，如果能联通就从 hub 获取离线任务包，如果不能联通
则等待用户手动上传」。本文件只钉 **hub 这一侧**：

  * 包在哪：`<traj-root>/<课>/task-<课>.zip` —— 与**控制台导出的是同一个文件**
    （`dashboard/src/server/bundles/export.ts` 的 `--export-bundle tmp/<课>/task-<课>.zip`；
    hub 的 `--traj-root` 正是 `tmp`）。端点是「把同一个文件按 HTTP 递出去」，不造第二份真相。
  * 拒因各说各话：非法课程名 400 / 没这个包 404（带已知课程表） / 未鉴权 401 —— 人在云机上
    排障时，「去控制台点导出」与「课程名写错了」是两条完全不同的下一步。
  * `/admin/offline`：段内进度（已收到的轮次 + 最近时间戳）。**hub 不跑段内那些轮**，课程账本
    里没有它们的行，所以「它在跑还是挂了」只能从补传产物目录回答。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from remote.hub_server import _HubQueue, _JobStore, as_hub, make_server
from remote.protocol import (
    AUTH_HEADER,
    OFFLINE_CAP_VALUE,
    OFFLINE_TASK_PACK_PATH,
    ProtocolError,
)

TOKEN = "sekret"


# ------------------------------------------------------------------ 夹具


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    """发现模式的真 HTTP hub（课程表靠盘上发现，与共享 hub 同形）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _get(
    base: str, path: str, *, token: str | None = TOKEN
) -> tuple[int, bytes, dict]:
    """取一次响应：返回 (status, 原始体, 响应头)。**返回原始字节**——包是 zip，不能 json.loads。"""
    headers = {} if token is None else {AUTH_HEADER: f"Bearer {token}"}
    req = urllib.request.Request(base + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _as_json(raw: bytes) -> dict:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_pack(tmp_path: Path, course: str, payload: bytes = b"PK\x03\x04fake-task") -> Path:
    d = tmp_path / course
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"task-{course}.zip"
    p.write_bytes(payload)
    return p


# ------------------------------------------------------------------ 端点：任务包


def test_task_pack_serves_the_same_file_the_console_exports(tmp_path: Path) -> None:
    """200 + 逐字节相同 + 习惯文件名 —— 云机拿到的就是控制台导出的那一个包。"""
    payload = b"PK\x03\x04" + b"offline-task-bundle" * 8
    pack = _write_pack(tmp_path, "c5-gae", payload)
    base, _hub, _srv = _boot(tmp_path)

    st, raw, headers = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert st == 200, raw[:200]
    assert raw == pack.read_bytes() == payload
    assert headers.get("Content-Type") == "application/zip"
    assert "task-c5-gae.zip" in headers.get("Content-Disposition", "")


def test_task_pack_missing_is_actionable_404(tmp_path: Path) -> None:
    """没导出过 ⇒ 404 且说清下一步（并附已知课程表：课程名写错是另一种排障）。"""
    base, hub, _srv = _boot(tmp_path)
    # 有这门课（`remote-jobs/` + jsonl = 被发现的判据），但还没导出过包
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    hub.discover()
    assert hub.courses() == ["c5-gae"]

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 404, body
    assert "导出" in body["error"], body
    assert body["course"] == "c5-gae"
    assert body["known_courses"] == ["c5-gae"]


def test_task_pack_rejects_unsafe_course_names(tmp_path: Path) -> None:
    """课程名进的是磁盘路径 ⇒ 分隔符/`..`/空名必须在入口断掉（不是等落盘才发现）。"""
    base, _hub, _srv = _boot(tmp_path)
    for bad in ("../secret", "a/b", "", "..", "a\\b"):
        st, raw, _h = _get(
            base, f"{OFFLINE_TASK_PACK_PATH}?course={urllib.parse.quote(bad)}"
        )
        assert st == 400, (bad, st, raw[:200])


def test_task_pack_requires_auth(tmp_path: Path) -> None:
    """与其余端点同一条边界：无 token 拿不到包（包里有课程 + 代码）。"""
    _write_pack(tmp_path, "c5-gae")
    base, _hub, _srv = _boot(tmp_path)
    st, _raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae", token=None)
    assert st in (401, 403), st


def test_task_pack_path_derives_from_single_course_job_root(tmp_path: Path) -> None:
    """单课程模式（`--job-root <traj>/<课>/remote-jobs`）下同一个算式仍成立。"""
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    hub = as_hub(
        _JobStore(tmp_path / "c5-gae" / "remote-jobs", tmp_path / "c5-gae" / "training_log.jsonl")
    )
    assert hub.task_pack_path("c5-gae") == tmp_path / "c5-gae" / "task-c5-gae.zip"
    with pytest.raises(ProtocolError):
        hub.task_pack_path("nope/../x")


# ------------------------------------------------------------------ 读面：段内进度


def test_admin_offline_lists_rounds_landed_by_backfeed(tmp_path: Path) -> None:
    """`/admin/offline` = 段内进度的唯一读面（轮次 + 最近时间戳）。"""
    base, hub, _srv = _boot(tmp_path)
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    _write_pack(tmp_path, "c5-gae")
    assert hub.discover() == ["c5-gae"]

    # 补传产物目录（`<job_root>/offline/<run_id>/it-NNN/`），形状由 store 决定
    store = hub._stores["c5-gae"]
    run = store.job_root / _JobStore.OFFLINE_DIR / "seg-20260919"
    for it in (3, 4, 5):
        d = run / f"it-{it:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "weights.json").write_bytes(b"{}")
        (d / "row.json").write_text(json.dumps({"it": it}), encoding="utf-8")

    st, raw, _h = _get(base, "/admin/offline")
    assert st == 200, raw[:200]
    progress = _as_json(raw)["progress"]
    assert progress["c5-gae"]["seg-20260919"]["its"] == [3, 4, 5]
    assert progress["c5-gae"]["seg-20260919"]["count"] == 3
    assert progress["c5-gae"]["seg-20260919"]["last_mtime"] > 0


def test_admin_offline_is_empty_when_nothing_landed(tmp_path: Path) -> None:
    """没有离线产物 ⇒ 空表（不是错误）；有课程但没段 ⇒ 该课程不出现在表里。"""
    base, hub, _srv = _boot(tmp_path)
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    hub.discover()

    st, raw, _h = _get(base, "/admin/offline")
    assert st == 200
    assert _as_json(raw)["progress"] == {}


def test_offline_capability_header_name_is_shared_with_workers() -> None:
    """头名/值是与 worker 的跨层契约：改一边忘另一边会变成「离线课永远没人领」。

    这里钉的是**字面量**（worker 侧那条断言在 `tests/test_worker_offline_cap.py`）。
    """
    from remote.protocol import OFFLINE_CAP_HEADER

    assert OFFLINE_CAP_HEADER == "X-Battle-Offline"
    assert OFFLINE_CAP_VALUE == "1"
