"""拆分的**契约守卫**：`loop_core` 的主循环骨架永住 `rl/loop_lifecycle.py`
（S4 第十九刀，2026-09-25）。

## 这一刀切了什么

`rl/loop_core.py` 931 → 446 行；`TrainingLoop` 本体里**唯一一条真正的方法间调用链**——7 个
成员（351 行）——搬到 `rl/loop_lifecycle.py::TrainingLifecycle`，另有 7 个模块级定义
（150 行）**只能随它走**（闭包实测）：

```
run ──→ _setup ──→ _setup_common
 │        ↑
 ├──→ run_one_round（→ RoundSteps 步骤表）
 └──→ _park_after_completion ──→ finish_course / _evalboard_idle
```

## 宿主判据（本刀的题眼）：为什么**不是**某个 sibling mixin

本仓通例是「调用者依赖被调用者」（把被调用的一簇挂到调用者那一侧）。本簇**破例**，因为
`_evalboard_idle` 有入边——两个 mixin 以 `self.` 调它：`rl/loop_remote_job.py`（1 处；
S4 第二十二刀前在 `rl/loop_remote.py`，调用者是 `_remote_ppo_publish`）与
`rl/loop_round_steps.py`（2 处）。新混入要同时是这两个 caller 的祖先才接得住；而
`set(RoundSteps.__mro__) ∩ set(TrainingRemote.__mro__) == {object}` —— **交集为空**，任何
sibling 宿主都不存在。唯一出路 = 组合根 `TrainingLoop`（`__bases__` 三件套 → 末位追加第四件）。
本文件把这条判据写成**机器可检**的形式（见 `test_host_verdict_is_the_composition_root`）。

## 本文件钉住什么

1. 7 个成员**定义**在新家，`TrainingLoop` 里**零同名定义**（门面只能是门面）；
2. 接线是**对象级**同一（`TrainingLoop.X is TrainingLifecycle.X`），不是同名副本；
3. 组装是**末位追加**：`TrainingLoop.__bases__ == (RoundSteps, TrainingSteps, TrainingGuards,
   TrainingLifecycle)`（逐字元组与全量 MRO 名单的唯一所有者在 `test_loop_core_tail_split.py`）；
   `TrainingSteps.__bases__` / `RoundSteps.__bases__[0]` / 各 `__mro__[1]` 逐字不变；
4. 7 个模块级名字**随簇走且不留别名**——旧家再没有这些属性，历史的
   `setattr(rl.loop_core, "should_park_on_done", …)` 会**响亮抛 AttributeError** 而不是
   静默变成空操作（本仓撞过三次的同族陷阱）；`run_inspect` 反向：它是文档化的可替换点，
   **必须留在**旧家；
5. **状态归属不变**：本混入零类级槽位声明、无 `__init__`；槽位仍全在 `TrainingLoop.__init__`；
6. **跨模块手闭集**（三张表）：入边（谁调本模块）· 出边（本模块调谁）· 槽位写-读手——任何
   新增都要显式改这些表；
7. 顶层 import 面**闭合**，且**不得**反向 import `rl.loop_core` / `rl.loop_round_steps` /
   `rl.loop_volume` / `rl.loop_remote` / `rl.loop_eval`（会成环或反向依赖）；
8. **★ 三条功能性**：出边手在真组合上解析到**预期的那个类**（不是同名副本）· `run_one_round`
   真的经新家跑通（含异常分类走 `round_failure`）· 旧家不再吸收 patch（响亮失败）。
"""

from __future__ import annotations

import ast
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent
LIFE_PY = NN_ROOT / "rl/loop_lifecycle.py"
CORE_PY = NN_ROOT / "rl/loop_core.py"
REMOTE_PY = NN_ROOT / "rl/loop_remote.py"
ROUND_STEPS_PY = NN_ROOT / "rl/loop_round_steps.py"

#: 这一簇的成员（闭集）：7 个方法 —— 新方法要么住在新家，要么改这张表。
CLUSTER = (
    "run",
    "_setup",
    "_setup_common",
    "run_one_round",
    "_park_after_completion",
    "finish_course",
    "_evalboard_idle",
)

