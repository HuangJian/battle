"""BC 作业**领取即独占**：进度窗（默认 900s）内别的 worker 领不走（2026-11-12 用户报障）。

现场：云机领到 BC job（`d206c66…`）后，BC 腿**没有心跳线程**（`remote/worker.py` 的 bc
分叉在 `job_round` 之前早退），唯一续命信号是每 epoch 的 `POST /jobs/{id}/epoch`；而旧
`_lease_state` 先判 `CLAIM_TTL_SEC=300s`，于是一门每 epoch 都在正常回传的课认领满 5 分钟
就回池、被第二台云机重领（双跑白烧 GPU）。修法：BC 的进度闸排在 TTL 之前——独占时长由
`bc_progress_stale_sec()` 决定，进度静默超窗才让出。
"""

from __future__ import annotations

from pathlib import Path

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
