"""自动离线交接（plan/auto-offline-handoff，2026-10-03）。

治什么：开课时不再指定离线/在线 —— **当且仅当**一块离线盘真的领走某门课时，hub 才把那门课
切成离线并停止向在线云机派发。本文件钉 hub 这一侧的判据：

  * **P0-1 无包死锁**：自动课「普遍无包」而旧的 claim 路径要求先有包 ⇒ 谁都不会领它。
    新形状：清单对自动课允许无包（`claimable` + `auto_handoff`），claim 遇缺包 = 翻 mode +
    请控制台导包 + 409 指路（不是 404）。
  * **P0-2 一拖一**：U2「TPU 一次只 drain 一门」必须是 **hub 侧不变量**（云机不可信），
    闸在 claim 的临界区；release 后自动解除。
  * **P0-3 / P1-3 pin**：`pinned` 是人的决定，落盘、重启不丢；pin online 的课离线盘永不自取。
  * **§3.8 数据损坏防线**：claim 翻的 offline 落 `offline-dispatch.json`，hub 重启后仍在。
  * **U3 waiting**：租约过期 ⇒ 该课停在 offline（不清零、不回 online）。
  * **U6 completed**：段末摘要报到跑满 ⇒ 不可再领；重导包（sha 变）自动解封。
  * **T4 排序**：按开课时间（`training-enabled.txt` 的 mtime）；读不到 ⇒ `+inf` 排最后。
  * **T8 stalled**：`running` 无进度 / 已翻 offline 无人跑 ⇒ 出告警（自动化固有代价必须显式付）。
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from common.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    OFFLINE_CLAIM_PATH,
    OFFLINE_RELEASE_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_TASKS_PATH,
)
from hub import offline as offline_mod
from hub.queue_offline import (
    auto_claimable,
    dispatch_record_default,
    dispatch_record_merge,
    open_time_key,
    stall_verdict,
)
from hub.server import _HubQueue, make_server

TOKEN = "sekret"


# ------------------------------------------------------------------ 夹具


def _boot(
    tmp_path: Path, now_fn: Callable[[], float] | None = None
) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    hub = _HubQueue({}, discover_root=tmp_path, now_fn=now_fn)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _req(
    base: str, path: str, *, method: str = "GET", token: str = TOKEN
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        base + path, headers={AUTH_HEADER: f"Bearer {token}"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(raw: bytes) -> dict:
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _course(tmp_path: Path, hub: _HubQueue, course: str, *, marker: bool = True) -> None:
    """造一门被 hub 发现的课（目录 + 账本 + 开课标记）。"""
    (tmp_path / course / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / course / "training_log.jsonl").touch()
    if marker:
        (tmp_path / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)


def _pack(tmp_path: Path, course: str, payload: bytes | None = None) -> Path:
    d = tmp_path / course
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"task-{course}.zip"
    p.write_bytes(payload if payload is not None else f"PK-test-{course}".encode())
    return p


def _stub_auto_handoff(monkeypatch, result: tuple[bool, str] = (True, "ok")) -> list[str]:
    calls: list[str] = []

    def fake(course: str, log=None):
        calls.append(course)
        return result

    # patch 面 = `hub.offline`（调用点在离线段面；`hub_server` 只是门面转发名）。
    monkeypatch.setattr(offline_mod, "trigger_auto_handoff", fake)
    return calls


@pytest.fixture(autouse=True)
def _clean_trigger_books():
    from hub.task_pack import reset_auto_handoff_triggers

    reset_auto_handoff_triggers()
    yield
    reset_auto_handoff_triggers()


# ------------------------------------------------------------------ 纯判据


def test_dispatch_record_merge_tolerates_junk_but_pins_the_gate_fields() -> None:
    rec = dispatch_record_merge({"mode": "offline", "pinned": True, "claimed_at": "x"}, "online")
    assert rec["mode"] == "offline" and rec["pinned"] is True and rec["claimed_at"] == 0.0
    bad = dispatch_record_merge({"mode": "bogus", "claimed_offline": "yes"}, "online")
    assert bad["mode"] == "online" and bad["claimed_offline"] is False
    none = dispatch_record_merge(None, "offline")
    assert none == dispatch_record_default("offline")


def test_open_time_key_sentinel_sorts_last_not_first() -> None:
    assert open_time_key(123.0) == 123.0
    assert open_time_key(None) > 1e18  # +inf：升序里排最后（0.0 会排最前）


def test_auto_claimable_table() -> None:
    ok = {"auto": True, "offline": False, "pack_exists": False, "holder_present": False}
    assert auto_claimable(**ok, completed=False, busy=False) is True  # 无包的自动课可领
    assert auto_claimable(**ok, completed=False, busy=True) is False  # 一拖一
    assert auto_claimable(**ok, completed=True, busy=False) is False  # 跑满不重跑
    assert auto_claimable(**ok, completed=False, busy=False) is True
    held = dict(ok, holder_present=True)
    assert auto_claimable(**held, completed=False, busy=False) is False
    # 非自动课（pin 的）：有包才可领，没包永远不可领
    pinned = {"auto": False, "offline": True, "pack_exists": True, "holder_present": False}
    assert auto_claimable(**pinned, completed=False, busy=False) is True
    assert auto_claimable(**dict(pinned, pack_exists=False), completed=False, busy=False) is False


def test_stall_verdict_covers_both_legs() -> None:
    def verdict(
        *,
        holder: bool,
        flipped_at: float,
        now: float,
        last_progress: float = 0.0,
        treat_offline: bool = True,
    ) -> str:
        return stall_verdict(
            treat_offline=treat_offline,
            pinned=False,
            completed=False,
            holder_present=holder,
            last_progress_mtime=last_progress,
            flipped_at=flipped_at,
            threshold=100.0,
            now=now,
        )

    assert verdict(holder=True, flipped_at=0.0, now=1000.0) == "running-stale"
    assert verdict(holder=True, flipped_at=0.0, now=1000.0, last_progress=950.0) == ""
    assert verdict(holder=False, flipped_at=500.0, now=1000.0) == "pending-export"
    assert verdict(holder=False, flipped_at=500.0, now=100.0) == ""
    # 从未交接过的课（没有 flip 锚点、也没有进度）不算停滞：不能平白报警
    assert verdict(holder=False, flipped_at=0.0, now=1e9) == ""
    assert verdict(holder=True, flipped_at=0.0, now=1000.0, treat_offline=False) == ""


# ------------------------------------------------------------------ 清单面


def test_unpinned_online_course_without_pack_is_claimable(tmp_path: Path) -> None:
    """P0-1 的回归锚：无包的在线课（不过是自动候选）在清单里可领。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    rows = hub.offline_tasks()
    row = next(r for r in rows if r["course"] == "c5-gae")
    assert row["state"] == "no_pack"
    assert row["claimable"] is True
    assert row["auto_handoff"] is True
    assert row["pack"] is None


