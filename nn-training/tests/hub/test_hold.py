"""tests/hub/test_hold.py — 接管（hold）判据 + `offline-dispatch.json` v2 双写（M1a）。

`plan/worker-type-dispatch-model.plan.md` §3-M1a（2026-10-07 起并入评审
`plan/worker-type-dispatch-model.review-bf.md` 的 F2/F8）。这一刀**只加判据与写入面**，
旧语义（mode/pinned/claimed_*）一字不动：

  * **纯判据**（住叶子 `hub/task_pack.py`，与 `lease_verdict` 同域）：`hold_progress_stale_sec()`
    · `hold_progress_at()` · `hold_state()`（只认进度，零进度 = stale）· `hold_restore_grace()`
    · `hold_expires_in()`（= min(进度余量, TTL 余量)）；
  * **落盘 v2**：`{v:2, …, hold:{worker_id,token,at,last_progress_at,touch_at},
    pending_export:{by,at}}` **与旧字段并存**（双写；读侧仍收 v1 形状，旧读方零变化）；
  * **`note_progress` 落盘节流 ≥60s**（内存每拍更新）——hub 重启最多丢 60s 龄，由恢复宽限吸收；
  * **恢复宽限**：盘上恢复的 hold 把 `last_progress_at`/`touch_at` 抬到 `now-300s` + 一行
    `hold-restored`（防「刚重启就把活着的 worker 判掉线」）；
  * **`pending_export` 是软态**：不建 hold、不占闸、读面超窗即视为没有（F8：惰性过期，
    不写盘清理 —— 崩溃的导包者不该留下永久「有人在导包」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.protocol import COURSE_ENABLE_MARKER
from hub.queue_offline import (
    AUTO_HANDOFF_PENDING_SEC,
    DISPATCH_VERSION,
    HOLD_PROGRESS_PERSIST_SEC,
    dispatch_record_default,
    dispatch_record_merge,
    hold_record_merge,
    pending_export_record_merge,
)
from hub.server import _HubQueue
from hub.task_pack import (
    HOLD_PROGRESS_STALE_SEC,
    HOLD_RESTORE_GRACE_SEC,
    hold_expires_in,
    hold_progress_at,
    hold_progress_stale_sec,
    hold_restore_grace,
    hold_state,
    hold_touch_at,
)

COURSE = "c5-gae"
STALE_ENV = "BCITY_HOLD_PROGRESS_STALE_SEC"


def _course(tmp_path: Path, hub: _HubQueue, course: str = COURSE) -> None:
    """造一门被 hub 发现的课（目录 + 账本 + 开课标记）——与 `test_auto_handoff` 同款。"""
    (tmp_path / course / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (tmp_path / course / "training_log.jsonl").touch()
    (tmp_path / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub.discover(force=True)


def _hub(tmp_path: Path, clock: list[float]) -> _HubQueue:
    """一个可注入时钟的 hub（阈值/节流的用例全靠它把时间推着走）。"""
    return _HubQueue({}, discover_root=tmp_path, now_fn=lambda: clock[0])


def _disk(tmp_path: Path, course: str = COURSE) -> dict:
    raw = (tmp_path / course / "offline-dispatch.json").read_text(encoding="utf-8")
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}


# ------------------------------------------------------------------ 纯判据


def test_hold_progress_threshold_is_env_overridable_and_never_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """阈值：缺省 900s；env 覆盖（e2e/单测调秒级）；非法 / 非正 ⇒ 回缺省（绝不 0）。"""
    monkeypatch.delenv(STALE_ENV, raising=False)
    assert HOLD_PROGRESS_STALE_SEC == 900.0
    assert hold_progress_stale_sec() == 900.0
    # ★ 与导包窗口**巧合同值、语义不同**（plan §1.5.2-P0-1 禁止合并常量）：两个名字都得在，
    #   调一个不能误伤另一个 —— 这条断言就是「它们是两份」的可执行版本。
    assert AUTO_HANDOFF_PENDING_SEC == 900.0
    monkeypatch.setenv(STALE_ENV, "10")
    assert hold_progress_stale_sec() == 10.0
    for bad in ("", "abc", "0", "-3"):
        monkeypatch.setenv(STALE_ENV, bad)
        assert hold_progress_stale_sec() == 900.0, bad


def test_hold_state_reads_progress_only_and_treats_zero_progress_as_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活性只认进度：心跳不参与；零进度 = stale（宽限兜「刚接管」而不是把零当活）。"""
    monkeypatch.delenv(STALE_ENV, raising=False)
    assert hold_state(1000.0, None) == "" and hold_state(1000.0, {}) == ""
    assert hold_state(1000.0, {"last_progress_at": 990.0}) == "live"
    # 边界：恰好 900s 仍算活（判据是「超过」）
    assert hold_state(1000.0, {"last_progress_at": 100.0}) == "live"
    assert hold_state(1000.0, {"last_progress_at": 99.9}) == "stale"
    assert hold_state(1000.0, {"worker_id": "w1"}) == "stale"
    # 值不合法 / 没有字段 ⇒ 缺失哨兵 `-1.0`（⇒ stale），不抛。
    # ★M1b：哨兵从 0.0 改成 -1.0 —— `0.0` 既是「没写」也是「真的 0 点」（假时钟从 0 起步的
    # 用例里，刚 claim 的进度锚会被读成没进度，活跃的 hold 当场被判 stale）。
    assert hold_progress_at({"last_progress_at": "x"}) == -1.0
    assert hold_progress_at(None) == -1.0
    assert hold_progress_at({"last_progress_at": 0.0}) == 0.0
    assert hold_touch_at({"at": 0.0}) == 0.0  # 键在就是真值（不再要求 > 0）
    assert hold_touch_at({}) == -1.0
    # env 调秒级 ⇒ 判据跟着走（同一条腿，不是两份阈值）
    monkeypatch.setenv(STALE_ENV, "10")
    assert hold_state(1000.0, {"last_progress_at": 995.0}) == "live"
    assert hold_state(1000.0, {"last_progress_at": 989.0}) == "stale"


