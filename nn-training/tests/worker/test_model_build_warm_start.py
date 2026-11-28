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


def test_intent_init_uses_module_entry(monkeypatch) -> None:
    """2026-11-28 回归：flat 脚本并包后，init 子进程必须走 `-m worker.ppo.intent`。

    旧实现拼 `nn-training/ppo_intent.py`（cwd=nn-training）⇒ 路径不存在，
    mode=intent/goal 首启直接 CalledProcessError（P′-4a 课程无法启动）。
    """
    import worker.model_build as mb
    import worker.ppo.intent as ppo_intent

    captured: dict = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw

    class _FakeModel:
        def parameters(self):
            return []

    monkeypatch.setattr(mb.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(mb.subprocess, "run", _fake_run)
    monkeypatch.setattr(ppo_intent, "build_rl_net", lambda _p: _FakeModel())
    monkeypatch.setattr(ppo_intent, "load_intent_weights", lambda _m, _p: None)

    mb._build_intent_or_goal("bc.json", "rl.json", "intent", 4)

    cmd = captured["cmd"]
    assert cmd[1:3] == ["-m", "worker.ppo.intent"]
    assert not any(str(part).endswith("ppo_intent.py") for part in cmd)
    assert captured["kw"]["cwd"] == str(mb.REPO_ROOT)
    assert captured["kw"]["check"] is True
