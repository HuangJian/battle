"""拆分的**契约守卫**：`TrainingSteps` 的 in-loop 评估链永住 `rl/loop_eval.py`
（S4 第十七刀，2026-09-24）。

## 这一刀切了什么

`rl/loop_steps.py` 940 → 666 行；`TrainingSteps`（845 行 / 20 方法）里**唯一一条真正的方法间
调用链**——8 个成员——搬到 `rl/loop_eval.py::TrainingEval`：

```
_eval_policy_cfg ← _eval_join_soft_sec ← _sweep_eval_tail ← _dispatch_delayed_eval
                                            ↑                        ↑
_join_eval ←────────────────────────────────┘                        │
_eval_covered ← _drain_pending_eval ──────────────────────────────────┘
（另有 `_eval_on_round` 的占位：真实现住 `rl/loop_dispatch.py`，MRO 胜过——S4 第二十刀前住 loop_core）
```

切法是**混入**（与第十四/十五刀同源）：同一把锁、同一个 `self`、**零行为变化**——
`class TrainingSteps(TrainingRemote, TrainingEval)`，组合类与四个「继承真混入」的测试宿主
一行不改。**追加**在既有基类之后而不是插队：两混入零重名、零互调、零 `super()` ⇒ 顺序在
今天是惰性的，没有理由去动已经写在文档与守卫里的 `TrainingRemote` 位置。

## 本文件钉住什么

1. 8 个成员**定义**在新家，`TrainingSteps` 里**零同名定义**（门面只能是门面）；
2. 接线是**对象级同一**（`TrainingSteps.X is TrainingEval.X`），不是同名副本；
3. 组装是**追加**：`__bases__ == (TrainingRemote, TrainingEval)`（组合类的直接基类里没有本簇；
   2026-09-25 S4 第十九刀起那支元组的第四件是 `TrainingLifecycle`——见 test_loop_lifecycle_split）；
4. **状态归属唯一**：五个 eval 槽位只在 `TrainingEval` 声明一处；旧类里那两处跨模块使用
   （`_log_report` 写 `_eval_thread`、`_record_iteration` 读 `_eval_join_sec`）**经继承**可见
   ——它们被逐条写死在 `CROSS_MODULE_HANDS` 里，将来要动必须显式改这张表；
5. 顶层 import 面**闭合**（本模块不许长出重依赖）；DI 目标（`rl.eval_dispatch` / `rl.eval_local`
   / `rl.queue` / `rl.archive`）**只许在方法体内延迟 import**——测试一直 patch 那些实现模块；
6. **★ 两条功能性**：跨模块的流式交棒（`_log_report` → `_join_eval` → `_eval_tail` 落在**同一个
   实例**上）· 占位**响亮失败**（MRO 被改坏时不静默返回 falsy 把 eval 全关掉）。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace

NN_ROOT = Path(__file__).resolve().parent.parent
EVAL_PY = NN_ROOT / "rl" / "loop_eval.py"
STEPS_PY = NN_ROOT / "rl" / "loop_steps.py"
CORE_PY = NN_ROOT / "rl" / "loop_core.py"

#: 这一簇的成员（闭集）：8 个方法 —— 新方法要么住在 `TrainingSteps`，要么改这张表。
CLUSTER = (
    "_eval_on_round",
    "_eval_policy_cfg",
    "_eval_join_soft_sec",
    "_sweep_eval_tail",
    "_join_eval",
    "_dispatch_delayed_eval",
    "_eval_covered",
    "_drain_pending_eval",
)

#: 随簇搬走的五个槽位（声明**只有一处**：`TrainingEval`）。
CLUSTER_SLOTS = (
    "_eval_thread",
    "_eval_gate",
    "_eval_tail",
    "_eval_tail_start",
    "_eval_join_sec",
)

#: 旧类里对这几个槽位的**跨模块使用**（写者/读者留在 `rl/loop_steps.py`，经继承解析）。
CROSS_MODULE_HANDS = (
    ("_log_report", "_eval_thread", "Store"),
    ("_record_iteration", "_eval_join_sec", "Load"),
)

#: 顶层 import 面的闭集（模块名）。
TOP_LEVEL_ALLOWED = {
    "__future__",
    "json",
    "threading",
    "time",
    "pathlib",
    "typing",
    "rl.eval_m1",
    "rl.log",
}

#: DI 目标：只许**方法体内**延迟 import（测试 patch 的是这些实现模块）。
DI_MODULES = ("rl.eval_dispatch", "rl.eval_local", "rl.queue", "rl.archive")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_node(path: Path, cls: str) -> ast.ClassDef:
    return next(
        n for n in ast.walk(_tree(path)) if isinstance(n, ast.ClassDef) and n.name == cls
    )


def _methods(path: Path, cls: str) -> dict[str, ast.FunctionDef]:
    node = _class_node(path, cls)
    return {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}


def _declared(path: Path, cls: str) -> set[str]:
    """类体里**声明**的实例属性名（有注解，无值也算）。"""
    node = _class_node(path, cls)
    out: set[str] = set()
    for m in node.body:
        if isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name):
            out.add(m.target.id)
    return out


def _self_attr_roles(fn: ast.FunctionDef) -> dict[str, set[str]]:
    """方法体里 `self.X` 的读写角色（Store / Load）。"""
    roles: dict[str, set[str]] = {}
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
        ):
            roles.setdefault(sub.attr, set()).add("Store" if isinstance(sub.ctx, ast.Store) else "Load")
    return roles


def _top_level_imports(path: Path) -> set[str]:
    """顶层 import 的模块名（`if TYPE_CHECKING` 里的也算顶层——与 remote_dag 同口径）。"""
    out: set[str] = set()
    for node in _tree(path).body:
        body = node.body if isinstance(node, ast.If) else [node]
        for n in body:
            if isinstance(n, ast.Import):
                out.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                out.add(n.module)
    return out


def _in_function_imports(path: Path) -> set[str]:
    """方法体里（延迟）import 的模块名。"""
    out: set[str] = set()
    for fn in [n for n in ast.walk(_tree(path)) if isinstance(n, ast.FunctionDef)]:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                out.add(sub.module)
            elif isinstance(sub, ast.Import):
                out.update(a.name for a in sub.names)
    return out


# ─────────────────────────────── 定义面 ───────────────────────────────


def test_cluster_is_defined_in_loop_eval_only() -> None:
    """8 个成员定义在 `TrainingEval`；`TrainingSteps` 里**不得**再有同名定义。

    这条挡的是「就地补一个同名方法」——那会让 `TrainingEval` 里那份变成死代码，而测试
    （跑在组合实例上，MRO 取到旧类那份）照样绿。
    """
    in_eval = set(_methods(EVAL_PY, "TrainingEval"))
    in_steps = set(_methods(STEPS_PY, "TrainingSteps"))
    assert set(CLUSTER) <= in_eval, sorted(set(CLUSTER) - in_eval)
    crept_back = sorted(set(CLUSTER) & in_steps)
    assert crept_back == [], f"这些方法又回到 TrainingSteps 了：{crept_back}"
    # 闭集：新家只装这 8 个方法（顺手往里塞 helper 会在这里红）
    assert in_eval == set(CLUSTER), sorted(in_eval ^ set(CLUSTER))


def test_wiring_is_by_object_identity_not_copies() -> None:
    """`TrainingSteps.X is TrainingEval.X`（同一个函数对象，不是同名副本）。"""
    from rl.loop_eval import TrainingEval
    from rl.loop_steps import TrainingSteps

    for name in CLUSTER:
        assert getattr(TrainingSteps, name) is getattr(TrainingEval, name), name


def test_composition_appends_the_new_mixin() -> None:
    """组装是**追加**：`(TrainingRemote, TrainingEval)`；组合类的直接基类里没有本簇。

    2026-09-25（S4 第十九/二十刀）**演进登记**：组合类元组多了第四件 `TrainingLifecycle`
    （主循环骨架——它的入边 `_evalboard_idle` 被两个 sibling 调，只能挂组合根）。本用例钉的
    把心不变：本簇**不是**组合类的直接基类（逐字元组见 `test_loop_lifecycle_split.py`）。

    `_eval_on_round` 那条占位/真实现的顺序契约本刀**也动了家**（真实现从组合根搬到
    `rl/loop_dispatch.py`，仍须早于 `TrainingEval`）——这里只钉「不是同一个对象 + 真实现不在旧家」，
    位置顺序钉在 `test_loop_core_tail_split.py`。
    """
    from rl.loop_core import TrainingLoop
    from rl.loop_eval import TrainingEval
    from rl.loop_guards import TrainingGuards
    from rl.loop_lifecycle import TrainingLifecycle
    from rl.loop_remote import TrainingRemote
    from rl.loop_round_steps import RoundSteps
    from rl.loop_steps import TrainingSteps

    assert TrainingSteps.__bases__ == (TrainingRemote, TrainingEval)
    # 追加（而不是插队）的判据：2026-09-23 写下的 MRO 第 2 位断言逐字仍成立。
    assert TrainingSteps.__mro__[1] is TrainingRemote
    # 组合类与四个「继承真混入」的测试宿主都不必改：本簇**不是**组合类的直接基类。
    assert TrainingLoop.__bases__ == (
        RoundSteps,
        TrainingSteps,
        TrainingGuards,
        TrainingLifecycle,
    )
    assert TrainingEval not in TrainingLoop.__bases__
    # 真实现（S4 第二十刀起住 rl/loop_dispatch，之前住组合根）；占位在 TrainingEval —— MRO 胜过它。
    assert TrainingLoop._eval_on_round is not TrainingEval._eval_on_round
    assert TrainingLoop._eval_on_round.__module__ == "rl.loop_dispatch"
    assert "def _eval_on_round" not in CORE_PY.read_text(encoding="utf-8")


# ─────────────────────────────── 状态面 ───────────────────────────────


def test_eval_slots_are_declared_exactly_once() -> None:
    """五个槽位只在 `TrainingEval` 声明一处；旧类里**不得**再声明（那是重复，不是契约）。"""
    from rl.loop_eval import TrainingEval
    from rl.loop_steps import TrainingSteps

    declared_here = _declared(EVAL_PY, "TrainingEval")
    for slot in CLUSTER_SLOTS:
        assert slot in declared_here, slot
        assert slot in TrainingEval.__annotations__, slot
    left = _declared(STEPS_PY, "TrainingSteps")
    assert sorted(set(CLUSTER_SLOTS) & left) == []
    assert sorted(set(CLUSTER_SLOTS) & set(TrainingSteps.__annotations__)) == []
    # 类级默认值（裸构造的单测脚手架没有 __init__ 赋值，靠的就是它们）
    assert TrainingEval._eval_tail is None
    assert TrainingEval._eval_tail_start is None
    assert TrainingEval._eval_join_sec == 0.0


def test_slots_resolve_through_inheritance_on_a_bare_instance() -> None:
    """裸 `TrainingSteps()` 上五个槽位都在（继承解析）——单测脚手架依赖这条。"""
    from rl.loop_eval import TrainingEval
    from rl.loop_steps import TrainingSteps

    for slot in CLUSTER_SLOTS:
        assert slot in TrainingEval.__annotations__, slot
    assert TrainingEval in TrainingSteps.__mro__
    assert TrainingSteps()._eval_join_sec == 0.0
    assert TrainingSteps()._eval_tail is None


def test_cross_module_hands_are_the_two_declared_ones() -> None:
    """旧类里对 eval 槽位的**跨模块使用**恰是申报的两处，且方向对得上。

    「申报」不是形式：这两处替换成 `getattr` 或搬到新家都得显式改这张表——否则状态归属
    会悄悄裂成「谁都能写」，而 `_eval_thread` 被覆盖正是 R4（jsonl 写回前 join）失效的样子。
    """
    found: set[tuple[str, str, str]] = set()
    for fn in _methods(STEPS_PY, "TrainingSteps").values():
        for attr, roles in _self_attr_roles(fn).items():
            if attr in CLUSTER_SLOTS:
                for role in roles:
                    found.add((fn.name, attr, role))
    assert found == set(CROSS_MODULE_HANDS), sorted(found ^ set(CROSS_MODULE_HANDS))


# ─────────────────────────────── 依赖面 ───────────────────────────────


def test_top_level_import_surface_is_closed() -> None:
    """顶层 import 面闭合：本模块不许长出重依赖（重依赖留在调用方或延迟 import）。"""
    got = _top_level_imports(EVAL_PY)
    assert got <= TOP_LEVEL_ALLOWED, sorted(got - TOP_LEVEL_ALLOWED)


def test_di_targets_are_lazy_only() -> None:
    """DI 目标只许在方法体内延迟 import（测试 patch 的是那些实现模块）。"""
    top = _top_level_imports(EVAL_PY)
    inside = _in_function_imports(EVAL_PY)
    for mod in DI_MODULES:
        assert mod not in top, f"{mod} 被提到顶层了——测试的 patch 目标会漂"
    # 至少 `dispatch_eval_bg` 的宿主（派发的唯一入口）必须在方法体里拿到
    assert "rl.eval_dispatch" in inside and "rl.eval_local" in inside


def test_loop_eval_does_not_import_the_facade_or_core() -> None:
    """不得成环：新家不许 import `rl.loop_steps`（门面）也不许 import `rl.loop_core`。"""
    got = _top_level_imports(EVAL_PY) | _in_function_imports(EVAL_PY)
    assert "rl.loop_steps" not in got
    assert "rl.loop_core" not in got


# ─────────────────────────────── 功能性 ───────────────────────────────


class _AliveThread:
    """假线程：报告自己还活着（交棒路径要它）。"""

    def __init__(self) -> None:
        self.joins: list[float | None] = []

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        self.joins.append(timeout)


def test_stream_report_thread_hands_off_across_the_module_boundary() -> None:
    """★ 跨模块接线：写者住 `rl/loop_steps.py`，读者住 `rl/loop_eval.py`，落在同一个实例上。

    `_log_report`（旧类）把 stream 报告里的 eval 线程句柄 pop 进 `_eval_thread`；
    `_join_eval`（新家）读到它、放行本机份额并把没跑完的尾巴交棒给下一轮 rollout 边界。
    这一步走了「继承 + 混入组装」，是本刀最需要被钉住的一条真实链路。
    """
    from rl.loop_steps import TrainingSteps

    ts = TrainingSteps()
    thread = _AliveThread()
    ts._stream_meta = {"stream": True}
    ts._report = {
        "_eval_thread": thread,
        "_stream": {
            "rollout_sec": 1.0,
            "ppo_sec": 2.0,
            "steps": 10,
            "chunks": 2,
            "agg": {"kl": 0.0},
        },
        "games": 4,
        "winRate": 0.5,
    }
    ts.args = SimpleNamespace(mode="per-tick", eval_window_sec=60)
    # 软等关掉（policy.evalJoinSoftSec=0）：交棒路径上不该站着等任何固定秒数。
    ts._last_dist_cfg = {"policy": {"evalJoinSoftSec": 0}}
    # `_eval_gate` 是**纯注解**（无类级默认，随簇搬来时逐字保留原样）：真实组合实例上由
    # `TrainingLoop.__init__` 赋值，裸 mixin 脚手架要自己给——与 tests/test_eval_timing.py 同款。
    ts._eval_gate = None

    ts._log_report(5, time.time())  # 写：rl/loop_steps.py::_log_report
    assert ts._eval_thread is thread, "stream 报告的线程句柄没落进 _eval_thread"
    ts._join_eval(5)  # 读：rl/loop_eval.py::_join_eval

    assert ts._eval_tail is not None, "没跑完的尾巴应交棒给下一轮 rollout 边界"
    assert ts._eval_tail[0] is thread
    assert thread.joins == [], f"缺省不该站等：{thread.joins}"
    assert ts._eval_tail_start is None, "交棒后时间基准复位"


def test_eval_on_round_placeholder_fails_loudly() -> None:
    """★ 占位用 `raise` 而不是 `...`：MRO 被改坏时**响亮失败**。

    静默返回 falsy 会把 eval 全关掉，而账本上只会看到「这几轮没评估」——没有任何报错。
    """
    import pytest

    from rl.loop_eval import TrainingEval
    from rl.loop_steps import TrainingSteps

    ts = TrainingSteps()
    with pytest.raises(NotImplementedError) as ei:
        ts._eval_on_round(6)
    assert "MRO" in str(ei.value)
    assert "TrainingEval" in str(ei.value), "文案要点名新家（旧文案写死 TrainingSteps）"
    assert TrainingEval._eval_on_round.__module__ == "rl.loop_eval"
