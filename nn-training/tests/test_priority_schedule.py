"""test_priority_schedule.py — 传输∥PPO 的优先级调度面（plan/transfer-scheduling，2026-09-22）。

本文件是**新调度面**的门禁（`/jobs/next` 竞速判定那条腿的老用例仍在
`test_race_broadcast.py`，两者并存过渡期；P3 删竞速时后者整文件消失、由本文件承接）：

  * §1.4 优先级表 = 纯函数五分支（`protocol.job_priority`），**时基只认 `computing_at`**
    ——「claim 之后下载了 5 分钟」不算掉队（R2-C1：拿 claim 起算会把慢链路误判成慢计算，
    于是多开备份把本来就慢的链路压得更死）。
  * `GET /jobs/peek`：**不认领**（无租约、无副作用、不动游标、不改可领取池）R1-4；
    halt 达令同行（承接退役的 `/jobs/next`）；离线课的能力闸照旧。
  * `claim(mode="backup")`（R1-1 + R2-3）：无租约、**不动原持有者的租约**、
    授权「你的回传不吃 403」、不产 reclaim / 不进 stale 名单。
  * `abandon`（R1-3）：租约即释 + 零 reclaim（否则 TTL 过期 ⇒ 三度冻结成毒包）。
  * highest 唯一性闸（R1-5）：N 个 worker 同拍问询 + claim ⇒ 仅 1 份 highest。
  * `post_result(mode="backup")` 收 403 = **丢弃**而非 `ProtocolError`（R1-2：
  漏了它 = 一个赢家把输家炸成训练停腿的事故）。
  * worker 取活三件套 `acquire_job` = peek → priority（none 必弃）→ claim（demoted 换下家）。
"""

from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.worker as W
from remote.hub_server import _HubQueue, _JobStore, make_server
from remote.protocol import (
    CLAIM_MODE_BACKUP,
    CLAIM_MODE_EXCLUSIVE,
    CLAIM_TTL_SEC,
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    COURSE_MODE_ONLINE,
    JOB_CANCEL_POLL_SEC,
    PAYLOAD_NAME,
    PRIORITY_HIGH,
    PRIORITY_HIGHEST,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    PRIORITY_NONE,
    STRAGGLER_SEC,
    JobCancelledError,
    ProtocolError,
    RetryableError,
    job_priority,
)

TOKEN = "sekret"
JID = "j" * 16


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _mini_manifest(jid: str = JID, course: str = "c5-gae") -> dict:
    return {
        "proto": 1,
        "runId": "test-run",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 1,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }


def _store(tmp_path: Path, clock: _Clock) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=clock)


def _publish(store: _JobStore, jid: str = JID) -> None:
    """在**给定 store** 上发布一份 pending job（store 层用例要让账本与租约同源：
    `claimable_job_ids` 读账本，写到另一份 store 上会得到空池——本文件初版就这么错过）。"""
    store.publish(jid, _mini_manifest(jid), b"PK\x03\x04fake")
    assert (store._job_dir(jid) / PAYLOAD_NAME).exists()


