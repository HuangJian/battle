"""自主离线交接（plan/auto-offline-handoff，2026-10-03）——★M4b 版本（课程无模式）。

治什么：开课时不再指定离线/在线 —— 一块离线盘（或云机）领走某门课**就是**「它在离线跑」，
hub 把「谁在跑」记成接管（hold）；协作派发被 hold 闸压住。本文件钉 hub 这一侧的判据：

  * **P0-1 无包死锁**：清单对每门在训课都允许「无包也 claimable」；claim 遇缺包 = 写
    `pending_export` 软态 + 请控制台导包 + 409 指路（不是 404），**不建 hold、不占闸**。
  * **P0-2 一拖一**：U2「一台盘一次只 drain 一门」是 **hub 侧不变量**（云机不可信），
    闸在 claim 的临界区、按 **worker** 判；release 后自动解除。
  * **唯一 opt-out = 停课**（删 `training-enabled.txt`）：默认清单不列，claim 409 `not_offline`。
  * **live 的 hold 不可被顶**（不变量 3）：`?takeover=1` 也不能覆盖活着的接管；要清只有
    两条路 —— 进度静默超阈（新主自动接管）或人到控制台点「强制解除接管」。
  * **§3.8 数据损坏防线**：hold 落 `offline-dispatch.json`，hub 重启后仍在。
  * **U3 waiting**：租约过期 ⇒ 该课照旧可被重领（不缩回任何「模式」）。
  * **U6 completed**：段末摘要报到跑满 ⇒ 不可再领；重导包（sha 变）自动解封。
  * **T4 排序**：按开课时间（`training-enabled.txt` 的 mtime）；读不到 ⇒ `+inf` 排最后。
  * **T8 stalled**：有人接管但无进度 / 有导包意向却超窗 ⇒ 出告警。

★M4b（2026-10-08，plan/worker-type-dispatch-model §3-M4b）把**模式**从这份文件里清掉了：
`mode_of` / `pinned` / `authority` / `seize` / `auto_handoff` 这些名字一律不再存在，
取而代之的是三件套：**hold**（谁在跑）· **pending_export**（有人在导包）· **completed**
（这段跑满了）。旧盘上的 mode/pinned/claimed_* 键**读到即忽略**（下方纯判据第一例钉死），
带模式参数的 admin POST 响亮 400（同文件 `test_admin_courses_rejects_the_retired_mode_actions`）。
"""

from __future__ import annotations

import json
import os
import threading
import time
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
    OFFLINE_HOLD_PATH,
    OFFLINE_PROGRESS_PATH,
    OFFLINE_RELEASE_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_TASK_PACK_PATH,
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
from hub.task_pack import lease_verdict

TOKEN = "sekret"

#: 清单行的字段全集（★M4b 守卫：mode/authority/pinned/seize/auto_handoff 一个都不许回来）。
ROW_KEYS = {
    "course",
    "state",
    "claimable",
    "reason",
    "busy",
    "open_time",
    "pack",
    "run_id",
    "it",
    "end_it",
    "stale_reason",
    "holder",
    "hold",
    "pending_export",
    "progress",
}


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


def test_dispatch_record_merge_ignores_the_retired_mode_keys() -> None:
    """★M4b：旧文件的 mode/pinned/claimed_*/flipped_at **读到即忽略**（不报错、不复活）。

    为什么宽容：派发状态是「重启后能不能继续跑」的唯一依据，一个写半行的 json 不该让
    hub 起不来；而旧键若被当成「有人持有」，就是幽灵接管（派发闸与本机 held 派生一起停摆）。
    """
    old = dispatch_record_merge(
        {
            "mode": "offline",
            "pinned": True,
            "claimed_offline": True,
            "claimed_by": "w1",
            "claimed_at": "x",
            "flipped_at": 1.0,
        }
    )
    assert old == dispatch_record_default()
    assert set(old) == {"v", "completed_pack_sha", "hold", "pending_export", "updated_at"}
    assert old["v"] == 2
    # v2 的三个真字段照归一（含坏值退回「没有」而不是幽灵）
    good = dispatch_record_merge(
        {
            "hold": {
                "worker_id": "w1",
                "token": "t",
                "at": 5.0,
                "last_progress_at": 6.0,
                "touch_at": 7.0,
            },
            "pending_export": {"by": "w2", "at": 8.0},
            "completed_pack_sha": "abc",
            "updated_at": "9",
        }
    )
    assert good["hold"]["worker_id"] == "w1" and good["pending_export"]["by"] == "w2"
    assert good["completed_pack_sha"] == "abc" and good["updated_at"] == 9.0
    assert dispatch_record_merge({"hold": {"worker_id": "", "token": ""}})["hold"] == {}
    assert dispatch_record_merge(None) == dispatch_record_default()


def test_open_time_key_sentinel_sorts_last_not_first() -> None:
    assert open_time_key(123.0) == 123.0
    assert open_time_key(None) > 1e18  # +inf：升序里排最后（0.0 会排最前）


def test_auto_claimable_table() -> None:
    """清单/claim 同源的唯一判据（★M4b：`auto`/`offline`/`pack_exists` 三个形参已删）。"""
    ok: dict = {}
    assert auto_claimable(holder_present=False, completed=False, busy=False, **ok) is True
    assert auto_claimable(holder_present=False, completed=False, busy=True) is False  # 一拖一
    assert auto_claimable(holder_present=False, completed=True, busy=False) is False  # 跑满
    # 有主 ⇒ 不可领；但主失联（stale）或已成墓碑（revoked）⇒ 不算挡领（五轮 P0-A）
    assert auto_claimable(holder_present=True, completed=False, busy=False) is False
    assert (
        auto_claimable(
            holder_present=True, completed=False, busy=False, holder_stale=True
        )
        is True
    )
    assert (
        auto_claimable(
            holder_present=True, completed=False, busy=False, holder_revoked=True
        )
        is True
    )


def test_stall_verdict_covers_both_legs() -> None:
    """① running 无进度 / ② 有导包意向却超窗（★M4b：去掉了 `treat_offline`/`authority`）。"""

    def verdict(
        *,
        holder: bool,
        now: float,
        last_progress: float = 0.0,
        pending_export_at: float = 0.0,
        export_window: float = 0.0,
    ) -> str:
        return stall_verdict(
            completed=False,
            holder_present=holder,
            last_progress_mtime=last_progress,
            threshold=100.0,
            now=now,
            pending_export_at=pending_export_at,
            export_window=export_window,
        )

    assert verdict(holder=True, now=1000.0) == "running-stale"
    assert verdict(holder=True, now=1000.0, last_progress=950.0) == ""
    # 无人接管、没有导包意向 ⇒ 不报警（不能平白把「刚开课还没人领」算成停滞）
    assert verdict(holder=False, now=1e9) == ""
    assert verdict(holder=False, now=1000.0, pending_export_at=500.0, export_window=100.0) == (
        "pending-export"
    )
    assert verdict(holder=False, now=1000.0, pending_export_at=950.0, export_window=100.0) == ""
    assert stall_verdict(
        completed=True,
        holder_present=True,
        last_progress_mtime=0.0,
        threshold=100.0,
        now=1000.0,
    ) == ""


