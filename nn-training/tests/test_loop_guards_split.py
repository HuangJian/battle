"""S23 契约（2026-09-25）—— `rl/loop_guards.py` 按判据同源切成四簇后不得腐烂。

S4 第二十三刀把 `TrainingGuards` 的 13 个成员按**判据同源**切成四簇——每一簇是「同一件事
的一条轴」，不是按大小或物理位置切（`_rotate_cleanup` 只有 52 行却独立成簇，302 行的
`_gate` 一簇也不拆）：

| 混入 | 判据 | 模块 | 成员 |
|---|---|---|---|
| `TrainingGuardsTrip` | **过程面**硬边界（更新健康度 / 评估显著度，连击式） | `rl/loop_guards_trip.py` | `_breaker` · `_stop_loss` |
| `TrainingGuardsLeg` | **结果面**停腿（退回了吗 / 比对照臂差吗） | `rl/loop_guards_leg.py` | `_kickstart_burn` · `_paired_kill` |
| `TrainingGuardsGate` | **课程结束门一整族**（求值 → 判决落地 → 预算硬断） | `rl/loop_guards_gate.py` | `_gate` · `_apply_verdict` · `_warn_min_train_unreachable` · `_budget_hard_cut` |
| `TrainingGuardsSweep` | **轮级磁盘回收** | `rl/loop_guards_sweep.py` | `_rotate_cleanup` |

DAG 与 S22（一条连通分量）**不同**：这是一个**多 sink 的 DAG**——`_ledger_apply` 被 3 簇调、
`_sync_cloud_halt` 被 2 簇 + 外部 `loop_lifecycle.finish_course` 调。按「入边来自多个 sibling
⇒ 锁进组合根」的既有规则（第十九刀），**提供者留根、调用者出包**：

| 留根（`rl/loop_guards.py::TrainingGuards`） | 判据 |
|---|---|
| `_ledger_apply` | 事件 → 账本视图增量（3 簇共用） |
| `_sync_cloud_halt` · `_is_soft_verdict` · `_gate_halt_mode` + 三个判决词表常量 | 判决 → 云机达令（2 簇共用 + 外部） |

★ 二者留根还有一条**硬理由**：它们是 `rl.loop_guards` 的 **patch 锚点**——`set_cloud_halt` /
`dist_common` 被四个测试文件以 `monkeypatch.setattr("rl.loop_guards.…", …, raising=True)`
打桩；搬走会让桩**静静失效**（本文件 `test_patch_anchor_stays_in_the_root` 正面钉住）。

本文件钉住这条切分不腐烂的若干面：定义面唯一（**旧家零残留**）· 对象恒等 · 组装逐字 ·
**借用声明闭集 = 派生集** · 入边 / 出边闭集 · 停云机态的**唯一写手** · 顶层 / 延迟 import
闭集 · 禁反向边 · **patch 面归属**（锚点在根、新家不得持有）· **五条功能性**（四簇各跑一次
真判 + patch 锚点真拦截）。既有语义回归仍由 `test_kickstart_plan.py` / `test_paired_kill.py` /
`test_loop_gate_{nopark,soft_remediate}.py` / `test_loop_park.py` 承担（那些文件一行不改）。
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

RL = NN_ROOT / "rl"

#: 成员 → (文件, 类)。搬走的 9 个，一个不漏。
HOMES: dict[str, tuple[str, str]] = {
    "_breaker": ("loop_guards_trip.py", "TrainingGuardsTrip"),
    "_stop_loss": ("loop_guards_trip.py", "TrainingGuardsTrip"),
    "_kickstart_burn": ("loop_guards_leg.py", "TrainingGuardsLeg"),
    "_paired_kill": ("loop_guards_leg.py", "TrainingGuardsLeg"),
    "_gate": ("loop_guards_gate.py", "TrainingGuardsGate"),
    "_warn_min_train_unreachable": ("loop_guards_gate.py", "TrainingGuardsGate"),
    "_apply_verdict": ("loop_guards_gate.py", "TrainingGuardsGate"),
    "_budget_hard_cut": ("loop_guards_gate.py", "TrainingGuardsGate"),
    "_rotate_cleanup": ("loop_guards_sweep.py", "TrainingGuardsSweep"),
}
MEMBERS = tuple(HOMES)

#: 留根四人 + 三个判决词表常量（常量是**类属性**，不是模块级名——`rl.loop_lifecycle` 与
#: `test_train_ledger.py` 按 `TrainingGuards.NO_CLOUD_HALT_KINDS` 取，故必须仍挂在根上）。
STAYS = ("_ledger_apply", "_is_soft_verdict", "_gate_halt_mode", "_sync_cloud_halt")
ROOT_CONSTS = ("CLOUD_HALT_VERDICTS", "NO_CLOUD_HALT_KINDS", "GATE_HALT_MODES")

#: 一族五个文件 → 类名。
FAMILY: dict[str, str] = {
    "loop_guards.py": "TrainingGuards",
    "loop_guards_trip.py": "TrainingGuardsTrip",
    "loop_guards_leg.py": "TrainingGuardsLeg",
    "loop_guards_gate.py": "TrainingGuardsGate",
    "loop_guards_sweep.py": "TrainingGuardsSweep",
}

#: 组装逐字：文件 → 自己写的基类元组。
BASES: dict[str, tuple[str, ...]] = {
    "loop_guards_trip.py": ("object",),
    "loop_guards_leg.py": ("object",),
    "loop_guards_gate.py": ("object",),
    "loop_guards_sweep.py": ("object",),
    "loop_guards.py": (
        "TrainingGuardsTrip",
        "TrainingGuardsLeg",
        "TrainingGuardsGate",
        "TrainingGuardsSweep",
    ),
}

#: 模块级助手 → 唯一消费它的新家（跟着唯一使用者搬，不留在根）。
HELPERS: dict[str, str] = {
    "_num_or_none": "loop_guards_trip.py",
    "_is_nonfinite": "loop_guards_trip.py",
    "_gate_startup_notes": "loop_guards_gate.py",
}

#: 入边闭集：成员 → {文件: `self.<成员>(` 的**真实 Call** 数}（新入边必须改这张表）。
INBOUND_CALLS: dict[str, dict[str, int]] = {
    "_breaker": {"loop_round_steps.py": 1},
    "_stop_loss": {"loop_round_steps.py": 1},
    "_kickstart_burn": {"loop_round_steps.py": 1},
    "_paired_kill": {"loop_round_steps.py": 1},
    "_gate": {"loop_round_steps.py": 1},
    "_warn_min_train_unreachable": {"loop_guards_gate.py": 1},
    "_apply_verdict": {"loop_guards_gate.py": 2},
    "_budget_hard_cut": {"loop_round_steps.py": 1},
    "_rotate_cleanup": {"loop_round_steps.py": 1},
    # 共享 sink：留根的就是因为它们被**多簇**调（三簇 9 处 + 轮内两处）。
    "_ledger_apply": {
        "loop_guards_gate.py": 2,
        "loop_guards_leg.py": 4,
        "loop_guards_trip.py": 3,
        "loop_round_steps.py": 1,
        "loop_steps.py": 1,
    },
    "_is_soft_verdict": {"loop_guards.py": 1, "loop_guards_gate.py": 1},
    "_gate_halt_mode": {"loop_guards.py": 1},
    "_sync_cloud_halt": {
        "loop_guards_gate.py": 3,
        "loop_guards_leg.py": 2,
        "loop_lifecycle.py": 1,
    },
}

#: 出边闭集：文件 → 它调用的**跨文件**兄弟（同簇互调不算；经真继承解析）。
OUTBOUND_HANDS: dict[str, frozenset[str]] = {
    "loop_guards.py": frozenset(),
    "loop_guards_trip.py": frozenset({"_ledger_apply"}),
    "loop_guards_leg.py": frozenset({"_ledger_apply", "_sync_cloud_halt"}),
    "loop_guards_gate.py": frozenset({"_is_soft_verdict", "_ledger_apply", "_sync_cloud_halt"}),
    "loop_guards_sweep.py": frozenset(),
}

#: 借用的**方法**（不在本簇里、以 `self.` 调）：文件 → {方法: 真实现住哪}。
#: 它们必须在类体里声明 `Any`，否则 mypy 报 `attr-defined`（同 S22 的 HELPER_HANDS 口径）。
BORROWED_METHODS: dict[str, dict[str, str]] = {
    "loop_guards_trip.py": {"_ledger_apply": "rl.loop_guards"},
    "loop_guards_leg.py": {"_ledger_apply": "rl.loop_guards", "_sync_cloud_halt": "rl.loop_guards"},
    "loop_guards_gate.py": {
        "_ledger_apply": "rl.loop_guards",
        "_sync_cloud_halt": "rl.loop_guards",
        "_is_soft_verdict": "rl.loop_guards",
        "_eval_on_round": "rl.loop_dispatch",
    },
    "loop_guards_sweep.py": {},
}

#: 顶层 import 面的闭集（非 stdlib）；多一个都要显式登记。
TOP_IMPORTS: dict[str, frozenset[str]] = {
    "loop_guards.py": frozenset(
        {
            "dist_common",
            "remote.hub_client",
            "rl.log",
            "rl.loop_guards_gate",
            "rl.loop_guards_leg",
            "rl.loop_guards_sweep",
            "rl.loop_guards_trip",
        }
    ),
    "loop_guards_trip.py": frozenset({"rl.breaker", "rl.events", "rl.log", "rl.stop_loss"}),
    "loop_guards_leg.py": frozenset(
        {
            "rl.config",
            "rl.events",
            "rl.gate_check",
            "rl.kickstart_burn",
            "rl.log",
            "rl.paired",
            "rl.paired_kill",
        }
    ),
    "loop_guards_gate.py": frozenset({"rl.events", "rl.gate_check", "rl.log"}),
    "loop_guards_sweep.py": frozenset({"platform_utils", "rl.log", "rl.workdir_sweep"}),
}

#: 允许的 stdlib 顶层 import（闭集的一部分）。
STDLIB_IMPORTS = frozenset(
    {"__future__", "json", "math", "time", "pathlib", "typing", "datetime"}
)

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "rl.loop_core",
        "rl.loop_guards",
        "rl.loop_lifecycle",
        "rl.loop_round_steps",
        "rl.loop_steps",
        "rl.loop_volume",
    }
)

#: ★ patch 锚点：这四个**模块全局名**必须只住组合根（四个测试文件按 `rl.loop_guards.<名>`
#: 打桩；搬走会让桩静静失效 —— `raising=True` 只能保证打桩那一刻响亮）。
PATCH_ANCHORS = ("set_cloud_halt", "dist_common")

#: 搬走后就**不该**在 `rl.loop_guards` 命名空间里存在的名字（旧家不再吸收 patch）。
GONE_FROM_ROOT = (
    "breaker_update",
    "write_circuit_break",
    "write_kickstart_burn",
    "write_paired_kill",
    "write_stop_loss",
    "evaluate",
    "read_trend_rows",
    "burn_overrides",
    "paired_kill_verdict",
    "sweep_failed_wave_dirs",
    "stop_loss_hit",
    "rmtree_best_effort",
)


# ─────────────────────────────── 解析助手 ───────────────────────────────


def _cls(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls_name
    )


def _methods(source: Path, cls_name: str) -> dict[str, ast.FunctionDef]:
    return {m.name: m for m in _cls(source, cls_name).body if isinstance(m, ast.FunctionDef)}


def _self_attrs(node: ast.AST) -> set[str]:
    return {
        n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    }


def _self_assigns(node: ast.AST) -> set[str]:
    """只看**赋值目标**（`self.X = …` / `+=`），不含读。"""
    out: set[str] = set()
    for n in ast.walk(node):
        targets: list[ast.AST] = []
        if isinstance(n, ast.Assign):
            targets = list(n.targets)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            targets = [n.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                out.add(t.attr)
    return out


def _self_calls(node: ast.AST) -> set[str]:
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
    }


def _top_level_names(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def _module_globals(path: Path) -> set[str]:
    """顶层 import 绑定的**名字**（`from remote.hub_client import set_cloud_halt` → `set_cloud_halt`）。"""
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            out.update((a.asname or a.name.split(".")[0]) for a in n.names)
        elif isinstance(n, ast.ImportFrom) and not n.level:
            out.update((a.asname or a.name) for a in n.names)
    return out


def _top_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module)
    return out


def _declared(path: Path, cls_name: str) -> set[str]:
    return {
        n.target.id
        for n in _cls(path, cls_name).body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }


def _family_reads(fname: str, *, assign: bool) -> dict[str, set[str]]:
    """本簇成员里 `self.<名>` 的读/写面（名字 → 出现它的成员名集合）。"""
    out: dict[str, set[str]] = {}
    for member, fn in _methods(RL / fname, FAMILY[fname]).items():
        names = _self_assigns(fn) if assign else (_self_attrs(fn) - _self_calls(fn))
        for name in names:
            out.setdefault(name, set()).add(member)
    return out


# ─────────────────────────────── 定义面 ───────────────────────────────


def test_members_live_in_exactly_their_new_home() -> None:
    """9 个搬走的成员各自只在新家定义一次；旧家（组合根）里**不得**再有同名。"""
    for member, (fname, _cls_name) in HOMES.items():
        definers = [name for name, cn in FAMILY.items() if member in _methods(RL / name, cn)]
        assert definers == [fname], f"{member} 的定义面：{definers}（期望 {fname}）"
    root_src = (RL / "loop_guards.py").read_text(encoding="utf-8")
    for member in MEMBERS:
        assert f"def {member}(" not in root_src, f"{member} 又回到组合根了"


def test_root_keeps_exactly_the_shared_sinks() -> None:
    """组合根的成员闭集**恰好**是四个共享 sink（多一个 = 有人顺手塞了新方法）。"""
    got = sorted(_methods(RL / "loop_guards.py", "TrainingGuards"))
    assert got == sorted(STAYS), f"组合根多/少了方法：{got}"


def test_helpers_moved_with_their_only_consumer() -> None:
    """三个模块级助手跟着唯一使用者走；组合根顶层只剩类本身。"""
    assert _top_level_names(RL / "loop_guards.py") == {"TrainingGuards"}
    for helper, fname in HELPERS.items():
        assert helper in _top_level_names(RL / fname), f"{helper} 应住 {fname}"
        for other in FAMILY:
            if other != fname:
                assert helper not in _top_level_names(RL / other), f"{helper} 出现在 {other}"


def test_identity_through_the_composition_root() -> None:
    """`TrainingGuards.X is 新家.X`——接线是**对象级**，不是同名副本。"""
    import rl.loop_guards as root

    for member, (fname, cls_name) in HOMES.items():
        home = getattr(__import__(f"rl.{fname[:-3]}", fromlist=[cls_name]), cls_name)
        got = getattr(root.TrainingGuards, member)
        assert got is getattr(home, member), member
        assert got.__module__ == f"rl.{fname[:-3]}", member


def test_no_member_name_shadowed_across_the_family() -> None:
    """13 个成员名在族里**零重名**（MRO 遮罩的充要条件）——声明的顺序恒惰性。"""
    all_names = MEMBERS + STAYS
    for name in all_names:
        definers = [f for f, cn in FAMILY.items() if name in _methods(RL / f, cn)]
        assert len(definers) == 1, f"{name} 被定义在 {definers}"


# ─────────────────────────────── 组装 ───────────────────────────────


def test_composition_is_exact() -> None:
    """组合根仍是 `TrainingGuards`、仍住 `rl/loop_guards.py`，基类元组逐字；组合类一行不改。"""
    import rl.loop_guards as root
    from rl.loop_core import TrainingLoop
    from rl.loop_eval import TrainingEval
    from rl.loop_export import TrainingExport
    from rl.loop_guards import TrainingGuards
    from rl.loop_guards_gate import TrainingGuardsGate
    from rl.loop_guards_leg import TrainingGuardsLeg
    from rl.loop_guards_sweep import TrainingGuardsSweep
    from rl.loop_guards_trip import TrainingGuardsTrip
    from rl.loop_lifecycle import TrainingLifecycle
    from rl.loop_remote import TrainingRemote
    from rl.loop_round_steps import RoundSteps
    from rl.loop_steps import TrainingSteps

    for fname, want in BASES.items():
        cls = getattr(root if fname == "loop_guards.py" else __import__(
            f"rl.{fname[:-3]}", fromlist=[FAMILY[fname]]
        ), FAMILY[fname])
        assert tuple(c.__name__ for c in cls.__bases__) == want, fname

    assert root.TrainingGuards.__module__ == "rl.loop_guards"
    # 组合类与**既有守卫一行不改**（本簇挂的是 `TrainingGuards` 一侧的链）。
    assert TrainingLoop.__bases__ == (
        RoundSteps,
        TrainingSteps,
        TrainingGuards,
        TrainingLifecycle,
    )
    assert TrainingSteps.__bases__ == (TrainingRemote, TrainingEval, TrainingExport)
    quads = (TrainingGuardsTrip, TrainingGuardsLeg, TrainingGuardsGate, TrainingGuardsSweep)
    for k in quads:
        assert k not in TrainingLoop.__bases__
        assert k in TrainingLoop.__mro__
    # MRO 相对位置：根之后、`TrainingLifecycle` 之前（四簇互不调用 ⇒ 顺序惰性，但要钉住）。
    mro = [c.__name__ for c in TrainingLoop.__mro__]
    assert mro[mro.index("TrainingGuards") + 1 : mro.index("TrainingLifecycle")] == [
        "TrainingGuardsTrip",
        "TrainingGuardsLeg",
        "TrainingGuardsGate",
        "TrainingGuardsSweep",
    ]


def test_borrowed_declarations_are_exactly_the_derived_set() -> None:
    """四簇的类体声明 = 「派生的事实」∪「借用的方法」——多一个（没用的声明）也要报。

    ★ 只数「谁碰到这个名字」不够（S20 的教训）：这里 `declared` 与 `self.X` 的派生集合
    逐项相等，任一侧长出来都红。
    """
    for fname, borrowed in BORROWED_METHODS.items():
        derived = set(_family_reads(fname, assign=False)) | {
            name for name in _family_reads(fname, assign=True)
        }
        want = derived | set(borrowed)
        assert _declared(RL / fname, FAMILY[fname]) == want, fname
        # 借用的方法集 == 实际以 `self.` 调的非本簇方法（不能声明了却没人调）
        called: set[str] = set()
        for fn in _methods(RL / fname, FAMILY[fname]).values():
            called |= _self_calls(fn)
        assert called - set(_methods(RL / fname, FAMILY[fname])) == set(borrowed), fname


def test_root_declares_the_slots_and_four_homes_declare_none_of_them() -> None:
    """训练状态槽位只在组合根声明一处；四簇**借**（`self.`）而不重复声明。"""
    root_decl = _declared(RL / "loop_guards.py", "TrainingGuards")
    assert {"args", "_jsonl_path", "_ledger", "_cloud_halted", "_eval_on_round"} <= root_decl
    for fname in HOMES.values():
        f = fname[0]
        if f == "loop_guards_sweep.py":
            continue
        for slot in ("_ledger", "_cloud_halted"):
            assert slot not in _declared(RL / f, FAMILY[f]), (f, slot)


# ─────────────────────────────── 入边 / 出边 / 状态 ───────────────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边逐项对账（AST 计真实 `Call`，不数源码字符串——S20 的假红教训）。"""
    got: dict[str, dict[str, int]] = {m: {} for m in INBOUND_CALLS}
    for path in sorted(RL.glob("*.py")):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "self"
                and n.func.attr in got
            ):
                got[n.func.attr][path.name] = got[n.func.attr].get(path.name, 0) + 1
    for member, want in INBOUND_CALLS.items():
        assert got[member] == want, f"{member} 的入边变了：{got[member]}"


