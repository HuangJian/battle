"""G20：本地新建的容器不得「只改不读」（`python.refactor.md` §P1-11）。

为什么单独一条：死变量在 AST 里长得不像死代码 —— 它一直在 `append` / `update`，
静态读起来像「正在收集」，只有把**读面**数一遍才发现没有任何人消费。2026-11-24
的清理（`remote/deliver_sync` 的死 `sent`）就是这类：声明 + 三次 append，零读者。

口径（**本地**、**新建**、**有改写**、**零读取**，四条同时成立）：

1. 名字是函数体里的一个简单赋值目标（`x = …` / `x: T = …`），且**只绑定一次**；
2. 右值是新建容器：`[]` / `{}` / `{1,2}` / 三种推导式，或 `list()` / `dict()` /
   `set()` / `deque()` / `Counter()` / `defaultdict(...)` 构造；
3. 至少有一处改写：`x.append/extend/update/add/…`、`x[k] = v`、`del x[k]`、`x += …`；
4. 除此之外**没有任何读取**：没有 `Load`（含 return / 传参 / 迭代 / `len` / 闭包捕获）、
   没有第二次绑定、没有 `global` / `nonlocal`。

保守面（宁可漏报，不误报）：参数、别名（`alias = rows` 是读）、模块级/实例属性容器、
第二处绑定、嵌套函数里的同名参数或绑定 —— 全部不报。「读一次再只改」也不报
（那不是死变量，只是写法差）。扫描面 = 生产包（与 G9 同面；`worker/scripts/` 豁免）。

零容忍：生产面当前必须是 0 处。自检三条（死容器必报 / 读、别名、参数、闭包不报）
保证这条门禁不是空跑。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

CONTAINER_CTORS = frozenset(
    {"list", "dict", "set", "deque", "Counter", "defaultdict", "OrderedDict"}
)
MUTATORS = frozenset(
    {
        "append",
        "appendleft",
        "extend",
        "extendleft",
        "insert",
        "add",
        "update",
        "setdefault",
        "clear",
        "sort",
        "reverse",
        "remove",
        "discard",
        "pop",
        "popitem",
    }
)


def _fresh_container(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        return name in CONTAINER_CTORS
    return False


def _is_subscript_target(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    if isinstance(parent, ast.Assign):
        return node in parent.targets
    if isinstance(parent, ast.AugAssign):
        return parent.target is node
    if isinstance(parent, ast.Delete):
        return node in parent.targets
    return False


def _is_mutation(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """`rows.append(…)` / `rows[k] = v` / `del rows[k]` / `rows += …`。"""
    parent = parents.get(node)
    # rows += [...]
    if isinstance(parent, ast.AugAssign) and parent.target is node:
        return True
    # rows[k] = v / del rows[k]（Subscript 的 value 在 AST 里是 Load，父句柄才是写入）
    if isinstance(parent, ast.Subscript) and parent.value is node:
        return _is_subscript_target(parent, parents)
    # rows.append(...) —— 只认「Name 作为 Attribute 的 value，且该 Attribute 是调用目标」
    if isinstance(parent, ast.Attribute) and parent.value is node:
        call = parents.get(parent)
        if isinstance(call, ast.Call) and call.func is parent and parent.attr in MUTATORS:
            return True
    return False


def _find_in_tree(tree: ast.Module) -> list[tuple[str, str, int]]:
    """`(函数名, 变量名, 行号)`；语义见模块头。"""
    out: list[tuple[str, str, int]] = []
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in functions:
        nodes = list(ast.walk(fn))
        parents: dict[ast.AST, ast.AST] = {}
        for owner in nodes:
            for child in ast.iter_child_nodes(owner):
                parents[child] = owner
        declared = {name for n in nodes if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names}
        # 嵌套作用域里同名参数 / 绑定的名字对「外层变量」不是读，宁可全部跳过。
        shadowed: set[str] = set()
        for inner in functions:
            if inner is fn:
                continue
            if not any(inner is x for x in nodes):
                continue
            for arg in (
                list(inner.args.posonlyargs)
                + list(inner.args.args)
                + list(inner.args.kwonlyargs)
                + ([inner.args.vararg] if inner.args.vararg else [])
                + ([inner.args.kwarg] if inner.args.kwarg else [])
            ):
                shadowed.add(arg.arg)
            for n in ast.walk(inner):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    shadowed.add(n.id)
        bindings: dict[str, list[ast.Name]] = {}
        occurrences: dict[str, list[ast.Name]] = {}
        for n in nodes:
            if not isinstance(n, ast.Name):
                continue
            occurrences.setdefault(n.id, []).append(n)
            if isinstance(n.ctx, ast.Store):
                parent = parents.get(n)
                is_simple = (
                    (isinstance(parent, ast.Assign) and n in parent.targets)
                    or (isinstance(parent, ast.AnnAssign) and parent.target is n)
                )
                if is_simple:
                    bindings.setdefault(n.id, []).append(n)
        for name, sites in bindings.items():
            if name in declared or name in shadowed or len(sites) != 1:
                continue
            site = sites[0]
            parent = parents.get(site)
            value = parent.value if isinstance(parent, (ast.Assign, ast.AnnAssign)) else None
            if not _fresh_container(value):
                continue
            others = [n for n in occurrences[name] if n is not site]
            if not others:
                continue  # 纯未使用变量：不是这条门禁的范围
            if not any(_is_mutation(n, parents) for n in others):
                continue
            if all(
                _is_mutation(n, parents)
                or (isinstance(n.ctx, (ast.Del,)) and isinstance(parents.get(n), ast.Delete))
                for n in others
            ):
                out.append((fn.name, name, site.lineno))
    return out


def find_write_only(source: str) -> list[tuple[str, str, int]]:
    """解析源码后调用 `_find_in_tree`（自检与外部诊断共用）。"""
    return _find_in_tree(ast.parse(source))


def _scan_tree() -> tuple[int, list[str]]:
    scanned = 0
    findings: list[str] = []
    for rel_dir in PROD_DIRS:
        for path in (ROOT / rel_dir).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            scanned += sum(
                1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            for fn, name, line in _find_in_tree(tree):
                findings.append(f"{rel}:{line} {fn}(): `{name}` 只改不读")
    return scanned, findings


def test_production_has_no_write_only_containers() -> None:
    scanned, findings = _scan_tree()
    if scanned <= 3000:
        raise AssertionError(f"扫描面异常（{scanned} 个函数）——门禁不能空跑")
    if findings:
        raise AssertionError("以下本地容器只被改写、没有被读取：\n" + "\n".join(findings))


DEAD = """
def f():
    rows = []
    for i in range(3):
        rows.append(i)
"""


def test_scanner_flags_a_dead_container() -> None:
    hits = [(fn, name) for fn, name, _ in find_write_only(DEAD)]
    if hits != [("f", "rows")]:
        raise AssertionError(f"死容器未被识别：{hits!r}")


def test_scanner_flags_subscript_only_container() -> None:
    src = """
def f():
    tally = {}
    for key in (1, 2):
        tally[key] = key + 1
    del tally[1]
"""
    hits = [(fn, name) for fn, name, _ in find_write_only(src)]
    if hits != [("f", "tally")]:
        raise AssertionError(f"下标写入容器未被识别：{hits!r}")


def test_scanner_accepts_reads_aliases_params_and_closures() -> None:
    src = """
LEDGER = []

def returned():
    rows = []
    rows.append(1)
    return rows

def passed_sink():
    rows = []
    rows.append(1)
    consume(rows)

def aliased():
    rows = []
    alias = rows
    rows.append(1)
    return alias

def parameter(rows):
    rows.append(1)

def shared():
    LEDGER.append(1)

def captured():
    rows = []
    rows.append(1)
    def peek():
        return rows
    return peek

def read_then_mutate():
    rows = []
    rows.append(1)
    if rows:
        rows.append(2)
"""
    assert find_write_only(src) == []
