"""tests/test_lock_reentrancy.py — 静态守卫：**非可重入锁的临界区不许可达同一把锁**（2026-10-09）。

## 背景（一次真事故，docs/nn/remote-transport.md §74）

`remote/deliver_proc.py::DelivererProcess._append` 持 `threading.Lock()` 调 `_ensure_alive()`，
而按需重启那条路（子进程死了、`result_done` 还没落盘）会回头**再调一次** `_append` 重述段末摘要
⇒ **同一线程自锁死**：`submit_round()` 永不返回（生产形态 = 训练线程永挂，比它要救的「腿半死」
更难查）。形态固定且静态可判 —— 「持锁函数 → 临界区里的调用 → 可达函数 → 也拿同一把锁」。
本守卫就是把那条判据钉进全仓。

## 判据与边界（刻意保守：宁可漏报也不错杀）

* **只查非可重入构造**：`Lock` / `Semaphore` / `BoundedSemaphore`；`RLock` 与 `Condition`
  （缺省内建的就是 RLock）的同线程重入是**合法语义** ⇒ 跳过。
* **同一把锁**按名字归并：`self._lock` 记 `("attr", "_lock")`、模块级/导入的 `_TASK_PACK_LOCK`
  记 `("global", "_TASK_PACK_LOCK")`、函数局部锁记 `("local", 文件, 函数, 名字)`。
  名字归并是为了认得出「定义处 + `from ... import` 处」是同一把（hub/offline.py 就是这么用
  `_TASK_PACK_LOCK` 的）与「mixin 注入的 `self._lock`」（hub/store.py 的 `_lock` 由
  `_AuthGuard.__init__` 建）。代价：同模块内两个**不同**锁若恰好同名会被当成一把 —— 判据只在
  模块内做（见下），所以那只可能造成**多报**，而这正是本仓位宁愿的方向（多报当场可见，漏报是静默的）。
* **调用图是模块内的**（加上闭包链与组合组）：`self.f()`（本模块继承链 → **组合组内跨文件
  同名方法**）· `Klass.f()` · 裸名 `f()`（本模块的模块级函数与**嵌套/闭包函数**）·
  `with <闭包变量>:`（外层函数的局部锁，键归到定义它的那个函数）。参数回调 / 注入的函数
  属性 / 导入的裸名**进不了闭包** ⇒ 它们不参与硬判据，改由下面的**目标面棘轮**兜。
  参数回调与**注入属性**与导入函数的**区别在于能不能看到定义**：看不到就进棘轮。
* **组合组（2026-10-09）**：`hub/store.py` 的 `_JobStore` 是「六个混入 + 组合类」共一把
  `_lock`（30/49 个方法）。混入里 `self._facts_locked()` 的定义在别的文件，模块内解析必然落空 ——
  判据改成：`self.f()` 本模块解析不到时，再到**组合组**（该类向上组合 + 基类闭包的类名集合）里
  按名字找。锁身份也按组归并（组内同名 `self._lock` = 同一对象），**跨组不归并** —— 名字归并的
  假阳（`LogBundle._lock` vs `WorkerServerState._lock`）由此消失。
* 2026-10-09 审计补齐的另一个盲区：「方法内局部锁」（`run()` 里 `lock = Lock()` + 嵌套闭包
  `with lock:`，`eval_dispatch` / `dispatch` 的形状）因登记条件 `self.cls is None` 而
  **完全不在管辖范围**；补上后受管辖锁点 209 个（其中这一族是新增的闭包临界区）。
* **例外通道**：临界区那一行（`with` / `.acquire()`，或它的上一行）带
  `# lock-reentrant-ok: <理由>` ⇒ 跳过该点。今天 0 处 —— 新加一处必须写明「为什么同一线程重入安全」。

## 红检（本用例自带反探针）

`_check()` 先跑一段**合成源码**：§74 的历史形态（`Lock` + 锁内回头调自己）必须被判红，
`RLock` 版必须判绿。判据失效（AST 改法、解析空转、类名解析断了）时这条先红 —— 而没有它，
「全仓 0 命中」与「守卫根本没在看」是同一种绿色。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest

from tests.helpers import source_scan

ROOT = Path(__file__).resolve().parent.parent  # nn-training
REPO = ROOT.parent
#: 扫描面：生产包 + 单测/集成层（测试帮助器也会死锁、一样挂门禁）+ 仓库根 `tools/`（python 工具）。
SCAN_ROOTS: Final = (
    ROOT / "common",
    ROOT / "remote",
    ROOT / "trainer",
    ROOT / "worker",
    ROOT / "hub",
    ROOT / "biz",
    ROOT / "tools",
    ROOT / "tests",
    ROOT / "e2e",
    REPO / "tools",
)
#: 目录名级排除（venv / 临时 / 缓存 / 依赖树）。
SKIP_PARTS: Final = frozenset({".venv", "tmp", "__pycache__", "node_modules", ".git"})

CTORS: Final = frozenset({"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition"})
#: 同线程重入**合法**的构造（Condition 缺省的内部锁就是 RLock）。
REENTRANT: Final = frozenset({"RLock", "Condition"})
#: 锁名后缀（用来认「构造看不见」的那些：导入的、参数注入的、mixin 建的）。
LOCK_SUFFIX: Final = re.compile(r"(lock|cv|cond|sem)$", re.I)
#: 例外标注（临界区那一行或它的上一行）。
OK_MARK: Final = re.compile(r"lock-reentrant-ok:\s*\S")
#: 扫描面下锁点的下限（结构性 sanity：解析面缩水/空转时当场红，而不是「0 命中」的假绿）。
MIN_LOCK_SITES: Final = 60
#: 目标面棘轮：临界区里「看得到调用、看不到目标」的**去重目标数**（今天 15，见文件头「边界」）。
#: 涨了就把数字一起改，并当场回答：那个目标会不会回头拿同一把锁？
#:
#: 2026-10-09 全仓审计逐个回答完的 15 个（全是**注入的可调用属性**，不是方法，组内也解析不到）：
#:   * `_now` ×10（hub 各混入 / `_AuthGuard` / `_JobStore`）—— `_AuthGuard.__init__` 的
#:     `now_fn or time.time`，`_HubQueue.__init__` 的 `self._solo._now`（链尽头仍是时钟）；
#:   * `_clock` ×2（`BulkScheduler` / `PrefetchStore`）—— 构造参数 `clock=time.time`；
#:   * `log` ×1（`CloudEvalRunner`）—— 生产不传 ⇒ `_log_default` → `log_line` → `print`（无锁）；
#:   * `_build` ×1（`CloudEvalRunner`）—— `_eval_job_builder(ctx, ep)` 在 runner **之前**构造，
#:     拿不到 runner 的 `_lock`（回调闭包只捕 `ctx`/`ep`）⇒ 不可能重入同一把；
#:   * `InstallGate → 回调 InstallTicket` —— `ticket()` 持锁只造票（票只存 `_gate`）；
#:     `_valid`/`_cancel`/`_install` 由 **copier 线程**在 `with` 之外调（跨线程，非重入）。
MAX_OPAQUE_CALLS: Final = 15

LockKey = tuple[object, ...]


def _ctor_name(node: ast.AST | None) -> str | None:
    """`threading.Lock()` / `Lock()` / `_th.Lock()` → `"Lock"`。"""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
    return name if name in CTORS else None


@dataclass
class _Site:
    lock: LockKey
    line: int
    end: int
    ctor: str
    ok: bool = False


@dataclass
class _Call:
    kind: str  # self | class | name | other
    name: str
    lineno: int
    #: 参数里把 `self`（或本类的**绑定方法**）交了出去 —— 回调候选：对面能回头拿同一把锁。
    callback: bool = False


@dataclass
class _Fn:
    #: (类名, qualname)——qualname 含闭包链（`run.worker`），嵌套同名函数不再互相覆盖。
    key: tuple[str | None, str]
    path: str
    calls: list[_Call] = field(default_factory=list)
    sites: list[_Site] = field(default_factory=list)
    locals: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return (f"{self.key[0]}." if self.key[0] else "") + str(self.key[1])



@dataclass
class _Module:
    path: str
    fns: dict[tuple[str | None, str], _Fn] = field(default_factory=dict)
    #: 简单名 -> 键（只含模块级函数与嵌套/闭包函数；方法必须经 `self.`/`Cls.` 调用，不算）。
    nested_names: dict[str, list[tuple[str | None, str]]] = field(default_factory=dict)
    bases: dict[str, list[str]] = field(default_factory=dict)
    class_locks: dict[str, dict[str, str]] = field(default_factory=dict)
    global_locks: dict[str, str] = field(default_factory=dict)
    imported_locks: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    defined_names: set[str] = field(default_factory=set)


class _Indexer(ast.NodeVisitor):
    def __init__(self, mod: _Module, lines: list[str]) -> None:
        self.mod = mod
        self.lines = lines
        self.cls: str | None = None
        self.fn: _Fn | None = None
        #: 词法作用域链（最内层在尾）——闭包引用的锁（外层函数的局部 `lock`）靠它解析。
        self.stack: list[_Fn] = []

    # ---- 结构 ----
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.mod.classes.add(node.name)
        self.mod.bases[node.name] = [
            b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases
        ]
        prev, self.cls = self.cls, node.name
        for st in node.body:
            self.visit(st)
        self.cls = prev

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = self.stack[-1] if self.stack else None
        qual = f"{parent.key[1]}.{node.name}" if parent is not None else node.name
        key = (self.cls, qual)
        fn = _Fn(key, self.mod.path)
        self.mod.fns[key] = fn
        self.mod.defined_names.add(node.name)
        if self.cls is None or "." in qual:
            # 模块级函数 + 嵌套/闭包函数：裸名 `f()` 只可能落到这两类上
            self.mod.nested_names.setdefault(node.name, []).append(key)
        prev, self.fn = self.fn, fn
        self.stack.append(fn)
        for st in node.body:
            self.visit(st)
        self.stack.pop()
        self.fn = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    # ---- 锁的创建（赋值）+ 导入 ----
    def visit_Assign(self, node: ast.Assign) -> None:
        ctor = _ctor_name(node.value)
        for t in node.targets:
            if isinstance(t, ast.Name):
                known = ctor is not None or LOCK_SUFFIX.search(t.id) is not None
                if not known:
                    continue
                if self.fn is not None:
                    # 任何函数的局部锁（含**方法内**与外层函数的闭包锁）都要登记：
                    # `clip.run` 里的 `lock = threading.Lock()` + 嵌巢闭包 `with lock:`
                    # 曾是全仓唯一没被本守卫看的锁形（方法内局部锁被历史条件漏掉）。
                    self.fn.locals[t.id] = ctor or "?"
                elif self.fn is None:
                    self.mod.global_locks.setdefault(t.id, ctor or "?")
            elif (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
                and self.cls
                and (ctor is not None or LOCK_SUFFIX.search(t.attr) is not None)
            ):
                self.mod.class_locks.setdefault(self.cls, {}).setdefault(t.attr, ctor or "?")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            name = a.asname or a.name
            if a.name != "*" and LOCK_SUFFIX.search(name):
                # 跨模块同一把锁：导入处与定义处按**名字**归并（hub/offline.py 的 _TASK_PACK_LOCK）。
                self.mod.imported_locks.add(name)
        self.generic_visit(node)

    # ---- 拿锁 + 收集临界区里的调用 ----
    def visit_With(self, node: ast.With) -> None:
        self._register_sites(node)
        if self.fn is None:
            self.generic_visit(node)
            return
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                self.fn.calls.append(self._call(n))
        for item in node.items:
            for n in ast.walk(item.context_expr):
                if isinstance(n, ast.Call):
                    self.fn.calls.append(self._call(n))

    def visit_Call(self, node: ast.Call) -> None:
        if self.fn is not None:
            self.fn.calls.append(self._call(node))
        self.generic_visit(node)

    # ---- 内部 ----
    def _register_sites(self, node: ast.With) -> None:
        if self.fn is None:
            return
        # 只登记本层 `with`：嵌套的由 generic_visit → 自己的 visit_With 登记（否则每层重登记一次）
        for item in node.items:
            key, ctor = self._lock_of(item.context_expr)
            if key is None:
                continue
            self.fn.sites.append(
                _Site(
                    key,
                    node.lineno,
                    node.end_lineno or node.lineno,
                    ctor or "?",
                    self._annotated(node.lineno),
                )
            )
        # `.acquire()`（手写拿锁）也在管辖范围
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "acquire"
            ):
                key, ctor = self._lock_of(inner.func.value)
                if key is not None:
                    self.fn.sites.append(
                        _Site(
                            key,
                            inner.lineno,
                            inner.end_lineno or inner.lineno,
                            ctor or "?",
                            self._annotated(inner.lineno),
                        )
                    )

    def _annotated(self, lineno: int) -> bool:
        for idx in (lineno - 1, lineno - 2):  # 本行 或 上一行
            if 0 <= idx < len(self.lines) and OK_MARK.search(self.lines[idx]):
                return True
        return False

    def _lock_of(self, expr: ast.AST) -> tuple[LockKey | None, str | None]:
        if (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id == "self"
        ):
            if LOCK_SUFFIX.search(expr.attr) is None:
                return None, None
            ctor = (self.mod.class_locks.get(self.cls or "") or {}).get(expr.attr, "?")
            return ("attr", expr.attr), ctor
        if isinstance(expr, ast.Name):
            # 从最内层往外找：`with lock:` 里的 `lock` 可能是外层函数的局部（闭包引用）——
            # 锁身份归到**定义它的那个函数**（与赋值处的键一致），不是引用处的键。
            for owner in reversed(self.stack):
                if expr.id in owner.locals:
                    return ("local", self.mod.path, owner.label, expr.id), owner.locals[expr.id]
            if expr.id in self.mod.global_locks:
                return ("global", expr.id), self.mod.global_locks[expr.id]
            if expr.id in self.mod.imported_locks:
                return ("global", expr.id), "?"
        return None, None

    def _call(self, call: ast.Call) -> _Call:
        f = call.func
        callback = self._passes_self(call)
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name) and f.value.id == "self":
                kind = "self"
            elif isinstance(f.value, ast.Name):
                kind = "class"
            else:
                kind = "other"
        elif isinstance(f, ast.Name):
            kind = "name"
        else:
            kind = "other"
        name = getattr(f, "attr", None) or getattr(f, "id", "") or "<expr>"
        if kind == "class" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            name = f"{f.value.id}.{f.attr}"
        return _Call(kind, name, call.lineno, callback)

    def _passes_self(self, call: ast.Call) -> bool:
        """参数里出现裸 `self` 或本类的**绑定方法** `self.m`（m 是本模块里定义的方法）⇒ 回调候选。

        `self.inflight` 这类**数据属性**不算（对方只拿到一份 dict）；`bool(self.x)` / `len(self.x)`
        也不算 —— 判据盯的是「对面拿着能回调进本对象的入口」。
        """
        args = [*call.args, *[k.value for k in call.keywords]]
        for arg in args:
            if isinstance(arg, ast.Name) and arg.id == "self":
                return True
            if (
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "self"
                and (self.cls, arg.attr) in self.mod.fns
            ):
                return True
        return False


def _build(path: Path) -> _Module:
    text = source_scan.read_text(str(path))
    tree = ast.parse(text, filename=str(path))
    mod = _Module(str(path))
    _Indexer(mod, text.splitlines()).visit(tree)
    return mod


@dataclass
class _Report:
    sites: int = 0
    problems: list[str] = field(default_factory=list)
    #: **目标面**（去重后的「哪个类上的哪个目标」）-> 出现位置。硬计数的是面，不是调用点：
    #: 同一个已审查过的目标多调一次不改变答案；新目标出现才必须当场回答。
    opaque: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _Audit:
    mods: dict[str, _Module]
    #: (文件, 类) -> 组合组（可能共享同一个 `self` 的类名闭包）——跨文件 mixin 靠它
    groups: dict[tuple[str, str], frozenset[str]]
    #: 类名 -> 定义它的模块（组内跨模块解析用）
    cls_mods: dict[str, list[_Module]]


def _compose_groups(mods: dict[str, _Module]) -> dict[tuple[str, str], frozenset[str]]:
    """每个类的**组合组**：可能共享同一个 `self` 的类名集合（向上组合 + 基类闭包）。

    hub store 是「六个混入 + 组合类」共一把 `_lock`（30/49 个方法）。混入里 `self._facts_locked()`
    的定义根本不在调用方文件（store_scheduling.py）—— 组内按名字解析才看得见它；
    而**跨组不归并**：`LogBundle._lock` 与 `WorkerServerState._lock` 同名但不同对象，
    2026-10-09 全仓审计里名字归并的假阳全来自这里。
    """
    all_bases: dict[str, list[str]] = {}
    for m in mods.values():
        for c, bs in m.bases.items():
            all_bases[c] = list(dict.fromkeys(all_bases.get(c, []) + list(bs)))
    #: 反向边（基类 -> 直接子类/组合类）：向上闭包靠它走，避免每类扫全表（~1500 类时那是 22M 次）
    children: dict[str, list[str]] = {}
    for c, bs in all_bases.items():
        for b in bs:
            children.setdefault(b, []).append(c)
    memo: dict[str, frozenset[str]] = {}
    up_memo: dict[str, frozenset[str]] = {}

    def closure(cls: str) -> frozenset[str]:
        """该类（传递地）的全部基类，含自己。"""
        if cls in memo:
            return memo[cls]
        out, stack = {cls}, [cls]
        while stack:
            c = stack.pop()
            for b in all_bases.get(c, []):
                if b and b not in out:
                    out.add(b)
                    stack.append(b)
        memo[cls] = frozenset(out)
        return memo[cls]

    def upward(cls: str) -> frozenset[str]:
        """把 cls（传递地）当基类的全部类 —— 运行时 `self` 可能是它们。"""
        if cls in up_memo:
            return up_memo[cls]
        out, seen = set(), set(children.get(cls, []))
        stack = list(seen)
        while stack:
            s = stack.pop()
            out.add(s)
            for t in children.get(s, []):
                if t not in seen:
                    seen.add(t)
                    stack.append(t)
        up_memo[cls] = frozenset(out)
        return up_memo[cls]

    groups: dict[tuple[str, str], frozenset[str]] = {}
    for m in mods.values():
        for c in m.classes:
            grp = set(closure(c))
            for s in upward(c):
                grp |= closure(s)
            groups[(m.path, c)] = frozenset(grp)
    return groups


def _canonical_lock(audit: _Audit, lock: LockKey, mod: _Module, fn: _Fn) -> LockKey:
    """锁身份归一：`attr` 锁按**组合组**归并（组内同名 = 同一对象），其余原样。"""
    if lock[0] != "attr" or not fn.key[0]:
        return lock
    grp = audit.groups.get((mod.path, fn.key[0]))
    return ("attr", grp if grp is not None else fn.key[0], lock[1])


def _resolve(
    audit: _Audit, kind: str, name: str, fn: _Fn, mod: _Module
) -> list[tuple[_Module, tuple[str | None, str]]]:
    """把调用解析成 (模块, 函数键) 列表（解析不到 ⇒ 空 ⇒ 进回调面棘轮）。

    `self.f()`：本模块继承链 → 组合组内跨模块同名方法；`Klass.f()`：本模块类；
    裸名 `f()`：本模块的模块级/嵌套（闭包）函数 —— 导入的裸名与参数回调仍解析不到。
    """
    if kind == "self":
        out: list[tuple[_Module, tuple[str | None, str]]] = []
        cur, seen = fn.key[0], set()
        while cur and cur not in seen:
            seen.add(cur)
            if (cur, name) in mod.fns:
                out.append((mod, (cur, name)))
            cur = next((b for b in mod.bases.get(cur, []) if b in mod.classes), None)
        if not out and fn.key[0]:
            grp = audit.groups.get((mod.path, fn.key[0]), frozenset())
            for g in sorted(grp):
                for gm in audit.cls_mods.get(g, []):
                    if (g, name) in gm.fns:
                        out.append((gm, (g, name)))
        return out
    if kind == "class":
        owner, _, meth = name.partition(".")
        return [(mod, (owner, meth))] if meth and (owner, meth) in mod.fns else []
    if kind == "name":
        return [(mod, k) for k in mod.nested_names.get(name, [])]
    return []


def _reachable_acquirers(
    audit: _Audit,
    starts: list[tuple[_Module, tuple[str | None, str]]],
    lock_cid: LockKey,
) -> list[tuple[_Fn, int]]:
    """从临界区里的调用出发做可达性 BFS，返回「也拿同一把锁」的 (函数, 行)。"""
    seen: set[tuple[str, tuple[str | None, str]]] = set()
    hits: list[tuple[_Fn, int]] = []
    queue = list(starts)
    while queue and len(seen) < 2000:
        m, key = queue.pop()
        if (m.path, key) in seen or key not in m.fns:
            continue
        seen.add((m.path, key))
        cur = m.fns[key]
        for site in cur.sites:
            if not site.ok and _canonical_lock(audit, site.lock, m, cur) == lock_cid:
                hits.append((cur, site.line))
        for call in cur.calls:
            for nm, nk in _resolve(audit, call.kind, call.name, cur, m):
                if (nm.path, nk) not in seen:
                    queue.append((nm, nk))
    return hits


#: 全仓扫描结果缓存（两个全仓用例共用一次解析；源文件在测试进程里不会变）。
_REPO_CACHE: dict[str, _Report] = {}


def _repo_report() -> _Report:
    """全仓扫描（源文件白名单见 `SCAN_ROOTS`）——只跑一次，两个用例共用。"""
    if not _REPO_CACHE:
        sources = {
            str(p): source_scan.read_text(str(p))
            for root in SCAN_ROOTS
            for p in source_scan.py_files(str(root))
            if not any(part in SKIP_PARTS for part in p.parts)
        }
        assert len(sources) > 200, f"扫描面缩水了（只拿到 {len(sources)} 个文件）"
        _REPO_CACHE["report"] = _check(sources)
    return _REPO_CACHE["report"]


def _check(sources: dict[str, str]) -> _Report:
    """对「路径 → 源码」跑一遍判据（真仓库与合成反探针共用同一条路径）。"""
    rep = _Report()
    mods: dict[str, _Module] = {}
    for path_str, text in sources.items():
        mod = _Module(path_str)
        _Indexer(mod, text.splitlines()).visit(ast.parse(text, filename=path_str))
        mods[path_str] = mod
    audit = _Audit(mods=mods, groups=_compose_groups(mods), cls_mods={})
    for m in mods.values():
        for c in m.classes:
            audit.cls_mods.setdefault(c, []).append(m)
    for path_str, mod in mods.items():
        for fn in mod.fns.values():
            for site in fn.sites:
                if site.ctor in REENTRANT or site.ok:
                    continue
                rep.sites += 1
                # 同一个调用可能被 with 体与 visit_Call 各收一次 ⇒ 先按 (kind, name, 行) 去重；
                # 回调判据必须逐**调用**看（同行两个调用：把 self 交出去的是哪一个要分得清）。
                inside = {
                    (c.kind, c.name, c.lineno): c
                    for c in fn.calls
                    if site.line < c.lineno <= site.end
                }
                starts: list[tuple[_Module, tuple[str | None, str]]] = []
                for kind, name, _ln in sorted(inside):
                    if kind == "other":
                        continue
                    starts.extend(_resolve(audit, kind, name, fn, mod))
                # ★ **不**把「持锁函数自己」排除在外：§74 的真身正是锁内回头再调自己
                #   （同一个 `with` 语句被二次进入）⇒ 那是最典型的一类命中，不是噪声。
                cid = _canonical_lock(audit, site.lock, mod, fn)
                hits = _reachable_acquirers(audit, starts, cid)
                if hits:
                    where = ", ".join(f"{h.label}()@{h.path}:{ln}" for h, ln in hits)
                    rep.problems.append(
                        f"{path_str}:{site.line} {fn.label} 持 {site.lock}（{site.ctor}）"
                        f" → 可达 {where} 也在拿同一把锁"
                    )
                    continue
                for (kind, name, ln), call in sorted(inside.items()):
                    owner = fn.key[0] or "<module>"
                    if kind == "self" and not _resolve(audit, kind, name, fn, mod):
                        rep.opaque.setdefault(f"{owner}.{name}", []).append(f"{path_str}:{ln} {fn.label}")
                    elif call.callback:
                        rep.opaque.setdefault(f"{owner} → 回调 {name}", []).append(
                            f"{path_str}:{ln} {fn.label}"
                        )
    return rep


# ------------------------------------------------------------------ 反探针（合成的历史形态）

_HISTORICAL_BUG = """
import threading
import time


