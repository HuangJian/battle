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

零容忍：生产面当前必须是 0 处。自检三条（死容器必报 / 下标写入必报 / 读、别名、参数、
闭包不报）保证这条门禁不是空跑。

扫描按**文件内容哈希**缓存（`tests/helpers/scan_cache.py`）：同一份源码只解析一次，
跨次门禁直接复用；判据与消息一字不变。嵌套作用域的归属在一次 DFS 里建表（不再对每个
函数重扫全模块的函数表），整树扫描从 ~8s 降到冷 ~2s / 热 ~0.1s。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from tests.helpers import scan_cache, source_scan

ROOT = Path(__file__).resolve().parents[1]
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 逐文件结论的磁盘缓存版本（`tests/helpers/scan_cache.py`）：判据 / 消息形状变了就 +1。
_CACHE_VERSION = 1

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


def _nested_functions(
    tree: ast.Module,
) -> dict[ast.AST, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """每个函数 → 它（任意深度）内嵌的函数，一次 DFS 建好。

    原先对每个函数都遍历整份模块的函数表再用 `inner is x for x in nodes` 判断归属，
    是全仓扫描里唯一的平方项；这里换成祖先栈，代价随节点数线性增长。
    """
    nested: dict[ast.AST, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    stack: list[tuple[ast.AST, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]]] = [(tree, ())]
    while stack:
        node, enclosing = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for owner in enclosing:
                nested.setdefault(owner, []).append(node)
            enclosing = (*enclosing, node)
        for child in ast.iter_child_nodes(node):
            stack.append((child, enclosing))
    return nested


def _shadowed_names(
    inner_funcs: Iterable[ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[str]:
    """嵌套作用域里同名参数 / 绑定的名字对「外层变量」不是读，宁可全部跳过。"""
    shadowed: set[str] = set()
    for inner in inner_funcs:
        args = inner.args
        for arg in (
            list(args.posonlyargs)
            + list(args.args)
            + list(args.kwonlyargs)
            + ([args.vararg] if args.vararg else [])
            + ([args.kwarg] if args.kwarg else [])
        ):
            shadowed.add(arg.arg)
        for node in ast.walk(inner):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                shadowed.add(node.id)
    return shadowed


def _parents(nodes: list[ast.AST]) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for owner in nodes:
        for child in ast.iter_child_nodes(owner):
            parents[child] = owner
    return parents


def _scan_module(tree: ast.Module) -> tuple[int, list[tuple[str, str, int]]]:
    """`(函数个数, [(函数名, 变量名, 行号)])`；语义见模块头。"""
    functions = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    nested = _nested_functions(tree)
    out: list[tuple[str, str, int]] = []
    for fn in functions:
        nodes = list(ast.walk(fn))
        declared = {
            name for n in nodes if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names
        }
        shadowed = _shadowed_names(nested.get(fn, ()))
        occurrences: dict[str, list[ast.Name]] = {}
        sites: dict[str, list[tuple[ast.Name, ast.AST | None]]] = {}
        for n in nodes:
            if isinstance(n, ast.Name):
                occurrences.setdefault(n.id, []).append(n)
            elif isinstance(n, ast.Assign):
                for target in n.targets:
                    if isinstance(target, ast.Name):
                        sites.setdefault(target.id, []).append((target, n.value))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                sites.setdefault(n.target.id, []).append((n.target, n.value))
        candidates = [
            sites_of_name
            for name, sites_of_name in sites.items()
            if name not in declared and name not in shadowed and len(sites_of_name) == 1
        ]
        if not candidates:
            continue
        # 父节点表只给真正的候选建（多数函数一处新建容器都没有），这是第二个大头的省法。
        parents: dict[ast.AST, ast.AST] | None = None
        for sites_of_name in candidates:
            site, value = sites_of_name[0]
            if not _fresh_container(value):
                continue
            others = [n for n in occurrences[site.id] if n is not site]
            if not others:
                continue  # 纯未使用变量：不是这条门禁的范围
            if parents is None:
                parents = _parents(nodes)
            if not any(_is_mutation(n, parents) for n in others):
                continue
            if all(
                _is_mutation(n, parents)
                or (isinstance(n.ctx, ast.Del) and isinstance(parents.get(n), ast.Delete))
                for n in others
            ):
                out.append((fn.name, site.id, site.lineno))
    return len(functions), out


def find_write_only(source: str) -> list[tuple[str, str, int]]:
    """解析源码后调用 `_scan_module`（自检与外部诊断共用）。"""
    return _scan_module(ast.parse(source))[1]


def _scan_file(path: Path, rel: str, cache: scan_cache.Cache) -> tuple[int, list[str]]:
    source = source_scan.read_text(str(path))
    hit: tuple[int, list[tuple[str, str, int]]] | None = cache.get(rel, source)
    if hit is None:
        hit = _scan_module(ast.parse(source, filename=rel))
        cache.put(rel, source, hit)
    count, raw = hit
    return count, [f"{rel}:{line} {fn}(): `{name}` 只改不读" for fn, name, line in raw]


def _scan_tree() -> tuple[int, list[str]]:
    scanned = 0
    findings: list[str] = []
    cache = scan_cache.Cache("write-only-containers", version=_CACHE_VERSION, root=ROOT)
    for rel_dir in PROD_DIRS:
        for path in source_scan.py_files(str(ROOT / rel_dir)):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            count, problems = _scan_file(path, rel, cache)
            scanned += count
            findings += problems
    cache.save(full_scan=True)  # 扫的是完整扫描面 ⇒ 顺手清掉旧内容的条目
    return scanned, findings


def test_production_has_no_write_only_containers() -> None:
    scanned, findings = _scan_tree()
    if scanned <= 3000:
        raise AssertionError(f"扫描面异常（{scanned} 个函数）——门禁不能空跑")
    if findings:
        raise AssertionError("以下本地容器只被改写、没有被读取：\n" + "\n".join(findings))


DEAD = """\
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
    src = """\
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
    src = """\
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
    hits = find_write_only(src)
    if hits:
        raise AssertionError(f"以下形态不该被报：{hits!r}")