def _publish_online_course(root: Path, course: str = "c5-gae", jid: str = JID) -> None:
    """盘上造一门**已开课**的在线课（开课标记 + `remote-jobs/` + jsonl + 一个 pending job）。"""
    job_root = root / course / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    (root / course / "training_log.jsonl").touch()
    (root / course / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    _JobStore(job_root, root / course / "training_log.jsonl").publish(
        jid, _mini_manifest(jid, course), b"PK\x03\x04fake"
    )


@contextmanager
def _hub(tmp_path: Path):
    """进程内真 HTTP hub（真路由 + 真鉴权 + 真 JSON 面）——新调度面的端到端夹具。

    先 `discover()`：`peek_jobs` 自己会扫，而 `priority`/`claim`/`set_mode` 都靠
    `_store_of(job_id)` **已发现**的课程表定位（否则全是 unknown job ⇒ 409/none）。
    """
    hub = _HubQueue({}, discover_root=tmp_path)
    hub.discover()
    srv: ThreadingHTTPServer = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", hub
    finally:
        srv.shutdown()
        srv.server_close()


# ═══════════════════════════ §1.4 优先级表（纯函数） ═══════════════════════════


def test_job_priority_five_branches() -> None:
    """五行映射逐条钉死（§1.4 表；观测面与 RPC 共用同一实现）。"""
    now = 10_000.0
    assert job_priority(
        landed=True, ready_elsewhere=False, claimed_elsewhere=True,
        computing_elsewhere_since=now - 1, now=now,
    ) == PRIORITY_NONE, "landed = 无优先级（唯一硬闸：放弃）"
    assert job_priority(
        landed=False, ready_elsewhere=False, claimed_elsewhere=True,
        computing_elsewhere_since=now - (STRAGGLER_SEC + 1), now=now,
    ) == PRIORITY_HIGH, "别处在算且超阈值 = 掉队救援"
    assert job_priority(
        landed=False, ready_elsewhere=False, claimed_elsewhere=True,
        computing_elsewhere_since=now - 1, now=now,
    ) == PRIORITY_MEDIUM, "别处在算未超阈值 = 中档备份"
    assert job_priority(
        landed=False, ready_elsewhere=False, claimed_elsewhere=True,
        computing_elsewhere_since=None, now=now,
    ) == PRIORITY_MEDIUM, "有人承诺在跑（还没开算）= 中档（永不升 high）"
    assert job_priority(
        landed=False, ready_elsewhere=True, claimed_elsewhere=True,
        computing_elsewhere_since=now - 1, now=now,
    ) == PRIORITY_LOW, "ready 是 computing 的后一阶段：它把优先级降到最低档"
    assert job_priority(
        landed=False, ready_elsewhere=False, claimed_elsewhere=False,
        computing_elsewhere_since=None, now=now,
    ) == PRIORITY_HIGHEST, "无人在做 = 最高（唯一性由 epoch 闸保证）"


def test_straggler_timebase_is_computing_at_not_claim(tmp_path: Path) -> None:
    """掉队判据只认 `computing_at`：claim 后下载 1 小时也不算「算得慢」（R2-C1）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    # 别人 exclusive claim 了这份 job（= 承诺在跑，可能还在下载）
    assert store.claim_outcome(JID, worker_id="other").ok is True
    clock.t += 10 * STRAGGLER_SEC  # 下载/解包慢到离谱
    assert store.priority_for(JID, exclude_worker="me")[0] == PRIORITY_MEDIUM, (
        "claim 起算 ⇒ 会把慢链路误判成慢计算（多开备份把链路压得更死）"
    )
    # 直到 `/start` 打点（PPO 真启动）才开始起算，越阈值才升 high
    store.start_job(JID, "other")
    assert store.priority_for(JID, exclude_worker="me")[0] == PRIORITY_MEDIUM
    clock.t += STRAGGLER_SEC + 1
    assert store.priority_for(JID, exclude_worker="me")[0] == PRIORITY_HIGH


def test_priority_excludes_asking_workers_own_marks(tmp_path: Path) -> None:
    """问询者**自己**的 computing 不算「别处在算」——否则每个 worker 都把自己判成中。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    store.claim_outcome(JID, worker_id="me")
    store.start_job(JID, "me")
    assert store.priority_for(JID, exclude_worker="me")[0] == PRIORITY_HIGHEST
    assert store.priority_for(JID, exclude_worker="other")[0] == PRIORITY_MEDIUM


# ═══════════════════════ claim(mode=) / abandon / highest 闸 ═══════════════════════


def test_backup_claim_takes_no_lease_and_does_not_steal(tmp_path: Path) -> None:
    """`mode="backup"`：无租约；**不动**原持有者的租约（R2-3，本轮评审推翻 pop 的写法）。

    pop 掉原租约的三个代价（为什么否决）：①原 worker 硬死后无租约可过期 ⇒ 毒包熔断
    失明；②job 立刻回池 ⇒ 第三/第四份可自由领取；③push 腿「hub 持租约防同一份活两处跑」
    的自保失效。所以备份只做一件事：**授权它的回传不吃 403**。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    a_token = store.claim_outcome(JID, mode=CLAIM_MODE_EXCLUSIVE, worker_id="A").token
    assert a_token
    expiry_before = store._leases[JID]

    out = store.claim_outcome(JID, mode=CLAIM_MODE_BACKUP, worker_id="B")
    assert out.ok is True and out.status == "backup" and out.token == ""
    assert store._leases[JID] == expiry_before, "原租约不得被动"
    assert store._lease_owners[JID] == a_token, "原持有人不得被换掉"
    # 双方都能回传：A 有租约 token；B 无租约但被显式授权 ⇒ 首写定胜负
    assert store.result_token_ok(JID, a_token) is True
    assert store.result_token_ok(JID, "") is True, "备份的回传不吃 403（R1-2）"
    # 未授权的第三方仍然 403（授权是**逐 job 显式**的，不是放开整条闸）
    assert store.result_token_ok("k" * 16, "") is True  # 未知 job：无租约照收（既有语义）
    store._backup_authorized.discard(JID)
    assert store.result_token_ok(JID, "") is False


def test_backup_claim_does_not_poison_or_reclaim(tmp_path: Path) -> None:
    """备份副本不进 `_stale_holders`、不产 `_reclaims`（§2.7：它是显式授权的重复计算）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    store.claim_outcome(JID, worker_id="A")
    store.claim_outcome(JID, mode=CLAIM_MODE_BACKUP, worker_id="B")
    assert store._stale_holders.get(JID, "") == ""
    assert store.reclaims(JID) == 0
    clock.t += CLAIM_TTL_SEC + 1
    assert store.claimable_job_ids() == [JID], "原租约照旧会过期（备份没把它 pop 掉）"
    assert store.reclaims(JID) == 0, "池过滤只判过期，回收计数发生在下一次领取时"
    # 下一个真领取者顶上来 → 原持有者那一次「零回传」照旧计毒。
    # （这就是 R2-3 否掉 `pop 原租约` 的原因：pop 掉之后原 worker 硬死无租约可过期
    #  ⇒ 熔断永久失明——这条断言就是那个失明的探针。）
    assert store.claim_outcome(JID, worker_id="C").ok is True
    assert store.reclaims(JID) == 1, "只该记原持有者那一次（备份没认领，不得追加）"


def test_abandon_releases_lease_and_zero_reclaim(tmp_path: Path) -> None:
    """`abandon` = release 租约 + 清可见性 + **零 reclaim**（R1-3）。

    漏掉 release 的代价：job 在 `CLAIM_TTL_SEC=300` 内被挡在池外，租约自然过期又走
    `_collect_expired_locked` ⇒ `_reclaims+1` ⇒ 三度达阈被冻成毒包（合法放弃被读成
    「认领后零回传」）。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    store.claim_outcome(JID, worker_id="A")
    store.start_job(JID, "A")
    assert store.claimable_job_ids() == []

    assert store.abandon_job(JID, "A") is True
    assert store.claimable_job_ids() == [JID], "abandon 后立即可领（不等 TTL）"
    assert store.reclaims(JID) == 0, "合法放弃不得计毒"
    assert store.priority_for(JID)[0] == PRIORITY_HIGHEST, "承诺痕迹随放弃撕掉"
    clock.t += CLAIM_TTL_SEC + 1
    assert store.reclaims(JID) == 0, "TTL 过期后仍是 0（没有任何零回传）"
    assert store.abandon_job(JID, "A") is True, "幂等"


def test_highest_single_holder_epoch_gate(tmp_path: Path) -> None:
    """N 个 worker 同拍问询 + claim ⇒ 仅 1 份 highest；后来者 `demoted`（R1-5）。

    「epoch 不匹配」**不是错误**：它是「有人比我快」的正常信号（不得转成
    `ProtocolError`、不得触发 `report_job_failure`）。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    epoch = store.scheduling_epoch()
    # 两个 worker 同拍问询：都看到 highest（问询是纯读，不该各自扣减）
    assert store.priority_for(JID, exclude_worker="A")[0] == PRIORITY_HIGHEST
    assert store.priority_for(JID, exclude_worker="B")[0] == PRIORITY_HIGHEST

    first = store.claim_outcome(JID, worker_id="A", expected_epoch=epoch)
    assert first.ok is True and first.status == "ok"
    second = store.claim_outcome(JID, worker_id="B", expected_epoch=epoch)
    assert second.ok is False and second.status == "demoted", "第二个必须降级为 low，不得抢"

    # 不带 expected_epoch 的老口径：也不是错误，只是「真的轮不到」（409）
    third = store.claim_outcome(JID, worker_id="C")
    assert third.ok is False and third.status == "held"
    # 唯一性仍成立：只有 A 留下承诺痕迹
    assert (store._claimed.get(JID) or {}).get("worker") == "A"


def test_claim_rejects_unknown_mode(tmp_path: Path) -> None:
    """mode 白名单（`CLAIM_MODES`）：写错就响亮拒收，不静默当独占。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    out = store.claim_outcome(JID, mode="race")  # type: ignore[arg-type]
    assert out.ok is False and out.status == "bad_mode"
    assert store._leases.get(JID) is None, "写错的模式绝不能静默降级成独占"


# ═══════════════════════════ HTTP 面：peek / priority / claim ═══════════════════════════


def test_peek_is_side_effect_free(tmp_path: Path) -> None:
    """R1-4：peek 不置租约、不动 `_cursor`、不改可领取池、不动 epoch。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        assert "c5-gae" in hub._stores  # 夹具已在建 hub 时 discover（见 `_hub`）
        before_pool = hub.claimable_job_ids("c5-gae")
        before_epoch = hub.epoch_of(JID)
        got = W.peek_jobs(base, TOKEN, worker_id="w1")
        assert got is not None
        cands, halt = got
        assert [c["job_id"] for c in cands] == [JID]
        assert cands[0]["course"] == "c5-gae" and cands[0]["payload_bytes"] > 0
        assert halt is False
        st = hub._store_of(JID)
        assert st is not None
        assert JID not in st._leases, "peek 绝不置租约（软持有的前提）"
        assert hub.claimable_job_ids("c5-gae") == before_pool
        assert hub.epoch_of(JID) == before_epoch, "peek 不改调度面版本"
        assert hub._cursor is None, "游标只由真正 claim 成功的一方推进（R2-C2）"


def test_peek_registers_worker_for_avoidance_chain(tmp_path: Path) -> None:
    """R2-2：`active_worker_count()`（避让链唯一输入）必须仍被喂——登记点从
    `/jobs/next` 搬到 peek/priority。漏了它 = 2026-09-18 的「超时回落队首改为推送
    其它 worker」静默消失，而纯函数单测测不出「调用点为 0」。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        assert hub.active_worker_count() == 0
        W.peek_jobs(base, TOKEN, worker_id="w1")
        W.peek_jobs(base, TOKEN, worker_id="w2")
        assert hub.active_worker_count() == 2
        W.request_priority(base, TOKEN, worker_id="w3", held=[JID])
        assert hub.active_worker_count() == 3


def test_peek_carries_halt_and_offline_gate(tmp_path: Path) -> None:
    """halt 达令同行（承接退役的 `/jobs/next`）；离线课仍只对带标 worker 可见。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        assert hub.set_mode("c5-gae", COURSE_MODE_OFFLINE) is True
        plain = W.peek_jobs(base, TOKEN, worker_id="plain")
        assert plain == ([], False), "无标：离线课不可见（也不是谁都领得到的池子）"
        marked = W.peek_jobs(base, TOKEN, worker_id="marked", offline_ok=True)
        assert marked is not None and [c["job_id"] for c in marked[0]] == [JID]
        assert hub.set_mode("c5-gae", COURSE_MODE_ONLINE) is True
        hub.set_halt(True)  # 停机达令（空 course = 全课程默认）
        halted = W.peek_jobs(base, TOKEN, worker_id="w1")
        assert halted is not None and halted[1] is True


def test_http_priority_claim_start_ready_flow(tmp_path: Path) -> None:
    """端到端：peek → priority(highest) → claim(ok) → start(computing_at) → ready(low)。

    这条链就是「谁该干活」的完整证据面；每一步的优先级变化都可从 `/jobs/priority`
    读回（观测行与判定共用一份实现）。
    """
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        pr = W.request_priority(base, TOKEN, worker_id="A", held=[JID])
        assert pr["priorities"][JID] == PRIORITY_HIGHEST
        assert isinstance(pr["epoch"], int) and pr["reasons"][JID]

        got = W.claim_job(base, TOKEN, JID, worker_id="A", expected_epoch=pr["epoch"])
        assert got is not None and got["status"] == "ok" and got["lease_token"]
        assert got["manifest"]["job_id"] == JID
        assert got["halt"] is False
        # 认领后：别人看到「有人承诺在跑」
        busy = W.request_priority(base, TOKEN, worker_id="B", held=[JID])
        assert busy["priorities"][JID] == PRIORITY_MEDIUM
        # 第二名拿同一个 epoch 来领 ⇒ demoted（不是错误、不是 409）
        second = W.claim_job(base, TOKEN, JID, worker_id="B", expected_epoch=pr["epoch"])
        assert second is not None and second["status"] == "demoted"
        assert second["priority"] == PRIORITY_LOW

        W.job_started(base, TOKEN, JID, worker_id="A")
        st = W.job_status(base, TOKEN, JID)
        assert st is not None and st["computing_at"] and st["landed"] is False
        # 掉队阈值起算后（未超）仍是 medium；理由里带 computing_at（观测行与判定同源）
        assert st["priority"] == PRIORITY_MEDIUM
        assert "computing_at" in st["priority_reason"]

        W.job_ready(base, TOKEN, JID, worker_id="A")
        st2 = W.job_status(base, TOKEN, JID)
        assert st2 is not None and st2["ready"] is True
        # ready 只降别人优先级，**永不**取消（§1.3.2）：landed 仍为 False，别人判 low
        after_ready = W.request_priority(base, TOKEN, worker_id="B", held=[JID])
        assert after_ready["priorities"][JID] == PRIORITY_LOW
        st3 = W.job_status(base, TOKEN, JID)
        assert st3 is not None and st3["landed"] is False


def test_http_abandon_returns_job_to_pool(tmp_path: Path) -> None:
    """HTTP 面 abandon：池内可领 + reclaims==0（与 store 层双断言）。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        claimed = W.claim_job(base, TOKEN, JID, worker_id="A")
        assert claimed is not None and claimed["status"] == "ok"
        assert hub.claimable_job_ids("c5-gae") == []
        W.abandon_job(base, TOKEN, JID, worker_id="A", reason="landed")
        assert hub.claimable_job_ids("c5-gae") == [JID]
        assert hub.reclaims(JID) == 0


def test_http_backup_claim_returns_no_token_and_authorizes_post(tmp_path: Path) -> None:
    """HTTP 面：备份 claim 返回空 token + 双方回传都放行（首写定胜负）。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, hub):
        a = W.claim_job(base, TOKEN, JID, worker_id="A")
        assert a is not None and a["lease_token"]
        b = W.claim_job(base, TOKEN, JID, mode=CLAIM_MODE_BACKUP, worker_id="B")
        assert b is not None and b["status"] == "backup" and b["lease_token"] == ""
        st = hub._store_of(JID)
        assert st is not None
        assert st.result_token_ok(JID, a["lease_token"]) is True
        assert st.result_token_ok(JID, "") is True


