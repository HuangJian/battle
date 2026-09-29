"""拆分的**契约守卫**：逐局 eval 行 schema + 账本 I/O 永住 `rl/eval_rows.py`（S5 第一刀，2026-09-27）。

`rl/eval_local.py` **1130 → 870 行**；簇（连续行 287–602 共 17 个名：三个字段抽取器 + `eval_row` +
账本读/并/去重）整块搬到 `rl/eval_rows.py`（345 行）。本文件钉五件事：

1. **定义唯一**——这些名字不许在 `eval_local.py` 里再实现一遍（否则「搬了一半」）；
2. **无环 / 分层**——`eval_rows.py` 只依赖 stdlib，且**不得** import `rl.eval_local`（反向边 = 环）；
3. **同一对象**——`eval_local` 的那些名字必须是 `eval_rows` 的转发（不是副本）；
4. **搬走的账本语义没变**——`merge_eval_rows` 的逐局去重 + summary 单调仍在（功能性用例）；
5. **remote 侧的耦合边**（同日续）——只用纯行/账本原语的两处（`hub/queue_resume` / `deliver_zip`）
   必须 `import rl.eval_rows` 而非 `rl.eval_local`；`offline_eval` 的 `eval_row` 走纯模块，而它对运行器的
   依赖（`run_local_eval_game` / `settle_eval_summary`）**保留**（合法）。

为什么单独成家：`remote/` 侧（`hub/queue_resume` 补传合并 / `deliver_zip` 产物导入 /
`offline_eval` 行构造）本来要 `import rl.eval_local` 才拿得到这些**纯行/账本**原语——那是「传输层
伸手进本机评估运行器」的语义错位。独立之后，纯数据模块可被任何一侧 import 而不拖入运行器。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.eval_local as eval_local_mod
import rl.eval_rows as eval_rows_mod
from tests.helpers import remote_dag as dag

ROWS_FILE = ROOT / "rl" / "eval_rows.py"
LOCAL_FILE = ROOT / "rl" / "eval_local.py"

#: 本次搬走的**定义**（常量 / 函数）——只许在 `eval_rows.py` 里出现。
MOVED_NAMES = {
    "EVAL_CENSUS_KEYS",
    "EVAL_LOOT_KEYS",
    "EVAL_V8_KEYS",
    "EVAL_V9_KEYS",
    "_read_ledger_rows",
    "_summary_games",
    "append_eval_rows",
    "append_eval_summaries",
    "eval_census_fields",
    "eval_loot_fields",
    "eval_row",
    "eval_row_key",
    "eval_row_keys",
    "eval_summary_key",
    "eval_v8_fields",
    "eval_v9_fields",
    "merge_eval_rows",
    "read_eval_rows",
    "read_eval_summary_rows",
}

#: `eval_rows.py` 允许的 import 面（stdlib-only；多一个即红）。
ALLOWED_IMPORTS = {"__future__", "json", "time", "pathlib"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    """该文件里 `def` / `class` / 顶层赋值定义的函数名 / 类名 / 变量名。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imports(path: Path) -> set[str]:
    """全部 import（含函数内延迟 import）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _imported_from(path: Path, module: str) -> set[str]:
    """从指定模块 import 的**名字**集合（含函数内延迟 import）。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.update(a.name for a in node.names)
    return out


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_moved_names_are_defined_in_eval_rows_and_not_redefined_in_eval_local() -> None:
    """定义唯一：搬走的名字只在 `eval_rows.py` 里实现（`eval_local.py` 再定义一遍 = 搬了一半）。"""
    assert _defined(ROWS_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(ROWS_FILE))
    leftovers = MOVED_NAMES & _defined(LOCAL_FILE)
    assert leftovers == set(), f"eval_local.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


# ─────────────────────── ② 依赖方向：stdlib-only，无环 ───────────────────────


def test_eval_rows_imports_only_stdlib() -> None:
    """纯数据模块：不得 import `rl.*` / `remote.*` / torch / numpy（多一个即深层耦合）。"""
    imported = {m.split(".")[0] for m in _imports(ROWS_FILE)}
    extra = sorted(imported - ALLOWED_IMPORTS)
    assert extra == [], f"rl/eval_rows.py 引入了 stdlib 之外的依赖：{extra}"


def test_eval_rows_never_imports_eval_local() -> None:
    """★ 本刀的意义：`eval_rows` 是**底座**，反向 import 运行器立刻成环。"""
    back = sorted(m for m in _imports(ROWS_FILE) if m.startswith("rl.eval_local"))
    assert back == [], f"rl/eval_rows.py 反向 import 了运行器：{back}"


def test_eval_rows_stays_pure_logic() -> None:
    """它在分层里是 L1 纯逻辑（不达 remote）——不在 `remote_dag` 的传输账本里，也不该进去。"""
    assert "rl.eval_rows" not in dag.LAYERS


# ───────────────────────── ③ 门面 ─────────────────────────


