"""tests/helpers/backend_contract_scan.py — 后端契约的**源码扫描**判据（免 torch 版）。

## 为什么

`rl/backend.py` 的 `RolloutBackend` 原本只在**运行期**被判（`importlib.import_module` 三个
后端 + `isinstance` + `inspect.signature`）。但三个后端模块（`ppo.engine` / `ppo.intent` /
`ppo.goal`）顶层都 `import torch` ⇒ **无 torch 的机器/镜像上整个文件收集失败**，P0-1 那一类
契约缺陷在那边一条都守不住。本模块把同一条契约换成**读源码**：

    「模块里能解析出这 5 个成员，且 `update` 的参数表能绑定 stream.py 无条件注入的关键字」

## 与运行期判据的等价性（**不能弱化** ⇒ 见 `tests/test_backend_contract_runtime.py` 交叉校验）

* **成员存在**：`@runtime_checkable` 的 `isinstance(mod, Protocol)` **只检查属性存在、不查签名**
  （`rl/backend.py` 模块 doc 已写明），故「静态成员表 ⊇ 5 个必需名」与 `isinstance` 同义。
  静态侧**多**一层运行期看不到的：**名字的来路**——`from X import y` 会递归确认 `y` 在 X 里
  仍可解析（被删掉的再导出在运行期要等 import 才炸，静态侧当场红）。
* **签名绑定**：静态侧把 AST 的 `def update(...)`（含 `update = ppo_update` 这类别名）翻成
  `inspect.Signature`，再跑**同一段** `sig.bind(...)`；与运行期
  `inspect.signature(backend.update)` 的绑定结果做**双向**交叉校验。

## 判据的边界（宁可红，也不静默放过）

* 名字解析只在**同模块内**起步，跨 `from X import y` 时递归到源模块确认（`_MAX_DEPTH`）。
* `if` / `try` 体内的定义标 `conditional=True`；调用方对必需成员**要求非 conditional**——
  带条件的定义在 torch 缺席时可能根本不存在，那正是本层要拦的形状。
* 解析不出来的成员（动态赋值、`functools.partial`、模块 `__getattr__` 钩子……）返回
  `UNRESOLVED` 而**不是**「视为通过」——静态层盖不住的形状必须显式暴露，由人决定怎么守。
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.helpers import source_scan

#: stream.py 复用后端时要求的 5 个成员（与 `rl/backend.py` 的 Protocol 一致；
#: 运行期交叉校验会把两边钉在一起）。
REQUIRED_MEMBERS: tuple[str, ...] = (
    "load_episode_from_shard",
    "chunk_episodes",
    "update",
    "load_episodes",
    "_ppo_load",
)

#: 跨模块递归解析的最大深度（`engine → common → np_core` 是 2，留裕量）。
_MAX_DEPTH = 4

#: 静态层覆盖不到的形状。
UNRESOLVED = "<unresolved>"


@dataclass(frozen=True)
class Member:
    """一个必需成员在源码里的来路。"""

    name: str
    #: "def" | "import:<module>" | "alias:<name>" | UNRESOLVED
    kind: str
    #: 定义落在 `if` / `try` 体内 ⇒ 在部分环境下可能根本不存在
    conditional: bool = False
    #: "def" / "alias:…" 解析到的那份形参表（签名解析用）
    node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    #: "import:<module>" 时的源模块名
    source_module: str | None = None
    #: "alias:<name>" 时的同模块目标名
    alias_target: str | None = None


@dataclass
class ModuleScan:
    """一个后端模块的静态契约结论。"""

    module: str
    path: Path
    members: dict[str, Member] = field(default_factory=dict)

    @property
    def present(self) -> set[str]:
        return {n for n, m in self.members.items() if m.kind != UNRESOLVED}

    @property
    def missing(self) -> list[str]:
        return [n for n in REQUIRED_MEMBERS if n not in self.present]

    @property
    def conditional(self) -> list[str]:
        return [n for n, m in self.members.items() if m.conditional and n != UNRESOLVED]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]  # tests/helpers/x.py → nn-training/


def module_path(modname: str, *, root: Path | None = None) -> Path | None:
    """`ppo.engine` → `<repo>/ppo/engine.py`；找不到文件时返回 None。"""
    base = (root or _repo_root()) / modname.replace(".", "/")
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _top_level_defs(tree: ast.Module) -> dict[str, Member]:
    """顶层（含 `if` / `try` 体内）的定义 / 别名 / `from … import …` → {name: Member}。"""
    out: dict[str, Member] = {}

    def walk(stmts: list[ast.stmt], *, cond: bool) -> None:
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, Member(node.name, "def", cond, node))
            elif isinstance(node, ast.ClassDef):
                out.setdefault(node.name, Member(node.name, "def", cond))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value, ast.Name):
                        out.setdefault(
                            target.id,
                            Member(target.id, f"alias:{node.value.id}", cond, alias_target=node.value.id),
                        )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    local = alias.asname or alias.name
                    out.setdefault(
                        local,
                        Member(local, f"import:{node.module or ''}", cond, source_module=node.module or ""),
                    )
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body, cond=True)
                walk(getattr(node, "orelse", []), cond=True)
                walk(getattr(node, "finalbody", []), cond=True)
                for handler in getattr(node, "handlers", []):
                    walk(handler.body, cond=True)

    walk(tree.body, cond=False)
    return out


def scan_module(modname: str, *, root: Path | None = None, _depth: int = 0) -> ModuleScan:
    """静态解析 `modname` 的必需成员（`from X import y` 会递归确认 `y` 在 X 里还在）。"""
    path = module_path(modname, root=root)
    if path is None:
        return ModuleScan(modname, Path(f"<missing:{modname}>"), {n: Member(n, UNRESOLVED) for n in REQUIRED_MEMBERS})
    defs = _top_level_defs(source_scan.parse(str(path)))
    members: dict[str, Member] = {}
    for name in REQUIRED_MEMBERS:
        found = defs.get(name)
        if found is None:
            members[name] = Member(name, UNRESOLVED)
            continue
        if found.source_module is not None:
            upstream = (
                scan_module(found.source_module, root=root, _depth=_depth + 1).members.get(name)
                if _depth < _MAX_DEPTH
                else None
            )
            if upstream is not None and upstream.kind != UNRESOLVED and not upstream.conditional:
                # `upstream.node` 可能来自更深一层（engine ← common ← np_core），签名照用。
                members[name] = Member(
                    name, found.kind, found.conditional, upstream.node, found.source_module
                )
            else:
                members[name] = Member(name, UNRESOLVED, found.conditional)
        elif found.alias_target is not None:
            target = defs.get(found.alias_target)
            if target is not None and target.node is not None:
                members[name] = Member(name, found.kind, found.conditional, target.node, alias_target=found.alias_target)
            else:
                members[name] = Member(name, UNRESOLVED, found.conditional)
        else:
            members[name] = found
    return ModuleScan(modname, path, members)


def signature_from_def(node: ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    """把 `def` 的形参表翻成 `inspect.Signature`，供**同一段** `sig.bind(...)` 复用。"""
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    n_defaults = len(a.defaults)
    params: list[inspect.Parameter] = []
    for i, arg in enumerate(positional):
        kind = (
            inspect.Parameter.POSITIONAL_ONLY
            if i < len(a.posonlyargs)
            else inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        has_default = i >= len(positional) - n_defaults
        params.append(
            inspect.Parameter(arg.arg, kind, default=1 if has_default else inspect.Parameter.empty)
        )
    for arg, dflt in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        params.append(
            inspect.Parameter(
                arg.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=1 if dflt is not None else inspect.Parameter.empty,
            )
        )
    if a.vararg is not None:
        params.append(inspect.Parameter(a.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    if a.kwarg is not None:
        params.append(inspect.Parameter(a.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(params)


def update_signature(scan: ModuleScan) -> inspect.Signature | None:
    """`update` 的静态签名（别名链已解开）；解析不出来时 None。"""
    m = scan.members.get("update")
    if m is None or m.node is None:
        return None
    return signature_from_def(m.node)


def bind_report(sig: inspect.Signature, kwargs: dict[str, Any]) -> str:
    """跑一次 `sig.bind(**kwargs)`："" = 可绑定，否则返回错误文本（两侧用**同一段**判据）。"""
    try:
        sig.bind(**kwargs)
    except TypeError as e:  # pragma: no cover - 只在契约破裂时走到
        return str(e)
    return ""


def stream_bind_kwargs(required_update_kwargs) -> dict[str, Any]:
    """`rl/stream.py` 那一次调用的形状：5 个定位参数**按名**传 + 无条件注入的关键字。

    静态侧与运行期侧都用这一份 kwargs 去 bind ⇒ 两侧的分歧只可能来自签名本身，
    不会来自「比的是两套不同调用」。
    """
    return {
        "model": None,
        "opt": None,
        "chunks": [],
        "epochs": 1,
        "device": "cpu",
        **dict.fromkeys(required_update_kwargs),
    }
