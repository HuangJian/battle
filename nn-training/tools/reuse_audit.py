"""tools/reuse_audit.py — **复用账本**（plan/nn-training-reuse-and-deps.plan.md §3-S0）。

量出生产树里三类「同名各写 N 份」，作为重构的**基线**与**只许降**的守卫输入
（守卫住 `tests/test_reuse_ledger.py`）：

1. **逐字节重复的函数体**（跨模块）——去掉 docstring/注释/空白后指纹相同；
2. **结构克隆**——AST 骨架相同（标识符 / 常量擦除）、token 数不同（同 token 数的归入第 1 类）；
3. **同名散落**——同一函数名在 ≥N 个模块里定义（**不是**欠账的充分条件：门面转发 /
   `BaseHTTPRequestHandler` 约定名 / 各模型自己的 `forward` 都是合法的，见 §1.2③）。

用法：

    python tools/reuse_audit.py            # 人读的摘要
    python tools/reuse_audit.py --json     # 机器读（守卫用）

**同一模块内**的克隆不算（模型里重复的前向形状是各自实现）⇒ 第 2 类是**下界**。
`STANDALONE_BOOT_MODULES`（R2 结构性豁免）在 `exempt` 后单独统计：它们的重复是有意的，
不进「欠账」计数，但仍在 `--json` 里留数（防止「豁免」变成隐形橡皮擦）。
"""

from __future__ import annotations

import ast
import collections
import hashlib
import io
import json
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import run_main

ROOT = Path(__file__).resolve().parents[1]

#: 生产树包（测试层 `tests/` · `e2e/` 不在账内：它们各有自己的债本）。
PROD_PACKAGES: tuple[str, ...] = ("common", "biz", "worker", "remote", "hub", "trainer", "tools")

#: 扫描时跳过的目录名（任意一层命中即跳）。
SKIP_PARTS: frozenset[str] = frozenset({"tmp", ".venv", "__pycache__", "ipynb"})

#: R2 结构性豁免：从 GitHub raw 单独拉取、必须在拿到 `code.zip` **之前**可用的引导模块。
#: 它们之间的重复是**有意为之**（`common/__init__.py`「谁不能用本包」）。
STANDALONE_BOOT_MODULES: frozenset[str] = frozenset(
    {
        "remote/tailscale_boot.py",
        "remote/notebook_boot.py",
        "remote/offline_boot.py",
    }
)

#: 内容重复的最小体量（token 数）。**必须 > 1**：`...` / `pass` 占位体（`Protocol` 存根、
#: 抽象方法）在四个模块里天然一样，把「接口声明」计成「重复实现」是纯噪声。
MIN_DUP_TOKENS = 6

#: 结构克隆的最小体量（token 数 < 此值的短函数不计：`return x or y` 这类骨架天然重样）。
MIN_CLONE_TOKENS = 12


@dataclass(frozen=True)
class DupGroup:
    """一组同指纹/同骨架的实现。"""

    names: tuple[str, ...]
    modules: tuple[str, ...]
    lineno: int

    def to_json(self) -> dict:
        return {"names": list(self.names), "modules": list(self.modules), "lineno": self.lineno}


@dataclass
class Report:
    files: int = 0
    functions: int = 0
    exact_groups: list[DupGroup] = field(default_factory=list)
    clone_groups: list[DupGroup] = field(default_factory=list)
    same_name: dict[str, list[str]] = field(default_factory=dict)
    exempt_exact: list[DupGroup] = field(default_factory=list)
    exempt_clones: list[DupGroup] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "files": self.files,
            "functions": self.functions,
            "exact_groups": [g.to_json() for g in self.exact_groups],
            "clone_groups": [g.to_json() for g in self.clone_groups],
            "same_name": self.same_name,
            "counts": {
                "exact_groups": len(self.exact_groups),
                "clone_groups": len(self.clone_groups),
                "same_name_ge3": sum(1 for m in self.same_name.values() if len(m) >= 3),
            },
            "exempt": {
                "exact_groups": len(self.exempt_exact),
                "clone_groups": len(self.exempt_clones),
            },
        }


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _body_without_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """函数体**减去首条 docstring**（只剩 docstring 时用 `pass` 占位）。"""
    body = list(node.body)
    if body and _is_docstring(body[0]):
        body = body[1:]
    return body or [ast.Pass()]


#: 行终止符（顺序即优先级：`\r\n` 必须排在 `\r` 之前，否则 CRLF 会被切成两行）。
_LINE_END_RE = re.compile(r"\r\n|\r|\n")


