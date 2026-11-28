"""拆分的**契约守卫**：动态采集（按样本量）编排是**协作对象** `VolumePlanner`
（P0-1 第五刀，2026-11-28；前身是 S4 第十八刀的 `TrainingVolume` 混入）。

## 这一刀切了什么

混入 → 对象：状态（15 个槽位）与逻辑（18 个方法）都归 `trainer/loop_volume.py::VolumePlanner`，
组合根 `TrainingLoop` 只留**恰好一行**转发壳（旧方法名不改，调用点零改动），装配在
`__init__` 里一行 `self.volume = VolumePlanner(self)`。

宿主读面用 `VolumeHost(Protocol)` + property（活读数，不拷副本）：
`report` / `stream_meta` / `rotate_seed` / `traj_dir` / `traj_root` / `jsonl_path` / `course_fp` /
`corpus_fp` / `state_init` / `extra_wver` / `payload_parts` / `model` / `opt` / `device` /
`ref_model` / `start_it`；写面只有 `land_collect` / `commit_journal`。

## 本文件钉住什么

1. 簇成员**定义**在 `VolumePlanner`，`TrainingLoop` 里只有一行壳（门面只能是门面）；
2. 壳逐条转发到 `self.volume.<新名>(…)`——**不再有对象级同一**（那是混入时代的事）；
3. 组装：`RoundSteps` 已无基类；`TrainingLoop.__bases__` 四件套不变；
4. **状态归属**：槽位只在 `VolumePlanner.__init__` 赋值；宿主/兄弟簇零写入；
5. **跨簇读面闭集**：外簇只许 `volume_view()` / `greedy_view()` 读，只许三个命令写；
6. 顶层 import 面闭合；`worker.volume_waves` / `worker.volume_quota` 仍只延迟 import；
7. 不得反向 import `trainer.loop_core` / `loop_steps` / `loop_round_steps`；
8. **★ 两条功能性**：`log` seam 住在**本模块** · `land_collect` 是报告唯一写面。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

NN_ROOT = Path(__file__).resolve().parents[2]
VOLUME_PY = NN_ROOT / "trainer" / "loop_volume.py"
CORE_PY = NN_ROOT / "trainer" / "loop_core.py"
STEPS_PY = NN_ROOT / "trainer" / "loop_steps.py"
ROUND_STEPS_PY = NN_ROOT / "trainer" / "loop_round_steps.py"

#: 协作对象的成员（闭集）：新名 → 组合根上的旧名壳。
PLANNER_METHODS: dict[str, str] = {
    "round_mem_shards": "_round_mem_shards",
    "volume_active": "_volume_active",
    "volume_stages": "_volume_stages",
    "volume_est_samples": "_volume_est_samples",
    "iteration_pairs": "_iteration_pairs",
    "volume_stage_ests_map": "_volume_stage_ests_map",
    "dispatch_volume_wave": "_dispatch_volume_wave",
    "volume_journal_replay": "_volume_journal_replay",
    "volume_topup": "_volume_topup",
    "volume_collect_continuous": "_volume_collect_continuous",
    "volume_continuous_loop": "_volume_continuous_loop",
    "volume_continuous_settle": "_volume_continuous_settle",
    "volume_collect_greedy": "_volume_collect_greedy",
    "dispatch_greedy_wave": "_dispatch_greedy_wave",
    "greedy_budget": "_greedy_budget",
    "greedy_wave_pairs": "_greedy_wave_pairs",
    "greedy_settle": "_greedy_settle",
    "greedy_scan": "_greedy_scan",
}

#: 只读观测面（组合根同名壳 + 外簇的唯一读入口）。
VIEWS = ("volume_view", "greedy_view")

#: 协作对象的写命令（外簇只许经这三个改它的状态）。
COMMANDS = ("reset_greedy", "reset_ordinary_seeds", "merge_greedy")

#: 槽位（`VolumePlanner.__init__` 是唯一初值点）。
PLANNER_SLOTS = (
    "_volume_target",
    "_volume_collected",
    "_volume_waves",
    "_volume_g0",
    "_volume_est",
    "_volume_capped",
    "_volume_halted",
    "_volume_stage_ests",
    "_volume_stage_stats",
    "_greedy_plan",
    "_greedy_stats",
    "_greedy_report",
    "_greedy_waves",
    "_volume_ordinary_seeds",
)

#: 宿主读面（`VolumeHost` 的 property；组合根同名 property 返回私有槽）。
HOST_READS = (
    "model",
    "opt",
    "device",
    "ref_model",
    "start_it",
    "traj_root",
    "traj_dir",
    "jsonl_path",
    "rotate_seed",
    "course_fp",
    "corpus_fp",
    "state_init",
    "extra_wver",
    "payload_parts",
    "report",
    "stream_meta",
)

#: 顶层 import 面的闭集（模块名）。
TOP_LEVEL_ALLOWED = {
    "__future__",
    "argparse",
    "collections.abc",
    "dataclasses",
    "pathlib",
    "types",
    "typing",
    "biz.course",
    "common.log",
    # P0-1 第五刀收尾（2026-11-28）：宿主面的具体类型。段账（`ShardSegments`）与
    # WAL（`CommitJournal`）在 `RolloutPhaseSpec` 里本来就是具体类型 —— 协议照抄，
    # 不再给它们各打一层 `Any`。
    "remote.payload_pack",
    "worker.commit_journal",
    "worker.reports",
    "worker.resume",
    "trainer.rollout_phase",
    "worker.volume_wave_plan",
}

#: DI 目标：只许**方法体内**延迟 import（测试 patch 的是这些实现模块）。
LAZY_ONLY = ("worker.volume_waves", "worker.volume_quota")

#: 外簇（除组合根与协作对象自己）扫描面。
SIBLING_SOURCES = (
    (STEPS_PY, "TrainingSteps"),
    (ROUND_STEPS_PY, "RoundSteps"),
    (NN_ROOT / "trainer" / "loop_remote.py", "TrainingRemote"),
    (NN_ROOT / "trainer" / "loop_remote_drive.py", "TrainingRemoteDrive"),
    (NN_ROOT / "trainer" / "loop_remote_job.py", "TrainingRemoteJob"),
    (NN_ROOT / "trainer" / "loop_remote_fail.py", "TrainingRemoteFail"),
    (NN_ROOT / "trainer" / "loop_remote_push.py", "TrainingRemotePush"),
    (NN_ROOT / "trainer" / "loop_guards.py", "TrainingGuards"),
    (NN_ROOT / "worker" / "loop_guards_trip.py", "TrainingGuardsTrip"),
    (NN_ROOT / "worker" / "loop_guards_leg.py", "TrainingGuardsLeg"),
    (NN_ROOT / "worker" / "loop_guards_gate.py", "TrainingGuardsGate"),
    (NN_ROOT / "worker" / "loop_guards_sweep.py", "TrainingGuardsSweep"),
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_node(path: Path, cls: str) -> ast.ClassDef:
    return next(
        n for n in ast.walk(_tree(path)) if isinstance(n, ast.ClassDef) and n.name == cls
    )


def _classes(path: Path) -> dict[str, ast.ClassDef]:
    return {
        n.name: n
        for n in ast.walk(_tree(path))
        if isinstance(n, ast.ClassDef)
    }


def _methods(path: Path, cls: str) -> dict[str, ast.FunctionDef]:
    node = _class_node(path, cls)
    return {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}


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
    """运行期顶层 import（`if TYPE_CHECKING:` 只服务类型面，不算运行依赖）。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            continue
        body = node.body if isinstance(node, ast.If) else [node]
        for n in body:
            if isinstance(n, ast.Import):
                out.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                out.add(n.module)
    return out


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


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


