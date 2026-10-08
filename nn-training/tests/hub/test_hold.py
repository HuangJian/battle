"""tests/hub/test_hold.py — 接管（hold）判据 + `offline-dispatch.json` v2（M1a/M4b）。

`plan/worker-type-dispatch-model.plan.md` §3-M1a（2026-10-07 起并入评审
`plan/worker-type-dispatch-model.review-bf.md` 的 F2/F8）。★M4b：旧语义
（mode/pinned/claimed_*）已随「课程无模式」删除 —— 盘上旧键**读到即忽略**，写出只剩 v2 形状：

  * **纯判据**（住叶子 `hub/task_pack.py`，与 `lease_verdict` 同域）：`hold_progress_stale_sec()`
    · `hold_progress_at()` · `hold_state()`（只认进度，零进度 = stale）· `hold_restore_grace()`
    · `hold_expires_in()`（= min(进度余量, TTL 余量)）；
  * **落盘 v2**：`{v:2, completed_pack_sha, hold:{worker_id,token,at,last_progress_at,touch_at},
    pending_export:{by,at}, updated_at}`（读侧照样收 v1 形状）；
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
    """读侧 tolerate 到底：v1 文件（没有新键）照读；垃圾 hold 不造幽灵接管。

    ★M4b：v1/v2 盘上的 mode/pinned/claimed_* 旧键**读到即忽略**（不再进结果、不报错）。
    """
    v1 = dispatch_record_merge({"v": 1, "mode": "offline", "claimed_by": "w1"})
    assert v1["hold"] == {} and v1["pending_export"] == {}
    assert "mode" not in v1 and "claimed_by" not in v1 and "pinned" not in v1
    assert dispatch_record_default()["v"] == DISPATCH_VERSION == 2
    assert dispatch_record_merge(None) == dispatch_record_default()
    junk = dispatch_record_merge({"hold": "x", "pending_export": [1]})
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


def test_note_hold_writes_v2_and_clears_pending_export(tmp_path: Path) -> None:
    """hold 建立 = 包到手 ⇒ 清软态；落盘是**纯 v2 形状**（★M4b：旧 mode 键一个不写）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_pending_export(COURSE, by="w9")
    assert hub.pending_export_of(COURSE)["by"] == "w9"

    hub.note_hold(COURSE, worker_id="w1", token="tok-a")
    disk = _disk(tmp_path)
    assert disk["v"] == DISPATCH_VERSION == 2
    assert set(disk) == {"v", "completed_pack_sha", "hold", "pending_export", "updated_at"}
    assert disk["hold"]["worker_id"] == "w1" and disk["hold"]["token"] == "tok-a"
    assert disk["hold"]["at"] == 1000.0
    assert disk["hold"]["last_progress_at"] == 1000.0  # claim 本身是第一个进度锚
    assert disk["hold"]["touch_at"] == 1000.0
    assert disk["pending_export"] == {} and hub.pending_export_of(COURSE) == {}
    hold = hub.hold_of(COURSE)
    assert hold["state"] == "live" and hold["worker_id"] == "w1"
    assert hold["expires_in"] == 900.0


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


