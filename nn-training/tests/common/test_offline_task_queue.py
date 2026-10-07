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
    `served[包 sha]` 防自激、租约的任何失败**不影响**训练；`resolve_courses` 两层选择
    （★ 2026-10-03 用户裁决）：离线可领整批优先，没有就抢 `seize` 行里 `open_time` 最小的一门。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
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
from hub import server as hub_server
from hub.server import _HubQueue, make_server
from remote import offline_boot

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


def test_tasks_marks_in_training_online_course_as_seize(tmp_path: Path) -> None:
    """★ 默认清单的在线在训课 + ★六轮 §4.2 半回摆（pin online 重新获得阻止力）：

    · 在训的 **auto** 在线课会出现（允许无包；claim 即触发导包 —— 自动交接的唯一入口）
      且带 `seize=True`（离线盘可抢）与 `open_time`（抢的顺序键）；
    · **pin online ⇒ `pinned_online`**：行**照发**（小项 5：云机空队列自解释不该少这一档），
      但 `claimable=false`、`seize=false`、`reason` 以 `pinned:` 开头；点「交还自动」后恢复可抢。
    · 停课（删开课标记）= 唯一 opt-out：默认面不列，`?include=all` 给 `not_offline`。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path)
    hub.discover(force=True)
    assert hub.mode_of("c5-gae") == "online"

    rows = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    assert [r["course"] for r in rows] == ["c5-gae"]
    assert rows[0]["authority"] == "auto"
    assert rows[0]["auto_handoff"] is True and rows[0]["claimable"] is True
    assert rows[0]["seize"] is True and rows[0]["open_time"] < 1e18

    # pin online：不再可抢，但**不藏**——行照发、只是 claimable/seize 都是 false（六轮 F6）。
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    rows2 = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    assert [r["course"] for r in rows2] == ["c5-gae"]
    assert rows2[0]["authority"] == "pinned_online"
    assert rows2[0]["seize"] is False and rows2[0]["claimable"] is False
    assert str(rows2[0]["reason"]).startswith("pinned:")

    # 停课：唯一 opt-out（默认面消失；排障面 not_offline）
    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)
    assert _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"] == []
    rows_all = _json(_req(base, OFFLINE_TASKS_PATH + "?include=all")[1])["tasks"]
    assert [r["state"] for r in rows_all] == ["not_offline"]
    assert rows_all[0]["claimable"] is False and rows_all[0]["seize"] is False


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

    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
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
    # proto=2：★M1b 起 claim 必须带它（缺 ⇒ 409 busy+error+proto_required，旧端拒收）；
    # 要测「旧端」就传 proto=""（`_claim(base, proto="")`）。
    qs = {"course": course, "worker": worker, "proto": "2", **extra}
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
    assert "不可被顶" in doc2["error"], doc2  # ★M1b：takeover 不再是出口（不变量 3）


def test_claim_by_the_same_worker_reuses_the_lease(tmp_path: Path) -> None:
    """评审 G1：cell 中断后重跑（同一台机器、同一个 `.worker-id`）不该被**自己**挡住。"""
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    first = _lease_of(base)
    st, doc = _claim(base, worker="w1")
    assert st == 200, doc
    assert doc["lease"]["token"] != first, "续领要换 token（旧会话的 token 立刻作废）"


def test_claim_takeover_cannot_override_a_live_hold(tmp_path: Path) -> None:
    """★M1b 语义反转（旧名 `..._overrides_a_foreign_lease`）：**live 的接管不可被顶**。

    旧行为：别的盘一个 `?takeover=1` 就能顶掉活着的租约（= 同一份活两处跑，数据损坏级）。
    新契约（plan §1.2-③ 不变量 3）：要清只有两条路——① 它自己**进度**静默超阈（新主自动
    接管，无需任何参数）② 人到控制台点「强制解除接管」（hub 侧 = `revoke_offline_lease`）。
    """
    base, _hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    _claim(base, worker="w1")
    st, doc = _claim(base, worker="w2", takeover="1")
    assert st == 409 and doc["held"] is True, doc
    assert doc["holder"]["worker_id"] == "w1"
    assert "不可被顶" in doc["error"], doc


def test_claim_needs_a_worker_and_an_existing_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 worker = 400（两台会互相顶租约）；缺包分两种（2026-10-03，plan/auto-offline-handoff）：

    · **自动候选**（在训课 —— pin 不再拦，用户 2026-10-03 裁决）⇒ 409 + 翻 mode + 触发导包
      （P0-1：旧的 404 会让「自动课普遍无包」变成死锁，整条自动交接链永不启动）；
    · **停课残留**（开课标记已删）⇒ ★六轮 F3（行为变更）：**409 `not_offline`**（旧写法是 404
      ——生产 `stopCourse` 推 `mode=offline`，旧 claim 门会放行 ⇒ 停课 + 有包今天能被领走）。
    """
    from hub import offline as offline_mod

    monkeypatch.setattr(
        offline_mod, "trigger_auto_handoff", lambda course, log=None: (True, "ok")
    )
    base, hub, _srv = _offline_hub(tmp_path)
    st, doc = _claim(base, worker="")
    assert st == 400 and "worker" in doc["error"]
    st, doc = _claim(base, worker="w1")
    assert st == 409 and doc["auto_handoff"] is True and doc["pending_export"] is True
    assert hub.mode_of("c5-gae") == "offline"

    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)  # 停课 ⇒ 退出自动候选
    st, doc = _claim(base, worker="w1")
    assert st == 409 and doc["not_offline"] is True, doc


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
    got, blocked, _manifest = offline_boot.resolve_courses({"course": ["a", "b"]}, {}, lambda _m: None)
    assert [t["course"] for t in got] == ["a", "b"]
    assert all(t["pack_sha256"] == "" for t in got)
    assert blocked == []


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
    got, _blocked, _manifest = offline_boot.resolve_courses({}, {}, lines.append, served={"b": "bb" * 32})
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


