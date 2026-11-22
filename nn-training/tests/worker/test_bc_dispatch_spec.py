"""tests/worker/test_bc_dispatch_spec.py —— `BcDispatchSpec` 的形状守卫（§P0-3，2026-11-22）。

`dispatch_bc_corpus` 的 25 个参数里 18 个带缺省值，其中一半是同族的「配速旋钮」
（`node_fail_limit` / `busy_retry_limit` / `busy_backoff_sec` / `tripped_idle_polls`）。
位置传参时它们只差一位：测试想拧小让位窗口，抄错一格就悄悄换成了生产缺省，而
用例还会绿（它只是慢）。所以判据不是「行数变少」，而是：

  · 值对象 frozen + kw_only（没有任何位置歧义）；
  · 字段表冻结（加/删都得动这里，避免顺手塞一个「顺便也传进去」的旋钮）；
  · 入口只收一个 spec。
生产调用点传的是 spec 而不是关键字——由 `test_bc_corpus_ratio` 的替身（签名收 spec
并断言类型）行为覆盖；不从源码读，否则会多出口径 B 的文本断言。
"""

from __future__ import annotations

import dataclasses

import pytest

from tests.helpers import run_setup_probe as probe
from worker.bc_dispatch import BcDispatchSpec

#: `BcDispatchSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "tasks",
        "out_dir",
        "it",
        "corpus_fp",
        "course_fp",
        "difficulty",
        "max_ticks",
        "stage_json_for",
        "lives_override",
        "player_level",
        "wins_only",
        "near_miss_times",
        "cfg",
        "nodes",
        "max_slots",
        "task_timeout_sec",
        "iter_suffix",
        "action_v6",
        "label_policy_path",
        "label_mode",
        "node_fail_limit",
        "busy_retry_limit",
        "busy_backoff_sec",
        "tripped_idle_polls",
        "log",
    }
)


def _case_spec(tmp_path) -> BcDispatchSpec:
    return BcDispatchSpec(
        tasks=[(1, 2)],
        out_dir=tmp_path,
        it=1,
        corpus_fp="cfp",
        course_fp="xfp",
        difficulty="hard",
        max_ticks=100,
    )


def test_bc_dispatch_spec_is_frozen_and_kw_only(tmp_path) -> None:
    spec = _case_spec(tmp_path)
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(BcDispatchSpec)), (
        "`BcDispatchSpec` 不是 kw_only ⇒ 25 个位置参数只是换了个地方写"
    )


def test_bc_dispatch_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(BcDispatchSpec)}
    assert got == FIELDS, (
        f"`BcDispatchSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_dispatch_bc_corpus_takes_only_the_spec() -> None:
    got = probe.param_names("worker.bc_dispatch", "dispatch_bc_corpus")
    assert got == ["spec"], f"`dispatch_bc_corpus` 的形参是 {got}，应只有 `spec`"

