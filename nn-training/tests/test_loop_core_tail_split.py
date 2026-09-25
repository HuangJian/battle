"""拆分的**契约守卫**：组合根收尾（S4 第二十刀，2026-09-25）。

## 这一刀切了什么

`rl/loop_core.py` **446 → 240 行**：余下的 7 个方法都是**叶子**（类内零互调），按**判据同源**
分成三簇，各自成模块：

| 新家 | 成员 | 判据（谁调它 / 同源在哪） |
|---|---|---|
| `rl/loop_baseline.py::TrainingBaseline` | `_baseline_eval_weights` · `_maybe_dispatch_baseline_eval` | it0 基线（wver 指纹 + 落地摘）——`step_course_iter` |
| `rl/loop_iter_dir.py::TrainingIterDir` | `_prepare_iter_dir` · `_check_quota_incident` | **`self._traj_dir` 里有没有本轮的活**——`step_prepare_iter` / `step_course_iter` |
| `rl/loop_dispatch.py::TrainingDispatch` | `_rollout_phase` · `_eval_on_round` · `_evalboard_yield` | **本轮把活派给谁 / 让位给谁**——`step_rollout` / `step_eval_dispatch` / `step_record_iteration` |

组合根余下 **只有** `__init__`（槽位持有者）与 `_run_inspect`（见下）。

## 宿主：三簇都挂 `RoundSteps` 一侧（而不动组合根）

mixin 级父调用者**全部**是 `RoundSteps`（`_eval_on_round` 另有 `TrainingEval` /
`TrainingGuards` 两个 sibling 调用者，但三者**互不继承**也不碍事——本簇不必挂组合根，因为
`RoundSteps` 是组合根的**第一个基类**，挂在它的基类里就已经在 `TrainingLoop` 的线性化里了）。
方向仍是本仓规则「调用者依赖被调用者」⇒ `class RoundSteps(TrainingVolume, TrainingBaseline,
TrainingIterDir, TrainingDispatch)`，`TrainingLoop.__bases__` **一行不改**。

⚠ **`_eval_on_round` 的顺序契约**（本刀唯一的真约束）：`rl/loop_eval.py` 的同名成员是**占位**
（body `raise`，MRO 被改坏就响亮失败），真实现必须在**线性化里更靠前**。挂 `RoundSteps` 一侧
天然满足（`TrainingDispatch` 的位置早于 `TrainingEval`）；若把本簇改成组合根的**末位**基类，
占位会反过来胜出 ⇒ `test_eval_on_round_position_contract` 钉住。

## 组合根的**结构性例外**：`_run_inspect` + `run_inspect` 必须同住 loop_core

`_run_inspect` 按**模块全局**解析 `run_inspect`（那是文档化的可替换点，`rl/loop.py` 再导出
它）。两者必须同住一个模块，而那个模块**不能** import `rl.loop_core`（loop_core import 它当
基类 ⇒ 成环）⇒ 只能在 `loop_core`。所以「组合根 = 纯组合类」在本仓的答案是**是，但留一个方法**：
`__init__` + `_run_inspect`。这条被 `test_composition_root_keeps_only_the_structural_pair` 钉住。

## 本文件还是**全量 MRO 名单与 `RoundSteps.__bases__` 的唯一所有者**

S18/S19 的守卫各自钉过一份（两处逐字重复、会各自漂）⇒ 本刀收尾时把这两条**汇总到这里**，
那两处改成只钉相对位置（本簇在首位 / 本簇在场）。

## 本文件钉住什么

1. 三簇的成员**定义**只在新家（`TrainingLoop` / `RoundSteps` 里零同名定义）；
2. 接线是**对象级**同一（`TrainingLoop.X is <簇>.X`）；
3. 组装逐字：`RoundSteps.__bases__` 四件套 + `TrainingLoop.__bases__` 四件套 + **全量 MRO 名单**；
4. 组合根的方法闭集**恰好** `{__init__, _run_inspect}`；
5. 三簇的**借用声明**闭集 = 派生集（同 `rl/loop_volume.py` 的约定），且类体零带值槽位；
6. **入边闭集**（谁以 `self.` 调本簇成员）+ **出边为空**（三簇互不调兄弟方法）
   + **槽位读者**（本刀三簇恰各一处）与**写手唯一**（赋值点只许 `loop_lifecycle._setup_common`）；
7. 顶层 import 闭集 / 禁反向 import / DI seam 只许方法体内延迟 import；
8. ★ **功能性**：`_check_quota_incident` 真跑（含 `log` seam 在本模块 = 打 `rl.loop_core.log` 收不到）
   · `_eval_on_round` 位置契约（真实现压住占位；裸 `TrainingEval` 调它**响亮报错**）
   · 旧家不再吸收 patch（搬走的名字在旧家**不存在** ⇒ 陈旧 patch 响亮失败）。
"""

