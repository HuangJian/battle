"""自动离线交接（plan/auto-offline-handoff，2026-10-03）。

治什么：开课时不再指定离线/在线 —— **当且仅当**一块离线盘真的领走某门课时，hub 才把那门课
切成离线并停止向在线云机派发。本文件钉 hub 这一侧的判据：

  * **P0-1 无包死锁**：自动课「普遍无包」而旧的 claim 路径要求先有包 ⇒ 谁都不会领它。
    新形状：清单对自动课允许无包（`claimable` + `auto_handoff`），claim 遇缺包 = 翻 mode +
    请控制台导包 + 409 指路（不是 404）。
  * **P0-2 一拖一**：U2「TPU 一次只 drain 一门」必须是 **hub 侧不变量**（云机不可信），
    闸在 claim 的临界区；release 后自动解除。
  * **P0-3 / P1-3 pin（2026-10-03 被用户裁决取代）**：`pinned` 仍是人的决定（落盘、重启不丢），
    但**不再拦离线盘** —— 用户口径：「不管什么时候上线接活，优先取当时就绪的离线课程；
    没有离线就抢第一个在训在线课」。唯一 opt-out = 停课（删 `training-enabled.txt`）；
    清单行新增 `seize`（在线在训、可被抢）/`open_time`（抢的顺序键）。
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
    OFFLINE_HEARTBEAT_PATH,
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
from hub.task_pack import (
    AUTHORITY_AUTO,
    AUTHORITY_NOT_OFFLINE,
    AUTHORITY_PINNED_OFFLINE,
    AUTHORITY_PINNED_ONLINE,
    AUTHORITY_STOPPED,
    lease_verdict,
)

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
def _clean_trigger_books(monkeypatch):
    """每个用例都从「控制台可达 + 触发账本干净」起步。

    ★ 2026-10-04：**默认把控制台触发打桩**——有包那条腿（claim 成功）现在也会请控制台核对
    包的新鲜度（`hub/offline._ask_console_freshness`），真发 HTTP 会打到开发机上正在跑的
    控制台（:8900，会给这里的假课程写真配置）。需要自定义结果的用例自己再调
    `_stub_auto_handoff(monkeypatch, ...)`——后打的桩生效（同一个 MonkeyPatch 实例）。
    """
    from hub.task_pack import reset_auto_handoff_triggers

    _stub_auto_handoff(monkeypatch)
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
        authority: str = AUTHORITY_AUTO,
    ) -> str:
        return stall_verdict(
            treat_offline=treat_offline,
            authority=authority,
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
    # ★六轮 P0-10（R3-e）：静音只给 pinned_online（人固定在线，不是离线候选）；
    # pinned_offline 的云机停机照常告警（报障二里最该响的那一声）。
    assert verdict(
        holder=True, flipped_at=0.0, now=1000.0, authority=AUTHORITY_PINNED_ONLINE
    ) == ""
    assert verdict(
        holder=True, flipped_at=0.0, now=1000.0, authority=AUTHORITY_PINNED_OFFLINE
    ) == "running-stale"
    assert verdict(
        holder=False, flipped_at=500.0, now=1000.0, authority=AUTHORITY_PINNED_OFFLINE
    ) == "pending-export"


# ------------------------------------------------------------------ 清单面


def test_unpinned_online_course_without_pack_is_claimable(tmp_path: Path) -> None:
    """P0-1 的回归锚：无包的在线课（自动候选）在清单里可领、可被抢。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    rows = hub.offline_tasks()
    row = next(r for r in rows if r["course"] == "c5-gae")
    assert row["state"] == "no_pack"
    assert row["claimable"] is True
    assert row["auto_handoff"] is True
    assert row["seize"] is True
    assert row["pack"] is None


