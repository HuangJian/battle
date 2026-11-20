"""tests/trainer/test_rescan_spec.py —— `RescanSpec` 的形状守卫（§P0-3，2026-11-20）。

`rescan_nodes` 的 27 个参数收成一个 spec，**要守住的是那条事故的成因面**：这个函数曾是 19 个
位置参数逐位对应，第 19 个错位过一次（线程启动即抛 `missing 6 required positional arguments`
⇒ `rollout-rescan` 报废 ⇒ 运行中上线的节点永远不被发现）。所以三条判据：

  1. 值对象本身：frozen + kw_only，字段表冻结在下面（加字段 = 改这张表）；
  2. `rescan_nodes` 只收一个 spec（`inspect.signature`，形参表逐字比对）；
  3. 唯一调用点（`trainer/queue_worker` 的 rescan 线程）**用关键字**传 `spec` —— 位置传参
     正是原来那条事故的形态，多一个位置实参也不许悄悄长回来。

结构判据走 `tests/helpers/run_setup_probe.py`（`inspect` + AST 生产者，返回普通数据）——理由同
那个文件的模块头：`tests/helpers/text_asserts.py` 的 B 类要结构判据，而口径 B 会把读 `.py` 的
断言计成新增欠账。
"""

from __future__ import annotations

import dataclasses
import threading

import pytest

from tests.helpers import run_setup_probe as probe
from trainer.queue_local import RescanSpec

#: `RescanSpec` 的**完整**字段表（加/删都得动这里，理由写进类的 docstring）。
FIELDS = frozenset(
    {
        "cfg",
        "code_hash",
        "upgrade_branch",
        "dirty_files",
        "local_bun",
        "spawned_ids",
        "alive",
        "lock",
        "weights_bytes",
        "iter_id",
        "wver",
        "task_timeout",
        "status_timeout",
        "all_settled",
        "deadline",
        "rescan_sec",
        "worker",
        "extra_threads",
        "halt_event",
        "first_probe_sec",
        "wkind",
        "streaks",
        "soft_streaks",
        "fail_streak_max",
        "soft_streak_max",
        "rearm_cap",
        "rearm_warned",
    }
)


def _spec() -> RescanSpec:
    """最小的合法 spec（装配点那份 `queue_worker` 的字段取值形状）。"""
    return RescanSpec(
        cfg={},
        code_hash="",
        upgrade_branch="",
        dirty_files=[],
        local_bun="",
        spawned_ids=set(),
        alive=[],
        lock=threading.Lock(),
        weights_bytes=b"",
        iter_id="",
        wver="",
        task_timeout=1.0,
        status_timeout=1.0,
        all_settled=threading.Event(),
        deadline=0.0,
        rescan_sec=0.0,
        worker=lambda *_a, **_k: None,
        extra_threads=[],
    )


def test_rescan_spec_is_frozen_and_kw_only() -> None:
    """旋钮袋冻结：线程与主循环**共享**同一份 spec，谁都不许就地改它。"""
    spec = _spec()
    field_name = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field_name, None)
    assert all(f.kw_only for f in dataclasses.fields(RescanSpec)), (
        "`RescanSpec` 不是 kw_only ⇒ 27 个位置参数只是换了个地方写（事故原形）"
    )


def test_rescan_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(RescanSpec)}
    assert got == FIELDS, (
        f"`RescanSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_rescan_nodes_takes_only_the_spec() -> None:
    """`rescan_nodes` 只剩一个形参 —— 逐位对应那类错位不可能再「安静地」发生。"""
    got = probe.param_names("trainer.queue_local", "rescan_nodes")
    assert got == ["spec"], f"`rescan_nodes` 的形参是 {got}，应只有 `spec`"


def test_the_rescan_thread_passes_the_spec_by_keyword() -> None:
    """唯一调用点必须 `kwargs=dict(spec=…)`：位置传参 = 事故原形。"""
    keys = probe.thread_kwarg_keys("trainer.queue_worker", "rescan_nodes")
    assert keys == ["spec"], f"rescan 线程的实参形状是 {keys}，应为单个关键字 `spec`"
