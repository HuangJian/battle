"""`worker/ppo/loop.py`（B8 ④ 的 PPOLoop 核心）与两个头适配面的四条守卫。

背景（`python.refactor.md` §0.4-11 / 附录 B 第 6 条）：`goal.py` 与 `intent.py` 的 update
曾是**同一顺序写了两遍**。抽成一份核心 + 两份配置（`HeadSpec` / `LossTerms`）之后，
最容易发生的退化有三种，本文件把它们变成红：

  ① 核心重新长出「头」的知识（`if tag == "ppo_goal"` 式分叉）—— 判据是**字符串字面量**里
     不许出现 `goal` / `intent`（模块 docstring 例外：它要讲清来龙去脉）；
  ② 两个入口重新长出循环体（有人图省事把 `permutation` / `clip_grad_norm_` 抄回来）——
     判据是源码面零这些调用，且都只 `return run_ppo(...)`；
  ③ 12 行归一克隆窗口重新出现在目标文件的函数里 —— 唯一允许的例外是 `main`（两个 CLI
     的参数表与 help 文案：并成一张配置表不会更短、只会更难读，见附录 B 第 6 条的 DoD）。

另加一条契约冻结：13 个训练输入的名字/缺省逐字不变 —— `trainer/stream.py` 与
`worker/backend.py::RolloutBackend` 仍按这些名字调 `update(...)`。P0-3 第四十九刀之后它们
不再摊在两个入口的签名上，而是 `HeadUpdateSpec` 的字段表（入口只收一个 `spec`）：
入口原来各自手抄一遍这 13 个名字，漏抄一个不报错。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import worker.ppo.goal as ppo_goal
import worker.ppo.intent as ppo_intent
from tests.helpers.clone_windows import function_windows
from worker.ppo.loop import HeadUpdateSpec

NN_DIR = Path(__file__).resolve().parents[2]
PPO_DIR = NN_DIR / "worker" / "ppo"

#: 一条训练腿的全部训练输入（`HeadUpdateSpec` 的字段表；名字敏感：stream.py 按这些名字传）。
EXPECTED_PARAMS = [
    "model",
    "opt",
    "chunks",
    "epochs",
    "device",
    "ckpt_path",
    "target_kl",
    "seed",
    "value_warmup_epochs",
    "ref_model",
    "kl_coef",
    "on_epoch_done",
    "rng",
]


def _docstring_rows(tree: ast.AST) -> set[int]:
    rows: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                first = body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    rows.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return rows


def test_core_learns_no_head_names() -> None:
    """核心只认 `HeadSpec` 的数据 —— 字符串字面量里不许出现两个头的名字。"""
    src = (PPO_DIR / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = _docstring_rows(tree)
    offenders = [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.lineno not in doc
        and ("goal" in n.value.lower() or "intent" in n.value.lower())
    ]
    assert not offenders, f"loop.py 的字符串字面量里出现了头名（应按 spec 传入）：{offenders}"


#: 循环体原语：只该在 `loop.py` 里出现（AST 判据，不挂措辞）。
LOOP_PRIMITIVES = frozenset({"permutation", "clip_grad_norm_", "sync_scalars", "approx_kl_est"})


def _callee_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            out.add(fn.attr)
        elif isinstance(fn, ast.Name):
            out.add(fn.id)
    return out


@pytest.mark.parametrize("name", ["goal.py", "intent.py"])
def test_entries_stay_thin(name: str) -> None:
    """两个入口只剩「给两份配置 + 转发」：循环体原语一个都不许留在它们身上。"""
    tree = ast.parse((PPO_DIR / name).read_text(encoding="utf-8"))
    called = _callee_names(tree)
    leaked = called & LOOP_PRIMITIVES
    assert not leaked, f"{name} 又抄回了循环体原语 {sorted(leaked)}"
    # 入口函数体只允许是 `return run_ppo(...)`（docstring 之外一条语句）。
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("ppo_update"):
            continue
        stmts = [
            s
            for s in fn.body
            if not (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)
            )
        ]
        assert len(stmts) == 1 and isinstance(stmts[0], ast.Return), (
            f"{fn.name} 不再是单条 `return run_ppo(...)`（语句数 {len(stmts)}）"
        )
        call = stmts[0].value
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == "run_ppo", f"{fn.name} 转发到了 {call.func.id}（应为 run_ppo）"


def test_no_clone_windows_outside_the_cli() -> None:
    """两个头之间不许再有 12 行克隆窗口；`main`（CLI 参数/文案）是有意保留的例外。"""
    goal = function_windows(PPO_DIR / "goal.py")
    intent = function_windows(PPO_DIR / "intent.py")
    shared = set(goal) & set(intent)
    leaking = [
        (goal[w][0], goal[w][1], intent[w][0], intent[w][1]) for w in shared if "main" not in goal[w]
    ]
    assert not leaking, (
        "goal.py ↔ intent.py 又出现了函数内 12 行克隆窗口（应抽进 loop.py）："
        f" {leaking[:5]}（共 {len(leaking)} 个，总窗口 {len(shared)}）"
    )


@pytest.mark.parametrize(
    ("mod", "fn_name"),
    [(ppo_goal, "ppo_update_goal"), (ppo_intent, "ppo_update_intent")],
)
def test_entry_signatures_are_frozen(mod, fn_name: str) -> None:
    """入口只收一份 spec；13 个输入名/缺省住在字段表里（见下面的字段表用例）。"""
    # eval_str：两个入口都是 `from __future__ import annotations` ⇒ 注解默认是字符串。
    params = inspect.signature(getattr(mod, fn_name), eval_str=True).parameters
    assert list(params) == ["spec"], f"{fn_name} 不再是「只收一份 spec」：{list(params)}"
    assert params["spec"].annotation is HeadUpdateSpec, f"{fn_name} 收的不是 HeadUpdateSpec"


def test_head_update_spec_fields_are_frozen() -> None:
    """13 个训练输入的字段表（顺序 + 缺省）—— stream / run_rl 按这些名字交付。"""
    fields = dataclasses.fields(HeadUpdateSpec)
    assert [f.name for f in fields] == EXPECTED_PARAMS, [f.name for f in fields]
    defaulted = {f.name: f.default for f in fields if f.default is not dataclasses.MISSING}
    assert list(defaulted) == EXPECTED_PARAMS[5:], (
        f"带缺省的应恰是第 6 项起的旋钮，实际 {list(defaulted)}"
    )


@pytest.mark.parametrize(
    ("mod", "fn_name"),
    [(ppo_goal, "ppo_update_goal"), (ppo_intent, "ppo_update_intent")],
)
def test_port_binds_stream_call_shape(mod, fn_name: str) -> None:
    """`update` 端口必须接住 stream 的 5 位置 + 任意旋钮关键字（拼错在 spec 构造时炸）。"""
    sig = inspect.signature(mod.update)
    assert list(sig.parameters)[:5] == EXPECTED_PARAMS[:5], list(sig.parameters)
    kinds = {p.kind for p in sig.parameters.values()}
    assert inspect.Parameter.VAR_KEYWORD in kinds, "update 丢了 **update_kwargs（stream/kwargs 注入面）"
    with pytest.raises(TypeError):
        mod.update(None, None, [], 1, "cpu", no_such_knob=1)