#: 随簇搬走的 7 个模块级定义（闭集，实测闭包；旧家不得留别名）。
CLUSTER_MODULE_NAMES = (
    "WAIT_RETRY_SEC",
    "_course_file_fp",
    "_kickstart_startup_check",
    "_paired_seed_startup_check",
    "KICKSTART_DEFAULT_WARN",
    "_kickstart_baseline_row",
    "should_park_on_done",
)

#: 入边闭集：谁以 `self.<名字>(` 调本模块（搬家前后**手数不变**，只是换了落点）。
INBOUND_CALLS = {
    "rl/loop_remote_job.py": 1,
    "rl/loop_round_steps.py": 2,
}

#: 出边闭集：本模块的 `self.<名字>(` 里，定义**不在**本模块的那些（混入常态：组合实例上动态解析）。
OUTBOUND_HANDS = {
    "run": {"_drain_pending_eval"},
    "run_one_round": {"round_failure", "round_steps"},
    "finish_course": {"_sync_cloud_halt"},
}

#: 出边手的**归属类**（★ 功能性断言用：必须解析到这些类，不是同名副本）。
OUTBOUND_OWNERS = {
    "_drain_pending_eval": "rl.loop_eval",
    "round_steps": "rl.loop_round_steps",
    "round_failure": "rl.loop_round_steps",
    "_sync_cloud_halt": "rl.loop_guards",
}

#: 槽位写手：本模块**写**的槽位（跨模块手，必须显式登记）。
#: 读者侧（`_prepare_iter_dir` / `_rollout_phase`）在 S4 第二十刀随它们搬出了旧家 ⇒ 那条
#: 闭集表归 `test_loop_core_tail_split.py`；本文件只钉写手 + 「旧家不再有读者」。
SLOT_HANDS_WRITTEN_BY = {
    "_setup_common": {"_course_fp", "_corpus_fp"},
}

#: 顶层 import 面（非 stdlib）——闭集：本模块不许长出重依赖。
TOP_IMPORTS = frozenset(
    {
        "dist_common",
        "rl.breaker",
        "rl.course",
        "rl.events",
        "rl.log",
        "rl.loop_guards",
        "rl.loop_round",
        "rl.loop_steps",
        "rl.queue",
        "rl.rollout_phase",
        "rl.train_ledger",
    }
)

#: 允许的 stdlib 顶层 import（闭集的一部分；多一个也要显式登记）。
STDLIB_IMPORTS = frozenset({"collections.abc", "sys", "time", "pathlib", "typing"})

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "rl.loop_core",
        "rl.loop_eval",
        "rl.loop_remote",
        "rl.loop_round_steps",
        "rl.loop_volume",
    }
)


def _cls(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)


def _methods(path: Path, cls_name: str) -> dict[str, ast.FunctionDef]:
    return {m.name: m for m in _cls(path, cls_name).body if isinstance(m, ast.FunctionDef)}


def _top_names(path: Path) -> set[str]:
    """模块级 def/class/赋值名。"""
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return out


def _top_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def _self_calls(path: Path, cls_name: str, method: str) -> set[str]:
    body = _methods(path, cls_name)[method]
    out: set[str] = set()
    for node in ast.walk(body):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            out.add(node.func.attr)
    return out


def _self_slots(path: Path, cls_name: str) -> dict[str, set[str]]:
    """每个方法碰到的 `self.<attr>` 名集合。"""
    out: dict[str, set[str]] = {}
    for name, node in _methods(path, cls_name).items():
        got: set[str] = set()
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "self"
            ):
                got.add(n.attr)
        out[name] = got
    return out


# ─────────────────────────────── 成员与接线 ───────────────────────────────