def test_pin_online_hides_course_from_the_offline_disk(tmp_path: Path) -> None:
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    assert hub.pinned_of("c5-gae") is True
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert "c5-gae" not in rows  # 默认清单（云机口径）根本不列它
    rows_all = {r["course"]: r for r in hub.offline_tasks(include_all=True)}
    assert rows_all["c5-gae"]["state"] == "not_offline"
    assert rows_all["c5-gae"]["claimable"] is False


def test_auto_course_without_pack_sorts_last_not_first(tmp_path: Path) -> None:
    """T4 + P1-5：同级时无包课排最后（升序里 `0.0` 会把它顶到最前）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "with-pack")
    _pack(tmp_path, "with-pack")
    _course(tmp_path, hub, "no-pack")
    rows = hub.offline_tasks()
    assert [r["course"] for r in rows] == ["with-pack", "no-pack"]
    # 无包课的状态名次更高（no_pack=3 > ready=0）——同时钉住「不是靠 mtime 碰巧」。
    assert rows[1]["state"] == "no_pack"


def test_sort_key_is_open_time_not_pack_mtime(tmp_path: Path) -> None:
    hub = _HubQueue({}, discover_root=tmp_path)
    for c in ("old-course", "new-course"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    # 包 mtime 故意反向：新开的课包更旧（旧实现按包 mtime 会把它排前）
    os.utime(tmp_path / "new-course" / "task-new-course.zip", (100.0, 100.0))
    os.utime(tmp_path / "old-course" / "task-old-course.zip", (900.0, 900.0))
    os.utime(tmp_path / "old-course" / COURSE_ENABLE_MARKER, (1000.0, 1000.0))
    os.utime(tmp_path / "new-course" / COURSE_ENABLE_MARKER, (5000.0, 5000.0))
    rows = [r["course"] for r in hub.offline_tasks()]
    assert rows == ["old-course", "new-course"]


def test_missing_open_marker_sorts_with_infinity(tmp_path: Path) -> None:
    """停课残留（offline 模式、标记已删）不该顶掉有开课时间的课。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "residue")
    _course(tmp_path, hub, "live")
    for c in ("residue", "live"):
        _pack(tmp_path, c)
        hub.set_mode_pinned(c, "offline", True)
    # 标记在发现之后删掉（真实场景：控制台停课删标记，而 hub 的表在下次扫描前还记着它）
    os.remove(tmp_path / "residue" / COURSE_ENABLE_MARKER)
    os.utime(tmp_path / "live" / COURSE_ENABLE_MARKER, (100.0, 100.0))
    rows = [r["course"] for r in hub.offline_tasks()]
    assert rows == ["live", "residue"]
    assert hub.open_time_of("residue") > 1e18