def test_pin_online_course_is_listed_but_locked(tmp_path: Path) -> None:
    """★ 2026-10-05（六轮 F6 / §4.2 半回摆）：pin online 的课**行照发**（云机空队列自解释），
    但 `seize=false ∧ claimable=false ∧ auto_handoff=false`；claim 走 409 `pinned_online`。
    交还自动（pin=0）后同一门课回到 auto 池（seize 恢复）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    assert hub.pinned_of("c5-gae") is True
    row = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row["authority"] == AUTHORITY_PINNED_ONLINE
    assert row["seize"] is False
    assert row["claimable"] is False and row["auto_handoff"] is False
    assert str(row["reason"]).startswith("pinned:")
    assert row["open_time"] == hub.open_time_of("c5-gae")
    assert row["open_time"] < 1e18  # 开课标记在 ⇒ 真实 mtime（不是 +inf 哨兵）
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["pinned_online"] is True, body
    assert hub.mode_of("c5-gae") == "online"
    # 交还自动（pin=0）⇒ 回 auto 池
    assert hub.set_mode_pinned("c5-gae", "online", False)[0] is True
    assert hub.pinned_of("c5-gae") is False
    row2 = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row2["authority"] == AUTHORITY_AUTO
    assert row2["seize"] is True and row2["claimable"] is True and row2["auto_handoff"] is True


def test_stopped_online_course_is_hidden_from_the_offline_disk(tmp_path: Path) -> None:
    """唯一 opt-out = 停课（删 `training-enabled.txt`）：默认清单不列，`?include=all` 给
    `not_offline`，claim 409 `not_offline`。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)
    assert {r["course"] for r in hub.offline_tasks()} == set()
    rows_all = {r["course"]: r for r in hub.offline_tasks(include_all=True)}
    assert rows_all["c5-gae"]["state"] == "not_offline"
    assert rows_all["c5-gae"]["claimable"] is False
    assert rows_all["c5-gae"]["seize"] is False
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["not_offline"] is True, body
    assert "不在训练中" in body["error"], body


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


def test_stopped_residue_is_excluded_from_default_list(tmp_path: Path) -> None:
    """★六轮 F3（行为变更）：停课残留（盘上还有 `mode=offline` 记录、标记已删）默认清单
    **不列**——「唯一 opt-out = 停课」不再被 `mode=offline` 记录绕过；`?include=all` 里可见。"""
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
    assert rows == ["live"]
    assert hub.open_time_of("residue") > 1e18
    all_rows = {r["course"]: r for r in hub.offline_tasks(include_all=True)}
    assert all_rows["residue"]["state"] == "not_offline"
    assert all_rows["residue"]["claimable"] is False
    assert all_rows["residue"]["authority"] == AUTHORITY_STOPPED


def test_seize_flag_matrix(tmp_path: Path) -> None:
    """`seize` 的边界：在线在训 ⇒ True；跑满的在线课 ⇒ False（completed 不可抢，二轮 P1-1
    的同一理由）；已离线 ⇒ False（`seize` 只标「在线在训」）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "online-live")
    _course(tmp_path, hub, "online-done")
    _course(tmp_path, hub, "offline-live")
    _pack(tmp_path, "online-done", b"PK-done")
    _pack(tmp_path, "offline-live")
    hub.note_offline_completed("online-done")
    hub.set_mode_pinned("offline-live", "offline", True)
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["online-live"]["seize"] is True
    assert rows["online-done"]["state"] == "completed"
    assert rows["online-done"]["seize"] is False
    assert rows["offline-live"]["seize"] is False


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


def test_stopped_course_with_offline_record_and_pack_is_rejected(tmp_path: Path) -> None:
    """★六轮 F3（行为变更，修洞）：停课后 `mode=offline`（生产 `stopCourse` 推的形状）
    **有包**也不再可领——旧判据（`mode != offline` 才拒）对它无效，云机今天能领走并整段跑。
    缺包同样是 409 `not_offline`（不是旧 404：停课不是「等人导包」）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    hub.set_mode_pinned("c5-gae", "offline", True)
    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)
    assert [r["course"] for r in hub.offline_tasks()] == []
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["not_offline"] is True, body
    # 缺包也走同一条 409：不替停掉的课触发导包
    os.remove(tmp_path / "c5-gae" / "task-c5-gae.zip")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body2 = _json(raw2)
    assert st2 == 409 and body2["not_offline"] is True, body2


