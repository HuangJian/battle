"""`dispatch_bc_eval` 拆分的契约守卫（长函数存量，2026-11-25）。

切法：入口只做装配（节点门 / 权重 / 槽位 / 图源循环），**单图源**的「派发 → 收齐 → 聚合」
整段住在 `_eval_source`（原 `for src in sources` 的循环体逐字搬出）。这个文件钉四件事：

1. 入口是编排：≤100 行，只调装配与 `_eval_source`，不再自己跑局/聚合；
2. `_eval_source(run, src, slots)` 的签名与所有权：局 + 槽位线程 + 聚合 + 机器健康账都在它里面；
3. `_EvalRun` 的字段是**闭集**（加字段 = 改这里，防止把入口的局部变量偷偷长回去）；
4. 助手不带新的顶层/函数内 import 边（`queue` / `threading` 是原循环体的既有延迟 import）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import worker.bc_eval as BE

SOURCE = Path(BE.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

RUN_FIELDS = [
    "iter_id",
    "wver",
    "it",
    "games_per_stage",
    "course_max_ticks",
    "meta_path",
    "log",
]


def _fn(name: str) -> ast.FunctionDef:
    hits = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(hits) == 1, name
    return hits[0]


def _calls(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_entry_is_orchestration_only() -> None:
    fn = _fn("dispatch_bc_eval")
    assert (fn.end_lineno or 0) - fn.lineno + 1 <= 100
    calls = _calls(fn)
    assert {"_push_eval_weights", "_eval_slots", "_eval_source", "_build_eval_sources"} <= calls
    # 单局与聚合的所有权在 `_eval_source`：入口再碰它们 = 拆分回潮。
    assert not (
        {"_run_bc_eval_one", "aggregate_eval_rows", "build_eval_panel", "record_agent_meta"} & calls
    )


def test_source_unit_owns_games_threads_and_aggregation() -> None:
    entry = BE._eval_source
    params = list(inspect.signature(entry).parameters)
    assert params == ["run", "src", "slots"]
    calls = _calls(_fn("_eval_source"))
    assert {
        "_run_bc_eval_one",
        "aggregate_eval_rows",
        "build_eval_panel",
        "record_agent_meta",
        "record_eval_failed",
    } <= calls
    # 槽位线程由这个单元起、自己 join（原循环体语义）。
    assert {"Runtime", "thread", "join"} <= calls


def test_run_context_is_a_closed_frozen_set() -> None:
    cls = BE._EvalRun
    assert [f.name for f in dataclasses.fields(cls)] == RUN_FIELDS
    assert all(f.kw_only for f in dataclasses.fields(cls))
    row = cls(
        iter_id="i",
        wver="w",
        it=1,
        games_per_stage=1,
        course_max_ticks=None,
        meta_path=None,
        log=lambda m: None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(row, RUN_FIELDS[0], "other")


def test_helpers_add_no_import_edges() -> None:
    for name in ("_push_eval_weights", "_eval_slots", "_eval_source"):
        mods: list[str] = []
        for n in ast.walk(_fn(name)):
            if isinstance(n, ast.Import):
                mods.extend(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                mods.append(n.module or "")
        assert mods <= ["queue", "threading"], (name, mods)
