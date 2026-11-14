"""G7：`common/**` 不得用模块级 `assert` 做运行时校验。

`python -O` 会把模块级 assert 整段删掉，而 schema 的布局不变量是“张量布局与 TS 侧
逐字一致”的正确性根基。这里把所有不在函数/类作用域里的 assert 都拦下；函数体内的
调试期 assert 不在本门禁口径内（P1-9 的“热路径保留但注明”）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from common import schema

ROOT = Path(__file__).resolve().parents[1]


class _ModuleAsserts(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope = 0
        self.hits: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    def visit_Assert(self, node: ast.Assert) -> None:
        if self.scope == 0:
            self.hits.append(node.lineno)


def test_common_has_no_module_level_asserts() -> None:
    bad: list[str] = []
    for p in (ROOT / "common").rglob("*.py"):
        v = _ModuleAsserts()
        v.visit(ast.parse(p.read_text(encoding="utf-8", errors="replace")))
        bad.extend(f"{p.relative_to(ROOT).as_posix()}:{ln}" for ln in v.hits)
    assert not bad, "common 模块级 assert 在 python -O 下会消失：\n  " + "\n  ".join(bad)


def test_schema_layout_check_survives_optimization() -> None:
    """显式 raise 与 assert 不同：不依赖解释器优化级别。"""
    with pytest.raises(RuntimeError, match="expected 2 entries"):
        schema._check_layout("probe", [(0, "only")], 2)
    with pytest.raises(RuntimeError, match=r"indices must be 0\.\.1"):
        schema._check_layout("probe", [(0, "a"), (0, "b")], 2)
