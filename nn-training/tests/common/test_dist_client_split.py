"""B8 第十一刀的**接口守卫**：取任务 / 权重传输已搬出 `common/distribution.py`。

搬出去不等于放生。两簇此后必须**经漏斗出站**——`distribution.node_request(...)`（属性查找），
不是 `from common.distribution import node_request`（那样打桩 `common.distribution.node_request`
对它们就变成静默空操作，本仓已经因此踩过三次）。20+ 条「零 socket」用例与收工断连的
`abort_active_events` 判据都押在这条缝上，所以它必须可机械判定，而不是写在 docstring 里。

三件事：① 搬走的名字不许在旧家再实现一遍（改回来 = 又变成一千行的杂物间）；
② 旧家不许再 re-export 它们（`X as X` 门面会让旧打桩点「看起来还在」而实际不生效）；
③ 新家的每一次漏斗调用都必须是属性形态（AST，不按文本）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "common" / "distribution.py"
CLIENTS = {
    "task_client": ROOT / "common" / "task_client.py",
    "weights_client": ROOT / "common" / "weights_client.py",
}

#: 搬走的公开名（每个新家挑代表 + 那两簇的核心入口）。
MOVED = {
    "fetch_task",
    "cancel_task",
    "validate_eval_result",
    "check_node_capabilities",
    "post_weights",
    "post_weights_parallel",
    "refresh_weights",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def test_moved_names_are_not_redefined_in_the_old_home() -> None:
    leftovers = MOVED & _defined(_tree(DIST))
    assert not leftovers, f"common/distribution.py 又实现了搬走的名字：{sorted(leftovers)}"


def test_the_old_home_does_not_re_export_the_moved_names() -> None:
    """旧家的 `X as X` 转发会让打桩 `common.distribution.X` 看起来仍有效（实际空操作）。"""
    exported: set[str] = set()
    for node in ast.walk(_tree(DIST)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("common."):
            exported.update(a.asname for a in node.names if a.asname and a.name == a.asname)
    assert not (MOVED & exported), f"common/distribution.py 又转发了：{sorted(MOVED & exported)}"


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_clients_reach_the_funnel_by_attribute(name: str) -> None:
    tree = _tree(CLIENTS[name])
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func).endswith("node_request")
    }
    assert calls == {"distribution.node_request"}, (
        f"{name}.py 的出站形态变了（须为 `distribution.node_request(...)`，"
        "`from … import node_request` 会让 `common.distribution.node_request` 打桩变成空操作）"
    )
