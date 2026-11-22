"""`remote.offline_eval.CloudEvalSpec` 的形状护栏（`python.refactor.md` §P0-3）。

`run_cloud_eval` 原来是 6 个必填 + 8 个带缺省的 14 个关键字。生产侧并不直接调它：
`plan_handoff._eval_job_builder` 造一张**关键字表**（`dict`），`CloudEvalRunner._loop` 再
`run_cloud_eval(**params)` 展开。那条路上键名错只在后台线程执行时才可能炸，而**取错值**
更糟、且完全静默：

  * `ts_code_sha256` 错 ⇒ 跨轮暖池 key 对不上（每轮重新 spawn 一池，冷启动白付一遍）；
  * `game_timeout_sec` 错 ⇒ 丢掉「显式给了数」与「没指定（0 = 走兜底 + 重试放大）」的区别；
  * `course_fp` 错 ⇒ 逐局行与课程配不上对（读数静默错位）。

收成 spec 后，那条路是一个对象：字段名在 `_eval_job_builder` 里就被 mypy 与本文件检查。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import get_args, get_type_hints

import pytest

import remote.offline_eval as OE
import remote.plan_handoff as PH
from remote.offline_eval import CloudEvalRunner, CloudEvalSpec, eval_plan_of, run_cloud_eval

ROOT = Path(__file__).resolve().parents[2]
MISSING = dataclasses.MISSING

#: `(字段名, 缺省值)` —— 与收进 spec 之前的 14 个形参逐一对齐（前 6 个无缺省）。
EXPECTED_FIELDS = [
    ("plan", MISSING),
    ("it", MISSING),
    ("weights_path", MISSING),
    ("eval_jsonl", MISSING),
    ("ts_root", MISSING),
    ("work_dir", MISSING),
    ("course", None),
    ("course_fp", ""),
    ("ts_code_sha256", ""),
    ("bun", ""),
    ("slots", 0),
    ("game_timeout_sec", 0.0),
    ("rollout_winrate", None),
    ("log", None),
]


def _plan():
    return eval_plan_of(
        SimpleNamespace(
            eval_stages="0",
            eval_games_per_stage=2,
            eval_every=1,
            stage_ids=[0],
            seeds_per_stage=1,
            stage_json=None,
        )
    )


def _spec(it: int = 1) -> CloudEvalSpec:
    return CloudEvalSpec(
        plan=_plan(),
        it=it,
        weights_path=Path("w.json"),
        eval_jsonl=Path("eval.jsonl"),
        ts_root="ts",
        work_dir="work",
    )


def test_spec_is_frozen_and_fully_keyword_only() -> None:
    rows = dataclasses.fields(CloudEvalSpec)
    field = rows[0].name  # 变量而非字面量（ruff B010）
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]


def test_field_table_matches_the_old_signature() -> None:
    got: list[tuple[str, object]] = []
    for f in dataclasses.fields(CloudEvalSpec):
        if f.default is not MISSING:
            got.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            got.append((f.name, f.default_factory()))
        else:
            got.append((f.name, MISSING))
    assert got == EXPECTED_FIELDS


def test_run_cloud_eval_takes_exactly_one_spec() -> None:
    params = inspect.signature(run_cloud_eval).parameters
    assert [p.name for p in params.values()] == ["spec"]
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_unknown_field_is_a_type_error() -> None:
    """拼错的字段名不许静默落缺省（旧形参下多余关键字也是 `TypeError`）。"""
    make: Callable[..., CloudEvalSpec] = CloudEvalSpec
    with pytest.raises(TypeError):
        make(
            plan=_plan(),
            it=1,
            weights_path=Path("w.json"),
            eval_jsonl=Path("e.jsonl"),
            ts_root="ts",
            work_dir="work",
            ts_code_sha="x",
        )


def test_runner_loop_hands_a_spec_not_a_keyword_table() -> None:
    """★ `CloudEvalRunner` 那条路：`_loop` 必须把 thunk 的产出**原样**交给 `run_cloud_eval`。

    本刀之前它是 `run_cloud_eval(**params)` —— 一张字典被展开。判据用 AST：`run_cloud_eval`
    的实参必须是**一个名字**（`spec`），不许出现 `**` 展开。
    """
    src = Path(OE.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "run_cloud_eval"
    ]
    assert len(calls) == 1, f"`run_cloud_eval(...)` 调用点 {len(calls)} 处（应恰好 1 处：runner 的循环）"
    call = calls[0]
    assert not call.keywords, f"restate：调用点仍在传关键字 {[k.arg for k in call.keywords]}"
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Name), ast.dump(call)


def test_production_builder_names_every_field_it_passes() -> None:
    """生产装配点（`plan_handoff._eval_job_builder`）用 `CloudEvalSpec(...)` 按名构造。

    允许它落缺省的两个字段：`bun`（空 ⇒ 解析成 TS 运行时自带的 bun）与 `rollout_winrate`
    （本轮不预置胜率）。其余必须逐个按名交齐。
    """
    src = Path(PH.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "CloudEvalSpec"
    ]
    assert len(calls) == 1, f"`CloudEvalSpec(...)` 出现 {len(calls)} 次（应恰好 1 次）"
    got = {k.arg for k in calls[0].keywords if k.arg is not None}
    want = {f.name for f in dataclasses.fields(CloudEvalSpec)}
    rely_on_default = {"bun", "rollout_winrate"}
    assert got == want - rely_on_default, (
        f"漏交 {sorted(want - rely_on_default - got)} / 多交 {sorted(got - want)}"
    )
    assert not calls[0].args, "装配点用位置实参构造 spec（kw_only 就是为了不许这样）"


def test_runner_type_narrows_the_build_port() -> None:
    """`CloudEvalRunner` 的装配端口是 `Callable[[int], CloudEvalSpec]`（不再是 `Any`）。"""
    params = inspect.signature(CloudEvalRunner.__init__).parameters
    assert list(params)[:2] == ["self", "build_job"]
    hints = get_type_hints(CloudEvalRunner.__init__)
    # `Callable[[int], CloudEvalSpec]` 的 get_args = ([int], CloudEvalSpec)
    assert get_args(hints["build_job"]) == ([int], CloudEvalSpec)
