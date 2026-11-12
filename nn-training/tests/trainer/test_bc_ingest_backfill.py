"""迟到补发行的入账语义（2026-11-04，**免 torch**）。

补发链路（`remote/bc_job.py` 的 backlog）会把隧道 530 丢掉的旧 epoch 在**最新 epoch 之后**
重新回传 ⇒ hub 的 `bc-metrics.jsonl` 追加出一行「回头行」（...ep6, ep5）。训练机这一侧必须：

* 账本照收（控制台要把缺的那一行补上）；
* **绝不派 eval**——`run_epoch_eval` 取的是 hub 的 `/resume`（此刻已是 ep6+ 的权重），
  拿它冒充 ep5 的结果就是一条假指标（比缺一行更糟）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import remote.hub_http as hub_http
import trainer.bc_ingest as bc_ingest
from trainer.bc_ingest import READY, BcWait, ingest_bc_metrics
from worker.bc_config import BcCourseConfig, BcEvalBlock, BcTrainBlock


def _course() -> BcCourseConfig:
    return BcCourseConfig(
        name="bc-istest",
        eval=BcEvalBlock(every_epochs=2, levels=["arena4"], games_per_stage=2),
    )


def _rows(*epochs: int) -> list[dict]:
    return [
        {
            "epoch": ep,
            "train_loss": 1.0,
            "val_loss": 1.1,
            "move_acc": 0.5,
            "fire_acc": 0.6,
            "lr": 3e-4,
        }
        for ep in epochs
    ]


def _ledger_epochs(path: Path) -> list[int]:
    return [
        json.loads(line)["epoch"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_late_backfill_row_is_ledgered_without_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ep4 → ep6 → 补发的 ep5：账本 [4,6,5]；eval 只发生在 4、6（每 2 epoch 的边界）。"""
    ledger = tmp_path / "training_log.jsonl"
    evaluated: list[int] = []

    def fake_eval(
        hub_url: str,
        token: str,
        jid: str,
        jsonl_path: Path,
        it: int,
        epoch: int,
        course: BcCourseConfig,
        cfg: dict | None,
        log: object,
    ) -> None:
        evaluated.append(epoch)

    monkeypatch.setattr(bc_ingest, "run_epoch_eval", fake_eval)
    monkeypatch.setattr(
        hub_http,
        "_request",
        lambda *_a, **_k: (200, json.dumps({"rows": _rows(4, 6, 5)}).encode("utf-8")),
    )

    wait = BcWait(jid="j" * 16)
    ingest_bc_metrics(
        wait,
        hub_url="http://hub",
        token="t",
        jsonl_path=ledger,
        it=1,
        course=_course(),
        cfg=None,
        logger=lambda _m: None,
    )

    assert evaluated == [4, 6]
    assert _ledger_epochs(ledger) == [4, 6, 5]
    assert wait.last_epoch == 6
    assert wait.seen_metrics == 3