def test_outbound_hands_closed_set() -> None:
    """出边：每簇只许调它申报的**跨簇**兄弟（方向的正面证据：调用者依赖被调用者）。"""
    home_of = {m: HOMES[m][0] for m in MEMBERS}
    home_of.update({m: "loop_guards.py" for m in STAYS})
    for fname, want in OUTBOUND_HANDS.items():
        called: set[str] = set()
        for fn in _methods(RL / fname, FAMILY[fname]).values():
            called |= _self_calls(fn)
        cross = {m for m in called if m in home_of and home_of[m] != fname}
        assert cross == set(want), f"{fname} 的跨簇出边变了：{sorted(cross)}"


def test_root_calls_nobody() -> None:
    """组合根**不回调**任何簇——提供者不该知道客户端（反向依赖的守卫）。

    根内部三人（`_sync_cloud_halt` → `_is_soft_verdict` / `_gate_halt_mode`）互调是允许的；
    钉的是「根 → 簇」这条方向不存在。
    """
    called: set[str] = set()
    for fn in _methods(RL / "loop_guards.py", "TrainingGuards").values():
        called |= _self_calls(fn)
    assert called & set(HOMES) == set(), sorted(called & set(HOMES))
    assert called <= set(STAYS)


def test_cloud_halt_state_has_a_single_writer() -> None:
    """`_cloud_halted`（云机停机达令当前态）的唯一写手 = 组合根（幂等与按课程下发的前提）。"""
    writers = {
        fname for fname in FAMILY if "_cloud_halted" in _family_reads(fname, assign=True)
    }
    assert writers == {"loop_guards.py"}, writers
    readers = {
        fname
        for fname in FAMILY
        if any("_cloud_halted" in _self_attrs(fn) for fn in _methods(RL / fname, FAMILY[fname]).values())
    }
    assert readers <= {"loop_guards.py"}, readers


