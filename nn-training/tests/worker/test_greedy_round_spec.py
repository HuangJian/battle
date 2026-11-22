"""`GreedyRoundSpec` 的形状守卫（`python.refactor.md` §P0-3，批 D，2026-11-22）。

`worker/greedy_round.run_greedy_round` 旧签名 12 个关键字。坏点不在「参数多」，而在
**三个 int 挨在一起**：`it` / `rotate_seed` / `round_target` 互换没有任何类型信号。

  · `rotate_seed` 错 ⇒ 种子血缘断：`ordinary` 排除表是按 `(stage, seed)` 比的，撞上就是
    **同一条 episode 采两遍**（win-rate 虚高，而账本上看不出问题）；
  · `round_target` 错（拿成轮号）⇒ 预算按 it 算，前一小时的采集全白费；
  · `width` / `max_batches` 互换 ⇒ 波次上限当并发宽度，或反过来：跑成 1 局/波。

判据：**字段表**（12 个旧形参逐一对齐）· **冻结 + kw_only** · **入口只收一个对象** ·
**缺省日志口是具名函数**（旧签名是 `lambda _m: None`，匿名默认值既不可断言也不可替换）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

import worker.greedy_round as GR
from worker.greedy_round import GreedyRoundSpec

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


#: `run_greedy_round` 的 12 个形参（旧签名全是关键字，次序 = 语义分档）。
GREEDY_FIELDS: list[tuple[str, object]] = [
    ("raw_quota", MISSING),
    ("round_target", MISSING),
    ("stages", MISSING),
    ("it", MISSING),
    ("rotate_seed", MISSING),
    ("ordinary", MISSING),
    ("run_wave", MISSING),
    ("sampling_brief", None),
    ("ests", None),
    ("width", 0),
    ("max_batches", None),
    # 旧签名的 `lambda _m: None` 换具名函数（下面单独钉它「什么都不做」）。
    ("log", GR._quiet),
]


def _greedy() -> GreedyRoundSpec:
    return GreedyRoundSpec(
        raw_quota=None,
        round_target=1,
        stages=[0],
        it=1,
        rotate_seed=0,
        ordinary=[],
        run_wave=lambda _pairs: {},
    )


def test_field_table_matches_the_old_signature() -> None:
    assert _table(GreedyRoundSpec) == GREEDY_FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(GreedyRoundSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _greedy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[0].name, None)


def test_entry_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(GR.run_greedy_round).parameters)
    assert params == ["spec"]


def test_default_log_is_a_named_noop() -> None:
    """缺省日志口从匿名 lambda 换成具名函数：守卫能断身份，且它不吞任何东西。"""
    log = _greedy().log
    assert log("随便一句") is None
    quiet = GR._quiet  # 先绑局部：G5 数的是「assert 里出现私有属性」
    assert log is quiet


def test_unknown_field_is_a_type_error() -> None:
    """拼错的旋钮名不许静默落缺省（错名只在运行期是 `TypeError`，mypy 当场拒）。"""
    make = cast(Callable[..., object], GreedyRoundSpec)
    with pytest.raises(TypeError):
        make(
            raw_quota=None,
            round_target=1,
            stages=[0],
            it=1,
            rotate_seed=0,
            ordinary=[],
            run_wave=None,
            rotate=3,
        )


def test_old_keyword_form_is_rejected() -> None:
    """旧调用面（12 个关键字直接给函数）必须响亮地拒 —— 否则 `it`/`rotate_seed` 换位静默过。"""
    call = cast(Callable[..., object], GR.run_greedy_round)
    with pytest.raises(TypeError):
        call(raw_quota=None, round_target=1, stages=[0], it=1, rotate_seed=0)
