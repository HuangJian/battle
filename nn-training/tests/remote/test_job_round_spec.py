"""tests/remote/test_round_spec.py —— `JobRoundSpec` 的形状守卫（§P0-3，2026-11-22）。

`run_one_round` 原先 21 个参数：作业身份（`job` / `part_dir` / `pf_store` / …）、传输身份、
训练旋钮、上报与生命周期、注入点 `run_job_fn`。旧表里只有前三个是位置位，其余 18 个靠
关键字逐名对齐 —— 漏一个只在走到那条分支才 `NameError`，名字写错则静默落默认值（`role`
错成 online、`prefetch_unpack` 错成缺省档）。所以判据是：

  · 值对象 frozen + kw_only；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

调用点的逐名一致由 `test_job_round_split::test_the_call_site_passes_exactly_the_round_interface`
用 AST 双向钉住。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, cast

import pytest

from remote.job_round import JobRoundSpec
from tests.helpers import run_setup_probe as probe

#: `JobRoundSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "base_url",
        "token",
        "job",
        "part_dir",
        "code_cache_dir",
        "pf_store",
        "polls_since_accept",
        "worker_id",
        "device",
        "torch_threads",
        "echo",
        "restart_argv",
        "prefetch_depth",
        "uploader",
        "run_job_fn",
        "log",
        "cache_root",
        "epoch_mult",
        "backend",
        "role",
        "prefetch_unpack",
    }
)


def _spec(tmp_path: Path) -> JobRoundSpec:
    return JobRoundSpec(
        base_url="http://hub.invalid",
        token="tok",
        job={"job_id": "j"},
        part_dir=tmp_path,
        code_cache_dir=None,
        pf_store=None,
        polls_since_accept=0,
        worker_id="w",
        device="cpu",
        torch_threads=0,
        echo=False,
        restart_argv=None,
        prefetch_depth=1,
        uploader=cast(Any, object()),
        run_job_fn=lambda *_a, **_k: {},
        log=lambda _m: None,
    )


def test_round_spec_is_frozen_and_kw_only(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(JobRoundSpec)), (
        "`JobRoundSpec` 不是 kw_only ⇒ 21 个参数只是换了个地方写"
    )


def test_round_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(JobRoundSpec)}
    assert got == FIELDS, (
        f"`JobRoundSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_run_one_round_takes_only_the_spec() -> None:
    got = probe.param_names("remote.job_round", "run_one_round")
    assert got == ["spec"], f"`run_one_round` 的形参是 {got}，应只有 `spec`"
