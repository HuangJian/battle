"""拆分的**契约守卫**：wire / 低速重抽 / bulk 节流簇永住 `remote/wire.py`（S4 第四步，2026-09-23）。

`remote/worker.py` 3450 → 3245 行；簇（`WIRE_*` 阈值 + `_WIRE` / `_BEST_RATE` / `_BULK` 状态 +
15 个自由函数）整块搬到 `remote/wire.py`。本文件钉四件事：

1. **定义唯一**——这些名字不许在 `worker.py` 里再实现一遍（否则「搬了一半」）；
2. **无环**——`wire.py` 不得 import `worker.py`（那会把 `remote/` 内部绕成环）；
3. **同一对象**——`worker` 的那些名字必须是 `wire` 的转发（不是副本）；
4. **状态仍是一份账**——`worker._WIRE` 与 `wire._WIRE` 是**同一个 dict**，
   `worker._BULK` 与 `wire._BULK` 是**同一个调度器**（拆成两份是这类重构最隐蔽的故障）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.wire as wire_mod
import remote.worker as worker_mod
from tests.helpers import remote_dag as dag

WIRE_FILE = ROOT / "remote" / "wire.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

#: 本次搬走的**定义**（常量 / 类 / 函数）——只许在 `wire.py` 里出现。
MOVED_NAMES = {
    "WIRE_MIN_RATE",
    "WIRE_PROBE_BYTES",
    "WIRE_PROBE_SEC",
    "WIRE_REROLL_BUDGET_SEC",
    "WIRE_REROLL_MAX",
    "WIRE_RATE_SAMPLE_MIN_BYTES",
    "WIRE_MAX_JOBS",
    "WireSlowError",
    "set_bulk_log",
    "_bulk_pace",
    "_note_rate",
    "_min_rate",
    "_reroll_decision",
    "_wire_bucket",
    "_wire_start",
    "_wire_add",
    "_wire_time",
    "_wire_hit",
    "_wire_note_reroll",
    "_wire_flush",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    """该文件里 `def` / `class` / 顶层赋值定义的**函数名 / 类名 / 变量名**。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_moved_names_are_defined_in_wire_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `wire.py` 里实现（`worker.py` 若又定义一遍 = 搬了一半）。"""
    assert _defined(WIRE_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(WIRE_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_wire_sits_below_its_remote_dependencies() -> None:
    """无环 + 分层：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得 import `remote.worker`」的特指断言；现在是一般化的
    「顶层 intra-remote 边必须严格向下」+「任何 import 不得碰 `rl`」。
    """
    dag.assert_remote_module("remote.wire")


def test_worker_forwards_every_moved_name() -> None:
    """`worker` 的命名空间里每个搬走的名字都在，且函数/类是**同一个对象**。"""
    for name in MOVED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(wire_mod, name), (
            f"remote.worker.{name} 不是 remote.wire.{name}（转发成了副本）"
        )


def test_state_stays_a_single_account_across_both_entry_points() -> None:
    """状态仍是一份账：`_WIRE` 同一个 dict、`_BULK` 同一个调度器（跨两个入口）。"""
    wire_mod._WIRE.clear()
    wire_mod._BULK.reset()
    wire_mod._wire_bucket("j-shared")
    assert worker_mod._WIRE["j-shared"] is wire_mod._WIRE["j-shared"]
    assert worker_mod._BULK is wire_mod._BULK
    wire_mod._WIRE.clear()


def test_wire_module_has_no_additional_mutable_module_state() -> None:
    """`wire.py` 的顶层可变容器只有 `_WIRE`——搬进来的不该顺手多带一份共享状态。"""
    mutable: set[str] = set()
    for node in _tree(WIRE_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == {"_WIRE"}, f"wire.py 顶层多了可变容器：{sorted(mutable - {'_WIRE'})}"


def test_note_rate_rebinding_happens_on_the_wire_module() -> None:
    """注入点口径：只有重绑 `wire._BEST_RATE` 才改变 `_min_rate`——`worker` 的名字是转发。"""
    saved = wire_mod._BEST_RATE
    try:
        worker_mod._BEST_RATE = 999_999.0  # 只改转发名：不该影响判据
        assert wire_mod._min_rate() == wire_mod.WIRE_MIN_RATE
        wire_mod._BEST_RATE = 999_999.0
        assert wire_mod._min_rate() > wire_mod.WIRE_MIN_RATE
    finally:
        wire_mod._BEST_RATE = saved
        worker_mod._BEST_RATE = saved
