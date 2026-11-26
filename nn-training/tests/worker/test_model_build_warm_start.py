"""warm-start 缩放的假 tensor 差分（2026-11-26，model_build 拆分）。

判据不是「源码长什么样」，而是**同一组假参数经过新 helper 与独立重实现的原始循环后，
缩放/清零结果逐项相同**。真实 torch 只在训练机上有，这里用最小假对象把两条路径对账。
"""

from __future__ import annotations

from contextlib import nullcontext

from worker.model_build import _scale_policy_and_value, _scale_trunk


class _Param:
    def __init__(self) -> None:
        self.scale = 1.0
        self.zeroed = False

    def mul_(self, x: float) -> None:
        self.scale *= float(x)

    def zero_(self) -> None:
        self.zeroed = True


class _Model:
    def __init__(self, names: list[str]) -> None:
        self.params = {n: _Param() for n in names}

    def named_parameters(self):
        return self.params.items()


_NAMES = [
    "stem.conv.weight",
    "blocks.0.conv.weight",
    "fc.weight",
    "move_head.weight",
    "fire_head.weight",
    "hold_head.weight",
    "value_head.weight",
]


def _snapshot(model: _Model) -> dict[str, tuple[float, bool]]:
    return {n: (p.scale, p.zeroed) for n, p in model.params.items()}


def test_trunk_scale_matches_the_reference_loop() -> None:
    got, want = _Model(_NAMES), _Model(_NAMES)
    _scale_trunk(got, 2.5, nullcontext)
    for name, p in want.named_parameters():
        if name.startswith(("stem.", "blocks.", "fc.")):
            p.mul_(2.5)
    assert _snapshot(got) == _snapshot(want)


def test_policy_scale_and_value_zero_match_the_reference_loop() -> None:
    got, want = _Model(_NAMES), _Model(_NAMES)
    _scale_policy_and_value(got, 3.0, False, nullcontext)
    for name, p in want.named_parameters():
        if name.startswith(("move_head.", "fire_head.", "hold_head.")):
            p.mul_(3.0)
        elif name.startswith("value_head."):
            p.zero_()
    assert _snapshot(got) == _snapshot(want)


def test_pretrained_value_head_is_not_zeroed() -> None:
    got = _Model(_NAMES)
    _scale_policy_and_value(got, 3.0, True, nullcontext)
    assert got.params["value_head.weight"].zeroed is False
    assert got.params["value_head.weight"].scale == 1.0
    assert got.params["move_head.weight"].scale == 3.0
