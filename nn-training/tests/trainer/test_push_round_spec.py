"""`PushJobRoundSpec` 的形状守卫（`python.refactor.md` §P0-3，批 D，2026-11-22）。

`trainer/loop_transport._push_job_round` 旧签名 10 个形参，前七个是位置参数，里面排着
**两对同类相邻位**：

  · `manifest`（dict）与 `jid`（str）—— 互换则 `JobUpload` 把 job id 当 manifest 发出去，
    hub 的 400 在几分钟后的推送线程里才回来；
  · `payload_bytes` / `code_bytes` —— **两个 bytes 相邻**，只有对端解压时才看得见；
  · `args`（名字空间）与 `log`（函数）—— 交换是「对象 vs 可调用」，同样只有运行期才知道。

判据：**字段表**（10 个旧形参逐一对齐）· **冻结 + kw_only** · **入口只收一个对象** ·
旧的位置调用面当场 `TypeError`。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest

import trainer.loop_transport as LT
from trainer.loop_transport import PushJobRoundSpec

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


#: `_push_job_round` 的 10 个形参（旧签名：前 8 个位置 + 2 个缺省）。
PUSH_ROUND_FIELDS: list[tuple[str, object]] = [
    ("nodes", MISSING),
    ("manifest", MISSING),
    ("jid", MISSING),
    ("payload_bytes", MISSING),
    ("code_bytes", MISSING),
    ("args", MISSING),
    ("timeout_sec", MISSING),
    ("log", MISSING),
    ("blobs", None),
    ("ts_code_bytes", None),
]


def _spec() -> PushJobRoundSpec:
    return PushJobRoundSpec(
        nodes=[],
        manifest={},
        jid="j",
        payload_bytes=b"",
        code_bytes=b"",
        args=SimpleNamespace(smoke=False),
        timeout_sec=1.0,
        log=lambda _m: None,
    )


def test_field_table_matches_the_old_signature() -> None:
    assert _table(PushJobRoundSpec) == PUSH_ROUND_FIELDS


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(PushJobRoundSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[0].name, None)


def test_entry_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(LT._push_job_round).parameters)
    assert params == ["spec"]


def test_old_positional_form_is_rejected() -> None:
    """旧调用面（8 个位置）必须响亮地拒：`manifest` / `jid` 互换是这条链上最贵的一种静默错。"""
    call = cast(Callable[..., object], LT._push_job_round)
    with pytest.raises(TypeError):
        call([], {}, "j", b"p", b"c", SimpleNamespace(smoke=False), 1.0, lambda _m: None)


def test_unknown_field_is_a_type_error() -> None:
    make = cast(Callable[..., object], PushJobRoundSpec)
    with pytest.raises(TypeError):
        make(nodes=[], manifest={}, job_id="j", payload_bytes=b"", code_bytes=b"")
