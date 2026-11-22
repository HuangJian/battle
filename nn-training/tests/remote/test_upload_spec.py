"""`UploadSpec` 的形状守卫（`python.refactor.md` §P0-3，批 G，2026-11-22）。

`remote/upload_proc` 的两个入口**本来就收同一张参数表**（10 个旋钮逐字相同，只有 `mode`
的缺省语义不同），所以同刀共收**一份** `UploadSpec`：

| 函数 | 旧形参 | 现在 |
|---|---|---|
| `UploaderProcess.__init__` | 11（含 `self`；`mode="process"`） | `spec` |
| `make_uploader` | 10（`mode=""` = 由缺省档决定） | `spec` |

坏点是**两对相邻同类位**：`drain_timeout` / `enqueue_timeout`（收线预算 vs 受理预算——互换后
收线提前放弃、把在飞那份丢给降级同步补发，现场只看到「收工变慢」）；`python` /
`worker_module`（解释器 vs 模块名——互换后子进程起不来，**每一次**回传都降级）。

判据：**字段表**（两个入口共用、逐字对齐旧形参）· **冻结 + kw_only** · **两个入口都只收
一个对象** · **`mode` 的两种缺省语义不许被「统一」**（`""` 在进程入口 = `process`，
在工厂入口 = `RESULT_UPLOAD_MODE_DEFAULT`）。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import cast

import pytest

import remote.upload_proc as UP
from remote.result_upload import RESULT_UPLOAD_MODE_DEFAULT, RESULT_UPLOAD_MODES
from remote.upload_proc import UploaderProcess, UploadSpec

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


#: 旧 `UploaderProcess.__init__` 的 10 个关键字（次序 = 语义分档）。
UPLOAD_FIELDS: list[tuple[str, object]] = [
    ("upload", MISSING),
    # `""` = 由入口决定：进程入口认成 `process`，工厂认成 `RESULT_UPLOAD_MODE_DEFAULT`。
    ("mode", ""),
    ("depth", UP.RESULT_QUEUE_DEPTH_DEFAULT),
    ("drain_timeout", UP.RESULT_DRAIN_TIMEOUT_SEC),
    ("enqueue_timeout", UP.RESULT_ENQUEUE_TIMEOUT_SEC),
    ("log", None),
    ("python", None),
    ("worker_module", UP.UPLOAD_WORKER_MODULE),
    ("spawn", None),
    ("record_wire", None),
]


def test_field_table_matches_the_old_signature() -> None:
    assert _table(UploadSpec) == UPLOAD_FIELDS


def _spec() -> UploadSpec:
    return UploadSpec(upload=lambda *_a, **_k: 200)


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(UploadSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[0].name, None)


def test_both_entries_take_exactly_one_spec() -> None:
    init_params = list(inspect.signature(UploaderProcess.__init__).parameters)
    assert init_params == ["self", "spec"]
    maker_params = list(inspect.signature(UP.make_uploader).parameters)
    assert maker_params == ["spec"]


def test_mode_default_keeps_one_meaning_per_entry() -> None:
    """`mode=""` 的两种解释都还在：进程入口 = `process`，工厂入口 = 缺省档（async）。"""
    up = UploaderProcess(_spec())  # 不 start：只读 `.mode`
    assert up.mode == "process"
    made = UP.make_uploader(_spec())
    assert made.mode == RESULT_UPLOAD_MODE_DEFAULT == "async"
    assert made.mode in RESULT_UPLOAD_MODES


def test_explicit_mode_still_wins() -> None:
    up = UploaderProcess(UploadSpec(upload=lambda *_a, **_k: 200, mode="async"))
    assert up.mode == "async"
    made = UP.make_uploader(UploadSpec(upload=lambda *_a, **_k: 200, mode="sync"))
    assert made.mode == "sync"


def test_old_keyword_form_is_rejected() -> None:
    """旧调用面（`make_uploader(upload=…)` / `UploaderProcess(upload=…)`）必须响亮地拒。

    拒的理由不是形式：`python`/`worker_module` 互换在旧签名下只会表现为「每次回传都降级」。
    """
    maker = cast(Callable[..., object], UP.make_uploader)
    with pytest.raises(TypeError):
        maker(upload=lambda *_a, **_k: 200)
    init = cast(Callable[..., object], UploaderProcess)
    with pytest.raises(TypeError):
        init(upload=lambda *_a, **_k: 200)


def test_unknown_field_is_a_type_error() -> None:
    make = cast(Callable[..., object], UploadSpec)
    with pytest.raises(TypeError):
        make(upload=lambda *_a, **_k: 200, drain=1.0)
