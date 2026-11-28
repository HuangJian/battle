"""拆分的**契约守卫**：组合根收尾（S4 第二十刀，2026-09-25）。

## 这一刀切了什么

`trainer/loop_core.py` **446 → 240 行**：余下的 7 个方法都是**叶子**（类内零互调），按**判据同源**
分成三簇，各自成模块：

| 新家 | 成员 | 判据（谁调它 / 同源在哪） |
|---|---|---|
| `trainer/loop_baseline.py::TrainingBaseline` | `_baseline_eval_weights` · `_maybe_dispatch_baseline_eval` | it0 基线（wver 指纹 + 落地摘）——`step_course_iter` |
| `trainer/loop_iter_dir.py::IterWorkspace` | `prepare` · `check_quota`（组合根留一行转发壳 `_prepare_iter_dir` / `_check_quota_incident`） | **本轮目录里有没有本轮的活**——`step_prepare_iter` / `step_course_iter` |
| `trainer/loop_dispatch.py::TrainingDispatch` | `_rollout_phase` · `_eval_on_round` · `_evalboard_yield` | **本轮把活派给谁 / 让位给谁**——`step_rollout` / `step_eval_dispatch` / `step_record_iteration` |

组合根余下 **只有** `__init__`（槽位持有者）与 `_run_inspect`（见下）。

## 宿主：三簇都挂 `RoundSteps` 一侧（而不动组合根）

mixin 级父调用者**全部**是 `RoundSteps`（`_eval_on_round` 另有 `TrainingEval` /
`TrainingGuards` 两个 sibling 调用者，但三者**互不继承**也不碍事——本簇不必挂组合根，因为
`RoundSteps` 是组合根的**第一个基类**，挂在它的基类里就已经在 `TrainingLoop` 的线性化里了）。
方向仍是本仓规则「调用者依赖被调用者」⇒ `class RoundSteps(TrainingVolume, TrainingBaseline,
TrainingIterDir, TrainingDispatch)`，`TrainingLoop.__bases__` **一行不改**。

⚠ **`_eval_on_round` 的顺序契约**（本刀唯一的真约束）：`trainer/loop_eval.py` 的同名成员是**占位**
（body `raise`，MRO 被改坏就响亮失败），真实现必须在**线性化里更靠前**。挂 `RoundSteps` 一侧
天然满足（`TrainingDispatch` 的位置早于 `TrainingEval`）；若把本簇改成组合根的**末位**基类，
占位会反过来胜出 ⇒ `test_eval_on_round_position_contract` 钉住。

## 组合根的**结构性例外**：`_run_inspect` + `run_inspect` 必须同住 loop_core

`_run_inspect` 按**模块全局**解析 `run_inspect`（那是文档化的可替换点，`trainer/loop.py` 再导出
它）。两者必须同住一个模块，而那个模块**不能** import `trainer.loop_core`（loop_core import 它当
基类 ⇒ 成环）⇒ 只能在 `loop_core`。所以「组合根 = 纯组合类」在本仓的答案是**是，但留一个方法**：
`__init__` + `_run_inspect`。这条被 `test_composition_root_keeps_only_the_structural_pair` 钉住。

## 本文件还是**全量 MRO 名单与 `RoundSteps.__bases__` 的唯一所有者**

S18/S19 的守卫各自钉过一份（两处逐字重复、会各自漂）⇒ 本刀收尾时把这两条**汇总到这里**，
那两处改成只钉相对位置（本簇在首位 / 本簇在场）。

## 本文件钉住什么

1. 三簇的成员**定义**只在新家（`TrainingLoop` / `RoundSteps` 里零同名定义）；
2. 仍是混入的簇接线是**对象级**同一（`TrainingLoop.X is <簇>.X`）；已改协作对象的簇
   （`IterWorkspace`，P0-1 第一刀）则由组合根上**恰好一行**的转发壳委派，装配在 `__init__`；
3. 组装逐字：`RoundSteps.__bases__` + `TrainingLoop.__bases__` 四件套 + **全量 MRO 名单**；
4. 组合根的方法闭集**恰好** `{__init__, _run_inspect}`；
5. 三簇的**借用声明**闭集 = 派生集（同 `trainer/loop_volume.py` 的约定），且类体零带值槽位；
6. **入边闭集**（谁以 `self.` 调本簇成员）+ **出边为空**（三簇互不调兄弟方法）
   + **槽位读者**（本刀三簇恰各一处）与**写手唯一**（赋值点只许 `loop_lifecycle._setup_common`）；
7. 顶层 import 闭集 / 禁反向 import / DI seam 只许方法体内延迟 import；
8. ★ **功能性**：`_check_quota_incident` 真跑（含 `log` seam 在本模块 = 打 `trainer.loop_core.log` 收不到）
   · `_eval_on_round` 位置契约（真实现压住占位；裸 `TrainingEval` 调它**响亮报错**）
   · 旧家不再吸收 patch（搬走的名字在旧家**不存在** ⇒ 陈旧 patch 响亮失败）。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

from tests.helpers import source_scan

NN_ROOT = Path(__file__).resolve().parents[2]
CORE_PY = NN_ROOT / "trainer/loop_core.py"
ROUND_STEPS_PY = NN_ROOT / "trainer/loop_round_steps.py"
LIFE_PY = NN_ROOT / "trainer/loop_lifecycle.py"
EVAL_PY = NN_ROOT / "trainer/loop_eval.py"

#: 已改成**协作对象**的簇（P0-1：第一刀 = 本轮目录，第二刀 = it0 基线）。
#: 模块 → (类名, 宿主上的属性名, 协议类名, 公开方法闭集, 组装后**归对象**的状态名, 壳表)。
#: 逻辑与状态都在对象里；组合根只留一行转发壳，装配在 `TrainingLoop.__init__`。
COLLABORATORS: dict[str, tuple[str, str, str, tuple[str, ...], set[str], dict[str, str]]] = {
    "trainer/loop_iter_dir.py": (
        "IterWorkspace",
        "iter_ws",
        "IterWorkspaceHost",
        ("check_quota", "prepare", "quota_state"),
        {"_zero_shard_streak"},
        {"_check_quota_incident": "check_quota", "_prepare_iter_dir": "prepare"},
    ),
    "trainer/loop_baseline.py": (
        "BaselineEval",
        "baseline",
        "BaselineHost",
        ("maybe_dispatch", "state", "weights"),
        {"_thread", "_landed_wver"},
        {"_maybe_dispatch_baseline_eval": "maybe_dispatch", "baseline_eval_state": "state"},
    ),
    "trainer/loop_export.py": (
        "BundleExporter",
        "exporter",
        "BundleHost",
        (
            "ensure_ts_code",
            "ensure_replay_converter",
            "volume_plan_block",
            "export_offline_bundle",
            "export_weights",
            "ts_code_view",
        ),
        {"_ts_code_sha256", "_ts_code_zip_path", "_replay_converter_sha256", "_replay_converter_path"},
        {
            "_ensure_ts_code": "ensure_ts_code",
            "_ensure_replay_converter": "ensure_replay_converter",
            "_export_offline_bundle": "export_offline_bundle",
            "_export_weights": "export_weights",
            "ts_code_view": "ts_code_view",
        },
    ),
    # P0-1 第四刀：派发与让位（无自有状态 —— 轮级槽位归组合根，结果经 `land_rollout` 落位）。
    "trainer/loop_dispatch.py": (
        "Dispatcher",
        "dispatch",
        "DispatchHost",
        ("eval_on_round", "evalboard_yield", "rollout_phase", "finalize_returned_shards"),
        set(),
        {
            "_eval_on_round": "eval_on_round",
            "_evalboard_yield": "evalboard_yield",
            "_rollout_phase": "rollout_phase",
            "_finalize_returned_shards": "finalize_returned_shards",
        },
    ),
}
#: 组合根的转发壳：壳名 → 协作对象的方法名（壳体必须**恰好**一行委派）。
SHELLS = {shell: target for spec in COLLABORATORS.values() for shell, target in spec[5].items()}

#: 组合根**只许**留这些成员：`__init__` 与五个**纯声明段**（`_init_*` 只做 `self.*` 赋值，
#: 2026-11-27 §385 按声明族切开）；`_run_inspect` 是结构性例外。
STAYS = (
    "__init__",
    "_run_inspect",
    "_init_core_state",
    "_init_volume_state",
    "_init_remote_state",
    "_init_eval_state",
    "_init_ledger_state",
    # P0-1（2026-11-28）：协作对象（`IterWorkspace` / `BaselineEval`）的宿主读面
    # （property，全部是**活**读数）与一字委派壳（形状由 `test_shells_delegate_to_the_collaborator` 钉住）。
    "traj_dir",
    "course_fp",
    "corpus_fp",
    "state_init",
    "node_rollout",
    "extra_wver",
    "eval_every",
    "_prepare_iter_dir",
    "_check_quota_incident",
    "_maybe_dispatch_baseline_eval",
    "baseline_eval_state",
    # P0-1 第三刀：产物出包协作对象的读面与壳。
    "traj_root",
    "jsonl_path",
    "rotate_seed",
    "remote_ppo",
    "_ensure_ts_code",
    "_ensure_replay_converter",
    "_export_offline_bundle",
    "_export_weights",
    "ts_code_view",
    # P0-1 第四刀：派发协作对象的读面、写面与四个壳。
    "start_it",
    "model",
    "opt",
    "device",
    "ref_model",
    "eval_at_set",
    "eb_window",
    "eb_thread",
    "payload_parts",
    "land_collect",
    "land_rollout",
    "_eval_on_round",
    "_evalboard_yield",
    "_rollout_phase",
    "_finalize_returned_shards",
)

#: 组装（本文件是这两条的唯一所有者）：`RoundSteps` 的基类元组与全量 MRO 名单。
#: 2026-09-25（S4 第二十一刀）：`TrainingSteps` 末位追加 `TrainingExport` ⇒ 全量 MRO 名单里
#: 在 `TrainingEval` 之后插入一项（`TrainingSteps.__bases__` 那三处断言由各刀的守卫自己演进——
#: 见 `tests/trainer/test_loop_transport_split.py` / `test_loop_eval_split.py` / `test_loop_lifecycle_split.py`）。
#: P0-1（2026-11-28）：`TrainingIterDir` / `TrainingBaseline` 不再是基类
#: ⇒ 两个协作对象（`IterWorkspace` / `BaselineEval`）不在这张表里。
ROUND_STEPS_BASES = ("TrainingVolume",)
#: `TrainingLoop` 的**全量 MRO 名单**（本文件是这份名单的**唯一所有者**；S18/S19/S20 那几处
#: 只钉相对位置，避开同一份名单三处各自漂）。S4 第二十二刀把 `TrainingRemote` 变成组合根
#: （`Push ← Job ← {Fail, Job} ← Drive`）⇒ 四个新混入插在 `TrainingRemote` 之后、`TrainingEval` 之前。
MRO_NAMES = [
    "TrainingLoop",
    "RoundSteps",
    "TrainingVolume",
    "TrainingSteps",
    "TrainingRemote",
    "TrainingRemoteDrive",
    "TrainingRemoteFail",
    "TrainingRemoteJob",
    "TrainingRemotePush",
    "TrainingEval",
    "TrainingGuards",
    # S4 第二十三刀把 `TrainingGuards` 的 13 个成员按判据同源切成四簇；组合根仍在原位，
    # 四簇作为它的基类紧随其后（互不调用、零重名 ⇒ 元组顺序惰性，但 MRO 名单是**全量**）。
    "TrainingGuardsTrip",
    "TrainingGuardsLeg",
    "TrainingGuardsGate",
    "TrainingGuardsSweep",
    "TrainingLifecycle",
    "object",
]
TRAINING_LOOP_BASES = ("RoundSteps", "TrainingSteps", "TrainingGuards", "TrainingLifecycle")

#: 入边闭集：成员 → {模块名: 呼叫点数}（mixi 级 `self.<成员>(`；新入边必须改这张表）。
INBOUND_CALLS = {
    "_check_quota_incident": {"loop_round_steps.py": 1},
    "_prepare_iter_dir": {"loop_round_steps.py": 1},
    "_rollout_phase": {"loop_round_steps.py": 1},
    # S4 第二十三刀：唯一的呼叫点 `self._eval_on_round(it)` 随 `_gate` 迁到 `loop_guards_gate.py`。
    "_eval_on_round": {"loop_eval.py": 1, "loop_guards_gate.py": 1, "loop_round_steps.py": 1},
    "_evalboard_yield": {"loop_round_steps.py": 1},
    "_finalize_returned_shards": {"loop_round_steps.py": 1},
    "_maybe_dispatch_baseline_eval": {"loop_round_steps.py": 1},
}

#: 槽位读者闭集（S4 第十九刀那条表移交过来的）：槽位 → **本刀三簇里**的读者。
#: 写手另有所有权断言（`test_slot_writer_is_unique`：赋值点只许在 `loop_lifecycle._setup_common`）。
#: 这两个槽位**归组合根独占**：协作对象只经宿主读面 `host.course_fp` / `host.corpus_fp` 取值
#: （协议声明，见 `test_collaborator_host_surface_is_declared`）⇒ 生产面零直接读者，写手唯一。
WRITE_OWNED_SLOTS = ("_course_fp", "_corpus_fp")

#: 顶层 import 闭集（非 stdlib）：本模块不许长出重依赖。
TOP_IMPORTS = {
    "trainer/loop_baseline.py": frozenset({"common.distribution", "common.log", "trainer.dispatch_queue"}),
    "trainer/loop_iter_dir.py": frozenset(
        {"common.distribution", "common.platform_utils", "trainer.collect_only", "common.log", "worker.resume"}
    ),
    # 2026-11-14：加 `common.log` —— `_evalboard_yield` 的心跳失败留痕（`warn_once`）。
    # 这是 L0 日志出口，同簇的 `loop_baseline` / `loop_iter_dir` 早已登记同一项；不是重依赖。
    "trainer/loop_dispatch.py": frozenset(
        {"common.log", "remote.payload_pack", "trainer.rollout_phase"}
    ),
}
STDLIB_IMPORTS = frozenset({"collections.abc", "dataclasses", "pathlib", "threading", "typing"})

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "trainer.loop_core",
        "trainer.loop_eval",
        "trainer.loop_lifecycle",
        "trainer.loop_remote",
        "trainer.loop_round_steps",
        "trainer.loop_steps",
        "trainer.loop_volume",
    }
)

#: 登记在案的**占位**（`_eval_on_round` 的真实现必须在 MRO 里更靠前，见头注）。
PLACEHOLDERS: dict[str, tuple[str, ...]] = {"_eval_on_round": ("loop_eval.py",)}

#: 搬走后旧家**不再持有**的模块全局（陈旧 patch 会响亮 AttributeError，而不是静默空操作）。
GONE_FROM_CORE = (
    "common.distribution",
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


def _self_call_counts(path: Path, only: frozenset[str] | None = None) -> Mapping[str, int]:
    """AST 计数源码里**真实的** `self.<attr>(…)` 调用。

    为什么不用 `src.count("self.x(")`（S17~S19 的老写法）：文档字符串/注释里提到一次
    调用形态就会被算成**一条入边**，守卫于是对着「合法的文档」报假红（本刀的 `_maybe_dispatch_baseline_eval`
    头注恰好写了自己的调用形态）。入边是**语法事实**，就该用语法量。

    实现搬进 `tests.helpers.source_scan`（缓存版）：本用例对两棵业务树跑两遍解析（入边 + 定义面）。

    `only` = 只关心这些名字（白名单）；入边闭集那条传它就走廉价子串预筛，与判据无关的
    文件直接跳过解析。
    """
    return source_scan.self_call_counts(str(path), only)


def _defined_names(path: Path, only: frozenset[str] | None = None) -> frozenset[str]:
    """源码里**会占住名字**的定义：顶层 def/class + 顶层类体的方法（文档字符串不算）。"""
    return source_scan.top_level_defs(str(path), only)


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
    """协作对象的实现只在新家；壳只在组合根；`RoundSteps` 里零同名定义。"""
    core = set(_methods(CORE_PY, "TrainingLoop"))
    steps = set(_methods(ROUND_STEPS_PY, "RoundSteps"))
    for rel, (cls, _attr, _proto, cnames, _owned, shells) in COLLABORATORS.items():
        home = set(_methods(NN_ROOT / rel, cls))
        assert set(cnames) <= home, f"{rel}: 少了 {sorted(set(cnames) - home)}"
        # 壳名允许与对象的方法同名（壳是一行委派）；其余实现不许在组合根再有一份。
        assert sorted((set(cnames) - set(shells)) & core) == [], (
            "协作对象的方法不许在组合根再定义一份"
        )
        assert sorted(set(cnames) & steps) == [], f"{rel}: 实现又住回 RoundSteps"
        # 壳名与对象方法同名（`ts_code_view`）不算「实现又住回家」。
        assert sorted(set(shells) - set(cnames) & home) == [], f"{rel}: 转发壳只许住组合根"
        assert sorted(set(shells) - core) == [], f"组合根缺壳：{sorted(set(shells) - core)}"


def _is_host_ref(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_host"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _host_reads(path: Path, cls_name: str) -> set[str]:
    """协作对象经宿主触达的名字（`self._host.<x>` 与 `host = self._host` 别名，读 + 写都算）。"""
    out: set[str] = set()
    for node in _methods(path, cls_name).values():
        aliases = {
            t.id
            for n in ast.walk(node)
            if isinstance(n, ast.Assign) and _is_host_ref(n.value)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for n in ast.walk(node):
            if not isinstance(n, ast.Attribute):
                continue
            base = n.value
            if _is_host_ref(base) or (isinstance(base, ast.Name) and base.id in aliases):
                out.add(n.attr)
    return out


def test_shells_delegate_to_the_collaborator() -> None:
    """协作对象：组装在 `__init__`，组合根上的壳**恰好一行**委派（加逻辑就红）。"""
    from importlib import import_module

    shells = _methods(CORE_PY, "TrainingLoop")
    for rel, (cls, attr, _proto, _members, _owned, table) in COLLABORATORS.items():
        mod = import_module(rel[: -len(".py")].replace("/", "."))
        assert getattr(mod, cls).__module__ == rel[: -len(".py")].replace("/", ".")
        for shell, target in table.items():
            body = [
                n
                for n in shells[shell].body
                if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)
            ]
            assert len(body) == 1 and isinstance(body[0], (ast.Expr, ast.Return)), (
                f"{shell} 的壳体不是单条语句"
            )
            call = body[0].value
            assert isinstance(call, ast.Call), f"{shell} 的壳体不是调用"
            fn = call.func
            assert isinstance(fn, ast.Attribute) and fn.attr == target, f"{shell} 没委派到 {target}"
            recv = fn.value
            assert (
                isinstance(recv, ast.Attribute)
                and recv.attr == attr
                and isinstance(recv.value, ast.Name)
                and recv.value.id == "self"
            ), f"{shell} 的接收者不是 self.{attr}"
        asm = [
            n
            for n in shells["__init__"].body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == attr for t in n.targets)
        ]
        assert len(asm) == 1, f"{cls} 不在 __init__ 里装配（或装配了两次）"
        val = asm[0].value
        assert isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == cls
        assert len(val.args) == 1 and isinstance(val.args[0], ast.Name) and val.args[0].id == "self"


def test_collaborator_host_surface_is_declared() -> None:
    """协作对象的宿主读面 = 它的 Protocol 声明集；自己的状态只在自己的 `__init__`。"""
    from importlib import import_module

    from trainer.loop_core import TrainingLoop

    for rel, (cls, _attr, proto_name, _cnames, owned, _table) in COLLABORATORS.items():
        path = NN_ROOT / rel
        proto = getattr(import_module(rel[: -len(".py")].replace("/", ".")), proto_name)
        proto_props = {n for n, v in vars(proto).items() if isinstance(v, property)}
        proto_methods = {
            n
            for n, v in vars(proto).items()
            if callable(v) and not isinstance(v, property) and not n.startswith("__")
        }
        declared = set(proto.__annotations__) | proto_props | proto_methods
        assert _host_reads(path, cls) == declared, sorted(_host_reads(path, cls) ^ declared)
        # 自己的状态：`__init__` 只赋 `_host` + 本簇槽位；其余一个不带。
        got_owned = {
            t.attr
            for node in _methods(path, cls)["__init__"].body
            for t in (
                list(node.targets)
                if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign) else []
            )
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
        }
        assert got_owned == {"_host"} | owned, sorted(got_owned)
        # 宿主侧：每个声明名都得是**活**的读面（property），不是 `__init__` 里的副本。
        for name in proto_props:
            assert isinstance(getattr(TrainingLoop, name, None), property), name
        for name in proto_methods:
            assert callable(getattr(TrainingLoop, name, None)), name
        # 归对象的状态不许在组合根的声明段里再出现一份（双真相）。
        core_slots: set[str] = set()
        for names in _self_slots(CORE_PY, "TrainingLoop").values():
            core_slots |= names
        assert sorted(owned & core_slots) == [], f"{cls} 的状态还在组合根里：{sorted(owned & core_slots)}"


def test_collaborator_calls_no_sibling_method() -> None:
    """协作对象只许调 Protocol **声明过**的宿主方法（其余一律只能读数据）。"""
    from importlib import import_module

    allowed = {}
    for rel, (_c, _attr, proto_name, _cn, _own, _tb) in COLLABORATORS.items():
        proto = getattr(import_module(rel[: -len(".py")].replace("/", ".")), proto_name)
        allowed[rel] = {
            n
            for n, v in vars(proto).items()
            if callable(v) and not isinstance(v, property) and not n.startswith("__")
        }
    for rel, (cls, _attr, _proto, cnames, _owned, _table) in COLLABORATORS.items():
        path = NN_ROOT / rel
        for name in cnames:
            called = {
                n.func.attr
                for n in ast.walk(_methods(path, cls)[name])
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "_host"
            }
            assert called <= allowed.get(rel, set()), f"{name} 调了协议外的宿主方法 {sorted(called)}"


def test_composition_is_exact() -> None:
    """组装逐字对账（本文件是唯一所有者）：`RoundSteps.__bases__` + 全量 MRO 名单。"""
    from trainer.loop_core import TrainingLoop
    from trainer.loop_round_steps import RoundSteps

    assert tuple(c.__name__ for c in RoundSteps.__bases__) == ROUND_STEPS_BASES
    assert tuple(c.__name__ for c in TrainingLoop.__bases__) == TRAINING_LOOP_BASES
    assert [c.__name__ for c in TrainingLoop.__mro__] == MRO_NAMES
    # P0-1：四个协作对象都**不在** MRO / 直接基类里（它们归组合根持有，不是基类）。
    from trainer.loop_baseline import BaselineEval
    from trainer.loop_dispatch import Dispatcher
    from trainer.loop_export import BundleExporter
    from trainer.loop_iter_dir import IterWorkspace

    for cls in (IterWorkspace, BaselineEval, BundleExporter, Dispatcher):
        assert cls not in TrainingLoop.__bases__
        assert cls not in TrainingLoop.__mro__


def test_composition_root_keeps_only_the_structural_pair() -> None:
    """★ 组合根的方法闭集**恰好** `{__init__, _run_inspect} ∪ _init_*` —— 只有声明，零逻辑。"""
    got = sorted(_methods(CORE_PY, "TrainingLoop"))
    assert got == sorted(STAYS), f"组合根多/少了方法：{got}"
    # `_run_inspect` 的模块级搭档必须同住（结构性例外的原因见头注）。
    top = _top_names(CORE_PY)
    assert {"run_inspect"} <= top
    assert "run_inspect" not in _top_names(LIFE_PY)


# ─────────────────────── 入边 / 出边 / 槽位（三张表） ───────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边闭集：每个成员只有登记的调用者，且呼叫点数逐一对账。"""
    # 2026-09-30（刀 4）：族的调用者两棵树上都有（`trainer/` 编排 + `biz/` 纯逻辑，
    # `_eval_on_round` 的唯一 external 调用点随 `_gate` 搬去了 `biz/loop_guards_gate.py`）
    # ⇒ 扫描面必须走 `logic_py_files`（两棵树），否则这里是**永真**的守卫。
    files = source_scan.logic_py_files(str(NN_ROOT))
    only = frozenset(INBOUND_CALLS)
    calls = {p.name: _self_call_counts(p, only) for p in files}
    defined = {p.name: _defined_names(p, only) for p in files}
    # P0-1：所有登记成员都是组合根上的转发壳（实现已在协作对象里）。
    owners = {shell: CORE_PY.name for shell in SHELLS}
    for member, want in INBOUND_CALLS.items():
        got = {name: c[member] for name, c in calls.items() if member in c}
        assert got == want, f"{member} 的入边变了：{got}"
        # 定义面唯一：只有持有者定义它，**加上**登记在案的占位（再多一份 = 遮罩/漂移）。
        definers = {name for name, names in defined.items() if member in names}
        want_definers = {owners[member]} | set(PLACEHOLDERS.get(member, ()))
        assert definers == want_definers, f"{member} 的定义面：{sorted(definers)}"