def test_resolve_courses_skips_given_up_courses(monkeypatch: pytest.MonkeyPatch) -> None:
    """本会话放弃的课（hub 触发导包到上界）不再每轮领一次。"""
    tasks = [
        {"course": "a", "claimable": True, "pack": {"sha256": "aa" * 32}},
        {"course": "b", "claimable": True, "pack": {"sha256": "bb" * 32}},
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    got, _blocked, _manifest = offline_boot.resolve_courses({}, {}, lambda _m: None, skip={"a"})
    assert [t["course"] for t in got] == ["b"]


def test_resolve_courses_renews_my_own_stale_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 2026-10-04 用户追问「colab 已停机、也切过模式，为什么还持有租约？」的回归锚。

    租约只在 **显式 release / 900s TTL 到期 / hub 重启**时消失——切模式与停课都不动它。
    而 hub 的 `lease_verdict` 早就分出一档 `mine`（同一 worker_id 重领直接续上，评审 G1：
    「Kaggle 十几分钟的会话预算，白等 900s 等于整个会话废掉」）——但**清单行的 `claimable`
    把任何持有者（包括自己）一律排除**，客户端从不去试 ⇒ 自己把自己锁到 TTL。
    这里钉住：没有可领的课、而某行是**我自己**的租约 ⇒ 照领（claim 会续上）。
    """
    tasks = [
        {
            "course": "mine",
            "claimable": False,
            "seize": False,
            "state": "claimed",
            "reason": "held: w-me",
            "holder": {"worker_id": "w-me", "expires_in": 812.0},
            "pack": {"sha256": "aa" * 32},
        },
        {
            "course": "other",
            "claimable": False,
            "seize": False,
            "state": "claimed",
            "reason": "held: w-other",
            "holder": {"worker_id": "w-other", "expires_in": 500.0},
            "pack": {"sha256": "bb" * 32},
        },
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    # 老调用形态（不传 worker）⇒ 谁也不认：空队列 + blocked 带回（两行都算「别人持有」）
    lines: list[str] = []
    got0, blocked0, _m0 = offline_boot.resolve_courses({}, {}, lines.append)
    assert got0 == []
    assert blocked0 == [
        {"course": "mine", "holder": "w-me", "expires_in": 812.0},
        {"course": "other", "holder": "w-other", "expires_in": 500.0},
    ], blocked0
    note = "\n".join(lines)
    assert "mine[claimed；held: w-me；持有 w-me（812s 后过期）]" in note, lines
    # 带上自己的 worker id ⇒ 续领那一门（别人的租约仍不动）
    lines2: list[str] = []
    got, _blocked, _manifest = offline_boot.resolve_courses({}, {}, lines2.append, worker="w-me")
    assert [t["course"] for t in got] == ["mine"]
    assert any("续领自己未交还的租约：mine" in ln for ln in lines2), lines2


def test_resolve_courses_logs_why_a_row_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 2026-10-04 现场（Kaggle：清单 3 条 ⇒ 队列为空，看不出为什么）：空队列必须**自解释**。

    不可领的原因全在 hub 行的 `state`/`reason`（not_offline=停课 / held=<worker> / completed /
    busy）与盘上事实（无包 / 包过期）里——丢掉它们，云机日志就只剩一句「队列为空」，
    排查只能靠人工 curl `/offline/tasks`（这正是当时卡住的那一步）。
    """
    tasks = [
        {
            "course": "stopped",
            "claimable": False,
            "seize": False,
            "state": "not_offline",
            "reason": "not_offline",
            "pack": {"sha256": "aa" * 32},
        },
        {
            "course": "held",
            "claimable": False,
            "seize": False,
            "state": "ready",
            "reason": "held: tpu-1",
            "pack": {"sha256": "bb" * 32},
        },
        {
            "course": "nopack",
            "claimable": False,
            "seize": False,
            "state": "empty",
            "reason": "",
            "pack": None,
            "stale_reason": "",
        },
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    lines: list[str] = []
    got, blocked, manifest = offline_boot.resolve_courses({}, {}, lines.append)
    assert (got, blocked) == ([], [])
    assert manifest == tasks, "第三个返回值必须是原始清单（终态判据要它的 state）"
    note = "\n".join(lines)
    assert "不可领/不可抢" in note, lines
    assert "stopped[not_offline；not_offline]" in note, lines
    assert "held[ready；held: tpu-1]" in note, lines
    assert "nopack[empty；无任务包（等控制台导出）]" in note, lines


def test_resolve_courses_prefers_offline_over_seize(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 用户裁决的优先级：有就绪的离线课 ⇒ 只取它，在训在线课（seize）这次不碰；
    离线课那份已跑过（served 同 sha）⇒ 顺延去抢。"""
    tasks = [
        {"course": "off", "claimable": True, "seize": False, "pack": {"sha256": "aa" * 32}},
        {"course": "on", "claimable": True, "seize": True, "pack": {"sha256": "bb" * 32}},
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    got, _blocked, _manifest = offline_boot.resolve_courses({}, {}, lambda _m: None)
    assert [t["course"] for t in got] == ["off"]
    got2, _b2, _m2 = offline_boot.resolve_courses({}, {}, lambda _m: None, served={"off": "aa" * 32})
    assert [t["course"] for t in got2] == ["on"]


def test_resolve_courses_seizes_the_first_in_training_online_course(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有就绪的离线课 ⇒ 按 `open_time` 升序抢**一门**（tie 用课名）；缺 `open_time`
    （老 hub / 磁盘读不到）排最后；busy 的行照收（claim 会回 409 busy，调用方等下一拍
    而不是烧 idle 预算）。"""
    tasks = [
        {"course": "b-late", "claimable": False, "seize": True, "open_time": 200.0, "pack": None},
        {"course": "c-tie", "claimable": False, "seize": True, "open_time": 100.0, "pack": None},
        {
            "course": "a-tie",
            "claimable": False,
            "seize": True,
            "open_time": 100.0,
            "pack": {"sha256": "aa" * 32},
        },
        {"course": "d-nokey", "claimable": False, "seize": True, "pack": None},
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    lines: list[str] = []
    got, _blocked, _manifest = offline_boot.resolve_courses({}, {}, lines.append)
    assert [t["course"] for t in got] == ["a-tie"]
    assert any("抢占第一个在训在线课" in ln for ln in lines), lines

    # 已跑过 a-tie 的这份包 ⇒ 顺延到下一门（c-tie）：过滤与离线路同一套。
    got2, _b2, _m2 = offline_boot.resolve_courses({}, {}, lambda _m: None, served={"a-tie": "aa" * 32})
    assert [t["course"] for t in got2] == ["c-tie"]


def test_run_batch_does_not_run_while_the_handoff_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动交接中间态 ⇒ 不建租约、不进 run、不计入失败；只回填给调用方（等下一拍）。"""
    ran: list[str] = []

    def fake_run(*a: Any, **k: Any) -> int:
        ran.append("x")
        return 0

    monkeypatch.setattr(offline_boot, "claim_course", lambda *a, **k: ("", "pending_export"))
    monkeypatch.setattr(offline_boot, "run_one_course", fake_run)
    leases = {"hub": "http://hub", "token": "tok", "worker": "w1", "served": {}}
    rc = offline_boot._run_batch(
        {}, {}, lambda _m: None, None, ["c5-gae"], multi=False, leases=leases, shas={}
    )
    assert rc == 0 and ran == []
    assert leases["blockers"] == {"c5-gae": "pending_export"}
    assert leases["ran"] == 0 and leases["gave_up"] == []


def test_run_auto_waits_for_the_export_without_burning_idle_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导包窗口（分钟级）不能被当成空转：全会话不因它提前收工（U2/P0-1）。"""
    state = {"n": 0}

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list[dict], list[dict], list[dict]]:
        state["n"] += 1
        if state["n"] == 1:
            return [{"course": "c5-gae", "pack_sha256": ""}], [], []
        return [], [], []

    def fake_batch(cfg, creds, log, stop, courses, *, multi, leases, shas):
        leases["blockers"] = {"c5-gae": "pending_export"}
        leases["ran"] = 0
        leases["gave_up"] = []
        return 0

    monkeypatch.setattr(offline_boot, "resolve_courses", fake_resolve)
    monkeypatch.setattr(offline_boot, "_run_batch", fake_batch)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "queue_mode": "once", "queue_poll_sec": 0},
        {"HUB_TOKEN": "tok"},
        lines.append,
        None,
    )
    assert rc == 0
    assert any("不占 idle 预算" in ln for ln in lines), lines


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

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list[dict], list[dict], list[dict]]:
        state["n"] += 1
        if state["n"] == 1:
            return [{"course": "c5-gae", "pack_sha256": "aa" * 32}], [], []
        return [], [], []

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
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], []))
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "queue_mode": "once"}, {}, lines.append, None
    )
    assert rc == 0 and any("once" in ln for ln in lines), lines