def test_stall_verdict_pending_export_leg_uses_the_export_window_and_anchor() -> None:
    """★M1b：② 的锚是**导包意向**的时刻（`pending_export.at`），窗是导包窗（独立常量）。

    为什么不能拿 ① 的 threshold 当 ② 的窗：前者是「在跑但没进度」的容许量（1800s），后者是
    「说了要导包、多久没动静算出事」（= 导包窗 900s），两个语义不同值也不同。
    """

    def verdict(
        *,
        now: float,
        pending_export_at: float,
        export_window: float = 0.0,
    ) -> str:
        return stall_verdict(
            completed=False,
            holder_present=False,
            last_progress_mtime=0.0,
            threshold=100.0,
            now=now,
            pending_export_at=pending_export_at,
            export_window=export_window,
        )

    # 只有导包意向：窗内不算停、超窗算停（窗 = export_window，不是 threshold）
    assert verdict(now=1000.0, pending_export_at=950.0, export_window=100.0) == ""
    assert (
        verdict(now=1000.0, pending_export_at=899.0, export_window=100.0) == "pending-export"
    )
    # 不给 export_window ⇒ 退回 threshold（旧调用点/旧形状的缺省，不制造新的失败态）
    assert verdict(now=1000.0, pending_export_at=899.0) == "pending-export"
    assert verdict(now=1000.0, pending_export_at=950.0) == ""


def test_lease_verdict_six_states() -> None:
    """P0-1 表驱动：expired→revoked→mine→stale→foreign（顺序即语义）。"""
    now = 1000.0
    live = {
        "worker_id": "w1",
        "at": now - 10.0,
        "expires_at": now + 500.0,
        "last_progress_at": now - 10.0,
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
    silent = dict(live, last_progress_at=now - 901.0)
    assert lease_verdict(now, silent, "w2") == "stale"
    # 自己静默后回来判 mine（不被自己 409）——顺序里 mine 在 stale 之前
    assert lease_verdict(now, silent, "w1") == "mine"
    # ★M4b：没有**进度**字段 = 不可证明活着 ⇒ stale（旧的 `beat_at` + 180s 退回腿已删：
    # 心跳只续 TTL，「心跳活、进度死」正是两条事故的原形）
    beat_only = {
        "worker_id": "w1",
        "at": now - 10.0,
        "expires_at": now + 500.0,
        "beat_at": now - 1.0,
    }
    assert lease_verdict(now, beat_only, "w2") == "stale"


# ------------------------------------------------------------------ 清单面


def test_open_course_without_pack_is_claimable(tmp_path: Path) -> None:
    """P0-1 的回归锚：无包的在训课在清单里可领（缺包不是「不可领」），且行里没有模式键。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    rows = hub.offline_tasks()
    row = next(r for r in rows if r["course"] == "c5-gae")
    assert row["state"] == "no_pack"
    assert row["claimable"] is True
    assert row["pack"] is None
    assert row["hold"] == {} and row["pending_export"] == {}
    assert set(row) == ROW_KEYS, sorted(set(row) ^ ROW_KEYS)
    for dead in ("mode", "authority", "pinned", "seize", "auto_handoff", "claimed_offline"):
        assert dead not in row, dead


def test_stopped_course_is_hidden_from_the_offline_disk(tmp_path: Path) -> None:
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
    assert rows_all["c5-gae"]["reason"].startswith("not_offline")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["not_offline"] is True, body
    assert "不在训练中" in body["error"], body


def test_stopped_residue_is_excluded_from_default_list(tmp_path: Path) -> None:
    """★M4b：停课残留（盘上还留着 mode=offline 的旧记录、标记已删）默认清单**不列**——
    「唯一 opt-out = 停课」不被任何旧记录绕过；`?include=all` 里可见（state=not_offline）。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "residue")
    _course(tmp_path, hub, "live")
    for c in ("residue", "live"):
        _pack(tmp_path, c)
    # 旧形状的派发记录（v1：mode=offline）——读到即忽略，既不列也不可领
    (tmp_path / "residue" / "offline-dispatch.json").write_text(
        json.dumps({"mode": "offline", "pinned": True, "claimed_offline": True}),
        encoding="utf-8",
    )
    # 标记在发现之后删掉（真实场景：控制台停课删标记，而 hub 的表在下次扫描前还记着它）
    os.remove(tmp_path / "residue" / COURSE_ENABLE_MARKER)
    os.utime(tmp_path / "live" / COURSE_ENABLE_MARKER, (100.0, 100.0))
    rows = [r["course"] for r in hub.offline_tasks()]
    assert rows == ["live"]
    assert hub.open_time_of("residue") > 1e18
    all_rows = {r["course"]: r for r in hub.offline_tasks(include_all=True)}
    assert all_rows["residue"]["state"] == "not_offline"
    assert all_rows["residue"]["claimable"] is False


def test_course_without_pack_sorts_last_not_first(tmp_path: Path) -> None:
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


def test_cold_course_follows_the_open_marker(tmp_path: Path) -> None:
    """不在表的冷课：判据只看**开课标记**（★M4b：`authority`/`seize` 已删）。

    为什么看盘不看表：课程表是「1 小时新鲜度扫描」的产物，而自主课本机不训练 ⇒ 冷掉或
    hub 重启后它从表里消失；那时只认表就会让「缺包自愈」在最需要它的场景里静默失效。
    """
    hub = _HubQueue({}, discover_root=tmp_path)
    d = tmp_path / "cold"
    (d / "remote-jobs").mkdir(parents=True)
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _pack(tmp_path, "cold")
    row = next(r for r in hub.offline_tasks() if r["course"] == "cold")
    assert row["claimable"] is True and row["state"] == "ready"
    # 标记删掉（停课）⇒ 从候选面消失（唯一 opt-out）；冷课连 `?include=all` 也不复活——
    # ★M4b：`not_offline` 行只服务**表里**（发现过）的课（`_order`），而冷课从没进过表。
    os.remove(d / COURSE_ENABLE_MARKER)
    assert [r["course"] for r in hub.offline_tasks()] == []
    assert [r["course"] for r in hub.offline_tasks(include_all=True)] == []


