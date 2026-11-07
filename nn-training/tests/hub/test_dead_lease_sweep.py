"""test_dead_lease_sweep.py — 死租约周期清扫（plan/dead-lease-sweep）。

现场（z11-bc，2026-11-07）：结果回传被掐后 worker 消失，`_claimed` / `_computing` / `_ready`
三份残留让这份 job 的优先级永远停在最低档；过期租约又只在「有人 claim 它」时才回收 ⇒
9h19m 零认领。本文件钉住清扫，不重证事故时间线。

时间一律假时钟（`_JobStore(now_fn=...)` + `_HubQueue(now_fn=...)`），禁睡真秒。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import CLAIM_TTL_SEC, PRIORITY_HIGHEST, PRIORITY_LOW
from hub.server import _HubQueue, _JobStore
from hub.store_leases import BC_PROGRESS_STALE_SEC, LEASE_ORPHAN

COURSE = "c5-gae"
JID = "j" * 16


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _manifest(jid: str, *, kind: str = "ppo") -> dict:
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
        "kind": kind,
    }


def _hub(tmp_path: Path, clock: _Clock, *, kind: str = "ppo") -> _HubQueue:
    store = _JobStore(
        tmp_path / COURSE / "remote-jobs",
        tmp_path / COURSE / "training_log.jsonl",
        now_fn=clock,
    )
    store.publish(JID, _manifest(JID, kind=kind), b"payload-bytes")
    return _HubQueue({COURSE: store}, order=[COURSE], now_fn=clock)


# ────────────────────────── 主用例：复现 z11-bc ──────────────────────────


def test_sweep_reaps_expired_lease_without_claim(tmp_path: Path) -> None:
    """claim → start → ready → 过 TTL → 没人 claim；清扫必须回收并让优先级回 highest。

    改前：`_reclaims == 0`、三份残留都在、优先级 `low`（`job_priority` 先判 `ready`）；
    改后：`_reclaims == 1`、残留清空、`highest`、池里可见。
    """
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]

    token = st.claim(JID, worker_id="A")
    assert token
    assert st.start_job(JID, "A") is True
    assert st.set_ready(JID, "A") is True
    # ★ B1：`_ready` 是 `job_priority` 的第一分支 ⇒ 现场是 low，不是 high。
    assert hub.job_priority_of(JID) == PRIORITY_LOW

    clock.t += CLAIM_TTL_SEC + 1
    assert hub.job_priority_of(JID) == PRIORITY_LOW, "没人 claim，三份残留还在"
    assert st.reclaims(JID) == 0

    assert hub.sweep_dead_leases() == 1
    assert st.reclaims(JID) == 1
    assert JID not in st._claimed
    assert JID not in st._computing
    assert JID not in st._ready
    assert hub.job_priority_of(JID) == PRIORITY_HIGHEST
    assert JID in hub.claimable_job_ids(COURSE)


def test_peek_jobs_triggers_sweep(tmp_path: Path) -> None:
    """唯一挂点是 `peek_jobs`：不显式调 sweep，peek 也必须把死租约收掉。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]
    assert st.claim(JID, worker_id="A")
    clock.t += CLAIM_TTL_SEC + 1

    hub.peek_jobs(worker_id="W")

    assert st.reclaims(JID) == 1
    assert JID in hub.claimable_job_ids(COURSE)


# ────────────────────────── 边界 ──────────────────────────


def test_sweep_leaves_live_lease_alone(tmp_path: Path) -> None:
    """心跳正常的租约不动：`_reclaims` 不涨、池里没有、持有人还是它。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]
    token = st.claim(JID, worker_id="A")
    assert token
    clock.t += 60
    assert st.heartbeat(JID, token) is True

    assert hub.sweep_dead_leases() == 0
    assert st.reclaims(JID) == 0
    assert JID not in hub.claimable_job_ids(COURSE)
    assert st.lease_worker(JID) == "A"


def test_sweep_freezes_after_three_zero_return_reclaims(tmp_path: Path) -> None:
    """三轮「claim → 过 TTL → sweep（无 result/无 fail）」⇒ 毒包熔断恢复生效。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]

    for n in range(1, 4):
        assert st.claim(JID, worker_id="A") is not None
        clock.t += CLAIM_TTL_SEC + 1
        assert hub.sweep_dead_leases() == 1
        assert st.reclaims(JID) == n

    info = st.frozen_info(JID)
    assert info is not None and info["reclaims"] == 3
    # ★ 清扫在回收那一刻就喊过（否则没人再来 claim 就永远不喊）——一次性事件已消费。
    assert info["announced"] is True
    assert JID in st.frozen_job_ids()
    assert st.claim(JID, worker_id="A") is None
    assert st.consume_freeze_announcement(JID) is None


def test_sweep_skips_settled_job(tmp_path: Path) -> None:
    """已结算（`result/` 在盘）⇒ 租约照收，但毒包计数不动、不冻结。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]
    assert st.claim(JID, worker_id="A")
    (st._job_dir(JID) / "result").mkdir(parents=True, exist_ok=True)
    clock.t += CLAIM_TTL_SEC + 1

    assert hub.sweep_dead_leases() == 1
    assert st.reclaims(JID) == 0
    assert st.frozen_info(JID) is None


def test_sweep_is_throttled(tmp_path: Path) -> None:
    """`SWEEP_MIN_SEC` 内不重复扫：窗口内**新造**一份死租约也不会被收，跨闸才收。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    st = hub._stores[COURSE]

    assert st.claim(JID, worker_id="A", ttl=0.2) is not None
    clock.t += 0.3
    assert hub.sweep_dead_leases() == 1
    assert st.reclaims(JID) == 1

    # 窗口内再造一份死的：租约过期但清扫节流还没放行。
    assert st.claim(JID, worker_id="A", ttl=0.2) is not None
    clock.t += 0.3
    assert hub.sweep_dead_leases() == 0
    assert st.reclaims(JID) == 1
    assert JID in st._leases, "没被回收：租约还在（虽然已死）"

    clock.t += 1.0  # 跨过 SWEEP_MIN_SEC
    assert hub.sweep_dead_leases() == 1
    assert st.reclaims(JID) == 2


def test_sweep_yields_bc_job_on_stale_progress(tmp_path: Path) -> None:
    """BC 判活只认进度：心跳还在、进度停 900s ⇒ 清扫按孤儿让出（既有语义节拍化）。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock, kind="bc")
    st = hub._stores[COURSE]
    token = st.claim(JID, worker_id="A")
    assert token
    # 每 60s 续租，租约一直活；进度锚停在 claim 时刻。
    for _ in range(int(BC_PROGRESS_STALE_SEC // 60)):
        clock.t += 60
        assert st.heartbeat(JID, token) is True
    clock.t += 1

    assert st._lease_state(JID, clock.t) == LEASE_ORPHAN
    assert hub.sweep_dead_leases() == 1
    assert st.reclaims(JID) == 1