def test_run_auto_drain_gives_up_after_idle_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """缺省 drain：空队列驻守，等满 `idle_wait_sec` 才收工（空队列**不是**错误）。"""
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], []))
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 0}, {}, lines.append, None
    )
    assert rc == 0 and any("已等满" in ln for ln in lines), lines


def test_run_auto_stops_on_the_keepalive_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """停机信号（notebook 保活）⇒ 收工，不再空驻守。"""
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], []))
    stop = threading.Event()
    stop.set()
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 999}, {}, lines.append, stop
    )
    assert rc == 0 and any("停机信号" in ln for ln in lines), lines


# ───────────────────────── 终态收工窗口（plan/offline-worker-graceful-exit）─────────


def test_all_terminal_accepts_terminal_and_served_rows() -> None:
    """★ 2026-10-07：两档都算「不会再自己变好」——① 终态 ② 本会话已跑过这份包（同 sha）。"""
    assert frozenset({"completed", "not_offline"}) == offline_boot.TERMINAL_STATES
    assert offline_boot.all_terminal([{"state": "completed"}, {"state": "not_offline"}]) is True
    # 现场原型：`x21-psh-b0` = completed；`x21-psh-b` = hub 仍列 `ready`，但本会话已跑过它的包
    assert (
        offline_boot.all_terminal(
            [
                {"course": "x21-psh-b0", "state": "completed", "pack": {"sha256": "a" * 64}},
                {"course": "x21-psh-b", "state": "ready", "pack": {"sha256": "b" * 64}},
            ],
            {"x21-psh-b": "b" * 64},
        )
        is True
    )


def test_all_terminal_rejects_rows_that_can_still_change() -> None:
    """负向：只要有一行还能自己变好（等导出 / 被别人持有 / 有可领的包）⇒ 不得提前收工。"""
    assert offline_boot.all_terminal([]) is False, "空清单不是「全终态」（那是「队列真的空」）"
    for row in ({"state": "held"}, {"state": "no_pack"}, {"state": "claimed"},
                {"state": "ready"}, {"state": ""}, {}):
        assert offline_boot.all_terminal([row]) is False, row
    # served 只对**同一份包**有效：hub 换了新段（新 sha）⇒ 又能领 ⇒ 不是终态。
    assert (
        offline_boot.all_terminal(
            [{"course": "b", "state": "ready", "pack": {"sha256": "c" * 64}}], {"b": "b" * 64}
        )
        is False
    )
    assert offline_boot.all_terminal([{"state": "completed"}, {"state": "no_pack"}]) is False


def test_run_auto_all_terminal_uses_the_short_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 2026-10-07 现场：清单**全为终态** ⇒ 走独立短窗口，不再空转满 `idle_wait_sec`。

    现场（17:04 已全终态 ⇒ 17:33 才收工 = 29 分钟）里 `idle_wait_sec=1800` 是元凶；
    这里给 `idle_wait_sec=9999` 也照样立刻收工（窗口只认新旋钮）。
    """
    manifest = [
        {"course": "x21-psh-b0", "state": "completed", "claimable": False, "pack": {"sha256": "a" * 64}},
        {"course": "x21-psh-b", "state": "completed", "claimable": False, "pack": {"sha256": "b" * 64}},
    ]

    def _no_batch(*_a: Any, **_k: Any) -> int:
        raise AssertionError("全终态 ⇒ 本拍不该跑任何课")

    monkeypatch.setattr(offline_boot, "_run_batch", _no_batch)
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], manifest))
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {
            "work_dir": str(tmp_path),
            "idle_wait_sec": 9999,
            "idle_wait_terminal_sec": 0,
            "queue_poll_sec": 0,
        },
        {},
        lines.append,
        None,
    )
    assert rc == 0
    assert any("全为终态" in ln for ln in lines), lines
    assert any("idle_wait_terminal_sec=0s ⇒ 收工" in ln for ln in lines), lines


def test_run_auto_stops_when_a_served_pack_comes_back_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 现场原型（判据 ②）：本会话跑过的课在 hub 上仍是 `ready`（同一份包）⇒ 也算终态。

    只判 `state ∈ TERMINAL_STATES` 会漏掉这一档 —— 而**现场正是这一档**：
    `x21-psh-b` 没进「不可领/不可抢」日志（说明它是 `claimable` 行，只被 `served` 挡下）。
    """
    manifest = [
        {"course": "b0", "state": "completed", "claimable": False, "pack": {"sha256": "a" * 64}},
        {"course": "b", "state": "ready", "claimable": True, "pack": {"sha256": "b" * 64}},
    ]
    state = {"n": 0}

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list[dict], list[dict], list[dict]]:
        state["n"] += 1
        if state["n"] == 1:
            return [{"course": "b", "pack_sha256": "b" * 64}], [], manifest
        return [], [], manifest

    def fake_batch(cfg, creds, log, stop, courses, *, multi, leases, shas):
        leases["served"]["b"] = "b" * 64  # 与生产 `_run_batch` 同义：跑完记 served[course]=sha
        leases["blockers"] = {}
        leases["gave_up"] = []
        leases["ran"] = 1
        return 0

    monkeypatch.setattr(offline_boot, "resolve_courses", fake_resolve)
    monkeypatch.setattr(offline_boot, "_run_batch", fake_batch)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {
            "work_dir": str(tmp_path),
            "idle_wait_sec": 9999,
            "idle_wait_terminal_sec": 0,
            "queue_poll_sec": 0,
        },
        {},
        lines.append,
        None,
    )
    assert rc == 0
    assert any("全为终态" in ln for ln in lines), lines
    assert any("idle_wait_terminal_sec=0s ⇒ 收工" in ln for ln in lines), lines