from __future__ import annotations

import ast
from pathlib import Path

NN_ROOT = Path(__file__).resolve().parent.parent
CORE_PY = NN_ROOT / "rl/loop_core.py"
ROUND_STEPS_PY = NN_ROOT / "rl/loop_round_steps.py"
LIFE_PY = NN_ROOT / "rl/loop_lifecycle.py"
EVAL_PY = NN_ROOT / "rl/loop_eval.py"

#: 三簇：新家路径 → (类名, 成员闭集)。
CLUSTERS = {
    "rl/loop_baseline.py": ("TrainingBaseline", ("_baseline_eval_weights", "_maybe_dispatch_baseline_eval")),
    "rl/loop_iter_dir.py": ("TrainingIterDir", ("_check_quota_incident", "_prepare_iter_dir")),
    "rl/loop_dispatch.py": ("TrainingDispatch", ("_eval_on_round", "_evalboard_yield", "_rollout_phase")),
}

#: 组合根**只许**留这两个成员（`__init__` = 槽位持有者；`_run_inspect` = 结构性例外）。
STAYS = ("__init__", "_run_inspect")

#: 组装（本文件是这两条的唯一所有者）：`RoundSteps` 的基类元组与全量 MRO 名单。
#: 2026-09-25（S4 第二十一刀）：`TrainingSteps` 末位追加 `TrainingExport` ⇒ 全量 MRO 名单里
#: 在 `TrainingEval` 之后插入一项（`TrainingSteps.__bases__` 那三处断言由各刀的守卫自己演进——
#: 见 `tests/test_loop_transport_split.py` / `test_loop_eval_split.py` / `test_loop_lifecycle_split.py`）。
ROUND_STEPS_BASES = ("TrainingVolume", "TrainingBaseline", "TrainingIterDir", "TrainingDispatch")
#: `TrainingLoop` 的**全量 MRO 名单**（本文件是这份名单的**唯一所有者**；S18/S19/S20 那几处
#: 只钉相对位置，避开同一份名单三处各自漂）。S4 第二十二刀把 `TrainingRemote` 变成组合根
#: （`Push ← Job ← {Fail, Job} ← Drive`）⇒ 四个新混入插在 `TrainingRemote` 之后、`TrainingEval` 之前。
MRO_NAMES = [
    "TrainingLoop",
    "RoundSteps",
    "TrainingVolume",
    "TrainingBaseline",
    "TrainingIterDir",
    "TrainingDispatch",
    "TrainingSteps",
    "TrainingRemote",
    "TrainingRemoteDrive",
    "TrainingRemoteFail",
    "TrainingRemoteJob",
    "TrainingRemotePush",
    "TrainingEval",
    "TrainingExport",
    "TrainingGuards",
    "TrainingLifecycle",
    "object",
]
TRAINING_LOOP_BASES = ("RoundSteps", "TrainingSteps", "TrainingGuards", "TrainingLifecycle")

