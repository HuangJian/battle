"""tests/helpers/source_scan.py — 源码扫描用的**文本缓存**（读盘一次）+ AST 即用即弃。

## 为什么

一批「分层 / 单一来源 / 注入点」守卫用例各自 `rglob("*.py")` → `read_text` → `ast.parse`
全仓生产代码（~650 文件）。这些只读检查**同一个 pytest 进程里会重复很多遍**（每个文件被好几个
用例、甚至同一用例的多条判据各读各解析一次），单次解析不贵、重复起来就是秒级墙钟。
2026-09-26 墙钟收敛：把「读盘 + 解析」收成一个 lru_cache。

## ⚠ 2026-09-29 改判：AST **不再常驻**（原 lru_cache 是净亏损）

`parse` 原先把 `ast.Module` 缓存在进程里。AST 是**巨型对象图**（一个模块上万节点，653 个文件
≈ 百万级对象），常驻后 cyclic GC 每一轮都要遍历它 —— 实测（同一批 653 文件，`tmp/dur/gc_probe.py`）：

| 形态 | 解析 653 文件 | 之后一次 `gc.collect()` |
|---|---|---|
| 常驻 AST（关 GC 量的裸时间） | 0.73s | **0.74s** |
| 用完即释放 | 0.69s | 0.002s |

GC 开着的实际代价：常驻 **2.55s** vs 不常驻 **0.82s**（3.1×）。更要命的是它**随常驻量累积**——
同一次会话里越靠后的扫描用例越慢（`--durations` 实测：同一批 204 个文件，会话第 2 个用例解析
只要 0.41s，第 11 个用例要 1.78s）。缓存省下的是「重复解析」的毫秒级，付出的是 GC 的秒级。

现在：**文本走 `read_text` 缓存**（字符串小、廉价、GC 友好），**AST 每次现解析、用完即释放**。
全量门禁实测调用面：2775 次 parse 调用 / 12 个 worker ⇒ 每个 worker 多付 ~0.3s 墙钟，换掉的是
上表那 3.1× 与累积退化。

## 安全性

* 仓库源码在一次 pytest 进程里**不会变**（用例不写生产文件），所以缓存内容与直接从盘读一致。
* xdist 每个 worker 是独立进程 ⇒ 各自一份缓存，不跨进程共享，没有并发写问题（lru_cache 线程安全）。
* **只用于仓库内的只读源码**；用例自己写进 `tmp_path` 又改写的文件不要走这里（会拿到旧内容）。

## 用法

    from tests.helpers import source_scan

    src = source_scan.read_text(str(path))          # 只读字符串（缓存）
    tree = source_scan.parse(str(path))             # AST（每次现解析，别把它挂到模块级容器）
    tree = source_scan.parse(str(path), errors="replace")  # 与 read_text 同参数

**派生小结果可以缓存**（与 AST 相反：它们是字符串集合，小、GC 友好）：

    files = source_scan.py_files(str(NN_ROOT / "rl"))      # 该目录下 *.py（排序、缓存）
    mods = source_scan.imports(str(path), root=str(NN_ROOT))  # 完整点分模块名集合（缓存）

`imports` 是「读一次 AST 换一份 string set」——同一份文件被分层的固定点循环、多个用例反复问时
只付一次解析；`test_layering` 的 `_rl_reaching_remote` 以前在 while 循环里每次重新解析全 `rl/`
（每次 ~650 文件），现在退化成一次集合运算。

## 给「全仓扫描」用例的两条提速建议（都不改判据语义）

1. **先廉价预筛再解析**：AST 判据几乎都能对应到源码里的一个字面量（`text=` 关键字、
   `_progress_logger` 这个标识符……）。先 `read_text`（缓存、~0.05ms）做子串判断，只有
   可能命中的文件才 `parse`。子串判据是**充分的**（合法 Python 里 kwarg 名/标识符就是源码
   里的字面量），所以不会漏判。
2. **`ast.walk` 只用在预筛剩下的文件上**：`ast.walk` 走 `iter_child_nodes`/`iter_fields`
   （每字段一次 `getattr`），全仓 25 万节点量级时它自己就是秒级开销。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType


@cache
def read_text(path: str, errors: str = "strict") -> str:
    """`Path(path).read_text(encoding="utf-8")` 的缓存版（`errors` 同 `Path.read_text`）。"""
    return Path(path).read_text(encoding="utf-8", errors=errors)


def parse(path: str, errors: str = "strict") -> ast.Module:
    """`ast.parse(read_text(path), filename=path)`。

    **刻意不缓存返回值**（原 `@cache` 是净亏损，见模块头「2026-09-29 改判」）：
    调用方拿到需要的信息后请立刻放手，不要把 AST 存进模块级容器 / fixture 里。
    """
    return ast.parse(read_text(path, errors), filename=path)


@cache
def py_files(root: str) -> tuple[Path, ...]:
    """`root` 下的 `*.py`（排序、缓存）。

    `rglob` 是**每调用一次就走一遍目录树**的 I/O；分层/单一来源那批守卫用例每个都要一次，
    缓存的是一份 Path 元组（小对象），代价可以忽略。
    """
    return tuple(sorted(Path(root).rglob("*.py")))


@cache
def _subpackages(root: str) -> frozenset[str]:
    """`root` 下**带 `__init__.py`** 的包子目录名。

    它是 `from <pkg> import <mod>` 的展开依据：裸包名与子模块是两条不同的依赖声明，
    只记裸包会让「纯逻辑 → 编排」这类断言**静默失效**（见 `test_layering` 头部的事故）。
    """
    return frozenset(
        p.name for p in Path(root).iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


@cache
def _facts_for(
    path: str, only: frozenset[str]
) -> tuple[Mapping[str, int], frozenset[str]]:
    """`_module_facts` 的**白名单版**：只认 `only` 里的名字，且先过廉价子串预筛。

    判据是**语法量**（`self.<attr>(…)` 的 `attr`、`def`/`class` 的 `name`），而它在源码里
    必然是字面量 ⇒ `name in text` 是**充分**条件（合法 Python 里不会出现「AST 里有这个名字、
    字面量却不在源码里」的情况），跳过只可能跳过「本来就不含它的文件」。

    预筛是**近似免费**的：102 个文件 × 十来个名字的子串扫描 ≈ 毫秒级；而被它挡掉的
    `ast.parse` + `ast.walk`（每文件 ~5ms，全 `rl/` 一次 ~0.5s）才是那批守卫的成本。
    """
    text = read_text(path)
    if not any(name in text for name in only):
        return MappingProxyType({}), frozenset()
    counts, defs = _module_facts(path)
    return (
        MappingProxyType({k: v for k, v in counts.items() if k in only}),
        frozenset(n for n in defs if n in only),
    )


@cache
def _module_facts(path: str) -> tuple[Mapping[str, int], frozenset[str]]:
    """文件的 (self 调用计数, 占名定义) —— **一次解析**导出两份派生小结果。

    「入边 / 出边 / 槽位」那批三张表守卫对全 `rl/`（102 文件）两样都要，而且分散在三个用例
    模块里各问一次：合在这里解析一遍，换来的是「重复解析」消失、而常驻物只是两个小容器
    （AST 本身用完即释放，见模块头）。
    """
    tree = parse(path)
    defs: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(node.name)
            if isinstance(node, ast.ClassDef):
                defs.update(
                    m.name
                    for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
    counts: dict[str, int] = {}
    for walk_node in ast.walk(tree):
        if (
            isinstance(walk_node, ast.Call)
            and isinstance(walk_node.func, ast.Attribute)
            and isinstance(walk_node.func.value, ast.Name)
            and walk_node.func.value.id == "self"
        ):
            counts[walk_node.func.attr] = counts.get(walk_node.func.attr, 0) + 1
    return MappingProxyType(counts), frozenset(defs)


def self_call_counts(path: str, only: frozenset[str] | None = None) -> Mapping[str, int]:
    """源码里**真实的** `self.<attr>(…)` 调用计数（AST）。

    不能退化成 `src.count("self.x(")`：文档字符串/注释里提到一次调用形态就会被算成一条入边，
    守卫于是对着「合法的文档」报假红。返回只读映射（缓存对象在所有调用者之间共享）。

    `only` = 只关心这些属性名（**白名单**，返回只保证它们的计数完备，别的属性名一律不出现）。
    给了它就能走上**廉价子串预筛**：源码里连这个名字都找不到的文件直接返回空，不解析
    （「入边闭集」那批守卫对全 `rl/` 只问十来个成员名，102 个文件里绝大多数与本判据无关）。
    """
    if only is None:
        return _module_facts(path)[0]
    return _facts_for(path, only)[0]


def top_level_defs(path: str, only: frozenset[str] | None = None) -> frozenset[str]:
    """源码里**会占住名字**的定义：顶层 `def` / `class` + 顶层类体的方法（文档字符串不算）。

    `only` 同 `self_call_counts`：只关心这些名字的定义面（返回的是它们的子集）。
    """
    if only is None:
        return _module_facts(path)[1]
    return _facts_for(path, only)[1]


@cache
def imports(path: str, root: str) -> frozenset[str]:
    """该文件里出现的**完整点分模块名**集合（AST，含函数内的延迟 import）。

    `from rl.log import log` → `rl.log`；`from remote import hub_client` → `remote` 与
    `remote.hub_client` **两条都记**（`root` 下带 `__init__.py` 的子包都会被展开）。

    缓存的是**字符串集合**（派生小结果），不是 AST —— 见模块头「AST 不常驻」的理由。
    同一份文件被分层固定点循环、多个用例反复问时只付一次解析。
    """
    subs = _subpackages(root)
    out: set[str] = set()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            if node.module in subs:  # 仅裸包：`from remote import hub_client`
                out.update(f"{node.module}.{a.name}" for a in node.names if a.name != "*")
    return frozenset(out)
