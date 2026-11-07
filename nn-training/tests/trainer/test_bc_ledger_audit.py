"""B8：BC 账本完整性审计（缺号 / 重复 / 末轮必达）。

报告 §6 的实证：107 条 `bc_epoch` 只覆盖 77 个 epoch，缺 5/25/80、30 条重复；
面板却把点连成一条连续曲线。`audit_bc_ledger` 负责把「不全」说出来。
"""

from __future__ import annotations

from trainer.bc_ingest import audit_bc_ledger


def test_complete_ledger() -> None:
    a = audit_bc_ledger([1, 2, 3, 4], 4)
    assert a["complete"] is True
    assert a["missing"] == [] and a["duplicates"] == 0 and a["observed"] == 4


def test_missing_epochs_are_named() -> None:
    a = audit_bc_ledger([1, 2, 3, 4, 6], 6)
    assert a["missing"] == [5]
    assert a["complete"] is False


def test_last_epoch_must_land() -> None:
    a = audit_bc_ledger(list(range(1, 80)), 80)
    assert a["missing"] == [80], "ep80 必达（交付轮不能悄悄缺）"


def test_duplicates_counted_and_not_double_counted_as_present() -> None:
    a = audit_bc_ledger([1, 1, 2, 2, 2, 3], 3)
    assert a["duplicates"] == 3
    assert a["observed"] == 3
    assert a["complete"] is False, "有重复 ⇒ 不完备（可能同时有丢行）"


def test_unknown_expected_only_reports_observed() -> None:
    a = audit_bc_ledger([2, 4, 4], 0)
    assert a["missing"] == []
    assert a["observed"] == 2
    assert a["duplicates"] == 1
    assert a["complete"] is False


def test_empty_ledger_with_expected_is_incomplete() -> None:
    a = audit_bc_ledger([], 3)
    assert a["missing"] == [1, 2, 3]
    assert a["complete"] is False
