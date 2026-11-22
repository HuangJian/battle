"""`BcEvalSpec` 的形状守卫（`python.refactor.md` §P0-3，批 C，2026-11-22）。

`worker.bc_eval.dispatch_bc_eval` 旧签名 12 个关键字，坏在四对「看起来可以互换」的：

  · `it` / `epoch` —— 评估行挂错轮（表里像「每轮都评了」，查时对不上）；
  · `eval_levels` / `course` + `course_env` —— 图源是一对互斥开关：`course_env=True`
    时 `eval_levels` 被**静默忽略**（传了也不报错），少传 `course` 则是空图源；
  · `course_max_ticks` —— 写进行的面板值，错 ⇒ 该行与别的面板不可相减（B4 口径）；
  · `meta_path` —— 机器健康账落点，错 ⇒ 节点页「今日贡献」系统性漏计（2026-11-13 报障）。

判据：**字段表**（12 个旧形参逐一对齐）· **冻结 + kw_only** · **入口只收一个对象**。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

import worker.bc_eval as BE
from worker.bc_eval import BcEvalSpec

MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    """`(字段名, 缺省值)`；无缺省记 `MISSING`（与 `dataclasses.fields` 同口径）。"""
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: `dispatch_bc_eval` 的 12 个形参（次序 = 必填 → 图源 → 观测）。
BC_EVAL_FIELDS: list[tuple[str, object]] = [
    ("weights_bytes", MISSING),
    # `course_env=True` 时被忽略（图源改看 `course`）。
    ("eval_levels", MISSING),
    ("games_per_stage", MISSING),
    ("it", MISSING),
    ("epoch", MISSING),
    # 课程声明的面板：写进评估行，跨面板不可相减。
    ("course_max_ticks", None),
    ("cfg", None),
    ("nodes", None),
    # `course_env=True` 时的课程对象（`stage_ids` / `max_ticks` / `difficulty` / `player`）。
    ("course", None),
    # 图源开关：True = 直接继承课程解析后的 stage 集与环境覆盖。
    ("course_env", False),
    # 机器健康账落点（`dist-agent-meta.jsonl`；与训练同一份）。
    ("meta_path", None),
    # 缺省 = 本模块的 `_log`（所以这里比较的是同一对象，不是 `None`——与本批其余三个 spec
    # 相反：`bc_eval` 是它自己的日志出口的拥有者，没有延迟解析的需要）。
    ("log", BE._log),
]


def test_field_table_matches_the_old_signature() -> None:
    assert _table(BcEvalSpec) == BC_EVAL_FIELDS


def test_dispatch_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(BE.dispatch_bc_eval).parameters)
    assert params == ["spec"]


def _minimal() -> BcEvalSpec:
    return BcEvalSpec(weights_bytes=b"x", eval_levels=[], games_per_stage=1, it=1, epoch=1)


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(BcEvalSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(_minimal(), rows[0].name, None)


def test_old_keyword_form_is_rejected() -> None:
    """旧调用面（12 个关键字直接给函数）必须响亮地拒——否则 `it` / `epoch` 换位静默过。"""
    call = cast(Callable[..., object], BE.dispatch_bc_eval)
    with pytest.raises(TypeError):
        call(weights_bytes=b"x", eval_levels=[], games_per_stage=1, it=1, epoch=2)


def test_unknown_field_is_a_type_error() -> None:
    make = cast(Callable[..., object], BcEvalSpec)
    with pytest.raises(TypeError):
        make(weights=b"x", eval_levels=[], games_per_stage=1, it=1, epoch=1)
