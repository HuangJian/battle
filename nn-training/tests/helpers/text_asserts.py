"""tests/helpers/text_asserts.py — 「文本断言」的**唯一计数口径**（2026-10-09，T5 / S3-2）。

## 为什么要有这个文件

`plan/nn-training-test-debt-cleanup.plan.md` §2-T5 要清理「断言源码长什么样」的用例，§3-S3-2 要一条
「处数只许降不许升」的护栏。**没有可执行口径的数字是不可证伪的**——评审（2026-10-09）实测同一批文件
按三种读法能数出 20/64/100 三个数，DoD 的「下降 ≥40%」因此无法判定。本文件把口径钉成代码：

## 口径（= 本文件的实现，改了就是改口径）

### 口径 A · 形态（`count_source`）—— 护栏用

**文本断言（text assert）** = 一条 `ast.Assert`，其断言表达式里含**至少一处**这样的比较：

  · 比较用 `in` / `not in`（不是 `==`）—— 断言的是「某段文本里有没有某段文本」；
  · 比较的**一侧是字符串字面量或 f-string**（`"…"` / `f"…"`）；
  · 另一侧**不是字面量容器**（`List/Tuple/Set/Dict`）—— `assert "a" in ["a", "b"]` 是纯数据断言，
    与「文本形态」无关，不算。

计数单位 = **assert 语句条数**（不是字面量处数、不是文件数）。一条 assert 里出现 3 处子串比较
仍记 1（它是一条用例判据，不是一个字面量）。

### 口径 B · 生产源码面（`count_source_asserts`）—— T5 的删除面

在口径 A 之外再加一道 `.py` 门：**被读的东西必须是 `.py` 源码**（或 `*.py` 的集合）。

  · 读的表达式里出现 `.py` 字面量 / `rglob("*.py")` / `getsource`；或
  · 出现源码扫描助手名（`py_files` / `logic_py_files` / `logic_module` / `logic_dotted`）；或
  · 表达式引用了**从上面两种绑出来的名字**（`for f in (ROOT/"trainer").rglob("*.py")` ⇒ `f`；
    `src = path.read_text()` ⇒ `src`）。函数体里读 `.py` 的函数算「生产者」（`_prod_sources()` 那族）。

为什么要这道门：不设门时口径 B 实测 **1524 条 > 口径 A 的 1488 条**——它把「读 golden `.json` 再断
数据」也算成了源码断言（那显然不是 T5 要清的东西）。

## 口径的已知边界（写出来，免得下一个人当 bug 修）

* **A 含「对捕获输出做子串断言」**：`assert "§8 结论与排序" in capsys.readouterr().out` 命中。这是
  **有意的**——它与 `assert "…" in <源码文本>` 同族：判据挂在被测物的**文本措辞**上，改一个标题字
  就红，而改标题字与行为对错无关。
* **A 不含 `==` 比较**：`assert out == "…"` 是「整串相等」，措辞一变也红，但它是**全等**判据，
  改写形态与本族不同 ⇒ 不计（要压它得另立口径，别偷偷混进来）。
* **B 不做数据流/累加分析**：`hits: list[str] = []` 之后 `.append(...)` 拼出来的名字追不到（追不动，
  一个追不动的口径就是不可执行的口径）。B 对 `assert "…" in src` 这类**直读**形态召回完整，
  对「拼装后断言」少计 ⇒ **它是下界**。
* **B 不区分「读的是生产源码」还是「读的是 `.py` 路径字符串」**：`assert manifest["script"] == "x.py"`
  这类也会命中（`.py` 门是文本级的）。判读时逐条看，别按条数批量砍（plan §2-T5「不许批量砍」）。

## 用法

    from tests.helpers import text_asserts

    text_asserts.count_source(src)              # 口径 A
    text_asserts.count_source_asserts(src)      # 口径 B
    text_asserts.totals(nn_root)                # (A 总数, B 总数, {文件: (A, B)})，一次读盘
    python -m tests.helpers.text_asserts --root <nn-training> [--scope form|source] [--json out.json]

`--root` 默认 = 本文件的上两级（`nn-training/`），扫描面 = `tests/**/*.py` + `e2e/**/*.py`
（= 门禁的 pytest 目标集，见 `nn-python-gate.sh:299`）。
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

#: 扫描面（相对 `--root`）：门禁 `pytest tests/ e2e/` 的同一批文件。
SCAN_DIRS: tuple[str, ...] = ("tests", "e2e")

#: 口径 B 的**廉价预筛**字面量（2026-10-10 提速）：`_node_is_py_face` 的三个来源
#: （`.py` 字符串常量 / 源码面助手名 / `getsource`）在合法 Python 里必然是源码里的字面量
#: ⇒ 一个都不出现的文件**连派生名字都建不出来**，不可能有 B 命中。
#: 规矩出处：`tests/helpers/source_scan.py` 头部「先廉价预筛再 ast.parse」。
_PY_FACE_TOKENS: tuple[str, ...] = (
    ".py",
    "py_files",
    "logic_py_files",
    "logic_module",
    "logic_dotted",
    "getsource",
)

#: 字面量容器：另一侧是这些类型时不算「文本断言」（见口径 A 第三条）。
_CONTAINERS = (ast.List, ast.Tuple, ast.Set, ast.Dict)

#: 读源码文本的调用名（属性或裸名）。
_READERS = frozenset({"read_text", "getsource"})

#: `tests/helpers/source_scan` 的源码面助手（出现即「读的是 .py 面」）。
_PY_HELPERS = frozenset({"py_files", "logic_py_files", "logic_module", "logic_dotted"})


@dataclass(frozen=True)
class TextAssert:
    """一条命中的 `assert`：`:line`（1 起）+ 参与比较的字面量（用于人工判读，不参与计数）。"""

    line: int
    negated: bool  # `not in` / `not` 包着 —— 「某物**不**在场」那族（T2 守灵）
    literals: tuple[str, ...]


# ═════════════════════════════ 口径 A：形态 ═════════════════════════════


def _is_str_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_text_expr(node: ast.expr) -> bool:
    """字符串字面量或 f-string（f-string 的片段也是措辞）。"""
    return _is_str_literal(node) or isinstance(node, ast.JoinedStr)


def _literal_text(node: ast.expr) -> str | None:
    # 不调 `_is_str_literal`：mypy 不会跨函数窄化 `node.value`（`ast.expr` 上没有 `.value`）。
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_negated(node: ast.expr) -> bool:
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)


def _comparisons(node: ast.expr) -> list[ast.Compare]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Compare)]


def _form_assert(test: ast.expr) -> TextAssert | None:
    """口径 A：`assert <test>` 的 `<test>` 命中 ⇒ 返回证据（否则 `None`）。"""
    lits: list[str] = []
    negated = False
    hit = False
    for cmp in _comparisons(test):
        lefts = [cmp.left, *cmp.comparators[:-1]]
        for op, left, right in zip(cmp.ops, lefts, cmp.comparators, strict=True):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if isinstance(right, _CONTAINERS):
                continue  # 纯数据断言（`in ["a","b"]`）
            if not (_is_text_expr(left) or _is_text_expr(right)):
                continue
            hit = True
            if isinstance(op, ast.NotIn):
                negated = True
            for side in (left, right):
                text = _literal_text(side)
                if text is not None:
                    lits.append(text)
    if not hit:
        return None
    return TextAssert(line=test.lineno, negated=negated or _is_negated(test), literals=tuple(lits))


def form_asserts(tree: ast.Module) -> tuple[TextAssert, ...]:
    """口径 A 的全部命中（按行号排序）。"""
    return _form_hits(_index(tree).asserts)


def _form_hits(asserts: tuple[ast.Assert, ...]) -> tuple[TextAssert, ...]:
    """口径 A 的命中（吃已算好的 assert 清单 —— 可被 `totals` 与 B 共用）。"""
    out = [
        found
        for node in asserts
        for found in [_form_assert(node.test)]
        if found is not None
    ]
    return tuple(sorted(out, key=lambda a: a.line))


# ═════════════════════════ 口径 B：生产源码面 ═════════════════════════


def _is_reader_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr in _READERS
    return isinstance(f, ast.Name) and f.id in _READERS


def _node_is_py_face(node: ast.AST) -> bool:
    """**单节点**判据（不 unparse、不递归）：这个节点本身就在指 `.py` 源码面吗？

    `ast.unparse` 曾是这里最贵的一步（每个节点一次源码重建）；换成三个廉价的节点判据——
    `.py` 只可能来自字符串字面量（`rglob("*.py")` / `"x.py"`），助手名与 `getsource` 只可能是
    Name/Attribute。口径不变（`.py` 字面量 / 助手名 / `getsource`），开销从「每节点 unparse」降到
    「每节点 isinstance」。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ".py" in node.value
    if isinstance(node, ast.Name):
        return node.id in _PY_HELPERS
    if isinstance(node, ast.Attribute):
        return node.attr in _PY_HELPERS or node.attr == "getsource"
    return False


