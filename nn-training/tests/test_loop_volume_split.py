"""拆分的**契约守卫**：`TrainingLoop` 的动态采集（按样本量）编排永住 `rl/loop_volume.py`
（S4 第十八刀，2026-09-25）。

## 这一刀切了什么

`rl/loop_core.py` 1386 → 923 行；`TrainingLoop`（25 方法）里**唯一一条真正的方法间调用链**
——9 个成员 / 445 行（占原模块 32%，且恰好是旧类的**尾块**）——搬到
`rl/loop_volume.py::TrainingVolume`：

```
_iteration_pairs ─┬─► _volume_active
                  ├─► _volume_est_samples ─► _volume_stage_ests_map ─┐
                  └─► _volume_stages ─────────────────────────────────┼─► _dispatch_volume_wave
_volume_topup ─┬─► _volume_journal_replay                             │
               └─► _volume_active                                     │
_volume_collect_continuous（VOLUME_RULE_V2 生产路径）───────────────────┘
```

切法是**混入**（与第十四/十五/十七刀同源）：同一把锁、同一个 `self`、**零行为变化**。
组装方向按本仓既有规则「调用者依赖被调用者」：本簇的外部入口**全部**在 `RoundSteps`
（`step_rollout` / `step_volume_topup`）⇒ `class RoundSteps(TrainingVolume)`，
**不是**给 `TrainingLoop` 加基类 —— 后者要改组合类 + 四个「继承真混入」的测试宿主，并让
`tests/test_loop_eval_split.py` 的「组合类直接基类」断言失守。本刀**零守卫改动**。

（2026-09-25 S4 第十九刀补：主循环骨架那一簇**不得不**动用组合根——它的入边让 sibling
 宿主不存在；那刀把组合类元组追加成四件，并同步登记了本文件与 `test_loop_eval_split.py`
 的两处断言。本文件钉的 `RoundSteps.__bases__` / `__mro__[1]` 逐字不变。）

## 本文件钉住什么

1. 9 个成员**定义**在新家，`TrainingLoop` 里**零同名定义**（门面只能是门面）；
2. 接线是**对象级同一**（`TrainingLoop.X is TrainingVolume.X`），不是同名副本——既有用例
   （`tests/test_rollout_volume.py` / `e2e/test_volume_e2e.py`）用的就是
   `TrainingLoop._volume_topup(cast(Any, stub), …)` 这种 **unbound 绑定**；
3. 组装是 `RoundSteps.__bases__[0] is TrainingVolume` 且 `__mro__[1]` 就是它（**末位追加**、
   不插队——S4 第十九/二十刀在它后面长过四件：`TrainingLifecycle` 与三个轮内实现混入）。
   逐字元组与全量 MRO 名单的唯一所有者在 `test_loop_core_tail_split.py`；
4. **状态归属**：七个 volume 槽位在 `TrainingVolume` 声明；`TrainingLoop.__init__` 仍负责
   **赋值**（跨轮字段的持有者不变）。另有三处在 `TrainingSteps` 里**有意并存**的声明——
   那个 sibling mixin 继承不到 `TrainingVolume`，不给声明 mypy 就报 attr-defined；
5. **跨模块手闭集**：除 `__init__`（Store × 7）与 `TrainingSteps._record_iteration`
   （Load × 3）外，任何宿主方法碰 volume 槽位都要显式改这张表；
6. 顶层 import 面**闭合**；`rl.volume_waves` / `rl.volume_quota` 只许在方法体内延迟 import
   （那是原有的 DI 面，测试 patch 的一直是实现模块）；
7. 不得反向 import `rl.loop_core` / `rl.loop_steps` / `rl.loop_round_steps`；
8. **★ 两条功能性**：`log` seam 住在**本模块**（打 `rl.loop_core.log` 是静默空操作——
   本刀的题眼）· unbound 绑定经 MRO 取到真实现。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

NN_ROOT = Path(__file__).resolve().parent.parent
VOLUME_PY = NN_ROOT / "rl" / "loop_volume.py"
CORE_PY = NN_ROOT / "rl" / "loop_core.py"
STEPS_PY = NN_ROOT / "rl" / "loop_steps.py"
ROUND_STEPS_PY = NN_ROOT / "rl" / "loop_round_steps.py"

#: 这一簇的成员（闭集）：9 个方法 —— 新方法要么住在 `TrainingLoop`，要么改这张表。
CLUSTER = (
    "_volume_active",
    "_volume_stages",
    "_volume_est_samples",
    "_iteration_pairs",
    "_volume_stage_ests_map",
    "_dispatch_volume_wave",
    "_volume_journal_replay",
    "_volume_topup",
    "_volume_collect_continuous",
)

#: 随簇搬走的七个槽位（新家 = `TrainingVolume`）。
CLUSTER_SLOTS = (
    "_volume_target",
    "_volume_collected",
    "_volume_waves",
    "_volume_g0",
    "_volume_est",
    "_volume_capped",
    "_volume_stage_ests",
)

#: 旧类里对这几个槽位的**跨模块使用**：宿主 `__init__` 赋值七个 + `TrainingSteps._record_iteration`
#: 读三个（落 iteration 事件）。任何新增读写都要显式改这张表。
CROSS_MODULE_HANDS: frozenset[str] = frozenset(
    [f"__init__|{slot}|Store" for slot in CLUSTER_SLOTS]
    + [
        "_record_iteration|_volume_capped|Load",
        "_record_iteration|_volume_collected|Load",
        "_record_iteration|_volume_target|Load",
    ]
)

#: `TrainingSteps` 里**有意并存**的槽位声明（该 sibling mixin 不继承 `TrainingVolume`）。
SIBLING_DECLARED = ("_volume_capped", "_volume_collected", "_volume_target")

#: 顶层 import 面的闭集（模块名）。
TOP_LEVEL_ALLOWED = {
    "__future__",
    "pathlib",
    "typing",
    "rl.course",
    "rl.log",
    "rl.reports",
    "rl.resume",
    "rl.rollout_phase",
}

#: DI 目标：只许**方法体内**延迟 import（测试 patch 的是这些实现模块）。
LAZY_ONLY = ("rl.volume_waves", "rl.volume_quota")


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
            roles.setdefault(sub.attr, set()).add(
                "Store" if isinstance(sub.ctx, ast.Store) else "Load"
            )
    return roles


def _top_level_imports(path: Path) -> set[str]:
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
    out: set[str] = set()
    for fn in [n for n in ast.walk(_tree(path)) if isinstance(n, ast.FunctionDef)]:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                out.add(sub.module)
            elif isinstance(sub, ast.Import):
                out.update(a.name for a in sub.names)
    return out


# ─────────────────────────────── 定义面 ───────────────────────────────


def test_cluster_is_defined_in_loop_volume_only() -> None:
    """9 个成员定义在 `TrainingVolume`；`TrainingLoop` 里**不得**再有同名定义。

    这条挡的是「就地补一个同名方法」——那会让 `TrainingVolume` 里那份变成死代码，而测试
    （跑在组合实例上，MRO 取到旧类那份）照样绿。
    """
    in_volume = set(_methods(VOLUME_PY, "TrainingVolume"))
    in_core = set(_methods(CORE_PY, "TrainingLoop"))
    assert set(CLUSTER) <= in_volume, sorted(set(CLUSTER) - in_volume)
    crept_back = sorted(set(CLUSTER) & in_core)
    assert crept_back == [], f"这些方法又回到 TrainingLoop 了：{crept_back}"
    # 闭集：新家只装这 9 个方法（顺手往里塞 helper 会在这里红）
    assert in_volume == set(CLUSTER), sorted(in_volume ^ set(CLUSTER))
    # 旧模块里只剩**指路注释**（不是实现）：读者找 `_volume_topup` 时不会两手空空。
    core_src = CORE_PY.read_text(encoding="utf-8")
    assert "已整体搬到" in core_src and "rl/loop_volume.py::TrainingVolume" in core_src
    for name in CLUSTER:
        assert f"def {name}" not in core_src, name


def test_wiring_is_by_object_identity_not_copies() -> None:
    """`TrainingLoop.X is TrainingVolume.X`（同一个函数对象，不是同名副本）。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_volume import TrainingVolume

    for name in CLUSTER:
        assert getattr(TrainingLoop, name) is getattr(TrainingVolume, name), name


