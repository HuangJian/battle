"""test_eval_bc_once — 手动 BC epoch 补评的账本查找/幂等纯逻辑。"""

from __future__ import annotations

from pathlib import Path

from trainer.eval_bc_once import _eval_done, _job_ids, _ledger_path


class _FakeCourse:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_traj(self, key: str) -> str:
        return str(self.root / key)


def test_ledger_path_is_training_log_not_traj_dir(tmp_path: Path) -> None:
    """账本路径必须是 `<traj>/training_log.jsonl`，不能是 `<traj>` 目录本身（2026-11-13）。

    事故：evalA 按钮能 spawn，但脚本把课程目录当账本 `read_text()` ⇒
    PermissionError，进程秒退、账本零 `bc_eval`。
    """
    p = _ledger_path(Path("g2-classic35.bc.jsonc"), _FakeCourse(tmp_path))
    assert p == tmp_path / "g2-classic35" / "training_log.jsonl"


def test_job_ids_latest_first_and_deduped() -> None:
    events = [
        {"event": "job_pending", "it": 2, "job_id": "old"},
        {"event": "job_completed", "it": 2, "job_id": "old"},
        {"event": "job_pending", "it": 2, "job_id": "new"},
        {"event": "job_pending", "it": 3, "job_id": "other"},
        {"event": "job_pending", "it": 2, "job_id": "new"},
    ]
    assert _job_ids(events, 2) == ["new", "old"]


def test_explicit_job_id_wins_even_if_not_in_ledger() -> None:
    events = [{"event": "job_pending", "it": 2, "job_id": "ledger"}]
    assert _job_ids(events, 2, "manual") == ["manual"]


def test_eval_done_matches_both_it_and_epoch() -> None:
    events = [
        {"event": "bc_eval", "it": 2, "epoch": 7},
        {"event": "bc_eval", "it": 3, "epoch": 8},
    ]
    assert _eval_done(events, 2, 7) is True
    assert _eval_done(events, 2, 8) is False  # 同 epoch 不同 it 不算
    assert _eval_done(events, 3, 7) is False