# ------------------------------------------------------------------ claim 面


def test_claim_without_pack_records_pending_export_and_triggers_export(
    tmp_path: Path, monkeypatch
) -> None:
    """P0-1：claim 建模入口 —— 缺包不再是 404，而是记 `pending_export` + 触发导包 + 409 指路。"""
    calls = _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409, body
    assert body["pending_export"] is True and body["triggered"] is True
    assert body["give_up"] is False
    assert "auto_handoff" not in body, "★M4b：响应里不再有模式时代的 auto_handoff 键"
    assert calls == ["c5-gae"]
    # 软态落盘：by=本 worker、at>0；**不建 hold、不建租约**（Q1）
    assert hub.pending_export_of("c5-gae")["by"] == "w1"
    assert hub.pending_export_of("c5-gae")["at"] > 0
    assert hub.hold_of("c5-gae") == {} and hub.offline_lease("c5-gae") is None
    disk = json.loads(
        (tmp_path / "c5-gae" / "offline-dispatch.json").read_text(encoding="utf-8")
    )
    assert "mode" not in disk and "claimed_offline" not in disk and "pinned" not in disk
    # 再问一次：节流（不重复打扰控制台）
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    assert st2 == 409 and _json(raw2)["triggered"] is False
    assert calls == ["c5-gae"]


