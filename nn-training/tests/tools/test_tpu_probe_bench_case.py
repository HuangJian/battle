"""`tools/tpu-probe.py::bench_case` 的形状守卫（`python.refactor.md` §P0-3，批 H2，2026-11-22）。

批 H2 收掉工具面最后一条宽签名：`bench_case(bk, chunks, epochs, *, sync_mode, ref_cache,
ref_model, warmup, repeat, seed)` = 3 位置 + 6 关键字 → 一个 `BenchCaseSpec`。

坏点（为什么值得动，不是「参数多不好看」）：

* `ref_model` 与 `ref_cache` 是 **D1/D2 段的两条互斥路径** —— 一个每步重算、一个每 chunk
  一次。按位置传时互换，「ref 优化收益」正好测出负号，而报告照样打印得出来；
* `warmup` / `repeat` / `seed` 是三个 int 挨着：`warmup` 与 `repeat` 互换把 XLA 编译时间算进
  稳态（读数系统性偏高），`seed` 当 `warmup` 则首步大山峰留在计时里。

装载法：`tools/tpu-probe.py` 文件名带连字符（不是可导入模块，`pyproject.toml` 也免了 N999），
所以走 `importlib.util.spec_from_file_location`；模块顶层只有常量与类定义，import 无害。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any, cast

import pytest

pytest.importorskip("torch")

from tests.tools.probe_loader import load_probe

MISSING = dataclasses.MISSING

T = load_probe("tpu-probe")

#: 旧签名的 9 个形参，按原顺序（前 3 个位置、后 6 个关键字）。
FIELDS: list[tuple[str, object]] = [
    ("bk", MISSING),
    ("chunks", MISSING),
    ("epochs", MISSING),
    ("sync_mode", "batched"),
    ("ref_cache", None),
    ("ref_model", None),
    ("warmup", 1),
    ("repeat", 2),
    ("seed", 0),
]


def _table(cls: type) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


def test_field_table_matches_the_old_signature() -> None:
    assert _table(T.BenchCaseSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(T.BenchCaseSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = T.BenchCaseSpec(bk="bk", chunks=[], epochs=1)
    name = dataclasses.fields(spec)[-1].name  # 变量化的字段名：直接 `setattr(x, "repeat", …)` 会撞 B010
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, name, 3)


def test_entry_takes_exactly_one_spec() -> None:
    assert list(inspect.signature(T.bench_case).parameters) == ["case"]


def test_old_positional_form_is_rejected() -> None:
    bench = cast(Callable[..., object], T.bench_case)
    with pytest.raises(TypeError):
        bench("bk", [], 1, "batched")


def test_unknown_field_is_a_type_error() -> None:
    """`ref` / `ref_cach` 这类拼错不许静默落缺省（D1/D2 的收益方向全押在这两个字段上）。"""
    make = cast(Callable[..., object], T.BenchCaseSpec)
    with pytest.raises(TypeError):
        make(bk="bk", chunks=[], epochs=1, ref="model")


def test_defaults_keep_the_probe_baseline() -> None:
    """缺省必须与旧签名逐字一致：`repeat=2`、`warmup=1`、`sync_mode="batched"`、无 ref。"""
    spec = T.BenchCaseSpec(bk="bk", chunks=[], epochs=4)
    assert (spec.sync_mode, spec.warmup, spec.repeat, spec.seed) == ("batched", 1, 2, 0)
    assert spec.ref_cache is None and spec.ref_model is None