def test_cluster_is_defined_in_the_planner_only() -> None:
    """成员定义在 `VolumePlanner`；组合根只有一行壳，混入类已消失。"""
    in_planner = set(_methods(VOLUME_PY, "VolumePlanner")) - {"__init__"}
    assert in_planner == {*PLANNER_METHODS, *VIEWS, *COMMANDS}, sorted(
        in_planner ^ {*PLANNER_METHODS, *VIEWS, *COMMANDS}
    )
    assert "TrainingVolume" not in _classes(VOLUME_PY), (
        "混入类没删干净——两套实现会各自漂"
    )
    core = _methods(CORE_PY, "TrainingLoop")
    for legacy in (*PLANNER_METHODS.values(), *VIEWS, "_greedy_halted"):
        if legacy == "_greedy_halted":
            continue
        assert legacy in core, f"旧调用点的壳不见了：{legacy}"
    # 新名不得在组合根上冒出第二份实现（只许壳，且壳里只有一次调用 + 可选 return）。
    for name in PLANNER_METHODS:
        assert name not in core, f"{name} 不该出现在组合根（那是协作对象自己的名字）"
    for legacy, name in ((old, new) for new, old in PLANNER_METHODS.items()):
        fn = core[legacy]
        body = [
            stmt
            for stmt in fn.body
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        ]
        assert len(body) == 1, f"{legacy} 的壳不止一行：{len(body)}"
        stmt = body[0]
        call = getattr(stmt, "value", None)
        assert isinstance(call, ast.Call), f"{legacy} 的壳不是一次调用"
        callee = call.func
        assert isinstance(callee, ast.Attribute) and callee.attr == name, (
            f"{legacy} 没有转发到 volume.{name}（实际：{ast.dump(callee)[:60]}）"
        )
        owner = callee.value
        assert isinstance(owner, ast.Attribute) and owner.attr == "volume", legacy


