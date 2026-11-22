"""`worker/ppo/np_core.LoadEpisodesSpec` 的形状护栏（`python.refactor.md` §P0-3 第四十九刀 A 批）。

`load_episodes_common` 的 13 个参数里，前 8 个是「哪条训练线」的方言（`label` /
`shard_kind` / `need_files` / `shard_loader` / `gae` / `gae_name` / `normalize_ret`），
后 5 个是随调用变的行为旋钮。旧签名把两腹排成一条关键字表，三份常量表
（`np_core.load_episodes` / `goal.load_episodes_goal` / `intent.load_episodes_intent`）
各手抄一遍：改名靠 grep，漏一个旋钮落回缺省也不报错。

这里钉住：spec 是 frozen + kw_only · 13 个字段/缺省与旧形参逐项对齐 · 入口只收一份 spec ·
三份生产常量表都**按名**交齐方言位（AST，不是文本）· 未知旋钮立刻 TypeError。

不 import `goal.py` / `intent.py`（它们顶层 `import torch`，无 torch 的镜像上会收集失败）；
两处生产表用 AST 看。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

from worker.ppo.np_core import LoadEpisodesSpec, load_episodes_common

PPO_DIR = Path(__file__).resolve().parents[2] / "worker" / "ppo"

#: `(字段名, 缺省标记)` —— 与收进 spec 之前的 13 个形参逐一对齐（前 8 个无缺省）。
MISSING = dataclasses.MISSING
EXPECTED_FIELDS = [
    ("data_root", MISSING),
    ("label", MISSING),
    ("shard_kind", MISSING),
    ("need_files", MISSING),
    ("shard_loader", MISSING),
    ("gae", MISSING),
    ("gae_name", MISSING),
    ("normalize_ret", MISSING),
    ("need_any_files", ()),
    ("load_log_every", 128),
    ("normalize_adv", True),
    ("per_stage_quota", 0),
    ("bundle", None),
]

#: 三份生产常量表必须交齐的方言位（`normalize_ret` 也在内：它是每条腿的决定，不是缺省）。
DIALECT_KEYS = {
    "label",
    "shard_kind",
    "need_files",
    "shard_loader",
    "gae",
    "gae_name",
    "normalize_ret",
}


def _fn(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def test_spec_is_frozen_and_fully_keyword_only() -> None:
    assert dataclasses.is_dataclass(LoadEpisodesSpec)
    rows = dataclasses.fields(LoadEpisodesSpec)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]
    first = rows[0].name  # 变量而非字面量（ruff B010）
    spec = _bare_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, first, "x")


def test_field_table_matches_the_old_signature() -> None:
    """字段名 / 顺序 / 缺省逐项等价，且没有多余字段。"""
    rows = []
    for f in dataclasses.fields(LoadEpisodesSpec):
        if f.default is not MISSING:
            rows.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            rows.append((f.name, f.default_factory()))
        else:
            rows.append((f.name, MISSING))
    assert rows == EXPECTED_FIELDS


def test_entry_takes_exactly_one_spec() -> None:
    params = inspect.signature(load_episodes_common).parameters
    assert list(params) == ["spec"], list(params)
    assert all(p.default is inspect.Parameter.empty for p in params.values())


def test_unknown_knob_fails_loudly() -> None:
    """旧写法拼错的旋钮静默落回缺省；现在构造 spec 就 TypeError。

    经 `Callable[..., …]` 变量调：这行**故意**多传一个关键字，静态检查器当场就会拦
    （否则这条用例根本跑不到运行期），而抑制注释会撞 `test_suppression_budget`。
    """
    ctor: Callable[..., LoadEpisodesSpec] = LoadEpisodesSpec
    with pytest.raises(TypeError):
        ctor(**_bare_kwargs(), no_such_knob=1)


@pytest.mark.parametrize(
    ("file_name", "table"),
    [
        ("np_core.py", "load_episodes"),
        ("goal.py", "load_episodes_goal"),
        ("intent.py", "load_episodes_intent"),
    ],
)
def test_production_tables_fill_the_dialect_by_name(file_name: str, table: str) -> None:
    """★ 本刀最可能的失误形态：三份常量表各手抄一遍关键字，漏一个不报错。

    AST 而不是文本：只看 `LoadEpisodesSpec(...)` 的关键字名集合与
    `load_episodes_common(...)` 的实参形状。
    """
    tree = ast.parse((PPO_DIR / file_name).read_text(encoding="utf-8"))
    fn = _fn(tree, table)
    specs = _calls(fn, "LoadEpisodesSpec")
    assert len(specs) == 1, f"{file_name}:{table} 里 `LoadEpisodesSpec(...)` 出现 {len(specs)} 次"
    assert not specs[0].args, f"{file_name}:{table} 仍用位置实参构造 spec（kw_only 就是为了不许这样）"
    got = {k.arg for k in specs[0].keywords if k.arg is not None}
    assert got >= DIALECT_KEYS, f"{file_name}:{table} 缺方言位 {sorted(DIALECT_KEYS - got)}"

    forwarded = _calls(fn, "load_episodes_common")
    assert len(forwarded) == 1, f"{file_name}:{table} 对 load_episodes_common 的调用数 {len(forwarded)}"
    assert len(forwarded[0].args) == 1, f"{file_name}:{table} 又按位置传了 {len(forwarded[0].args)} 个实参"
    call = forwarded[0].args[0]
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "LoadEpisodesSpec", (
        f"{file_name}:{table} 传给入口的不是 `LoadEpisodesSpec(...)`"
    )
    assert not forwarded[0].keywords, f"{file_name}:{table} 在入口上又挂了关键字"


def _bare_kwargs() -> dict:
    return {
        "data_root": "root",
        "label": "t",
        "shard_kind": "RL",
        "need_files": (),
        "shard_loader": lambda _p: {},
        "gae": lambda _d: ([], []),
        "gae_name": "GAE",
        "normalize_ret": False,
    }


def _bare_spec() -> LoadEpisodesSpec:
    return LoadEpisodesSpec(**_bare_kwargs())