def test_run_auto_does_not_take_the_terminal_window_on_a_mixed_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """负向：有一行**还能自己变好**（`no_pack` = 等控制台导出）⇒ 照旧走 idle 口径。"""
    manifest = [
        {"course": "done", "state": "completed", "claimable": False, "pack": {"sha256": "a" * 64}},
        {"course": "await-pack", "state": "no_pack", "claimable": False, "pack": None},
    ]
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], manifest))
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {
            "work_dir": str(tmp_path),
            "idle_wait_sec": 0,
            "idle_wait_terminal_sec": 0,
            "queue_poll_sec": 0,
        },
        {},
        lines.append,
        None,
    )
    assert rc == 0
    assert not any("全为终态" in ln for ln in lines), lines
    assert any("已等满 idle_wait_sec=0s" in ln for ln in lines), lines


def test_run_auto_terminal_window_is_independent_of_idle_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """终态窗口是**独立**预算：`idle_wait_sec=0` 也不会把它提前收掉（旧口径由新旋钮接管）。

    证明三点：① 进的是终态分支（不是 idle 分支）；② 窗口没到点；③ 最终由会话预算退出。
    """
    manifest = [{"course": "done", "state": "completed", "claimable": False, "pack": {"sha256": "a" * 64}}]
    monkeypatch.setattr(offline_boot, "resolve_courses", lambda cfg, creds, log, **kw: ([], [], manifest))
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {
            "work_dir": str(tmp_path),
            "idle_wait_sec": 0,
            "idle_wait_terminal_sec": 9999,
            "session_budget_sec": 0.05,
            "queue_poll_sec": 0.02,
        },
        {},
        lines.append,
        None,
    )
    assert rc == 0
    assert any("全为终态" in ln for ln in lines), lines
    assert not any("idle_wait_terminal_sec" in ln and "⇒ 收工" in ln for ln in lines), lines
    assert any("session_budget_sec" in ln for ln in lines), lines


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
    assert offline_boot.claim_course("http://hub", "tok", "c5-gae", "w1", lines.append) == (
        "",
        "net",
    )
    offline_boot.release_course("http://hub", "tok", "c5-gae", "lease1", lines.append)
    assert any("连不上" in ln for ln in lines), lines


def test_claim_course_reports_a_foreign_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    """409「被持有」⇒ `("", "held")`（照旧跑）+ 一行「已被谁持有、多久后过期」。"""
    monkeypatch.setattr(
        offline_boot,
        "_post_json",
        lambda url, token, log, *, timeout=0.0: (
            409,
            {"holder": {"worker_id": "w9", "expires_in": 120.0}, "held": True},
        ),
    )
    lines: list[str] = []
    assert offline_boot.claim_course("http://hub", "tok", "c5-gae", "w1", lines.append) == (
        "",
        "held",
    )
    assert any("w9" in ln and "120" in ln for ln in lines), lines