def test_host_reads_are_live_properties_not_copies() -> None:
    """宿主读面全是 property（活读数）：`VolumeHost` 声明的名字与组合根逐个对上。"""
    from trainer.loop_core import TrainingLoop

    host = _class_node(VOLUME_PY, "VolumeHost")
    declared = {
        m.name
        for m in host.body
        if isinstance(m, ast.FunctionDef)
        and any(_is_property(d) for d in m.decorator_list)
    }
    assert declared == set(HOST_READS), sorted(declared ^ set(HOST_READS))
    for name in HOST_READS:
        assert isinstance(getattr(TrainingLoop, name), property), name
    # 协作对象只认协议上的名字：`self._host.X` 必须在闭集里。
    used = {
        sub.attr
        for fn in _methods(VOLUME_PY, "VolumePlanner").values()
        for sub in ast.walk(fn)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.value, ast.Attribute)
        and sub.value.attr == "_host"
    }
    allowed = set(HOST_READS) | {
        "args",
        "bun",
        "ppo_backend",
        "update_kwargs",
        "commit_journal",
        "land_collect",
    }
    assert used <= allowed, sorted(used - allowed)


def _is_property(dec: ast.expr) -> bool:
    return isinstance(dec, ast.Name) and dec.id == "property"


def test_composition_root_only_forwards() -> None:
    """组装：`RoundSteps` 无基类；`TrainingLoop.__bases__` 四件套；装配一行。"""
    from trainer.loop_core import TrainingLoop
    from trainer.loop_guards import TrainingGuards
    from trainer.loop_lifecycle import TrainingLifecycle
    from trainer.loop_round_steps import RoundSteps
    from trainer.loop_steps import TrainingSteps

    assert RoundSteps.__bases__ == (object,)
    assert TrainingLoop.__bases__ == (
        RoundSteps,
        TrainingSteps,
        TrainingGuards,
        TrainingLifecycle,
    )
    init = _methods(CORE_PY, "TrainingLoop")["__init__"]
    init_src = ast.get_source_segment(CORE_PY.read_text(encoding="utf-8"), init) or ""
    assert "self.volume = VolumePlanner(self)" in init_src
    # 初始化归协作对象（`_init_volume_state` 已从组合根删除，`STAYS` 闭集在
    # `test_loop_core_tail_split.py` 里挡它回来）。
    # 生产入口全在 RoundSteps（它才是调用者），且只经协作对象/壳。
    round_src = ROUND_STEPS_PY.read_text(encoding="utf-8")
    for name in ("_iteration_pairs", "_volume_active", "_volume_collect_continuous"):
        assert f"self.{name}(" in round_src, f"RoundSteps 不再调 {name}——方向判断失效"
    # 退役的离散补波：生产路径零调用（只由既有 e2e/单测以 unbound 形式驱动）。
    assert "self._volume_topup(" not in round_src
    topup_step = _methods(ROUND_STEPS_PY, "RoundSteps")["step_volume_topup"]
    topup_src = ast.get_source_segment(round_src, topup_step)
    assert topup_src is not None and topup_src.rstrip().endswith("return None")