def test_duplicate_row_does_not_cancel_same_batch_boundary_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同批重复行（ep2 在 ep3 之后重发）只是重发，不得把它从 eval 候选里误删。"""
    ledger = tmp_path / "training_log.jsonl"
    evaluated: list[int] = []

    def fake_eval(
        hub_url: str,
        token: str,
        jid: str,
        jsonl_path: Path,
        it: int,
        epoch: int,
        course: BcCourseConfig,
        cfg: dict | None,
        log: object,
    ) -> None:
        evaluated.append(epoch)

    monkeypatch.setattr(bc_ingest, "run_epoch_eval", fake_eval)
    monkeypatch.setattr(
        hub_http,
        "_request",
        lambda *_a, **_k: (200, json.dumps({"rows": _rows(1, 2, 3, 2)}).encode("utf-8")),
    )

    wait = BcWait(jid="j" * 16)
    ingest_bc_metrics(
        wait,
        hub_url="http://hub",
        token="t",
        jsonl_path=ledger,
        it=1,
        course=_course(),
        cfg=None,
        logger=lambda _m: None,
    )

    assert evaluated == [2], "重复行不是迟到补发行；边界 ep2 的 eval 必须照派"
    assert _ledger_epochs(ledger) == [1, 2, 3]


def test_ready_on_first_poll_flushes_metrics_before_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一次轮询就 READY：必须在审计前把最后一批指标入账（短 job 零次 PENDING）。"""
    ledger = tmp_path / "training_log.jsonl"

    def request(base: str, token: str, path: str, timeout: float = 0.0):
        if path.endswith("/result"):
            return 200, json.dumps({"metrics": {"epochs": 3}}).encode("utf-8")
        if path.endswith("/bc-metrics"):
            return 200, json.dumps({"rows": _rows(1, 2, 3)}).encode("utf-8")
        return 404, b"{}"

    monkeypatch.setattr(hub_http, "_request", request)
    course = BcCourseConfig(
        name="bc-flush",
        train=BcTrainBlock(epochs=3),
        eval=BcEvalBlock(every_epochs=0),
    )
    wait = BcWait(jid="j" * 16)

    status, loaded = wait.poll_once(
        hub_url="http://hub",
        token="t",
        jsonl_path=ledger,
        it=1,
        course=course,
        cfg=None,
        logger=lambda _m: None,
    )

    assert status == READY and loaded is not None
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [e["epoch"] for e in events if e["event"] == "bc_epoch"] == [1, 2, 3]
    audit = [e for e in events if e["event"] == "bc_ledger_audit"]
    assert audit and audit[-1]["complete"] is True
    assert wait.seen_metrics == 3


def test_ready_evaluates_final_epoch_when_metric_row_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """READY 时最终权重已回，但末 epoch 的 bc_metrics 行可能没到；仍要补该边界 eval。"""
    ledger = tmp_path / "training_log.jsonl"
    evaluated: list[int] = []

    def fake_eval(
        hub_url: str,
        token: str,
        jid: str,
        jsonl_path: Path,
        it: int,
        epoch: int,
        course: BcCourseConfig,
        cfg: dict | None,
        log: object,
    ) -> None:
        evaluated.append(epoch)

    def request(base: str, token: str, path: str, timeout: float = 0.0):
        if path.endswith("/result"):
            return 200, json.dumps(
                {"metrics": {"epochs": 4, "epochs_completed": 4}}
            ).encode("utf-8")
        if path.endswith("/bc-metrics"):
            # 末行 ep4 没上传：result 已带最终权重，但没有 bc_epoch 行可当触发点。
            return 200, json.dumps({"rows": _rows(1, 2, 3)}).encode("utf-8")
        return 404, b"{}"

    monkeypatch.setattr(bc_ingest, "run_epoch_eval", fake_eval)
    monkeypatch.setattr(hub_http, "_request", request)
    wait = BcWait(jid="j" * 16)

    status, _ = wait.poll_once(
        hub_url="http://hub",
        token="t",
        jsonl_path=ledger,
        it=1,
        course=_course(),
        cfg=None,
        logger=lambda _m: None,
    )

    assert status == READY
    assert evaluated == [2, 4]


def test_in_order_rows_keep_evaluating_at_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """判定不能把正常的整批增量（1,2 两行一次拉到）误判成补发。"""
    ledger = tmp_path / "training_log.jsonl"
    evaluated: list[int] = []

    def fake_eval(
        hub_url: str,
        token: str,
        jid: str,
        jsonl_path: Path,
        it: int,
        epoch: int,
        course: BcCourseConfig,
        cfg: dict | None,
        log: object,
    ) -> None:
        evaluated.append(epoch)

    monkeypatch.setattr(bc_ingest, "run_epoch_eval", fake_eval)
    monkeypatch.setattr(
        hub_http,
        "_request",
        lambda *_a, **_k: (200, json.dumps({"rows": _rows(1, 2)}).encode("utf-8")),
    )

    wait = BcWait(jid="j" * 16)
    ingest_bc_metrics(
        wait,
        hub_url="http://hub",
        token="t",
        jsonl_path=ledger,
        it=1,
        course=_course(),
        cfg=None,
        logger=lambda _m: None,
    )

    assert evaluated == [2]
    assert _ledger_epochs(ledger) == [1, 2]
    assert wait.last_epoch == 2
