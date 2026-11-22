"""`FetchSpec` 的形状守卫（`python.refactor.md` §P0-3，批 F2，2026-11-22）。

批 F2：`remote/tailscale_boot.fetch_guarded`(1 位置 + 8 关键字 → 1 个 `FetchSpec`)。
调用面 8 处（`notebook_boot` 1 · `offline_boot` 1 · 护栏用例 6）。

坏点：`idle_timeout`（停滞多久算死）与 `total_timeout`（整份预算）是两个相邻 float —— 互换后
**停滞判据变成整份预算、整份预算变成停滞判据**：要么一个 6 KB/s 的坏签永远挂着（没有停滞判据），
要么正常下载在几秒后被总预算砍断，而两次都只表现为「取包失败」。`attempts`（重抽次数）与
`reroll`（开关）相邻且一个 int 一个 bool（`True` 能被读成 1）。`log` 与 `label` 相邻（回调 vs 名字）
——互换后进度行里打的是函数对象、传输账不可 grep。

除形状外钉住：三个缺省（停滞预算 = `BOOT_IDLE_TIMEOUT_SEC`、整份不设上限、`attempts=3` / `reroll` 开）
与「缺省回调是具名函数而不是每次现造的 lambda」——后者让「谁在写进度行」可判。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

from remote import tailscale_boot as ts

MISSING = dataclasses.MISSING


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


#: 旧 `fetch_guarded` 的 9 个形参（第 1 个位置）。
FIELDS: list[tuple[str, object]] = [
    ("url", MISSING),
    ("headers", None),
    ("log", None),
    ("label", ""),
    ("idle_timeout", ts.BOOT_IDLE_TIMEOUT_SEC),
    ("total_timeout", None),
    ("attempts", 3),
    ("reroll", True),
    ("opener", None),
]


def test_field_table_matches_the_old_signature() -> None:
    assert _table(ts.FetchSpec) == FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(ts.FetchSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = ts.FetchSpec(url="http://hub/code")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[-1].name, object())


def test_entry_takes_exactly_one_spec() -> None:
    entry = ts.fetch_guarded
    assert list(inspect.signature(entry).parameters) == ["spec"]


def test_old_form_is_rejected() -> None:
    call = cast(Callable[..., object], ts.fetch_guarded)
    with pytest.raises(TypeError):
        call("http://hub/code", label="code.zip")


def test_progress_and_budget_are_distinct_knobs() -> None:
    """停滞判据与整份预算是两个字段，谁都不能冒充另一个（互换 = 静默的行为切换）。"""
    spec = ts.FetchSpec(url="u", idle_timeout=20.0, total_timeout=None)
    assert spec.idle_timeout == 20.0
    assert spec.total_timeout is None
    other = ts.FetchSpec(url="u", idle_timeout=ts.BOOT_IDLE_TIMEOUT_SEC, total_timeout=300.0)
    assert other.total_timeout == 300.0
    assert other.attempts == 3 and other.reroll is True


def test_default_progress_sink_is_a_named_function() -> None:
    """缺省回调是具名函数（不是每次现造的 lambda）：`fetch_guarded` 里用它兜 `log=None`。"""
    sink = ts._discard_progress
    assert callable(sink)
    assert sink.__name__ == "_discard_progress"
    assert sink("x") is None