#: 入边闭集：成员 → {模块名: 呼叫点数}（mixi 级 `self.<成员>(`；新入边必须改这张表）。
INBOUND_CALLS = {
    "_check_quota_incident": {"loop_round_steps.py": 1},
    "_prepare_iter_dir": {"loop_round_steps.py": 1},
    "_rollout_phase": {"loop_round_steps.py": 1},
    "_eval_on_round": {"loop_eval.py": 1, "loop_guards.py": 1, "loop_round_steps.py": 1},
    "_evalboard_yield": {"loop_round_steps.py": 1},
    "_maybe_dispatch_baseline_eval": {"loop_round_steps.py": 1},
}

#: 槽位读者闭集（S4 第十九刀那条表移交过来的）：槽位 → **本刀三簇里**的读者。
#: 写手另有所有权断言（`test_slot_writer_is_unique`：赋值点只许在 `loop_lifecycle._setup_common`）。
SLOT_READERS = {
    "_course_fp": {
        "rl/loop_iter_dir.py": "_prepare_iter_dir",
        "rl/loop_dispatch.py": "_rollout_phase",
    },
    "_corpus_fp": {
        "rl/loop_iter_dir.py": "_prepare_iter_dir",
        "rl/loop_dispatch.py": "_rollout_phase",
    },
}

#: 顶层 import 闭集（非 stdlib）：本模块不许长出重依赖。
TOP_IMPORTS = {
    "rl/loop_baseline.py": frozenset({"dist_common", "rl.log", "rl.queue"}),
    "rl/loop_iter_dir.py": frozenset(
        {"dist_common", "platform_utils", "rl.collect_only", "rl.log", "rl.resume"}
    ),
    "rl/loop_dispatch.py": frozenset({"rl.rollout_phase"}),
}
STDLIB_IMPORTS = frozenset({"pathlib", "typing"})

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "rl.loop_core",
        "rl.loop_eval",
        "rl.loop_lifecycle",
        "rl.loop_remote",
        "rl.loop_round_steps",
        "rl.loop_steps",
        "rl.loop_volume",
    }
)

#: 登记在案的**占位**（`_eval_on_round` 的真实现必须在 MRO 里更靠前，见头注）。
PLACEHOLDERS: dict[str, tuple[str, ...]] = {"_eval_on_round": ("loop_eval.py",)}

#: 搬走后旧家**不再持有**的模块全局（陈旧 patch 会响亮 AttributeError，而不是静默空操作）。
GONE_FROM_CORE = (
    "dist_common",
    "rmtree_best_effort",
    "precollect_snapshot_wver",
    "completed_pairs",
    "dispatch_rollout_phase",
    "RUN_ID",
)


def _cls(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)


def _methods(path: Path, cls_name: str) -> dict[str, ast.FunctionDef]:
    return {m.name: m for m in _cls(path, cls_name).body if isinstance(m, ast.FunctionDef)}


