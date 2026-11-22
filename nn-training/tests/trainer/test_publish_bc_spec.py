"""`PublishBcSpec` 的形状守卫（`python.refactor.md` §P0-3，批 C，2026-11-22）。

`trainer.bc_loop.publish_bc_job` 旧签名 12 个关键字，其中真正危险的是**三对外观相近**的：

  · `course_fp` / `corpus_fp` —— 互换 ⇒ payload 写的是对方的血缘指纹，云端 D14 逐 shard
    拒收，现场只看到「云机不要这份语料」（2026-09-20 事故的同一条面）；
  · `traj` / `job_root` —— 互换 ⇒ 往语料目录里写 job、往 job 目录里找 shard，
    `iter_bc_shard_dirs` 一个都扫不到 ⇒ `SystemExit`「无完整 BC shard」；
  · `round_name` / `data_round_dir` —— 同一个「语料在哪一轮」的两种说法（真轮缺省 /
    fixed 源），少传一个就是发布找不到 shard。

判据：**字段表**（12 个旧形参逐一对齐）· **冻结 + kw_only** · **入口只收一个对象**。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import trainer.bc_loop as BL
from trainer.bc_loop import PublishBcSpec
from worker.bc_config import BcCourseConfig

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


#: `publish_bc_job` 的 12 个形参（旧签名全是关键字，次序 = 语义分档）。
PUBLISH_BC_FIELDS: list[tuple[str, object]] = [
    ("course", MISSING),
    ("course_fp", MISSING),
    ("corpus_fp", MISSING),
    ("course_text", MISSING),
    ("it", MISSING),
    ("traj", MISSING),
    ("job_root", MISSING),
    ("jsonl_path", MISSING),
    ("course_key", ""),
    # 语料轮目录名（真轮缺省 `it{it}`；冒烟轮 `"smoke"`）。
    ("round_name", ""),
    # fixed 源的实际语料根；None = 历史 `<traj>/bc-data/<round>`。
    ("data_round_dir", None),
    ("log", BL.log),
]


def test_field_table_matches_the_old_signature() -> None:
    assert _table(PublishBcSpec) == PUBLISH_BC_FIELDS


def test_publish_bc_job_takes_exactly_one_spec() -> None:
    params = list(inspect.signature(BL.publish_bc_job).parameters)
    assert params == ["spec"]


def test_spec_is_frozen_and_keyword_only() -> None:
    rows = dataclasses.fields(PublishBcSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    spec = _minimal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, rows[0].name, None)


def _minimal() -> PublishBcSpec:
    return PublishBcSpec(
        course=cast(BcCourseConfig, None),
        course_fp="f" * 64,
        corpus_fp="c" * 64,
        course_text="{}",
        it=1,
        traj=Path("."),
        job_root=Path("jobs"),
        jsonl_path=Path("log.jsonl"),
    )


def test_old_keyword_form_is_rejected() -> None:
    """旧调用面（`publish_bc_job(course=…, course_fp=…, …)`）必须响亮地拒。

    否则 `course_fp` / `corpus_fp` 换位这类错误会一路静默到云端 D14 才炸。
    """
    call = cast(Callable[..., object], BL.publish_bc_job)
    with pytest.raises(TypeError):
        call(course=object(), course_fp="f" * 64, corpus_fp="c" * 64, it=1)


def test_unknown_field_is_a_type_error() -> None:
    make = cast(Callable[..., object], PublishBcSpec)
    with pytest.raises(TypeError):
        make(course=None, course_fingerprint="f" * 64)
