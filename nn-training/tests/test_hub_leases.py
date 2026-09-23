"""test_hub_leases.py — P3b 独占加超时（supersede §343）。

plan: `plan/multi-course-parallel-training.md`（P3b-W3/W5、§3.9 B3）。

- 领取即设租约（owner + expiry + last_heartbeat 同时置）；响应带 lease_token。
- 活租约 job 不在可领取池；TTL 过期回池；心跳续租（60s 节奏）；主动 release 回池。
- 有活租约验回传 token；无租约照收；首写锁定保留。
- halt 不拦分发/不清租约（正交回归）。

时间一律 fake 时钟（`_JobStore(now_fn=...)` 注入点），禁止睡真 300s。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import CLAIM_TTL_SEC, PAYLOAD_NAME
from remote.hub_server import _JobStore


def _mini_manifest(jid: str = "j" * 16) -> dict:
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


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _store(tmp_path: Path, clock: _Clock) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=clock)


def _publish(store: _JobStore, jid: str = "j" * 16) -> None:
    store.publish(jid, _mini_manifest(jid), b"payload-bytes")
    assert ((store._job_dir(jid)) / PAYLOAD_NAME).exists()


# ────────────────────────── 独占 / 回池 ──────────────────────────


def test_claim_excludes_from_pool_until_expiry(tmp_path: Path) -> None:
    """领取后活租约期内不在池中；TTL 过期回池（死 worker 回收）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.claimable_job_ids() == ["j" * 16]
    token = store.claim("j" * 16)
    assert token  # 下发 lease_token
    assert isinstance(token, str)
    assert store.claimable_job_ids() == []  # 独占：超时前不重发
    assert store.claim("j" * 16) is None  # 并发领取竞负
    clock.t += CLAIM_TTL_SEC - 1
    assert store.claimable_job_ids() == []  # 边界内仍独占
    clock.t += 1.001
    assert store.claimable_job_ids() == ["j" * 16]  # 过期回池


def test_claim_sets_owner_and_expiry_together(tmp_path: Path) -> None:
    """B3：owner + expiry + last_heartbeat 同时置（否则 heartbeat 恒 False）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16)
    assert isinstance(token, str)
    assert store._lease_owners.get("j" * 16) == token
    assert store._leases.get("j" * 16) == clock.t + CLAIM_TTL_SEC
    assert store._last_heartbeat.get("j" * 16) == clock.t
    # 领取即有 owner → 心跳立即有效（旧 bug：只写 _leases 时恒 False）
    assert store.heartbeat("j" * 16, token) is True


def test_heartbeat_renews_with_claim_ttl(tmp_path: Path) -> None:
    """心跳续租改用 CLAIM_TTL_SEC（唯一 TTL 源）；每 60s 心跳 → 300s 后仍在租。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16)
    assert isinstance(token, str)
    for _ in range(5):  # 5 × 60s 心跳，跨过初始 TTL
        clock.t += 60
        assert store.heartbeat("j" * 16, token) is True
    assert store.claimable_job_ids() == []  # 正向：一直在租
    clock.t += CLAIM_TTL_SEC + 1  # 心跳停 → 过期
    assert store.claimable_job_ids() == ["j" * 16]  # 反向：回池


def test_failed_heartbeat_does_not_extend(tmp_path: Path) -> None:
    """错 token 心跳不续租（心跳 5xx ×N → 靠过期回池的边界）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16)
    assert isinstance(token, str)
    expiry_before = store._leases["j" * 16]
    assert store.heartbeat("j" * 16, "wrong-token") is False
    assert store.heartbeat("no-such-job", token) is False
    assert store._leases["j" * 16] == expiry_before  # 失败不延长
    assert store._last_heartbeat["j" * 16] == clock.t  # 未动


def test_release_returns_to_pool_immediately(tmp_path: Path) -> None:
    """主动 release 立即回池；非持有人放不掉。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16)
    assert isinstance(token, str)
    assert store.release("j" * 16, "wrong") is False
    assert store.claimable_job_ids() == []
    assert store.release("j" * 16, token) is True
    assert store.claimable_job_ids() == ["j" * 16]


# ────────────────────────── 回传鉴权 / 首写 ──────────────────────────


def test_result_token_ok_matrix(tmp_path: Path) -> None:
    """有活租约验 token；无租约照收（旧 worker/重发兼容）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.result_token_ok("j" * 16, "") is True  # 从未领取 → 照收
    token = store.claim("j" * 16)
    assert isinstance(token, str)
    assert store.result_token_ok("j" * 16, token) is True
    assert store.result_token_ok("j" * 16, "wrong") is False
    assert store.result_token_ok("j" * 16, "") is False
    clock.t += CLAIM_TTL_SEC + 1  # 过期 → 照收
    assert store.result_token_ok("j" * 16, "") is True


def test_store_result_first_write_wins(tmp_path: Path) -> None:
    """首写锁定保留（hub 重启丢租约 → 首写胜，结果一致）。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.store_result("j" * 16, {"a": 1}) is True
    assert store.store_result("j" * 16, {"a": 2}) is False
    saved = json.loads((store._job_dir("j" * 16) / "result" / "result.json").read_text())
    assert saved == {"a": 1}


# ────────────────────────── halt 正交 ──────────────────────────


def test_halt_does_not_block_leases(tmp_path: Path) -> None:
    """halt 期租约流正常：halt 不拦分发、不清租约。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    store.halt_workers = True
    token = store.claim("j" * 16)
    assert token
    assert store.claimable_job_ids() == []
    assert store.heartbeat("j" * 16, token) is True
    assert store.result_token_ok("j" * 16, token) is True