def test_root_keeps_the_verdict_vocabulary_constants() -> None:
    """三个判决词表常量仍是 `TrainingGuards` 的**类属性**（外部按类取，不按模块取）。"""
    from rl.loop_guards import TrainingGuards

    for const in ROOT_CONSTS:
        assert const in TrainingGuards.__dict__, const
    assert frozenset({"plateau"}) == TrainingGuards.NO_CLOUD_HALT_KINDS
    assert frozenset({"REMEDIATE", "PAUSE", "ABORT"}) == TrainingGuards.CLOUD_HALT_VERDICTS
    assert TrainingGuards.GATE_HALT_MODES == ("halt", "notify")


# ─────────────────────────────── 依赖面 ───────────────────────────────


def test_top_level_import_surface_is_closed() -> None:
    for fname, want in TOP_IMPORTS.items():
        got = _top_imports(RL / fname)
        assert got <= (want | STDLIB_IMPORTS), f"{fname} 长出非申报 import：{sorted(got - want)}"
        assert got >= want, f"{fname} 少了申报 import：{sorted(want - got)}"


def test_no_reverse_edges() -> None:
    """四簇不许 import 编排上游（只靠 `self.` 回调 / 组合根）。"""
    for fname in HOMES.values():
        got = _top_imports(RL / fname[0])
        assert not (got & FORBIDDEN_IMPORTS), f"{fname[0]} 出现反向边：{sorted(got & FORBIDDEN_IMPORTS)}"