def test_members_live_in_the_new_home_only() -> None:
    """7 个成员定义在新家；`TrainingLoop` 里零同名定义（也不在别处重复定义）。"""
    in_life = _methods(LIFE_PY, "TrainingLifecycle")
    for name in CLUSTER:
        assert name in in_life, f"{name} 不在 TrainingLifecycle"
    in_core = _methods(CORE_PY, "TrainingLoop")
    assert sorted(set(CLUSTER) & set(in_core)) == []
    # 7 个模块级定义也只在新家（旧家不留同名 def/赋值）。
    top_life = _top_names(LIFE_PY)
    top_core = _top_names(CORE_PY)
    for name in CLUSTER_MODULE_NAMES:
        assert name in top_life, name
        assert name not in top_core, f"{name} 在旧家留了别名/副本"


def test_wiring_is_object_identity() -> None:
    """接线是对象级同一，不是同名副本。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_lifecycle import TrainingLifecycle

    for name in CLUSTER:
        got = getattr(TrainingLoop, name)
        assert got is getattr(TrainingLifecycle, name), name
        assert got.__module__ == "rl.loop_lifecycle", name


def test_module_level_names_moved_without_aliases() -> None:
    """7 个模块级名字的新家可用、旧家**不存在**（陈旧 patch 会响亮失败）。"""
    import rl.loop_core as core_mod
    import rl.loop_lifecycle as life_mod

    for name in CLUSTER_MODULE_NAMES:
        assert hasattr(life_mod, name), name
        assert not hasattr(core_mod, name), f"rl.loop_core.{name} 还在——陈旧 patch 会变空操作"
    # 反向：`run_inspect` 是文档化的可替换点（`_run_inspect` 的委托点）⇒ 必须留在旧家。
    assert hasattr(core_mod, "run_inspect")
    assert not hasattr(life_mod, "run_inspect")


def test_composition_appends_the_new_mixin() -> None:
    """组装是**末位追加**：组合类元组四件；S17/S18 钉的各基类元组与第 2 位逐字不变。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_guards import TrainingGuards
    from rl.loop_lifecycle import TrainingLifecycle
    from rl.loop_remote import TrainingRemote
    from rl.loop_round_steps import RoundSteps
    from rl.loop_steps import TrainingEval, TrainingSteps
    from rl.loop_volume import TrainingVolume

    assert TrainingLoop.__bases__ == (
        RoundSteps,
        TrainingSteps,
        TrainingGuards,
        TrainingLifecycle,
    )
    # 旧三刀钉的「追加不插队」纪律仍逐字成立（元组已随各刀末位追加演进：S17 追加 `TrainingEval`、
    # S21 追加 `TrainingExport`；本刀断言的是「前三项逐字不变」）。
    from rl.loop_export import TrainingExport

    assert TrainingSteps.__bases__ == (TrainingRemote, TrainingEval, TrainingExport)
    assert TrainingSteps.__mro__[1] is TrainingRemote
    assert RoundSteps.__bases__[0] is TrainingVolume
    assert RoundSteps.__mro__[1] is TrainingVolume
    # 本簇只要**在场**（入边要它同时被 RoundSteps 与 TrainingRemote 看到）；顺序无契约。
    # 全量 MRO 名单的唯一所有者在 `test_loop_core_tail_split.py`（S4 第二十刀收尾那份）。
    assert TrainingLifecycle in TrainingLoop.__mro__