class Deliverer:
    def __init__(self):
        self._lock = threading.%(ctor)s()
        self._proc = None
        self._final = {"final": 1}

    def append(self, obj):
        with self._lock:
            self._ensure_alive()
            self._write(obj)

    def _ensure_alive(self):
        if self._proc is not None:
            return
        self._final = None
        if self._final is None:
            self.append({"final": 1})

    def _write(self, obj):
        time.sleep(0)
"""


#: 方法内局部锁 + 嵌套闭包引用同一个 `lock` —— 2026-10-09 审计发现的守卫盲区
#: （`eval_dispatch` / `dispatch` 的 `run()` 就是这个形状：一把 `threading.Lock()`，
#: 十几个嵌套闭包 `with lock:`）。同一把锁在闭包链里被重入 = 同样的自锁死。
_CLOSURE_BUG = """
import threading


class Runner:
    def run(self):
        lock = threading.%(ctor)s()

        def work():
            with lock:
                pump()

        def pump():
            with lock:
                pass

        work()
"""

#: 组合组反探针（hub store 形态）：混入 `Ledger` 持锁调 `self._bump_locked()`，而它的定义在
#: **另一个文件**的混入 `Lease`，两者由 `Store` 组合成同一个运行时对象 ⇒ 同一把 `self._lock`。
_MIXIN_BUG = """
import threading