def test_pending_export_anchor_is_first_write_and_only_a_new_exporter_resets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★M1b：导包意向的 `at` **首写为准**（同一位重复 claim/轮询不刷新）——它是停滞告警的锚点。

    为什么必须钉：云机在等包时会**反复** claim（`begin_pending_export` 每拍都跑）。若每次
    claim 都把 `at` 推到现在，「已翻 offline 却没人跑」永远算「刚刚才说」⇒ T8 的告警在
    这正是最需要它的时候脑死。换主 ⇒ 新一轮导包，重新计时。
    """
    monkeypatch.setenv("BCITY_AUTO_HANDOFF_PENDING_SEC", "100")
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    hub.note_pending_export(COURSE, by="w9")
    clock[0] = 1050.0  # 同一位、窗内 ⇒ 不刷新
    hub.note_pending_export(COURSE, by="w9")
    assert hub.pending_export_of(COURSE) == {"by": "w9", "at": 1000.0}
    clock[0] = 1100.0  # 换主（另一台云机）⇒ 新一轮，重新计时
    hub.note_pending_export(COURSE, by="w10")
    assert hub.pending_export_of(COURSE) == {"by": "w10", "at": 1100.0}
    clock[0] = 1100.0 + 200.0  # 超窗后同一位回来（上一轮早作废）⇒ 也重新计时
    hub.note_pending_export(COURSE, by="w10")
    assert hub.pending_export_of(COURSE) == {"by": "w10", "at": 1300.0}


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


# ──────────────────────── 派发闸的第三层（★M1b / Q5） ────────────────────────

#: 本文件的 job manifest 最小形状（与 `test_role_routing` 同源：账本/租约不关心内容）。
def _manifest(jid: str, *, kind: str = "ppo") -> dict:
    return {
        "proto": 1,
        "runId": "run-x",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "payload_sha256": "p" * 64,
        "kind": kind,
        "course": "// course\n{}",
    }


def test_store_gate_windows_match_task_pack_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`store_leases` 里那份窗常量是**抄本**（它不许 import 调度上层）⇒ 这份对账必须有。"""
    import hub.store_leases as sl
    from hub.server import _JobStore
    from hub.task_pack import HOLD_PROGRESS_STALE_SEC as TP_STALE

    assert sl.HOLD_STALE_SEC == TP_STALE == 900.0
    assert sl.HOLD_STALE_ENV == STALE_ENV
    # 缺省与 env 两条路都对得上（同一把尺子：窗在两边都必须能调）
    store = _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")
    monkeypatch.delenv(STALE_ENV, raising=False)
    assert store._hold_stale_sec() == 900.0
    monkeypatch.setenv(STALE_ENV, "7")
    assert store._hold_stale_sec() == 7.0


def test_live_hold_blocks_every_kind_until_it_goes_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q5 ③：接管期间这门课的活**谁都别碰**（含接管者自己）；stale 即放行。"""
    monkeypatch.setenv(STALE_ENV, "10")
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    st.publish("j" * 16, _manifest("j" * 16), b"PK\x03\x04fake")
    hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
    out = st.claim_outcome("j" * 16, worker_id="online-1", role="online")
    assert out.ok is False and out.reason == "held:cloud-1", out
    # 接管者自己也不许领（它此刻在跑离线段）——闸不看请求方是谁
    out = st.claim_outcome("j" * 16, worker_id="cloud-1", role="online")
    assert out.ok is False and out.reason == "held:cloud-1", out
    # 进度静默超阈 ⇒ stale ⇒ 闸自己开（不必等人来解）
    clock[0] += 20.0
    out = st.claim_outcome("j" * 16, worker_id="online-1", role="online")
    assert out.ok is True, out


def test_bc_job_is_exempt_from_the_role_gate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q5 ①②③：`kind=bc` 只豁免角色闸；**接管闸照样吃它**（顺序不许调换）。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    st.publish("k" * 16, _manifest("k" * 16, kind="bc"), b"PK\x03\x04fake")
    # bc 的 kind 归属是 online（`KIND_ROLES`），但离线盘也能领 —— 这正是一刀「双角色」。
    out = st.claim_outcome("k" * 16, worker_id="off-1", role="offline")
    assert out.ok is True, out
    st.abandon_job("k" * 16)
    # 非 bc 的对照：同一份活换成 ppo ⇒ 角色闸拒绝（豁免是 bc 专属）
    st.publish("m" * 16, _manifest("m" * 16, kind="ppo"), b"PK\x03\x04fake")
    out = st.claim_outcome("m" * 16, worker_id="off-1", role="offline")
    assert out.ok is False and out.reason == "role", out
    # 接管闸吃 bc：有个活 hold 时同一个 bc 作业也领不走
    hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
    out = st.claim_outcome("k" * 16, worker_id="off-1", role="offline")
    assert out.ok is False and out.reason == "held:cloud-1", out


def test_publish_invalidates_the_kind_cache(tmp_path: Path) -> None:
    """重发会换 manifest（可能换了 kind）⇒ 缓存必须跟着失效，否则闸拿旧 kind 判新 job。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    st.publish("k" * 16, _manifest("k" * 16, kind="bc"), b"PK\x03\x04fake")
    assert st.job_kind("k" * 16) == "bc"
    assert st.job_kind("nope-not-on-disk") == "ppo", "读不到 manifest ⇒ 缺省 ppo（与 role_of 同兜底）"
    # 同一个 job_id 重发成 ppo ⇒ 离线盘立刻被角色闸拦住（旧缓存必须已清）
    st.publish("k" * 16, _manifest("k" * 16, kind="ppo"), b"PK\x03\x04fake")
    assert st.job_kind("k" * 16) == "ppo"
    out = st.claim_outcome("k" * 16, worker_id="off-1", role="offline")
    assert out.ok is False and out.reason == "role", out