def _top_names(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return out


def _self_call_counts(path: Path) -> dict[str, int]:
    """AST 计数源码里**真实的** `self.<attr>(…)` 调用。

    为什么不用 `src.count("self.x(")`（S17~S19 的老写法）：文档字符串/注释里提到一次
    调用形态就会被算成**一条入边**，守卫于是对着「合法的文档」报假红（本刀的 `_maybe_dispatch_baseline_eval`
    头注恰好写了自己的调用形态）。入边是**语法事实**，就该用语法量。
    """
    out: dict[str, int] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            out[node.func.attr] = out.get(node.func.attr, 0) + 1
    return out


def _defined_names(path: Path) -> set[str]:
    """源码里**会占住名字**的定义：顶层 def/class + 顶层类体的方法（文档字符串不算）。"""
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            if isinstance(n, ast.ClassDef):
                out.update(
                    m.name
                    for m in n.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
    return out


def _top_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def _self_assigns(path: Path, cls_name: str) -> dict[str, set[str]]:
    """方法 → 它**赋值**的 `self.<attr>` 集合（只看赋值目标，不看读取）。"""
    out: dict[str, set[str]] = {}
    for name, node in _methods(path, cls_name).items():
        got: set[str] = set()
        for n in ast.walk(node):
            targets: list[ast.expr] = []
            if isinstance(n, ast.Assign):
                targets = list(n.targets)
            elif isinstance(n, ast.AnnAssign):
                targets = [n.target]
            for t in targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                ):
                    got.add(t.attr)
        out[name] = got
    return out


def _self_slots(path: Path, cls_name: str) -> dict[str, set[str]]:
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


def _self_calls(path: Path, cls_name: str, method: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_methods(path, cls_name)[method]):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            out.add(node.func.attr)
    return out


# ─────────────────────────────── 定义与接线 ───────────────────────────────


def test_members_live_in_the_new_homes_only() -> None:
    """三簇成员定义只在新家；旧家（`TrainingLoop` / `RoundSteps`）零同名定义。"""
    for rel, (cls, members) in CLUSTERS.items():
        path = NN_ROOT / rel
        got = set(_methods(path, cls))
        assert got == set(members), f"{rel}: {sorted(got ^ set(members))}"
    core = set(_methods(CORE_PY, "TrainingLoop"))
    steps = set(_methods(ROUND_STEPS_PY, "RoundSteps"))
    for _, members in CLUSTERS.values():
        assert sorted(set(members) & core) == []
        assert sorted(set(members) & steps) == []


def test_wiring_is_object_identity() -> None:
    """`TrainingLoop.X is <簇>.X`（同一个函数对象，不是同名副本）。"""
    import rl.loop_baseline as bl
    import rl.loop_core as core_mod
    import rl.loop_dispatch as dp
    import rl.loop_iter_dir as lid

    homes = {"rl/loop_baseline.py": bl, "rl/loop_iter_dir.py": lid, "rl/loop_dispatch.py": dp}
    for rel, (cls, members) in CLUSTERS.items():
        home = getattr(homes[rel], cls)
        for name in members:
            got = getattr(core_mod.TrainingLoop, name)
            assert got is getattr(home, name), name
            assert got.__module__ == rel[: -len(".py")].replace("/", "."), name


def test_composition_is_exact() -> None:
    """组装逐字对账（本文件是唯一所有者）：`RoundSteps.__bases__` + 全量 MRO 名单。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_round_steps import RoundSteps

    assert tuple(c.__name__ for c in RoundSteps.__bases__) == ROUND_STEPS_BASES
    assert tuple(c.__name__ for c in TrainingLoop.__bases__) == TRAINING_LOOP_BASES
    assert [c.__name__ for c in TrainingLoop.__mro__] == MRO_NAMES
    # 方向：本刀的三簇**不是**组合类的直接基类（挂的是调用者一侧）。
    for name in ROUND_STEPS_BASES[1:]:
        assert name not in TRAINING_LOOP_BASES


def test_composition_root_keeps_only_the_structural_pair() -> None:
    """★ 组合根的方法闭集**恰好** `{__init__, _run_inspect}`——本刀的目标，正面钉住。"""
    got = sorted(_methods(CORE_PY, "TrainingLoop"))
    assert got == sorted(STAYS), f"组合根多/少了方法：{got}"
    # `_run_inspect` 的模块级搭档必须同住（结构性例外的原因见头注）。
    top = _top_names(CORE_PY)
    assert {"run_inspect"} <= top
    assert "run_inspect" not in _top_names(LIFE_PY)


def test_borrowed_declarations_are_exactly_the_touched_set() -> None:
    """三簇的借用声明闭集 = 派生集（碰到的、不属于本模块的名字）；类体零带值槽位。"""
    for rel, (cls, _) in CLUSTERS.items():
        path = NN_ROOT / rel
        touched: set[str] = set()
        for names in _self_slots(path, cls).values():
            touched |= names
        borrowed = {
            n for n in touched - set(_methods(path, cls)) - _top_names(path) if not n.startswith("__")
        }
        assert borrowed, f"{rel}: 派生集为空说明重建坏了"
        from importlib import import_module

        mod = import_module(rel[: -len(".py")].replace("/", "."))
        assert set(getattr(mod, cls).__annotations__) == borrowed, rel
        # 类体零带值槽位：**声明块**只许写注解（`x: T`），不许写 `x: T = ...` 或 `x = ...`。
        body = _cls(path, cls).body
        assert not [n for n in body if isinstance(n, ast.Assign)], rel
        assert not [n for n in body if isinstance(n, ast.AnnAssign) and n.value is not None], rel


# ─────────────────────── 入边 / 出边 / 槽位（三张表） ───────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边闭集：每个成员只有登记的调用者，且呼叫点数逐一对账。"""
    rl_dir = NN_ROOT / "rl"
    files = sorted(rl_dir.glob("*.py"))
    calls = {p.name: _self_call_counts(p) for p in files}
    defined = {p.name: _defined_names(p) for p in files}
    owners = {m: rel.split("/")[-1] for rel, (_, members) in CLUSTERS.items() for m in members}
    for member, want in INBOUND_CALLS.items():
        got = {name: c[member] for name, c in calls.items() if member in c}
        assert got == want, f"{member} 的入边变了：{got}"
        # 定义面唯一：只有持有者定义它，**加上**登记在案的占位（再多一份 = 遮罩/漂移）。
        definers = {name for name, names in defined.items() if member in names}
        want_definers = {owners[member]} | set(PLACEHOLDERS.get(member, ()))
        assert definers == want_definers, f"{member} 的定义面：{sorted(definers)}"


def test_outbound_hands_are_empty() -> None:
    """出边为空：三簇成员不调任何兄弟混入的方法（它们只读写自己的槽位）。"""
    for rel, (cls, members) in CLUSTERS.items():
        path = NN_ROOT / rel
        own = set(_methods(path, cls))
        for name in members:
            ext = {
                c
                for c in _self_calls(path, cls, name)
                if c not in own and not c.startswith("__")
            }
            assert ext == set(), f"{rel}:{name} 调了兄弟方法 {sorted(ext)}"


def test_slot_readers_closed_set() -> None:
    """槽位读者闭集（自 S4 第十九刀移交）：`_course_fp` / `_corpus_fp` 在本刀三簇里各恰一处。"""
    for slot, want in SLOT_READERS.items():
        got: dict[str, str] = {}
        for rel in CLUSTERS:
            path = NN_ROOT / rel
            cls = CLUSTERS[rel][0]
            for m, names in _self_slots(path, cls).items():
                if slot in names:
                    got[rel] = m
        assert got == want, f"{slot} 的读者变了：{got}"


def test_slot_writer_is_unique() -> None:
    """★ 写手唯一：这两个槽位**只在** `loop_lifecycle._setup_common` 被赋值（读≠写）。

    为什么单列：读者侧随手一改就会把「谁拥有这份状态」的账翻错（本刀三簇是纯读者）。
    只数「谁碰到这个名字」不够——`loop_lifecycle.run_one_round` 也**读** `_course_fp`
    （S4 第十九刀的守卫曾用这个宽松口径，反探针 ⑬ 实测抓不住改名写手）。
    """
    assigns = _self_assigns(LIFE_PY, "TrainingLifecycle")
    for slot in SLOT_READERS:
        assert {m for m, names in assigns.items() if slot in names} == {"_setup_common"}, slot


def test_top_level_imports_closed_no_reverse_edges() -> None:
    """顶层 import 闭集 + 禁反向边。"""
    for rel, want in TOP_IMPORTS.items():
        got = _top_imports(NN_ROOT / rel)
        assert got & FORBIDDEN_IMPORTS == set(), f"{rel} 长出反向边：{got & FORBIDDEN_IMPORTS}"
        assert got >= want, f"{rel} 少了：{want - got}"
        extra = got - want - STDLIB_IMPORTS - {"__future__"}
        assert extra == set(), f"{rel} 顶层 import 面长出：{sorted(extra)}"


# ─────────────────────────────── ★ 功能性 ───────────────────────────────


def test_quota_incident_runs_from_the_new_home(tmp_path, monkeypatch) -> None:
    """★ `_check_quota_incident` 真跑：`log` seam 在**本模块**（打旧家收不到）。"""
    import types
    from typing import Any, cast

    import rl.loop_core as core_mod
    import rl.loop_iter_dir as lid

    seen: list[str] = []
    core_seen: list[str] = []
    monkeypatch.setattr(lid, "log", lambda m: seen.append(str(m)))
    monkeypatch.setattr(core_mod, "log", lambda m: core_seen.append(str(m)))  # 旧家：收不到

    def _stub(node_rollout: bool) -> Any:
        return types.SimpleNamespace(
            _traj_dir=tmp_path, _node_rollout=node_rollout, _zero_shard_streak=0,
            args=types.SimpleNamespace(course_name="probe"),
        )

    obj = _stub(False)
    cast(Any, lid.TrainingIterDir._check_quota_incident)(obj, 7)  # 第 1 轮：零 shard，计数 1
    assert obj._zero_shard_streak == 1
    cast(Any, lid.TrainingIterDir._check_quota_incident)(obj, 8)  # 第 2 轮：响亮告警
    assert obj._zero_shard_streak == 2
    assert any("quota" in m for m in seen), seen
    assert core_seen == [], "打 rl.loop_core.log 竟收到了——seam 的落点不对"
    # 上云轮：本地零 shard 是**预期** ⇒ 不喊、计数归零
    node = _stub(True)
    node._zero_shard_streak = 5
    cast(Any, lid.TrainingIterDir._check_quota_incident)(node, 9)
    assert node._zero_shard_streak == 0
    assert not any("quota" in m for m in seen[1:])


def test_eval_on_round_position_contract() -> None:
    """★ 顺序契约：真实现压住 `TrainingEval` 的占位；占位被直接调用时**响亮报错**。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_dispatch import TrainingDispatch
    from rl.loop_eval import TrainingEval

    assert TrainingLoop._eval_on_round is TrainingDispatch._eval_on_round
    assert TrainingLoop._eval_on_round is not TrainingEval._eval_on_round
    mro = [c.__name__ for c in TrainingLoop.__mro__]
    assert mro.index("TrainingDispatch") < mro.index("TrainingEval"), "占位会反过来胜出"
    # 占位的语义：MRO 被改坏时必须响亮失败，而不是静默返回 falsy 把 eval 全关掉。
    probe = object.__new__(TrainingEval)
    try:
        TrainingEval._eval_on_round(probe, 1)
    except Exception:
        pass
    else:  # pragma: no cover - 失败分支
        raise AssertionError("占位没有响亮报错")


def test_old_home_no_longer_absorbs_patches() -> None:
    """★ 旧家不再吸收 patch：搬走的模块全局在 `rl.loop_core` 里**不存在**（响亮 AttributeError）。"""
    import rl.loop_baseline as bl
    import rl.loop_core as core_mod
    import rl.loop_dispatch as dp
    import rl.loop_iter_dir as lid

    for name in GONE_FROM_CORE:
        assert not hasattr(core_mod, name), f"rl.loop_core.{name} 还在——陈旧 patch 会静默失效"
    # 新家各自持有自己真正用到的那个（= 真注入点）。
    assert hasattr(bl, "RUN_ID")
    assert hasattr(lid, "completed_pairs") and hasattr(lid, "precollect_snapshot_wver")
    assert hasattr(dp, "dispatch_rollout_phase")
    assert hasattr(lid, "log") and hasattr(bl, "log")
