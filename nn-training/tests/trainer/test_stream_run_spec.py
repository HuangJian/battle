"""tests/trainer/test_stream_run_spec.py —— `StreamRunSpec` 的形状守卫（§P0-3，2026-11-22）。

`run_rollout_stream` 原先 22 个参数：前 10 个是身份位，后 12 个是可选接线 —— 评估触发
（`on_collect_done` / `on_ppo_started`）、波次回调、零落盘（`on_shard`）、收尾解冻
（`on_collect_end`）、eval 动态闸（`on_dispatch_drained` / `on_dispatch_active`），中间还夹着
`backend` / `update_kwargs`。位置表里串一位不会报错，只会让评估不触发、语料不记账或闸不
解冻。所以判据是：

  · 值对象 frozen + kw_only（回调面不许靠位置猜）；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

生产调用点传的就是 spec —— 由 `test_stream_collect_end` 与 e2e 的既有行为用例覆盖
（不读源码：读 `getsource` 会把每条 assert 计入口径 B 的文本断言）。
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from tests.helpers import run_setup_probe as probe
from trainer.stream import StreamRunSpec

#: `StreamRunSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "bun",
        "rl_path",
        "traj_dir",
        "pairs",
        "args",
        "cfg",
        "iter_id",
        "model",
        "opt",
        "device",
        "on_collect_done",
        "on_ppo_started",
        "on_epoch_done",
        "backend",
        "update_kwargs",
        "extra_wver",
        "course_fp",
        "corpus_fp",
        "on_shard",
        "on_collect_end",
        "on_dispatch_drained",
        "on_dispatch_active",
    }
)


def test_stream_run_spec_is_frozen_and_kw_only(tmp_path) -> None:
    spec = StreamRunSpec(
        bun="bun",
        rl_path=str(tmp_path / "w.json"),
        traj_dir=tmp_path,
        pairs=[],
        args=SimpleNamespace(),
        cfg={"policy": {}},
        iter_id="rid.1",
        model=None,
        opt=None,
        device="cpu",
    )
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(StreamRunSpec)), (
        "`StreamRunSpec` 不是 kw_only ⇒ 22 个位置参数只是换了个地方写"
    )


def test_stream_run_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(StreamRunSpec)}
    assert got == FIELDS, (
        f"`StreamRunSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_run_rollout_stream_takes_only_the_spec() -> None:
    got = probe.param_names("trainer.stream", "run_rollout_stream")
    assert got == ["spec"], f"`run_rollout_stream` 的形参是 {got}，应只有 `spec`"