def test_slot_writer_is_unique() -> None:
    """★ 写手唯一：这两个槽位**只在** `loop_lifecycle._setup_common` 被赋值（读≠写）。

    为什么单列：读者侧随手一改就会把「谁拥有这份状态」的账翻错（本刀三簇是纯读者）。
    只数「谁碰到这个名字」不够——`loop_lifecycle.run_one_round` 也**读** `_course_fp`
    （S4 第十九刀的守卫曾用这个宽松口径，反探针 ⑬ 实测抓不住改名写手）。
    """
    assigns = _self_assigns(LIFE_PY, "TrainingLifecycle")
    for slot in WRITE_OWNED_SLOTS:
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


def test_quota_incident_runs_from_the_collaborator(tmp_path, monkeypatch) -> None:
    """★ `IterWorkspace.check_quota` 真跑：`log` seam 在**本模块**（打旧家收不到）。"""
    import types

    import trainer.loop_core as core_mod
    import trainer.loop_iter_dir as lid

    seen: list[str] = []
    core_seen: list[str] = []
    monkeypatch.setattr(lid, "log", lambda m: seen.append(str(m)))
    monkeypatch.setattr(core_mod, "log", lambda m: core_seen.append(str(m)))  # 旧家：收不到

    def _host(node_rollout: bool) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            traj_dir=tmp_path,
            node_rollout=node_rollout,
            extra_wver=None,
            course_fp=None,
            corpus_fp="",
            state_init=False,
            args=types.SimpleNamespace(course_name="probe"),
        )

    ws = lid.IterWorkspace(_host(False))
    ws.check_quota(7)  # 第 1 轮：零 shard，计数 1
    assert ws.quota_state().zero_shard_streak == 1
    ws.check_quota(8)  # 第 2 轮：响亮告警
    assert ws.quota_state().zero_shard_streak == 2
    assert any("quota" in m for m in seen), seen
    assert core_seen == [], "打 trainer.loop_core.log 竟收到了——seam 的落点不对"
    # 上云轮：本地零 shard 是**预期** ⇒ 不喊、计数归零
    node = lid.IterWorkspace(_host(True))
    node._zero_shard_streak = 5
    node.check_quota(9)
    assert node.quota_state().zero_shard_streak == 0
    assert not any("quota" in m for m in seen[1:])