def test_composition_is_on_the_caller_side() -> None:
    """组装在**调用者一侧**：`RoundSteps(TrainingVolume)`；第一个判据逐项均成立。

    2026-09-25（S4 第十九/二十刀）**演进登记**：组合类元组尾多了 `TrainingLifecycle`；
    `RoundSteps` 又在**末位**长了三个轮内实现混入（S4 第二十刀）。旧一刀钉的把心仍逐字成立：
    本簇是 `RoundSteps.__bases__` 的**第一位**、`__mro__[1]` 就是它（= 追加不插队）。
    **逐字元组与全量 MRO 名单的唯一所有者**是 `test_loop_core_tail_split.py`（本刀收尾的那份）；
    本用例只钉与本簇有关的结构关系，免得同一事实在三个文件里各写一份、各自漂。
    """
    from rl.loop_core import TrainingLoop
    from rl.loop_guards import TrainingGuards
    from rl.loop_lifecycle import TrainingLifecycle
    from rl.loop_remote import TrainingRemote
    from rl.loop_round_steps import RoundSteps
    from rl.loop_steps import TrainingEval, TrainingSteps
    from rl.loop_volume import TrainingVolume

    assert RoundSteps.__bases__[0] is TrainingVolume
    assert RoundSteps.__mro__[1] is TrainingVolume
    # 判据：走调用者一侧 ⇒ 组合类与四个「继承真混入」的测试宿主一行不改。
    assert TrainingLoop.__bases__ == (
        RoundSteps,
        TrainingSteps,
        TrainingGuards,
        TrainingLifecycle,
    )
    assert TrainingVolume not in TrainingLoop.__bases__
    assert TrainingLifecycle not in RoundSteps.__bases__
    # 相对位置（两簇各自必须压在被它取代的那些实现之前）：本簇仍在 RoundSteps 的第一位。
    assert RoundSteps.__mro__[1] is TrainingVolume
    assert TrainingVolume not in TrainingSteps.__mro__
    # 方向对得上：**生产**入口全在 RoundSteps（它才是调用者）
    round_src = ROUND_STEPS_PY.read_text(encoding="utf-8")
    for name in ("_iteration_pairs", "_volume_active", "_volume_collect_continuous"):
        assert f"self.{name}(" in round_src, f"RoundSteps 不再调 {name}——方向判断失效"
        assert f"def {name}" not in round_src, f"{name} 被就地重新定义在 RoundSteps 里了"
    # `_volume_topup`（离散补波）自 VOLUME_RULE_V2 起已退役：`step_volume_topup` 只是
    # `STEP_ORDER` 要求的**空步**，生产路径不调本簇任何方法——它只由既有 e2e/单测以 unbound
    # 形式驱动。这条同时挡住「把退役规则又接回生产路径」而没人注意。
    assert "self._volume_topup(" not in round_src
    topup_step = _methods(ROUND_STEPS_PY, "RoundSteps")["step_volume_topup"]
    topup_src = ast.get_source_segment(round_src, topup_step)
    assert topup_src is not None and topup_src.rstrip().endswith("return None")