def test_claim_unknown_job_409(tmp_path: Path) -> None:
    """未知 job ⇒ 409（调用方丢副本），**不是** demoted（那是「有人比我快」）。"""
    _publish_online_course(tmp_path)
    with _hub(tmp_path) as (base, _hubq):
        assert W.claim_job(base, TOKEN, "z" * 16, worker_id="A") is None


# ═══════════════════════════ worker 侧：403 丢弃 / 取活三件套 ═══════════════════════════


def _patch_request(monkeypatch: pytest.MonkeyPatch, status: int, body: bytes = b"{}") -> None:
    monkeypatch.setattr(W, "_request", lambda *a, **k: (status, body), raising=True)


def test_post_result_403_backup_is_discard_not_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-2：backup 副本收 403 ⇒ **返回 403、不抛**；非 backup 的 403 仍走 ProtocolError。

    漏了这条 = 备份先回传吃 403 ⇒ ProtocolError ⇒ `report_job_failure` ⇒ 训练停腿
    （「一个赢家把输家炸成事故」，hub 侧注释里已写过一次）。
    """
    _patch_request(monkeypatch, 403, b'{"error":"lease mismatch"}')
    monkeypatch.setattr(W, "_wire_add", lambda *a, **k: None, raising=True)
    result = {"job_id": JID, "weights_json": "", "opt_tar_b64": ""}
    assert W.post_result("http://hub", "tok", JID, result, mode=CLAIM_MODE_BACKUP) == 403
    with pytest.raises(ProtocolError):
        W.post_result("http://hub", "tok", JID, result, mode="ok")


def test_post_result_409_is_success_for_both_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 = 别人已落盘（幂等成功）——两种模式都按丢弃，绝不报失败。"""
    _patch_request(monkeypatch, 409, b'{"error":"already stored"}')
    monkeypatch.setattr(W, "_wire_add", lambda *a, **k: None, raising=True)
    result = {"job_id": JID, "weights_json": "", "opt_tar_b64": ""}
    assert W.post_result("http://hub", "tok", JID, result) == 409
    assert W.post_result("http://hub", "tok", JID, result, mode=CLAIM_MODE_BACKUP) == 409


