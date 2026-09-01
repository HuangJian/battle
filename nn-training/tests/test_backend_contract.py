"""Rollout 后端契约回归（plan/python-refactor.md P0-1 / P1-2）。

**为什么需要这个文件**：三套 PPO 后端被 `rl/stream.py` 以 duck typing 复用，
长期没有任何类型约束。goal 后端的 `ppo_update_goal` 因此缺少 `on_epoch_done`
形参而无人察觉——`stream.py:123-124` 无条件注入它，缺陷直到训练中途第一个
wave 才以 TypeError 爆炸（默认配置下 `--mode goal` 100% 崩溃）。

契约本体在 `rl/backend.py`。本文件把两条约束变成可执行断言：

1. **结构契约**：后端具备 5 个必需成员（`isinstance` + `runtime_checkable` Protocol）。
2. **签名契约**：`update` 必须能绑定 `stream.py` 无条件注入的关键字参数
   （`ckpt_path` / `on_epoch_done`）—— 用 `inspect.Signature.bind()` 验证，
   不执行函数体，因此无需构造模型即可在毫秒级捕获 P0-1 这一整类缺陷。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.backend import REQUIRED_UPDATE_KWARGS, RolloutBackend
from rl.modes import _MODE_BACKEND_NAMES, _MODES, get_backend


def _backends() -> list[tuple[str, Any]]:
    return [(m, get_backend(m)) for m in _MODES]


def test_all_modes_have_a_registered_backend() -> None:
    """模式注册表与后端注册表必须逐项对应——新增模式忘记注册后端即失败。"""
    assert set(_MODES) == set(_MODE_BACKEND_NAMES), (
        f"--mode 取值 {sorted(_MODES)} 与后端注册表 {sorted(_MODE_BACKEND_NAMES)} 不一致"
    )


@pytest.mark.parametrize("mode,backend", _backends())
def test_backend_satisfies_protocol(mode: str, backend: Any) -> None:
    """结构契约：后端模块须具备流式迭代调用的 5 个成员。"""
    missing = [
        m
        for m in (
            "load_episode_from_shard",
            "chunk_episodes",
            "update",
            "load_episodes",
            "_ppo_load",
        )
        if not hasattr(backend, m)
    ]
    assert not missing, f"backend {mode!r} 缺少契约成员 {missing}"
    assert isinstance(backend, RolloutBackend), f"backend {mode!r} 不满足 RolloutBackend 契约"


@pytest.mark.parametrize("mode,backend", _backends())
def test_update_accepts_stream_injected_kwargs(mode: str, backend: Any) -> None:
    """签名契约（P0-1 捕获器）：update 必须接受 stream.py 无条件注入的所有关键字。

    `rl/stream.py:123-124` 在 `on_epoch_done` 非空时把它塞进 `update_kwargs`，
    `:286` 再 `backend.update(..., **update_kwargs)`。`run_rl.py` 恒传该回调
    （双缓冲提前预采的触发点），故不接受它的后端在流式模式下必然 TypeError。
    """
    sig = inspect.signature(backend.update)
    params = sig.parameters
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if not has_var_kw:
        missing = sorted(REQUIRED_UPDATE_KWARGS - set(params))
        assert not missing, (
            f"backend {mode!r} 的 update() 不接受 stream.py 注入的关键字 {missing}；"
            f"当前形参={list(params)}"
        )

    # 端到端可绑定性：不执行函数体，仅校验调用签名（毫秒级捕获该类缺陷）。
    bind_kwargs: dict[str, Any] = {k: None for k in REQUIRED_UPDATE_KWARGS}
    sig.bind(model=None, opt=None, chunks=[], epochs=1, device="cpu", **bind_kwargs)


@pytest.mark.parametrize("mode,backend", _backends())
def test_update_is_exposed(mode: str, backend: Any) -> None:
    """stream.py:286 调用的是 `update`，不是 `ppo_update*`——别名必须存在且可调用。"""
    fn = getattr(backend, "update", None)
    assert callable(fn), f"backend {mode!r} 的 update 不可调用"
    assert inspect.isfunction(fn) or inspect.ismethod(fn)