def _split_lines(src: str) -> list[str]:
    """把源码切成「解析器口径」的行 —— `ast._splitlines_no_ff` 的等价实现。

    为什么自己切（2026-10-11 实测）：`ast.get_source_segment()` **每次调用**都把整份源码
    重切一遍，而指纹是按**每条子语句**取的 ⇒ O(文件大小 × 语句数)。生产树 288 文件 /
    2847 函数实测 **94s**（`_splitlines_no_ff` 占 87s）—— 单个用例预算只有 60s，账本守卫
    必红（forkdist 下更绕：SIGALRM 先打在扫描里，报错被 terminalreporter 的 assert 盖成
    一条难认的 AssertionError）。修法 = 每文件切一次 + 按预切行取片段（`_segment`）。
    等价性由 `tests/test_reuse_ledger.py::test_split_lines_and_segment_match_stdlib` 钉住
    （CRLF / 裸 CR / 换页符 / 非 ASCII 字节偏移对账）。

    与 `str.splitlines()` 的差别：只认 `\n` 与 `\r`（`\r\n` 成对）；**不认** `\f` / `\x85`
    等 —— 它们在 Python 源码里不是换行（`\f` 合法地当空白），照 `splitlines()` 切会把行号切歪。

    实现 = 单条正则 `\r\n|\r|\n` 的 `finditer`（2026-10-30 提速：原先是逐字符 Python 循环，
    占整份扫描 ~1s / 344 文件）。**换行口径一个字节没动**：交替序 `\r\n` 在前保证 CRLF 成对，
    `\f` 等不在式里 ⇒ 照旧不切；等价性仍由 `test_split_lines_and_segment_match_stdlib` 对着
    stdlib 的逐字节对账钉住（CRLF / 裸 CR / 换页符 / 非 ASCII 字节偏移）。
    """
    lines: list[str] = []
    start = 0
    for m in _LINE_END_RE.finditer(src):
        end = m.end()
        lines.append(src[start:end])
        start = end
    if start < len(src):
        lines.append(src[start:])
    return lines


def _segment(lines: list[str], node: ast.AST) -> str:
    """预切行版的 `ast.get_source_segment(..., padded=False)`（缺位置信息 ⇒ `""`）。

    ⚠ 列偏移是 **UTF-8 字节偏移**（与 stdlib 同款：`lines[i].encode()[col:end].decode()`）——
    别改成字符切片，含中文注释/字符串的行会切歪。多行片段按 stdlib 语义拼接：行自身带换行符，
    直接 `""` 连接（不是 `"\n".join`）。
    """
    lineno = getattr(node, "lineno", None)
    col_offset = getattr(node, "col_offset", None)
    end_lineno = getattr(node, "end_lineno", None)
    end_col_offset = getattr(node, "end_col_offset", None)
    if lineno is None or col_offset is None or end_lineno is None or end_col_offset is None:
        return ""  # 重建出来的占位节点（`_body_without_docstring` 的 `Pass`）没有位置
    # getattr 的静态类型是 `Any | None`；上面已排除 None，这里收窄（`no-any-return`）。
    lineno = cast(int, lineno) - 1
    end_lineno = cast(int, end_lineno) - 1
    col_offset = cast(int, col_offset)
    end_col_offset = cast(int, end_col_offset)
    if end_lineno == lineno:
        return lines[lineno].encode()[col_offset:end_col_offset].decode()
    first = lines[lineno].encode()[col_offset:].decode()
    last = lines[end_lineno].encode()[:end_col_offset].decode()
    middle = lines[lineno + 1 : end_lineno]
    return "".join((first, *middle, last))


