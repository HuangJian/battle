"""重启后的入账接续（2026-11-12，g1c-4s2e 报障：重启 trainer 后已算过的 epoch 又重算）。

现场：`BcWait.seen_metrics` / `ledger_epochs` 是**内存**记账。trainer 一重启，新会话从 0 开始
⇒ 下一拍把 hub `bc-metrics` 的**全量**行当成新行重放：

* 同一个 epoch 在账本里出现两次（曲线出现重复点 / 审计报 duplicates）；
* `eval.every_epochs` 的每个边界**重跑 eval**（实测 ep10/20/30/40/50 各 200 局白算）。

job 还是同一个（hub 的 `bc-resume` 继续，`job_pending` 没有新行），所以账本是唯一可靠的
「这个 job 已经算过什么」。本文件钉住：新会话第一次跑 `ingest_bc_metrics` 前先按账本自愈。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import remote.hub_http as hub_http
import trainer.bc_ingest as bc_ingest
from trainer.bc_ingest import BcWait, ingest_bc_metrics
from worker.bc_config import BcCourseConfig, BcEvalBlock, BcTrainBlock

JID = "f375867375c1da8f"


def _course() -> BcCourseConfig:
    return BcCourseConfig(
        name="bc-restart",
        train=BcTrainBlock(epochs=10),
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


def _write(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8"
    )


def _ledger(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_restart_does_not_reingest_or_reeval_known_epochs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重启前已入账 ep1..4、已评过 ep2/4 ⇒ 重启后只补 ep5/6，只为 ep6 派 eval。"""
    ledger = tmp_path / "training_log.jsonl"
    _write(
        ledger,
        [
            {"event": "run_start", "runId": "bc-run-1", "epochs": 10},
            {"event": "job_pending", "job_id": JID, "runId": "bc-run-1", "it": 1},
            *[
                {
                    "event": "bc_epoch",
                    "it": 1,
                    "epoch": ep,
                    "train_loss": 1.0,
                    "val_loss": 1.1,
                    "ts": ep,
                }
                for ep in (1, 2, 3, 4)
            ],
            {"event": "bc_eval", "it": 1, "epoch": 2, "wver": "w2", "levels": [], "ts": 2},
            {"event": "bc_eval", "it": 1, "epoch": 4, "wver": "w4", "levels": [], "ts": 4},
            # 重启：新 run_start，但 job 还是同一个（没有新的 job_pending）
            {"event": "run_start", "runId": "bc-run-2", "epochs": 10},
        ],
    )
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
        lambda *_a, **_k: (200, json.dumps({"rows": _rows(1, 2, 3, 4, 5, 6)}).encode("utf-8")),
    )

    wait = BcWait(jid=JID)
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

    epochs = [e["epoch"] for e in _ledger(ledger) if e["event"] == "bc_epoch"]
    assert epochs == [1, 2, 3, 4, 5, 6], "已入账的 epoch 不许重复写（重启重放）"
    assert evaluated == [6], "已算过的 eval 边界不许重算（ep2/ep4 在账本里）"
    assert wait.last_epoch == 6


def test_restart_evaluates_boundary_with_metric_row_but_no_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重启前 bc_epoch ep4 已入账、bc_eval ep4 没写成 ⇒ 重启后必须补评，不许把指标行当已评。"""
    ledger = tmp_path / "training_log.jsonl"
    _write(
        ledger,
        [
            {"event": "job_pending", "job_id": JID, "runId": "bc-run-1", "it": 1},
            *[
                {"event": "bc_epoch", "it": 1, "epoch": ep, "ts": ep}
                for ep in (1, 2, 3, 4)
            ],
            {"event": "bc_eval", "it": 1, "epoch": 2, "wver": "w2", "levels": [], "ts": 2},
            {"event": "run_start", "runId": "bc-run-2", "epochs": 10},
        ],
    )
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
        lambda *_a, **_k: (200, json.dumps({"rows": _rows(1, 2, 3, 4)}).encode("utf-8")),
    )

    wait = BcWait(jid=JID)
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

    assert evaluated == [4], "指标行已入账不等于 eval 已完成；缺 bc_eval 的边界必须补评"
    assert [e["epoch"] for e in _ledger(ledger) if e["event"] == "bc_epoch"] == [1, 2, 3, 4]


def test_new_job_starts_from_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """换 job（新的 `job_pending`）⇒ 账本里上一份 job 的 epoch **不算数**，从 ep1 重新入账。"""
    ledger = tmp_path / "training_log.jsonl"
    _write(
        ledger,
        [
            {"event": "job_pending", "job_id": "old" * 5, "it": 1},
            {"event": "bc_epoch", "it": 1, "epoch": 1, "ts": 1},
            {"event": "bc_eval", "it": 1, "epoch": 1, "wver": "w", "levels": [], "ts": 1},
            {"event": "job_pending", "job_id": JID, "it": 1},
        ],
    )
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

    wait = BcWait(jid=JID)
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

    epochs = [e["epoch"] for e in _ledger(ledger) if e["event"] == "bc_epoch"]
    assert epochs == [1, 1, 2], "新 job 的 ep1 必须入账（旧 job 的同号行不算已入账）"
    assert evaluated == [2], "新 job 的 eval 边界照常派"