def test_patch_anchor_stays_in_the_root() -> None:
    """★ 两个 patch 锚点（`set_cloud_halt` / `dist_common`）必须只住组合根。

    四个测试文件按 `rl.loop_guards.<名>` 打桩；搬走会让桩**静静失效**（最坏的一种：
    测试仍绿，但打的是空气）。
    """
    root_globals = _module_globals(RL / "loop_guards.py")
    for anchor in PATCH_ANCHORS:
        assert anchor in root_globals, anchor
        for fname in HOMES.values():
            assert anchor not in _module_globals(RL / fname[0]), f"{anchor} 跑到 {fname[0]}"


def test_old_home_no_longer_absorbs_the_moved_seams() -> None:
    """搬走的名称不得留在 `rl.loop_guards` 命名空间（否则旧桩会「打中一个没人用的名字」）。"""
    root_globals = _module_globals(RL / "loop_guards.py")
    leaked = sorted(set(GONE_FROM_ROOT) & root_globals)
    assert leaked == [], f"这些 seam 还挂在旧家：{leaked}"


# ─────────────────────────────── ★ 功能性 ───────────────────────────────


def _obj(**attrs: object) -> object:
    from rl.loop_guards import TrainingGuards

    obj = TrainingGuards.__new__(TrainingGuards)
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def _base_args(**kw: object) -> SimpleNamespace:
    d: dict[str, object] = {
        "mode": "goal",
        "kl_break": 0.6,
        "kl_break_consec": 3,
        "ent_break": 2.0,
        "ent_break_consec": 3,
        "ent_break_max_winrate": 0.5,
        "out": "tmp/out.json",
        "remote_hub_url": "",
        "remote_token": "",
        "course_path": "curricula/t5-kk.jsonc",
        "stop_loss_at": 1,
        "stop_loss_delta": 0.0,
        "max_hours": 0.0,
        "keep_iters": 0,
        "remote_job_root": "",
        "kickstart_ref": False,
    }
    d.update(kw)
    return SimpleNamespace(**d)