def test_claim_course_maps_auto_handoff_states(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 二轮评审 P0：409 的每种中间态各成一码（旧口径一律当「被持有」⇒ 无租约进入 run）。

    对码不对文案：`_run_batch`/`_run_auto` 的分流全靠这几个词。
    """
    cases: list[tuple[dict, str]] = [
        ({"busy": True, "error": "busy: c-a 正在 w9 上跑"}, "busy"),
        ({"pending_export": True}, "pending_export"),
        ({"pending_export": True, "give_up": True}, "give_up"),
        ({"completed": True}, "completed"),
        ({"not_offline": True}, "not_offline"),
        # ★P1-4 / 六轮 F6：人固定在线 ⇒ 与「停课/被持有」分开的一码（本拍不跑）
        ({"pinned_online": True}, "pinned_online"),
        ({"holder": {"worker_id": "w9"}}, "held"),
    ]
    for doc, want in cases:
        monkeypatch.setattr(
            offline_boot,
            "_post_json",
            lambda url, token, log, *, timeout=0.0, _doc=doc: (409, _doc),
        )
        got = offline_boot.claim_course("http://hub", "tok", "c5-gae", "w1", lambda _m: None)
        assert got == ("", want), (doc, got)


def test_heartbeat_loop_stops_on_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    """★P1-4 / 六轮 F5：心跳收 409 `revoked`（人切回在线/交还自动）⇒ **置停止事件**。

    旧口径把 revoked 当「过期/被接管」继续跑完；新口径下这一课已不再归本盘，
    `run_loop` 在下一个轮边界收尾并打包（不能杀正在算的那一轮）——停止信号就是这条链的入口。
    """
    hits: list[str] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        hits.append(url)
        return (409, {"revoked": True})

    monkeypatch.setattr(offline_boot, "_post_json", fake_post)
    lines: list[str] = []
    done = offline_boot.heartbeat_loop(
        "http://hub", "tok", "c5-gae", "lease1", lines.append, interval=0.01
    )
    assert done.wait(5.0) is True, f"revoked 未置停止事件；lines={lines}"
    assert any("撤销" in ln for ln in lines), lines
    n = len(hits)
    # sleep-ok: 夹具模拟的工作量：给线程退出留窗口（期间本会再跳 5 次，只放行在途的一拍）
    time.sleep(0.05)
    assert len(hits) <= n + 1, "revoked 之后不该继续打点"


def test_resolve_skips_pinned_online_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """★P1-4 / 六轮 F6：pin online 的行**不抢**（行照发、只是锁住）；同表 auto 的照抢。

    「人固定的课」与「被别人持有」不同：它不会自动放出来（不算 blocked 等活）。
    """
    tasks = [
        {
            "course": "fixed",
            "authority": "pinned_online",
            "claimable": False,
            "seize": False,
            "state": "online",
            "reason": "pinned: 人固定在在线",
            "pack": {"sha256": "aa" * 32},
            "open_time": 1.0,
        },
        {
            "course": "auto",
            "authority": "auto",
            "claimable": False,
            "seize": True,
            "state": "online",
            "reason": "",
            "pack": {"sha256": "bb" * 32},
            "open_time": 2.0,
        },
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    got, blocked, _manifest = offline_boot.resolve_courses({}, {}, lambda _m: None)
    assert [t["course"] for t in got] == ["auto"], got
    assert blocked == [], blocked  # 人固定的课不会自动放出来：不算「等它放」


def test_resolve_picks_stale_holder_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """★P1-4：死盘的 stale 行（hub 已判 `claimable=true`）⇒ 直接领，不必等 TTL 过期。"""
    tasks = [
        {
            "course": "dead-held",
            "authority": "auto",
            "claimable": True,
            "seize": False,
            "state": "claimed",
            "reason": "held-stale: w-dead 已静默",
            "pack": {"sha256": "cc" * 32},
            "holder": {
                "worker_id": "w-dead",
                "expires_in": 400.0,
                "stale": True,
                "silent_sec": 301.0,
            },
        },
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    got, blocked, _manifest = offline_boot.resolve_courses({}, {}, lambda _m: None)
    assert [t["course"] for t in got] == ["dead-held"], got
    assert got[0]["pack_sha256"] == "cc" * 32
    assert blocked == [], blocked


def test_blocked_note_reports_stale_holder() -> None:
    """★P1-4：不可领的行若持有者已静默超阈，那一句必须带「已静默 Ns（可直接接管）」
    ——否则人以为只能等 TTL（切模式/停课都不会清租约，这是当时卡住的现场）。"""
    note = offline_boot._blocked_note(
        {
            "course": "c5-gae",
            "state": "claimed",
            "reason": "held-stale: w-dead",
            "holder": {
                "worker_id": "w-dead",
                "expires_in": 30.0,
                "stale": True,
                "silent_sec": 301.0,
            },
            "pack": {"sha256": "ee" * 32},
        }
    )
    assert "w-dead" in note and "301" in note and "可直接接管" in note, note


# ───────────────── M1b（plan/worker-type-dispatch-model §3-M1b，2026-10-07）─────────────────


def test_task_list_worker_claimable_implies_the_same_claim_succeeds(tmp_path: Path) -> None:
    """★DoD#2 守门用例（清单/claim **同源契约**，`queue_offline.py` 模块头第 12-14 行）：

    「带 `?worker=` 的清单说 `claimable=true`」⇒ 同参 `claim` **必成功**。

    ⚠ 这是**上界**契约（F14）：清单与 claim 之间仍有并发窗口（别的盘在这几微秒里抢走），
    所以本用例只在不引入并发的形状下跑——判据是「清单不是乐观的」，不是「claim 永不失败」。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, "c-a")
    _course(tmp_path, "c-b")
    hub.discover(force=True)
    _write_pack(tmp_path, "c-a")
    _write_pack(tmp_path, "c-b")
    # 1) 两台盘各自的第一门课都可领（不忙、无主）
    rows = {r["course"]: r for r in _json(_req(base, OFFLINE_TASKS_PATH + "?worker=w1")[1])["tasks"]}
    assert rows["c-a"]["claimable"] is True and rows["c-b"]["claimable"] is True
    # 2) w1 领下 c-a（清单说的就成真）
    st, _doc = _claim(base, course="c-a", worker="w1")
    assert st == 200, _doc
    # 3) 再问同一个 worker：c-a 不可领（自己持有）、c-b 不可领（一拖一）——拒因必须指名道姓
    rows = {r["course"]: r for r in _json(_req(base, OFFLINE_TASKS_PATH + "?worker=w1")[1])["tasks"]}
    assert rows["c-a"]["claimable"] is False and rows["c-a"]["busy"] is False
    assert rows["c-b"]["claimable"] is False and rows["c-b"]["busy"] is True
    assert "c-a" in rows["c-b"]["reason"]
    st_busy, doc_busy = _claim(base, course="c-b", worker="w1")
    assert st_busy == 409 and doc_busy["busy"] is True, doc_busy  # 一拖一：同源拒绝
    # 自己持有那门：清单说「不可领」（对**别人**不可领）而**自己**再领 = 续领（mine 出口，
    # cell 中断重跑用）——两边不矛盾：清单的 claimable 答的是「你现在拿到手了吗」。
    st_mine, doc_mine = _claim(base, course="c-a", worker="w1")
    assert st_mine == 200 and doc_mine["lease"]["token"] != "", doc_mine
    # 4) 换一台盘：它的清单说可领 ⇒ 可领（多机并行；一拖一只管同一台盘）
    rows = {r["course"]: r for r in _json(_req(base, OFFLINE_TASKS_PATH + "?worker=w2")[1])["tasks"]}
    assert rows["c-b"]["claimable"] is True and rows["c-b"]["busy"] is False
    st3, doc3 = _claim(base, course="c-b", worker="w2")
    assert st3 == 200, doc3


def test_worker_param_and_upper_bound_semantics(tmp_path: Path) -> None:
    """★M1b / §69：清单缺 `?worker=` = **上界**语义（不含一拖一），带 worker 才是真判据。

    为什么两种都要在：云机选下一门只认带 worker 的那份；而控制台排障不带 worker
    （它不想让「某台盘正在跑」把别的课显示成不可领——那会让排障面说谎）。
    """
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, c)
        _write_pack(tmp_path, c)
    hub.discover(force=True)
    assert _claim(base, course="c-a", worker="w1")[0] == 200
    upper = _json(_req(base, OFFLINE_TASKS_PATH)[1])["tasks"]
    rows = {r["course"]: r for r in upper}
    assert rows["c-b"]["claimable"] is True and rows["c-b"]["busy"] is False
    scoped = {r["course"]: r for r in _json(_req(base, OFFLINE_TASKS_PATH + "?worker=w1")[1])["tasks"]}
    assert scoped["c-b"]["claimable"] is False and scoped["c-b"]["busy"] is True
    assert upper[0]["hold"] == {} or upper[0]["hold"]  # 字段在（形状钉住，值随状态）
    assert "hold" in upper[0] and "pending_export" in upper[0] and "busy" in upper[0]


# ───────────── M3（plan/worker-type-dispatch-model §3-M3，2026-10-07）：轮内打点 / 领取分流 ─────────────


def test_progress_constants_track_the_wire_and_the_hold_window() -> None:
    """打点端点名 / 钩子名各在云机侧宠了一份（不得 import `remote.*`）——必须逐字同步。

    另钉一个**数量关系**（不是等值）：轮内打点的节流 M（`PROGRESS_MIN_INTERVAL_SEC`）必须
    ≤ 300s（Q2 的硬上界），且 `3×M ≤` hold 的进度静默阈 —— 丢两三拍也不该把「正在算」判成
    「掉线」（那是自动接管的入口，误判就是两处跑同一份活）。
    """
    from common import progress_hook, protocol
    from hub import task_pack

    assert offline_boot.OFFLINE_PROGRESS_PATH == protocol.OFFLINE_PROGRESS_PATH
    assert offline_boot.PROGRESS_HOOK_NAME == progress_hook.HOOK_NAME
    assert 0.0 < offline_boot.PROGRESS_MIN_INTERVAL_SEC <= 300.0, "Q2：M ≤ 300s"
    assert task_pack.hold_progress_stale_sec() >= 3 * offline_boot.PROGRESS_MIN_INTERVAL_SEC


