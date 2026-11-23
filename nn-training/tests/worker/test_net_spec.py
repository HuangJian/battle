"""`NetSpec` 的形状护栏（`python.refactor.md` §P0-3 批 C2）。

`StudentNet` / `PPOStudent`（torch）与它们的 Paddle 双生原来是 9 个关键字参数：
`in_ch` / `board` / `scalar_dim`（三个形状）、`h` / `d` / `head_hidden`（三个宽度）、
`policy_extra` / `spatial_tower` / `action_v6`（三个相邻布尔档）。九位全带缺省 ⇒ 互换任何一对
都**不抛异常**，只是建出另一个架构的模型；而 `arch()` 会把那个错的档写回权重文件。

这里钉住四件事：

* spec 是 frozen + kw_only，字段表逐个成立（少一个旋钮就没人能再传它）；
* 两个框架的入口都只吃一份 spec（旧关键字面立刻 TypeError）；
* 双生同源：同一份 spec 在两个框架上建出的 `arch()` 与头宽逐字段相同；
* 三个布尔档各自真的换形（`action_v6` → hold 头、`policy_extra` → +`POLICY_EXTRA_DIM` 列、
  `spatial_tower` 需要 `policy_extra`），所以「互换静默」不再是可选项。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest

from common.schema import POLICY_EXTRA_DIM
from worker.models.arch_meta import (
    DEFAULT_D,
    DEFAULT_H,
    DEFAULT_HEAD_HIDDEN,
    NetSpec,
    build_ppo,
    read_arch_spec,
)
from worker.models.student import PPOStudent, StudentNet

#: 字段表（按声明顺序 = 旧形参顺序）。顺序本身就是兼容面：`arch()` 与权重文件一一对应。
FIELDS = [
    "in_ch",
    "board",
    "scalar_dim",
    "h",
    "d",
    "head_hidden",
    "policy_extra",
    "spatial_tower",
    "action_v6",
]


def test_field_table_is_frozen() -> None:
    assert [f.name for f in dataclasses.fields(NetSpec)] == FIELDS


def test_defaults_are_the_legacy_arch() -> None:
    """缺省档 = legacy 128（`h=64 / d=8 / head_hidden=128`，无 extra / 无塔 / 5-2 头）。"""
    spec = NetSpec()
    assert spec.h == DEFAULT_H and spec.d == DEFAULT_D
    assert spec.head_hidden == DEFAULT_HEAD_HIDDEN
    assert not spec.policy_extra and not spec.spatial_tower and not spec.action_v6


def test_spec_is_frozen_and_keyword_only() -> None:
    assert dataclasses.is_dataclass(NetSpec)
    assert all(f.kw_only for f in dataclasses.fields(NetSpec)), (
        "`NetSpec` 不是 kw_only ⇒ 9 个参数只是换了个地方按位置写"
    )
    spec = NetSpec()
    field = FIELDS[3]  # 变量而非字面量：`setattr` 才能表达「运行时不许改」
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, 8)
    with pytest.raises(TypeError):
        NetSpec(*[8])  # 位置位不再存在


def test_entries_take_one_spec_only() -> None:
    """两个入口都只吃一个 `NetSpec`：旧的关键字面不留半条。"""
    assert StudentNet(NetSpec(h=8, d=1)).h == 8
    assert PPOStudent(NetSpec(h=8, d=1)).h == 8
    ctors: list[Callable[..., object]] = [StudentNet, PPOStudent]
    for ctor in ctors:
        with pytest.raises(TypeError):
            ctor(h=8, d=1)
        with pytest.raises(TypeError):
            ctor()


def test_build_ppo_hands_the_spec_to_the_factory() -> None:
    """`build_ppo` 的契约是「把 spec 交给工厂」—— 缺省路径 = 全默认档。"""
    seen: list[NetSpec] = []

    def factory(spec: NetSpec) -> str:
        seen.append(spec)
        return "made"

    assert build_ppo(factory, None) == "made"
    assert seen == [NetSpec()]


def test_read_arch_spec_missing_file_is_all_defaults() -> None:
    assert read_arch_spec(None) == NetSpec()
    assert read_arch_spec("no/such/weights.json") == NetSpec()


def test_three_boolean_knobs_each_change_shape() -> None:
    """三个相邻布尔档各自真的换形 —— 互换不再只是「名字不同」。"""
    legacy = PPOStudent(NetSpec(h=8, d=1))
    leg_a = PPOStudent(NetSpec(h=8, d=1, policy_extra=True))
    leg_b = PPOStudent(NetSpec(h=8, d=1, policy_extra=True, spatial_tower=True))
    v6 = PPOStudent(NetSpec(h=8, d=1, action_v6=True))

    assert legacy.hold_head is None and v6.hold_head is not None
    assert leg_a.policy_input_dim == legacy.policy_input_dim + POLICY_EXTRA_DIM
    assert leg_b.policy_input_dim != leg_a.policy_input_dim
    assert leg_a.arch()["policyExtra"] is True
    assert leg_a.arch().get("spatialTower") is None
    assert leg_b.arch()["spatialTower"] is True
    with pytest.raises(ValueError):
        PPOStudent(NetSpec(h=8, d=1, spatial_tower=True))  # 塔的头输入含 extra


def test_torch_and_paddle_twins_share_one_spec() -> None:
    """同一份 spec → 两个框架的 `arch()` 与头宽逐字段相同（混跑能互相接手 ckpt 的前提）。"""
    paddle_student = pytest.importorskip("worker.models.student_paddle")
    for spec in (
        NetSpec(h=8, d=1),
        NetSpec(h=8, d=1, policy_extra=True),
        NetSpec(h=8, d=1, policy_extra=True, spatial_tower=True),
        NetSpec(h=8, d=1, action_v6=True),
    ):
        torch_m = PPOStudent(spec)
        paddle_m = paddle_student.PPOStudent(spec)
        assert torch_m.arch() == paddle_m.arch()
        assert torch_m.policy_input_dim == paddle_m.policy_input_dim
        assert (torch_m.hold_head is None) == (paddle_m.hold_head is None)
