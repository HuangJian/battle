"""`common/task_key.TaskKeySpec` 的形状护栏（`python.refactor.md` §P0-3）。

`task_key` 原来是 16 个参数：5 个身份位 + 11 个带缺省的开关。其中 9 个开关是同型的
「激活 ⇒ 键尾追加一段 `:xxx`」字符串，**位置相邻且全同型** —— 错一位不报错，只是键变长
或变短，后果是取消帧打不中这一局（训练侧继续白烧卡），或者两态不同的局撞成同一个键
（full / replay 语料互相覆盖）。

这里钉住：spec 是 frozen + kw_only · 16 个字段与缺省逐项对齐旧参数 · `task_key` 只收一份
spec · 未知关键字 `TypeError`。键的**逐字节**配方不在这里，在 `test_task_cancel.py` 的固定
向量里（与 `tools/agent` 的 TS 实现逐字节对拍）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable

import pytest

from common.task_key import TaskKeySpec, task_key

#: `(字段名, 缺省值)` —— 与收进 spec 之前的 16 个参数逐一对齐（前 5 个无缺省）。
EXPECTED_FIELDS = [
    ("iter_id", dataclasses.MISSING),
    ("mode", dataclasses.MISSING),
    ("kind", dataclasses.MISSING),
    ("stage", dataclasses.MISSING),
    ("seed", dataclasses.MISSING),
    ("stage_json_hash", ""),
    ("course_fp", ""),
    ("decision_events", ""),
    ("decision_k", ""),
    ("label_mode", ""),
    ("skip_steel_only_shots", ""),
    ("break_bricks_on_path", ""),
    ("suppress_useless_fire", ""),
    ("suppress_lead_miss_fire", ""),
    ("opening_connect", ""),
    ("corpus_fmt", ""),
]


def _bare() -> TaskKeySpec:
    return TaskKeySpec(iter_id="it1", mode="rollout", kind="rollout", stage=2000, seed=7)


def test_spec_is_frozen_and_fully_keyword_only() -> None:
    """frozen：错就抛，不静默改；kw_only：16 个同型字段不许靠位置传。"""
    spec = _bare()
    field = sorted(f.name for f in dataclasses.fields(TaskKeySpec))[0]  # 变量而非字面量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(spec)), [
        f.name for f in dataclasses.fields(spec) if not f.kw_only
    ]


def test_fields_and_defaults_match_the_old_signature() -> None:
    """收 spec 前后**逐项**等价：字段名 / 顺序 / 缺省值，以及没有多余字段。"""
    got = []
    for f in dataclasses.fields(TaskKeySpec):
        if f.default is not dataclasses.MISSING:
            got.append((f.name, f.default))
        elif f.default_factory is not dataclasses.MISSING:
            got.append((f.name, f.default_factory()))
        else:
            got.append((f.name, dataclasses.MISSING))
    assert got == EXPECTED_FIELDS


def test_task_key_takes_exactly_one_positional_spec() -> None:
    """入口只剩 `(spec)`：16 个位置参数一旦回潮，这条立刻红。"""
    params = inspect.signature(task_key).parameters
    assert [p.name for p in params.values()] == ["spec"]
    assert all(
        p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params.values()
    )
    assert [p.default for p in params.values()] == [inspect.Parameter.empty]


def test_defaults_are_byte_identical_to_the_old_recipe() -> None:
    """全缺省 ⇒ 键只有身份段（老键逐字节不变；键配方细目在 test_task_cancel.py）。"""
    assert task_key(_bare()) == "it1:rollout:rollout:2000:7"


def test_unknown_field_is_a_type_error() -> None:
    """拼错的字段名不许静默落缺省：旧签名下多余关键字也直接 `TypeError`，误差面不因收 spec 而变宽。"""
    make: Callable[..., TaskKeySpec] = TaskKeySpec
    with pytest.raises(TypeError):
        make(
            iter_id="it1",
            mode="rollout",
            kind="rollout",
            stage=2000,
            seed=7,
            break_brick_on_path="1",
        )