def test_pin_online_course_claim_is_rejected_then_unset_reopens(tmp_path: Path) -> None:
    """★ 2026-10-05（六轮 F6 / §4.2 半回摆）：pin online 的课 claim 一律 409 `pinned_online`
    （旧题眼是「照样被抢并翻 offline」——本条推翻它）；点「交还自动」后同一张包立刻可领。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    hub.set_mode_pinned("c5-gae", "online", True)
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["pinned_online"] is True, body
    assert hub.mode_of("c5-gae") == "online"
    assert hub.dispatch_record("c5-gae")["claimed_offline"] is False
    # 交还自动（pin=0）⇒ 同一份包可领，且 hub 当场翻 offline（T2 的正例）
    assert hub.set_mode_pinned("c5-gae", "online", False)[0] is True
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st2 == 200 and _json(raw2).get("lease"), raw2[:200]
    assert hub.mode_of("c5-gae") == "offline"
    assert hub.dispatch_record("c5-gae")["claimed_offline"] is True


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


def test_claim_with_pack_asks_console_for_freshness(tmp_path: Path, monkeypatch) -> None:
    """★ 2026-10-04 用户报障的回归锚（抢占在训在线课 ⇒ 在线权重/动量白丢？）。

    有包那条腿（`note_claim` 翻模式）过去**不问控制台** ⇒ 盘上的旧包被直接取走；而离线腿的
    续跑锚点只认回传/导入的轮次（`queue_resume.resume_sources`），**看不见本机在线轮**。
    现在 claim 成功也要请控制台按「包比权重/源码新?」判一次（旧包由控制台**同步**作废 +
    重导 ⇒ 云机随后取包拿到的一定是新包）。
    """
    calls = _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    body = _json(raw)
    assert calls == ["c5-gae"], "有包的抢占也要问一次控制台（不然旧包直接开跑）"
    assert "新鲜度" in body.get("handoff", ""), body
    # 非自动课（pinned_offline）：不替人决定，不问控制台
    calls.clear()
    # 先交还 c5-gae（一拖一：全局至多一门在跑，busy 闸对 pinned_offline 同样生效）
    token = body["lease"]["token"]
    rel = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease={token}", method="POST")
    assert rel[0] == 200, rel[1][:200]
    _course(tmp_path, hub, "c-pinned")
    _pack(tmp_path, "c-pinned")
    hub.set_mode_pinned("c-pinned", "offline", True)
    st2, _raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-pinned&worker=w1", method="POST")
    assert st2 == 200, "pinned 离线课有包可领"
    assert calls == [], "非自动课不问控制台新鲜度"


def test_claim_with_pack_degrades_when_console_unreachable(tmp_path: Path, monkeypatch) -> None:
    """控制台不可达：claim 照常成功，只在回执里说清（不制造新的失败态）。"""
    _stub_auto_handoff(monkeypatch, (False, "OSError"))
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    assert "控制台不可达" in _json(raw).get("handoff", ""), raw[:300]


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


# ------------------------------------------------------------------ T0 撤单（plan/switch-mode-drops-jobs）


def _publish_job(hub: _HubQueue, course: str, jid: str, *, it: int = 3) -> None:
    """往某课 store 发一份未结算 job（payload 落盘即进可领池）。role 显式给 online。"""
    hub._stores[course].publish(
        jid, {"job_id": jid, "it": it, "role": "online"}, b"PK\x03\x04fake"
    )


def _cancelled_ids(hub: _HubQueue, course: str) -> set[str]:
    return {
        str(e.get("job_id"))
        for e in hub._stores[course]._read_ledger()
        if e.get("event") == "job_cancelled"
    }


def test_drop_jobs_cancels_unclaimed_and_keeps_inflight(tmp_path: Path) -> None:
    """T0：`&drop_jobs=1` 作废**未认领**的 job；在飞的（有活租约）不动。

    用户 2026-10-03 裁决：「不管在算的，只管没领的」——撤销在飞 job 属于轮边界强杀，不做。
    """
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "free-1")
        _publish_job(hub, "c5-gae", "free-2")
        _publish_job(hub, "c5-gae", "inflight")
        st = hub._stores["c5-gae"]
        assert st.claim("inflight", worker_id="w1") is not None  # 在飞：有人承诺在跑
        assert set(st.claimable_job_ids()) == {"free-1", "free-2"}

        code, raw = _req(
            base, "/admin/courses?course=c5-gae&mode=offline&pin=1&drop_jobs=1", method="POST"
        )
        assert code == 200, raw[:200]
        assert hub.mode_of("c5-gae") == "offline"
        assert _cancelled_ids(hub, "c5-gae") == {"free-1", "free-2"}
        assert st.claimable_job_ids() == []
        # 在飞的仍持有租约（撤单只管没领的）——这是本用例的**反面断言**。
        # 注：`lease_expires_in` 住 `_HubQueue`（经 `_store_of` 委派），**不是** `_JobStore`
        # 的方法；这里直读 `_leases`（本仓测试读 store 私有属性的既有先例，见 `hub/store.py` 头部）。
        assert "inflight" in st._leases

        # 幂等：再撤一次不重复写账本
        assert (
            _req(
                base, "/admin/courses?course=c5-gae&mode=offline&pin=1&drop_jobs=1", method="POST"
            )[0]
            == 200
        )
        assert _cancelled_ids(hub, "c5-gae") == {"free-1", "free-2"}
    finally:
        srv.shutdown()


def test_mode_switch_without_drop_jobs_leaves_queue_intact(tmp_path: Path) -> None:
    """开课/停课/回灌走的 `pushCourseMode` 不带 `drop_jobs` ⇒ 队列一字不动（既有契约）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "keep-1")
        code, raw = _req(base, "/admin/courses?course=c5-gae&mode=offline&pin=1", method="POST")
        assert code == 200, raw[:200]
        assert _cancelled_ids(hub, "c5-gae") == set()
        assert hub._stores["c5-gae"].claimable_job_ids() == ["keep-1"]
    finally:
        srv.shutdown()


