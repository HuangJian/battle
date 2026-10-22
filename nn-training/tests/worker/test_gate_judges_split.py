"""拆分的**契约守卫**：课程门求值器的「判决项面」永住 `biz/gate_judges.py`（S5 第十五刀，2026-09-27）。

本刀把 `_Partial` / `_Ctx` 词汇 + 统计助手（`_mean` / `_slope` / `_sustain` / `_window` /
`_halves`）+ 11 个 kind 判决函数 + `_route_by_completion` + 注册表 `_JUDGES_NO_TEACHER` +
**判决项接口** `_eval_one`（22 名）**逐字节**搬进新家；输入一律来自 `biz.gate_inputs`
（单向：inputs ← judges ← check），引擎 `biz.gate_check.evaluate` 的两趟调度与
`only_kinds=("duty",)` 过滤都经 `_eval_one`。

本文件钉九件事：① 定义唯一 ② 引擎**反向留守**（`evaluate` / 两趟调度留 check）③ 依赖面
闭集（stdlib + `biz.gate_inputs`；`biz.config` 只许 TYPE_CHECKING，运行期零加载）④ 不得反向
import（`biz.gate_check` 不许出现）⑤ 门面对象恒等（`is`）⑥ 判决项接口（注册表键集 +
11 kind 覆盖 + 兜底 dormant）⑦ 运行期零 `biz.config` / torch / numpy（子进程验证）⑧ 不进
`remote_dag.LAYERS` ⑨ 分流判据语义。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.gate_check as gate_check_mod
import worker.gate_inputs as gate_inputs_mod
import worker.gate_judges as gate_judges_mod
from tests.helpers import remote_dag as dag
from tests.subproc_util import run_utf8

JUDGES_FILE = ROOT / "worker" / "gate_judges.py"
CHECK_FILE = ROOT / "worker" / "gate_check.py"

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
    "_median",
    "_route_by_completion",
    "_slope",
    "_sustain",
    "_window",
    "_wins_mastery_pooled",
}

#: `gate_judges.py` 允许的 import 面（stdlib + 输入面；多一个即红）。
#: `biz.config` 只许 `if TYPE_CHECKING:` 取类型——运行期零加载由本文件子进程用例钉住。
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "math",
    "typing",
    "worker.gate_inputs",
    "worker.config",
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
#: ⚠ 2026-10-22（DECISIONS §2026-10-22-fixed-offpool-eval-seeds）：`_rotation_notes`（双轨轮转
#: 退化告警）随双轨退场而删 —— 语料不再轮换，「轮转轨形同固定段」这个病灶不存在了。
ENGINE_SURFACE = {
    "EXIT_CODES",
    "GateResult",
    "RuleReading",
    "VERDICT_PRIORITY",
    "_lazy_config",
    "_notes",
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
    """判决面只许 stdlib + `biz.gate_inputs`（`biz.config` 类型例外，见 ALLOWED_IMPORTS 注释）。"""
    extra = sorted(_imports(JUDGES_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"worker/gate_judges.py 引入了依赖：{extra}"


def test_gate_judges_never_imports_the_engine() -> None:
    """★ 本刀的意义：判决面是**底座**，任何 `biz.gate_check` 反向 import（哪怕 TYPE_CHECKING
    块）都会倒置方向（引擎才许 import 判决面）；业务依赖只许是输入面与 config 类型。

    2026-09-30（刀 6）：判决面与它的两个依赖（`gate_inputs` / `config`）都搬进
    `worker/`（本地训练栈）⇒ 这里比的是 `worker.` 前缀，且**只许**这两个；
    `trainer.`（编排）**一处都不许**。
    """
    deps = sorted(m for m in _imports(JUDGES_FILE) if m.startswith("worker."))
    assert deps == ["worker.config", "worker.gate_inputs"], deps
    orch = sorted(m for m in _imports(JUDGES_FILE) if m.startswith("trainer."))
    assert orch == [], f"判决面不得碰编排 trainer：{orch}"
    assert "worker.gate_judges" in _imports(CHECK_FILE)  # 门面链在引擎侧，方向如上


def test_gate_judges_stays_pure_logic() -> None:
    """纯逻辑（不达传输面）：账本里它没有任何通往 `remote.*` 的路径。

    2026-09-30（刀 6）：判据从「不在 `remote_dag` 的账本里」改成**可达性**——
    `worker/` 整包入账之后，前者的写法恒为假（哑守卫）。
    """
    assert dag.reaches_transport("worker.gate_judges") is False


# ───────────────────────── ⑤ 门面恒等 ─────────────────────────


def test_gate_check_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `gate_judges` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(gate_check_mod, name), f"biz.gate_check 丢了门面 {name}"
        assert getattr(gate_check_mod, name) is getattr(gate_judges_mod, name), (
            f"biz.gate_check.{name} 不是 biz.gate_judges.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_gate_check() -> None:
    """名字是契约：旧写法 `from biz.gate_check import evaluate, BudgetInfo` 必须仍然成立。"""
    from worker.gate_check import BudgetInfo, evaluate

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
    """红线随家：导入判决面 / 输入面后，`worker.config` / torch / numpy 均不入 `sys.modules`。"""
    code = (
        "import sys, worker.gate_inputs, worker.gate_judges; "
        "print('worker.config=' + str('worker.config' in sys.modules)); "
        "print('torch=' + str('torch' in sys.modules)); "
        "print('numpy=' + str('numpy' in sys.modules))"
    )
    out = run_utf8([sys.executable, "-c", code], cwd=str(ROOT), timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    kv = dict(line.split("=") for line in out.stdout.splitlines() if "=" in line)
    assert kv["worker.config"] == "False"
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


# ───────────────────────── ⑩ C2 门模板：区间中位统计 ─────────────────────────


def test_median_is_outlier_immune_and_matches_statistics_median() -> None:
    """`_median` 是 C2「区间中位」口径的统计层支撑：对单点跳变免疫，且与
    `statistics.median` 同口径（偶数个取中间两值均值）。

    为什么钉这一条：门一旦压到单点噪声量级（100 局/点下 SE=4.3pp），算术均值会被
    单轮异常拖过门限；中位数不会。本用例用「一个 100 倍的离群值」把两者区分开。
    """
    import statistics

    median = gate_judges_mod._median
    mean = gate_judges_mod._mean

    def row(v: float | None) -> Any:
        return SimpleNamespace(metric=lambda _k: v)

    def rows(*vs: float | None) -> list[Any]:
        return [row(v) for v in vs]

    # 奇数个：正中那个
    assert median(rows(3.0, 1.0, 2.0), "x") == 2.0
    # 偶数个：中间两值均值（与 statistics.median 同口径）
    assert median(rows(4.0, 1.0, 3.0, 2.0), "x") == 2.5
    # 与 statistics.median 逐例对齐
    for vs in ((1.0,), (1.0, 2.0), (5.0, 1.0, 3.0, 2.0, 4.0), (9.0, 9.0, 9.0, 1.0)):
        assert median(rows(*vs), "x") == statistics.median(vs)
    # 缺数据（None）整条剔除，不按 0 计 —— 与 `_mean` 同约
    assert median(rows(1.0, None, 3.0), "x") == 2.0
    assert median(rows(None, None), "x") is None
    assert median([], "x") is None

    # 关键对照：一个离群值把均值拖走一倍，中位数纹丝不动
    clean = rows(1.0, 2.0, 2.0, 2.0, 3.0)
    dirty = rows(1.0, 2.0, 2.0, 2.0, 300.0)
    assert mean(clean, "x") == 2.0
    dirty_mean = mean(dirty, "x")
    assert dirty_mean is not None and dirty_mean > 60.0  # 均值被单点带飞
    assert median(clean, "x") == median(dirty, "x") == 2.0  # 中位数免疫
