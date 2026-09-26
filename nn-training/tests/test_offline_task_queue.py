"""离线任务清单 + 领取租约 + 云机队列（2026-09-25，`plan/offline-task-discovery.plan.md`）。

用户口径（2026-09-25）：「云机不应该要在 `battle.offline.ipynb` 里配置离线课程名，它应该直接向
hub 问询，逐个下载离线任务包并完成训练任务」。

本文件钉两侧：

  * **hub 侧**（`GET /offline/tasks` + `claim`/`heartbeat`/`release`）：清单**只读**（不触发重导、
    不动账本）、候选面 = 课程表 ∪ 盘上的开课标记（评审 S-1：离线课冷掉后从表里消失，包还在盘上）、
    409 而非 403、租约**惰性过期**、同一个 `worker_id` 回来能续领（评审 G1：Kaggle 上白等 900s
    等于废掉整个会话）、`release` **不覆盖**别人的租约；
  * **云机侧**（`fetch_task_list` / `resolve_courses` / `_run_auto` / `worker_id_of` / 心跳）：
    老 hub 只探测一次就降级、`CFG.course` 非空时**一次都不问清单**、清单空 = 正常收工（rc=0）、
    `served[包 sha]` 防自激、租约的任何失败**不影响**训练。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from common.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    INIT_WEIGHTS_NAME,
    OFFLINE_CLAIM_PATH,
    OFFLINE_HEARTBEAT_PATH,
    OFFLINE_LEASE_TTL_SEC,
    OFFLINE_QUEUE_VERSION,
    OFFLINE_RELEASE_PATH,
    OFFLINE_TASKS_PATH,
    PLAN_NAME,
)
from remote import hub_server, offline_boot
from remote.hub_server import _HubQueue, make_server

TOKEN = "sekret"
INIT = b'{"format":"nn-weights-json","params":{"w":1}}'

# ------------------------------------------------------------------ 夹具


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    """发现模式的真 HTTP hub（课程表靠盘上发现，与共享 hub 同形）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _req(base: str, path: str, *, method: str = "GET", token: str | None = TOKEN) -> tuple[int, bytes]:
    headers = {} if token is None else {AUTH_HEADER: f"Bearer {token}"}
    data = b"" if method == "POST" else None
    req = urllib.request.Request(base + path, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _course(tmp_path: Path, name: str = "c5-gae") -> None:
    """造一门「hub 能发现」的课（目录 + 开课标记 + 账本）；模式由调用方在真 hub 上切。"""
    ent = tmp_path / name
    (ent / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (ent / "training_log.jsonl").touch()
    (ent / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")


def _write_pack(tmp_path: Path, course: str = "c5-gae", *, it: int = 3, end_it: int = 9) -> Path:
    """写一个**真包**（索引 + plan + init 权重）：判据的输入就是索引里那几行。"""
    sha = hashlib.sha256(INIT).hexdigest()
    idx = {
        "kind": "run",
        "runId": "run-off",
        "it": it,
        "commit": "c" * 40,
        "parts": {INIT_WEIGHTS_NAME: {"sha256": sha}},
    }
    p = tmp_path / course / f"task-{course}.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(hub_server.TASK_PACK_INDEX_NAME, json.dumps(idx))
        z.writestr(PLAN_NAME, json.dumps({"start_it": it, "end_it": end_it}))
        z.writestr(INIT_WEIGHTS_NAME, INIT.decode("utf-8"))
    return p


def _age(path: Path, secs: float = 7200.0) -> None:
    """把一个文件做旧（`discover` 的 1 小时新鲜窗之外）。"""
    old = time.time() - secs
    os.utime(path, (old, old))


def _offline_hub(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    """起 hub + 一门已切离线的课（大部分清单用例的公共前戏）。"""
    base, hub, srv = _boot(tmp_path)
    _course(tmp_path)
    hub.discover(force=True)
    assert hub.set_mode("c5-gae", "offline") is True
    return base, hub, srv


# ------------------------------------------------------------------ hub：清单


def test_tasks_lists_an_offline_course_with_its_pack(tmp_path: Path) -> None:
    """清单逐字段：state/claimable/包三件/段元信息/持有者/进度。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    pack = _write_pack(tmp_path)

    st, raw = _req(base, OFFLINE_TASKS_PATH)
    body = _json(raw)
    assert st == 200, raw[:200]
    assert body["hub_version"] == OFFLINE_QUEUE_VERSION
    assert len(body["tasks"]) == 1, body
    t = body["tasks"][0]
    assert t["course"] == "c5-gae"
    assert t["state"] == "ready" and t["claimable"] is True
    assert t["pack"]["name"] == "task-c5-gae.zip"
    assert t["pack"]["bytes"] == pack.stat().st_size
    assert t["pack"]["sha256"] == hashlib.sha256(pack.read_bytes()).hexdigest()
    assert (t["run_id"], t["it"], t["end_it"]) == ("run-off", 3, 9)
    assert t["holder"] is None and t["progress"] == {"count": 0, "last_mtime": 0.0}


def test_tasks_is_readonly_and_stable(tmp_path: Path) -> None:
    """两次调用逐字段相同，且**不碰**触发账本（清单不触发重导；plan §1.4-2）。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    before = dict(hub_server._TASK_PACK_TRIGGERS)
    first = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    second = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    assert first == second
    assert dict(hub_server._TASK_PACK_TRIGGERS) == before, "清单不得写触发账本"


def test_tasks_hides_online_courses_unless_include_all(tmp_path: Path) -> None:
    """默认只报离线课；`?include=all`（控制台排障）才给 `not_offline`，且 `claimable=false`。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path)
    hub.discover(force=True)
    assert hub.mode_of("c5-gae") == "online"

    assert _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"] == []
    rows = _json(_req(base, OFFLINE_TASKS_PATH + "?include=all")[1])["tasks"]
    assert [r["state"] for r in rows] == ["not_offline"]
    assert rows[0]["claimable"] is False


def test_tasks_candidate_face_is_disk_fact_not_the_course_table(tmp_path: Path) -> None:
    """评审 S-1：课冷掉（不在表里）、盘上有开课标记（包也旧了）⇒ 清单里仍然有它、仍可领。"""
    base, hub, _srv = _boot(tmp_path)
    ent = tmp_path / "c5-gae"
    ent.mkdir()
    (ent / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _age(_write_pack(tmp_path))  # 包旧 ⇒ 不构成「活证据」
    hub.discover(force=True)
    assert hub.courses() == [], "没有新鲜活证据 ⇒ 不在课程表（本用例的前提）"

    rows = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    assert [r["course"] for r in rows] == ["c5-gae"]
    assert rows[0]["state"] == "ready" and rows[0]["claimable"] is True


def test_tasks_reports_no_pack_stale_and_claimed(tmp_path: Path) -> None:
    """三种非 ready 状态各自的判据（`stale` 的包**照样可领**：包旧只是起点旧）。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    assert _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"][0]["state"] == "no_pack"

    _write_pack(tmp_path)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT + b"-moved-on")
    row = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"][0]
    assert row["state"] == "stale" and row["stale_reason"] and row["claimable"] is True

    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    row = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"][0]
    assert row["state"] == "claimed" and row["claimable"] is False
    assert row["holder"]["worker_id"] == "w1"


def test_tasks_requires_auth(tmp_path: Path) -> None:
    """与其余端点同一条边界：无 token 拿不到清单（清单里有课程名与落点）。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    assert _req(base, OFFLINE_TASKS_PATH, token=None)[0] in (401, 403)


def test_tasks_without_a_traj_root_is_an_empty_list(tmp_path: Path) -> None:
    """hub 不知道课程根（`--discover` 未开）⇒ 空清单，不是 500。"""
    hub = _HubQueue({})
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    st, raw = _req(base, OFFLINE_TASKS_PATH)
    assert st == 200 and _json(raw)["tasks"] == []


# ------------------------------------------------------------------ hub：租约


def _claim(base: str, course: str = "c5-gae", worker: str = "w1", **extra: str) -> tuple[int, dict]:
    qs = {"course": course, "worker": worker, **extra}
    st, raw = _req(base, OFFLINE_CLAIM_PATH + "?" + urllib.parse.urlencode(qs), method="POST")
    return st, _json(raw)


def _lease_of(base: str, worker: str = "w1") -> str:
    return str(_claim(base, worker=worker)[1]["lease"]["token"])


def test_claim_grants_a_lease_and_blocks_another_worker(tmp_path: Path) -> None:
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)

    st, doc = _claim(base, worker="w1")
    assert st == 200, doc
    lease = doc["lease"]
    assert lease["course"] == "c5-gae" and lease["worker_id"] == "w1"
    assert lease["ttl_sec"] == OFFLINE_LEASE_TTL_SEC and lease["token"]
    assert lease["expires_at"] > 0

    st2, doc2 = _claim(base, worker="w2")
    assert st2 == 409, doc2
    assert doc2["held"] is True and doc2["holder"]["worker_id"] == "w1"
    assert "takeover=1" in doc2["error"], doc2


def test_claim_by_the_same_worker_reuses_the_lease(tmp_path: Path) -> None:
    """评审 G1：cell 中断后重跑（同一台机器、同一个 `.worker-id`）不该被**自己**挡住。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    first = _lease_of(base)
    st, doc = _claim(base, worker="w1")
    assert st == 200, doc
    assert doc["lease"]["token"] != first, "续领要换 token（旧会话的 token 立刻作废）"


def test_claim_takeover_overrides_a_foreign_lease(tmp_path: Path) -> None:
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    _claim(base, worker="w1")
    st, doc = _claim(base, worker="w2", takeover="1")
    assert st == 200 and doc["lease"]["worker_id"] == "w2"


def test_claim_needs_a_worker_and_an_existing_pack(tmp_path: Path) -> None:
    """空 worker = 400（两台会互相顶租约）；没包 = 404（与 task-pack 同口径，带课程表）。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    st, doc = _claim(base, worker="")
    assert st == 400 and "worker" in doc["error"]
    st, doc = _claim(base, worker="w1")
    assert st == 404 and doc["known_courses"] == ["c5-gae"]


def test_claim_rejects_an_unsafe_course_name(tmp_path: Path) -> None:
    """课程名进的是磁盘路径 ⇒ 与 `task_pack_path` 同一条拦截（400，不是 404/500）。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    assert _claim(base, course="../secret")[0] == 400


def test_heartbeat_extends_and_reports_expired_or_taken(tmp_path: Path) -> None:
    """续租：token 对 ⇒ 延长；token 错 ⇒ 409「被接管」；过期后 ⇒ 409「已过期」。"""
    base, hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    lease = _claim(base, worker="w1")[1]["lease"]
    tok = str(lease["token"])

    st, raw = _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok}", method="POST")
    doc = _json(raw)
    assert st == 200 and doc["ttl_sec"] == OFFLINE_LEASE_TTL_SEC
    assert doc["expires_at"] >= lease["expires_at"]

    st, raw = _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease=bogus", method="POST")
    assert st == 409 and _json(raw)["expired"] is False
    # 惰性过期：时钟一推过 TTL，租约当场无主（不用清理线程）。
    hub._now = lambda: 10**10  # type: ignore[method-assign]
    st, raw = _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok}", method="POST")
    assert st == 409 and _json(raw)["expired"] is True


def test_release_does_not_steal_and_frees_for_the_owner(tmp_path: Path) -> None:
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    tok = _lease_of(base)

    st, raw = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease=other", method="POST")
    assert st == 409 and _json(raw)["holder"]["worker_id"] == "w1", "不覆盖别人的租约"
    assert _claim(base, worker="w2")[0] == 409

    st, raw = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease={tok}", method="POST")
    assert st == 200 and _json(raw)["released"] is True
    assert _claim(base, worker="w2")[0] == 200


def test_release_of_an_expired_lease_is_a_noop_success(tmp_path: Path) -> None:
    """过期/没领过 ⇒ 本来就无主：交还算成功（幂等）。"""
    base, hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    tok = _lease_of(base)
    hub._now = lambda: 10**10  # type: ignore[method-assign]
    st, _doc = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease={tok}", method="POST")
    assert st == 200


def test_admin_offline_exposes_leases(tmp_path: Path) -> None:
    """控制台要能回答「谁在跑哪门课」：`/admin/offline` 多一个 `leases` 字段。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    _claim(base, worker="w1")

    doc = _json(_req(base, "/admin/offline")[1])
    assert doc["leases"]["c5-gae"]["worker_id"] == "w1"
    assert doc["leases"]["c5-gae"]["expires_in"] > 0
    assert "progress" in doc


def test_leases_live_on_the_hub_instance(tmp_path: Path) -> None:
    """租约住实例（生产一进程一 hub 等价；单测里各 hub 自己干净，不必共用账本）。"""
    one = _HubQueue({}, discover_root=tmp_path)
    two = _HubQueue({}, discover_root=tmp_path)
    one.claim_offline("c5-gae", "w1")
    assert two.offline_lease("c5-gae") is None


# ------------------------------------------------------------------ 云机侧：清单


def test_module_constants_track_protocol() -> None:
    """端点名/协议版本/租约时长在云机侧各拄了一份（不得 import `remote.*`）——改名必须两边一起。"""
    from common import protocol

    assert offline_boot.OFFLINE_TASKS_PATH == protocol.OFFLINE_TASKS_PATH
    assert offline_boot.OFFLINE_CLAIM_PATH == protocol.OFFLINE_CLAIM_PATH
    assert offline_boot.OFFLINE_HEARTBEAT_PATH == protocol.OFFLINE_HEARTBEAT_PATH
    assert offline_boot.OFFLINE_RELEASE_PATH == protocol.OFFLINE_RELEASE_PATH
    assert offline_boot.OFFLINE_QUEUE_VERSION == protocol.OFFLINE_QUEUE_VERSION
    assert offline_boot.OFFLINE_LEASE_TTL_SEC == protocol.OFFLINE_LEASE_TTL_SEC


def _no_net(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `urlopen` 换成「一碰就炸」：用来证「这条路一次网都不碰」。"""

    def boom(req: Any, timeout: float | None = None) -> Any:
        raise AssertionError("这条路不该碰网络")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_fetch_task_list_returns_none_on_an_old_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """老 hub（404/405）⇒ 返回 `None`（调用方降级），日志点名「老 hub」。"""

    def not_found(req: Any, timeout: float | None = None) -> Any:
        raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    lines: list[str] = []
    assert offline_boot.fetch_task_list("http://hub", "tok", lines.append) is None
    assert any("老 hub" in ln for ln in lines), lines


def test_resolve_courses_uses_cfg_and_never_asks_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CFG.course` 非空 ⇒ 老行为，清单端点一次都不问（老 hub 不该被每轮刷）。"""
    _no_net(monkeypatch)
    got = offline_boot.resolve_courses({"course": ["a", "b"]}, {}, lambda _m: None)
    assert [t["course"] for t in got] == ["a", "b"]
    assert all(t["pack_sha256"] == "" for t in got)


def test_resolve_courses_filters_claimable_and_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """只领 `claimable`；本会话已跑过的包（同 sha）跳过 ⇒ 不重跑同一段（防自激）。"""
    tasks: list[dict] = [
        {"course": "a", "claimable": True, "pack": {"sha256": "aa" * 32}},
        {"course": "b", "claimable": True, "pack": {"sha256": "bb" * 32}},
        {"course": "c", "claimable": False, "pack": {"sha256": "cc" * 32}},
        {"course": "d", "claimable": True, "pack": None},  # 缺 pack 字段也照领（容忍形状不全）
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    lines: list[str] = []
    got = offline_boot.resolve_courses({}, {}, lines.append, served={"b": "bb" * 32})
    assert [t["course"] for t in got] == ["a", "d"]
    assert any("已跑过这份包" in ln for ln in lines), lines


def test_resolve_courses_probes_an_old_hub_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """老 hub：第一次就响亮失败并**记住**，之后不再每轮刷一个必然失败的端点。"""
    calls: list[int] = []

    def old_hub(hub: str, token: str, log: Any, **kw: Any) -> None:
        calls.append(1)
        return None

    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", old_hub)
    probe: dict = {}
    for _ in range(2):
        with pytest.raises(SystemExit):
            offline_boot.resolve_courses({}, {}, lambda _m: None, probe=probe)
    assert len(calls) == 1, "只探测一次"
    assert probe.get("unsupported") is True


def test_run_refuses_when_course_is_empty_and_auto_discover_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """显式关掉自动发现 = 「我就是要手填 course」⇒ 空 ⇒ 响亮拒（与今天一致）。"""
    _no_net(monkeypatch)
    with pytest.raises(SystemExit) as ei:
        offline_boot.run(
            {"auto_discover": False, "work_dir": str(tmp_path)},
            lambda _m: None,
            lambda _k, _d=None: "",
        )
    assert "auto_discover=False" in str(ei.value)


# ------------------------------------------------------------------ 云机侧：队列循环 / 心跳


def test_worker_id_persists_in_the_work_dir(tmp_path: Path) -> None:
    """评审 G1：worker id 落盘复用（重跑 cell 不会被自己留下的租约挡住）。"""
    lines: list[str] = []
    first = offline_boot.worker_id_of(tmp_path, lines.append)
    assert first and (tmp_path / offline_boot.WORKER_ID_NAME).is_file()
    assert offline_boot.worker_id_of(tmp_path, lambda _m: None) == first
    assert any("本机 worker id" in ln for ln in lines), lines


def test_run_auto_claims_runs_and_releases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """一轮的完整接线：问清单 → 领租约（带落盘的 worker id）→ 跑 → 交还 → 记 served。"""
    state = {"n": 0}

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> list[dict]:
        state["n"] += 1
        if state["n"] == 1:
            return [{"course": "c5-gae", "pack_sha256": "aa" * 32}]
        return []

    seen: dict = {}

    def fake_batch(cfg, creds, log, stop, courses, *, multi, leases, shas):
        seen.update(courses=list(courses), worker=leases["worker"], hub=leases["hub"], shas=shas)
        return 0

    monkeypatch.setattr(offline_boot, "resolve_courses", fake_resolve)
    monkeypatch.setattr(offline_boot, "_run_batch", fake_batch)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    # drain 但 idle_wait=0 ⇒ 第一轮跑完立刻到「已等满」⇒ 收工（不会真 sleep）。
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 0},
        {"HUB_TOKEN": "tok"},
        lambda _m: None,
        None,
    )
    assert rc == 0
    assert seen["courses"] == ["c5-gae"] and seen["hub"] == "http://hub"
    assert seen["worker"] and (tmp_path / offline_boot.WORKER_ID_NAME).is_file()
    assert seen["shas"] == {"c5-gae": "aa" * 32}


def test_run_auto_once_mode_returns_on_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`queue_mode="once"`：队列空 = 正常收工（rc=0），不驻守。"""
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: [])
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "queue_mode": "once"}, {}, lines.append, None
    )
    assert rc == 0 and any("once" in ln for ln in lines), lines


def test_run_auto_drain_gives_up_after_idle_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """缺省 drain：空队列驻守，等满 `idle_wait_sec` 才收工（空队列**不是**错误）。"""
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: [])
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 0}, {}, lines.append, None
    )
    assert rc == 0 and any("已等满" in ln for ln in lines), lines


def test_run_auto_stops_on_the_keepalive_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """停机信号（notebook 保活）⇒ 收工，不再空驻守。"""
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: [])
    stop = threading.Event()
    stop.set()
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 999}, {}, lines.append, stop
    )
    assert rc == 0 and any("停机信号" in ln for ln in lines), lines


def test_heartbeat_loop_beats_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """心跳线程按周期续租；`set()` 之后不再打点。"""
    hits: list[str] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        hits.append(url)
        return (200, {})

    monkeypatch.setattr(offline_boot, "_post_json", fake_post)
    done = offline_boot.heartbeat_loop(
        "http://hub", "tok", "c5-gae", "lease1", lambda _m: None, interval=0.01
    )
    for _ in range(500):
        if hits:
            break
        # sleep-ok: 轮询步长（等的是「心跳线程已打过一次」这个谓词，兜底只挡挂起）
        time.sleep(0.01)
    assert hits and OFFLINE_HEARTBEAT_PATH in hits[0] and "lease=lease1" in hits[0]
    done.set()
    n = len(hits)
    # sleep-ok: 夹具模拟的工作量：给「stop 之后不再打点」留一段窗口（期间本会再跳 5 次）
    time.sleep(0.05)
    # 允许**在途的那一拍**：`done.set()` 与心跳线程的 `done.wait` 有竞态——interval=0.01s
    # 比主线程 break→set 的间隔还短，满载时线程可能刚好越过检查、正在打这一拍（实测
    # n=1 而窗口末 len=2 ⇒ 假红，2026-09-26）。循环一次只打一拍 ⇒ 至多多一拍；窗口内
    # 本会跳 5 次，所以 ≥2 才是真的没看 `done`。
    assert len(hits) <= n + 1, "stop 之后不该继续打点（最多放行在途的一拍）"


def test_claim_and_release_never_raise_on_a_dead_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """租约的任何失败都只是日志（`""` / 无动作）：训练永不因网络停摆（plan §1.4-4）。"""

    def dead(req: Any, timeout: float | None = None) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", dead)
    lines: list[str] = []
    assert offline_boot.claim_course("http://hub", "tok", "c5-gae", "w1", lines.append) == ""
    offline_boot.release_course("http://hub", "tok", "c5-gae", "lease1", lines.append)
    assert any("连不上" in ln for ln in lines), lines


def test_claim_course_reports_a_foreign_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    """409 ⇒ 返回 `""`（照旧跑）+ 一行「已被谁持有、多久后过期」。"""
    monkeypatch.setattr(
        offline_boot,
        "_post_json",
        lambda url, token, log, *, timeout=0.0: (
            409,
            {"holder": {"worker_id": "w9", "expires_in": 120.0}, "held": True},
        ),
    )
    lines: list[str] = []
    assert offline_boot.claim_course("http://hub", "tok", "c5-gae", "w1", lines.append) == ""
    assert any("w9" in ln and "120" in ln for ln in lines), lines
