"""后端契约的**运行期**半边：真 import 三个后端 + 与源码扫描层的等价交叉校验。

`tests/test_backend_contract.py` 是免 torch 的**源码扫描层**（无 torch 的机器/镜像上也能守
P0-1 那类契约缺陷）；本文件是它的 ground truth：

1. **原判据照旧**（§「运行期 ground truth」）——真 `importlib.import_module` 三个后端模块、
   真 `isinstance(mod, RolloutBackend)`、真 `inspect.signature(mod.update).bind(...)`。
   这一半**一条断言都没删**（2026-09-26 拆分时从 test_backend_contract.py 原样搬来）。
2. **等价交叉校验**（§「静态层不弱化」）——逐模式断言「静态扫描的结论 ⇔ 运行期的事实」，
   两个方向都比：静态说缺 ⇒ 运行期也缺；静态说 `update` 绑不上 ⇒ 运行期的签名也绑不上。
   若哪天扫描器漏了某种定义形状（动态赋值 / 模块 `__getattr__`……），静态侧会**假红**而不是
   静默放过，且这条交叉校验会当场指出分歧点。

⚠ 本文件按 DECISIONS §2026-09-26-goalnn-notorch-half 的「**不静默 skip**」口径刻意要求真
torch：无 torch 时它是**收集错误**（响亮的红），不是假绿。免 torch 覆盖面由上面那个文件承担。
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
from tests.helpers import backend_contract_scan as scan


def _backends() -> list[tuple[str, Any]]:
    return [(m, get_backend(m)) for m in _MODES]


def _bind_kwargs() -> dict[str, Any]:
    return scan.stream_bind_kwargs(REQUIRED_UPDATE_KWARGS)


# ============================================================ 运行期 ground truth


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
    """签名契约（P0-1 捕获器）：update 必须接受 stream.py 无条件注入的所有关键字。"""
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
    sig.bind(**scan.stream_bind_kwargs(REQUIRED_UPDATE_KWARGS))


@pytest.mark.parametrize("mode,backend", _backends())
def test_update_is_exposed(mode: str, backend: Any) -> None:
    """stream.py:286 调用的是 `update`，不是 `ppo_update*`——别名必须存在且可调用。"""
    fn = getattr(backend, "update", None)
    assert callable(fn), f"backend {mode!r} 的 update 不可调用"
    assert inspect.isfunction(fn) or inspect.ismethod(fn)


# ============================================================ 静态层不弱化（交叉校验）


@pytest.mark.parametrize("mode,backend", _backends())
def test_static_member_scan_matches_runtime(mode: str, backend: Any) -> None:
    """静态成员表 ⇔ 运行期属性表（含 `isinstance` 同义式），**两个方向**都比。

    这是「拆成源码扫描层**没有弱化**结构断言」的机械证明：只要 `isinstance(backend,
    RolloutBackend)`（= 5 个属性齐备）成立与否，静态扫描就必须给出同一答案。
    """
    s = scan.scan_module(_MODE_BACKEND_NAMES[mode])
    runtime_present = {m for m in scan.REQUIRED_MEMBERS if hasattr(backend, m)}

    assert s.present == runtime_present, (
        f"backend {mode!r}：静态扫描 {sorted(s.present)} vs 运行期 {sorted(runtime_present)} 不一致；"
        f"静态侧来路={ {n: m.kind for n, m in s.members.items()} }"
    )
    isinstance_ok = isinstance(backend, RolloutBackend)
    assert isinstance_ok == (not s.missing), (
        f"backend {mode!r}：isinstance(RolloutBackend)={isinstance_ok}，"
        f"但静态扫描 missing={s.missing} —— 结构判据的两层分歧"
    )


@pytest.mark.parametrize("mode,backend", _backends())
def test_static_update_signature_matches_runtime(mode: str, backend: Any) -> None:
    """静态 `update` 签名 ⇔ 运行期 `inspect.signature(update)`，对**同一份**调用形状双向比。

    两侧用同一个 `stream_bind_kwargs()`（`stream.py` 的调用形状）去 bind：一边可绑、
    另一边不可绑就是分歧——静态层据此才会在无 torch 的机器上给出与这里相同的结论。
    """
    static_sig = scan.update_signature(scan.scan_module(_MODE_BACKEND_NAMES[mode]))
    assert static_sig is not None, f"backend {mode!r}：静态 update 签名解析失败"

    kwargs = _bind_kwargs()
    static_err = scan.bind_report(static_sig, kwargs)
    runtime_err = scan.bind_report(inspect.signature(backend.update), kwargs)
    assert bool(static_err) == bool(runtime_err), (
        f"backend {mode!r}：静态签名与运行期签名的可绑定性不一致\n"
        f"  静态:   {static_sig}\n"
        f"  运行期: {inspect.signature(backend.update)}\n"
        f"  静态错误:   {static_err or '(可绑定)'}\n"
        f"  运行期错误: {runtime_err or '(可绑定)'}"
    )