def test_auto_claim_offline_drops_unclaimed_jobs(tmp_path: Path) -> None:
    """claim 自动翻 offline 也撤（裁决 Q2：与人的开关共用同一条切模式链，不靠人记得带参数）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "stale-1")
        grant, why = hub.claim_offline("c5-gae", "tpu-1")
        assert why == "" and grant, why
        assert hub.mode_of("c5-gae") == "offline"
        assert _cancelled_ids(hub, "c5-gae") == {"stale-1"}
        assert hub._stores["c5-gae"].claimable_job_ids() == []
    finally:
        srv.shutdown()


def test_seize_claim_flips_auto_course_and_drops_unclaimed_jobs(tmp_path: Path) -> None:
    """T0 同链：**auto**（pin=0）的在训在线课被 claim 翻成 offline，并撤掉未认领的在线 job；
    ★六轮 §4.2 半回摆：pin online 的课不再走这条腿（claim 409 `pinned_online`，队列一字不动）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "stale-auto")
        _pack(tmp_path, "c5-gae")
        st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=tpu-1", method="POST")
        assert st == 200, raw[:200]
        assert hub.mode_of("c5-gae") == "offline"
        assert _cancelled_ids(hub, "c5-gae") == {"stale-auto"}
        assert hub._stores["c5-gae"].claimable_job_ids() == []
        # pin online 的另一门课：claim 被拒，**不撤单**
        _course(tmp_path, hub, "c-hidden")
        _publish_job(hub, "c-hidden", "still-here")
        _pack(tmp_path, "c-hidden")
        hub.set_mode_pinned("c-hidden", "online", True)
        st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-hidden&worker=tpu-1", method="POST")
        assert st2 == 409, raw2[:200]
        assert _cancelled_ids(hub, "c-hidden") == set()
        assert hub._stores["c-hidden"].claimable_job_ids() == ["still-here"]
    finally:
        srv.shutdown()


