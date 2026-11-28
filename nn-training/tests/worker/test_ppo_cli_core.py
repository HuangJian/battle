"""B8 第十四刀（`docs/nn/engineering.md` §223）：goal / intent 的 CLI 只剩一份实现。

`-m worker.ppo.goal --help` / `-m worker.ppo.intent --help` 的输出是对外契约（日志与
`--help` 都进过 runbook），所以并成 `worker/ppo/cli.py` 之后由本文件钉两面：

1. **文案 / 顺序 / 默认值逐字不变** —— 直接读 `cli._parser(spec)` 的 Action 表；
2. **`main()` 只是壳** —— 单句 `run_cli(_spec())`，且两个模块里不再有 `add_argument`
   （否则同一处修正又得改两遍，正是这一刀要消灭的 bug 面）。
"""

import ast
from pathlib import Path

import pytest

from worker.ppo import cli as ppo_cli
from worker.ppo import goal as ppo_goal
from worker.ppo import intent as ppo_intent

ROOT = Path(__file__).resolve().parents[2]

#: (dest, help) 逐字；`None` = 该参数本来就没有 help。
_ARGS = {
    "goal": [
        ("init_from", "goal-BC weights (init mode)"),
        ("resume", "RL goal weights (update mode)"),
        ("data", "goal trajectory shard root (update)"),
        ("out", None),
        ("epochs", None),
        ("mb", None),
        ("lr", None),
        ("value_warmup_epochs", None),
        ("kl_coef", None),
        (
            "ref_weights",
            "kickstarting 参考策略权重（goal-BC 冻结快照；缺省 = 当前权重）",
        ),
        ("device", None),
        ("seed", None),
        ("threads", None),
    ],
    "intent": [
        ("init_from", "B′/BC intent weights (init mode)"),
        ("resume", "RL intent weights (update mode)"),
        ("data", "intent trajectory shard root (update)"),
        ("out", None),
        ("epochs", None),
        ("mb", None),
        ("lr", None),
        ("value_warmup_epochs", "前 N 个 epoch 只训 value 头（策略冻结；B′ 冷启动 value 随机）"),
        ("kl_coef", "kickstarting KL 惩罚系数（plan #5 衰减 0.5/iter；0=关闭）"),
        ("ref_weights", "kickstarting 参考策略权重（B′ 冻结快照；缺省 = 当前权重）"),
        ("device", None),
        ("seed", None),
        ("threads", None),
    ],
}

_DEFAULTS = {
    "init_from": None,
    "resume": None,
    "data": None,
    "epochs": 4,
    "mb": 512,
    "lr": 1e-4,
    "value_warmup_epochs": 0,
    "kl_coef": 0.0,
    "ref_weights": None,
    "device": "cpu",
    "seed": 7,
    "threads": 8,
}


def _spec(head: str) -> ppo_cli.CliSpec:
    return ppo_goal._spec() if head == "goal" else ppo_intent._spec()


@pytest.mark.parametrize("head", ["goal", "intent"])
def test_option_order_and_help_text_are_frozen(head):
    parser = ppo_cli._parser(_spec(head))
    acts = [a for a in parser._actions if a.option_strings and a.dest != "help"]
    assert [(a.dest, a.help) for a in acts] == _ARGS[head]
    assert acts[3].option_strings == ["--out"]


@pytest.mark.parametrize("head", ["goal", "intent"])
def test_defaults_are_frozen(head):
    ns = ppo_cli._parser(_spec(head)).parse_args(["--out", "o.json"])
    assert vars(ns) == {**_DEFAULTS, "out": "o.json"}


def test_intent_cli_update_binds_port_shape():
    """2026-11-28 回归：CLI update 模式必须能接住端口调用形状。

    `cli.run` 调 `spec.update(model, opt, chunks, epochs, device, seed=..., ...)`；
    曾绑 `ppo_update_intent`（只收 HeadUpdateSpec）⇒ update 模式 TypeError。
    """
    import inspect

    params = inspect.signature(ppo_intent._spec().update).parameters
    assert {"model", "opt", "chunks", "epochs", "device"} <= set(params)
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


@pytest.mark.parametrize("head", ["goal", "intent"])
def test_main_is_a_thin_shell(head):
    """`main()` 只有一句 `run_cli(_spec())`；参数定义只住 `cli._parser`。"""
    path = ROOT / "worker" / "ppo" / f"{head}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _shell_violations(tree) == []


def _shell_violations(tree: ast.Module) -> list[str]:
    """`main` 的形状 + 模块里有没有 argparse 定义（为空 = 壳还是壳）。"""
    bad: list[str] = []
    inlined = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
    ]
    if inlined:
        bad.append(f"{len(inlined)} 处 add_argument（参数定义只该住 worker/ppo/cli.py）")

    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if len(mains) != 1:
        return [*bad, f"main 定义数 = {len(mains)}"]
    body = [n for n in mains[0].body if not _is_docstring(n)]
    if len(body) != 1:
        return [*bad, f"main() 的语句数 = {len(body)}（应只有一句 run_cli(_spec())）"]
    call = body[0].value if isinstance(body[0], ast.Expr) else None
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
        return [*bad, "main() 的唯一语句不是一次调用"]
    if call.func.id != "run_cli" or call.keywords:
        return [*bad, "main() 调的不是 run_cli(_spec())"]
    if len(call.args) != 1:
        return [*bad, f"run_cli 的实参数 = {len(call.args)}"]
    arg = call.args[0]
    if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "_spec"):
        bad.append("run_cli 的实参不是 _spec()")
    elif arg.args or arg.keywords:
        bad.append("_spec() 不该收实参（配置住 `_spec` 里）")
    return bad


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
