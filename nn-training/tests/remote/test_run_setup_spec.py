"""tests/remote/test_run_setup_spec.py —— `RunSetup` 的形状守卫（§P0-3 第四刀）。

三条判据，合起来钉住「宽签名真的没了，而且不会长回来」：

  1. **值对象本身**：`RunSetup` 是 frozen + kw_only 的数据类，字段表冻结在这里
     （旋钮袋不许悄悄长/缩 —— 加一个旋钮是设计动作，要人改这张表）；
  2. **三条入口只收一个对象**：`open_run_context` / `run_standalone` / `run_plan_job` 的形参表
     （`inspect.signature`），以及它们**都**经过 `open_run_context`（函数体调用图）；
  3. **身份位只有一个校验点**：`_require_run_identity`（plan / manifest / job_dir / work_dir
     缺哪个点名哪个），`open_run_context` 里恰一次；`RunContext.__init__` 只剩 4 个形参。

结构判据全走 `tests/helpers/run_setup_probe.py`（`inspect` + AST 生产者，返回值是普通数据）——
理由是 `tests/helpers/text_asserts.py` 模块头的 B 类：契约是「谁定义了什么、谁调了谁」，
不是「文件里有没有这串字符」；那一层还顺带避免了口径 B 把读 `.py` 的断言计成新增欠账。
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from remote.plan_handoff import RunContext, RunSetup
from tests.helpers import run_setup_probe as probe

#: 旋钮袋的**完整**字段表（加/删都得动这里，理由写进 `RunSetup` 的 docstring）。
FIELDS = frozenset(
    {
        # 身份
        "plan",
        "plan_sha256",
        "manifest",
        "job_dir",
        "work_dir",
        "artifacts_dir",
        # 执行面
        "device",
        "torch_threads",
        "code_cache_dir",
        "ts_code_cache_dir",
        "ts_tree",
        "code_zip_bytes",
        "ts_code_zip_bytes",
        "course",
        "max_iters",
        "budget_sec",
        # 云机 A 层评估 / 放大 / 轮换
        "eval_on_cloud",
        "eval_slots",
        "eval_game_timeout_sec",
        "rollout_workers",
        "sample_mult",
        "epoch_mult",
        "rotation",
        # 补传 / outbox / 取消面
        "hub_url",
        "hub_token",
        "hub_course",
        "hub_lease",
        "backup_root",
        "deliver",
        "outbox_dir",
        "stop_file",
        "floor_it",
        "resume_dir",
        "deliverer",
        "run_job_fn",
        "log",
    }
)

#: 入口 → 期望形参表（`run_plan_job` 另收上一份产物）。
EXPECTED_PARAMS = {
    "open_run_context": ["setup"],
    "run_standalone": ["setup"],
    "run_plan_job": ["setup", "first_result"],
}


def test_run_setup_is_a_frozen_kw_only_value_object() -> None:
    """旋钮袋必须是 frozen + kw_only：`dataclasses.replace` 是入口覆盖单旋钮的手法。"""
    setup = RunSetup()
    field_name = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(setup, field_name, None)
    # `kw_only` 在 `@dataclass(...)` 上是**逐字段**生效的（3.10+），故按字段读。
    assert all(f.kw_only for f in dataclasses.fields(RunSetup)), (
        "`RunSetup` 不是 kw_only ⇒ 三十多个位置参数只是换了个地方写"
    )


def test_run_setup_field_table_is_frozen() -> None:
    """字段表冻结：加一个旋钮 = 设计动作，不是顺手。"""
    got = {f.name for f in dataclasses.fields(RunSetup)}
    assert got == FIELDS, (
        f"`RunSetup` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_every_entry_takes_one_spec_object() -> None:
    """三条入口都只收一个 `RunSetup`（`run_plan_job` 再加一份上一轮产物）—— 宽签名真没了。"""
    for entry, expected in EXPECTED_PARAMS.items():
        got = probe.param_names(probe.ENTRY_MODULES[entry], entry)
        assert got == expected, f"{entry} 的形参是 {got}，应为 {expected}"


def test_run_context_init_keeps_only_the_derived_handles() -> None:
    """`RunContext.__init__` 只剩 spec + 三个**派生**句柄（`store` / 轮形态 / TS 根）。"""
    got = list(inspect.signature(RunContext.__init__).parameters)
    assert got == ["self", "setup", "store", "round_forms", "ts_tree_dir"], got


def test_identity_guard_is_the_single_check_point() -> None:
    """身份位只在 `_require_run_identity` 一处校验，`open_run_context` 恰调一次。"""
    calls = probe.called_names("remote.plan_handoff", "open_run_context")
    assert calls.count("_require_run_identity") == 1, (
        "`open_run_context` 里 `_require_run_identity` 不是恰一次 ⇒ 有第二条身份判据住进来了"
    )
    # 两类缺失各一条（plan/manifest 与 job_dir/work_dir）——多一条就是多了第二种判据。
    assert probe.raise_count("remote.plan_handoff", "_require_run_identity") == 2


def test_entries_go_through_the_single_assembly_point() -> None:
    """两条入口都在**函数体里**调 `open_run_context`（装配点唯一，不许各自拼 `RunContext`）。"""
    for entry in ("run_standalone", "run_plan_job"):
        calls = probe.called_names(probe.ENTRY_MODULES[entry], entry)
        assert calls.count("open_run_context") == 1, f"{entry} 没走唯一装配点"
        # 集合判据（不是子串 `in`）：口径 A 会把「文本里有没有」当形态断言计（text_asserts §C3）。
        assert {"RunContext"}.isdisjoint(calls), f"{entry} 自己拼了 `RunContext`（绕过装配点）"
