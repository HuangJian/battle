"""tests/trainer/test_rollout_phase_spec.py —— `RolloutPhaseSpec` 的形状守卫（§P0-3，2026-11-22）。

`dispatch_rollout_phase` 原先 25 个参数：前 16 个是身份位，后 9 个是**可选接线**
（`on_shard` / `on_collect_end` / `on_dispatch_drained` / `on_dispatch_active` /
`halt_event` / `pool_halt` / …）。两个调用点各只传其中一簇，位置表里漏一个回调不会报错，
只会在某条腿上静默少做一件事（零落盘少记一段账、eval 闸没人解冻）。所以判据是：

  · 值对象 frozen + kw_only（回调面不许靠位置猜）；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

生产调用点传的就是 spec —— 由 `_rollout_phase` / 补波路径的既有行为用例覆盖
（不读源码：读 `getsource` 会把每条 assert 计入口径 B 的文本断言）。
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from tests.helpers import run_setup_probe as probe
from trainer.rollout_phase import RolloutPhaseSpec

#: `RolloutPhaseSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "args",
        "bun",
        "dist_cfg",
        "it",
        "traj_dir",
        "pairs",
        "jsonl_path",
        "model",
        "opt",
        "device",
        "ppo_backend",
        "update_kwargs",
        "start_it",
        "ref_model",
        "extra_wver",
        "eval_on_round",
        "course_fp",
        "corpus_fp",
        "halt_event",
        "on_result",
        "pool_halt",
        "on_shard",
        "on_collect_end",
        "on_dispatch_drained",
        "on_dispatch_active",
    }
)


def test_rollout_phase_spec_is_frozen_and_kw_only(tmp_path) -> None:
    spec = RolloutPhaseSpec(
        args=SimpleNamespace(),
        bun="bun",
        dist_cfg=None,
        it=1,
        traj_dir=tmp_path,
        pairs=[],
        jsonl_path=tmp_path / "x.jsonl",
        model=object(),
        opt=object(),
        device="cpu",
        ppo_backend=object(),
        update_kwargs=lambda *_a, **_k: {},
        start_it=0,
        ref_model=object(),
        extra_wver=None,
        eval_on_round=False,
    )
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(RolloutPhaseSpec)), (
        "`RolloutPhaseSpec` 不是 kw_only ⇒ 25 个位置参数只是换了个地方写"
    )


def test_rollout_phase_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(RolloutPhaseSpec)}
    assert got == FIELDS, (
        f"`RolloutPhaseSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_dispatch_rollout_phase_takes_only_the_spec() -> None:
    got = probe.param_names("trainer.rollout_phase", "dispatch_rollout_phase")
    assert got == ["spec"], f"`dispatch_rollout_phase` 的形参是 {got}，应只有 `spec`"