def _pinger(
    hub: str, lease: str = "lk", *, post: Any = None, interval: float = 3600.0, token: str = "tok"
) -> tuple[Any, list[str]]:
    """装一个打点层（`post` 是假 HTTP；None = 真 `_post_json`/真网络）→ `(detach, log 行)`。"""
    lines: list[str] = []
    detach = offline_boot.install_progress_pinger(
        hub, token, "c5-gae", lease, lines.append, interval=interval, post=post
    )
    return detach, lines


def test_progress_ping_lands_on_the_wire_once_per_window() -> None:
    """形状 + 节流：URL/参数逐字（hub 只吃这两个查询参），首拍恒发，窗内不重复。"""
    from common import progress_hook

    seen: list[tuple[str, str, float]] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        seen.append((url, token, timeout))
        return 200, {}

    detach, lines = _pinger("http://hub", post=fake_post, interval=3600.0)
    try:
        assert progress_hook.report("iter", done=1, total=8) is True
        assert progress_hook.report("iter", done=2, total=8) is False, "窗内（3600s）不再发"
        assert progress_hook.report("round-start", it=7, force=True) is True, "轮边界能顶开节流"
    finally:
        assert detach() == ""
    assert [u for u, _t, _s in seen] == [
        "http://hub/offline/progress?course=c5-gae&lease=lk"
    ] * 2
    assert all(t == "tok" for _u, t, _s in seen)
    assert all(0.0 < s <= offline_boot.PROGRESS_TIMEOUT_SEC for _u, _t, s in seen)
    assert any("局 1/8" in ln for ln in lines), lines
    assert any("it7" in ln for ln in lines), lines


def test_progress_ping_opens_the_next_window_by_time() -> None:
    """节流的尺子是**时间**（不是局数）：窗过就再发；`force` 与节流互不干扰。"""
    from common import progress_hook

    hits: list[int] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        hits.append(1)
        return 200, {}

    detach, _lines = _pinger("http://hub", post=fake_post, interval=0.0)
    try:
        for _ in range(3):
            progress_hook.report("iter", done=1, total=3)
    finally:
        detach()
    assert len(hits) == 3, "interval=0 ⇒ 每一拍都发（时间窗开着）"


def test_progress_ping_409_outcomes_stop_pinging() -> None:
    """409 三态各回各的结局：`revoked` / `expired` / `taken` —— 都**停打点**并带上原因。"""
    from common import progress_hook

    for body, want, needle in (
        ({"revoked": True, "holder": {"worker_id": "w-else"}}, "revoked", "已被撤销"),
        ({"expired": True}, "expired", "已过期"),
        ({"holder": {"worker_id": "w-new"}}, "taken", "w-new"),
    ):
        hits: list[int] = []

        def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0, _b: dict = body):
            hits.append(1)
            return 409, dict(_b)

        detach, lines = _pinger("http://hub", post=fake_post, interval=0.0)
        try:
            assert progress_hook.report("iter", done=1, total=3) is False
            assert progress_hook.report("iter", done=2, total=3) is False
            assert len(hits) == 1, "409 之后不再打扰 hub"
        finally:
            assert detach() == want
        assert any(needle in ln for ln in lines), (want, lines)


def test_progress_ping_gives_up_after_the_failure_limit_without_touching_training() -> None:
    """连不上（HTTP 0）不是什么大事：几拍之后停打点、留一行；训练与租约交给心跳兜。"""
    from common import progress_hook

    hits: list[int] = []

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        hits.append(1)
        return 0, {}

    detach, lines = _pinger("http://hub", post=fake_post, interval=0.0)
    try:
        for _ in range(offline_boot.PROGRESS_FAIL_LIMIT + 2):
            assert progress_hook.report("iter", done=1, total=3) is False
    finally:
        assert detach() == "unreachable"
    assert len(hits) == offline_boot.PROGRESS_FAIL_LIMIT
    assert any("停打点" in ln for ln in lines), lines


def test_progress_pinger_detach_restores_a_previous_registration() -> None:
    """同一进程里逐课装/卸（`_run_batch` 的顺序就是它）：卸下自己后要还原上一层，不许漏。"""
    from types import ModuleType

    outer = ModuleType(offline_boot.PROGRESS_HOOK_NAME)
    outer.__dict__["note_progress"] = lambda kind="", **kw: True
    sys.modules[offline_boot.PROGRESS_HOOK_NAME] = outer
    try:
        detach, _lines = _pinger("http://hub", post=lambda *a, **k: (200, {}))
        assert sys.modules[offline_boot.PROGRESS_HOOK_NAME] is not outer
        assert detach() == ""
        assert sys.modules[offline_boot.PROGRESS_HOOK_NAME] is outer
    finally:
        sys.modules.pop(offline_boot.PROGRESS_HOOK_NAME, None)


def test_install_progress_pinger_does_not_start_a_thread() -> None:
    """★Q2 的红线：打点**绝不另起线程**（定时线程正是「心跳活、进度死」的成因）。

    判据 = 装层前后线程数不变，且装完之后**不调完成事件就一枪都不发**。
    """
    from common import progress_hook

    hits: list[int] = []
    before = threading.active_count()

    def fake_post(url: str, token: str, log: Any, *, timeout: float = 0.0) -> tuple[int, dict]:
        hits.append(1)
        return 200, {}

    detach, _lines = _pinger("http://hub", post=fake_post)
    try:
        assert threading.active_count() == before
        # sleep-ok: 夹具模拟的窗口——若真有定时线程，这段时间足够它打一枪（断言它没打）
        time.sleep(0.05)
        assert hits == [], "没有完成事件就不该有任何打点"
        assert progress_hook.report("iter", done=1, total=2) is True
    finally:
        detach()
    assert len(hits) == 1


