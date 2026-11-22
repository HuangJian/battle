"""tests/trainer/test_dispatch_spec.py —— `DispatchSpec` 的形状守卫（§P0-3，2026-11-22）。

`run_rollout_queue`（薄包装）与 `RolloutDispatcher.__init__`（OO 本体）旧版是**两张同样的
20 参表**，包装层把 19 个参数按**位置**原样转手（只有 `on_shard` 靠关键字）。传错一位不会
报错：`cfg` 进了 `args`、`on_result` 进了 `local_slots_max`，要跑到对应相位才炸。

现在两个入口共用一张 `DispatchSpec`。判据：

  · 值对象 frozen + kw_only；
  · 字段表冻结（加/删都得动这里）；
  · 两个入口都只收 `spec`。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from tests.helpers import run_setup_probe as probe
from trainer.dispatch import RolloutDispatcher
from trainer.dispatch_state import DispatchSpec

#: `DispatchSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "bun",
        "rl_path",
        "traj_dir",
        "pairs",
        "args",
        "cfg",
        "iter_id",
        "on_result",
        "local_slots_max",
        "tail_dispatch",
        "halt_event",
        "on_queue_drained",
        "on_queue_active",
        "local_suspend",
        "extra_wver",
        "course_fp",
        "corpus_fp",
        "pool_halt",
        "on_shard",
    }
)


def test_dispatch_spec_is_frozen_and_kw_only(tmp_path: Path) -> None:
    from types import SimpleNamespace

    spec = DispatchSpec(
        bun="bun",
        rl_path="w.json",
        traj_dir=tmp_path,
        pairs=[],
        args=SimpleNamespace(),
        cfg={},
        iter_id="rid.1",
    )
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(DispatchSpec)), (
        "`DispatchSpec` 不是 kw_only ⇒ 20 个位置参数只是换了个地方写"
    )


def test_dispatch_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(DispatchSpec)}
    assert got == FIELDS, (
        f"`DispatchSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_both_dispatch_entries_take_only_the_spec() -> None:
    names = probe.param_names("trainer.dispatch_queue", "run_rollout_queue")
    names += list(inspect.signature(RolloutDispatcher.__init__).parameters)
    assert names == ["spec", "self", "spec"], (
        f"派发入口的形参是 {names}，应只有 `run_rollout_queue(spec)` / "
        "`RolloutDispatcher(self, spec)`"
    )