def _rows(wr: list[float | None]) -> list[dict]:
    return [
        {"event": "eval_summary", "iter": i, "winRate": v, "games": 50}
        for i, v in enumerate(wr)
    ]


def test_trip_breaker_really_trips_on_nonfinite(tmp_path: Path) -> None:
    """★ 过程面：NaN 出现**即**熔断（不走连击），并补一条 ABORT 判决。

    seam 断言：判决写入解析到**新家**的 `write_gate_verdict`——打旧家的名字打不中。
    """
    import rl.loop_guards as root
    import rl.loop_guards_trip as trip

    seen: list[str] = []
    jsonl = tmp_path / "training_log.jsonl"
    obj = _obj(
        args=_base_args(),
        _agg={"kl": float("nan"), "entropy": 1.0, "policy": 0.0, "value": 0.0},
        _report={"winRate": 0.5},
        _jsonl_path=jsonl,
        _kl_streak=0,
        _ent_streak=0,
        _ent_peak=None,
        _tripped=None,
        _prev_entropy=None,
        _ledger=None,
    )
    trip.write_gate_verdict = lambda *a, **kw: seen.append(a[2])  # type: ignore[assignment]
    try:
        assert trip.TrainingGuardsTrip._breaker(obj, 7) is True  # type: ignore[arg-type]
    finally:
        del trip.write_gate_verdict
    assert seen == ["ABORT"], "熔断必须同写 ABORT 判决（否则执行面读不到）"
    assert "non-finite" in obj._tripped  # type: ignore[attr-defined]
    events = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").strip().splitlines()]
    assert any(e["event"] == "circuit_break" and e["iter"] == 7 for e in events)
    # 旧家不再吸收这个 seam（响亮：属性不存在）
    assert not hasattr(root, "write_gate_verdict")


