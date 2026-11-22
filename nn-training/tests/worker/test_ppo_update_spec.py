"""`worker.ppo.update_spec` 的形状护栏（`python.refactor.md` §P0-3）。

`ppo_update` 原来是 torch 20 / Paddle 21 个参数（5 位置 + 15/16 关键字），关键字拼错
Python 不报错，只是**静默落回缺省**（`kl_coef` / `target_kl` / `steps_out` / `lazy` 各有
各的失效方式）。这里钉住三件事：

* 两个引擎的入口只收一份 spec（形状回退立刻红）；
* spec 是 frozen + kw_only，字段表 = 收参数时的逐条搬运（少一个旋钮就没人能再传它）；
* 端口适配器 `update(model, opt, chunks, epochs, device, **kw)` 仍在，且
  `REQUIRED_UPDATE_KWARGS`（stream.py 无条件注入的那三个）全在 spec 字段里 ——
  否则 `**kw` 会在构造 spec 时 TypeError，流式训练整条腿断掉。
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable

import pytest

from tests.helpers.run_setup_probe import param_names
from worker.backend import REQUIRED_UPDATE_KWARGS
from worker.ppo.engine import ppo_update as ppo_update_torch
from worker.ppo.engine import update as port_update_torch
from worker.ppo.engine_paddle import ppo_update as ppo_update_paddle
from worker.ppo.engine_paddle import update as port_update_paddle
from worker.ppo.update_spec import BaseUpdateSpec, PaddleUpdateSpec, TorchUpdateSpec

#: torch / Paddle **同名同义**的 19 个旋钮（按声明顺序）。
BASE_FIELDS = [
    "model",
    "opt",
    "chunks",
    "epochs",
    "device",
    "ckpt_path",
    "on_epoch_done",
    "kl_coef",
    "ref_model",
    "kickstart_kl",
    "ent_coef",
    "demo_bank",
    "demo_bc_coef",
    "demo_per_mb",
    "progress",
    "progress_head",
    "value_anchor",
    "rng",
    "lazy",
]

#: 两侧**各自独有**的字段：torch 没有 `steps_out`/`log`，Paddle 没有 `target_kl`。
SIDE_FIELDS = {"target_kl", "steps_out", "log"}

#: 三个 spec 类（基类 + 两侧）：frozen / kw_only / 字段表都要逐个成立。
SPEC_CLASSES: list[type[BaseUpdateSpec]] = [
    BaseUpdateSpec,
    TorchUpdateSpec,
    PaddleUpdateSpec,
]


def _names(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def test_spec_is_frozen_and_keyword_only() -> None:
    for cls in SPEC_CLASSES:
        assert dataclasses.is_dataclass(cls)
        spec = cls(model=None, opt=None, chunks=None, epochs=1)
        field = sorted(f.name for f in dataclasses.fields(cls))[0]  # 变量而非字面量
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(spec, field, None)
        assert all(f.kw_only for f in dataclasses.fields(cls)), (
            f"`{cls.__name__}` 不是 kw_only ⇒ 20 个参数只是换了个地方写"
        )


def test_side_specs_only_add_their_own_knobs() -> None:
    assert _names(BaseUpdateSpec) == BASE_FIELDS
    assert _names(TorchUpdateSpec) == [*BASE_FIELDS, "target_kl"]
    assert _names(PaddleUpdateSpec) == [*BASE_FIELDS, "steps_out", "log"]
    # 超集 spec 是明确否决的做法：两侧独有的字段不得互相渗透。
    assert SIDE_FIELDS.isdisjoint(set(BASE_FIELDS))


def test_unknown_knob_is_a_type_error() -> None:
    """旧写法的静默缺省 ⇒ 现在构造 spec 当场炸（拼错名字不再训一轮错的）。"""
    builder: Callable[..., BaseUpdateSpec] = TorchUpdateSpec
    with pytest.raises(TypeError, match="lazzy"):
        builder(model=None, opt=None, chunks=None, epochs=1, lazzy=True)


def test_engines_take_only_the_spec() -> None:
    for module in ("worker.ppo.engine", "worker.ppo.engine_paddle"):
        assert param_names(module, "ppo_update") == ["spec"], module
    assert list(inspect.signature(ppo_update_torch).parameters) == ["spec"]
    assert list(inspect.signature(ppo_update_paddle).parameters) == ["spec"]


@pytest.mark.parametrize("port", [port_update_torch, port_update_paddle])
def test_port_adapter_keeps_the_five_positional_semantics(
    port: Callable[..., object],
) -> None:
    """端口 `update(...)` 是**同一个调用面**驱动四个后端 ⇒ 5 位置 + `**kwargs` 不动。"""
    params = list(inspect.signature(port).parameters.values())
    assert [p.name for p in params] == [
        "model",
        "opt",
        "chunks",
        "epochs",
        "device",
        "update_kwargs",
    ]
    assert params[-1].kind is inspect.Parameter.VAR_KEYWORD
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params[:-1])


def test_every_required_port_kwarg_is_a_spec_field() -> None:
    """stream.py 无条件注入的键必须在 spec 里，否则构造 spec 时 TypeError。"""
    assert set(_names(BaseUpdateSpec)) >= REQUIRED_UPDATE_KWARGS