def test_progress_ping_refreshes_the_hold_on_a_real_hub(tmp_path: Path) -> None:
    """端到端（真 hub + 真 HTTP）：带对租约的 ping ⇒ 200 且 hold 的活性锚被刷新。

    为什么必须有这一条：URL/参数名漂了（`lease` 写成 `lease_token` 之类）时，假 `post` 那边
    一路绿，只有真端点会说「不行」；而「进度刷新活性」才是这一层存在的理由 —— 长轮次里
    静默 900s 就会被别人接管（自动接管是设计，误接管是事故）。
    """
    from common import progress_hook

    base, hub, _srv = _offline_hub(tmp_path)
    _write_pack(tmp_path)
    st, doc = _claim(base)
    assert st == 200, doc
    tok = str(doc["lease"]["token"])
    # 造出「心跳活、进度死」那一档（§68）：TTL 被心跳续到很后面，而进度锚停在接管那一刻
    # （判据全走 `_now()`：把时钟推过静默阈 900s，但不超过续过的 TTL）。
    now = time.time()
    with hub._lease_lock:
        hub._leases["c5-gae"]["expires_at"] = now + 10_000.0
    real_now = hub._now
    hub._now = lambda: now + 2_000.0  # type: ignore[method-assign]
    try:
        assert hub.hold_of("c5-gae")["state"] == "stale"
        detach, lines = _pinger(base, tok, token=TOKEN)
        assert sys.modules[offline_boot.PROGRESS_HOOK_NAME] is not None
        assert hub.hold_of("c5-gae")["state"] == "stale", "装层本身不算进度"
        assert progress_hook.report("iter", done=1, total=4) is True
        assert hub.hold_of("c5-gae")["state"] == "live", "打点刷活了 hold"
        assert detach() == ""
    finally:
        hub._now = real_now  # type: ignore[method-assign]
    assert any("→ hub" in ln for ln in lines), lines
    # 错租约（别人接管了 / token 过期）⇒ 真端点回 409，层按 taken/expired 停手（不再瞎 ping）
    detach2, _l2 = _pinger(base, "not-my-token", token=TOKEN)
    try:
        assert progress_hook.report("iter", done=1, total=4) is False
    finally:
        assert detach2() in ("taken", "expired")


def test_resolve_courses_puts_a_pending_export_row_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """★M3：导包软态（`pending_export`）**排到最后**——它不占闸（Q1）所以照领，但 claim 必 409。

    为什么是排序而不是跳过：先把能跑的挑完（列表顺序 = `_run_batch` 的 claim/执行顺序），
    `_run_batch` 走到导包那门时记一笔 blocker 就换下一门——一次注定失败的 claim 不该排在队首。
    """
    tasks = [
        {"course": "exporting", "claimable": True, "pending_export": {"by": "w", "at": 1.0}},
        {"course": "ready", "claimable": True, "pending_export": {}},
        {"course": "also-ready", "claimable": True},
    ]
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "fetch_task_list", lambda *a, **k: tasks)
    lines: list[str] = []
    got, blocked = offline_boot.resolve_courses({}, {}, lines.append)
    assert [t["course"] for t in got] == ["ready", "also-ready", "exporting"]
    assert blocked == []
    assert any("导包" in ln and "最后" in ln for ln in lines), lines