def test_hold_restore_grace_raises_old_contacts_to_now_minus_grace() -> None:
    """恢复宽限：只抬不压；抬过之后仍判活（重启不误杀活着的 worker）。"""
    assert HOLD_RESTORE_GRACE_SEC == 300.0
    assert hold_restore_grace(1000.0, 0.0) == 700.0
    assert hold_restore_grace(1000.0, 100.0) == 700.0
    assert hold_restore_grace(1000.0, 950.0) == 950.0
    assert hold_state(1000.0, {"last_progress_at": hold_restore_grace(1000.0, 1.0)}) == "live"


def test_hold_expires_in_is_min_of_progress_and_ttl_remainder() -> None:
    """P2-3：读面那一列仍是「剩余秒」，但语义 = min(进度余量, TTL 余量)，永不负数。"""
    kw = {"ttl_sec": 900.0, "stale_sec": 900.0}
    fresh = {"at": 1000.0, "touch_at": 1000.0, "last_progress_at": 1000.0}
    assert hold_expires_in(1000.0, fresh, **kw) == 900.0
    # 心跳把 TTL 推着走（touch_at 新）而进度停了 ⇒ 报**进度**余量
    hb = {"at": 1000.0, "touch_at": 1400.0, "last_progress_at": 1000.0}
    assert hold_expires_in(1400.0, hb, **kw) == 500.0
    # 进度在动、TTL 老 ⇒ 报 TTL 余量
    prog = {"at": 1000.0, "touch_at": 1000.0, "last_progress_at": 1450.0}
    assert hold_expires_in(1450.0, prog, **kw) == 450.0
    assert hold_expires_in(9999.0, fresh, **kw) == 0.0
    assert hold_expires_in(1000.0, {}, **kw) == 0.0


# ------------------------------------------------------------------ v2 形状


def test_dispatch_record_merge_reads_v1_and_normalizes_hold_shapes() -> None:
    """读侧 tolerate 到底：v1 文件（没有新键）照读；垃圾 hold 不造幽灵接管。"""
    v1 = dispatch_record_merge({"v": 1, "mode": "offline", "claimed_by": "w1"}, "online")
    assert v1["hold"] == {} and v1["pending_export"] == {}
    assert v1["mode"] == "offline" and v1["claimed_by"] == "w1"
    assert dispatch_record_default("offline")["v"] == DISPATCH_VERSION == 2
    assert dispatch_record_merge(None, "offline") == dispatch_record_default("offline")
    junk = dispatch_record_merge({"hold": "x", "pending_export": [1]}, "online")
    assert junk["hold"] == {} and junk["pending_export"] == {}
    # 全空字段的 hold 记录 = 没有 hold（不让一个坏 dict 变成「有人持有」）
    assert hold_record_merge({"worker_id": "", "at": 0}) == {}
    assert hold_record_merge({"last_progress_at": -1}) == {}
    merged = hold_record_merge(
        {"worker_id": 7, "token": None, "at": "x", "last_progress_at": -1}
    )
    assert merged == {
        "worker_id": "7",
        "token": "",
        "at": 0.0,
        "last_progress_at": 0.0,
        "touch_at": 0.0,
    }
    assert pending_export_record_merge({"by": "", "at": 0}) == {}
    assert pending_export_record_merge({"by": 9, "at": "x"}) == {"by": "9", "at": 0.0}


# ------------------------------------------------------------------ 写入 / 读数