# ------------------------------------------------------------------ claim 面


def test_claim_without_pack_flips_mode_and_triggers_export(
    tmp_path: Path, monkeypatch
) -> None:
    """P0-1：claim 建模入口 —— 缺包不再是 404，而是翻 mode + 触发导包 + 409 指路。"""
    calls = _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409, body
    assert body["auto_handoff"] is True and body["pending_export"] is True
    assert body["triggered"] is True and body["give_up"] is False
    assert calls == ["c5-gae"]
    assert hub.mode_of("c5-gae") == "offline"
    rec = hub.dispatch_record("c5-gae")
    assert rec["claimed_offline"] is True and rec["mode"] == "offline"
    assert (tmp_path / "c5-gae" / "offline-dispatch.json").is_file()
    # 再问一次：节流（不重复打扰控制台）
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st2 == 409 and _json(raw2)["triggered"] is False
    assert calls == ["c5-gae"]


def test_claim_without_pack_degrades_when_console_unreachable(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_auto_handoff(monkeypatch, (False, "OSError"))
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["pending_export"] is True
    assert "控制台" in body["trigger_note"], body
    # 半状态可见：已翻 offline（本机停采）但没人跑 ⇒ 由 stalled 告警兜（另一条用例）


def test_claim_missing_pack_for_non_auto_course_keeps_404(tmp_path: Path) -> None:
    """人管（pin）的课缺包仍是旧 404：不替人决定导包。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    hub.set_mode_pinned("c5-gae", "offline", True)
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 404, body
    assert body["known_courses"] == ["c5-gae"]


def test_pin_online_course_is_rejected_by_claim_even_with_pack(tmp_path: Path) -> None:
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    hub.set_mode_pinned("c5-gae", "online", True)
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["not_offline"] is True, body


def test_claim_with_pack_flips_mode_and_persists(tmp_path: Path) -> None:
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert hub.mode_of("c5-gae") != "offline"
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    assert hub.mode_of("c5-gae") == "offline"
    disk = json.loads(
        (tmp_path / "c5-gae" / "offline-dispatch.json").read_text(encoding="utf-8")
    )
    assert disk["mode"] == "offline" and disk["claimed_offline"] is True
    assert disk["claimed_by"] == "w1"


def test_restart_restores_dispatch_state(tmp_path: Path) -> None:
    """§3.8 数据损坏防线：重启后正在 TPU 上跑的课**仍是 offline**（不被启动参数解封）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    # 模拟重启：同一目录、启动参数说 online
    hub2 = _HubQueue(
        {"c5-gae": hub._stores["c5-gae"]}, order=["c5-gae"], modes={"c5-gae": "online"}
    )
    assert hub2.mode_of("c5-gae") == "offline"
    assert hub2.pinned_of("c5-gae") is False
    assert hub2.dispatch_record("c5-gae")["claimed_offline"] is True


def test_pin_survives_restart(tmp_path: Path) -> None:
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    hub2 = _HubQueue({}, discover_root=tmp_path)
    hub2.discover(force=True)
    assert hub2.pinned_of("c5-gae") is True
    assert hub2.mode_of("c5-gae") == "online"


def test_busy_gate_blocks_second_auto_claim_and_releases(tmp_path: Path) -> None:
    """U2 一拖一：全局任一时刻至多一门 running；release 后闸自动解除。"""
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")
    assert st == 200, raw[:200]
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c-b"]["claimable"] is False
    assert str(rows["c-b"]["reason"]).startswith("busy:")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w1", method="POST")
    body2 = _json(raw2)
    assert st2 == 409 and body2["busy"] is True, body2
    # release c-a ⇒ c-b 可领
    token = _json(raw)["lease"]["token"]
    rel = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c-a&lease={token}", method="POST")
    assert rel[0] == 200, rel[1][:200]
    st3, raw3 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w1", method="POST")
    assert st3 == 200, raw3[:200]


def test_pending_export_window_also_blocks_second_course(tmp_path: Path, monkeypatch) -> None:
    """导包窗口（已翻 offline、包还没出现）也算 busy —— 否则两台云机同时翻开两门课。"""
    _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")[0] == 409
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c-b"]["claimable"] is False
    assert "交接" in str(rows["c-b"]["reason"])
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w1", method="POST")
    assert st == 409 and _json(raw)["busy"] is True


def test_waiting_course_stays_offline_after_lease_expiry(tmp_path: Path) -> None:
    """U3：掉线后一直等待（mode 保持 offline，不回 online），直到人切回。"""
    clock = [0.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    clock[0] += 1000.0  # 租约 TTL=900 ⇒ 过期
    assert hub.offline_lease("c5-gae") is None
    assert hub.mode_of("c5-gae") == "offline"
    assert hub.dispatch_record("c5-gae")["claimed_offline"] is True
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c5-gae"]["state"] == "ready"  # 可被重连续领（waiting 的出口之一）
    assert rows["c5-gae"]["claimable"] is True


def test_completed_pack_cannot_be_reclaimed_until_re_export(tmp_path: Path) -> None:
    """U6 + 二轮 P1-1：跑满锚在**包 sha** 上；重导包（sha 变）自动解封。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae", b"PK-old")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    hub.note_offline_completed("c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["completed"] is True, body
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c5-gae"]["state"] == "completed" and rows["c5-gae"]["claimable"] is False
    # 重导：新 sha ⇒ 自动解封
    from hub.task_pack import _file_sha256

    _pack(tmp_path, "c5-gae", b"PK-new")
    assert hub.completion_blocked(
        "c5-gae", _file_sha256(tmp_path / "c5-gae" / "task-c5-gae.zip")
    ) is False


def test_stalled_alert_covers_pending_export_window(tmp_path: Path, monkeypatch) -> None:
    """T8：已翻 offline、没人跑 ⇒ 告警（不能靠「人总会看到」）。"""
    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]  # 不用 0：`flipped_at=0` 与「没有锚点」不可区分（生产时钟恒为墙钟）
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 409
    assert hub.offline_stalled() == []  # 刚开始交接：不算停
    clock[0] += 2000.0  # 超过缺省阈值 1800s
    stalled = hub.offline_stalled()
    assert [s["course"] for s in stalled] == ["c5-gae"]
    assert stalled[0]["why"] == "pending-export"
    st, raw = _req(base, "/admin/offline")
    assert st == 200 and _json(raw)["stalled"][0]["course"] == "c5-gae"


def _post_json(base: str, path: str, body: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={AUTH_HEADER: f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_result_end_it_reached_marks_completed_and_is_served(tmp_path: Path) -> None:
    """T6：段末摘要自报跑满 ⇒ `/admin/offline.results` 按 run_id 带出 ∧ 该包记 completed。

    三件事一起钉（缺任何一件 T6 的链就断在这）：
      ① `end_it_reached` 进白名单（rec + 应答）；② 真的联动 `note_offline_completed`
      （此前它只有测试直接调 = 死代码）；③ 读面能按 run_id 被控制台取走。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae", b"PK-done")
    st, raw = _post_json(
        base,
        OFFLINE_RESULT_PATH,
        {
            "course": "c5-gae",
            "run_id": "seg-1",
            "it_end": 110,
            "state": "complete",
            "end_it_reached": True,
        },
    )
    assert st == 200 and _json(raw)["end_it_reached"] is True, raw[:200]
    st2, raw2 = _req(base, "/admin/offline")
    rec = _json(raw2)["results"]["c5-gae"]["seg-1"]
    assert rec["end_it_reached"] is True and rec["it_end"] == 110 and rec["state"] == "complete"
    # 生产链：completed ⇒ 不可再领（U6；claim 409 + completed 标记）
    from hub.task_pack import _file_sha256

    sha = _file_sha256(tmp_path / "c5-gae" / "task-c5-gae.zip")
    assert hub.completion_blocked("c5-gae", sha) is True
    st3, raw3 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st3 == 409 and _json(raw3)["completed"] is True, raw3[:200]


def test_result_without_end_flag_does_not_mark_completed(tmp_path: Path) -> None:
    """半段摘要（没有 `end_it_reached`）不得把包封成 completed（T6 负向）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae", b"PK-mid")
    st, raw = _post_json(
        base,
        OFFLINE_RESULT_PATH,
        {"course": "c5-gae", "run_id": "seg-mid", "it_end": 40, "state": "budget"},
    )
    assert st == 200 and _json(raw)["end_it_reached"] is False, raw[:200]
    st2, raw2 = _req(base, "/admin/offline")
    assert _json(raw2)["results"]["c5-gae"]["seg-mid"]["end_it_reached"] is False
    # 未跑满 ⇒ 照旧可领（回归锚：别把每一次段末摘要都封包）
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200


def test_admin_courses_get_reports_pin_and_claim_state(tmp_path: Path) -> None:
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    st, raw = _req(base, "/admin/courses")
    body = _json(raw)
    assert st == 200
    row = body["courses"][0]
    assert row["claimed_offline"] is True and row["pinned"] is False
    # pin=0（人的动作）可把 claim 翻的 offline 切回在线
    st2, raw2 = _req(
        base, "/admin/courses?course=c5-gae&mode=online&pin=0", method="POST"
    )
    assert st2 == 200, raw2[:200]
    assert hub.mode_of("c5-gae") == "online"
    assert hub.pinned_of("c5-gae") is False
    # 不带 pin 的 legacy 写入在 claim-offline 之后被拒
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    assert hub.dispatch_record("c5-gae")["claimed_offline"] is True
    st3, raw3 = _req(base, "/admin/courses?course=c5-gae&mode=online", method="POST")
    assert st3 == 400 and "pin" in _json(raw3)["why"]