def test_cancelled_job_is_rejected_even_with_stale_peek(tmp_path: Path) -> None:
    """作废有两道：账本（真闸，重启后仍作废）+ 进程内 set（挡「拿作废前 peek 的 jid 硬领」）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "gone-1")
        assert (
            _req(
                base, "/admin/courses?course=c5-gae&mode=offline&pin=1&drop_jobs=1", method="POST"
            )[0]
            == 200
        )
        # 切回在线（parked 解除）——证明下面拒的是「作废」这条闸，不是停摆/归属闸
        assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
        st = hub._stores["c5-gae"]
        assert st.claimable_job_ids() == []  # 账本真闸：池子里没有它了
        assert st.claim("gone-1", worker_id="w1") is None  # 即时闸：硬领也被拒
        assert "gone-1" not in st._leases, "被拒的认领不该留下租约"
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ 六轮评审（2026-10-05）


def test_lease_verdict_six_states() -> None:
    """P0-1 表驱动：expired→revoked→mine→stale→foreign（顺序即语义）。"""
    now = 1000.0
    live = {
        "worker_id": "w1",
        "at": now - 10.0,
        "expires_at": now + 500.0,
        "beat_at": now - 10.0,
        "revoked": False,
    }
    assert lease_verdict(now, None, "w1") == "free"
    assert lease_verdict(now, dict(live), "w1") == "mine"
    assert lease_verdict(now, dict(live), "w2") == "foreign"
    assert lease_verdict(now, {"worker_id": "w1", "expires_at": now - 1.0}, "w1") == "expired"
    tomb = dict(live, revoked=True)
    # 墓碑是全局否决、不看身份：老主与新主都判 revoked（且排在 mine 之前）
    assert lease_verdict(now, tomb, "w1") == "revoked"
    assert lease_verdict(now, dict(tomb), "w2") == "revoked"
    silent = dict(live, worker_id="w1", at=now - 900.0, beat_at=now - 181.0)
    assert lease_verdict(now, silent, "w2") == "stale"
    # 自己静默后回来判 mine（不被自己 409）——顺序里 mine 在 stale 之前
    assert lease_verdict(now, silent, "w1") == "mine"
    # 旧记录无 beat_at ⇒ 用 at（语义不变）
    old = {"worker_id": "w1", "at": now - 900.0, "expires_at": now + 500.0}
    assert lease_verdict(now, old, "w2") == "stale"


def test_auto_claimable_stale_and_revoked_are_claimable() -> None:
    """P0-A：主失联（stale）或已成墓碑（revoked）⇒ 不算挡领；旧调用（不带新形参）逐字不变。"""
    base = {"auto": True, "offline": False, "pack_exists": True, "holder_present": True}
    assert auto_claimable(**base, completed=False, busy=False) is False
    assert auto_claimable(**base, completed=False, busy=False, holder_stale=True) is True
    assert auto_claimable(**base, completed=False, busy=False, holder_revoked=True) is True
    assert (
        auto_claimable(
            auto=True, offline=False, pack_exists=False, holder_present=False,
            completed=False, busy=False,
        )
        is True
    )


def test_queue_state_and_admin_offline_expose_authority(tmp_path: Path) -> None:
    """P0-11：`/admin/queue` 每课行带 authority+pinned；`/admin/offline` 租约带
    stale/revoked；墓碑保留在 holders（F5）且交还自动后清单行可渲染 held-revoked。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    q = _json(_req(base, "/admin/queue")[1])
    row = q["courses"]["c5-gae"]
    assert row["authority"] == AUTHORITY_AUTO and row["pinned"] is False
    leases = _json(_req(base, "/admin/offline")[1])["leases"]
    assert leases["c5-gae"]["worker_id"] == "w1"
    assert leases["c5-gae"]["revoked"] is False and leases["c5-gae"]["silent_sec"] == 0.0
    # 人切固定在线：authority 立刻变；清单行 pinned 且不可领
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    q2 = _json(_req(base, "/admin/queue")[1])
    assert q2["courses"]["c5-gae"]["authority"] == AUTHORITY_PINNED_ONLINE
    assert q2["courses"]["c5-gae"]["pinned"] is True
    row_pin = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row_pin["claimable"] is False and str(row_pin["reason"]).startswith("pinned:")
    # 墓碑保留在 /admin/offline（不静默删）——带 revoked=true 与字段
    leases2 = _json(_req(base, "/admin/offline")[1])["leases"]
    assert leases2["c5-gae"]["revoked"] is True
    # 交还自动（pin=0）：authority 回 auto；清单行可渲染 held-revoked ∧ 新主可直接接管
    assert hub.set_mode_pinned("c5-gae", "online", False)[0] is True
    row2 = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row2["holder"] is not None and row2["holder"]["revoked"] is True
    assert str(row2["reason"]).startswith("held-revoked")
    assert row2["claimable"] is True


def test_switching_online_clears_all_four_handoff_fields(tmp_path: Path) -> None:
    """P1-F：切在线/交还自动后 `claimed_offline/by/at/flipped_at` 全归零，租约成墓碑；
    不再产生 `pending-export` 假停滞。"""
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")[0] == 200
    rec = hub.dispatch_record("c5-gae")
    assert rec["claimed_offline"] is True and rec["claimed_by"] == "w1"
    assert rec["claimed_at"] > 0 and rec["flipped_at"] > 0
    assert hub.set_mode_pinned("c5-gae", "online", True)[0] is True
    rec2 = hub.dispatch_record("c5-gae")
    assert rec2["claimed_offline"] is False
    assert rec2["claimed_by"] == "" and rec2["claimed_at"] == 0.0 and rec2["flipped_at"] == 0.0
    lease = hub.offline_lease("c5-gae")
    assert lease is not None and lease["revoked"] is True
    clock[0] += 10_000.0  # 旧 flipped_at 若还在，这里会假报 pending-export
    assert hub.offline_stalled() == []


