"""BC 每 epoch 权重归档 + eval 取**该 epoch** 快照（#12 / B6，2026-11-05）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import remote.hub_http as hub_http
import trainer.bc_ingest as bc_ingest
from common.protocol import encode_weights_json
from trainer.bc_ingest import BcWait, ingest_bc_metrics, run_epoch_eval
from worker.bc_config import BcCourseConfig, BcEvalBlock

JID = "j" * 16


def _course(tmp_path: Path, *, backup: bool = True) -> BcCourseConfig:
    return BcCourseConfig(
        name="bc-arch",
        backup_dir=str(tmp_path / "weights") if backup else "",
        eval=BcEvalBlock(every_epochs=0),  # 0 = 关（本文件只测归档）
    )


def _rows(*epochs: int) -> list[dict]:
    return [
        {
            "epoch": ep,
            "train_loss": 1.0,
            "val_loss": 1.1,
            "move_acc": 0.5,
            "fire_acc": 0.6,
            "hold_acc": 0.7,
            "lr": 3e-4,
        }
        for ep in epochs
    ]


def _epoch_weights(ep: int) -> bytes:
    return f"weights-ep{ep}".encode()


def _fake_hub(paths: list[str], rows: list[dict]):
    def request(base: str, token: str, path: str, timeout: float = 0.0):
        paths.append(path)
        if path.endswith("/bc-metrics"):
            return 200, json.dumps({"rows": rows}).encode("utf-8")
        if "/epoch-weights/" in path:
            ep = int(path.rsplit("/", 1)[-1])
            return 200, json.dumps(
                {"epoch": ep, "weights": encode_weights_json(_epoch_weights(ep))}
            ).encode("utf-8")
        return 404, b"{}"

    return request


def _archived(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("bc-arch.ep*.json"))


def test_ingest_archives_every_new_epoch(tmp_path: Path, monkeypatch) -> None:
    paths: list[str] = []
    monkeypatch.setattr(hub_http, "_request", _fake_hub(paths, _rows(1, 2)))
    course = _course(tmp_path)
    wait = BcWait(jid=JID)

    ingest_bc_metrics(
        wait,
        hub_url="http://hub",
        token="t",
        jsonl_path=tmp_path / "log.jsonl",
        it=1,
        course=course,
        cfg=None,
        logger=lambda _m: None,
    )

    assert wait.seen_metrics == 2
    snaps = _archived(tmp_path / "weights")
    assert [p.name.split(".")[1] for p in snaps] == ["ep1", "ep2"]
    assert snaps[0].read_bytes() == _epoch_weights(1)


def test_archive_is_idempotent_for_replayed_epoch(tmp_path: Path, monkeypatch) -> None:
    """补发链路会重放同一 epoch：归档复用旧文件，不产生第二份。"""
    paths: list[str] = []
    monkeypatch.setattr(hub_http, "_request", _fake_hub(paths, _rows(3)))
    course = _course(tmp_path)
    for _ in range(2):
        ingest_bc_metrics(
            BcWait(jid=JID),
            hub_url="http://hub",
            token="t",
            jsonl_path=tmp_path / "log.jsonl",
            it=1,
            course=course,
            cfg=None,
            logger=lambda _m: None,
        )
    assert len(_archived(tmp_path / "weights")) == 1


def test_no_backup_dir_means_no_archive(tmp_path: Path, monkeypatch) -> None:
    """没配 backup_dir 不写 RL 默认归档面（也不该去拉权重）。"""
    paths: list[str] = []
    monkeypatch.setattr(hub_http, "_request", _fake_hub(paths, _rows(1)))
    course = _course(tmp_path, backup=False)

    ingest_bc_metrics(
        BcWait(jid=JID),
        hub_url="http://hub",
        token="t",
        jsonl_path=tmp_path / "log.jsonl",
        it=1,
        course=course,
        cfg=None,
        logger=lambda _m: None,
    )

    assert not any(Path(p).parent.name == "epoch-weights" for p in paths)
    assert not (tmp_path / "weights").exists()


def test_epoch_eval_reads_that_epoch_not_latest_resume(tmp_path: Path, monkeypatch) -> None:
    """B6：ep4 的 eval 必须取 `/epoch-weights/4`，不能拿 `/resume`（可能是 ep6）。"""
    paths: list[str] = []
    monkeypatch.setattr(hub_http, "_request", _fake_hub(paths, []))
    seen: dict[str, bytes] = {}

    def fake_eval(*, weights_bytes: bytes, **kwargs):
        seen["weights"] = weights_bytes
        return {"epoch": kwargs["epoch"], "wver": "x", "levels": []}

    monkeypatch.setattr(bc_ingest, "dispatch_bc_eval", fake_eval)

    run_epoch_eval(
        "http://hub",
        "t",
        JID,
        tmp_path / "log.jsonl",
        1,
        4,
        _course(tmp_path),
        None,
        lambda _m: None,
    )

    assert paths == [f"/jobs/{JID}/epoch-weights/4"]
    assert seen["weights"] == _epoch_weights(4)


def test_ledger_carries_hold_acc(tmp_path: Path) -> None:
    """#11 的前置：v6 第三头不得在入账时被丢掉。"""
    ledger = tmp_path / "log.jsonl"
    bc_ingest.ledger_bc_epoch(ledger, 2, {"epoch": 3, "hold_acc": 0.61})
    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["hold_acc"] == 0.61
    # legacy 行没有该键 ⇒ 写 None，旧账本仍可读。
    bc_ingest.ledger_bc_epoch(ledger, 2, {"epoch": 4})
    assert json.loads(ledger.read_text(encoding="utf-8").splitlines()[1])["hold_acc"] is None


@pytest.mark.parametrize("status", [404, 500])
def test_archive_failure_does_not_break_ingest(tmp_path: Path, monkeypatch, status: int) -> None:
    """归档是观测面：失败只跳过，指标照入账。"""
    def request(base: str, token: str, path: str, timeout: float = 0.0):
        if path.endswith("/bc-metrics"):
            return 200, json.dumps({"rows": _rows(1)}).encode("utf-8")
        return status, b"{}"

    monkeypatch.setattr(hub_http, "_request", request)
    wait = BcWait(jid=JID)
    ingest_bc_metrics(
        wait,
        hub_url="http://hub",
        token="t",
        jsonl_path=tmp_path / "log.jsonl",
        it=1,
        course=_course(tmp_path),
        cfg=None,
        logger=lambda _m: None,
    )
    assert wait.seen_metrics == 1
    assert _archived(tmp_path / "weights") == []
