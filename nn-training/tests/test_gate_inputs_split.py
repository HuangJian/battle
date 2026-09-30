"""拆分的**契约守卫**：课程门求值器的「输入读数面」永住 `biz/gate_inputs.py`（S5 第十五刀，2026-09-27）。

本刀把 1412 行 `biz/gate_check.py` 的输入读数面（15 名）**逐字节**搬进新家：`EvalRow` /
`BudgetInfo` 两个模型 + 行归一化（`_row_from_summary` / `_num` / `normalize_rows`）+
`read_trend_rows` + `first_*` / `count_*` / `sum_*` 事件扫描 + `load_override` 与常量/异常；
判决面 `biz.gate_judges` 从本家取行模型，引擎 `biz.gate_check` 留全量门面（依赖单向：
inputs ← judges ← check）。

本文件钉八件事：① 定义唯一（15 名只许在新家实现）② 引擎**反向留守**（evaluate / main /
GateResult 不动）③ 依赖面闭集（**stdlib-only 叶子**，多一个即红）④ 不得反向 import（仓内
模块零 import）⑤ 门面对象恒等（`is`）⑥ 旧 import 面仍成立（「名字是契约，位置不是」）
⑦ 读数语义（行过滤 / 去重键 / override 响亮报错）⑧ 不进 `remote_dag.LAYERS`（纯逻辑）。

为什么单独成家（独立所有者 + 独立触发条件）：这些读数的触发者是「判决前的一次读数」
（trend 行 / 事件账本 / override 文件），与判决本身（`biz.gate_judges` 的纯计算）和引擎
（`evaluate` 的两趟调度与 CLI）零共享状态；行模型是三者**共用**的输入，归本家才能让
判决面单向依赖它、而不是反向。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import worker.gate_check as gate_check_mod
import worker.gate_inputs as gate_inputs_mod
from tests.helpers import remote_dag as dag

INPUTS_FILE = ROOT / "worker" / "gate_inputs.py"
CHECK_FILE = ROOT / "worker" / "gate_check.py"

#: 本次搬走的**定义**（常量 / 异常 / 类 / 函数）——只许在 `gate_inputs.py` 里出现。
MOVED_NAMES = {
    "BudgetInfo",
    "EvalRow",
    "GateOverrideError",
    "_OVERRIDE_VERDICTS",
    "_TS_FMT",
    "_num",
    "_row_from_summary",
    "count_iteration_events",
    "first_iter_end_ts",
    "first_run_start_ts",
    "load_override",
    "normalize_rows",
    "read_trend_rows",
    "sum_train_samples",
    "sum_train_sec",
}

#: `gate_inputs.py` 允许的 import 面（**stdlib-only 叶子**；多一个即红）。
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "json",
    "pathlib",
    "time",
    "typing",
}

#: 引擎与判决模型**留在** `gate_check.py`（不然这刀搬错了对象）。
ENGINE_SURFACE = {
    "EXIT_CODES",
    "GateResult",
    "RuleReading",
    "VERDICT_PRIORITY",
    "_lazy_config",
    "_notes",
    "_rotation_notes",
    "build_cli",
    "evaluate",
    "main",
}


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


# ───────────────────────── ① 定义唯一 / ② 引擎反向留守 ─────────────────────────


def test_moved_names_are_defined_in_gate_inputs_and_not_redefined_in_gate_check() -> None:
    """定义唯一：搬走的名字只在新家实现（旧家里再定义一遍 = 搬了一半）。"""
    defined_here = _defined(INPUTS_FILE)
    assert defined_here >= MOVED_NAMES, sorted(MOVED_NAMES - defined_here)
    leftovers = MOVED_NAMES & _defined(CHECK_FILE)
    assert leftovers == set(), f"gate_check.py 里仍在实现这些名字：{sorted(leftovers)}"


def test_gate_check_kept_the_engine_surface() -> None:
    """反向：引擎（两趟调度 + ADVANCE 附加条件 + CLI + 判决模型）**留在** `gate_check.py`。"""
    defined = _defined(CHECK_FILE)
    assert defined >= ENGINE_SURFACE, f"gate_check.py 丢了引擎面：{sorted(ENGINE_SURFACE - defined)}"


# ─────────────────────── ③④ 依赖方向：stdlib-only 叶子，无反向边 ───────────────────────


def test_gate_inputs_is_a_stdlib_only_leaf() -> None:
    """输入读数面是**叶子**：只许 stdlib（多一个即红）——连 `biz.log` 都不需要。"""
    extra = sorted(_imports(INPUTS_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"worker/gate_inputs.py 引入了依赖：{extra}"


def test_gate_inputs_never_imports_repo_modules() -> None:
    """★ 本刀的意义：输入面是**底座**，任何仓内 import（尤其 `biz.gate_check` / `biz.gate_judges`）
    都会成环或倒置方向。"""
    back = sorted(m for m in _imports(INPUTS_FILE) if m.split(".")[0] in {"trainer", "remote", "common", "models", "train", "ppo", "data", "scripts"})
    assert back == [], f"worker/gate_inputs.py 反向 import 了仓内模块：{back}"


def test_gate_inputs_stays_pure_logic() -> None:
    """纯逻辑（不达传输面）：账本里它没有任何通往 `remote.*` 的路径。

    2026-09-30（刀 6）：判据从「不在 `remote_dag` 的账本里」改成**可达性**——
    `worker/` 整包入账之后，前者的写法恒为假（哑守卫）。
    """
    assert dag.reaches_transport("worker.gate_inputs") is False


# ───────────────────────── ⑤⑥ 门面恒等 / 旧 import 面 ─────────────────────────


def test_gate_check_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `gate_inputs` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(gate_check_mod, name), f"biz.gate_check 丢了门面 {name}"
        assert getattr(gate_check_mod, name) is getattr(gate_inputs_mod, name), (
            f"biz.gate_check.{name} 不是 biz.gate_inputs.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_gate_check() -> None:
    """名字是契约：旧写法 `from biz.gate_check import EvalRow, load_override` 必须仍然成立。"""
    from worker.gate_check import (
        BudgetInfo,
        EvalRow,
        GateOverrideError,
        load_override,
        read_trend_rows,
    )

    assert EvalRow is gate_inputs_mod.EvalRow
    assert BudgetInfo is gate_inputs_mod.BudgetInfo
    assert GateOverrideError is gate_inputs_mod.GateOverrideError
    assert load_override is gate_inputs_mod.load_override
    assert read_trend_rows is gate_inputs_mod.read_trend_rows


# ───────────────────────── ⑦ 读数语义（逐条钉住） ─────────────────────────


def test_row_from_summary_prefers_anchor_wr_and_drops_bad_rows() -> None:
    """门内唯一胜率入口：`anchor_wr`（锚点轨）优先，旧行回退 `winRate`；无 games 丢弃。"""
    r = gate_inputs_mod._row_from_summary(
        {
            "event": "eval_summary",
            "iter": 5,
            "wver": "w1",
            "games": 200,
            "wins": 40,
            "winRate": 0.2,
            "anchor_wr": 0.7,
            "rotateSeed": 7,
        }
    )
    assert r is not None
    assert r.win_rate == 0.7 and r.seed_fp == "7"
    assert gate_inputs_mod._row_from_summary({"event": "iteration"}) is None
    assert (
        gate_inputs_mod._row_from_summary(
            {"event": "eval_summary", "iter": 5, "wver": "w", "games": 0, "wins": 0}
        )
        is None
    )


def test_normalize_rows_dedupes_by_course_fp_and_wver() -> None:
    """去重键 `(course_fp, wver)`：同键（同 wver）只保留**后**一条——崩溃重放不虚增；
    不同 wver 各留一条、按 `(iter, wver)` 升序；别的课的 fp 行不串门。"""
    rows = [
        {"event": "eval_summary", "iter": 10, "wver": "a", "games": 100, "wins": 50, "course_fp": "fp1"},
        {"event": "eval_summary", "iter": 10, "wver": "a", "games": 100, "wins": 80, "course_fp": "fp1"},
        {"event": "eval_summary", "iter": 20, "wver": "b", "games": 100, "wins": 60, "course_fp": "fp1"},
        {"event": "eval_summary", "iter": 30, "wver": "c", "games": 100, "wins": 90, "course_fp": "fp2"},
        {"event": "iteration", "iter": 1},
    ]
    out = gate_inputs_mod.normalize_rows(rows, "fp1")
    assert [(r.iter, r.wver, r.wins) for r in out] == [(10, "a", 80), (20, "b", 60)]


def test_read_trend_rows_filters_baseline_and_course_fp(tmp_path: Path) -> None:
    """it0 基线不进趋势（除非显式 `include_baseline`）；别的课的 fp 行不串门；缺文件 → ()。"""
    p = tmp_path / "eval_log.jsonl"
    p.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"event": "eval_summary", "iter": 0, "wver": "bc", "games": 200, "wins": 100},
                {"event": "eval_summary", "iter": 10, "wver": "a", "games": 200, "wins": 120, "course_fp": "fp1"},
                {"event": "eval_summary", "iter": 20, "wver": "b", "games": 200, "wins": 130, "course_fp": "fp2"},
                {"event": "iteration", "iter": 1},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    assert [r["iter"] for r in gate_inputs_mod.read_trend_rows(p)] == [10, 20]
    assert [r["iter"] for r in gate_inputs_mod.read_trend_rows(p, course_fp="fp1")] == [10]
    assert [r["iter"] for r in gate_inputs_mod.read_trend_rows(p, include_baseline=True)] == [0, 10, 20]
    assert gate_inputs_mod.read_trend_rows(tmp_path / "missing.jsonl") == ()


def test_load_override_loud_errors(tmp_path: Path) -> None:
    """§4.6：不存在 → None；存在但非法 → **响亮** `GateOverrideError`（绝不静默忽略）。"""
    assert gate_inputs_mod.load_override(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"verdict": "NOPE"}), encoding="utf-8")
    with pytest.raises(gate_inputs_mod.GateOverrideError):
        gate_inputs_mod.load_override(bad)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"verdict": "HOLD"}), encoding="utf-8")
    assert gate_inputs_mod.load_override(ok) == {"verdict": "HOLD"}


def test_event_scanners_basics(tmp_path: Path) -> None:
    """账本扫描：iteration 计数 / 样本通过量 / 训练秒 / run_start 与首迭代时刻；缺文件回落。"""
    p = tmp_path / "training_log.jsonl"
    rows = (
        {"event": "run_start", "time": "2026-09-27 10:00:00"},
        {"event": "iteration", "time": "2026-09-27 10:05:00", "samples": 1000, "epochs": 4, "ppo_cloud_sec": 12.5},
        {"event": "iteration", "time": "2026-09-27 10:09:00", "samples": 2000, "epochs": 4, "ppo_sec": 99.0},
    )
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert gate_inputs_mod.count_iteration_events(p) == 2
    assert gate_inputs_mod.sum_train_samples(p) == 1000 * 4 + 2000 * 4
    assert gate_inputs_mod.sum_train_sec(p) == 12.5 + 99.0  # 云端自报优先，旧行回落 ppo_sec
    assert gate_inputs_mod.first_run_start_ts(p) is not None
    assert gate_inputs_mod.first_iter_end_ts(p) is not None
    missing = tmp_path / "missing.jsonl"
    assert gate_inputs_mod.count_iteration_events(missing) == 0
    assert gate_inputs_mod.sum_train_samples(missing) == 0.0
    assert gate_inputs_mod.sum_train_sec(missing) == 0.0
    assert gate_inputs_mod.first_run_start_ts(missing) is None
