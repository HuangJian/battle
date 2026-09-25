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
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from remote import hub_server
from remote.hub_server import _HubQueue, _JobStore, as_hub, make_server
from remote.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    OFFLINE_TASK_PACK_PATH,
    ROLE_HEADER,
    ROLE_HEADER_VALUE,
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
    # 开课标记：课程表 = 账本 ∧ `training-enabled.txt`（控制台「开课」写、停课删）——
    # 只造账本的目录在 2026-09-20 之后不算在训（不写它，`hub.discover()` 什么也扫不到）。
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover()
    assert hub.courses() == ["c5-gae"]

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 404, body
    assert "导出" in body["error"], body
    assert body["course"] == "c5-gae"
    assert body["known_courses"] == ["c5-gae"]


def test_task_pack_online_course_is_409_even_when_the_pack_lives_on_disk(tmp_path: Path) -> None:
    """★ mode 门（2026-09-25，plan/online-offline-role-routing §2.4）：包在盘上 ≠ 该发给你。

    现场：切离线时控制台会自动导出 `task-<课>.zip`，而且「已有包不动」⇒ **切回在线后那个包
    还在**。本端点原来不查 mode ⇒ 离线盘能把一门**在线**课取走并自己跑整段（L6）。
    判据用 409 而不是 404：包在、没丢，正确动作是「去控制台切回离线」，404 会把人引向
    「再导一次」（越导越乱）。
    """
    payload = b"PK\x03\x04" + b"stale-but-present" * 8
    _write_pack(tmp_path, "c5-gae", payload)
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    base, hub, _srv = _boot(tmp_path)
    hub.discover()
    assert hub.courses() == ["c5-gae"] and hub.mode_of("c5-gae") != "offline"

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 409, body
    assert raw != payload, "拦住就不能把包发出去"
    assert body["mode"] != "offline" and "online" in body["error"]
    # 反面：同一份包、同一门课，切成离线后照发（证明上面拦的是归属而不是「文件没了」）
    assert hub.set_mode("c5-gae", "offline") is True
    st2, raw2, _h2 = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert st2 == 200 and raw2 == payload


def test_task_pack_cold_course_still_served(tmp_path: Path) -> None:
    """「不在课程表里」**不**等于在线（离线课本来就不常训练，从表里掉出去是常态）。

    拿“不在表里”当 online 会把正常取包锁死（与 `_task_pack_miss_candidate` ① 同一条规则）。
    """
    payload = b"PK\x03\x04" + b"cold-course" * 8
    _write_pack(tmp_path, "c5-gae", payload)
    base, hub, _srv = _boot(tmp_path)
    assert hub.courses() == []  # 真没扫到这门课
    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert st == 200 and raw == payload


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
    # 开课标记：课程表 = 账本 ∧ `training-enabled.txt`（控制台「开课」写、停课删）——
    # 只造账本的目录在 2026-09-20 之后不算在训（不写它，`hub.discover()` 什么也扫不到）。
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
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
    # 开课标记：课程表 = 账本 ∧ `training-enabled.txt`（控制台「开课」写、停课删）——
    # 只造账本的目录在 2026-09-20 之后不算在训（不写它，`hub.discover()` 什么也扫不到）。
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
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
    # 开课标记：课程表 = 账本 ∧ `training-enabled.txt`（控制台「开课」写、停课删）——
    # 只造账本的目录在 2026-09-20 之后不算在训（不写它，`hub.discover()` 什么也扫不到）。
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover()

    st, raw, _h = _get(base, "/admin/offline")
    assert st == 200
    assert _as_json(raw)["progress"] == {}


# --------------------------------------------------- 新鲜度门（§8，2026-09-24）
#
# 用户口径：「即使云机重启、之前的工作目录全丢，重新请求离线任务包时 hub 端也要基于课程的
# **最新状态**重新打包，而不是继续使用开课时那份任务包，避免重复训练浪费算力」。
# 过期的包比没包更危险（404 只让云机多等一拍），所以：过期 ⇒ 触发重导 + 409；
# 到上界仍过期 ⇒ 照发 + 告警（**不把云机 brick 到 deadline**）；判不了 ⇒ 照发。

