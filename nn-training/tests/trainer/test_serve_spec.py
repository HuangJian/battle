"""`trainer/loop_serve.ServeSpec` 的形状护栏（`python.refactor.md` §P0-3）。

`serve` 原来是 18 个参数（1 位置 + 17 关键字），其中 `prepare` / `max_steps` /
`max_seconds` 三个旋钮决定进程是**常驻**还是**跑一拍就走**，`pool` / `supervisor` 决定
e2e 是否碰进程与 torch。名字抄错只会静默落回默认值 —— 这里的断言钉住「入口只收一份
spec」，形状回退（又长出第二个位置参数）立刻红。
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from trainer.loop_serve import ServeSpec, serve

#: `(字段名, 缺省值)` —— 与 `serve` 收进 spec 之前的 18 个参数逐一对齐。
EXPECTED_FIELDS = [
    ("courses", None),
    ("argv", None),
    ("traj_root", "tmp"),
    ("control_file", None),
    ("iters", 0),
    ("poll_sec", 15.0),
    ("capacities", None),
    ("cache_courses", 0),
    ("cache_mb", 256.0),
    ("step_mode", True),
    ("max_seconds", 0.0),
    ("max_steps", 0),
    ("prepare", True),
    ("bun", None),
    ("pool", None),
    ("supervisor", None),
    ("now", None),
    ("sleep", None),
]


def test_spec_is_frozen_and_keyword_only() -> None:
    assert dataclasses.is_dataclass(ServeSpec)
    field = sorted(f.name for f in dataclasses.fields(ServeSpec))[0]  # 变量而非字面量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(ServeSpec(), field, None)
    assert all(f.kw_only for f in dataclasses.fields(ServeSpec)), (
        "`ServeSpec` 不是 kw_only ⇒ 18 个参数只是换了个地方写"
    )


def test_spec_field_table() -> None:
    rows = [(f.name, f.default) for f in dataclasses.fields(ServeSpec)]
    assert [name for name, _ in rows] == [name for name, _ in EXPECTED_FIELDS]
    for (name, default), (_, expected) in zip(rows, EXPECTED_FIELDS, strict=True):
        if name in {"now", "sleep"}:
            continue  # 缺省是绑定时捕获的 time 函数，启动期替换不掉
        assert default == expected, name


def test_serve_takes_only_the_spec() -> None:
    params = list(inspect.signature(serve).parameters.values())
    assert [p.name for p in params] == ["spec"]
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