def test_trip_stop_loss_needs_two_consecutive_significant_rounds(tmp_path: Path) -> None:
    """★ 过程面：Δ≤−2σ 一轮只记数，**连续两轮**才停车（P1-9）。"""
    from rl.loop_guards_trip import TrainingGuardsTrip

    jsonl = tmp_path / "training_log.jsonl"
    obj = _obj(
        args=_base_args(),
        _jsonl_path=jsonl,
        _stop_loss_streak=0,
        _ledger=None,
    )
    rec = {"delta": -0.1, "games": 400, "winRate": 0.5}  # σ=0.025 ⇒ −2σ=−0.05
    assert TrainingGuardsTrip._stop_loss(obj, 5, rec) is False  # type: ignore[arg-type]
    assert TrainingGuardsTrip._stop_loss(obj, 6, rec) is True  # type: ignore[arg-type]
    events = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").strip().splitlines()]
    assert [e["streak"] for e in events if e["event"] == "stop_loss"] == [1, 2]


def test_leg_kickstart_burn_really_stops_the_leg(tmp_path: Path) -> None:
    """★ 结果面：连续 3 点低于起点基线 ⇒ 停腿（True）+ 可回放的账本。"""
    from rl.loop_guards_leg import TrainingGuardsLeg

    jsonl = tmp_path / "training_log.jsonl"
    (tmp_path / "eval_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _rows([0.35, 0.29, 0.28, 0.27])),
        encoding="utf-8",
    )
    obj = _obj(
        args=_base_args(kickstart_ref=True),
        _jsonl_path=jsonl,
        _traj_root=tmp_path,
        _burn_streak=0,
        _ledger=None,
    )
    assert TrainingGuardsLeg._kickstart_burn(obj, 3, None) is True  # type: ignore[arg-type]
    assert obj._burn_streak == 3  # type: ignore[attr-defined]
    events = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").strip().splitlines()]
    burn = [e for e in events if e["event"] == "kickstart_burn"][-1]
    assert burn["baseline"] == pytest.approx(0.35)
    assert any(e["event"] == "gate_verdict" and e.get("verdict") == "ABORT" for e in events)