def test_stale_lease_is_reclaimed_by_new_worker(tmp_path: Path) -> None:
    """T3（hub 侧）：静默超阈 ⇒ 清单 stale+claimable、新主 claim 200（带 reclaimed）、
    旧主心跳 409 `taken`；同一 worker 回来仍判 mine。"""
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    tok1 = _json(raw)["lease"]["token"]
    clock[0] += 200.0  # 静默超阈（180s）但未过 TTL
    row = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row["claimable"] is True and row["holder"]["stale"] is True
    assert str(row["reason"]).startswith("held-stale")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w2", method="POST")
    body2 = _json(raw2)
    assert st2 == 200 and body2["lease"].get("reclaimed") is True, body2
    assert body2["lease"]["reclaimed_from"] == "w1"
    assert body2["lease"]["worker_id"] == "w2"
    # 旧主心跳：409 taken（token 不符）
    st3, raw3 = _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok1}", method="POST")
    assert st3 == 409, raw3[:200]
    # 新主自己再来（同 id）⇒ mine 续上，不被自己的静默 409（顺序：mine 在 stale 之前）
    clock[0] += 200.0
    st4, raw4 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w2", method="POST")
    assert st4 == 200 and _json(raw4)["lease"].get("reclaimed") is not True, raw4[:200]


def test_busy_gate_blocks_pinned_offline_claim(tmp_path: Path) -> None:
    """★六轮 F2：A 在跑 X 时，B 领 **pinned_offline** 的 Y 也要吃 409 `busy`
    （旧写法 `if auto:` 会对着 pin 离线课静默放行）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        for c in ("c-a", "c-y"):
            _course(tmp_path, hub, c)
            _pack(tmp_path, c)
        hub.set_mode_pinned("c-y", "offline", True)
        st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")
        assert st == 200, raw[:200]
        rows = {r["course"]: r for r in hub.offline_tasks()}
        assert rows["c-y"]["claimable"] is False
        assert str(rows["c-y"]["reason"]).startswith("busy:")
        st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-y&worker=w2", method="POST")
        assert st2 == 409 and _json(raw2)["busy"] is True, raw2[:300]
        token = _json(raw)["lease"]["token"]
        assert _req(base, f"{OFFLINE_RELEASE_PATH}?course=c-a&lease={token}", method="POST")[0] == 200
        st3, raw3 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-y&worker=w2", method="POST")
        assert st3 == 200, raw3[:200]
    finally:
        srv.shutdown()


def test_busy_gate_ignores_stale_lease(tmp_path: Path) -> None:
    """§3.4：死盘（静默超阈）不占「一拖一」闸——新盘能领别的课。"""
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")[0] == 200
    clock[0] += 200.0  # c-a 静默超阈
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w2", method="POST")
    assert st == 200, raw[:200]


def test_handoff_trigger_budget_resets_on_new_round(tmp_path: Path, monkeypatch) -> None:
    """P0-B + F4：导包触发账本烧满 3 次后——换主 ⇒ 重置；**同主重开会话（超窗）** 也重置
    （`.worker-id` 持久，同机重开沿用同一 id，只判换主会漏这一档）。"""
    from hub import task_pack as tp

    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    hub.begin_auto_handoff("c5-gae", "w1")
    for _ in range(3):
        tp.note_auto_handoff_trigger("c5-gae", now=clock[0])
        clock[0] += 700.0
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "give_up"
    hub.begin_auto_handoff("c5-gae", "w2")  # 换主 ⇒ 重置
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "trigger"
    for _ in range(3):
        tp.note_auto_handoff_trigger("c5-gae", now=clock[0])
        clock[0] += 700.0
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "give_up"
    clock[0] += 1000.0  # 距上次 claim 超 AUTO_HANDOFF_PENDING_SEC=900
    hub.begin_auto_handoff("c5-gae", "w2")  # 同主、超窗 ⇒ 仍是「新一轮」
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "trigger"


def test_handoff_window_anchor_is_claimed_at(tmp_path: Path, monkeypatch) -> None:
    """§3.4/§2.4 R2-d：busy 窗口锚 `claimed_at`（每次 pending_export claim 刷新）；
    无人重试超窗后不再占闸（`flipped_at` 继续服务停滞告警——两个锚点分道）。"""
    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c-a")
    _course(tmp_path, hub, "c-b")
    _pack(tmp_path, "c-b")
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")[0] == 409
    clock[0] += 2000.0  # 超过窗口（900s）；新主重试刷新窗口
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w2", method="POST")[0] == 409
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w2", method="POST")
    assert st == 409 and _json(raw)["busy"] is True, raw[:300]
    clock[0] += 1000.0  # 再超窗、无人重试 ⇒ 不占闸
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w2", method="POST")
    assert st2 == 200, raw2[:200]


def test_republish_after_cancel_revives_job(tmp_path: Path) -> None:
    """六轮 F1：生产 republish = 磁盘 IPC 写一条**晚于** `job_cancelled` 的 `job_pending`
    （`remote/hub_client.publish_job` 无条件追加，`hub_client.py:1058-1075`）。hub 的即时闸
    （`_cancelled`）必须按账本净态对账放行——否则「peek 可见 ∧ claim 永远 cancelled」。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "rev-1")
        st = hub._stores["c5-gae"]
        assert st.cancel_unsettled_jobs(reason="test") == ["rev-1"]
        assert st.claim("rev-1", worker_id="w1") is None  # 未复活：净态仍是撤单
        # 逐字复刻生产写者的那一行（同一 jsonl、同一事件形状）
        with open(st.jsonl_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event": "job_pending", "job_id": "rev-1", "runId": "r1", "it": 3, "ts": 1.0}
                )
                + "\n"
            )
        assert "rev-1" in st.claimable_job_ids()  # 池可见
        token = st.claim("rev-1", worker_id="w1")  # 即时闸对账 ⇒ 放行
        assert token is not None
        assert "rev-1" not in st._cancelled
    finally:
        srv.shutdown()