# ──────────────────────── ★M5：BC 独占（需求 7 / Q5） ────────────────────────


def test_bc_never_gets_a_backup_replica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★M5：BC 「领取后独占」——备份副本被**当面拒**；ppo 的备份语义一点不动。"""
    from common.protocol import CLAIM_MODE_BACKUP

    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    st.publish("k" * 16, _manifest("k" * 16, kind="bc"), b"PK\x03\x04fake")
    assert st.claim_outcome("k" * 16, worker_id="online-1", role="online").ok is True
    out = st.claim_outcome(
        "k" * 16, mode=CLAIM_MODE_BACKUP, worker_id="online-9", role="online"
    )
    assert out.ok is False and out.reason == "no_backup", out
    # 对照：同样的路子给 ppo 发备份是成立的（R2-3 的显式授权腿不许被误伤）
    st.publish("m" * 16, _manifest("m" * 16, kind="ppo"), b"PK\x03\x04fake")
    assert st.claim_outcome("m" * 16, worker_id="online-1", role="online").ok is True
    out = st.claim_outcome(
        "m" * 16, mode=CLAIM_MODE_BACKUP, worker_id="online-9", role="online"
    )
    assert out.ok is True and out.status == "backup", out
    # 前置：bc 确实在飞（否则下面那条什么都没验到）；而在飞面里它**不进**备份候选
    assert "k" * 16 in st.inflight_job_ids(not_held_by="online-9")
    assert "k" * 16 not in [
        j["job_id"] for j in hub.peek_jobs(worker_id="online-9", role="online")
    ], "bc 不做备份：peek 不许把它摆出来（客户端少一趟无功往返）"
    # 对照：ppo 的在飞作业照旧进备份候选（peek 的备份腿没被误伤）
    assert "m" * 16 in [
        j["job_id"] for j in hub.peek_jobs(worker_id="online-9", role="online")
    ]


def test_bc_liveness_is_progress_only_and_a_dead_lease_is_conceded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★M5：bc 判活**只认 epoch**（心跳不算）；超窗 ⇒ 孤儿 ⇒ 让出（别人立刻能领）。"""
    import hub.store_leases as sl
    from hub.task_pack import HOLD_PROGRESS_STALE_SEC

    #: 两个窗巧合同值、语义不同（P0-1 禁合并）——两个名字都得在，且都可由 env 调。
    assert sl.BC_PROGRESS_STALE_SEC == 900.0 == HOLD_PROGRESS_STALE_SEC
    monkeypatch.setenv(sl.BC_PROGRESS_STALE_ENV, "10")
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    jid = "k" * 16
    st.publish(jid, _manifest(jid, kind="bc"), b"PK\x03\x04fake")
    out = st.claim_outcome(jid, worker_id="off-1", role="offline")
    assert out.ok is True and out.token, out
    assert st.lease_worker(jid) == "off-1"
    # 心跳把 TTL 推着走（每 5s 一跳），但**一次 epoch 都不打**
    for t in (1005.0, 1010.0, 1015.0):
        clock[0] = t
        assert st.heartbeat(jid, out.token) is True
    clock[0] = 1030.0  # 距 claim 30s > 10s 窗 ⇒ 心跳活、进度死 —— 判死（§68 的教训）
    assert st.lease_worker(jid) == "", "心跳不算活性：超窗必须让出"
    assert st.reclaims(jid) == 1, "回收与过期同路：毒包计数 +1"
    events = [
        json.loads(ln).get("event")
        for ln in st.jsonl_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert events.count("lease-orphan-reaped") == 1, (
        "孤儿回收必须在账本里留凭据（health 面就是靠它证明队列诚实）"
    )
    # 让出后可被别人领；新主拿到的是**新**租约（进度锚重写）
    clock[0] = 1031.0
    out2 = st.claim_outcome(jid, worker_id="off-2", role="offline")
    assert out2.ok is True, out2
    assert st.lease_worker(jid) == "off-2"
    assert st.job_progress_at(jid) == 1031.0
    # 打点续命：下一次 epoch 把窗重新推满（同一条腿，不是两份阈值）
    st.note_job_progress(jid)
    clock[0] = 1040.0
    assert st.bc_drain_of("off-2") == jid
    clock[0] = 1042.0
    assert st.bc_drain_of("off-2") == "", "超窗 ⇒ 不再是 drain（卡死的 bc 不许把盘钉住）"


def test_hold_and_bc_are_mutually_exclusive_in_both_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★M5/Q5：一台盘同一时刻至多占一样——带 hold 的领不到 BC，在跑 BC 的领不到课程。"""
    import hub.store_leases as sl

    monkeypatch.setenv(STALE_ENV, "600")  # hold 很长命（本用例不许它自然过期）
    monkeypatch.setenv(sl.BC_PROGRESS_STALE_ENV, "600")
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub, COURSE)
    _course(tmp_path, hub, "c9-bc")
    #: 课 A 上的 hold（自主盘 off-1 在跑）+ 课 B 上的一个 bc 作业
    hub.note_hold(COURSE, worker_id="off-1", token="tok-a")
    stb = hub._stores["c9-bc"]
    jid = "b" * 16
    stb.publish(jid, _manifest(jid, kind="bc"), b"PK\x03\x04fake")
    # 方向①：带着 A 的 hold 去领 B 的 bc ⇒ 拒（跨课程腿，住 hub 层）
    out = hub.claim_job(jid, worker_id="off-1", role="offline")
    assert out.ok is False and out.reason == f"holding:{COURSE}", out
    # 同源：peek 也不把它摆给这台盘（“清单是上界”的同上口径）
    assert jid not in [j["job_id"] for j in hub.peek_jobs(worker_id="off-1", role="offline")]
    assert jid in [j["job_id"] for j in hub.peek_jobs(worker_id="off-2", role="offline")]
    # 别的盘能领（闸是按 worker 的，不是把活冻住）
    assert hub.claim_job(jid, worker_id="off-2", role="offline").ok is True
    # 方向②：这台（现在在跑 bc 的）盘去接课程 ⇒ busy（`_busy_locked` 的另一半）
    lease, why = hub.claim_offline(COURSE, "off-2")
    assert (lease, why) == ({}, "foreign")  # A 上还有别人的活 hold（先证“不是被这两条闸拒的”）
    hub.note_release(COURSE)  # 课 A 空出来
    assert hub.busy_reason(COURSE, "off-2").startswith("busy:"), hub.busy_reason(COURSE, "off-2")
    lease, why = hub.claim_offline(COURSE, "off-2")
    assert (lease, why) == ({}, "busy"), (lease, why)
    assert hub.worker_bc_drain("off-2") == ("c9-bc", jid)
    # bc 进度超窗（= 让出）⇒ 课程接管自动恢复（惰性判据，无清理线程）
    clock[0] += 601.0
    assert hub.worker_bc_drain("off-2") == ("", "")
    lease, why = hub.claim_offline(COURSE, "off-2")
    assert why == "" and lease.get("token"), (lease, why)


def test_hold_mirror_is_pushed_to_the_store_and_reads_without_the_token(
    tmp_path: Path,
) -> None:
    """闸的输入是**镜像**（`_sync_hold` 推）：`note_hold` 当拍生效，且清单面不外露 token。"""
    clock = [1000.0]
    hub = _hub(tmp_path, clock)
    _course(tmp_path, hub)
    st = hub._stores[COURSE]
    assert st.hold_meta == {}
    hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
    assert st.hold_meta == {"worker_id": "cloud-1", "last_progress_at": 1000.0}, st.hold_meta
    assert st.hold_blocked() == "held:cloud-1"
    # 打点把进度锚推着走（闸跟着续命）
    clock[0] = 1500.0
    assert hub.note_progress(COURSE, token="tok-1") is True
    assert st.hold_meta["last_progress_at"] == 1500.0
    # 清单面（`/admin/queue`）看得到 hold、看不到 token
    row = hub.queue_state()["courses"][COURSE]
    assert row["hold"]["worker_id"] == "cloud-1" and row["hold"]["state"] == "live"
    assert "token" not in row["hold"], row["hold"]
    # release / revoke 清镜像（清完协作派发当天恢复）
    hub.note_release(COURSE)
    assert st.hold_meta == {} and st.hold_blocked() == ""
    hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
    assert hub.revoke_offline_lease(COURSE, "test") is True
    assert st.hold_meta == {} and hub.hold_of(COURSE) == {}
