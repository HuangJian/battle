"""tests/hub/test_offline_metrics.py —— 只带统计的补传（`?parts=metrics`，2026-10-23）。

`plan/offline-deliver-full-and-gaps` §3.4 / S4。抖动的另一半：「权重发不动，但读数不能丢」
——以及 `anchors` 档里锚点之间那几轮，它们从来没有权重上行的机会。

钉住四条：

  1. **落点只有两处**：`<run>/metrics.jsonl` 与课程侧 `training_log.jsonl`；
  2. **不创建 `it-N/`**（没有权重 ⇒ 不该让 `resume_anchor` 看见一个空轮）；
  3. **幂等**：同一 `(it, weights_fp)` 重投 ⇒ 跳过，不追加第二行；
  4. **不可信输入**：体不是对象 / 缺 `rows` / `it` 非法 ⇒ `ProtocolError`，**不落任何东西**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError

COURSE = "c5-gae"
RUN = "r" * 8


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """权重归档根指到 tmp（同 `test_offline_backfeed`：别往真 `nn-training/weights/` 撒）。"""
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


def _store(tmp_path: Path):
    from hub.server import _JobStore

    d = tmp_path / COURSE
    (d / "remote-jobs").mkdir(parents=True, exist_ok=True)
    return _JobStore(d / "remote-jobs", d / "training_log.jsonl")


def _row(it: int, *, wfp: str = "") -> dict:
    return {
        "it": it,
        "row": {
            "weights_fp": wfp or f"fp-{it}",
            "opt_bytes": 1234,
            "agg": {"kl": 0.0123, "entropy": 1.5, "policy": 0.001, "value": 0.4},
            "report": {
                "games": 94,
                "winRate": 0.5,
                "outcomes": {"stage_clear": 50, "lives_exhausted": 44},
            },
            "wall_sec": 15.0,
            "steps": 24576,
        },
    }


def test_metrics_lands_in_both_ledgers_and_creates_no_round_dir(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    res = store.store_offline_metrics(
        {"run_id": RUN, "rows": [_row(1), _row(2), _row(3)]}
    )
    assert res["status"] == "accepted"
    assert res["rows"] == 3 and res["skipped"] == 0

    mpath = store.offline_run_dir(RUN) / "metrics.jsonl"
    lines = [json.loads(ln) for ln in mpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    assert [ln["it"] for ln in lines] == [1, 2, 3]
    assert all(ln["event"] == "offline_metrics" for ln in lines)
    assert all(ln["parts"] == "metrics" for ln in lines)
    # ★ 不创建 it-N/：这一轮没有权重
    assert not (store.offline_run_dir(RUN) / "it-001").exists()


def test_metrics_is_idempotent_by_it_and_fingerprint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    body = {"run_id": RUN, "rows": [_row(1), _row(2)]}
    first = store.store_offline_metrics(body)
    second = store.store_offline_metrics(body)
    assert first["rows"] == 2 and first["skipped"] == 0
    assert second["rows"] == 0 and second["skipped"] == 2, "同轮重投必须跳过"
    # 换指纹（换了权重）⇒ 算新一轮
    third = store.store_offline_metrics({"run_id": RUN, "rows": [_row(1, wfp="other")]})
    assert third["rows"] == 1


def test_metrics_only_rows_do_not_freeze_the_active_pointer(tmp_path: Path) -> None:
    """★ 评审 P0：统计行进课程账本时必须是 `weights_landed=false` 的临时行。

    否则 `_ledger_has_newer_iter` 会把「统计跑在前面」读成「权重已落」，活动权重指针永久
    停在旧轮。
    """
    store = _store(tmp_path)
    store.store_offline_metrics({"run_id": RUN, "rows": [_row(10)]})
    ledger = [
        json.loads(ln)
        for ln in store.jsonl_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    iters = [r for r in ledger if r.get("event") == "iteration"]
    assert iters and iters[-1]["iter"] == 10
    assert iters[-1]["weights_landed"] is False, "统计行必须可被读方识别为没有权重"
    assert store._ledger_has_newer_iter(5) is False, "统计行不能冻结活动权重"
    assert store._ledger_has_newer_iter(10) is False


def test_unknown_parts_is_rejected_loudly() -> None:
    """★ 评审 P2：未实现的 `?parts=weights/opt` 不能静默落进整轮分支。"""
    from hub.offline_ingest import ingest_offline_artifact

    status, res = ingest_offline_artifact(
        None, "", {"run_id": RUN, "rows": [_row(1)]}, parts="weights"
    )
    assert status == 400
    # 精确形状（不用子串判据：`tests/test_source_text_assert_budget.py` 在数这个）
    assert res == {"error": "未知 parts='weights'（当前只支持 metrics）"}


def test_metrics_rejects_untrusted_input_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for body in (
        ["not", "a", "dict"],
        {"run_id": RUN},  # 缺 rows
        {"run_id": RUN, "rows": []},  # 空 rows
    ):
        with pytest.raises(ProtocolError):
            store.store_offline_metrics(body)
    assert not store.offline_run_dir(RUN).exists(), "拒收时一个字节都不该落"


def test_metrics_skips_rows_without_a_usable_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    res = store.store_offline_metrics(
        {
            "run_id": RUN,
            "rows": [
                {"row": {"weights_fp": "x"}},  # 没 it
                {"it": "3"},  # it 不是整数
                {"it": -1},  # 负数
                "junk",  # 不是对象
                _row(7),  # 唯一合法的一条
            ],
        }
    )
    assert res["rows"] == 1 and res["it"] == [7]