# ─────────────────────────────── 状态面 ───────────────────────────────


def test_volume_slots_are_declared_in_the_new_home() -> None:
    """七个槽位在 `TrainingVolume` 声明；`TrainingSteps` 的三处**有意并存**（并集闭合）。"""
    from rl.loop_steps import TrainingSteps
    from rl.loop_volume import TrainingVolume

    declared_here = _declared(VOLUME_PY, "TrainingVolume")
    for slot in CLUSTER_SLOTS:
        assert slot in declared_here, slot
        assert slot in TrainingVolume.__annotations__, slot
    # sibling mixin（TrainingSteps）继承不到 TrainingVolume ⇒ 它读的三个必须自己声明。
    sibling = set(_declared(STEPS_PY, "TrainingSteps")) & set(CLUSTER_SLOTS)
    assert sorted(sibling) == sorted(SIBLING_DECLARED), sorted(sibling)
    assert sorted(set(TrainingSteps.__annotations__) & set(CLUSTER_SLOTS)) == sorted(
        SIBLING_DECLARED
    )


def test_host_still_initializes_the_slots() -> None:
    """`TrainingLoop.__init__` 仍**赋值**全部七个槽位（跨轮字段的持有者不变）。"""
    init = _methods(CORE_PY, "TrainingLoop")["__init__"]
    writes = {
        n.attr
        for n in ast.walk(init)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }
    assert set(CLUSTER_SLOTS) <= writes, sorted(set(CLUSTER_SLOTS) - writes)


