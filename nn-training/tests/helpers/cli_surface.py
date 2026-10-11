"""tests/helpers/cli_surface.py —— `common/cli` 全迁的**扫描器**（判据住 `tests/test_cli_house.py`）。

**为什么单独成文件**：`tests/test_source_text_assert_budget.py` 的口径 B 会把「断言表达式引用了
**本文件里**读 `.py` 源码绑出来的名字」计成本文断言 —— 与「源码长什么样 = 行为对」无关的东西，
不该在那条基线上记一笔。把扫描搬进 helper 之后，用例只引用**跨模块调用结果**，判据不变、
基线不动（同 `plan/nn-training-test-debt-cleanup.plan.md` §2-T5 的口径纪律）。

扫描面 = 生产树 `common` `biz` `worker` `remote` `hub` `trainer` `tools`（`tests/` `e2e/` 不在内：
那边自带测试替身的 parser 是合法的）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("common", "biz", "worker", "remote", "hub", "trainer", "tools")
#: 定义点自己（`house_parser` 当然要构造真 parser）
HOUSE_FILE = ROOT / "common" / "cli.py"
PARSER_CTOR = "ArgumentParser"
HOUSE_CTOR = "house_parser"
HOUSE_EXIT = "run_main"
MAIN_NAME = "main"
MAIN_GUARD = "__main__"
EXIT_ATTR = "exit"


def production_files() -> list[Path]:
    out: list[Path] = []
    for top in TREES:
        out.extend(sorted((ROOT / top).rglob("*.py")))
    return out


def _tree(p: Path) -> ast.Module | None:
    try:
        return ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _name_calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and bool(test.comparators)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == MAIN_GUARD
    )


def bare_parser_sites() -> list[str]:
    """还有谁在直接构造 `argparse.ArgumentParser`（定义点除外）。"""
    out: list[str] = []
    for p in production_files():
        if p == HOUSE_FILE:
            continue
        tree = _tree(p)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == PARSER_CTOR
            ):
                out.append(f"{_rel(p)}:{n.lineno}")
    return out


def old_style_exit_sites() -> list[str]:
    """`__main__` 块里还留着裸 `main()` 或 `sys.exit(main())` 的站点（**含 try/if 等嵌套**）。

    嵌套面是实测漏洞：旧版只看 `__main__` 的顶层语句，`hub/tunnel_ab_probe.py` 的
    `try: sys.exit(main())` 整块漏过（2026-10-11 全迁复核补齐）。
    """
    out: list[str] = []
    for p in production_files():
        tree = _tree(p)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.If) or not is_main_guard(n.test):
                continue
            # walk 到块内所有节点；只认 **表达式语句**（`ast.Expr`），这样
            # `run_main(lambda: main(...))` 里作为实参出现的 main(...) 不会被误报。
            for st in (node for block in n.body for node in ast.walk(block)):
                if not isinstance(st, ast.Expr) or not isinstance(st.value, ast.Call):
                    continue
                fn = st.value.func
                if isinstance(fn, ast.Name) and fn.id == MAIN_NAME:
                    out.append(f"裸 main(): {_rel(p)}:{st.lineno}")
                if (
                    isinstance(fn, ast.Attribute)
                    and fn.attr == EXIT_ATTR
                    and st.value.args
                    and isinstance(st.value.args[0], ast.Call)
                    and getattr(st.value.args[0].func, "id", "") == MAIN_NAME
                ):
                    out.append(f"sys.exit(main()): {_rel(p)}:{st.lineno}")
    return out


def house_ctor_calls() -> int:
    return sum(
        len(_name_calls(_tree(p) or ast.Module(body=[], type_ignores=[]), HOUSE_CTOR))
        for p in production_files()
        if p != HOUSE_FILE
    )


def house_exit_calls() -> int:
    return sum(
        len(_name_calls(_tree(p) or ast.Module(body=[], type_ignores=[]), HOUSE_EXIT))
        for p in production_files()
    )
