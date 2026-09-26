"""Rollout 后端契约回归（plan/python-refactor.md P0-1 / P1-2）—— **免 torch 的源码扫描层**。

**为什么需要这个文件**：三套 PPO 后端被 `rl/stream.py` 以 duck typing 复用，
长期没有任何类型约束。goal 后端的 `ppo_update_goal` 因此缺少 `on_epoch_done`
形参而无人察觉——`stream.py:123-124` 无条件注入它，缺陷直到训练中途第一个
wave 才以 TypeError 爆炸（默认配置下 `--mode goal` 100% 崩溃）。

契约本体在 `rl/backend.py`。本文件把两条约束变成可执行断言：

1. **结构契约**：后端具备 5 个必需成员。
2. **签名契约**：`update` 必须能绑定 `stream.py` 无条件注入的关键字参数
   （`ckpt_path` / `on_epoch_done`）——不执行函数体，因此无需构造模型即可在毫秒级
   捕获 P0-1 这一整类缺陷。

2026-09-26 改造：判据从**运行期 import** 换成**源码扫描**（`tests/helpers/backend_contract_scan`）。
三个后端模块顶层都 `import torch` ⇒ 原先整个文件在无 torch 的机器/镜像上**收集失败**，
P0-1 那类缺陷在那边一条都守不住。对照口径写在 `test_backend_contract_runtime.py`：

* 结构契约与 `isinstance(mod, RolloutBackend)` **同义**——`@runtime_checkable` 只查属性存在、
  不查签名（见 `rl/backend.py` 模块 doc）；静态层额外能看见名字的**来路**（`from X import y`
  会递归确认 y 在 X 里还在）。
* 签名契约由 AST 形参表翻成 `inspect.Signature` 后跑**同一段** `sig.bind(...)`；那半边文件
  在真 torch 下做**双向交叉校验**（静态结论 ⇔ 运行期结论），保证这一层不弱化。
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.backend import REQUIRED_UPDATE_KWARGS
from rl.modes import _MODE_BACKEND_NAMES, _MODES
from tests.helpers import backend_contract_scan as scan

#: 模式 → 静态契约结论（模块级：parametrize 在收集期就要值）。
_SCANS: dict[str, scan.ModuleScan] = {m: scan.scan_module(_MODE_BACKEND_NAMES[m]) for m in _MODES}


def test_the_scanner_actually_catches_the_p0_1_shape(tmp_path: Path) -> None:
    """自证：拿 P0-1 的原形（update 少了 `on_epoch_done`）砸这一层，必须砸得响。

    守则用例只断言「现在是好的」，本用例断言「坏的时候它会红」——否则整理完拆分后一个
    静默失效的扫描器看起来与好的扫描器一模一样。四条军规各有一块反例：
      ① 缺 `on_epoch_done` 且无 `**kwargs` ⇒ bind 出错（P0-1 原形）；
      ② 有 `**kwargs` ⇒ 绑得上（不假红）；
      ③ 成员定义在 `if` / `try` 体内 ⇒ `conditional` 非空；
      ④ `from X import y` 而 X 里已无 y ⇒ UNRESOLVED（运行期要等 import 才炸，这里当场红）。
    """
    root = tmp_path
    (root / "bknd.py").write_text(
        "from src_mod import chunk_episodes\n"
        "\n"
        "def load_episodes(data_root): ...\n"
        "\n"
        "def _ppo_load(ckpt_path, model, opt): ...\n"
        "\n"
        "def ppo_update(model, opt, chunks, epochs, device, ckpt_path=None): ...  # 缺 on_epoch_done\n"
        "\n"
        "update = ppo_update\n"
        "\n"
        "if True:\n"
        "    def load_episode_from_shard(dirpath): ...  # 条件定义（torch 缺席时可能不存在）\n",
        encoding="utf-8",
    )
    (root / "src_mod.py").write_text("def chunk_episodes(episodes, mb): ...\n", encoding="utf-8")
    # ④ 名字来路：`from src_mod import _ppo_load` 而 src_mod 里没有它 ⇒ UNRESOLVED，
    #    而不是「有个 import 语句就算存在」（运行期要等 import 才炸）。
    (root / "bknd_ghost.py").write_text("from src_mod import _ppo_load\n", encoding="utf-8")
    # ⑤ 边界（故意：盖不住的形状要响）：`update = lambda …` 静态解析不出签名 ⇒
    #    UNRESOLVED  + update_signature=None，而不是静默当作「有」。
    (root / "bknd_lambda.py").write_text("update = lambda *a: None\n", encoding="utf-8")

    s = scan.scan_module("bknd", root=root)
    assert s.present == {
        "load_episode_from_shard",
        "chunk_episodes",
        "update",
        "load_episodes",
        "_ppo_load",
    }, f"成员表解析错：{ {n: m.kind for n, m in s.members.items()} }"
    assert scan.scan_module("bknd_ghost", root=root).members["_ppo_load"].kind == scan.UNRESOLVED
    lam = scan.scan_module("bknd_lambda", root=root)
    assert lam.members["update"].kind == scan.UNRESOLVED, "lambda 赋值必须显式暴露为未解析"
    assert scan.update_signature(lam) is None
    # ③ 写在 `if True:` 里 ⇒ conditional（torch 缺席的环境里它可能根本不存在）。
    assert "load_episode_from_shard" in s.conditional

    # ① P0-1 原形：缺 on_epoch_done + 无 **kwargs ⇒ 绑不上（正是守则用例要抓的形状）。
    sig = scan.update_signature(s)
    assert sig is not None, "别名 update = ppo_update 应当解析得到签名"
    assert "on_epoch_done" not in sig.parameters
    assert scan.bind_report(sig, scan.stream_bind_kwargs(REQUIRED_UPDATE_KWARGS)), "缺关键字必须绑不上"

    # ② 不假红：同一个 update 加上 **kwargs 就能绑上。
    (root / "bknd_ok.py").write_text(
        "def ppo_update(model, opt, chunks, epochs, device, ckpt_path=None, **kwargs): ...\n"
        "update = ppo_update\n",
        encoding="utf-8",
    )
    ok_sig = scan.update_signature(scan.scan_module("bknd_ok", root=root))
    assert ok_sig is not None
    assert not scan.bind_report(ok_sig, scan.stream_bind_kwargs(REQUIRED_UPDATE_KWARGS))


def test_all_modes_have_a_registered_backend() -> None:
    """模式注册表与后端注册表必须逐项对应——新增模式忘记注册后端即失败。"""
    assert set(_MODES) == set(_MODE_BACKEND_NAMES), (
        f"--mode 取值 {sorted(_MODES)} 与后端注册表 {sorted(_MODE_BACKEND_NAMES)} 不一致"
    )


@pytest.mark.parametrize("mode", _MODES)
def test_backend_exposes_the_five_contract_members(mode: str) -> None:
    """结构契约：后端模块须具备流式迭代调用的 5 个成员（= `isinstance` 的静态同义）。"""
    s = _SCANS[mode]
    assert not s.missing, (
        f"backend {mode!r}（{s.path.name}）缺少契约成员 {s.missing}；"
        f"解析到的成员={ {n: m.kind for n, m in s.members.items()} }"
    )
    assert not s.conditional, (
        f"backend {mode!r} 的成员 {s.conditional} 定义在 `if` / `try` 体内——"
        f"带条件的定义在某些环境（如无 torch 的机器）可能根本不存在，"
        f"运行期 `isinstance` 会时真时假；请改成无条件定义"
    )


@pytest.mark.parametrize("mode", _MODES)
def test_update_accepts_stream_injected_kwargs(mode: str) -> None:
    """签名契约（P0-1 捕获器）：update 必须接受 stream.py 无条件注入的所有关键字。

    `rl/stream.py:123-124` 在 `on_epoch_done` 非空时把它塞进 `update_kwargs`，
    `:286` 再 `backend.update(..., **update_kwargs)`。`run_rl.py` 恒传该回调
    （双缓冲提前预采的触发点），故不接受它的后端在流式模式下必然 TypeError。
    """
    sig = scan.update_signature(_SCANS[mode])
    assert sig is not None, (
        f"backend {mode!r} 的 update 解析不出静态签名——静态层盖不住的形状"
        f"（动态赋值 / partial / 模块 __getattr__ 钩子？）；"
        f"要么写成普通 `def`（或 `update = ppo_update` 这种别名），要么显式决定怎么守"
    )
    params = sig.parameters
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if not has_var_kw:
        missing = sorted(REQUIRED_UPDATE_KWARGS - set(params))
        assert not missing, (
            f"backend {mode!r} 的 update() 不接受 stream.py 注入的关键字 {missing}；"
            f"当前形参={list(params)}"
        )

    # 端到端可绑定性：不执行函数体，仅校验调用签名（毫秒级捕获该类缺陷）。
    err = scan.bind_report(sig, scan.stream_bind_kwargs(REQUIRED_UPDATE_KWARGS))
    assert not err, f"backend {mode!r} 的 update 绑定不了 stream.py 的调用形状：{err}"


@pytest.mark.parametrize("mode", _MODES)
def test_update_is_exposed(mode: str) -> None:
    """stream.py:286 调用的是 `update`，不是 `ppo_update*`——别名必须存在且是普通函数。

    （运行期那半边同时断言 `inspect.isfunction/ismethod`；静态侧对等的形式是
    「解析到一份 `def`」——纯数据赋值 / `None` 占位会在这里红。）
    """
    m = _SCANS[mode].members["update"]
    assert m.kind != scan.UNRESOLVED, f"backend {mode!r} 解析不出 update 的来路"
    assert m.node is not None, (
        f"backend {mode!r} 的 update 来路是 {m.kind!r}，但没有解析到函数定义——"
        f"stream.py 会直接调用它，必须是真函数（别名到 `def` 也可以）"
    )
    assert isinstance(m.node, (ast.FunctionDef, ast.AsyncFunctionDef)), m.node
