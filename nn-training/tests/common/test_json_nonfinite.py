"""`common/fs` 的两个 JSON 写入口不许写出非标准字面量（2026-11-15）。

`json.dumps` 默认写出 `NaN` / `Infinity` / `-Infinity` —— 三个**非标准** JSON 字面量。TS 端
`JSON.parse` 遇到直接抛错，失败形态是「文件/账本打开就崩」，而不是「数值不准」；而 NaN/inf
在训练侧是**真实形状**（val_loss 发散、gnorm 爆炸、A-eval 的 delta 算不出来）。

口径只有一个实现：`common.stats.sanitize_json`（非有限 float → None）。本文件钉住
`atomic_write_json` 与 `JsonlLedger.append` 这两个 Chokepoint 都过它 —— 它们是全仓 JSON
文件 / JSONL 账本的写入口，收在这里比在几十个调用点各写一遍稳。

判据不用文本 grep：权重或路径里随便就撞出 `"NaN"` 子串，只能靠**解析器**判标准 JSON
（`parse_constant` 是 `json.loads` 对那三个字面量的唯一钩子）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from common.fs import JsonlLedger, append_jsonl, atomic_write_json
from common.stats import sanitize_json


def _strict_loads(raw: str) -> Any:
    """解析 JSON，并把 `NaN` / `Infinity` / `-Infinity` 变成**失败**（TS 端就是这么崩的）。"""

    def _reject(literal: str) -> None:
        raise AssertionError(f"写出了非标准 JSON 字面量 {literal}")

    return json.loads(raw, parse_constant=_reject)


def test_atomic_write_json_turns_non_finite_into_null(tmp_path: Path) -> None:
    out = tmp_path / "state.json"
    atomic_write_json(out, {"loss": float("nan"), "gnorm": float("inf"), "n": 3})
    got = _strict_loads(out.read_text(encoding="utf-8"))
    assert got == {"loss": None, "gnorm": None, "n": 3}


def test_append_jsonl_turns_non_finite_into_null(tmp_path: Path) -> None:
    ledger = JsonlLedger()
    path = tmp_path / "eval_log.jsonl"
    try:
        append_jsonl(
            path,
            {"it": 1, "delta": float("-inf"), "acc": float("nan"), "n": 2},
            ledger=ledger,
        )
    finally:
        ledger.close()
    lines = [_strict_loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines == [{"it": 1, "delta": None, "acc": None, "n": 2}]


def test_every_writer_shares_the_single_sanitizer() -> None:
    """口径单源：`bc_core` 的转发名必须**就是** `common.stats` 的那个对象。

    两个写入口（`common/fs`）+ BC 的 weights meta 共用一份实现；转发成了副本 = 口径又开始漂，
    而这族的漂移是静默的（一个写 `null`、一个写 `NaN`，读侧才炸）。
    """
    from worker.train import bc_core

    assert bc_core.sanitize_json is sanitize_json


def test_sanitize_json_leaves_finite_numbers_and_structure_alone() -> None:
    obj = {"a": 1, "b": 2.5, "c": True, "d": None, "e": [1, 2], "f": {"g": "x"}}
    assert sanitize_json(obj) == obj


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_sanitize_json_maps_each_non_finite(bad: float) -> None:
    assert sanitize_json(bad) is None
    assert sanitize_json({"k": [bad]}) == {"k": [None]}