def test_cross_module_hands_are_the_declared_ones() -> None:
    """除申报的两处外，无人碰 volume 槽位——否则状态归属会悄悄裂成「谁都能写」。"""
    found: set[str] = set()
    for path, cls in (
        (CORE_PY, "TrainingLoop"),
        (STEPS_PY, "TrainingSteps"),
        (NN_ROOT / "rl" / "loop_remote.py", "TrainingRemote"),
        (NN_ROOT / "rl" / "loop_guards.py", "TrainingGuards"),
        (ROUND_STEPS_PY, "RoundSteps"),
    ):
        for fn in _methods(path, cls).values():
            for attr, roles in _self_attr_roles(fn).items():
                if attr in CLUSTER_SLOTS:
                    for role in roles:
                        found.add(f"{fn.name}|{attr}|{role}")
    assert found == set(CROSS_MODULE_HANDS), sorted(found ^ set(CROSS_MODULE_HANDS))


# ─────────────────────────────── 依赖面 ───────────────────────────────


def test_top_level_import_surface_is_closed() -> None:
    """顶层 import 面闭合：本模块不许长出重依赖（重依赖留在调用方或延迟 import）。"""
    got = _top_level_imports(VOLUME_PY)
    assert got <= TOP_LEVEL_ALLOWED, sorted(got - TOP_LEVEL_ALLOWED)


def test_lazy_targets_stay_lazy() -> None:
    """`rl.volume_waves` / `rl.volume_quota` 只许方法体内延迟 import（原有的 DI 面）。"""
    top = _top_level_imports(VOLUME_PY)
    inside = _in_function_imports(VOLUME_PY)
    for mod in LAZY_ONLY:
        assert mod not in top, f"{mod} 被提到顶层了——纯逻辑面会跟着进 import 图"
        assert mod in inside, f"{mod} 的延迟 import 不见了"


def test_loop_volume_does_not_import_the_hosts() -> None:
    """不得成环：新家不许 import `rl.loop_core` / `rl.loop_steps` / `rl.loop_round_steps`。"""
    got = _top_level_imports(VOLUME_PY) | _in_function_imports(VOLUME_PY)
    for bad in ("rl.loop_core", "rl.loop_steps", "rl.loop_round_steps"):
        assert bad not in got, bad


# ─────────────────────────────── 功能性 ───────────────────────────────


def test_log_seam_lives_here_not_in_loop_core(monkeypatch) -> None:
    """★ 本刀的题眼：`_volume_topup` 的日志按**本模块**的 `log` 解析。

    方法一搬走，`monkeypatch.setattr(rl.loop_core, "log", …)` 就成了**静默空操作**（同名 seam
    在两个命名空间里是两个各自真实的注入点——S4 第二步的教训）。这里把两个方向都钉住：
    打在本模块 → 命中；打在 `rl.loop_core` → 一个字节都收不到。
    """
    import rl.loop_core as core_mod
    import rl.loop_volume as vol_mod

    # 真的 mixin 实例（不是 SimpleNamespace）：`_volume_topup` 内部要 `self._volume_active()`，
    # 而那条只能经真类解析——这本身就说明「这一簇是一个整体」。
    host = vol_mod.TrainingVolume()
    host.args = SimpleNamespace(target_transitions=100)
    host._stream_meta = {"stream": True}  # 流式轮：v1 不补波，走一条只写日志就返回的分支

    seen: list[str] = []
    monkeypatch.setattr(vol_mod, "log", lambda m: seen.append(str(m)))
    core_seen: list[str] = []
    monkeypatch.setattr(core_mod, "log", lambda m: core_seen.append(str(m)))

    vol_mod.TrainingVolume._volume_topup(host, 1, None)
    assert seen and "流式路径" in seen[0], seen
    assert core_seen == [], f"补丁打在了旧命名空间上却仍有输出：{core_seen}"


def test_unbound_lookup_through_the_composition_class_still_works() -> None:
    """★ 既有用例的形态：`TrainingLoop._volume_active(cast(Any, stub))` —— 经 MRO 取真实现。"""
    from rl.loop_core import TrainingLoop

    off = cast(Any, SimpleNamespace(args=SimpleNamespace(target_transitions=0)))
    on = cast(Any, SimpleNamespace(args=SimpleNamespace(target_transitions=7)))
    assert TrainingLoop._volume_active(off) is False
    assert TrainingLoop._volume_active(on) is True
    # 关集解析缺席/坏值 → 响亮 SystemExit（不是静默取一个关集）
    assert TrainingLoop._volume_stages(
        cast(Any, SimpleNamespace(args=SimpleNamespace(stages="0-3", curriculum_stages="", rotate_stages=0)))
    ) == [0, 1, 2, 3]