def _is_py_source(expr: ast.expr, names: frozenset[str]) -> bool:
    """这个表达式读的是 `.py` 源码面（文本门）或引用了由它绑出来的名字。"""
    return any(
        _node_is_py_face(n) or (isinstance(n, ast.Name) and n.id in names) for n in ast.walk(expr)
    )


@dataclass(frozen=True)
class _Index:
    """**一次 `ast.walk`** 收全的三个清单（A/B 两个口径共用，省掉重复遍历）。"""

    #: 「有值」的绑定（Assign / AnnAssign / AugAssign / walrus / for / with-as）—— 闭包迭代的输入。
    binds: tuple[tuple[tuple[ast.expr, ...], ast.expr], ...]
    #: 函数定义（含嵌套/方法）—— `_producer_funcs` 的入参，**顺序 = `ast.walk`（BFS）**。
    funcs: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]
    #: 全部 assert 节点（口径 A/B 的判据入口）。
    asserts: tuple[ast.Assert, ...]


def _index(tree: ast.Module) -> _Index:
    """一遍遍历收全三个清单（2026-10-10 提速：原来 A/B 各走一遍、B 内部又各走几遍）。

    判据与顺序与旧版逐条一致：绑定那支就是原 `_assignments` 的同一串 `if/elif`（同一批
    条件、同一次 `ast.walk` 顺序）；`funcs` 是 **BFS 顺序**（`calls[fn.name] = …` 对同名
    函数是「后者覆盖前者」⇒ 顺序是语义的一部分）。
    """
    binds: list[tuple[tuple[ast.expr, ...], ast.expr]] = []
    funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    asserts: list[ast.Assert] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            asserts.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node)
        elif isinstance(node, ast.Assign):
            binds.append((tuple(node.targets), node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            if node.value is not None:  # `x: int`（裸声明）没有值
                binds.append(((node.target,), node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            binds.append(((node.target,), node.iter))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            binds.append(((node.optional_vars,), node.context_expr))
    return _Index(tuple(binds), tuple(funcs), tuple(asserts))


def _derived_names(binds: tuple[tuple[tuple[ast.expr, ...], ast.expr], ...]) -> frozenset[str]:
    """`py_source_derived_names` 的实现（吃已算好的绑定清单）。

    2026-10-10 提速：先给每条绑定算好「值里有没有 `.py` 门节点 / 引用了哪些名字」（各走一遍
    子树），不动点循环从此只做集合运算。旧版每次迭代都把**所有**绑定表达式重走一遍
    （实测 1.0s → 0.4s）；判据等价（`_is_py_source` 的 `or` 两侧就这两件事）。
    """
    prepared: list[tuple[tuple[ast.expr, ...], bool, frozenset[str]]] = []
    for targets, value in binds:
        face = False
        refs: set[str] = set()
        for n in ast.walk(value):
            if _node_is_py_face(n):
                face = True
            elif isinstance(n, ast.Name):
                refs.add(n.id)
        prepared.append((targets, face, frozenset(refs)))
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for targets, face, ref_names in prepared:
            if not face and not (ref_names & names):
                continue
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in names:
                    names.add(t.id)
                    changed = True
    return frozenset(names)


def py_source_derived_names(tree: ast.Module) -> frozenset[str]:
    """**一级绑定闭包**：由「读 `.py` 源码」赋值出来的名字（含 tuple/多目标/with-as/for 目标）。

    已知边界见模块头（累加式拼装追不到 ⇒ 本口径是下界）。
    """
    return _derived_names(_index(tree).binds)


@dataclass
class _FnFrame:
    """一个函数的「子树事实」：裸名调用 + 有没有 `.py` 门节点（出栈时并给外层）。"""

    called: set[str] = field(default_factory=set)
    face: bool = False


def _producer_funcs(
    tree: ast.Module, funcs: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...] | None = None
) -> frozenset[str]:
    """同文件里**返回 `.py` 源码文本**的函数名（`_prod_sources()` 那族）。

    做法：每个函数体**只走一遍**（收「调了哪些裸名函数」+「体内有没有 `.py` 门节点」），
    然后在调用图上迭代到不动点（`_index().binds` 那头同款：先收清单再迭代，不在树上反复跑）。

    2026-10-10 提速：判据就是 `ast.walk(fn)` 的**并集**，而嵌套函数的子树本来就在外层里 ⇒
    每个节点只归到**最内层**函数、出栈时并给外层（一遍显式栈遍历），不再对每个函数各
    `ast.walk(fn)` 一遍（实测 1.14s → 0.25s）。节点顺序无关紧要（并集）；`calls` 的覆盖
    顺序仍按传进来的 `funcs`（= `ast.walk` 的 BFS 顺序）。
    """
    if funcs is None:
        funcs = _index(tree).funcs
    frames: dict[int, _FnFrame] = {}
    #: 非 AST 的**帧收尾哨兵**（压进同一只工作栈；`list.extend` 前先压它 ⇒ 子树先跑完）。
    close: object = object()
    stack: list[_FnFrame] = []
    work: list[object] = [tree]
    while work:
        item = work.pop()
        if not isinstance(item, ast.AST):  # 唯一的非 AST 项就是哨兵
            frame = stack.pop()
            if stack:
                stack[-1].called |= frame.called
                stack[-1].face = stack[-1].face or frame.face
            continue
        node: ast.AST = item
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            frame = _FnFrame()
            frames[id(node)] = frame
            stack.append(frame)
            work.append(close)
        elif stack:
            cur = stack[-1]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                cur.called.add(node.func.id)
            if not cur.face and _node_is_py_face(node):
                cur.face = True
        work.extend(ast.iter_child_nodes(node))
    calls: dict[str, set[str]] = {}
    direct: set[str] = set()
    for fn in funcs:
        frame = frames[id(fn)]
        calls[fn.name] = frame.called
        if frame.face:
            direct.add(fn.name)
    out = set(direct)
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in out and (called & out):
                out.add(name)
                changed = True
    return frozenset(out)


def source_asserts(tree: ast.Module) -> tuple[TextAssert, ...]:
    """口径 B 的全部命中（按行号排序）。"""
    return _source_hits(tree, _index(tree))


def _source_hits(tree: ast.Module, idx: _Index) -> tuple[TextAssert, ...]:
    """口径 B 的命中（吃已算好的索引 —— 一次遍历供 A/B 两口径）。"""
    names = _derived_names(idx.binds)
    producers = _producer_funcs(tree, idx.funcs)
    out: list[TextAssert] = []
    for node in idx.asserts:
        test = node.test
        direct = (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id in producers
        )
        if not (direct or _is_py_source(test, names)):
            continue
        negated = any(
            isinstance(o, ast.NotIn) for c in _comparisons(test) for o in c.ops
        ) or _is_negated(test)
        lits = [
            n.value
            for n in ast.walk(test)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        out.append(TextAssert(line=node.lineno, negated=negated, literals=tuple(lits)))
    return tuple(sorted(out, key=lambda a: a.line))


# ═════════════════════════════ 文件 / 目录层 ═════════════════════════════


def count_source(source: str, filename: str = "<string>") -> tuple[TextAssert, ...]:
    """口径 A（见模块头）。"""
    return form_asserts(ast.parse(source, filename=filename))


def count_source_asserts(source: str, filename: str = "<string>") -> tuple[TextAssert, ...]:
    """口径 B（见模块头；T5 的删除面，**下界**）。

    先过廉价预筛（`_PY_FACE_TOKENS`）：字面量一个都不出现 ⇒ 不必解析就知道是 0。
    """
    if not any(tok in source for tok in _PY_FACE_TOKENS):
        return ()
    return source_asserts(ast.parse(source, filename=filename))


def scan_paths(root: Path) -> list[Path]:
    """扫描面 = `root/{tests,e2e}` 下的 `*.py`（排序）。"""
    files: list[Path] = []
    for sub in SCAN_DIRS:
        d = root / sub
        if d.is_dir():
            files += list(sorted(d.rglob("*.py")))
    return files


def audit_files(
    paths: list[Path] | tuple[Path, ...], root: Path | None = None
) -> dict[str, tuple[TextAssert, ...]]:
    """逐文件计数口径 A（**只读**；`root` 只影响 key 的相对路径）。

    刻意**不走** `tests.helpers.source_scan.read_text` 缓存：那份缓存的语义是「仓库内**只读**生产源码」，
    而这里扫的是测试层自身（同一次 pytest 里没有任何东西会改写它们）——用不用缓存结果一样，
    但缓存会把 ~300 个测试文件正文常驻到会话结束，得不偿失（见 `source_scan` 头部「AST 不常驻」）。
    """
    out: dict[str, tuple[TextAssert, ...]] = {}
    for p in sorted(paths):
        rel = str(p.relative_to(root)).replace("\\", "/") if root else str(p)
        out[rel] = count_source(p.read_text(encoding="utf-8"), filename=rel)
    return out


def audit_source_text(root: Path) -> dict[str, tuple[TextAssert, ...]]:
    """逐文件口径 B（只要命中的文件）。"""
    out: dict[str, tuple[TextAssert, ...]] = {}
    for p in scan_paths(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        found = count_source_asserts(p.read_text(encoding="utf-8"), filename=rel)
        if found:
            out[rel] = found
    return out


def source_assert_counts(root: Path) -> dict[str, int]:
    """`{文件: 条数}`（只要 >0 的），给护栏测试与归属表用。"""
    return {rel: len(v) for rel, v in audit_source_text(root).items()}


def _counts(tree: ast.Module, *, has_py_face: bool) -> tuple[int, int]:
    """`(口径 A 条数, 口径 B 条数)` —— **一次建索引**（2026-10-10：A/B 共用同一次遍历）。

    `has_py_face=False`（源码里一个 `_PY_FACE_TOKENS` 都没有）⇒ B 直接记 0，连闭包都不建。
    """
    idx = _index(tree)
    form = len(_form_hits(idx.asserts))
    if not has_py_face:
        return form, 0
    names = _derived_names(idx.binds)
    producers = _producer_funcs(tree, idx.funcs)
    py_src = 0
    for node in idx.asserts:
        test = node.test
        direct = (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id in producers
        )
        if direct or _is_py_source(test, names):
            py_src += 1
    return form, py_src


def totals(root: Path) -> tuple[int, int, dict[str, tuple[int, int]]]:
    """`(A 总数, B 总数, {文件: (A, B)})` —— **一次读盘 / 一次解析**出两个口径。

    护栏测试用（`tests/test_source_text_assert_budget.py`）：两个口径各扫一遍会把读盘 + 解析
    付两次，而它们看的是同一批文件。
    """
    per_file: dict[str, tuple[int, int]] = {}
    for p in scan_paths(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        text = p.read_text(encoding="utf-8")
        has_py_face = any(tok in text for tok in _PY_FACE_TOKENS)
        form, py_src = _counts(ast.parse(text, filename=rel), has_py_face=has_py_face)
        if form or py_src:
            per_file[rel] = (form, py_src)
    return (
        sum(f for f, _ in per_file.values()),
        sum(s for _, s in per_file.values()),
        per_file,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文本断言计数（口径见模块头）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--json", default=None, help="把逐文件明细写到该路径")
    ap.add_argument("--top", type=int, default=20, help="按条数打印前 N 个文件")
    ap.add_argument("--scope", choices=("form", "source"), default="form",
                    help="form = 口径 A · source = 口径 B（生产源码面）")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if args.scope == "source":
        src_found = audit_source_text(root)
        n = sum(len(v) for v in src_found.values())
        print(f"生产源码文本断言：{n} 条 / {len(src_found)} 个文件")
        for rel, items in sorted(src_found.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines = ",".join(str(a.line) for a in items)
            print(f"  {len(items):3d}  {rel}  lines={lines}")
        if args.json:
            Path(args.json).write_text(
                json.dumps(
                    {rel: [{"line": a.line, "negated": a.negated} for a in v]
                     for rel, v in src_found.items()},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            print(f"明细 → {args.json}")
        return 0

    found = audit_files(scan_paths(root), root=root)
    total = sum(len(v) for v in found.values())
    per_file = sorted(found.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    print(f"文本断言：{total} 条 / {sum(1 for v in found.values() if v)} 个文件（扫描 {len(found)} 个 .py）")
    for rel, items in per_file[: args.top]:
        if not items:
            break
        neg = sum(1 for a in items if a.negated)
        print(f"  {len(items):3d}  {rel}（其中「不在场」{neg}）")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {rel: [{"line": a.line, "negated": a.negated, "literals": list(a.literals)} for a in v]
                 for rel, v in found.items() if v},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"明细 → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