def test_eval_local_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `eval_rows` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(eval_local_mod, name), f"rl.eval_local 丢了门面 {name}"
        assert getattr(eval_local_mod, name) is getattr(eval_rows_mod, name), (
            f"rl.eval_local.{name} 不是 rl.eval_rows.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_eval_local() -> None:
    """名字是契约：旧的 `from rl.eval_local import eval_row`（多处在用）必须仍然成立。"""
    from rl.eval_local import eval_row, eval_row_key, merge_eval_rows

    assert eval_row is eval_rows_mod.eval_row
    assert eval_row_key is eval_rows_mod.eval_row_key
    assert merge_eval_rows is eval_rows_mod.merge_eval_rows


# ─────────────────────── ④ 账本语义没变（功能性） ───────────────────────


def _manifest(stage: int, seed: int, win: bool = True) -> dict:
    return {
        "win": win,
        "cleared": win,
        "outcome": "cleared" if win else "death",
        "ticks": 120,
        "stage": stage,
        "seed": seed,
        "dims": {},
    }


def test_eval_row_builds_a_dedupable_ledger_row() -> None:
    """行构造 + 去重键同类：同 (iter,wver,stage,seed) 的行按同一键去重。"""
    row = eval_rows_mod.eval_row(
        _manifest(3, 860001), it=5, key16="a" * 16, task=(3, 860001), node="self"
    )
    assert row["event"] == "eval" and row["iter"] == 5 and row["stage"] == 3
    assert eval_rows_mod.eval_row_key(row) == (5, "a" * 16, 3, 860001)
    # node 不进键：云机与节点各跑一次是同一份读数
    other = eval_rows_mod.eval_row(
        _manifest(3, 860001), it=5, key16="a" * 16, task=(3, 860001), node="cloud"
    )
    assert eval_rows_mod.eval_row_key(other) == eval_rows_mod.eval_row_key(row)


# ─────────────── ⑤ remote 侧的耦合边（S5 第二刀：改指纯模块，消掉运行器依赖） ───────────────

#: `remote/` 里**只**用纯行/账本原语、因此必须 import `rl.eval_rows` 的调用点。
REMOTE_PURE_CALLERS = {
    "remote/hub/queue_resume.py": {"append_eval_rows", "append_eval_summaries"},
    "remote/deliver_zip.py": {"merge_eval_rows"},
}


def test_remote_pure_ledger_callers_use_eval_rows_not_eval_local() -> None:
    """★ 本刀的目的：这两处只为「纯行/账本原语」而 import —— 现在指向 `rl.eval_rows`。

    否则「传输层伸手进运行器」的边会悄悄长回来（改名测试不会红，只有这条会）。
    """
    for rel, names in REMOTE_PURE_CALLERS.items():
        path = ROOT / rel
        from_local = _imported_from(path, "rl.eval_local")
        assert not (from_local & names), (
            f"{rel} 仍从 rl.eval_local 取纯行/账本原语 {sorted(from_local & names)}"
            "（应 import rl.eval_rows）"
        )
        from_rows = _imported_from(path, "rl.eval_rows")
        assert names <= from_rows, (
            f"{rel} 没从 rl.eval_rows 取 {sorted(names - from_rows)}"
        )


def test_offline_eval_takes_eval_row_from_eval_rows_but_keeps_the_runner() -> None:
    """`remote/offline_eval.py` 是第三种：它**确实要**运行器（`run_local_eval_game` /
    `settle_eval_summary`）——那条边是**合法**的，本刀不动；只有 `eval_row`（纯 schema）改指纯模块。
    """
    path = ROOT / "remote" / "offline_eval.py"
    assert "eval_row" in _imported_from(path, "rl.eval_rows"), "eval_row 没走 rl.eval_rows"
    assert "eval_row" not in _imported_from(path, "rl.eval_local"), "eval_row 仍从 rl.eval_local 取"
    assert {"run_local_eval_game", "settle_eval_summary"} & _imported_from(path, "rl.eval_local"), (
        "offline_eval 不再 import 运行器了（这条边本应保留）——是否搬错了？"
    )


def test_merge_eval_rows_dedupes_games_and_keeps_monotonic_summaries(tmp_path: Path) -> None:
    """★ 搬走的账本语义：逐局按 `(iter,wver,stage,seed)` 去重；summary 单调不缩。"""
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "eval_log.jsonl"
    rows = [
        eval_rows_mod.eval_row(
            _manifest(3, 860001), it=5, key16="a" * 16, task=(3, 860001), node="cloud"
        ),
        {"event": "eval_summary", "iter": 5, "wver": "a" * 16, "games": 100, "wins": 10},
    ]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    games, sums = eval_rows_mod.merge_eval_rows(src, dst)
    assert (games, sums) == (1, 1)
    # 再并一次：逐局被去重吞掉，summary 不缩（单调）⇒ 一行不写。
    assert eval_rows_mod.merge_eval_rows(src, dst) == (0, 0)
    assert len(eval_rows_mod.read_eval_rows(dst)) == 1