def test_job_cancelled_is_not_a_failure_class() -> None:
    """取消是**正常结局**：它绝不能是 `ProtocolError`（⇒ report_job_failure ⇒ 停腿）
    也不是 `RetryableError`（⇒ release 把别人已赢下的活放回池）。"""
    assert not issubclass(JobCancelledError, ProtocolError)
    assert not issubclass(JobCancelledError, RetryableError)


def test_acquire_job_prefers_highest_and_skips_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """取活三件套：`none`（已 landed）必弃、demoted 换下家、选到最高档的就 claim。"""
    monkeypatch.setattr(
        W,
        "peek_jobs",
        lambda *a, **k: (
            [
                {"job_id": "done1", "course": "cA"},
                {"job_id": "busy1", "course": "cA"},
                {"job_id": "free1", "course": "cB"},
            ],
            False,
        ),
        raising=True,
    )
    monkeypatch.setattr(
        W,
        "request_priority",
        lambda *a, **k: {
            "epoch": 7,
            "priorities": {"done1": PRIORITY_NONE, "busy1": PRIORITY_MEDIUM, "free1": PRIORITY_HIGHEST},
            "reasons": {},
        },
        raising=True,
    )
    claimed: list[tuple[str, object]] = []

    def _claim(base, token, jid, *, mode="exclusive", worker_id="", expected_epoch=None, **k):
        claimed.append((jid, expected_epoch))
        if jid == "free1":
            return {"job_id": jid, "manifest": {"job_id": jid}, "status": "ok", "lease_token": "t"}
        return None

    monkeypatch.setattr(W, "claim_job", _claim, raising=True)
    logs: list[str] = []
    got = W.acquire_job("http://hub", "tok", worker_id="A", log=logs.append)
    assert got is not None and got["job_id"] == "free1"
    assert claimed == [("free1", 7)], "none 直接丢、medium 排在 highest 之后"
    assert any("none" in m and "done1" in m for m in logs), "丢弃已 landed 的本地副本要留痕"