def test_note_hold_writes_v2_keeps_old_fields_and_clears_pending_export(
    tmp_path: Path,
) -> None:
    """双写：新键落盘 + 旧键一个不少（旧读方零变化）；hold 建立 = 包到手 ⇒ 清软态。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_pending_export(COURSE, by="w9")
    assert hub.pending_export_of(COURSE)["by"] == "w9"

    hub.note_hold(COURSE, worker_id="w1", token="tok-a")
    disk = _disk(tmp_path)
    assert disk["v"] == DISPATCH_VERSION == 2
    assert disk["hold"]["worker_id"] == "w1" and disk["hold"]["token"] == "tok-a"
    assert disk["hold"]["at"] == 1000.0
    assert disk["hold"]["last_progress_at"] == 1000.0  # claim 本身是第一个进度锚
    assert disk["hold"]["touch_at"] == 1000.0
    assert disk["pending_export"] == {} and hub.pending_export_of(COURSE) == {}
    # ★ 旧字段并存（旧读者零变化）
    for key in ("mode", "pinned", "claimed_offline", "claimed_by", "claimed_at", "flipped_at"):
        assert key in disk, key
    assert hub.pinned_of(COURSE) is False
    assert hub.dispatch_effective_mode(COURSE, "online") == "online"
    hold = hub.hold_of(COURSE)
    assert hold["state"] == "live" and hold["worker_id"] == "w1"
    assert hold["expires_in"] == 900.0
    # 旧写者（claim 记账）不会把新键抹掉
    hub.note_claim(COURSE, "w1")
    again = _disk(tmp_path)
    assert again["hold"]["token"] == "tok-a" and again["claimed_by"] == "w1"


def test_note_progress_throttles_disk_writes_but_keeps_memory_fresh(tmp_path: Path) -> None:
    """打点：内存每拍更新，落盘 ≥60s 一次；节流基准是**盘上那份**（不是内存）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_hold(COURSE, worker_id="w1", token="tok")

    clock[0] = 1010.0
    assert hub.note_progress(COURSE) is False  # < 60s ⇒ 只改内存
    assert _disk(tmp_path)["hold"]["last_progress_at"] == 1000.0
    assert hub.hold_of(COURSE)["last_progress_at"] == 1010.0

    clock[0] = 1070.0
    assert hub.note_progress(COURSE) is True  # ≥ 60s ⇒ 落盘
    disk = _disk(tmp_path)
    assert disk["hold"]["last_progress_at"] == 1070.0
    assert disk["hold"]["touch_at"] == 1070.0  # 进度也是「合法接触」
    assert disk["updated_at"] == 1070.0
    assert HOLD_PROGRESS_PERSIST_SEC == 60.0


def test_progress_ping_from_a_stale_holder_cannot_revive_the_old_hold(tmp_path: Path) -> None:
    """换主竞态：旧主的 ping 不带门就会把旧 hold 复活（新主的独占被一道过期 ping 解除）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_hold(COURSE, worker_id="w1", token="tok-old")
    hub.note_hold(COURSE, worker_id="w2", token="tok-new")  # 换主
    clock[0] = 1200.0
    assert hub.note_progress(COURSE, token="tok-old") is False  # 旧令牌：整笔作废
    assert hub.hold_of(COURSE)["token"] == "tok-new"
    # 盘上仍停在**新主接管那一刻**（旧主的 ping 一个字都没写）
    assert _disk(tmp_path)["hold"]["last_progress_at"] == 1000.0
    assert hub.note_progress(COURSE, token="tok-new") is True
    assert hub.hold_of(COURSE)["last_progress_at"] == 1200.0


def test_progress_without_hold_is_a_noop_and_never_creates_one(tmp_path: Path) -> None:
    """hold 只由 claim 建（Q1）：打点不建 hold、不写盘。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    assert hub.note_progress(COURSE) is False
    assert hub.hold_of(COURSE) == {}
    assert not (tmp_path / COURSE / "offline-dispatch.json").exists()


def test_hold_persists_across_restart_with_grace_and_logs_hold_restored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """重启：盘上那份照读；超阈的进度被宽限抬到 now-300 ⇒ 仍判活（不误杀）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_hold(COURSE, worker_id="w1", token="tok")
    rec = _disk(tmp_path)
    rec["hold"]["last_progress_at"] = 1000.0 - 5000.0
    (tmp_path / COURSE / "offline-dispatch.json").write_text(
        json.dumps(rec), encoding="utf-8"
    )

    clock[0] = 9000.0
    hub2 = _hub(tmp_path, clock)
    _course(tmp_path, hub2)
    hold = hub2.hold_of(COURSE)
    out = capsys.readouterr().out
    assert "hold-restored" in out, out
    assert hold["last_progress_at"] == pytest.approx(8700.0)  # now - 300
    assert hold["touch_at"] == pytest.approx(8700.0)
    assert hold["state"] == "live"
    assert hold["expires_in"] == pytest.approx(600.0)  # min(进度 600, TTL 1200)
    # 落盘节流的基准取盘上的 `updated_at`（1000.0）⇒ 重启后第一拍就能刷
    assert hub2.note_progress(COURSE) is True
    assert _disk(tmp_path)["hold"]["last_progress_at"] == 9000.0


def test_pending_export_is_soft_and_expires_lazily(tmp_path: Path) -> None:
    """软态：不建 hold、不占闸；超窗的读面视为没有（惰性，不写盘清理）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_pending_export(COURSE, by="w9")
    assert hub.pending_export_of(COURSE) == {"by": "w9", "at": 1000.0}
    assert hub.hold_of(COURSE) == {}
    clock[0] = 1000.0 + AUTO_HANDOFF_PENDING_SEC + 1.0
    assert hub.pending_export_of(COURSE) == {}
    # 盘上那份还在（软态不是事实源，超窗只是不再当它存在）
    assert hub.dispatch_record(COURSE)["pending_export"]["by"] == "w9"
