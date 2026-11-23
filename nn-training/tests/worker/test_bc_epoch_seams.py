"""BC 两个后端（torch / paddle）的 epoch 循环必须是同一个三接缝形状。

`run_epochs` 只做**编排**：训练循环在 `_train_epoch`、验证在 `_validate`、每 epoch 回调在
`_emit_epoch`。这条判据 2026-11-21 先在 torch 侧落地（engineering §252），2026-11-23 补齐
paddle 双生 —— 两侧一旦哪边把循环塞回去，排障时「同一个 bug 在两个后端长成两种形状」，
而形状门禁（>200 行）对 150 行级别的回填没有感觉。

纯 AST，不 import 任一个后端（本机没有 paddle 也要跑到）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TWINS = {
    "torch": ROOT / "worker" / "train" / "bc.py",
    "paddle": ROOT / "worker" / "train" / "bc_paddle.py",
}
SEAMS = ("run_epochs", "_train_epoch", "_validate", "_emit_epoch")


def _train_class(path: Path) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Train"]
    assert len(hits) == 1, f"{path.name} 里 `_Train` 类数 = {len(hits)}"
    return hits[0]


FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


def _methods(cls: ast.ClassDef) -> dict[str, FuncNode]:
    return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _loops(node: ast.AST) -> list[ast.AST]:
    """`node` 体内的循环语句（不数推导式里的隐式循环）。"""
    return [n for n in ast.walk(node) if isinstance(n, (ast.For, ast.AsyncFor, ast.While))]


def _lines(node: FuncNode) -> int:
    """函数体行数（`end_lineno` 在类型上可空，这里收一次）。"""
    end = node.end_lineno
    assert end is not None, node
    return end - node.lineno + 1


@pytest.mark.parametrize("backend", sorted(TWINS))
def test_the_four_seams_exist(backend: str) -> None:
    methods = _methods(_train_class(TWINS[backend]))
    missing = [name for name in SEAMS if name not in methods]
    assert not missing, f"{backend} 的 `_Train` 缺接缝 {missing}"


@pytest.mark.parametrize("backend", sorted(TWINS))
def test_run_epochs_only_orchestrates(backend: str) -> None:
    """`run_epochs` 里只能有「epoch 循环」这一层；内层训练/验证循环必须已搬走。"""
    run_epochs = _methods(_train_class(TWINS[backend]))["run_epochs"]
    top = _loops(run_epochs)
    assert len(top) == 1, f"{backend}.run_epochs 顶层循环数 = {len(top)}（应只有 epoch 循环）"
    epoch_loop = top[0]
    assert isinstance(epoch_loop, ast.For), f"{backend}.run_epochs 的 epoch 循环不是 `for`"
    nested = [loop for loop in _loops(epoch_loop) if loop is not epoch_loop]
    assert nested == [], f"{backend}.run_epochs 的 epoch 循环内又套了 {len(nested)} 个循环"


@pytest.mark.parametrize("backend", sorted(TWINS))
def test_the_seams_own_the_loops(backend: str) -> None:
    methods = _methods(_train_class(TWINS[backend]))
    for name in ("_train_epoch", "_validate"):
        loops = _loops(methods[name])
        assert len(loops) == 1, f"{backend}.{name} 里的批次循环数 = {len(loops)}"


@pytest.mark.parametrize("backend", sorted(TWINS))
def test_seams_stay_small_enough_to_read(backend: str) -> None:
    """接缝自身不许长回 100 行以上（>200 行的形状门禁对回填太钝）。"""
    methods = _methods(_train_class(TWINS[backend]))
    sizes = {name: _lines(methods[name]) for name in SEAMS}
    fat = {name: size for name, size in sizes.items() if size > 100}
    assert not fat, f"{backend} 的接缝超过 100 行：{fat}"


def test_both_backends_keep_the_same_seams() -> None:
    """接缝名两侧必须一一对应（落地方法各叫各的：`finalize` / `finalize_bc`，不在此列）。"""
    for backend in sorted(TWINS):
        methods = _methods(_train_class(TWINS[backend]))
        assert set(SEAMS) <= set(methods), f"{backend} 缺 {sorted(set(SEAMS) - set(methods))}"
    torch_other = set(_methods(_train_class(TWINS["torch"]))) - set(SEAMS)
    paddle_other = set(_methods(_train_class(TWINS["paddle"]))) - set(SEAMS)
    assert len(torch_other) <= 1 and len(paddle_other) <= 1, (
        f"接缝之外又多出了方法：torch={sorted(torch_other)} paddle={sorted(paddle_other)}"
    )