def test_run_batch_reports_the_revoked_outcome_and_retires_the_course(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★M3：打点层报回 `revoked`（人把课切回在线/交还自动）⇒ 段照常跑完，本会话不再领它。

    清单/结算两个方向都钉：`leases["revoked"]` 回填给 `_run_auto`（下拍跳过），而 rc 不受影响
    （段已经完整落盘 + 打包，不是失败）。
    """
    beats: list[threading.Event] = []
    released: list[str] = []

    monkeypatch.setattr(offline_boot, "claim_course", lambda *a, **k: ("lease1", ""))

    def fake_beat(hub: str, token: str, course: str, lease: str, log: Any, **kw: Any) -> Any:
        beats.append(threading.Event())
        return beats[-1]

    monkeypatch.setattr(offline_boot, "heartbeat_loop", fake_beat)

    def fake_release(hub: str, token: str, course: str, lease: str, log: Any, **kw: Any) -> None:
        released.append("rel")

    monkeypatch.setattr(offline_boot, "release_course", fake_release)

    def fake_run(cfg, creds, log, stop, *, course, multi, lease, progress, **_kw: Any) -> int:
        assert lease == "lease1"
        progress["outcome"] = "revoked"
        return 0

    monkeypatch.setattr(offline_boot, "run_one_course", fake_run)
    leases: dict = {"hub": "http://hub", "token": "tok", "worker": "w1", "served": {}}
    lines: list[str] = []
    rc = offline_boot._run_batch(
        {}, {}, lines.append, None, ["c5-gae"], multi=False, leases=leases, shas={}
    )
    assert rc == 0
    assert leases["revoked"] == ["c5-gae"] and leases["ran"] == 1
    assert any("租约结局：revoked" in ln for ln in lines), lines
    # 心跳线停、租约交还（交还可能 409，那是 hub 说「你已经不是持有人」——不影响成果）
    assert beats and beats[0].is_set() and released == ["rel"]


def test_run_auto_does_not_reclaim_a_revoked_course(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★M3：段内被撤销的课，本会话不再领（下一拍的 `resolve_courses(skip=…)` 必须看见它）。"""
    state = {"n": 0}
    seen_skip: list[set[str]] = []

    def fake_resolve(cfg: dict, creds: dict, log: Any, **kw: Any) -> tuple[list[dict], list[dict]]:
        state["n"] += 1
        seen_skip.append(set(kw.get("skip") or ()))
        if state["n"] == 1:
            return [{"course": "c5-gae", "pack_sha256": ""}], []
        return [], []

    def fake_batch(cfg, creds, log, stop, courses, *, multi, leases, shas):
        leases["revoked"] = ["c5-gae"]
        leases["blockers"] = {}
        leases["gave_up"] = []
        leases["ran"] = 1
        return 0

    monkeypatch.setattr(offline_boot, "resolve_courses", fake_resolve)
    monkeypatch.setattr(offline_boot, "_run_batch", fake_batch)
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    lines: list[str] = []
    rc = offline_boot._run_auto(
        {"work_dir": str(tmp_path), "idle_wait_sec": 0},
        {"HUB_TOKEN": "tok"},
        lines.append,
        None,
    )
    assert rc == 0
    assert len(seen_skip) >= 2 and "c5-gae" in seen_skip[1], seen_skip
    assert any("租约已被撤销" in ln for ln in lines), lines


def test_explicit_course_queue_claims_before_fetching_the_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★M3 / P1-2 配套：`CFG.course` 点名腿也**先 claim 再取包**（次序是判据，不是风格）。

    · 取包门看 hold：不 claim 的话，本机上一段留下的 live hold 会让**自己**取不到包；
    · hub 侧「谁在跑这门课」只剩 hold 一个真源：不 claim = 控制台看到「没人接手」。
    所以顺序必须是 claim → 取包，且 claim 拿到的租约要一路传到 `run_one_course`（→ `?lease=`）。
    """
    events: list[str] = []
    got_lease: list[str] = []

    def fake_claim(
        hub: str, token: str, course: str, worker: str, log: Any, **kw: Any
    ) -> tuple[str, str]:
        events.append("claim")
        return "lease9", ""

    monkeypatch.setattr(offline_boot, "claim_course", fake_claim)

    def fake_run(cfg, creds, log, stop, *, course, multi, lease, progress=None, **_kw: Any) -> int:
        events.append("run")
        got_lease.append(lease)
        return 0

    monkeypatch.setattr(offline_boot, "run_one_course", fake_run)
    monkeypatch.setattr(offline_boot, "heartbeat_loop", lambda *a, **k: threading.Event())
    monkeypatch.setattr(offline_boot, "release_course", lambda *a, **k: None)
    leases = {"hub": "http://hub", "token": "tok", "worker": "w-exp", "served": {}}
    rc = offline_boot._run_batch(
        {}, {}, lambda _m: None, None, ["c5-gae"], multi=False, leases=leases, shas={}
    )
    assert rc == 0 and events == ["claim", "run"], events
    assert got_lease == ["lease9"], "取包那条腿（run_one_course）必须拿到 claim 的租约"


def test_run_hands_the_lease_context_to_the_batch_for_explicit_courses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★M3：点名腿的接线（`run()` → `_run_batch(leases=…)`）——旧行为是 `leases=None`。"""
    seen: dict = {}

    monkeypatch.setattr(offline_boot, "_load_deliverable", lambda: _StubDeliverable())
    monkeypatch.setattr(
        offline_boot, "_explicit_leases", lambda cfg, creds, log: {"hub": "h", "worker": "w"}
    )

    def spy(cfg, creds, log, stop, courses, *, multi, leases, shas=None) -> int:
        seen.update(courses=list(courses), leases=leases, multi=multi)
        return 0

    monkeypatch.setattr(offline_boot, "_run_batch", spy)
    rc = offline_boot.run(
        {"course": ["c5-gae"], "work_dir": str(tmp_path)},
        lambda _m: None,
        lambda _k, _d=None: "tok",
    )
    assert rc == 0
    assert seen["courses"] == ["c5-gae"] and seen["leases"] == {"hub": "h", "worker": "w"}
    assert seen["multi"] is False


def test_explicit_leases_stays_empty_without_a_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有可用的 hub 地址 ⇒ 点名腿不领租约（纯离线 + 手动包：取包/回传照旧）。"""
    _no_net(monkeypatch)
    lines: list[str] = []
    assert offline_boot._explicit_leases({}, {}, lines.append) == {}
    assert any("纯离线" in ln for ln in lines), lines


class _StubDeliverable:
    """`_load_deliverable()` 的最小桩（点名腿只问它「要跑哪几门课」）。"""

    def requested_courses(self, cfg: dict) -> list[str]:
        return [str(c) for c in (cfg.get("course") or [])]

    def download_dir(self, cfg: dict) -> Path:
        return Path(str(cfg.get("work_dir") or "."))

    def course_work_dir(self, cfg: dict, course: str, *, multi: bool = False) -> Path:
        root = Path(str(cfg.get("work_dir") or "."))
        return root / course if multi else root

    def package_deliverable(self, dest: Path, course: str, download_dir: Path, log: Any) -> None:
        """交付物打包（本用例不关心产物；`run_one_course` 的尾巴要它存在）。"""
        return None


def test_run_one_course_wires_the_progress_pinger_around_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★M3：打点层装在「hub 可达 ∧ 领到租约」的整段外面，且**跑完必须卸下**。

    为什么卸下是硬要求：`_run_batch` 逐课串行，而打点层的 URL 绑着课程名 + 租约；留着一个
    指向上一课的层，下一段的进度就会报到别人账上（静默的错账）。顺序也钉住：
    install → run_loop_main → detach（detach 的返回值 = 本段租约结局）。
    """
    order: list[str] = []

    def spy_install(hub: str, token: str, course: str, lease: str, log: Any, **kw: Any):
        assert (hub, token, course, lease) == ("http://hub", "tok", "c5-gae", "lease7")
        order.append("install")

        def _detach() -> str:
            order.append("detach")
            return "revoked"

        return _detach

    def fake_loop(argv: list[str]) -> int:
        order.append("run")
        return 0

    monkeypatch.setattr(offline_boot, "_load_deliverable", lambda: _StubDeliverable())
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: ["http://hub"])
    monkeypatch.setattr(offline_boot, "probe_hub", lambda hub, token, log: True)
    monkeypatch.setattr(offline_boot, "obtain_pack", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "ensure_code", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "ensure_ts_tree", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "fetch_resume", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "install_progress_pinger", spy_install)
    monkeypatch.setattr(offline_boot, "local_artifacts", lambda dest: None)
    progress: dict = {}
    rc = offline_boot.run_one_course(
        {"course": "c5-gae", "device": "cpu", "work_dir": str(tmp_path)},
        {"HUB_TOKEN": "tok"},
        lambda _m: None,
        None,
        course="c5-gae",
        lease="lease7",
        run_loop_main=fake_loop,
        progress=progress,
    )
    assert rc == 0
    assert order == ["install", "run", "detach"], order
    assert progress["outcome"] == "revoked"


def test_run_one_course_does_not_arm_the_pinger_without_a_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """没领到租约（老 hub / 纯离线）⇒ 不装打点层：没有 hold 就没有「进度」这回事（Q1）。"""
    armed: list[int] = []

    monkeypatch.setattr(offline_boot, "_load_deliverable", lambda: _StubDeliverable())
    monkeypatch.setattr(offline_boot, "hub_candidates", lambda cfg, creds: [])
    monkeypatch.setattr(offline_boot, "obtain_pack", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "ensure_code", lambda *a, **k: None)
    monkeypatch.setattr(offline_boot, "ensure_ts_tree", lambda *a, **k: None)
    def fake_install(*a: Any, **k: Any) -> Any:
        armed.append(1)
        return lambda: ""

    monkeypatch.setattr(offline_boot, "install_progress_pinger", fake_install)
    monkeypatch.setattr(offline_boot, "local_artifacts", lambda dest: None)
    progress: dict = {}
    rc = offline_boot.run_one_course(
        {"course": "c5-gae", "device": "cpu", "work_dir": str(tmp_path)},
        {},
        lambda _m: None,
        None,
        course="c5-gae",
        run_loop_main=lambda argv: 0,
        progress=progress,
    )
    assert rc == 0 and armed == [] and progress == {}