def test_acquire_job_demoted_moves_to_next_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """claim 回 demoted ⇒ 试下一份（§2.3 ④：还有别的活就换）。"""
    monkeypatch.setattr(
        W,
        "peek_jobs",
        lambda *a, **k: ([{"job_id": "a1", "course": "cA"}, {"job_id": "b1", "course": "cB"}], False),
        raising=True,
    )
    monkeypatch.setattr(
        W,
        "request_priority",
        lambda *a, **k: {"epoch": 1, "priorities": {}, "reasons": {}},
        raising=True,
    )

    def _claim(base, token, jid, **k):
        if jid == "a1":
            return {"job_id": jid, "status": "demoted", "priority": PRIORITY_LOW}
        return {"job_id": jid, "manifest": {"job_id": jid}, "status": "ok", "lease_token": "t"}

    monkeypatch.setattr(W, "claim_job", _claim, raising=True)
    got = W.acquire_job("http://hub", "tok", worker_id="A", log=lambda m: None)
    assert got is not None and got["job_id"] == "b1"


def test_acquire_job_halt_and_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """停机达令上浮；hub 不可达（peek 返回 None）⇒ None（外层按「无 job」退避重试）。"""
    monkeypatch.setattr(W, "peek_jobs", lambda *a, **k: ([], True), raising=True)
    assert W.acquire_job("http://hub", "tok", worker_id="A", log=lambda m: None) == {"halt": True}
    monkeypatch.setattr(W, "peek_jobs", lambda *a, **k: None, raising=True)
    assert W.acquire_job("http://hub", "tok", worker_id="A", log=lambda m: None) is None
    monkeypatch.setattr(W, "peek_jobs", lambda *a, **k: ([], False), raising=True)
    assert W.acquire_job("http://hub", "tok", worker_id="A", log=lambda m: None) is None