INIT = b'{"format":"nn-weights-json","params":{"w":1}}'


@pytest.fixture(autouse=True)
def _clean_trigger_ledger():
    """触发账本是模块级的（要跨请求存活）——每个用例前后清干净，否则会互相串。

    两本账（过期 / 缺包）都要清：它们共享同一门课名，串起来会让「恰好一次」的断言随机红。
    """
    hub_server.reset_task_pack_triggers()
    hub_server.reset_task_pack_miss_triggers()
    yield
    hub_server.reset_task_pack_triggers()
    hub_server.reset_task_pack_miss_triggers()


def _export_real_pack(tmp_path: Path, course: str, init: bytes, monkeypatch) -> Path:
    """用**真导出器**写一个包：判据的输入就是索引里的 `parts[...]` sha。"""
    from remote import bundle as bundle_mod
    from remote.artifacts import sha256_bytes

    monkeypatch.setattr(bundle_mod, "normalize_manifest", lambda m: m)
    src = tmp_path / "_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "init_weights.json").write_bytes(init)
    (src / "code.zip").write_bytes(b"PK\x03\x04code")
    with zipfile.ZipFile(src / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/x.ts", "// ts\n")
    plan = json.dumps({"start_it": 1, "end_it": 5}).encode("utf-8")
    out = tmp_path / course / f"task-{course}.zip"
    bundle_mod.export_bundle(
        out,
        manifest={
            "kind": "run",
            "runId": "run-off",
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


def _stub_trigger(monkeypatch, result: tuple[bool, str] = (True, "ok")) -> list[str]:
    calls: list[str] = []

    def fake(course: str, log=None):
        calls.append(course)
        return result

    monkeypatch.setattr(hub_server, "trigger_task_bundle_export", fake)
    return calls


def test_task_pack_index_name_tracks_the_exporter() -> None:
    """索引名在 hub 侧又拄了一份（过期判定要读它）——改名必须两边一起。"""
    from remote.bundle import BUNDLE_INDEX

    assert hub_server.TASK_PACK_INDEX_NAME == BUNDLE_INDEX


def test_stale_reason_only_speaks_when_both_sides_are_readable() -> None:
    """判据（纯函数）：两侧都可读且不等 ⇒ 过期；任一不可读 ⇒ 不判（照发）。"""
    assert hub_server.task_pack_stale_reason(pack_init_sha="a" * 64, active_sha="a" * 64) == ""
    assert hub_server.task_pack_stale_reason(pack_init_sha="", active_sha="a" * 64) == ""
    assert hub_server.task_pack_stale_reason(pack_init_sha="a" * 64, active_sha="") == ""
    why = hub_server.task_pack_stale_reason(pack_init_sha="a" * 64, active_sha="b" * 64)
    assert "sha12=aaaaaaaaaaaa" in why and "sha12=bbbbbbbbbbbb" in why


def test_decide_task_pack_is_a_total_table() -> None:
    """四种结局定死：serve / trigger / throttled / give_up（上界优先于节流）。"""
    assert hub_server.decide_task_pack(stale="", secs_since_trigger=0.0, triggers=0) == "serve"
    assert hub_server.decide_task_pack(stale="x", secs_since_trigger=1e9, triggers=0) == "trigger"
    assert hub_server.decide_task_pack(stale="x", secs_since_trigger=10.0, triggers=1) == "throttled"
    assert (
        hub_server.decide_task_pack(
            stale="x", secs_since_trigger=1e9, triggers=hub_server.TASK_PACK_STALE_TRIGGER_LIMIT
        )
        == "give_up"
    )


def test_task_pack_fresh_is_byte_identical_and_silent(tmp_path, monkeypatch) -> None:
    """反向判据：包已是最新 ⇒ 不触发、不 409、不多一次请求（与今天逐字节一致）。"""
    calls = _stub_trigger(monkeypatch)
    pack = _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT)  # 课程当前位置 == 包起点
    base, _hub, _srv = _boot(tmp_path)

    st, raw, headers = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert st == 200, raw[:200]
    assert raw == pack.read_bytes()
    assert "task-c5-gae.zip" in headers.get("Content-Disposition", "")
    assert calls == [], "最新就不该触发重导"


def test_task_pack_stale_triggers_exactly_once_and_answers_409(tmp_path, monkeypatch) -> None:
    calls = _stub_trigger(monkeypatch)
    _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT + b"-moved-on")
    base, _hub, _srv = _boot(tmp_path)

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 409, body
    assert body["stale"] is True and body["triggered"] is True
    assert "已触发" in body["error"], body
    assert calls == ["c5-gae"], "恰好触发一次"


def test_task_pack_throttle_stops_a_second_trigger(tmp_path, monkeypatch) -> None:
    """多台云机同时问不该各触发一次；窗内第二次直接 409（不触发）。"""
    calls = _stub_trigger(monkeypatch)
    _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT + b"-moved-on")
    base, _hub, _srv = _boot(tmp_path)

    first = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    second = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert first[0] == 409 and second[0] == 409
    assert "刚刚已触发" in _as_json(second[1])["error"]
    assert calls == ["c5-gae"]