def test_eval_on_round_position_contract() -> None:
    """★ 顺序契约：真实现压住 `TrainingEval` 的占位；占位被直接调用时**响亮报错**。

    P0-1 第四刀把 `TrainingDispatch` 改成协作对象后，胜出者从「MRO 里更靠前的混入」变成
    **组合根类体里的转发壳**：`TrainingLoop` 是 MRO 的第一项，且壳就定义在它自己的
    `__dict__` 里 ⇒ 永远先于任何基类的占位解析。判据随之改成钉这件事（而不是下标比较）。
    """
    import inspect

    from trainer.loop_core import TrainingLoop
    from trainer.loop_eval import TrainingEval

    assert "_eval_on_round" in TrainingLoop.__dict__, "壳不在组合根类体里——占位可能上位"
    assert TrainingLoop._eval_on_round is not TrainingEval._eval_on_round
    # 壳是一行委派（由 `test_shells_delegate_to_the_collaborator` 钉形状）；这里只钉「它真的转发」。
    src = inspect.getsource(TrainingLoop._eval_on_round)
    assert "eval_on_round" in src and "dispatch" in src, src
    # 占位的语义：MRO 被改坏时必须响亮失败，而不是静默返回 falsy 把 eval 全关掉。
    probe = object.__new__(TrainingEval)
    try:
        TrainingEval._eval_on_round(probe, 1)
    except Exception:
        pass
    else:  # pragma: no cover - 失败分支
        raise AssertionError("占位没有响亮报错")


def test_old_home_no_longer_absorbs_patches() -> None:
    """★ 旧家不再吸收 patch：搬走的模块全局在 `trainer.loop_core` 里**不存在**（响亮 AttributeError）。"""
    import trainer.loop_baseline as bl
    import trainer.loop_core as core_mod
    import trainer.loop_dispatch as dp
    import trainer.loop_iter_dir as lid

    for name in GONE_FROM_CORE:
        assert not hasattr(core_mod, name), f"trainer.loop_core.{name} 还在——陈旧 patch 会静默失效"
    # 新家各自持有自己真正用到的那个（= 真注入点）。
    assert hasattr(bl, "RUN_ID")
    assert hasattr(lid, "completed_pairs") and hasattr(lid, "precollect_snapshot_wver")
    assert hasattr(dp, "dispatch_rollout_phase")
    assert hasattr(lid, "log") and hasattr(bl, "log")
