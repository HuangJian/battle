"""BC 作业**领取即独占**：进度窗（默认 900s）内别的 worker 领不走（2026-11-12 用户报障）。

现场：云机领到 BC job（`d206c66…`）后，BC 腿**没有心跳线程**（`remote/worker.py` 的 bc
分叉在 `job_round` 之前早退），唯一续命信号是每 epoch 的 `POST /jobs/{id}/epoch`；而旧
`_lease_state` 先判 `CLAIM_TTL_SEC=300s`，于是一门每 epoch 都在正常回传的课认领满 5 分钟
就回池、被第二台云机重领（双跑白烧 GPU）。修法：BC 的进度闸排在 TTL 之前——独占时长由
`bc_progress_stale_sec()` 决定，进度静默超窗才让出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.protocol import COURSE_ENABLE_MARKER
from hub.server import _HubQueue
from hub.store import _JobStore

JID = "d" * 16


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


def _manifest(jid: str = JID, **over: object) -> dict:
    m: dict = {
        "proto": 1,
        "runId": "test-run",
        "it": 1,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
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
        "kind": "bc",
    }
    m.update(over)
    return m


def _store(tmp_path: Path, clock: _Clock) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl", now_fn=clock)


def test_bc_claim_is_exclusive_past_the_plain_claim_ttl(tmp_path: Path) -> None:
    """过了 `CLAIM_TTL_SEC`（300s）但进度窗未满 ⇒ 仍是原主的活，第二台领不走。"""
    clock = _Clock()
    st = _store(tmp_path, clock)
    st.publish(JID, _manifest(), b"PK\x03\x04fake")
    assert st.claim(JID, worker_id="cloud-1")
    clock.tick(400.0)  # > 300s
    assert st.claim(JID, worker_id="cloud-2") is None
    assert st.lease_worker(JID) == "cloud-1"
    clock.tick(450.0)  # 认领后 850s，仍在 900s 窗内
    assert st.claim(JID, worker_id="cloud-2") is None


def test_bc_epoch_progress_renews_the_window_then_stale_yields(tmp_path: Path) -> None:
    """每 epoch 的进度锚续独占；静默超窗才让出（与过期同路回收）。"""
    clock = _Clock()
    st = _store(tmp_path, clock)
    st.publish(JID, _manifest(), b"PK\x03\x04fake")
    assert st.claim(JID, worker_id="cloud-1")
    clock.tick(600.0)
    st.note_job_progress(JID, clock())  # 一次 epoch 回传
    clock.tick(600.0)  # 距认领 1200s，但距进度 600s < 900s
    assert st.claim(JID, worker_id="cloud-2") is None
    clock.tick(400.0)  # 进度静默 1000s > 900s ⇒ 让出
    assert st.claim(JID, worker_id="cloud-2")


def test_course_lock_survives_hub_and_trainer_restart(tmp_path: Path) -> None:
    """hub 重启（内存租约全丢）+ trainer 重启（同课新 job）都不破坏独占（2026-11-12 用户口径）。"""
    clock = _Clock()
    st = _store(tmp_path, clock)
    st.publish(JID, _manifest(), b"PK\x03\x04fake")
    assert st.claim(JID, worker_id="cloud-1")
    # hub 重启：新 store，内存租约全空，但课程级独占记录在盘上
    st2 = _store(tmp_path, clock)
    assert st2.bc_exclusive_owner() == ("cloud-1", JID)
    assert st2.claim(JID, worker_id="cloud-2") is None
    # trainer 重启：同课换了新 job_id，别的云机一样领不走
    jid2 = "e" * 16
    st2.publish(jid2, _manifest(jid2, it=2), b"PK\x03\x04fake")
    assert st2.claim(jid2, worker_id="cloud-2") is None
    # 原 worker 的 epoch 进度仍能续窗（落盘）
    clock.tick(600.0)
    st2.note_job_progress(JID, clock())
    clock.tick(600.0)  # 距认领 1200s，但距进度 600s < 900s
    assert st2.claim(jid2, worker_id="cloud-2") is None
    clock.tick(400.0)  # 进度静默 > 900s ⇒ 让出
    assert st2.claim(jid2, worker_id="cloud-2")


def test_same_worker_tag_cannot_steal(tmp_path: Path) -> None:
    """两台同环境云机共用 `kaggle-t`（worker_name 按环境命名）——重启后新机也领不走。"""
    clock = _Clock()
    st = _store(tmp_path, clock)
    st.publish(JID, _manifest(), b"PK\x03\x04fake")
    assert st.claim(JID, worker_id="kaggle-t")
    st2 = _store(tmp_path, clock)  # hub 重启：内存租约全丢，锁文件还在
    jid2 = "f" * 16
    st2.publish(jid2, _manifest(jid2, it=2), b"PK\x03\x04fake")
    # 同一 worker 名（另一台机器）不能把同一门课的第二份 job 领走
    assert st2.claim(jid2, worker_id="kaggle-t") is None
    assert st2.claim(JID, worker_id="kaggle-t") is None
    clock.tick(901.0)
    assert st2.claim(jid2, worker_id="kaggle-t")


def test_epoch_rebuilds_lock_when_file_missing(tmp_path: Path) -> None:
    """认领发生在部署/重启前（盘上无锁）+ hub 重启 ⇒ 第一发 epoch 就重建锁，立刻挡人。"""
    clock = _Clock()
    st = _store(tmp_path, clock)
    st.publish(JID, _manifest(), b"PK\x03\x04fake")
    assert st.claim(JID, worker_id="kaggle-t")
    st._bc_lock_clear(JID)  # 模拟旧代码认领：内存租约在、锁文件不在
    st2 = _store(tmp_path, clock)  # hub 重启
    assert st2.bc_exclusive_owner() == ("", "")
    st2.note_job_progress(JID)  # 原 worker 的 epoch 到达（无租约也收）
    assert st2.bc_exclusive_owner() == ("", JID)  # 持有者未知，但锁已重建
    assert st2.claim(JID, worker_id="kaggle-t") is None
    assert st2.claim(JID, worker_id="cloud-2") is None
    clock.tick(901.0)
    assert st2.claim(JID, worker_id="cloud-2")


def _hub(tmp_path: Path, clock: _Clock) -> _HubQueue:
    """两门课的最小 hub（真实目录 + 开课标记），用于 peek/解除独占的端到端断言。"""
    for name in ("g1e-4s2e", "g1g-4s2e"):
        (tmp_path / name / "remote-jobs").mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "training_log.jsonl").touch()
        (tmp_path / name / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    hub = _HubQueue({}, discover_root=tmp_path, now_fn=clock)
    hub.discover(force=True)
    return hub


def test_locked_bc_course_is_not_offered_to_other_workers(tmp_path: Path) -> None:
    """别的 worker 的 peek（预取/认领同一入口）看不到已独占的 BC 课程；其它课照旧。"""
    clock = _Clock()
    hub = _hub(tmp_path, clock)
    bc = hub._stores["g1e-4s2e"]
    ppo = hub._stores["g1g-4s2e"]
    ppo_jid = "e" * 16
    bc.publish(JID, _manifest(JID, kind="bc"), b"PK\x03\x04fake")
    ppo.publish(ppo_jid, _manifest(ppo_jid, kind="ppo"), b"PK\x03\x04fake")
    assert bc.claim(JID, worker_id="cloud-1")
    hub2 = _hub(tmp_path, clock)  # hub 重启：内存租约全丢，独占记录在盘上
    ids = {c["job_id"] for c in hub2.peek_jobs(worker_id="cloud-2")}
    assert JID not in ids, "别人独占的 BC 课不许出现在 peek（预取腿同源）"
    assert ppo_jid in ids, "另一门待训课照常可见"
    # dashboard 的「解除 BC 独占」按钮：删锁后同课 job 立刻重新可见
    info = hub2.clear_bc_exclusive("g1e-4s2e")
    assert info["cleared"] is True and info["worker_id"] == "cloud-1", info
    assert JID in {c["job_id"] for c in hub2.peek_jobs(worker_id="cloud-2")}
    with pytest.raises(ValueError):
        hub2.clear_bc_exclusive("no-such-course")