class Ledger:
    _lock: threading.Lock

    def publish(self):
        with self._lock:
            self._bump_locked()


class Lease:
    def _bump_locked(self):
        with self._lock:
            pass


class Store(Ledger, Lease):
    def __init__(self):
        self._lock = threading.Lock()
"""

#: 反噪声控制：不在同一组合组里的同名 `_lock` 方法 —— 不得互相牵连。
_MIXIN_UNRELATED = """
import threading


class Other:
    def __init__(self):
        self._lock = threading.Lock()

    def _bump_locked(self):
        with self._lock:
            pass
"""

#: 反噪声控制：两个**不相关**的函数各持一把同名局部锁 —— 不得互相牵连。
_UNRELATED_LOCAL_LOCKS = """
import threading


def helper():
    other = threading.Lock()
    with other:
        pass


class A:
    def one(self):
        lock = threading.Lock()
        with lock:
            helper()
"""


def test_checker_flags_the_historical_pattern_and_spares_the_rlock_version() -> None:
    """§74 的形态必须被判红；同一段代码把 `Lock` 换成 `RLock` 必须判绿（判据不是「锁内调用」本身）。"""
    red = _check({"synthetic.py": _HISTORICAL_BUG % {"ctor": "Lock"}})
    assert red.problems, "合成的自锁死没被判红 —— 判据失效（AST/解析/继承链任一环断了）"
    assert "append" in red.problems[0] and "_lock" in red.problems[0], red.problems
    green = _check({"synthetic.py": _HISTORICAL_BUG % {"ctor": "RLock"}})
    assert not green.problems, green.problems
    assert green.sites == 0, "RLock 不该进入受管辖的锁点集"


def test_checker_sees_method_local_and_closure_locks() -> None:
    """方法内局部锁 + 闭包引用必须在管辖范围内（2026-10-09 审计发现的盲区）。

    红检：`run()` 的局部 `lock` 被嵌套闭包 `work`/`pump` 共用，`work` 持锁调 `pump`
    （`pump` 也拿同一把）必须报出；换成 `RLock` 必须判绿。
    绿检（防错杀）：两个不相关函数各持一把**同名**局部锁，必须互不牵连。
    """
    red = _check({"closure_synth.py": _CLOSURE_BUG % {"ctor": "Lock"}})
    assert red.sites == 2, f"闭包锁点没被认出来（认出 {red.sites} 个）—— 本用例的前提失效"
    assert red.problems, (
        "方法内局部锁（闭包共用的 `lock`）的重入没被判红 —— 守卫又只剩模块级锁可见了"
    )
    assert "pump" in red.problems[0] or "work" in red.problems[0], red.problems
    green = _check({"closure_synth.py": _CLOSURE_BUG % {"ctor": "RLock"}})
    assert not green.problems, green.problems
    assert green.sites == 0, "RLock 不该进入受管辖的锁点集"
    clean = _check({"unrelated_synth.py": _UNRELATED_LOCAL_LOCKS})
    assert clean.sites == 2, clean.sites
    assert not clean.problems, f"不同函数的同名局部锁被当成同一把（错杀）：{clean.problems}"


def test_checker_sees_mixin_locks_across_files() -> None:
    """组合组（hub store 形态）必须在管辖范围内：跨文件混入互调同组同名锁 = 同一对象。

    红检：`Ledger.publish` 持锁调 `self._bump_locked()`（定义在 `Lease`，同一组合对象）必须判红。
    绿检（防错杀）：把不相干的 `Other._bump_locked`（另一组合组、同名锁）一起喂进去，
    问题数必须仍是 1 —— 跨组名字归并的假阳不得回来。
    """
    red = _check({"mix_core.py": _MIXIN_BUG})
    assert red.sites == 2, f"混入/组合类里的 self._lock 没被认全（{red.sites}）"
    assert red.problems, "同组混入的同名锁重入没被判红 —— 组合组解析失效（hub store 的盲区回来了）"
    assert "publish" in red.problems[0], red.problems
    both = _check({"mix_core.py": _MIXIN_BUG, "unrelated.py": _MIXIN_UNRELATED})
    assert both.sites == 3, both.sites
    assert len(both.problems) == 1, f"跨组同名锁被当成同一把（错杀）：{both.problems}"


@pytest.mark.time_budget(15)  # 全仓静态解析（612 模块 / 209 个锁点）：单跑 2.6s，门禁 8 worker 下 ~5s
def test_no_rerun_of_an_owned_lock_through_the_critical_section() -> None:
    """全仓硬判据：非可重入锁的临界区里，不许可达「也拿同一把锁」的函数。"""
    rep = _repo_report()
    assert rep.sites >= MIN_LOCK_SITES, (
        f"只认出 {rep.sites} 个锁点（下限 {MIN_LOCK_SITES}）—— 解析面或构造识别坏了，"
        "此时「0 命中」是假绿"
    )
    assert not rep.problems, (
        "非可重入锁被同一线程重入（自锁死，见 modules 头 / docs/nn/remote-transport.md §74）：\n  "
        + "\n  ".join(rep.problems)
        + "\n（确属设计如此 ⇒ 在临界区那一行加 `# lock-reentrant-ok: <理由>`）"
    )


def test_opaque_targets_under_a_lock_stay_a_short_ledger() -> None:
    """回调面棘轮：临界区里「看得到调用、看不到目标」的**目标面**只许有意地涨。

    2026-10-09 升级后棘轮按「哪个类上的哪个目标」去重（同一目标多调一次不重复计数），
    已审查的目标（全部是**注入的可调用属性**，不是方法，组内也解析不到）见
    `MAX_OPAQUE_CALLS` 旁的清单：`_now` / `_clock` / `log` / `_build` 都是构造时注入的
    时钟/日志/回调函数（构造点逐个看过：`time.time` / 日志函数 / 已审的构建回调）。
    新目标出现 ⇒ 当场回答「它会不会回头拿同一把锁」，答完再改常量。
    """
    rep = _repo_report()
    assert len(rep.opaque) <= MAX_OPAQUE_CALLS, (
        f"锁内「看不到目标」的目标面从 {MAX_OPAQUE_CALLS} 涨到 {len(rep.opaque)}：\n  "
        + "\n  ".join(f"{k}  @ {v[0]}" for k, v in sorted(rep.opaque.items()))
        + "\n（逐个确认：目标会不会拿同一把锁？确认完把 MAX_OPAQUE_CALLS 改到新值）"
    )
