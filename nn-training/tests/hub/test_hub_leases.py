"""test_hub_leases.py — P3b 独占加超时（supersede §343）。

plan: `plan/multi-course-parallel-training.md`（P3b-W3/W5、§3.9 B3）。

- 领取即设租约（owner + expiry + last_heartbeat 同时置）；响应带 lease_token。
- 活租约 job 不在可领取池；TTL 过期回池；心跳续租（60s 节奏）；主动 release 回池。
- **孤儿早收（§52，2026-10-01）**：claim 后**自始至终零心跳**且静默超 `ORPHAN_GRACE_SEC`
  ⇒ 提前回池 + 账本事件 `lease-orphan-reaped`；push 腿（`push:` 前缀持有人）豁免。
- 有活租约验回传 token；无租约照收；首写锁定保留。
- halt 不拦分发/不清租约（正交回归）。

时间一律 fake 时钟（`_JobStore(now_fn=...)` 注入点），禁止睡真 300s。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import CLAIM_MODE_BACKUP, CLAIM_TTL_SEC, PAYLOAD_NAME, push_worker_id_of
from hub.server import _JobStore
from hub.store_leases import ORPHAN_GRACE_SEC


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


def _events(store: _JobStore) -> list[dict]:
    """账本**原始**行。`_read_ledger()` 按设计只认 `job_*` 三种（审计事件不进增量缓存），
    所以看 `lease-orphan-reaped` 必须直读 jsonl。"""
    if not store.jsonl_path.exists():
        return []
    return [
        json.loads(line)
        for line in store.jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _orphan_events(store: _JobStore) -> list[dict]:
    return [e for e in _events(store) if e.get("event") == "lease-orphan-reaped"]


# ────────────────────────── 独占 / 回池 ──────────────────────────


def test_claim_excludes_from_pool_until_expiry(tmp_path: Path) -> None:
    """领取后活租约期内不在池中；TTL 过期回池（死 worker 回收）。

    ★ 2026-10-01（§52）：claim 后**补一次心跳**——本用例测的是 TTL 边界，而零心跳的租约
    到 180s 就会被孤儿规则提前收走（那条规则由 `test_claim_silence_past_grace_reaps_and_logs`
    与 `test_heartbeat_once_never_reaped` 覆盖）。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.claimable_job_ids() == ["j" * 16]
    token = store.claim("j" * 16)
    assert token  # 下发 lease_token
    assert isinstance(token, str)
    assert store.claimable_job_ids() == []  # 独占：超时前不重发
    assert store.claim("j" * 16) is None  # 并发领取竞负
    clock.t += 1
    assert store.heartbeat("j" * 16, token) is True  # 活 worker：有心跳
    clock.t = store._leases["j" * 16] - 1  # 到期前一秒
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


# ────────────────────────── §52 孤儿租约早收 ──────────────────────────
#
# 事故形状（2026-09-28，bc-human-retrial job 460b637b）：claim POST 到达即设 owner+expiry，
# token 却随响应一起丢（客户端 30s 读超时）⇒「hub 有主、世上无人持有 token」，job 在 peek
# 里隐身满一个 CLAIM_TTL_SEC=300。新规则：自 claim 起**零心跳**且静默超宽限 ⇒ 提前回池。
# 时间一律假时钟（禁睡真 180s）。


def test_claim_silence_past_grace_reaps_and_logs(tmp_path: Path) -> None:
    """179s 无声 ⇒ 仍租住（池外、领不到、算在飞）；181s ⇒ 回池 **且领得到** + 账本事件。

    ★「池里看得见」与「claim 拿得到」必须同时翻转（§52 判据唯一）：只在池过滤里加判据
    会做出「看得见、领不到」——那比隐身更难查。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16, worker_id="w-v100")
    assert token
    clock.t += 179
    assert store.claimable_job_ids() == []
    assert store.claim("j" * 16) is None
    assert store.inflight() == ["j" * 16]
    assert _orphan_events(store) == []
    clock.t += 2  # 181s：早于 TTL(300) 的孤儿判定点
    assert store.claimable_job_ids() == ["j" * 16]  # 回池
    assert store.inflight() == []  # 观测面不再报「在飞」
    token2 = store.claim("j" * 16)  # ★ 池可见 ⇒ 领得到（回收走 claim 同一入口）
    assert token2 and token2 != token
    evs = _orphan_events(store)
    assert len(evs) == 1, evs
    assert evs[0]["job_id"] == "j" * 16
    assert evs[0]["worker"] == "w-v100"  # 谁跑死的（避让链的同一份记录）
    assert evs[0]["silent_sec"] >= ORPHAN_GRACE_SEC
    assert evs[0]["reclaims"] == 1  # 孤儿算一次 reclaim（与 TTL 过期同路）


def test_heartbeat_once_never_reaped(tmp_path: Path) -> None:
    """一次心跳就把孤儿规则关掉：181s 不早收；跨过 TTL 走**既有过期路径**回池。

    ★ 口径（评审修正）不是「1000s 仍租住」——那与 TTL 语义矛盾：一次心跳把租约推到
    claim+1+300，到点照旧过期回池，**全程没有** `lease-orphan-reaped`。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    token = store.claim("j" * 16)
    assert token
    clock.t += 1
    assert store.heartbeat("j" * 16, token) is True
    clock.t += ORPHAN_GRACE_SEC  # 181s：零心跳的租约早在这一刻前就被收了
    assert store.claimable_job_ids() == []
    assert store.claim("j" * 16) is None
    assert store.inflight() == ["j" * 16]
    assert _orphan_events(store) == []
    clock.t = store._leases["j" * 16] + 1  # 跨过这次的 TTL 到期点
    assert store.claimable_job_ids() == ["j" * 16]  # 既有过期路径回池
    assert store.claim("j" * 16)
    assert _orphan_events(store) == []  # 过期 ≠ 孤儿，不写那个事件


def test_push_lease_is_never_orphan_reaped(tmp_path: Path) -> None:
    """push 腿租约（`push:` 前缀持有人）**永不**早收：上传段零心跳是它的**正常**形状。

    `PushDispatcher` 住 hub 进程内，只在 `_await_result` 里续租；判它孤儿 = 同一份活两处跑
    + 回传 403 丢结果（上游 2026-10-01 评审）。它的治理在 `--push-timeout-sec`。
    """
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    lease = store.claim("j" * 16, worker_id=push_worker_id_of("gpu1"))
    assert lease
    clock.t += ORPHAN_GRACE_SEC + 1
    assert store.claimable_job_ids() == []  # 早收不碰它
    assert store.claim("j" * 16) is None
    assert store.inflight() == ["j" * 16]
    assert _orphan_events(store) == []
    clock.t = store._leases["j" * 16] + 1  # 跨过 TTL ⇒ 按**过期**回收
    assert store.claimable_job_ids() == ["j" * 16]
    assert store.claim("j" * 16)
    assert _orphan_events(store) == []


def test_backup_never_reaped(tmp_path: Path) -> None:
    """backup 副本无租约（只写 `_last_heartbeat`，从不写 `_leases`）⇒ 早收规则够不着它。"""
    clock = _Clock()
    store = _store(tmp_path, clock)
    _publish(store)
    assert store.claim("j" * 16, mode=CLAIM_MODE_BACKUP) == ""
    clock.t += ORPHAN_GRACE_SEC * 3  # 远超宽限
    assert store.claimable_job_ids() == ["j" * 16]  # 副本不占池（既有语义）
    assert _orphan_events(store) == []


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
