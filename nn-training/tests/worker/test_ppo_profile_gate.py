"""`worker/ppo/engine.py::_profile_steps` —— `PPO_PROFILE` 开关的纯判定（2026-10-21）。

为什么单独立一条：PPO 步耗卡在 0.49s/step 时，`PPO_CUDA_DIAG` 只给 median/p95，分不清
「GPU 真在算」还是「主机在排队」；`PPO_PROFILE=1` 是那条分岔的读数口。判定写成纯函数
（不 import torch.profiler）⇒ 用例不需要 CUDA，也不会在跑门禁时真开 profiler。

三条纪律（与 `PPO_CUDA_DIAG` 同款）：默认关 · 非 CUDA 恒关（CPU/XLA 给假读数更糟）·
步数钳在 1..50（一个 job 96 步，抓多了开销自己就成了被测对象）。
"""

from __future__ import annotations

import pytest

import worker.ppo.engine as eng


def _env(monkeypatch: pytest.MonkeyPatch, **kw: str) -> None:
    monkeypatch.delenv("PPO_PROFILE", raising=False)
    monkeypatch.delenv("PPO_PROFILE_STEPS", raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_default_off_on_every_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    assert eng._profile_steps("cuda") == 0
    assert eng._profile_steps("cuda:0") == 0
    assert eng._profile_steps("cpu") == 0


def test_enabled_only_on_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU / XLA 上开也恒 0：CPU 没有 CUDA 活动列，XLA 的惰性图/profile 语义不同。"""
    _env(monkeypatch, PPO_PROFILE="1")
    assert eng._profile_steps("cpu") == 0
    assert eng._profile_steps("xla:0") == 0
    assert eng._profile_steps("cuda") == 10
    assert eng._profile_steps("cuda:1") == 10


def test_steps_env_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, PPO_PROFILE="1", PPO_PROFILE_STEPS="3")
    assert eng._profile_steps("cuda") == 3
    _env(monkeypatch, PPO_PROFILE="1", PPO_PROFILE_STEPS="0")
    assert eng._profile_steps("cuda") == 1
    _env(monkeypatch, PPO_PROFILE="1", PPO_PROFILE_STEPS="999")
    assert eng._profile_steps("cuda") == 50


def test_garbage_steps_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量手滑（`PPO_PROFILE_STEPS=abc`）不该让整单 PPO 炸 —— 回缺省 10。"""
    _env(monkeypatch, PPO_PROFILE="1", PPO_PROFILE_STEPS="abc")
    assert eng._profile_steps("cuda") == 10


def test_torch_device_object_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """调用点传的是 `torch.device` 而不是 str —— `str(device)` 判据要成立。"""
    import torch

    _env(monkeypatch, PPO_PROFILE="1")
    assert eng._profile_steps(torch.device("cpu")) == 0