def _norm_source(node: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> str:
    """去掉 docstring / 注释 / 换行 / 缩进后的内容指纹。

    ⚠ **不要重建 AST 节点**来剔 docstring：新建的节点没有 `lineno`/`col_offset`
    ⇒ 取出的片段是空串 ⇒ 所有函数指纹都成空串、全仓被归为一组
    （本工具初版就是这么错的，由用例 `test_fingerprint_is_not_empty_for_body_functions` 钉住）。
    正确做法：按**子语句**取源码片段（位置信息在原子节点上）。行表由 `_funcs()` 每文件切一次
    后传入 —— **别**改回调 `ast.get_source_segment`（那是 O(文件大小)/语句，见 `_split_lines`）。
    """
    seg = "\n".join(_segment(lines, st) for st in _body_without_docstring(node))
    try:
        toks = tokenize.generate_tokens(io.StringIO(seg).readline)
        keep = [
            t.string
            for t in toks
            if t.type
            not in (
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENCODING,
                tokenize.ENDMARKER,
            )
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return " ".join(seg.split())
    return " ".join(keep)


def _shape(node: ast.AST) -> str:
    """AST 骨架：擦掉标识符名与常量值，只留结构（找结构克隆）。

    ⚠ **原地擦除 + `ast.dump` 同款单趟直书**（2026-10-30 提速，全仓扫描 −2s）：上一版是
    「`ast.walk` 先擦一遍 + 再 `ast.dump` 重走一遍」，同一棵子树被遍历两次（walk 的
    `iter_child_nodes`/`deque` 占 2.2s）；且 `ast.dump` **逐层** `', '.join` 返回串，父层再拼
    一次 ⇒ 拷贝量 ∝ 节点数 × 深度。这里改成一趟递归：**下行时擦、同时把文本追加进平铺列表**，
    最后只 `"".join` 一次。

    输出与 `ast.dump(擦完的树)` **逐字节相同**（默认参数口径：`annotate_fields=True` /
    `include_attributes=False`，缺失字段 `AttributeError` 跳过，非 AST 非 list 走 `repr`）——
    全仓 344 文件 / 3824 函数改前改后逐条对账 0 处不一致（对账脚本：比 `_funcs()` 四元组）；
    分组只看字符串相等，字节相同 ⇒ 分组、计数、基线必然一致，改坏由
    `tests/test_reuse_ledger.py` 的计数基线与合成自证用例当场抓住。

    上一版提速的记事（别回退）：旧旧版先 `ast.parse(ast.unparse(node))` 再擦——`unparse` 2.7s
    + 重解析 1.4s 纯属白付；节点本身只有 `_fields` 参与 dump（`lineno`/`col_offset` 是
    attributes，缺省不带）⇒ 「重解析的新树」与「原树擦完」逐字符等价。

    原地改的代价：调用方必须在取完 `_norm_source`（它按**子语句位置**切源码，且靠
    `Constant.value` 是 `str` 认 docstring）之后才能调本函数 —— 见 `_funcs()` 的两趟写法。
    """
    out: list[str] = []
    _shape_into(node, out)
    return "".join(out)


def _blank_identifiers(node: ast.AST) -> None:
    """原地擦除（与旧版 `ast.walk` 里的六条分支逐字对齐，顺序也一致）。"""
    if isinstance(node, ast.Name):
        node.id = "_"
    elif isinstance(node, ast.Attribute):
        node.attr = "_"
    elif isinstance(node, (ast.arg, ast.keyword)):
        node.arg = "_"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node.name = "_"
    elif isinstance(node, ast.Constant):
        node.value = None
    elif isinstance(node, ast.ClassDef):
        node.name = "_"


def _shape_into(node: ast.AST, out: list[str]) -> None:
    """`ast.dump(node)` 的文本追加进 `out`（**先擦自己，再写字段**）。"""
    _blank_identifiers(node)
    cls = type(node)
    out.append(cls.__name__)
    out.append("(")
    first = True
    for name in node._fields:
        try:
            value = getattr(node, name)
        except AttributeError:  # ast.dump 同款：缺字段跳过
            continue
        # ast.dump 同款：「值为 None 且**类上**的默认值也是 None」的字段不写（`arg.type_comment` /
        # `arguments.vararg` / `Constant.kind` 都靠这条隐掉）——少写/多写会直接改分组基线。
        if value is None and getattr(cls, name, ...) is None:
            continue
        if first:
            first = False
        else:
            out.append(", ")
        out.append(name)
        out.append("=")
        _shape_value(value, out)
    out.append(")")


def _shape_value(value: object, out: list[str]) -> None:
    """字段值 → 文本（三分支与 `ast.dump` 的 `_format` 同构：AST / list / repr）。"""
    if isinstance(value, ast.AST):
        _shape_into(value, out)
    elif isinstance(value, list):
        if not value:
            out.append("[]")
            return
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(", ")
            _shape_value(item, out)
        out.append("]")
    else:
        out.append(repr(value))


def prod_files(root: Path = ROOT) -> list[Path]:
    """生产树里的全部 `.py`（按包枚举，跳过 SKIP_PARTS）。

    ⚠ SKIP 判据用**相对 `root`** 的路径段，不能用绝对路径：本仓的 pytest `tmp_path`
    就落在 `<repo>/tmp/pytest-tmp/…`，绝对路径里带 `tmp` ⇒ 合成源码的自证用例会把整棵树
    跳空（实测踩过）。
    """
    out: list[Path] = []
    for pkg in PROD_PACKAGES:
        base = root / pkg
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if not (SKIP_PARTS & set(p.relative_to(root).parts)):
                out.append(p)
    return sorted(out)


def _funcs(path: Path) -> list[tuple[str, str, str, int]]:
    """→ `[(name, content_fingerprint, shape, lineno)]`（解析失败 ⇒ 空）。"""
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    lines = _split_lines(src)  # 每文件只切一次（逐语句重切 = 94s，见 `_split_lines`）
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # ⚠ **两趟**：`_shape` 原地擦常量（`Constant.value = None`），而 `_norm_source` 靠
    # 「首条语句是 `Constant(str)`」认 docstring、按位置切源码 ⇒ 内容指纹必须先全部取完。
    # 单趟（父函数先擦、子函数后取）会把**嵌套函数**的 docstring 算进指纹（顺序错位）。
    contents = [(n.name, _norm_source(n, lines), n.lineno) for n in nodes]
    out: list[tuple[str, str, str, int]] = []
    for (name, content, lineno), node in zip(contents, nodes, strict=True):
        try:
            shape = _shape(node)
        except Exception:  # 个别语法（新版 match 等）擦不动 ⇒ 不因此丢整份文件
            shape = ""
        out.append((name, content, shape, lineno))
    return out


def scan(root: Path = ROOT) -> Report:
    """扫一遍生产树，产出账本（确定性：同输入同输出）。"""
    rep = Report()
    by_content: dict[str, list[tuple[str, str, int]]] = collections.defaultdict(list)
    by_shape: dict[str, list[tuple[str, str, int, str]]] = collections.defaultdict(list)
    by_name: dict[str, set[str]] = collections.defaultdict(set)
    tok_of: dict[str, int] = {}

    files = prod_files(root)
    rep.files = len(files)
    for f in files:
        rel = f.relative_to(root).as_posix()
        for name, content, shape, line in _funcs(f):
            rep.functions += 1
            by_name[name].add(rel)
            key = hashlib.sha1(content.encode()).hexdigest()  # 去重键，非安全用途
            by_content[key].append((rel, name, line))
            tok_of[key] = len(content.split())
            if shape:
                by_shape[shape].append((rel, name, line, key))

    def _group(rows: list[tuple[str, str, int]]) -> DupGroup:
        return DupGroup(
            names=tuple(sorted({n for _, n, _ in rows})),
            modules=tuple(sorted({m for m, _, _ in rows})),
            lineno=min(ln for _, _, ln in rows),
        )

    for key, rows in by_content.items():
        mods = {m for m, _, _ in rows}
        if len(mods) < 2:
            continue
        if tok_of[key] < MIN_DUP_TOKENS:  # 占位体（`...` / `pass`）不算重复实现
            continue
        (rep.exempt_exact if mods <= STANDALONE_BOOT_MODULES else rep.exact_groups).append(
            _group(rows)
        )

    # ⚠ 变量名不能沿用上面的 `rows`：mypy 会把两处循环的元组元数**合并**，
    # 于是「4 元解包」在 3 元类型上报错（实测：misc / Need more than 3 values to unpack）。
    for shape_rows in by_shape.values():
        mods = {m for m, _, _, _ in shape_rows}
        if len(mods) < 2:
            continue
        # 与第 1 类去重：同一骨架若只对应**一个**内容指纹，那它本就是逐字节重复（已计）。
        if len({k for _, _, _, k in shape_rows}) < 2:
            continue
        if max(tok_of[k] for _, _, _, k in shape_rows) < MIN_CLONE_TOKENS:
            continue
        group = _group([(m, n, ln) for m, n, ln, _ in shape_rows])
        (rep.exempt_clones if mods <= STANDALONE_BOOT_MODULES else rep.clone_groups).append(group)

    rep.same_name = {
        name: sorted(mods) for name, mods in sorted(by_name.items()) if len(mods) >= 2
    }
    rep.exact_groups.sort(key=lambda g: (-len(g.modules), g.names))
    rep.clone_groups.sort(key=lambda g: (-len(g.modules), g.names))
    return rep


def _print_text(rep: Report) -> None:
    c = rep.to_json()["counts"]
    print(f"生产树 {rep.files} 文件 / {rep.functions} 函数定义")
    print(f"逐字节重复组 {c['exact_groups']}（豁免 {len(rep.exempt_exact)}）")
    for g in rep.exact_groups:
        print(f"  x{len(g.modules)} {list(g.names)} {list(g.modules)}")
    print(f"结构克隆组 {c['clone_groups']}（豁免 {len(rep.exempt_clones)}）")
    for g in rep.clone_groups:
        print(f"  x{len(g.modules)} {list(g.names)} {list(g.modules)}")
    print(f"同名 ≥3 模块 {c['same_name_ge3']}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rep = scan()
    if "--json" in args:
        print(json.dumps(rep.to_json(), ensure_ascii=False, indent=2))
    else:
        _print_text(rep)
    return 0


if __name__ == "__main__":
    run_main(main)