def test_claim_without_pack_degrades_when_console_unreachable(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_auto_handoff(monkeypatch, (False, "OSError"))
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["pending_export"] is True
    assert "控制台" in body["trigger_note"], body
    # 半状态可见：软态已记下（别的盘排序靠后）但没人跑 ⇒ 由 stalled 告警兜（另一条用例）


def test_stopped_course_with_pack_is_rejected(tmp_path: Path) -> None:
    """★六轮 F3 + ★M4b：停课 ⇒ 409 `not_offline`（**有包也不可领**：停课不是「等人导包」）。

    旧判据（`mode != offline` 才拒）随模式删除——那个洞（停课后旧 `mode=offline` 记录让云机
    整段跑）在新判据下不存在：判据是**开课标记**，与任何历史记录无关。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    (tmp_path / "c5-gae" / "offline-dispatch.json").write_text(
        json.dumps({"mode": "offline", "pinned": True}), encoding="utf-8"
    )
    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)
    assert [r["course"] for r in hub.offline_tasks()] == []
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["not_offline"] is True, body
    # 缺包也走同一条 409：不替停掉的课触发导包
    os.remove(tmp_path / "c5-gae" / "task-c5-gae.zip")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body2 = _json(raw2)
    assert st2 == 409 and body2["not_offline"] is True, body2
    assert hub.pending_export_of("c5-gae") == {}


def test_claim_with_pack_grants_hold_and_leaves_the_job_queue_alone(tmp_path: Path) -> None:
    """★M4b 行为变更：claim 成功 = **建 hold**，不再翻模式、不再撤单。

    为什么撤单那条腿必须走：旧写法「claim 自动课 ⇒ 撤掉未认领的在线 job」的前提是
    「这门课被切成离线、在线 job 不再有人领」。新模型里没有切模式，在线 job 照旧可被协作盘
    领走（hub 的一拖一只管同一台盘）——所以领走一门课**不许**动它的队列，否则会静默吃掉
    在线盘的活（这正是本轮重构要拆的耦合）。
    """
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _pack(tmp_path, "c5-gae")
        hub._stores["c5-gae"].publish(
            "job-1", {"job_id": "job-1", "it": 3, "role": "online"}, b"PK\x03\x04fake"
        )
        st, raw = _req(
            base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST"
        )
        assert st == 200, raw[:200]
        assert hub.hold_of("c5-gae")["worker_id"] == "w1"
        assert (hub.offline_lease("c5-gae") or {}).get("worker_id") == "w1"
        disk = json.loads(
            (tmp_path / "c5-gae" / "offline-dispatch.json").read_text(encoding="utf-8")
        )
        assert disk["hold"]["worker_id"] == "w1" and disk["v"] == 2
        assert "mode" not in disk and "claimed_offline" not in disk
        # 队列一字不动（既没撤单、也照旧可被在线盘领）
        assert hub._stores["c5-gae"].claimable_job_ids() == ["job-1"]
        assert not (tmp_path / "c5-gae" / "remote-jobs" / "job_cancelled.jsonl").exists()
    finally:
        srv.shutdown()


def test_claim_with_pack_asks_console_for_freshness(tmp_path: Path, monkeypatch) -> None:
    """★ 2026-10-04 用户报障的回归锚（抢占在训课 ⇒ 在线权重/动量白丢？）。

    有包那条腿（claim 成功）过去**不问控制台** ⇒ 盘上的旧包被直接取走；而离线腿的
    续跑锚点只认回传/导入的轮次（`queue_resume.resume_sources`），**看不见本机在线轮**。
    现在 claim 成功也要请控制台按「包比权重/源码新?」判一次（旧包由控制台**同步**作废 +
    重导 ⇒ 云机随后取包拿到的一定是新包）。
    """
    calls = _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    body = _json(raw)
    assert calls == ["c5-gae"], "有包的抢占也要问一次控制台（不然旧包直接开跑）"
    assert "新鲜度" in body.get("handoff", ""), body
    # 交还后另一门课：stub 账本清零再 claim，确认「每次 claim 都会问一次」
    token = body["lease"]["token"]
    assert (
        _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease={token}", method="POST")[0]
        == 200
    )
    calls.clear()
    _course(tmp_path, hub, "c-other")
    _pack(tmp_path, "c-other")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-other&worker=w1", method="POST")
    assert st2 == 200, raw2[:200]
    assert calls == ["c-other"]


def test_claim_with_pack_degrades_when_console_unreachable(tmp_path: Path, monkeypatch) -> None:
    """控制台不可达：claim 照常成功，只在回执里说清（不制造新的失败态）。"""
    _stub_auto_handoff(monkeypatch, (False, "OSError"))
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    assert "控制台不可达" in _json(raw).get("handoff", ""), raw[:300]


def test_restart_keeps_the_hold_from_disk(tmp_path: Path) -> None:
    """§3.8 数据损坏防线（★M4b 版）：重启后正在跑的课**仍被接管**（不被启动参数解封）。

    旧版钉的是「盘上 `mode=offline` ⇒ 启动参数说 online 也照跑」；模式没了之后，同一个防护
    责任整个落在 **hold**（「谁在跑」是唯一的独占判据）。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
    # 模拟重启：同一目录、带一个「模式段」（旧启动参数，现在只记 WARN）
    hub2 = _HubQueue({}, discover_root=tmp_path, order=["c5-gae"], ignored_modes={"c5-gae"})
    hub2.discover(force=True)
    assert hub2.hold_of("c5-gae")["worker_id"] == "w1"
    assert hub2.mode_ignored("c5-gae") is True, "启动参数的模式段只记 WARN 标记"
    row = next(r for r in hub2.offline_tasks() if r["course"] == "c5-gae")
    assert row["state"] == "claimed" and row["claimable"] is False
    assert hub2._stores["c5-gae"].hold_blocked() == "held:w1"


def test_busy_gate_blocks_the_same_worker_and_releases(tmp_path: Path) -> None:
    """U2 一拖一：**同一台盘**任一时刻至多一门 running（Q3/D3）；release 后闸自动解除。

    ★M1b 语义变更：一拖一按 **worker** 判（不再是「全局只有一门」）——两台盘各跑一门是
    多机并行的本意。而清单的 `busy`/`claimable` 只有**带 `?worker=`** 才知道「你在跑什么」，
    不带就是**上界**语义（`claimable=true` 只保证「无并发时能领」，plan §1.5.4-P2-x/§69）。
    """
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w1", method="POST")
    assert st == 200, raw[:200]
    # 不带 worker ⇒ 上界：两台盘的多机并行在这里必须看起来「都可领」。
    upper = {r["course"]: r for r in hub.offline_tasks()}
    assert upper["c-b"]["claimable"] is True
    assert upper["c-b"]["busy"] is False
    # 带 worker=w1 ⇒ 一拖一生效（同一台盘）：c-b 看着不可领，拒因说得清「你在哪门课上」。
    rows = {r["course"]: r for r in hub.offline_tasks(worker="w1")}
    assert rows["c-b"]["claimable"] is False
    assert str(rows["c-b"]["reason"]).startswith("busy:")
    assert "c-a" in str(rows["c-b"]["reason"])
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w1", method="POST")
    body2 = _json(raw2)
    assert st2 == 409 and body2["busy"] is True, body2
    # 而**另一台盘** w2 领 c-b 不受 w1 影响（多机并行）
    st_w2, raw_w2 = _req(
        base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w2", method="POST"
    )
    assert st_w2 == 200, raw_w2[:200]
    # release c-a ⇒ w1 的 c-b 不再吃 busy，而是被别人带 held
    token = _json(raw)["lease"]["token"]
    rel = _req(base, f"{OFFLINE_RELEASE_PATH}?course=c-a&lease={token}", method="POST")
    assert rel[0] == 200, rel[1][:200]
    rows2 = {r["course"]: r for r in hub.offline_tasks(worker="w1")}
    assert rows2["c-b"]["claimable"] is False  # 现在挡它的是 w2 的 hold（不是 busy）
    assert str(rows2["c-b"]["reason"]).startswith("held:")
    rel_w2 = _req(
        base,
        f"{OFFLINE_RELEASE_PATH}?course=c-b&lease={_json(raw_w2)['lease']['token']}",
        method="POST",
    )
    assert rel_w2[0] == 200, rel_w2[1][:200]
    st3, raw3 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w1", method="POST")
    assert st3 == 200, raw3[:200]


def test_pending_export_soft_state_does_not_gate_others(tmp_path: Path, monkeypatch) -> None:
    """★M1b / Q1 反转（旧名 `test_pending_export_window_also_blocks_second_course`）：

    导包窗口（包还没出现 / `pending_export` 软态）**不占任何闸**——旧行为是「一次导包把所有
    盘冻住」，那正是本次重构要拆的痛点（F11：`_busy_locked` 的腿②整条删）。现在：同一台盘
    可以同时让两门课进入导包态（导包是几十分钟的后台活），而**包到手那一刻**才由 `note_hold`
    建 hold、才吃一拖一。
    """
    _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
    st_a, raw_a = _req(
        base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w1", method="POST"
    )
    assert st_a == 409 and _json(raw_a)["pending_export"] is True
    row_a = next(r for r in hub.offline_tasks(worker="w1") if r["course"] == "c-a")
    assert row_a["state"] == "no_pack"
    assert row_a["pending_export"]["by"] == "w1"
    assert row_a["hold"] == {}  # Q1：无包 claim **不建 hold**
    # 同一台盘的 c-b 照旧可领（导包软态不占闸），且 c-b 的 claim 也走导包腿。
    rows = {r["course"]: r for r in hub.offline_tasks(worker="w1")}
    assert rows["c-b"]["claimable"] is True
    assert rows["c-b"]["busy"] is False
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w1", method="POST")
    assert st == 409 and _json(raw)["pending_export"] is True
    assert _json(raw).get("busy") is None


def test_waiting_course_is_reclaimable_after_lease_expiry(tmp_path: Path) -> None:
    """U3：掉线后一直等待（无人持有），过期后照旧可被重连续领。"""
    clock = [0.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    clock[0] += 1000.0  # 租约 TTL=900 ⇒ 过期
    assert hub.offline_lease("c5-gae") is None
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c5-gae"]["state"] == "ready"  # 可被重连续领（waiting 的出口之一）
    assert rows["c5-gae"]["claimable"] is True
    # 盘上的 hold 仍写着 w1：但它的进度已静默超阈 ⇒ 新主自动接管（清单说得出理由）
    assert rows["c5-gae"]["holder"]["worker_id"] == "w1"
    assert rows["c5-gae"]["holder"]["stale"] is True
    assert str(rows["c5-gae"]["reason"]).startswith("held-stale")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w2", method="POST")
    assert st2 == 200, raw2[:200]


def test_completed_pack_cannot_be_reclaimed_until_re_export(tmp_path: Path) -> None:
    """U6 + 二轮 P1-1：跑满锚在**包 sha** 上；重导包（sha 变）自动解封。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae", b"PK-old")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
    hub.note_offline_completed("c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["completed"] is True, body
    rows = {r["course"]: r for r in hub.offline_tasks()}
    assert rows["c5-gae"]["state"] == "completed" and rows["c5-gae"]["claimable"] is False
    # 重导：新 sha ⇒ 自动解封
    from hub.task_pack import _file_sha256

    _pack(tmp_path, "c5-gae", b"PK-new")
    assert (
        hub.completion_blocked(
            "c5-gae", _file_sha256(tmp_path / "c5-gae" / "task-c5-gae.zip")
        )
        is False
    )


def test_stalled_alert_covers_pending_export_window(tmp_path: Path, monkeypatch) -> None:
    """T8：记了导包意向、没人跑 ⇒ 告警（不能靠「人总会看到」）。"""
    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]  # 不用 0：`pending_export.at=0` 与「没有锚点」不可区分（生产时钟恒为墙钟）
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 409
    )
    assert hub.offline_stalled() == []  # 刚开始导包：不算停
    clock[0] += 2000.0  # 超过缺省阈值 1800s
    stalled = hub.offline_stalled()
    assert [s["course"] for s in stalled] == ["c5-gae"]
    assert stalled[0]["why"] == "pending-export"
    st, raw = _req(base, "/admin/offline")
    assert st == 200 and _json(raw)["stalled"][0]["course"] == "c5-gae"


def test_admin_courses_exposes_hold_and_release_hold_clears_it(
    tmp_path: Path, monkeypatch
) -> None:
    """★M1b：控制台的「强制解除接管」= `/admin/courses?...&release_hold=1`。

    两个面一起钉：① 读面（`/admin/courses` 的 `held`/`holder`/`pending_export`）——控制台
    据此渲染徽标；② 写面（release_hold）= 立墓碑 + 清 hold，且**进程重启后仍然有效**
    （hold 是落盘的，而内存租约可能已经没了 —— 那正是人在现场最可能遇到的情形）。
    """
    _stub_auto_handoff(monkeypatch)
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    hub.note_hold("c5-gae", worker_id="cloud-1", token="tok-1")
    st, raw = _req(base, "/admin/courses")
    row = next(r for r in _json(raw)["courses"] if r["course"] == "c5-gae")
    assert st == 200 and row["held"] is True and row["holder"] == "cloud-1", row
    assert set(row) == {"course", "training", "held", "holder", "pending_export", "mode_ignored"}
    # 清掉内存租约（模拟「hub 重启过，盘上还有 hold」）⇒ release_hold 仍要清得掉
    hub._leases.clear()
    st, raw = _req(base, "/admin/courses?course=c5-gae&release_hold=1", method="POST")
    assert st == 200 and _json(raw)["released"] is True, raw
    assert hub.hold_of("c5-gae") == {}
    st, raw = _req(base, "/admin/courses")
    row = next(r for r in _json(raw)["courses"] if r["course"] == "c5-gae")
    assert row["held"] is False and row["holder"] == ""
    # 本来就没接管 ⇒ 409（「没得解」要说出来，别谎报成功）
    st, raw = _req(base, "/admin/courses?course=c5-gae&release_hold=1", method="POST")
    assert st == 409 and _json(raw)["released"] is False, raw


def test_admin_courses_rejects_the_retired_mode_actions(tmp_path: Path) -> None:
    """★M4b 退役钉：带 `mode`/`pin`/`drop_jobs` 的 POST ⇒ **400 + 退役文案**（响亮，不静默失败）。

    为什么不是 200 静默忽略：旧控制台 / 老脚本 / 人手里的 curl 会以为「切模式成功」，
    而实际上什么都没发生——响亮 400 是唯一能让人去刷新页面的信号（兼容矩阵：旧 console ×
    新 hub ⇒ 400 退役文案）。
    """
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    for qs in ("mode=offline", "mode=online&pin=1", "mode=offline&pin=1&drop_jobs=1", "drop_jobs=1"):
        st, raw = _req(base, f"/admin/courses?course=c5-gae&{qs}", method="POST")
        body = _json(raw)
        assert st == 400, (qs, st, body)
        assert "已退役" in body["error"], (qs, body)
    # 未知动作同样 400（不是 200 的空承诺）
    st, raw = _req(base, "/admin/courses?course=c5-gae&boom=1", method="POST")
    assert st == 400 and "未知动作" in _json(raw)["error"], raw
    assert hub.hold_of("c5-gae") == {}


def test_admin_queue_has_no_mode_or_authority_fields(tmp_path: Path) -> None:
    """★M4b：`/admin/queue` 每课行**不再**有 `mode`/`authority`/`pinned`；`/admin/offline`
    的租约带 stale/revoked（接管面的两个读点口径一致）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
    q = _json(_req(base, "/admin/queue")[1])
    row = q["courses"]["c5-gae"]
    for dead in ("mode", "authority", "pinned"):
        assert dead not in row, dead
    # 接管读数在（嵌套 `hold`，与 /admin/courses 同源——控制台 holdFacts 读的正是它）
    assert row["hold"]["state"] == "live" and row["hold"]["worker_id"] == "w1"
    assert q.get("offline") is None, "★M4b：队列观测顶层不再有 offline 课程清单"
    leases = _json(_req(base, "/admin/offline")[1])["leases"]
    assert leases["c5-gae"]["worker_id"] == "w1"
    assert leases["c5-gae"]["revoked"] is False and leases["c5-gae"]["stale"] is False
    # 强制解除接管 ⇒ 墓碑（保留在 /admin/offline，不静默删）且清单行可渲染 held-revoked
    assert (
        _req(base, "/admin/courses?course=c5-gae&release_hold=1", method="POST")[0] == 200
    )
    leases2 = _json(_req(base, "/admin/offline")[1])["leases"]
    assert leases2["c5-gae"]["revoked"] is True
    row2 = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row2["holder"] is not None and row2["holder"]["revoked"] is True
    assert str(row2["reason"]).startswith("held-revoked")
    assert row2["claimable"] is True


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
    st3, raw3 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
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
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )


def test_admin_courses_get_reports_hold_state(tmp_path: Path) -> None:
    """`/admin/courses` 的 GET 行读的是 **hold**（★M4b：`claimed_offline`/`pinned` 已删）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
    st, raw = _req(base, "/admin/courses")
    row = _json(raw)["courses"][0]
    assert st == 200
    assert row["held"] is True and row["holder"] == "w1"
    assert row["training"] is True and row["pending_export"] is False
    for dead in ("claimed_offline", "pinned", "mode", "authority"):
        assert dead not in row, dead
    # 交还 ⇒ held 立刻回 false（清完协作派发当天恢复）
    tok = str((hub.offline_lease("c5-gae") or {}).get("token") or "")
    assert _req(base, f"{OFFLINE_RELEASE_PATH}?course=c5-gae&lease={tok}", method="POST")[0] == 200
    row2 = next(r for r in _json(_req(base, "/admin/courses")[1])["courses"] if r["course"] == "c5-gae")
    assert row2["held"] is False and row2["holder"] == ""


# ------------------------------------------------------------------ 队列面（撤单 vs claim）


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


def test_missing_pack_claim_does_not_drop_unclaimed_jobs(tmp_path: Path, monkeypatch) -> None:
    """★M4b 行为变更（旧名 `test_auto_claim_offline_drops_unclaimed_jobs`）：claim **不再撤单**。

    旧写法把「切模式」与「撤掉未认领的在线 job」绑在一起；模式退役后撤单腿一并删除
    （裁决 Q2 的原意是「与人的开关共用同一条切模式链」，而那条链整体不存在了）。
    后果是**可见的**：一门课的队列要么由在线盘照常领走，要么由离线盘 claim 后照常取走
    ——不再有任何路径静默作废别人的活。要撤单只有人工动作（`cancel_unsettled_jobs`）。
    """
    _stub_auto_handoff(monkeypatch)
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "stale-1")
        st, raw = _req(
            base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=tpu-1", method="POST"
        )
        assert st == 409 and _json(raw)["pending_export"] is True, raw[:200]
        assert _cancelled_ids(hub, "c5-gae") == set()
        assert hub._stores["c5-gae"].claimable_job_ids() == ["stale-1"]
        # 有包的那条腿同样不撤单
        _pack(tmp_path, "c5-gae")
        st2, raw2 = _req(
            base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=tpu-1", method="POST"
        )
        assert st2 == 200, raw2[:200]
        assert _cancelled_ids(hub, "c5-gae") == set()
        assert hub._stores["c5-gae"].claimable_job_ids() == ["stale-1"]
    finally:
        srv.shutdown()


def test_cancelled_job_is_rejected_even_with_stale_peek(tmp_path: Path) -> None:
    """作废有两道：账本（真闸，重启后仍作废）+ 进程内 set（挡「拿作废前 peek 的 jid 硬领」）。

    ★M4b：作废的唯一入口是 `cancel_unsettled_jobs`（人的动作 / 停课链）；claim 不再撤单。
    """
    base, hub, srv = _boot(tmp_path)
    try:
        _course(tmp_path, hub, "c5-gae")
        _publish_job(hub, "c5-gae", "gone-1")
        store = hub._stores["c5-gae"]
        assert store.cancel_unsettled_jobs(reason="test") == ["gone-1"]
        assert store.claimable_job_ids() == []  # 账本真闸：池子里没有它了
        assert store.claim("gone-1", worker_id="w1") is None  # 即时闸：硬领也被拒
        assert "gone-1" not in store._leases, "被拒的认领不该留下租约"
    finally:
        srv.shutdown()


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


# ------------------------------------------------------------------ 六轮评审（2026-10-05）


def test_stale_lease_is_reclaimed_by_new_worker(tmp_path: Path, monkeypatch) -> None:
    """T3（hub 侧）：**进度**静默超阈 ⇒ 清单 stale+claimable、新主 claim 200（带 reclaimed）、
    旧主心跳 409 `taken`；同一 worker 回来仍判 mine。

    ★M1b/F2：超阈的尺子从「心跳静默 180s」换成「**进度**静默 900s」——两者是**两个量级**
    （心跳 60s 一跳，进度按轮/按检查点）。本用例用 env 把新阈值调到 180s，保持原来的
    时间尺（这同时钉住「生效的是新阈值，不是那个旧常量」）。
    """
    monkeypatch.setenv("BCITY_HOLD_PROGRESS_STALE_SEC", "180")
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")
    assert st == 200, raw[:200]
    tok1 = _json(raw)["lease"]["token"]
    clock[0] += 200.0  # 进度静默超阈（env 调成 180s）但未过 TTL
    row = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row["claimable"] is True and row["holder"]["stale"] is True
    assert str(row["reason"]).startswith("held-stale")
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w2", method="POST")
    body2 = _json(raw2)
    assert st2 == 200 and body2["lease"].get("reclaimed") is True, body2
    assert body2["lease"]["reclaimed_from"] == "w1"
    assert body2["lease"]["worker_id"] == "w2"
    # 旧主心跳：409 taken（token 不符）
    st3, raw3 = _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok1}", method="POST")
    assert st3 == 409, raw3[:200]
    # 新主自己再来（同 id）⇒ mine 续上，不被自己的静默 409（顺序：mine 在 stale 之前）
    clock[0] += 200.0
    st4, raw4 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w2", method="POST")
    assert st4 == 200 and _json(raw4)["lease"].get("reclaimed") is not True, raw4[:200]


def test_busy_gate_ignores_stale_lease(tmp_path: Path) -> None:
    """§3.4：死盘（**进度**静默超阈）不占「一拖一」闸——同一台盘能接着领别的课。

    ★M1b：静默的尺子从「别的课的**心跳** 180s」换成「**进度** 900s」（活性只认进度）。
    """
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w1", method="POST")[0]
        == 200
    )
    clock[0] += 1000.0  # c-a 进度静默超阈（900）⇒ 它不再算「w1 在跑」
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w1", method="POST")
    assert st == 200, raw[:200]


def test_busy_gate_ignores_revoked_lease(tmp_path: Path) -> None:
    """§3.4/六轮：墓碑（revoked）不算「在跑」——「强制解除接管」留下的墓碑不该冻结全池。

    对账：同形状但租约仍活（`test_busy_gate_blocks_the_same_worker_and_releases`）⇒ 409。
    """
    base, hub, _srv = _boot(tmp_path)
    for c in ("c-a", "c-b"):
        _course(tmp_path, hub, c)
        _pack(tmp_path, c)
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w1", method="POST")[0]
        == 200
    )
    assert hub.revoke_offline_lease("c-a", "人强制解除接管") is True
    lease = hub.offline_lease("c-a")
    assert lease is not None and lease["revoked"] is True
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w2", method="POST")
    assert st == 200, raw[:200]


def test_handoff_trigger_budget_resets_on_new_round(tmp_path: Path, monkeypatch) -> None:
    """P0-B + F4：导包触发账本烧满 3 次后——换主 ⇒ 重置；**同主重开会话（超窗）** 也重置
    （`.worker-id` 持久，同机重开沿用同一 id，只判换主会漏这一档）。

    ★M4b：入口改成 `begin_pending_export`（旧 `begin_auto_handoff`）——它只写软态，
    不翻模式、不撤单。
    """
    from hub import task_pack as tp

    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _, why = hub.begin_pending_export("c5-gae", "w1")
    assert why == ""
    for _ in range(3):
        tp.note_auto_handoff_trigger("c5-gae", now=clock[0])
        clock[0] += 700.0
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "give_up"
    hub.begin_pending_export("c5-gae", "w2")  # 换主 ⇒ 重置
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "trigger"
    for _ in range(3):
        tp.note_auto_handoff_trigger("c5-gae", now=clock[0])
        clock[0] += 700.0
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "give_up"
    clock[0] += 1000.0  # 距上次导包意向超 AUTO_HANDOFF_PENDING_SEC=900
    hub.begin_pending_export("c5-gae", "w2")  # 同主、超窗 ⇒ 仍是「新一轮」
    assert tp.auto_handoff_decision("c5-gae", now=clock[0]) == "trigger"


def test_begin_pending_export_refuses_stopped_and_unknown(tmp_path: Path) -> None:
    """★M4b：软态写入的两条前置闸（开课标记在 ∧ 课程在表里），拒因分开说。"""
    hub = _HubQueue({}, discover_root=tmp_path)
    _course(tmp_path, hub, "c5-gae")
    assert hub.begin_pending_export("ghost", "w1")[0] == "not_open"
    os.remove(tmp_path / "c5-gae" / COURSE_ENABLE_MARKER)
    hub.discover(force=True)
    assert hub.begin_pending_export("c5-gae", "w1")[0] == "not_open"
    assert hub.pending_export_of("c5-gae") == {}


def test_pending_export_soft_state_does_not_gate_and_expires(tmp_path: Path, monkeypatch) -> None:
    """★M1b 替换旧 `test_handoff_window_anchor_is_claimed_at`（Q1 + F8 + F11）：

    旧用例钉的是「导包窗口占一拖一闸」（腿②）——那条腿按 Q1 **已整条删**（一次导包不该把
    整个生态冻住）。新语义分两半：① 软态**不占闸**（同一台盘领别的有包课照旧成功）；
    ② 读面有**窗**（`AUTO_HANDOFF_PENDING_SEC`）——崩溃/换机的导包者不该在盘上留一条
    永久「有人在导包」（惰性过期，不养清理线程）。`pending_export.at` 仍被每次缺包 claim
    刷新（它服务两件事：读面的窗 + 停滞告警的锚）。
    """
    _stub_auto_handoff(monkeypatch)
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c-a")
    _course(tmp_path, hub, "c-b")
    _pack(tmp_path, "c-b")
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w1", method="POST")[0]
        == 409
    )
    row = next(r for r in hub.offline_tasks(worker="w1") if r["course"] == "c-a")
    assert row["pending_export"]["by"] == "w1" and row["hold"] == {} and row["busy"] is False
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-b&worker=w1", method="POST")
    assert st == 200, raw[:200]  # 软态不占闸
    clock[0] += 2000.0
    anchor = float(hub.dispatch_record("c-a")["pending_export"]["at"])
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c-a&worker=w2", method="POST")[0]
        == 409
    )
    assert float(hub.dispatch_record("c-a")["pending_export"]["at"]) > anchor  # 重试刷新锚点
    clock[0] += 1000.0  # 再超窗（900s）且无人重试 ⇒ 读面不再当有人在导
    assert hub.pending_export_of("c-a") == {}
    row2 = next(r for r in hub.offline_tasks(worker="w1") if r["course"] == "c-a")
    assert row2["pending_export"] == {}


def test_discovered_course_inherits_hold_from_dispatch_record(tmp_path: Path) -> None:
    """P0-8（R3-a）+ ★M1c：hub 以 discover 起、盘上已有 **live hold** ⇒ 闸的镜像立刻为真
    （否则接管中的课重启后被解封队列，在线盘能领走残留 job）。

    旧版这条钉的是 `mode=offline ⇒ st.parked`；`parked` 随 mode 退役后，同一个防护责任
    整个搬到 hold（「谁在跑」是唯一的独占判据）。
    """
    d = tmp_path / "c5-gae"
    (d / "remote-jobs").mkdir(parents=True)
    (d / "training_log.jsonl").touch()
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    rec = dispatch_record_default()
    rec["hold"] = {
        "worker_id": "cloud-1",
        "token": "tok-1",
        "at": 1.0,
        "last_progress_at": time.time(),
        "touch_at": time.time(),
    }
    (d / "offline-dispatch.json").write_text(json.dumps(rec), encoding="utf-8")
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover(force=True)
    assert "c5-gae" in hub.courses()
    assert hub.hold_of("c5-gae")["worker_id"] == "cloud-1"
    assert hub._stores["c5-gae"].hold_blocked() == "held:cloud-1"


# ───────────────── M1b（plan/worker-type-dispatch-model §3-M1b，2026-10-07）─────────────────
#
# 这一节钉的是**消费点切换**：新端点（`/offline/hold` · `/offline/progress`）、旧端拒收
# （`?proto=2`）、取包 lease 门（P1-2）、以及「心跳活 / 进度死」的四象限（§68 的防线）。


def test_old_client_without_proto_is_rejected_loudly(tmp_path: Path) -> None:
    """★DoD#3：claim 缺 `?proto=2` ⇒ 409，且响应**同时**含 `busy:true` + `error` 全文 +
    `proto_required:2`。为什么借 `busy`：旧码的 409 分流只有 busy 腿会把 `error` 打进日志
    且本拍不跑（不耗 idle 预算、绝不静默双跑）——「请刷新 notebook」得真能到达现场。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_CLAIM_PATH}?course=c5-gae&worker=w1", method="POST")
    body = _json(raw)
    assert st == 409 and body["busy"] is True and body["proto_required"] == 2, body
    assert "battle.offline.ipynb" in body["error"], body
    # 拒在入口：没有租约、没有 hold、磁盘记录一字未动
    assert hub.offline_lease("c5-gae") is None and hub.hold_of("c5-gae") == {}
    assert not (tmp_path / "c5-gae" / "offline-dispatch.json").exists()


