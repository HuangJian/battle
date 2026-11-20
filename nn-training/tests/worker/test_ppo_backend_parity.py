"""`worker/ppo/backend.py`（B8 第十三刀：torch/Paddle 共用训练步）的四条守卫。

背景：`engine.py`（torch，per-tick）与 `engine_paddle.py`（Paddle 双生）的训练步曾是
**同一顺序写两遍**（13 个 12 行克隆窗口全落在这一步里）。抽成「一份顺序 + 一张算子表」之后，
最容易发生的退化是三种，本文件把它们变成红：

  ① 共享核被某个框架污染（`import torch` / `import paddle` 进了 `backend.py`）——
     那样 Paddle-only 的解释器会在 import 期就炸，而两侧单测都不会要求它不炸；
  ② 两个引擎里又长出第二步数学（有人就近改一边）——判据是**函数内 12 行克隆窗口为 0**，
     与 `test_ppo_loop_core.py` 同一个检测器（`tests/helpers/clone_windows.py`）；
  ③ 算子表缺字段 / 某个钩子在某一侧填 `None` —— `Backend` 是 frozen dataclass（缺字段
     import 期就炸），这里再钉「每个字段都是可调用的」与「两个引擎都真走 `run_step`」。

数值对账不在这里：torch 侧的逐位 A/B 与 Paddle 侧的 `tools/paddle_parity.py`
（fixture / check / verify 三段）是证据来源，见 `docs/nn/engineering.md` §222。
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
import torch

import worker.ppo.backend as ppo_backend
import worker.ppo.engine as engine
import worker.ppo.engine_paddle as engine_paddle
from tests.helpers.clone_windows import function_windows

NN_DIR = Path(__file__).resolve().parents[2]
PPO_DIR = NN_DIR / "worker" / "ppo"


def test_backend_module_is_framework_free() -> None:
    """共享核不 import 任何后端：Paddle-only 解释器也要能 import 它。"""
    tree = ast.parse((PPO_DIR / "backend.py").read_text(encoding="utf-8"))
    backends = {"torch", "paddle"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] in backends]
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in backends
        ):
            offenders.append(node.module)
    assert not offenders, f"worker/ppo/backend.py 里出现了后端 import：{offenders}"


@pytest.mark.parametrize("builder", ["_torch_backend", "_paddle_backend"])
def test_every_hook_is_wired(builder: str) -> None:
    """两个引擎都必须把算子表的每个字段填成可调用对象（`None`/漏填 = 静默失效）。"""
    mod = engine if builder == "_torch_backend" else engine_paddle
    bk = (
        getattr(mod, builder)(torch.device("cpu"))
        if builder == "_torch_backend"
        else getattr(mod, builder)()
    )
    missing = [
        f.name
        for f in dataclasses.fields(ppo_backend.Backend)
        if not callable(getattr(bk, f.name))
    ]
    assert not missing, f"{builder} 漏了算子：{missing}"


@pytest.mark.parametrize("name", ["engine.py", "engine_paddle.py"])
def test_engine_calls_the_shared_step(name: str) -> None:
    """两侧都必须真的走 `run_step`（把数学抄回引擎里就没有第二份实现可对账了）。"""
    tree = ast.parse((PPO_DIR / name).read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "run_step" in called, f"{name} 没有调用 backend.run_step"


def test_no_clone_windows_between_the_two_engines() -> None:
    """torch ↔ Paddle 之间不许再出现函数内 12 行克隆窗口（数学只写一遍）。"""
    torch_w = function_windows(PPO_DIR / "engine.py")
    paddle_w = function_windows(PPO_DIR / "engine_paddle.py")
    shared = set(torch_w) & set(paddle_w)
    detail = [(torch_w[w][0], torch_w[w][1], paddle_w[w][0], paddle_w[w][1]) for w in shared]
    assert not detail, (
        "engine.py ↔ engine_paddle.py 又出现了函数内 12 行克隆窗口"
        f"（应抽进 worker/ppo/backend.py）：{detail[:5]}（共 {len(detail)} 个）"
    )