def test_switch_to_online_does_not_drop_unclaimed_jobs(tmp_path: Path) -> None:
    """P0-6②：`drop_jobs` 仅 `m == offline` 生效——切回在线时撤单反成 bug
    （停在队首的活正是回来要领的；§4.3 半球修正）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "keep-1")
        assert (
            _req(base, "/admin/courses?course=c5-gae&mode=offline&pin=1&drop_jobs=1", method="POST")[0]
            == 200
        )
        _publish_job(hub, "c5-gae", "keep-2")
        assert (
            _req(base, "/admin/courses?course=c5-gae&mode=online&pin=1&drop_jobs=1", method="POST")[0]
            == 200
        )
        assert hub.mode_of("c5-gae") == "online"
        assert _cancelled_ids(hub, "c5-gae") == {"keep-1"}  # keep-2 未被撤
        assert hub._stores["c5-gae"].claimable_job_ids() == ["keep-2"]
    finally:
        srv.shutdown()


def test_discovered_course_inherits_parked_from_dispatch_record(tmp_path: Path) -> None:
    """P0-8（R3-a）：hub 以 discover 起、盘上已有 offline 记录 ⇒ `st.parked` 立刻为真
    （否则离线课重启后被解封队列，在线盘能领走残留 job）。"""
    d = tmp_path / "c5-gae"
    (d / "remote-jobs").mkdir(parents=True)
    (d / "training_log.jsonl").touch()
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    rec = dispatch_record_default("online")
    rec["mode"] = "offline"
    (d / "offline-dispatch.json").write_text(json.dumps(rec), encoding="utf-8")
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover(force=True)
    assert "c5-gae" in hub.courses()
    assert hub.mode_of("c5-gae") == "offline"
    assert hub._stores["c5-gae"].parked is True


def test_cold_course_authority_follows_dispatch_record(tmp_path: Path) -> None:
    """R3-c/F6：不在表的冷课，authority 从盘上记录派生（无记录 ⇒ pinned_offline；
    {online,!pin} ⇒ not_offline；{online,pin} ⇒ pinned_online；{offline} ⇒ pinned_offline）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    d = tmp_path / "cold"
    (d / "remote-jobs").mkdir(parents=True)
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _pack(tmp_path, "cold")
    assert hub.authority_of("cold") == AUTHORITY_PINNED_OFFLINE
    assert hub.is_runnable_offline("cold") is True
    row = next(r for r in hub.offline_tasks() if r["course"] == "cold")
    assert row["claimable"] is True and row["auto_handoff"] is False
    rec = dispatch_record_default("online")  # {online, pin:false}
    (d / "offline-dispatch.json").write_text(json.dumps(rec), encoding="utf-8")
    assert hub.authority_of("cold") == AUTHORITY_NOT_OFFLINE
    row2 = next(r for r in hub.offline_tasks() if r["course"] == "cold")
    assert row2["claimable"] is False and row2["reason"] == "not_offline"
    rec["pinned"] = True
    (d / "offline-dispatch.json").write_text(json.dumps(rec), encoding="utf-8")
    assert hub.authority_of("cold") == AUTHORITY_PINNED_ONLINE
    rec2 = dispatch_record_default("online")
    rec2["mode"] = "offline"
    (d / "offline-dispatch.json").write_text(json.dumps(rec2), encoding="utf-8")
    assert hub.authority_of("cold") == AUTHORITY_PINNED_OFFLINE