# ─────────────────────────────── 状态面 ───────────────────────────────


def test_slots_are_initialized_in_the_planner_only() -> None:
    """槽位只在 `VolumePlanner.__init__` 赋值；宿主与兄弟簇零写入。"""
    init = _methods(VOLUME_PY, "VolumePlanner")["__init__"]
    stores = {
        attr for attr, roles in _self_attr_roles(init).items() if "Store" in roles
    }
    missing = sorted(set(PLANNER_SLOTS) - stores)
    assert missing == [], f"协作对象没初始化这些槽：{missing}"

    offenders: set[str] = set()
    for path, cls in ((CORE_PY, "TrainingLoop"), *SIBLING_SOURCES):
        for name, fn in _methods(path, cls).items():
            for attr, roles in _self_attr_roles(fn).items():
                if attr in PLANNER_SLOTS and "Store" in roles:
                    offenders.add(f"{path.name}|{name}|{attr}")
    assert offenders == set(), sorted(offenders)


def test_sibling_clusters_only_touch_views_and_commands() -> None:
    """外簇只许经只读视图读、经三个命令写——否则状态归属会裂成「谁都能改」。"""
    reads: dict[str, set[str]] = {}
    commands: set[str] = set()
    for path, cls in SIBLING_SOURCES:
        for fn in _methods(path, cls).values():
            for sub in ast.walk(fn):
                if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                    continue
                owner = sub.func.value
                if not (
                    isinstance(owner, ast.Attribute)
                    and owner.attr == "volume"
                    and isinstance(owner.value, ast.Name)
                    and owner.value.id == "self"
                ):
                    continue
                if sub.func.attr in VIEWS:
                    reads.setdefault(sub.func.attr, set()).add(path.name)
                elif sub.func.attr in COMMANDS:
                    commands.add(sub.func.attr)
                else:
                    raise AssertionError(
                        f"{path.name} 绕过视图直接调 volume.{sub.func.attr}()"
                    )
    assert "volume_view" in reads and "greedy_view" in reads, reads
    assert commands == {"reset_greedy", "reset_ordinary_seeds", "merge_greedy"}, commands


# ─────────────────────────────── 依赖面 ───────────────────────────────


def test_top_level_import_surface_is_closed() -> None:
    """顶层 import 面闭合：本模块不许长出重依赖（重依赖留在调用方或延迟 import）。"""
    got = _top_level_imports(VOLUME_PY)
    assert got <= TOP_LEVEL_ALLOWED, sorted(got - TOP_LEVEL_ALLOWED)


def test_lazy_targets_stay_lazy() -> None:
    """`biz.volume_waves` / `biz.volume_quota` 只许方法体内延迟 import（原有的 DI 面）。"""
    top = _top_level_imports(VOLUME_PY)
    inside = _in_function_imports(VOLUME_PY)
    for mod in LAZY_ONLY:
        assert mod not in top, f"{mod} 被提到顶层了——纯逻辑面会跟着进 import 图"
        assert mod in inside, f"{mod} 的延迟 import 不见了"


