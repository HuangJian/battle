"""tests/helpers/run_setup_probe.py —— `RunSetup` 形状守卫用的**结构化探针**（§P0-3 第四刀）。

为什么单独一层：`tests/remote/test_run_setup_spec.py` 要断言的是「谁定义了什么、谁调了谁」——
这正是 `tests/helpers/text_asserts.py` 模块头点名的 **B 类**（架构接线），必须用结构判据。
而口径 B 的已知边界是「读的东西带 `.py` 就计入源码面文本断言」⇒ 把 `inspect.getsource` / AST
留在**生产者**一侧（本文件），判据留在用例一侧断普通值（int / list / set），两边都不冤枉。

刻意**不做**的事：不写断言（生产者只产数据）、不 import `remote` 之外的东西（这里只探形状，
不装配）。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap

#: 三条入口 → 模块（点号名，不碰路径字面量）。
ENTRY_MODULES = {
    "open_run_context": "remote.plan_handoff",
    "run_standalone": "remote.run_loop",
    "run_plan_job": "remote.plan_run",
    "_require_run_identity": "remote.plan_handoff",
}


def _func(module: str, name: str):
    return getattr(importlib.import_module(module), name)


def param_names(module: str, name: str) -> list[str]:
    """函数形参名（按声明顺序，含 `self` 以外的全部）。"""
    return list(inspect.signature(_func(module, name)).parameters)


def _body_tree(module: str, name: str) -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(_func(module, name))))


def called_names(module: str, name: str) -> list[str]:
    """函数体里**被调用**的名字（`ast.Name` 形式的调用；属性调用记成属性名）。"""
    out: list[str] = []
    for node in ast.walk(_body_tree(module, name)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.append(f.id)
            elif isinstance(f, ast.Attribute):
                out.append(f.attr)
    return out


def raise_count(module: str, name: str) -> int:
    """函数体里的 `raise` 条数（身份守卫 = 按缺失种类分几条）。"""
    return sum(1 for node in ast.walk(_body_tree(module, name)) if isinstance(node, ast.Raise))