def test_hold_endpoint_reads_liveness_for_the_trainer(tmp_path: Path) -> None:
    """★M1b / P0-5①：`GET /offline/hold` 是 trainer 的只读解锁通道。

    只读（不建 hold、不刷活性）；`held` 只在 **live** 时为真 —— stale 必须给 false，
    否则「云机掉线了本机该立刻恢复」这条腿会被一个陈旧的 hold 永久钉死。
    """
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")
    body = _json(raw)
    assert st == 200 and body["held"] is False and body["state"] == "", body
    assert hub.hold_of("c5-gae") == {}  # 只读：读一次不建 hold
    assert (
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[0]
        == 200
    )
    body = _json(_req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")[1])
    assert body["held"] is True and body["state"] == "live"
    assert body["worker_id"] == "w1" and body["progress_ago"] == 0.0
    assert body["pending_export"] is False
    # 进度静默超阈（默认 900s）⇒ held=false（本机该恢复协作），但 holder 仍看得见
    clock[0] += 1000.0
    body = _json(_req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")[1])
    assert body["held"] is False and body["state"] == "stale"
    assert body["worker_id"] == "w1" and body["progress_ago"] == 1000.0
    # 课程名缺失 = 400（不是 500、也不是「held=false」的假答案）
    assert _req(base, OFFLINE_HOLD_PATH)[0] == 400
    assert _req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae", token="bad")[0] in (401, 403)


def test_progress_endpoint_refreshes_liveness_only_for_the_holder(tmp_path: Path) -> None:
    """★M1b / Q2：`POST /offline/progress` 与心跳同一套 token 分流，效果多一条：

    **只认进度的活性**被刷（心跳刷 TTL 但不刷活性 —— §68 的假活就是心跳当活性算出来的）。
    非持有者 / 过期 / 被撤销一律 409（不是 200 的静默 no-op：云机要能据此收尾）。
    """
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    tok = _json(
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[1]
    )["lease"]["token"]
    clock[0] += 800.0  # 心跳与 TTL 还活着，但进度快超阈了
    assert _json(_req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")[1])["held"] is True
    # 心跳不刷活性：推它一把，态度不变（仍会走向 stale）
    assert (
        _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok}", method="POST")[0]
        == 200
    )
    clock[0] += 200.0  # 距上次**进度** 1000s > 900
    body = _json(_req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")[1])
    assert body["held"] is False and body["state"] == "stale", body
    # 进度打点把它救回来（同一拍：TTL + 活性都刷）
    st, raw = _req(
        base, f"{OFFLINE_PROGRESS_PATH}?course=c5-gae&lease={tok}", method="POST"
    )
    assert st == 200 and _json(raw)["ok"] is True, raw[:200]
    body = _json(_req(base, f"{OFFLINE_HOLD_PATH}?course=c5-gae")[1])
    assert body["held"] is True and body["progress_ago"] == 0.0
    assert hub.hold_of("c5-gae")["last_progress_at"] == clock[0]  # 活性锚 = 打点那一刻
    # 换主后旧 token 打点 = 409 taken（旧主的 ping 不续新主的命）
    st, raw = _req(base, f"{OFFLINE_PROGRESS_PATH}?course=c5-gae&lease=deadbeef", method="POST")
    assert st == 409 and _json(raw)["ok"] is False, raw[:200]
    # 没有租约的课：打点不建 hold（Q1）——只为不存在的租约开一条 200 会把「谁在跑」稀释掉
    _course(tmp_path, hub, "c-cold")
    assert (
        _req(base, f"{OFFLINE_PROGRESS_PATH}?course=c-cold&lease={tok}", method="POST")[0]
        == 409
    )
    assert hub.hold_of("c-cold") == {}


def test_heartbeat_alive_progress_dead_is_stale_and_auto_takeover(tmp_path: Path) -> None:
    """★DoD#1 反面（§68 的假活）：**心跳一直活着、进度整段死** ⇒ 判掉线、可被自动接管。

    这正是 2026-10-05/06 两条事故的原形：旧判据只看心跳 ⇒ 云机挂死也能永久占着课。
    """
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    tok1 = _json(
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[1]
    )["lease"]["token"]
    for _ in range(6):  # 每 200s 一拍心跳：TTL 一直被续（旧判据下这盘永远「活」）
        clock[0] += 200.0
        assert (
            _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok1}", method="POST")[0]
            == 200
        )
    row = next(r for r in hub.offline_tasks() if r["course"] == "c5-gae")
    assert row["holder"]["stale"] is True and row["holder"]["progress_ago"] == 1200.0
    assert row["claimable"] is True and str(row["reason"]).startswith("held-stale")
    assert row["state"] != "claimed", "主已掉线 ⇒ 清单不该还说「有人在跑」"
    # 新盘无需任何参数即自动接管；旧主心跳立刻 409 taken
    st2, raw2 = _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w2", method="POST")
    assert st2 == 200 and _json(raw2)["lease"]["reclaimed_from"] == "w1", raw2[:200]
    assert (
        _req(base, f"{OFFLINE_HEARTBEAT_PATH}?course=c5-gae&lease={tok1}", method="POST")[0]
        == 409
    )


def test_live_hold_requires_the_lease_token_to_fetch_the_pack(tmp_path: Path) -> None:
    """★M1b / P1-2：取包门 = 「无 live hold ∨ 持 lease 且 token 相符」**加在**旧门上。

    live hold ⇒ 不带 `?lease=` 的旧客户端拿 409（旧端兜底第二道闸：包在盘上不等于发给你）；
    带对 token 的持有者照取。stale 的 hold 不再拦（那是接管窗口）。
    """
    clock = [1000.0]
    base, hub, _srv = _boot(tmp_path, now_fn=lambda: clock[0])
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    tok = _json(
        _req(base, f"{OFFLINE_CLAIM_PATH}?proto=2&course=c5-gae&worker=w1", method="POST")[1]
    )["lease"]["token"]
    st, raw = _req(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")
    body = _json(raw)
    assert st == 409 and body["held"] is True and body["holder"]["worker_id"] == "w1", body
    st, _raw = _req(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae&lease={tok}")
    assert st == 200, _raw[:200]
    # stale ⇒ 放行（新主会在取包前先 claim；旧客户端在这里也拿得到，包旧只是起点旧）
    clock[0] += 1000.0
    assert _req(base, f"{OFFLINE_TASK_PACK_PATH}?course=c5-gae")[0] == 200


def test_tasks_route_serves_the_same_rows_as_the_list(tmp_path: Path) -> None:
    """★M4b：`GET /offline/tasks` 与 `hub.offline_tasks()` 逐字段同源（清单面无第二份真相），
    且行里没有模式键（HTTP 面也走同一条守卫）。"""
    base, hub, _srv = _boot(tmp_path)
    _course(tmp_path, hub, "c5-gae")
    _pack(tmp_path, "c5-gae")
    st, raw = _req(base, OFFLINE_TASKS_PATH)
    body = _json(raw)
    assert st == 200
    rows = body["tasks"] if isinstance(body.get("tasks"), list) else body["courses"]
    got = {r["course"]: r for r in rows}
    want = {r["course"]: r for r in hub.offline_tasks()}
    assert set(got) == set(want)
    for dead in ("mode", "authority", "pinned", "seize", "auto_handoff"):
        assert dead not in got["c5-gae"], dead
