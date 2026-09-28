"""拆分的**契约守卫**：课程门求值器的「判决项面」永住 `rl/gate_judges.py`（S5 第十五刀，2026-09-27）。

本刀把 `_Partial` / `_Ctx` 词汇 + 统计助手（`_mean` / `_slope` / `_sustain` / `_window` /
`_halves`）+ 11 个 kind 判决函数 + `_route_by_completion` + 注册表 `_JUDGES_NO_TEACHER` +
**判决项接口** `_eval_one`（22 名）**逐字节**搬进新家；输入一律来自 `rl.gate_inputs`
（单向：inputs ← judges ← check），引擎 `rl.gate_check.evaluate` 的两趟调度与
`only_kinds=("duty",)` 过滤都经 `_eval_one`。

本文件钉九件事：① 定义唯一 ② 引擎**反向留守**（`evaluate` / 两趟调度留 check）③ 依赖面
闭集（stdlib + `rl.gate_inputs`；`rl.config` 只许 TYPE_CHECKING，运行期零加载）④ 不得反向
import（`rl.gate_check` 不许出现）⑤ 门面对象恒等（`is`）⑥ 判决项接口（注册表键集 +
11 kind 覆盖 + 兜底 dormant）⑦ 运行期零 `rl.config` / torch / numpy（子进程验证）⑧ 不进
`remote_dag.LAYERS` ⑨ 分流判据语义。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.gate_check as gate_check_mod
import rl.gate_inputs as gate_inputs_mod
import rl.gate_judges as gate_judges_mod
from tests.helpers import remote_dag as dag
from tests.subproc_util import run_utf8

JUDGES_FILE = ROOT / "rl" / "gate_judges.py"
CHECK_FILE = ROOT / "rl" / "gate_check.py"

#: 本次搬走的**定义**（常量 / 类 / 函数）——只许在 `gate_judges.py` 里出现。
MOVED_NAMES = {
    "DUTY_MIN_EVENTS",
    "_Ctx",
    "_JUDGES_NO_TEACHER",
    "_Partial",
    "_eval_budget",
    "_eval_course_valid",
    "_eval_cross_course",
    "_eval_dependency",
    "_eval_duty",
    "_eval_hack",
    "_eval_one",
    "_eval_plateau",
    "_eval_skill_floor",
    "_eval_teacher_parity",
    "_eval_wins_mastery",
    "_halves",
    "_mean",
    "_route_by_completion",
    "_slope",
    "_sustain",
    "_window",
    "_wins_mastery_pooled",
}

#: `gate_judges.py` 允许的 import 面（stdlib + 输入面；多一个即红）。
#: `rl.config` 只许 `if TYPE_CHECKING:` 取类型——运行期零加载由本文件子进程用例钉住。
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "math",
    "typing",
    "rl.gate_inputs",
    "rl.config",
}

#: 判决项接口的 kind 覆盖：注册表 6 键 + `_eval_one` 5 分支（skill_floor / plateau /
#: budget / transfer / retention）= 11 种。
REGISTRY_KINDS = {
    "wins_mastery",
    "teacher_parity",
    "course_valid",
    "hack",
    "duty",
    "dependency",
}
BRANCH_KINDS = {"skill_floor", "plateau", "budget", "transfer", "retention"}
ALL_KINDS = REGISTRY_KINDS | BRANCH_KINDS

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
    """全部 import（含函数内延迟 import / TYPE_CHECKING 块）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


# ───────────────────────── ① 定义唯一 / ② 引擎反向留守 ─────────────────────────


def test_moved_names_are_defined_in_gate_judges_and_not_redefined_in_gate_check() -> None:
    """定义唯一：搬走的名字只在新家实现（旧家里再定义一遍 = 搬了一半）。"""
    defined_here = _defined(JUDGES_FILE)
    assert defined_here >= MOVED_NAMES, sorted(MOVED_NAMES - defined_here)
    leftovers = MOVED_NAMES & _defined(CHECK_FILE)
    assert leftovers == set(), f"gate_check.py 里仍在实现这些名字：{sorted(leftovers)}"


def test_gate_check_kept_the_engine_surface() -> None:
    """反向：引擎（两趟调度 + ADVANCE 附加条件 + CLI + 判决模型）**留在** `gate_check.py`。"""
    defined = _defined(CHECK_FILE)
    assert defined >= ENGINE_SURFACE, f"gate_check.py 丢了引擎面：{sorted(ENGINE_SURFACE - defined)}"


# ─────────────────────── ③④ 依赖方向：闭集 + 无反向边 ───────────────────────