def test_task_pack_gives_up_and_serves_after_the_trigger_limit(tmp_path, monkeypatch) -> None:
    """上界（不 brick 保险丝）：连触发到顶仍过期 ⇒ **照发旧包** + 一行告警。

    为什么非有不可：只要还有别的 worker 在回传，`weights.json` 就一直在动 ⇒ 判据是移动靶，
    没有上界时云机会被 409 卡到 deadline（30 分钟）然后 SystemExit。
    """
    monkeypatch.setattr(hub_server, "TASK_PACK_STALE_THROTTLE_SEC", 0.0)  # 让每次请求都可触发
    calls = _stub_trigger(monkeypatch)
    pack = _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT + b"-moved-on")
    base, _hub, _srv = _boot(tmp_path)

    for _ in range(hub_server.TASK_PACK_STALE_TRIGGER_LIMIT):
        assert _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[0] == 409
    served = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert served[0] == 200, served[1][:200]
    assert served[1] == pack.read_bytes()
    assert calls == ["c5-gae"] * hub_server.TASK_PACK_STALE_TRIGGER_LIMIT


def test_task_pack_degrades_to_guidance_when_the_console_is_unreachable(tmp_path, monkeypatch) -> None:
    """控制台不可达/不属本机（只读门控 403）⇒ 降级为 409 + 指引，**不抛**（云机还得能排障）。"""
    _stub_trigger(monkeypatch, (False, "unreachable"))
    _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    (tmp_path / "c5-gae" / "weights.json").write_bytes(INIT + b"-moved-on")
    base, _hub, _srv = _boot(tmp_path)

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 409, body
    assert body["triggered"] is False
    assert "导出任务包" in body["error"], body


def test_task_pack_without_active_weights_is_served(tmp_path, monkeypatch) -> None:
    """课程还没权重（从未回传/冷启动）⇒ 判不了 ⇒ 照发（不把云机拦在门外）。"""
    calls = _stub_trigger(monkeypatch)
    pack = _export_real_pack(tmp_path, "c5-gae", INIT, monkeypatch)
    base, _hub, _srv = _boot(tmp_path)
    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    assert st == 200 and raw == pack.read_bytes()
    assert calls == []