def test_host_verdict_is_the_composition_root() -> None:
    """★ 宿主判据的机器形式：入边存在 ⇒ 定义只能挂组合根，sibling 宿主不存在。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_lifecycle import TrainingLifecycle
    from rl.loop_remote import TrainingRemote
    from rl.loop_round_steps import RoundSteps

    # ① 入边真的存在（两个 caller 以 self. 调它）——搬家前后手数不变。
    for rel, want in INBOUND_CALLS.items():
        src = (NN_ROOT / rel).read_text(encoding="utf-8")
        assert src.count("self._evalboard_idle(") == want, f"{rel} 的入边数变了"
        assert "def _evalboard_idle" not in src, f"{rel} 就地定义了它"
    # ② 两个 caller 的祖先集交集为空 ⇒ 任何 sibling 都接不住这条入边。
    anc_rs = set(RoundSteps.__mro__)
    anc_tr = set(TrainingRemote.__mro__)
    assert anc_rs & anc_tr == {object}
    assert not hasattr(RoundSteps, "_evalboard_idle")
    assert not hasattr(TrainingRemote, "_evalboard_idle")
    # ③ 定义在组合根上 ⇒ 同一条入边在真组合上照旧解析。
    assert TrainingLifecycle in TrainingLoop.__mro__
    assert TrainingLifecycle not in anc_rs and TrainingLifecycle not in anc_tr
    probe = TrainingLoop.__new__(TrainingLoop)
    assert probe._evalboard_idle.__func__ is TrainingLifecycle._evalboard_idle


def test_borrowed_declarations_are_exactly_the_touched_set() -> None:
    """状态归属不变：声明块只是**借用**，且逐项等于「碰到的、不属于本模块的」名字集合。

    本仓的混入约定（同 `rl/loop_volume.py`）是为 mypy 在每个文件里重复声明借用状态；因此
    本用例钉的不是「零声明」而是：**声明闭集 == 派生出来的借用集**（多一个/少一个都红）、
    且类体里不得出现带默认值的类级槽位（那才是「本混入自己持有状态」）。
    """
    from rl.loop_core import TrainingLoop
    from rl.loop_lifecycle import TrainingLifecycle

    touched: set[str] = set()
    for names in _self_slots(LIFE_PY, "TrainingLifecycle").values():
        touched |= names
    borrowed = {
        n
        for n in touched - set(_methods(LIFE_PY, "TrainingLifecycle")) - _top_names(LIFE_PY)
        if not n.startswith("__")
    }
    assert borrowed, "派生集不该为空——碰不到借用名字说明重建过程坏了"
    assert set(TrainingLifecycle.__annotations__) == borrowed
    # 类体里不得有带值的类级槽位/赋值（借来的契约只声明、不赋值）。
    assert not [n for n in _cls(LIFE_PY, "TrainingLifecycle").body if isinstance(n, ast.Assign)]
    assert not [
        n
        for n in _cls(LIFE_PY, "TrainingLifecycle").body
        if isinstance(n, ast.AnnAssign) and n.value is not None
    ]
    # 无 `__init__`：跨轮字段的持有者仍是组合根。
    assert "__init__" not in _methods(LIFE_PY, "TrainingLifecycle")
    assert "__init__" in _methods(CORE_PY, "TrainingLoop")
    probe = object.__new__(TrainingLoop)
    assert not hasattr(probe, "_traj_root")


# ─────────────────────────── 跨模块手（三张表） ───────────────────────────


def test_cross_module_inbound_hands_closed_set() -> None:
    """入边闭集：只有那两个文件、只有 `_evalboard_idle`（新入边必须改这张表）。"""
    assert set(INBOUND_CALLS) == {"rl/loop_remote_job.py", "rl/loop_round_steps.py"}
    for rel in INBOUND_CALLS:
        src = (NN_ROOT / rel).read_text(encoding="utf-8")
        # 入边只有 `self._evalboard_idle(` 这一种形状；没有别的 `self.<本簇成员>(`。
        for name in CLUSTER:
            if name == "_evalboard_idle":
                continue
            assert f"self.{name}(" not in src, f"{rel} 多了新入边 self.{name}("


def test_cross_module_outbound_hands_closed_set() -> None:
    """出边闭集：本模块调出去的 `self.<名字>`，逐个对账（多一个少一个都红）。"""
    own = set(_methods(LIFE_PY, "TrainingLifecycle"))
    got: dict[str, set[str]] = {}
    for name in CLUSTER:
        ext = {
            c
            for c in _self_calls(LIFE_PY, "TrainingLifecycle", name)
            if c not in own and not c.startswith("__")
        }
        if ext:
            got[name] = ext
    assert got == OUTBOUND_HANDS, f"出边变了：{got}"


def test_slot_hands_closed_set() -> None:
    """槽位写手闭集 + 旧家不再有读者（读者侧闭集表归 `test_loop_core_tail_split.py`）。"""
    life_slots = _self_slots(LIFE_PY, "TrainingLifecycle")
    core_slots = _self_slots(CORE_PY, "TrainingLoop")
    for writer, slots in SLOT_HANDS_WRITTEN_BY.items():
        got = slots & life_slots[writer]
        assert got == slots, f"{writer} 不再写 {sorted(slots - got)}"
    for slot in SLOT_HANDS_WRITTEN_BY["_setup_common"]:
        readers = {m for m, names in core_slots.items() if slot in names}
        assert readers == set(), f"旧家仍有 {slot} 的读者：{sorted(readers)}"


def test_top_level_imports_closed_no_reverse_edges() -> None:
    """顶层 import 闭集 + 禁反向边。"""
    got = _top_imports(LIFE_PY)
    assert got & FORBIDDEN_IMPORTS == set(), f"长出反向边：{got & FORBIDDEN_IMPORTS}"
    assert got >= TOP_IMPORTS, f"少了：{TOP_IMPORTS - got}"
    extra = got - TOP_IMPORTS - STDLIB_IMPORTS - {"__future__"}
    assert extra == set(), f"顶层 import 面长出：{sorted(extra)}"


# ─────────────────────────────── ★ 功能性 ───────────────────────────────


def test_outbound_hands_resolve_on_the_real_composition() -> None:
    """★ 出边手在真组合上解析到**预期的那个类**（同名副本会在这里露馅）。"""
    from rl.loop_core import TrainingLoop

    for name, owner in OUTBOUND_OWNERS.items():
        got = getattr(TrainingLoop, name)
        assert got.__module__ == owner, f"{name} 解析到了 {got.__module__}，预期 {owner}"


def test_one_round_runs_through_the_new_home() -> None:
    """★ `run_one_round` 真的跑通：终态直通 + 异常分类走 `round_failure`（跨模块手通电）。"""
    import types
    from typing import Any, cast

    from rl.loop_core import TrainingLoop
    from rl.loop_round import ROUND_NEXT, ROUND_RETRY, RoundOutcome, finish

    obj = TrainingLoop.__new__(TrainingLoop)
    seen: list[int] = []

    def ok_step(ctx: Any) -> Any:
        seen.append(ctx.it)
        return finish(ROUND_NEXT)

    cast(Any, obj).round_steps = lambda: [ok_step]
    out = cast(Any, obj).run_one_round(7)
    assert isinstance(out, RoundOutcome)
    assert out.status == ROUND_NEXT
    assert seen == [7]

    def boom_step(ctx: Any) -> Any:
        raise ValueError("引擎炸了")

    def fake_failure(e: BaseException, it: int, backoff: bool) -> RoundOutcome:
        assert isinstance(e, ValueError) and backoff is True
        return RoundOutcome(ROUND_RETRY, it)

    cast(Any, obj).round_steps = lambda: [boom_step]
    cast(Any, obj).round_failure = fake_failure
    assert cast(Any, obj).run_one_round(8).status == ROUND_RETRY
    # 让位（is_wait）在组合路径是**响亮报错**，不是静默停住。
    from rl.loop_round import RoundYieldError, wait_for

    cast(Any, obj).round_steps = lambda: [lambda ctx: wait_for("等 job")]
    try:
        cast(Any, obj).run_one_round(9)
    except RoundYieldError:
        pass
    else:  # pragma: no cover - 失败分支
        raise AssertionError("让位没有响亮报错")


def test_patch_points_moved_loudly() -> None:
    """★ 旧家不再吸收 patch：`setattr(rl.loop_core, <搬走的名字>, …)` 必须响亮失败。"""
    import rl.loop_core as core_mod

    for name in CLUSTER_MODULE_NAMES:
        assert not hasattr(core_mod, name)
        try:
            getattr(core_mod, name)
        except AttributeError:
            pass
        else:  # pragma: no cover - 失败分支
            raise AssertionError(f"旧家还能拿到 {name}——陈旧 patch 会静默失效")
    # `log` seam 也随方法走：非本模块用例不许再打 `rl.loop_core.log`（打不中就是空操作）。
    from rl.loop_lifecycle import TrainingLifecycle

    assert TrainingLifecycle.run.__module__ == "rl.loop_lifecycle"