def test_loop_volume_does_not_import_the_hosts() -> None:
    """不得成环：协作对象不许 import `trainer.loop_core` / `loop_steps` / `loop_round_steps`。"""
    got = _top_level_imports(VOLUME_PY) | _in_function_imports(VOLUME_PY)
    for bad in ("trainer.loop_core", "trainer.loop_steps", "trainer.loop_round_steps"):
        assert bad not in got, bad


# ─────────────────────────────── 功能性 ───────────────────────────────


def _stream_host() -> Any:
    """最小宿主：流式轮的 `volume_topup` 只读 `args` / `stream_meta` 就返回。"""
    ns = SimpleNamespace(
        args=SimpleNamespace(target_transitions=100),
        stream_meta={"stream": True},
    )
    ns.volume = __import__(
        "trainer.loop_volume", fromlist=["VolumePlanner"]
    ).VolumePlanner(ns)
    return ns


def test_log_seam_lives_here_not_in_loop_core(monkeypatch) -> None:
    """★ 本簇的日志按**本模块**的 `log` 解析（同名 seam 在旧命名空间是静默空操作）。"""
    import trainer.loop_core as core_mod
    import trainer.loop_volume as vol_mod

    host = _stream_host()
    seen: list[str] = []
    monkeypatch.setattr(vol_mod, "log", lambda m: seen.append(str(m)))
    core_seen: list[str] = []
    monkeypatch.setattr(core_mod, "log", lambda m: core_seen.append(str(m)))

    host.volume.volume_topup(1, None)
    assert seen and "流式路径" in seen[0], seen
    assert core_seen == [], f"补丁打在了旧命名空间上却仍有输出：{core_seen}"


def test_report_is_landed_through_the_host_entry() -> None:
    """★ 报告只有**一个写面**（宿主的 `land_collect`）：协作对象零 `self._report` 写入。"""
    writes = {
        name: roles["_report"]
        for name, fn in _methods(VOLUME_PY, "VolumePlanner").items()
        if (roles := _self_attr_roles(fn)).get("_report")
    }
    assert "Store" not in {r for roles in writes.values() for r in roles}, sorted(writes)
    calls = sum(
        1
        for fn in _methods(VOLUME_PY, "VolumePlanner").values()
        for sub in ast.walk(fn)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "land_collect"
    )
    assert calls == 6, f"报告落位点从 6 处变了：{calls}（新路径要显式记账）"
    host = _methods(CORE_PY, "TrainingLoop")["land_collect"]
    stores = {
        sub.attr
        for sub in ast.walk(host)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.ctx, ast.Store)
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "self"
    }
    assert stores == {"_report", "_stream_meta"}, sorted(stores)
    disp = CORE_PY.read_text(encoding="utf-8")
    assert "self.land_collect(outcome.report, outcome.stream_meta)" in disp


def test_legacy_shells_drive_the_collaborator() -> None:
    """★ 既有用例的形态：`TrainingLoop._volume_*(stub, …)` —— 壳把活派给 `stub.volume`。

    壳里的 `self.volume` 在 unbound 调用下解析到桩自己挂的对象；这里用一个只实现
    「关集解析所需读面」的最小宿主钉住这条路径。
    """
    from trainer.loop_core import TrainingLoop

    calls: list[str] = []

    def fake_active() -> bool:
        calls.append("active")
        return False

    host = SimpleNamespace(
        args=SimpleNamespace(target_transitions=0, stages="0-3", curriculum_stages="", rotate_stages=0),
        volume=SimpleNamespace(volume_active=fake_active),
    )
    assert TrainingLoop._volume_active(cast(Any, host)) is False
    assert calls == ["active"]