def test_leg_paired_kill_is_inert_without_a_paired_course(tmp_path: Path) -> None:
    """★ 结果面：没声明 `paired_rotate_seed` 就没有「对端」这回事 ⇒ 零行为。"""
    from rl.loop_guards_leg import TrainingGuardsLeg

    obj = _obj(
        args=_base_args(),
        _jsonl_path=tmp_path / "training_log.jsonl",
        _traj_root=tmp_path,
        _pair_kill_streak=0,
        _ledger=None,
    )
    assert TrainingGuardsLeg._paired_kill(obj, 5, None) is False  # type: ignore[arg-type]


def test_gate_budget_hard_cut_really_stops(tmp_path: Path) -> None:
    """★ 门族：max_hours 到顶 ⇒ 轮级硬断 True + 落 STOP（exit-watchdog 认它是设计内停车）。"""
    from rl.loop_guards_gate import TrainingGuardsGate

    jsonl = tmp_path / "training_log.jsonl"
    started = datetime.now() - timedelta(hours=10)
    jsonl.write_text(
        json.dumps({"event": "run_start", "time": started.strftime("%Y-%m-%d %H:%M:%S")}) + "\n",
        encoding="utf-8",
    )
    obj = _obj(args=_base_args(max_hours=1.0), _jsonl_path=jsonl)
    assert TrainingGuardsGate._budget_hard_cut(obj, 9) is True  # type: ignore[arg-type]
    events = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").strip().splitlines()]
    assert any(e["event"] == "gate_verdict" and e.get("verdict") == "STOP" for e in events)