def test_gate_judges_import_face_is_closed() -> None:
    """判决面只许 stdlib + `rl.gate_inputs`（`rl.config` 类型例外，见 ALLOWED_IMPORTS 注释）。"""
    extra = sorted(_imports(JUDGES_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"rl/gate_judges.py 引入了依赖：{extra}"


def test_gate_judges_never_imports_the_engine() -> None:
    """★ 本刀的意义：判决面是**底座**，任何 `rl.gate_check` 反向 import（哪怕 TYPE_CHECKING
    块）都会倒置方向（引擎才许 import 判决面）；`rl.` 依赖只许是输入面与 config 类型。"""
    repo = sorted(m for m in _imports(JUDGES_FILE) if m.startswith("rl."))
    assert repo == ["rl.config", "rl.gate_inputs"], repo
    assert "rl.gate_judges" in _imports(CHECK_FILE)  # 门面链在引擎侧，方向如上


def test_gate_judges_stays_pure_logic() -> None:
    """它在分层里是纯逻辑（不达 remote）——不在 `remote_dag` 的传输账本里，也不该进去。"""
    assert "rl.gate_judges" not in dag.LAYERS


# ───────────────────────── ⑤ 门面恒等 ─────────────────────────


def test_gate_check_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `gate_judges` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(gate_check_mod, name), f"rl.gate_check 丢了门面 {name}"
        assert getattr(gate_check_mod, name) is getattr(gate_judges_mod, name), (
            f"rl.gate_check.{name} 不是 rl.gate_judges.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_gate_check() -> None:
    """名字是契约：旧写法 `from rl.gate_check import evaluate, BudgetInfo` 必须仍然成立。"""
    from rl.gate_check import BudgetInfo, evaluate

    assert BudgetInfo is gate_inputs_mod.BudgetInfo  # 门面两段链：check → judges/inputs 都是同一对象
    assert callable(evaluate)


# ───────────────────────── ⑥ 判决项接口（入口唯一 + 覆盖 + 兜底） ─────────────────────────


def test_judge_interface_registry_covers_every_kind() -> None:
    """注册表键集固定；注册表 ∪ `_eval_one` 分支 = 全部 11 种 kind（一个不多一个不少）。"""
    reg = gate_judges_mod._JUDGES_NO_TEACHER
    assert set(reg) == REGISTRY_KINDS
    assert reg["duty"] is gate_judges_mod._eval_duty
    assert reg["wins_mastery"] is gate_judges_mod._eval_wins_mastery
    src = JUDGES_FILE.read_text(encoding="utf-8")
    for branch in sorted(BRANCH_KINDS):
        assert f'"{branch}"' in src, f"_eval_one 分支缺 kind {branch}"
    assert len(ALL_KINDS) == 11


def test_eval_one_is_the_only_entry_and_falls_back_to_dormant() -> None:
    """判决项接口：未启用 ⇒ dormant 短路（不需要 ctx）；未知 kind ⇒ dormant 兜底，不崩训练。"""
    disabled: Any = SimpleNamespace(enabled=False, kind="duty")
    no_ctx: Any = None
    no_spec: Any = None
    p = gate_judges_mod._eval_one(disabled, no_ctx, (), no_spec)
    assert p.dormant is True and p.fired is False and p.completion == 0.0
    unknown: Any = SimpleNamespace(enabled=True, kind="no_such_kind")
    p2 = gate_judges_mod._eval_one(unknown, no_ctx, (), no_spec)
    assert p2.dormant is True and "未实现" in p2.reason


# ───────────────────────── ⑦ 运行期纯净（子进程验证） ─────────────────────────


def test_gate_judges_and_inputs_import_without_heavy_deps() -> None:
    """红线随家：导入判决面 / 输入面后，`rl.config` / torch / numpy 均不入 `sys.modules`。"""
    code = (
        "import sys, rl.gate_inputs, rl.gate_judges; "
        "print('rl.config=' + str('rl.config' in sys.modules)); "
        "print('torch=' + str('torch' in sys.modules)); "
        "print('numpy=' + str('numpy' in sys.modules))"
    )
    out = run_utf8([sys.executable, "-c", code], cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    kv = dict(line.split("=") for line in out.stdout.splitlines() if "=" in line)
    assert kv["rl.config"] == "False"
    assert kv["torch"] == "False"
    assert kv["numpy"] == "False"


# ───────────────────────── ⑨ 分流判据语义 ─────────────────────────


def test_route_by_completion_semantics() -> None:
    """G4/G5 分流：取 `advance_if` 里**最弱一环**的完成度 ≥ `advance_frac` 才 ADVANCE；无
    `advance_if` 保守走 REMEDIATE；`advance_frac` 缺省 1.0。"""
    route = gate_judges_mod._route_by_completion

    def rule(advance_if: tuple[str, ...], frac: float | None) -> Any:
        return SimpleNamespace(advance_if=advance_if, advance_frac=frac)

    assert route(rule((), None), {"a": 1.0}) == "REMEDIATE"
    assert route(rule(("a", "b"), 0.5), {"a": 0.6, "b": 0.5}) == "ADVANCE"
    assert route(rule(("a", "b"), 0.5), {"a": 0.9, "b": 0.4}) == "REMEDIATE"
    assert route(rule(("a",), None), {"a": 0.99}) == "REMEDIATE"
    assert route(rule(("a",), None), {"a": 1.0}) == "ADVANCE"
    assert route(rule(("gone",), 0.5), {}) == "REMEDIATE"  # 缺席按 0.0 计
    assert gate_judges_mod.DUTY_MIN_EVENTS == 2
