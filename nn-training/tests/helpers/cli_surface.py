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
from dataclasses import dataclass
from functools import cache
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
#: 廉价预筛：四个判据的命中形态都**必然**以这四个标识符之一出现在源码里
#: （`ArgumentParser` 是属性名、`house_parser`/`run_main` 是裸名、`__main__` 是守卫字面量）
#: ⇒ 一个都不出现的文件解析出来四个数都不会变，直接跳过（332 → 79 个文件，见 `_scan`）。
PREFILTER_TOKENS = (PARSER_CTOR, HOUSE_CTOR, HOUSE_EXIT, MAIN_GUARD)


def production_files() -> list[Path]:
    out: list[Path] = []
    for top in TREES:
        out.extend(sorted((ROOT / top).rglob("*.py")))
    return out


def _tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and bool(test.comparators)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == MAIN_GUARD
    )


@dataclass(frozen=True)
class _Scan:
    """四个判据一次扫出来的「小结果」（字符串/整数，GC 友好 —— 不缓存 AST，见 `source_scan` 头部）。"""

    bare_parsers: tuple[str, ...]
    old_exits: tuple[str, ...]
    house_ctors: int
    house_exits: int


@cache
def _scan() -> _Scan:
    """一遍 `ast.walk` 出四个数（2026-10-25）。

    原先是**四条用例各扫一遍** 332 个生产文件（每条 ~0.9s，一个文件 4 个 `pytest`
    用例 = 3.6s）：判据一模一样，只是过滤器不同。这里把「遍历/命中形状」合成一次，
    结果缓存成小对象——每个用例仍然只断言自己那一项（失败指向不变）。

    逐条对应旧实现（语义不得漂）：
      · `bare_parsers`：`<p>.ArgumentParser(...)` 的调用点，**跳过 `common/cli.py` 自己**；
      · `old_exits`：`__main__` 守卫块内（**含嵌套**，如 `try: sys.exit(main())`）的裸
        `main()` / `sys.exit(main())` 表达式语句——只认 `ast.Expr`，作为实参出现的不算；
      · `house_ctors`：`house_parser(...)` 的裸名调用数（**跳过 `common/cli.py`**）；
      · `house_exits`：`run_main(...)` 的裸名调用数（**包含 `common/cli.py`**，与旧版一致）。
    顺序：文件序 → 该文件内 `ast.walk`（BFS）序，与旧版逐项拼接同序。
    """
    bare: list[str] = []
    old: list[str] = []
    ctors = 0
    exits = 0
    for p in production_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        if not any(tok in text for tok in PREFILTER_TOKENS):
            continue
        tree = _tree(text)
        if tree is None:
            continue
        in_house = p == HOUSE_FILE
        rel = _rel(p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                if isinstance(fn, ast.Attribute):
                    if fn.attr == PARSER_CTOR and not in_house:
                        bare.append(f"{rel}:{n.lineno}")
                elif isinstance(fn, ast.Name):
                    if fn.id == HOUSE_CTOR and not in_house:
                        ctors += 1
                    elif fn.id == HOUSE_EXIT:
                        exits += 1
            elif isinstance(n, ast.If) and is_main_guard(n.test):
                # 只认可执行语句（`ast.Expr`）——`run_main(lambda: main(...))` 里作实参的不算。
                for block in n.body:
                    for st in ast.walk(block):
                        if not isinstance(st, ast.Expr) or not isinstance(st.value, ast.Call):
                            continue
                        inner = st.value
                        if isinstance(inner.func, ast.Name) and inner.func.id == MAIN_NAME:
                            old.append(f"裸 main(): {rel}:{st.lineno}")
                        if (
                            isinstance(inner.func, ast.Attribute)
                            and inner.func.attr == EXIT_ATTR
                            and inner.args
                            and isinstance(inner.args[0], ast.Call)
                            and getattr(inner.args[0].func, "id", "") == MAIN_NAME
                        ):
                            old.append(f"sys.exit(main()): {rel}:{st.lineno}")
    return _Scan(tuple(bare), tuple(old), ctors, exits)


def bare_parser_sites() -> list[str]:
    """还有谁在直接构造 `argparse.ArgumentParser`（定义点除外）。"""
    return list(_scan().bare_parsers)


def old_style_exit_sites() -> list[str]:
    """`__main__` 块里还留着裸 `main()` 或 `sys.exit(main())` 的站点（**含 try/if 等嵌套**）。

    嵌套面是实测漏洞：旧版只看 `__main__` 的顶层语句，`hub/tunnel_ab_probe.py` 的
    `try: sys.exit(main())` 整块漏过（2026-10-11 全迁复核补齐）。
    """
    return list(_scan().old_exits)


def house_ctor_calls() -> int:
    return _scan().house_ctors


def house_exit_calls() -> int:
    return _scan().house_exits