def test_acquire_job_falls_back_to_highest_when_priority_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """priority 问询失败不空转：按「无人在做」选（唯一性由 hub 的 claim 闸兜底）。"""
    monkeypatch.setattr(
        W, "peek_jobs", lambda *a, **k: ([{"job_id": "solo", "course": "cA"}], False), raising=True
    )
    monkeypatch.setattr(
        W,
        "request_priority",
        lambda *a, **k: {"epoch": None, "priorities": {}, "reasons": {}},
        raising=True,
    )
    monkeypatch.setattr(
        W,
        "claim_job",
        lambda base, token, jid, **k: {
            "job_id": jid,
            "manifest": {"job_id": jid},
            "status": "ok",
            "lease_token": "t",
        },
        raising=True,
    )
    got = W.acquire_job("http://hub", "tok", worker_id="A", log=lambda m: None)
    assert got is not None and got["job_id"] == "solo"


def test_worker_loop_cancel_abandons_and_never_reports_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """landed 取消的 worker 侧动作（§2.4）：停算丢弃 → **不 POST**、**不报 fail**、
    `abandon`（含 release 租约）；`cancel_latency_s` 进日志。"""
    jobs: list[dict | None] = [
        {"job_id": JID, "manifest": {"job_id": JID}, "status": "backup", "lease_token": ""},
        None,
    ]
    monkeypatch.setattr(W, "acquire_job", lambda *a, **k: jobs.pop(0), raising=True)

    def _cancel(*a, **k):
        raise JobCancelledError("landed")

    monkeypatch.setattr(W, "run_job", _cancel, raising=True)
    monkeypatch.setattr(W, "start_cancel_watcher", lambda *a, **k: None, raising=True)
    abandoned: list[str] = []
    monkeypatch.setattr(
        W, "abandon_job", lambda *a, **k: abandoned.append(str(a[2])), raising=True
    )
    posted: list[str] = []
    monkeypatch.setattr(W, "post_result", lambda *a, **k: posted.append(str(a[2])), raising=True)
    reported: list[str] = []
    monkeypatch.setattr(
        W, "report_job_failure", lambda *a, **k: reported.append(str(a[2])), raising=True
    )
    logs: list[str] = []
    W.worker_loop(
        "http://hub", "tok", work_dir=tmp_path, poll_sec=0.0, once=True, log=logs.append
    )
    assert abandoned == [JID], "取消必须 abandon（含 release 租约，R1-3）"
    assert posted == [] and reported == [], "停算丢弃：零回传、零失败上报"
    assert any("cancel_latency_s" in m for m in logs)