def test_authority_combination_matrix(tmp_path: Path) -> None:
    """F6 全组合表（在表部分）：无记录 ⇒ auto；{online,pin} ⇒ pinned_online；
    {offline,pin} ⇒ pinned_offline；{online,!pin}/{offline,!pin} ⇒ auto；标记删 ⇒ stopped。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "tbl")
    _pack(tmp_path, "tbl")
    assert hub.authority_of("tbl") == AUTHORITY_AUTO
    assert hub.auto_handoff_allowed("tbl") is True
    assert hub.set_mode_pinned("tbl", "online", True)[0] is True
    assert hub.authority_of("tbl") == AUTHORITY_PINNED_ONLINE
    assert hub.is_runnable_offline("tbl") is False
    assert hub.set_mode_pinned("tbl", "offline", True)[0] is True
    assert hub.authority_of("tbl") == AUTHORITY_PINNED_OFFLINE
    assert hub.auto_handoff_allowed("tbl") is False
    assert hub.set_mode_pinned("tbl", "online", False)[0] is True
    assert hub.authority_of("tbl") == AUTHORITY_AUTO  # 交还自动
    assert hub.set_mode_pinned("tbl", "offline", False)[0] is True
    assert hub.authority_of("tbl") == AUTHORITY_AUTO  # claim 翻的 offline（未 pin）
    assert hub.auto_handoff_allowed("tbl") is True
    os.remove(tmp_path / "tbl" / COURSE_ENABLE_MARKER)
    assert hub.authority_of("tbl") == AUTHORITY_STOPPED
    assert hub.is_runnable_offline("tbl") is False
    assert hub.auto_handoff_allowed("tbl") is False


def test_busy_gate_ignores_revoked_lease(tmp_path: Path) -> None:
    """§3.4/六轮：墓碑（revoked）不算「在跑」——「交还自动」留下的墓碑不该冻结全池。

    对账：同形状但租约仍活（`test_busy_gate_blocks_second_auto_claim_and_releases`）⇒ 409。
    """
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    assert _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-a&worker=w1", method="POST")[0] == 200
    # 交还自动（pin=0）⇒ 在线分支给租约立墓碑；authority 回 auto（不是 pinned_online，
    # 所以本用例真的走的是「墓碑不算忙」那条腿，而不是「pin 在线不占闸」）。
    assert hub.set_mode_pinned("c-a", "online", False)[0] is True
    assert hub.authority_of("c-a") == AUTHORITY_AUTO
    lease = hub.offline_lease("c-a")
    assert lease is not None and lease["revoked"] is True
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c-b&worker=w2", method="POST")
    assert st == 200, raw[:200]


def test_auto_eligible_alias_has_no_production_readers() -> None:
    """★评审 P2-4（机械守卫）：`auto_eligible` 拆成 `auto_handoff_allowed` / `is_runnable_offline`
    后，生产代码**不得**再直读这个糊在一起的名字（两个问题必须被显式选择）。

    白名单 = 空（只剩 `queue_offline.py` 的定义本身）。新读者 ⇒ 本用例红。
    """
    from hub import queue_offline as qo_mod

    hub_dir = Path(qo_mod.__file__).parent
    offenders: list[str] = []
    for py in sorted(hub_dir.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for ln in text.splitlines():
            if "auto_eligible(" in ln and "def auto_eligible" not in ln:
                offenders.append(f"{py.name}: {ln.strip()}")
    assert offenders == [], offenders
    assert "def auto_eligible(" in (hub_dir / "queue_offline.py").read_text(encoding="utf-8")
