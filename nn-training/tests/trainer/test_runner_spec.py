"""`trainer/batch_runner.RunnerSpec` 的形状护栏（`python.refactor.md` §P0-3）。

`BatchEvalRunner.__init__` 与 `dispatch_batch_bg` 原来是**同一张 13 参表的两份**，后者按
**位置**把 13 个实参转手给前者。相邻的 `unit_idx` / `unit_of` 都是 `int`、`init_sha16` 与
`run_id` 都是 `str` ⇒ 传错一位不报错，只在台账里出现「本单元之外」的键（iterId 命名空间
是 god/nn 不串键的唯一保证）。这里钉住三件事：

* 两个入口都只收一份 spec（位置转手段回退立刻红）；
* spec 是 frozen + kw_only，字段表 = 收参数时的逐条搬运（少一个旋钮就没人能再传它）；
* `kind` / `include_scorable` 的缺省仍在 spec 里（`kind=None` ⇒ 由 policy 推）。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from trainer.batch_runner import BatchEvalRunner, RunnerSpec, dispatch_batch_bg

#: `(字段名, 缺省值)` —— 与收进 spec 之前的 16 个参数逐一对齐（前 11 个无缺省）。
EXPECTED_FIELDS = [
    ("bun", dataclasses.MISSING),
    ("rl_path", dataclasses.MISSING),
    ("eval_log", dataclasses.MISSING),
    ("args", dataclasses.MISSING),
    ("cfg", dataclasses.MISSING),
    ("batch", dataclasses.MISSING),
    ("unit", dataclasses.MISSING),
    ("unit_idx", dataclasses.MISSING),
    ("unit_of", dataclasses.MISSING),
    ("run_id", dataclasses.MISSING),
    ("engine_epoch", dataclasses.MISSING),
    ("policy", "nn"),
    ("window_event", None),
    ("init_sha16", ""),
    ("kind", None),
    ("include_scorable", False),
]


def _minimal() -> RunnerSpec:
    return RunnerSpec(
        bun="bun",
        rl_path=None,
        eval_log=Path("tmp/spec-probe.jsonl"),
        args=object(),
        cfg={},
        batch={"batch_id": "b"},
        unit={"rung": "x"},
        unit_idx=0,
        unit_of=1,
        run_id="run",
        engine_epoch="epoch",
    )


def test_spec_is_frozen_and_keyword_only() -> None:
    assert dataclasses.is_dataclass(RunnerSpec)
    spec = _minimal()
    field = sorted(f.name for f in dataclasses.fields(RunnerSpec))[0]  # 变量而非字面量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(RunnerSpec)), (
        "`RunnerSpec` 不是 kw_only ⇒ 16 个参数只是换了个地方写"
    )


def test_spec_field_table() -> None:
    rows = [(f.name, f.default) for f in dataclasses.fields(RunnerSpec)]
    assert [name for name, _ in rows] == [name for name, _ in EXPECTED_FIELDS]
    for (name, default), (_, expected) in zip(rows, EXPECTED_FIELDS, strict=True):
        assert default == expected, name


def test_defaults_keep_the_old_semantics() -> None:
    """缺省不只是「能构造」：`kind=None` 由 policy 推、`include_scorable` 关。"""
    spec = _minimal()
    assert spec.kind is None and spec.include_scorable is False
    assert spec.policy == "nn" and spec.init_sha16 == "" and spec.window_event is None


def test_both_entries_take_only_the_spec() -> None:
    init_params = list(inspect.signature(BatchEvalRunner.__init__).parameters)
    assert init_params == ["self", "spec"]
    dispatch_params = list(inspect.signature(dispatch_batch_bg).parameters.values())
    assert [p.name for p in dispatch_params] == ["spec"]
    assert dispatch_params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