def test_cancel_watcher_sets_event_only_on_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    """取消环：只有**正面证据**（`landed=True`）才置位；问不到（None）不算赢。"""
    states: list[dict | None] = [None, {"landed": False}, {"landed": True}]
    monkeypatch.setattr(W, "job_status", lambda *a, **k: states.pop(0), raising=True)
    stop = threading.Event()
    fired = threading.Event()
    th = W.start_cancel_watcher(
        "http://hub", "tok", JID, stop, fired, interval=0.0, log=lambda m: None
    )
    th.join(timeout=5)
    assert fired.is_set()
    assert th.is_alive() is False, "置位后立即退出（不留常驻线程）"

    stop2 = threading.Event()
    never = threading.Event()
    monkeypatch.setattr(W, "job_status", lambda *a, **k: {"landed": False}, raising=True)

    def _stop_soon() -> None:
        import time as _t

        _t.sleep(0.1)
        stop2.set()

    threading.Thread(target=_stop_soon, daemon=True).start()
    W.start_cancel_watcher(
        "http://hub", "tok", JID, stop2, never, interval=0.01, log=lambda m: None
    ).join(timeout=5)
    assert never.is_set() is False, "landed=False 永不取消（ready 更不取消）"


def test_run_job_wires_cancel_callback_into_ppo() -> None:
    """接线回归（源码级）：取消回调必须真的挂上 `ppo_update(on_epoch_done=…)`。

    为什么用源码断言：这条链中段是「跑一整段真 PPO」，单测里起不来；而这一跳断掉的
    表现极隐（取消延迟永远是「跑完才响应」，日志上完全正常）。这是 **epoch 级**取消点
    （R1-6）——别去动 `ppo_update` 内层结构，也别往 chunk 里加回调。
    """
    src = (ROOT / "remote" / "worker.py").read_text(encoding="utf-8")
    assert "on_epoch_done=_cancel_at_epoch_boundary if should_cancel is not None else None" in src
    assert "def _cancel_at_epoch_boundary" in src
    assert "raise JobCancelledError(" in src
    assert "except JobCancelledError:\n        # 取消是**正常结局**" in src
    assert JOB_CANCEL_POLL_SEC <= 2.0