def test_gate_is_inert_without_a_gates_block(tmp_path: Path) -> None:
    """★ 门族：老课程（无 `gates` 块）恒 False——零行为变化。"""
    from rl.loop_guards_gate import TrainingGuardsGate

    obj = _obj(args=_base_args(), _jsonl_path=tmp_path / "training_log.jsonl", _traj_root=tmp_path)
    assert TrainingGuardsGate._gate(obj, 3) is False  # type: ignore[arg-type]
    # 门族的另外两个成员在无输入时也不得抛（借用的槽位缺失一律退化）
    spec = SimpleNamespace(min_train_samples=None)
    budget = SimpleNamespace(iters=0, cur_iter=0)
    TrainingGuardsGate._warn_min_train_unreachable(obj, 3, spec, budget)  # type: ignore[arg-type]


def test_sweep_really_rotates_the_iter_dirs(tmp_path: Path) -> None:
    """★ 磁盘回收：keep_iters=1 在 it3 时删 it1/it2、留 it3；job 清理由 manifest 定。"""
    from rl.loop_guards_sweep import TrainingGuardsSweep

    for name in ("it1", "it2", "it3"):
        (tmp_path / name).mkdir()
    obj = _obj(
        args=_base_args(keep_iters=1),
        _traj_root=tmp_path,
        _traj_dir=tmp_path / "it3",
    )
    TrainingGuardsSweep._rotate_cleanup(obj, 3)  # type: ignore[arg-type]
    assert not (tmp_path / "it1").exists()
    assert not (tmp_path / "it2").exists()
    assert (tmp_path / "it3").exists()


def test_root_ledger_view_advances_by_increment() -> None:
    """★ 共享 sink：写入账本处顺手并入视图；视图缺失/抛错都不得阻断训练。"""
    from rl.loop_guards import TrainingGuards

    seen: list[str] = []

    class _View:
        def apply_event(self, event: dict) -> None:
            seen.append(event["event"])

    obj = _obj(_ledger=_View())
    TrainingGuards._ledger_apply(obj, {"event": "iteration"})  # type: ignore[arg-type]
    assert seen == ["iteration"]
    TrainingGuards._ledger_apply(_obj(_ledger=None), {"event": "iteration"})  # type: ignore[arg-type]

    class _Boom:
        def apply_event(self, event: dict) -> None:
            raise RuntimeError("观测失败不得影响训练主链")

    TrainingGuards._ledger_apply(_obj(_ledger=_Boom()), {"event": "iteration"})  # type: ignore[arg-type]


def test_patch_anchor_really_intercepts_the_cloud_halt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ patch 锚点真拦截：按 `rl.loop_guards.set_cloud_halt` 打桩能拦住真调用。"""
    sent: list[tuple[str, str, bool]] = []

    def _fake(url: str, tok: str, halt: bool, **kw: object) -> bool:
        sent.append((url, tok, halt))
        return True

    monkeypatch.setattr("rl.loop_guards.set_cloud_halt", _fake, raising=True)
    obj = _obj(
        args=_base_args(remote_hub_url="http://hub", remote_token="tok"),
        _traj_root=tmp_path,
        _cloud_halted=False,
    )
    from rl.loop_guards import TrainingGuards

    TrainingGuards._sync_cloud_halt(obj, 5, "ABORT")  # type: ignore[arg-type]
    assert sent == [("http://hub", "tok", True)]
    TrainingGuards._sync_cloud_halt(obj, 6, "ABORT")  # type: ignore[arg-type]
    assert len(sent) == 1, "停机态已一致 ⇒ 幂等，不重发"


def test_wall_clock_is_only_used_by_the_budget_guard() -> None:
    """本文件的确定性前提：四簇里只有 `_budget_hard_cut` 碰墙钟（明写在审查口径里）。"""
    importers: list[str] = []
    for fname in FAMILY:
        for fn in _methods(RL / fname, FAMILY[fname]).values():
            if any(
                isinstance(n, ast.Attribute) and n.attr in {"time", "mktime", "strptime"}
                for n in ast.walk(fn)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            ):
                importers.append(f"{fname}::{fn.name}")
    assert sorted(importers) == [
        "loop_guards_gate.py::_budget_hard_cut",
        "loop_guards_gate.py::_gate",
    ]