def test_trigger_marks_busy_as_success(tmp_path, monkeypatch) -> None:
    """控制台回 409（上一次导出还在跑）⇒ **算触发成功**（它本来就会产新包）。"""
    import urllib.error

    def fail(req, timeout=0.0):
        raise urllib.error.HTTPError(req.full_url, 409, "busy", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(hub_server, "_net_urlopen", fail)
    ok, why = hub_server.trigger_task_bundle_export("c5-gae", log=lambda _m: None)
    assert ok is True and why == "busy"


def test_trigger_reports_unreachable_console(tmp_path, monkeypatch) -> None:
    def boom(req, timeout=0.0):
        raise OSError("connection refused")

    monkeypatch.setattr(hub_server, "_net_urlopen", boom)
    ok, why = hub_server.trigger_task_bundle_export("c5-gae", log=lambda _m: None)
    assert ok is False and why == "OSError"


def test_role_header_name_is_shared_with_workers() -> None:
    """头名/值是与 worker 的跨层契约：改一边忘另一边会变成「整段 job 永远没人领」。

    这里钉的是**字面量**（worker 侧那条断言在 `tests/test_worker_offline_cap.py`）。
    字面量**故意**保留 `X-Battle-Offline`（语义已从「能力」升为「归属」，改名只会让混合
    部署里的带标 worker 静默掉线）——所以这条断言守的是「不许顺手改名」。
    """
    assert ROLE_HEADER == "X-Battle-Offline"
    assert ROLE_HEADER_VALUE == "1"

# --------------------------------------------- 缺包自愈门（§3.4，2026-09-25）
#
# 用户口径（2026-09-25 报障）：在线课切成离线后云机取包 404，等满 `wait_pack_sec`（30 分钟）
# 才由一句 `SystemExit` 告诉人。根因之一：**包不存在时这条路径零自愈**——`_task_pack_gate`
# 只管「过期」；缺包连门都不进。现在 hub 替这门课推一次控制台重导（带节流 + 上界）。
#
# 候选面的判据（评审 S-1）：**盘上的事实优先于「hub 扫到了没有」**。课程表是「1 小时新鲜度
# 扫描」的产物，而离线课本机不训练 ⇒ 冷掉/重启后它就从表里消失；只认表会在最需要自愈的
# 场景里静默无作为。所以：表里明确 online ⇒ 不导（配置误会）；否则盘上有开课标记就算数。


def _offline_course(tmp_path: Path, hub: _HubQueue, course: str = "c5-gae") -> None:
    """造一门「hub 认为是离线」的课：目录 + 开课标记 + 账本 + `mode=offline`。"""
    (tmp_path / course / "remote-jobs").mkdir(parents=True)
    (tmp_path / course / "training_log.jsonl").touch()
    (tmp_path / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)
    assert hub.set_mode(course, "offline") is True


def test_missing_pack_triggers_one_rebuild_and_says_so(tmp_path, monkeypatch) -> None:
    """缺包 ⇒ 替云机推一次重导（404 正文如实报），窗内第二次只节流、**不再打扰控制台**。"""
    calls = _stub_trigger(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _offline_course(tmp_path, hub)

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 404, body
    assert body["triggered"] is True and body["give_up"] is False
    assert "重导" in body["trigger_note"]
    assert body["retry_after"] == hub_server.TASK_PACK_STALE_THROTTLE_SEC
    assert calls == ["c5-gae"], "恰好替云机推一次"

    again = _as_json(_get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[1])
    assert again["triggered"] is False
    assert "刚刚已触发" in again["trigger_note"], again
    assert calls == ["c5-gae"], "节流窗内不再重复"


def test_missing_pack_gives_up_after_the_miss_limit(tmp_path, monkeypatch) -> None:
    """上界：连推到顶仍没包 ⇒ 不再触发、只在正文里指路手动（**不制造新的等待理由**）。"""
    monkeypatch.setattr(hub_server, "TASK_PACK_STALE_THROTTLE_SEC", 0.0)
    calls = _stub_trigger(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _offline_course(tmp_path, hub)
    url = f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae"

    for _ in range(hub_server.TASK_PACK_MISS_TRIGGER_LIMIT):
        body = _as_json(_get(base, url)[1])
        assert body["triggered"] is True and body["give_up"] is False, body
    last = _as_json(_get(base, url)[1])
    assert last["triggered"] is False and last["give_up"] is True, last
    assert "手动" in last["trigger_note"], last
    assert calls == ["c5-gae"] * hub_server.TASK_PACK_MISS_TRIGGER_LIMIT


def test_missing_pack_ledger_resets_when_the_pack_appears(tmp_path, monkeypatch) -> None:
    """包又在了（200）⇒ 缺包账本清零。

    为什么非清不可：不清就等于**一次上界用一辈子**——运维修好再删包（或干脆重导失败）时，
    hub 再也不会替云机推一次（plan §3.6-10）。
    """
    monkeypatch.setattr(hub_server, "TASK_PACK_STALE_THROTTLE_SEC", 0.0)
    calls = _stub_trigger(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _offline_course(tmp_path, hub)
    url = f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae"

    for _ in range(hub_server.TASK_PACK_MISS_TRIGGER_LIMIT):
        _get(base, url)
    assert _as_json(_get(base, url)[1])["give_up"] is True

    pack = _write_pack(tmp_path, "c5-gae")
    assert _get(base, url)[0] == 200, "包在盘上 ⇒ 照发"
    pack.unlink()
    body = _as_json(_get(base, url)[1])
    assert body["triggered"] is True, body
    assert calls == ["c5-gae"] * (hub_server.TASK_PACK_MISS_TRIGGER_LIMIT + 1)


def test_missing_pack_online_course_is_not_triggered(tmp_path, monkeypatch) -> None:
    """表里明确是 online ⇒ **不替它导**（云机来取包是配置误会），正文里说清下一步。"""
    calls = _stub_trigger(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    (tmp_path / "c5-gae" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "c5-gae" / "training_log.jsonl").touch()
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)
    assert hub.mode_of("c5-gae") == "online"

    body = _as_json(_get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[1])
    assert body["triggered"] is False
    assert "online" in body["trigger_note"] and "控制台" in body["trigger_note"], body
    assert calls == []


def test_missing_pack_unknown_course_does_not_trigger(tmp_path, monkeypatch) -> None:
    """拼错的课程名 / traj-root 不对 ⇒ 推重导也没用（推了只会造一个同样取不到的包）：只指路。"""
    calls = _stub_trigger(monkeypatch)
    base, _hub, _srv = _boot(tmp_path)
    body = _as_json(_get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[1])
    assert body["triggered"] is False and body["give_up"] is False
    assert "开课标记" in body["trigger_note"] and "--traj-root" in body["trigger_note"], body
    assert calls == []


def test_missing_pack_marker_on_disk_is_enough_when_hub_forgot_it(tmp_path, monkeypatch) -> None:
    """评审 S-1：离线课本机不训练 ⇒ 冷掉后 hub 表里没有它；**盘上的事实**仍让自愈成立。"""
    calls = _stub_trigger(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    (tmp_path / "c5-gae").mkdir()
    (tmp_path / "c5-gae" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)
    assert hub.courses() == [], "只有开课标记、没有新鲜活证据 ⇒ 不在表里（本用例的前提）"

    st, raw, _h = _get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _as_json(raw)
    assert st == 404, body
    assert body["known_courses"] == []
    assert body["triggered"] is True, body
    assert calls == ["c5-gae"]


def test_missing_pack_trigger_unreachable_console_points_to_manual(tmp_path, monkeypatch) -> None:
    """控制台不可达 ⇒ 降级成「请手动导」（云机还得能排障，不抛、不 brick）。"""
    _stub_trigger(monkeypatch, (False, "OSError"))
    base, hub, _srv = _boot(tmp_path)
    _offline_course(tmp_path, hub)

    body = _as_json(_get(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[1])
    assert body["triggered"] is False and body["give_up"] is False
    assert "控制台" in body["trigger_note"] and "导出任务包" in body["trigger_note"], body


def test_discover_counts_a_fresh_task_pack_as_liveness_evidence(tmp_path) -> None:
    """评审 S-1：包是**文件系统事实**（与 `task_pack_path` 同源），也是这门课活着的证据。

    离线课本机不训练 ⇒ `remote-jobs`/`training_log` 一小时后全部变旧；只认那两样会让这门课
    从表里消失（控制台切离线的 mode POST 400、404 正文的 `known_courses` 也没有它）。
    """
    hub = _HubQueue({}, discover_root=tmp_path)
    ent = tmp_path / "c5-gae"
    ent.mkdir()
    (ent / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)
    assert hub.courses() == [], "只有标记、没有活证据 ⇒ 还不算"

    (ent / "task-c5-gae.zip").write_bytes(b"PK\x03\x04fake-task")
    hub.discover(force=True)
    assert hub.courses() == ["c5-gae"], "新鲜的包就是活证据"
