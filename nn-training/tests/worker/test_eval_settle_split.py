"""`settle_eval_summary` 拆分的契约守卫（engineering §344，2026-11-25）。

入口只做装配：读数（账本 / 兜底）→ 缺口三分 → summary 行 → 日志。行为守卫钉四件事：

1. 缺口恒等式 `dropped == never_dispatched + lost + carried`（含第二项残留）；
2. summary 字段闭集与落盘序；
3. `missing` 的排序 / 上限 / reason 优先级；
4. 零局与 `eval_bundle` 的两条日志路径（二选一）。

不读生产源码：`getsource` 会把断言计进文本断言口径 B，形状由 `test_python_shape_budget`
的存量闸负责。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

import pytest

from worker import eval_track


class _Bundle:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def add(self, key: str, value: str) -> None:
        self.events.append(("add", key, value))

    def note(self, value: str) -> None:
        self.events.append(("note", value, ""))


def _spec(
    tmp_path: Path,
    *,
    total: int,
    pairs: list[tuple[int, int]],
    landed: set[tuple[int, int]],
    left_pairs: set[tuple[int, int]] | None = None,
    left_pending: int = 0,
    close_reason: str | None = "settled",
    bundle: _Bundle | None = None,
    rollout_winrate: float | None = 0.5,
) -> tuple[eval_track.EvalSettleSpec, list[dict]]:
    rows: list[dict] = []
    spec = eval_track.EvalSettleSpec(
        eval_jsonl=tmp_path / "eval.jsonl",
        key16="wver0123456789ab",
        it=3,
        pairs=pairs,
        total=total,
        landed=landed,
        wins=[1],
        cleared_total=[1],
        outcomes={"win": 1},
        node_games={"n1": 1},
        append_row=lambda row: rows.append(row),
        t_eval_start=time.time() - 10,
        rollout_winrate=rollout_winrate,
        course_fp="fp",
        left_pending=left_pending,
        left_pairs=left_pairs,
        close_reason=close_reason,
        eval_bundle=bundle,
    )
    return spec, rows


@pytest.mark.parametrize(
    ("total", "pairs", "landed", "left", "expect"),
    [
        (4, [(0, 1), (0, 2), (0, 3), (0, 4)], {(0, 1), (0, 2), (0, 3), (0, 4)}, set(), (0, 0, 0, 0)),
        (4, [(0, 1), (0, 2), (0, 3), (0, 4)], {(0, 1), (0, 2)}, {(0, 3), (0, 4)}, (2, 2, 0, 0)),
        (4, [(0, 1), (0, 2), (0, 3), (0, 4)], {(0, 1), (0, 2)}, {(0, 4)}, (2, 1, 1, 0)),
        (6, [(0, i) for i in range(1, 11)], {(0, i) for i in range(1, 6)}, set(), (5, 0, 1, 4)),
        (0, [], set(), None, (0, 0, 0, 0)),
    ],
)
def test_gap_triangle_is_exact(
    tmp_path: Path,
    total: int,
    pairs: list[tuple[int, int]],
    landed: set[tuple[int, int]],
    left: set[tuple[int, int]] | None,
    expect: tuple[int, int, int, int],
) -> None:
    spec, rows = _spec(tmp_path, total=total, pairs=pairs, landed=landed, left_pairs=left)
    eval_track.settle_eval_summary(spec)
    row = rows[0]
    assert row["dropped"] == row["never_dispatched"] + row["lost"] + row["carried"]
    assert (row["dropped"], row["never_dispatched"], row["lost"], row["carried"]) == expect


def test_summary_row_keeps_its_field_order(tmp_path: Path) -> None:
    pairs = [(0, 1), (0, 2)]
    spec, rows = _spec(
        tmp_path, total=2, pairs=pairs, landed=set(pairs), left_pending=0
    )
    eval_track.settle_eval_summary(spec)
    assert list(rows[0]) == [
        "event",
        "iter",
        "wver",
        "time",
        "sec",
        "games",
        "wins",
        "winRate",
        "clears",
        "clearRate",
        "outcomes",
        "dropped",
        "never_dispatched",
        "lost",
        "carried",
        "left_pending",
        "close_reason",
        "eval_tail_race_grants",
        "eval_dup_games",
        "frozenSec",
        "missing",
        "rolloutWinRate",
        "nodes",
        "ring_intr_mean",
        "ring_kill_rate",
        "ring_ttk_mean",
        "inner_press_frac",
        "kills_mean",
        "zero_kill_frac",
        "phits_mean",
        "pickup_mean",
        "timeout_frac",
        "course_fp",
    ]
    assert rows[0]["games"] == 2
    assert rows[0]["wins"] == 1
    assert rows[0]["nodes"] == {"n1": 1}


def test_missing_is_sorted_capped_and_reasoned(tmp_path: Path) -> None:
    pairs = [(0, i) for i in range(1, 26)]
    (tmp_path / "dist-agent-meta.jsonl").write_text(
        json.dumps(
            {"mode": "eval", "it": 3, "ok": False, "stage": 0, "seed": 2, "reason": "node-failed"}
        )
        + "\n",
        encoding="utf-8",
    )
    spec, rows = _spec(
        tmp_path,
        total=25,
        pairs=pairs,
        landed=set(),
        left_pairs={(0, 1)},
        left_pending=25,
    )
    eval_track.settle_eval_summary(spec)
    missing = cast(list[dict[str, object]], rows[0]["missing"])
    assert len(missing) == eval_track.MISSING_MAX
    assert [m["seed"] for m in missing] == list(range(1, 21))
    assert missing[0]["reason"] == "never-dispatched"
    assert missing[1]["reason"] == "node-failed"
    assert missing[2]["reason"] == "undispatched"


def test_zero_games_is_unknown_and_logs_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(eval_track, "log", logs.append)
    spec, rows = _spec(tmp_path, total=0, pairs=[], landed=set())
    eval_track.settle_eval_summary(spec)
    row = rows[0]
    assert row["games"] == 0
    assert row["winRate"] is None
    assert row["clearRate"] is None
    assert row["timeout_frac"] is None
    assert logs == ["[eval] it3: no game settled within window — nothing recorded"]


def test_eval_bundle_replaces_the_log_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logs: list[str] = []
    monkeypatch.setattr(eval_track, "log", logs.append)
    bundle = _Bundle()
    spec, _rows = _spec(
        tmp_path, total=1, pairs=[(0, 1)], landed={(0, 1)}, bundle=bundle
    )
    eval_track.settle_eval_summary(spec)
    assert logs == []
    assert [e[0] for e in bundle.events] == ["add"]
    assert bundle.events[0][1] == "winRate"

    empty_bundle = _Bundle()
    spec, _rows = _spec(
        tmp_path, total=0, pairs=[], landed=set(), bundle=empty_bundle
    )
    eval_track.settle_eval_summary(spec)
    assert [e[0] for e in empty_bundle.events] == ["note"]
