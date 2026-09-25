"""S22 契约（2026-09-25）—— `rl/loop_remote.py` 的 862 行连通分量切成四簇后不得腐烂。

S4 第二步把「远端 PPO 腿」13 个方法（862 行，**一条连通分量**）从 `TrainingSteps` 搬进
`rl/loop_remote.py::TrainingRemote`。第二十二刀再把这条分量**按判据同源**切成四簇——每一簇
是「同一件事 / 同一套失败语义」，不是按大小或物理位置切：

| 混入 | 判据 | 模块 | 方法 |
|---|---|---|---|
| `TrainingRemotePush` | 把一份 job **送到节点**（提交 / 首发 / 取回） | `rl/loop_remote_push.py` | `_push_submit_node` · `_push_submit_first` · `_push_fetch` |
| `TrainingRemoteJob` | **一份远端 PPO job 的四步** + 组合入口 | `rl/loop_remote_job.py` | `_remote_ppo` · `_remote_ppo_publish` · `_remote_ppo_probe` · `_remote_ppo_fetch` · `_remote_ppo_land` |
| `TrainingRemoteFail` | **远端失败的唯一处置策略** | `rl/loop_remote_fail.py` | `_abort_node_failure` · `_handle_remote_failure` |
| `TrainingRemoteDrive` | **谁驱动这条腿**（轮内 / 整轮 / 整段） | `rl/loop_remote_drive.py` | `_remote_ppo_step` · `_remote_iter` · `_remote_run_segment` |

依赖是**一条链**（调用者依赖被调用者）：`Push ← Job ← {Fail, Job} ← Drive`。`TrainingRemote`
退成**零方法的组合根**（仍住 `rl/loop_remote.py` ⇒ `from rl.loop_remote import TrainingRemote`
零迁移），`TrainingSteps.__bases__` / `TrainingLoop.__bases__` 与四个「继承真混入」的测试宿主
**一行不改**。

本文件钉住这条切分不腐烂的若干面：定义面唯一 · 对象恒等 · 组装逐字 · 组合根零方法 ·
**借用声明闭集 = 派生集** · 入边 / 出边 / 槽位写手闭集 · 顶层与延迟 import 闭集 · 禁反向边 ·
旧家零 seam · **四条功能性**。`tests/test_loop_transport_split.py` 保留 S4 第二步的契约（那几条
与「13 个方法住同一个类」相关的断言已改成读组合根 MRO / 读一族）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
RL = NN_ROOT / "rl"

#: 成员 → (文件, 类)。13 个，一个不漏。
HOMES: dict[str, tuple[str, str]] = {
    "_push_submit_node": ("loop_remote_push.py", "TrainingRemotePush"),
    "_push_submit_first": ("loop_remote_push.py", "TrainingRemotePush"),
    "_push_fetch": ("loop_remote_push.py", "TrainingRemotePush"),
    "_remote_ppo": ("loop_remote_job.py", "TrainingRemoteJob"),
    "_remote_ppo_publish": ("loop_remote_job.py", "TrainingRemoteJob"),
    "_remote_ppo_probe": ("loop_remote_job.py", "TrainingRemoteJob"),
    "_remote_ppo_fetch": ("loop_remote_job.py", "TrainingRemoteJob"),
    "_remote_ppo_land": ("loop_remote_job.py", "TrainingRemoteJob"),
    "_abort_node_failure": ("loop_remote_fail.py", "TrainingRemoteFail"),
    "_handle_remote_failure": ("loop_remote_fail.py", "TrainingRemoteFail"),
    "_remote_ppo_step": ("loop_remote_drive.py", "TrainingRemoteDrive"),
    "_remote_iter": ("loop_remote_drive.py", "TrainingRemoteDrive"),
    "_remote_run_segment": ("loop_remote_drive.py", "TrainingRemoteDrive"),
}
MEMBERS = tuple(HOMES)

#: 一族五个文件 → 类名（组合根在第一位）。
FAMILY: dict[str, str] = {
    "loop_remote.py": "TrainingRemote",
    "loop_remote_push.py": "TrainingRemotePush",
    "loop_remote_job.py": "TrainingRemoteJob",
    "loop_remote_fail.py": "TrainingRemoteFail",
    "loop_remote_drive.py": "TrainingRemoteDrive",
}

#: 组装逐字：文件 → (自己写的基类元组, 类名)。
BASES: dict[str, tuple[tuple[str, ...], str]] = {
    "loop_remote_push.py": (("object",), "TrainingRemotePush"),
    "loop_remote_fail.py": (("object",), "TrainingRemoteFail"),
    "loop_remote_job.py": (("TrainingRemotePush",), "TrainingRemoteJob"),
    "loop_remote_drive.py": (("TrainingRemoteFail", "TrainingRemoteJob"), "TrainingRemoteDrive"),
    "loop_remote.py": (("TrainingRemoteDrive",), "TrainingRemote"),
}

#: 入边闭集：成员 → {文件: 呼叫点数}（`self.<成员>(` 的**真实 Call**；新入边必须改这张表）。
INBOUND_CALLS: dict[str, dict[str, int]] = {
    "_abort_node_failure": {"loop_remote_drive.py": 2, "loop_remote_fail.py": 1},
    "_handle_remote_failure": {"loop_remote_drive.py": 4},
    "_remote_ppo": {"loop_export.py": 1, "loop_remote_drive.py": 2},
    "_remote_ppo_fetch": {"loop_remote_drive.py": 1, "loop_remote_job.py": 1},
    "_remote_ppo_land": {"loop_remote_drive.py": 1, "loop_remote_job.py": 1},
    "_remote_ppo_probe": {"loop_remote_drive.py": 1},
    "_remote_ppo_publish": {"loop_remote_drive.py": 1, "loop_remote_job.py": 1},
    "_remote_ppo_step": {"loop_round_steps.py": 1},
    "_remote_iter": {"loop_round_steps.py": 1},
    "_remote_run_segment": {"loop_round_steps.py": 1},
    "_push_fetch": {"loop_remote_job.py": 1},
    "_push_submit_first": {"loop_remote_job.py": 1},
    "_push_submit_node": {"loop_remote_push.py": 2},
}

#: 出边闭集：文件 → 它调用的**跨簇**兄弟方法（同簇互调不算；经真继承解析）。
OUTBOUND_HANDS: dict[str, frozenset[str]] = {
    "loop_remote.py": frozenset(),
    "loop_remote_push.py": frozenset(),
    "loop_remote_job.py": frozenset({"_push_fetch", "_push_submit_first"}),
    "loop_remote_fail.py": frozenset(),
    "loop_remote_drive.py": frozenset(
        {
            "_abort_node_failure",
            "_handle_remote_failure",
            "_remote_ppo",
            "_remote_ppo_fetch",
            "_remote_ppo_land",
            "_remote_ppo_probe",
            "_remote_ppo_publish",
        }
    ),
}

#: 外部助手（**不在**本族里）→ 哪个文件声明了它（必须声明，否则 mypy 报 attr-defined）。
HELPER_HANDS: dict[str, str] = {
    "_commit_journal": "loop_remote_job.py",
    "_ensure_ts_code": "loop_remote_job.py",
    "_evalboard_idle": "loop_remote_job.py",
    "_forensics": "loop_remote_job.py",
    "_per_stage_quota": "loop_remote_job.py",
    "_volume_plan_block": "loop_remote_drive.py",
}

#: 槽位写手闭集：槽位 → {写它的文件}（跨模块共享的只能是登记的那几处）。
SLOT_WRITERS: dict[str, frozenset[str]] = {
    "_agg": frozenset({"loop_remote_job.py"}),
    "_bundle_index": frozenset({"loop_remote_job.py"}),
    "_chunks_n": frozenset({"loop_remote_job.py"}),
    "_code_sha256": frozenset({"loop_remote_job.py"}),
    "_code_zip_path": frozenset({"loop_remote_job.py"}),
    "_collect_child": frozenset({"loop_remote_job.py"}),
    "_demo_raw": frozenset({"loop_remote_job.py"}),
    "_kl_cum": frozenset({"loop_remote_job.py"}),
    "_ppo_cloud_sec": frozenset({"loop_remote_job.py"}),
    "_ppo_sec": frozenset({"loop_remote_job.py"}),
    "_total_steps": frozenset({"loop_remote_job.py"}),
    "_wire": frozenset({"loop_remote_job.py"}),
    # 停腿标记与连败计数：判据（Fail）与驱动（Drive）都要碰——这是**有意**的两处。
    "_leg_abort": frozenset({"loop_remote_fail.py", "loop_remote_drive.py"}),
    "_remote_fail": frozenset({"loop_remote_fail.py", "loop_remote_drive.py"}),
    "_node_rollout_sec": frozenset({"loop_remote_drive.py"}),
    "_report": frozenset({"loop_remote_drive.py"}),
    "_rollout_sec": frozenset({"loop_remote_drive.py"}),
}

#: 顶层 import 面（非 stdlib）：逐文件闭集。
TOP_IMPORTS: dict[str, frozenset[str]] = {
    "loop_remote.py": frozenset({"rl.loop_remote_drive"}),
    "loop_remote_push.py": frozenset(
        {"common.protocol", "remote.push_client", "rl.log", "rl.loop_round", "rl.loop_transport"}
    ),
    "loop_remote_job.py": frozenset(
        {
            "common.protocol",
            "rl.log",
            "rl.loop_remote_push",
            "rl.loop_round",
            "rl.loop_transport",
        }
    ),
    "loop_remote_fail.py": frozenset(
        {"common.protocol", "rl.events", "rl.log", "rl.loop_transport"}
    ),
    "loop_remote_drive.py": frozenset(
        {
            "common.protocol",
            "dist_common",
            "rl.events",
            "rl.log",
            "rl.loop_remote_fail",
            "rl.loop_remote_job",
            "rl.loop_round",
            "rl.loop_transport",
        }
    ),
}
STDLIB_IMPORTS = frozenset({"__future__", "hashlib", "pathlib", "time", "typing"})

#: 延迟 import（方法体内）逐文件闭集——它们各自服务一段，不许在模块级长出。
DELAYED_IMPORTS: dict[str, frozenset[str]] = {
    "loop_remote.py": frozenset(),
    "loop_remote_push.py": frozenset({"common.protocol"}),
    "loop_remote_job.py": frozenset(
        {
            "remote.bundle",
            "remote.hub_client",
            "remote.push_client",
            "rl.collect_only",
            "rl.config",
            "rl.queue",
            "rl.reward_library",
        }
    ),
    "loop_remote_fail.py": frozenset(),
    "loop_remote_drive.py": frozenset({"rl.iter_job", "rl.plan"}),
}

#: 反向边（禁）：本族谁都不许 import 这些（它只靠 `self.*` 回调 / 组合根除外）。
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

#: 组合根**不得**持有的模块全局（陈旧 patch 会静默失效）。
GONE_FROM_ROOT = (
    "dist_common",
    "log",
    "_push_submit",
    "_push_wait_result",
    "_run_wait_sec",
    "write_gate_verdict",
    "write_event",
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
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
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


def _self_call_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "self"
        ):
            counts[n.func.attr] = counts.get(n.func.attr, 0) + 1
    return counts


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


def _top_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module)
    return out


def _delayed_imports(path: Path, cls_name: str) -> set[str]:
    out: set[str] = set()
    for m in _methods(path, cls_name).values():
        for n in ast.walk(m):
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


# ─────────────────────────────── 定义面 / 组装 ───────────────────────────────


def test_members_live_in_exactly_their_new_home() -> None:
    """13 个成员各自只在新家定义一次；旧家（`TrainingSteps`）里不得再有同名。"""
    for member, (fname, _cls_name) in HOMES.items():
        definers = [
            name for name, cn in FAMILY.items() if member in _methods(RL / name, cn)
        ]
        assert definers == [fname], f"{member} 的定义面：{definers}（期望 {fname}）"
    left = _methods(NN_ROOT / "rl" / "loop_steps.py", "TrainingSteps")
    crept_back = sorted(set(MEMBERS) & set(left))
    assert crept_back == [], f"这些方法又回到 TrainingSteps 了：{crept_back}"


def test_identity_through_the_composition_root() -> None:
    """`TrainingRemote.X is 新家.X`——接线是**对象级**，不是同名副本。"""
    import rl.loop_remote as root

    for member, (fname, cls_name) in HOMES.items():
        home = getattr(__import__(f"rl.{fname[:-3]}", fromlist=[cls_name]), cls_name)
        got = getattr(root.TrainingRemote, member)
        assert got is getattr(home, member), member
        assert got.__module__ == f"rl.{fname[:-3]}", member


def test_composition_is_exact() -> None:
    """组装逐字：五件链 + `TrainingSteps` / `TrainingLoop` 的元组一行不改。"""
    import rl.loop_core as core
    import rl.loop_remote as root
    import rl.loop_remote_drive as drive
    import rl.loop_remote_fail as fail_mod
    import rl.loop_remote_job as job
    import rl.loop_remote_push as push
    import rl.loop_steps as steps

    mods = {
        "loop_remote_push.py": push,
        "loop_remote_fail.py": fail_mod,
        "loop_remote_job.py": job,
        "loop_remote_drive.py": drive,
        "loop_remote.py": root,
    }
    for fname, (want, cls_name) in BASES.items():
        cls = getattr(mods[fname], cls_name)
        assert tuple(c.__name__ for c in cls.__bases__) == want, fname
    # 组合根仍在原文件、仍是被 `TrainingSteps` 继承的第一个基类。
    assert root.TrainingRemote.__module__ == "rl.loop_remote"
    assert steps.TrainingSteps.__bases__ == (root.TrainingRemote, steps.TrainingEval, steps.TrainingExport)
    assert steps.TrainingSteps.__mro__[1] is root.TrainingRemote
    assert core.TrainingLoop.__bases__ == (
        core.RoundSteps,
        steps.TrainingSteps,
        core.TrainingGuards,
        core.TrainingLifecycle,
    )
    # 四个混入**都不是**组合类的直接基类（挂的是 TrainingSteps 一侧的链）。
    for k in (drive.TrainingRemoteDrive, job.TrainingRemoteJob, fail_mod.TrainingRemoteFail, push.TrainingRemotePush):
        assert k not in core.TrainingLoop.__bases__
        assert k in core.TrainingLoop.__mro__


def test_composition_root_has_zero_methods() -> None:
    """组合根**正面**断言零方法——比「13 个搬走的成员不在」更硬：它挡的是
    「顺手在入口补个小函数」，而层号看不出「只长了一个小函数」。"""
    fns = [m.name for m in _cls(RL / "loop_remote.py", "TrainingRemote").body if isinstance(m, ast.FunctionDef)]
    assert fns == [], fns
    assert _declared(RL / "loop_remote.py", "TrainingRemote") == set()
    import rl.loop_remote as root

    for name in GONE_FROM_ROOT:
        assert not hasattr(root, name), f"组合根不该持有 {name}"


def test_borrowed_declarations_are_exactly_the_touched_set() -> None:
    """借用声明闭集 == 派生集（跨 MRO 的并集）：多一个（死声明）/ 少一个（mypy 报 attr-defined）都红。"""
    all_declared: set[str] = set()
    for fname, cls_name in FAMILY.items():
        all_declared |= _declared(RL / fname, cls_name)

    touched: set[str] = set()
    own_methods: set[str] = set()
    top_names: set[str] = set()
    for fname, cls_name in FAMILY.items():
        ms = _methods(RL / fname, cls_name)
        own_methods |= set(ms)
        for m in ms.values():
            touched |= _self_attrs(m)
        top_names |= _top_level_names(RL / fname)
    derived = {n for n in touched - own_methods - top_names if not n.startswith("__")}
    assert derived, "派生集为空说明解析坏了"
    assert all_declared == derived, sorted(all_declared ^ derived)


def test_helper_hands_are_declared_where_they_are_used() -> None:
    """外部助手（其它 mixin 提供的）必须**在用到它的那个文件里**声明（mypy 才看得见）。"""
    for hand, fname in HELPER_HANDS.items():
        assert hand in _declared(RL / fname, FAMILY[fname]), f"{hand} 应在 {fname} 声明"
        used = {
            f
            for f, cn in FAMILY.items()
            if any(hand in _self_calls(m) for m in _methods(RL / f, cn).values())
        }
        assert used == {fname}, f"{hand} 的使用面：{sorted(used)}"


# ─────────────────────── 入边 / 出边 / 槽位（三张表） ───────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边闭集：只有登记的调用者，呼叫点数逐一对账（AST 计**真实 Call**）。"""
    files = sorted(RL.glob("*.py"))
    calls = {p.name: _self_call_counts(p) for p in files}
    for member, want in INBOUND_CALLS.items():
        got = {name: c[member] for name, c in calls.items() if member in c}
        assert got == want, f"{member} 的入边变了：{got}"


def test_outbound_hands_closed_set() -> None:
    """出边闭集：跨簇兄弟调用只有登记的那些（同簇互调不算）。"""
    for fname, cls_name in FAMILY.items():
        own = set(_methods(RL / fname, cls_name))
        ext: set[str] = set()
        for m in _methods(RL / fname, cls_name).values():
            ext |= {c for c in _self_calls(m) if c in MEMBERS and c not in own}
        assert ext == set(OUTBOUND_HANDS[fname]), f"{fname} 出边变了：{sorted(ext)}"
    # 跨簇兄弟经**真继承**解析（不是 `Any` 声明）⇒ 组合根上全部可达。
    import rl.loop_remote as root

    for hands in OUTBOUND_HANDS.values():
        for hand in hands:
            assert callable(getattr(root.TrainingRemote, hand)), hand


def test_slot_writers_closed_set() -> None:
    """槽位写手闭集：除登记的两处跨模块共享（`_leg_abort` / `_remote_fail`）外，
    每个槽位只有一个写它的文件——否则状态归属会悄悄裂成「谁都能写」。"""
    got: dict[str, set[str]] = {}
    for fname, cls_name in FAMILY.items():
        for m in _methods(RL / fname, cls_name).values():
            for slot in _self_assigns(m):
                got.setdefault(slot, set()).add(fname)
    for slot, want in SLOT_WRITERS.items():
        assert got.get(slot, set()) == set(want), f"{slot} 的写手变了：{sorted(got.get(slot, set()))}"
    extra = {k for k in got if k not in SLOT_WRITERS and not k.startswith("__")}
    assert extra == set(), f"本族多写了未登记的 self 属性：{sorted(extra)}"


# ─────────────────────────────── 依赖面 ───────────────────────────────


def test_top_level_imports_closed() -> None:
    """顶层 import 面逐文件闭合（重依赖留在延迟 import 或调用方）。"""
    for fname in FAMILY:
        got = _top_imports(RL / fname)
        real = {d for d in got if d.split(".")[0] not in STDLIB_IMPORTS}
        assert real == set(TOP_IMPORTS[fname]), f"{fname} 顶层 import 变了：{sorted(real)}"


def test_delayed_imports_closed() -> None:
    """方法体内的延迟 import 逐文件闭合——它们各自服务一段，不许在模块级长出。"""
    for fname, cls_name in FAMILY.items():
        got = {d for d in _delayed_imports(RL / fname, cls_name) if d.split(".")[0] not in STDLIB_IMPORTS}
        assert got == set(DELAYED_IMPORTS[fname]), f"{fname} 延迟 import 变了：{sorted(got)}"


def test_no_reverse_edges() -> None:
    """本族谁都不许 import 上游（`loop_core` / 两个宿主 / 兄弟族）——只靠 `self.*` 回调。"""
    for fname in FAMILY:
        top = _top_imports(RL / fname)
        inside = _delayed_imports(RL / fname, FAMILY[fname])
        hit = (top | inside) & FORBIDDEN_IMPORTS
        assert hit == set(), f"{fname} 长出反向边：{sorted(hit)}"


# ─────────────────────────────── 功能性 ───────────────────────────────


def test_fail_abort_writes_the_verdict_through_this_modules_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ `_abort_node_failure` 真跑：判决与日志都按 **`rl.loop_remote_fail`** 的全局解析。

    打在本模块 → 命中；打在旧家 `rl.loop_remote` → 一个字节都收不到（「seam 只剩一份」的
    机械形式：同名 seam 在两个命名空间里是两个各自真实的注入点，patch 错的那个是**静默空操作**）。
    """
    import rl.loop_remote as root_mod
    import rl.loop_remote_fail as fail_mod
    from rl.loop_remote_fail import TrainingRemoteFail

    host = SimpleNamespace(_jsonl_path=Path("unused.jsonl"), _leg_abort=False)
    verdicts: list[tuple[tuple, dict]] = []
    logs: list[str] = []
    monkeypatch.setattr(fail_mod, "write_gate_verdict", lambda *a, **k: verdicts.append((a, k)))
    monkeypatch.setattr(fail_mod, "log", lambda m: logs.append(str(m)))
    root_seen: list = []
    monkeypatch.setattr(root_mod, "write_gate_verdict", lambda *a, **k: root_seen.append(a), raising=False)
    monkeypatch.setattr(root_mod, "log", lambda m: root_seen.append(m), raising=False)

    TrainingRemoteFail._abort_node_failure(cast(Any, host), 7, ValueError("node lacks bun"), where="远端 PPO")

    assert host._leg_abort is True
    assert len(verdicts) == 1
    args, kwargs = verdicts[0]
    assert args[1] == 7 and args[2] == "ABORT" and kwargs.get("decider") == "loop"
    assert logs and "GATE ABORT" in logs[0]
    assert root_seen == [], f"补丁打在旧家却仍有输出：{root_seen}"


def test_fail_policy_stops_the_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ `_handle_remote_failure` 真跑：确定性失败**不消耗配额**直接停腿；可重试失败第 3 次停腿。"""
    import rl.loop_remote_fail as fail_mod
    from common.protocol import JobFailedError
    from rl.loop_remote_fail import TrainingRemoteFail

    monkeypatch.setattr(fail_mod, "write_gate_verdict", lambda *a, **k: None)
    monkeypatch.setattr(fail_mod, "log", lambda m: None)
    monkeypatch.setattr(fail_mod, "fatal_remote_http", lambda e: None)

    class _FailHost(TrainingRemoteFail):
        """**真子类**（不是 SimpleNamespace）：`_handle_remote_failure` 内部要
        `self._abort_node_failure`，那条只能经真类解析——这本身就说明「这一簇是一个整体」。"""

        def __init__(self, *, leg_abort: bool, remote_fail: int) -> None:
            self._jsonl_path = Path("unused.jsonl")
            self._leg_abort = leg_abort
            self._remote_fail = remote_fail

    # ① 节点已回报原因（确定性）⇒ 立即停腿、不消耗连败配额
    host = _FailHost(leg_abort=False, remote_fail=0)
    assert host._handle_remote_failure(1, JobFailedError("no bun")) is True
    assert host._leg_abort is True and host._remote_fail == 0

    # ② 可重试失败：计配额，第 3 次（2 → 3）停腿；**没有**本机降级这一档
    host2 = _FailHost(leg_abort=False, remote_fail=2)
    assert host2._handle_remote_failure(5, TimeoutError("flaky")) is True
    assert host2._remote_fail == 3 and host2._leg_abort is True


def test_cross_cluster_handoff_resolves_to_one_object() -> None:
    """★ 跨簇交棒：Drive 借 Fail / Job，Job 借 Push——在**同一个对象**上解析到真实现。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_remote_fail import TrainingRemoteFail
    from rl.loop_remote_job import TrainingRemoteJob
    from rl.loop_remote_push import TrainingRemotePush

    assert TrainingLoop._abort_node_failure is TrainingRemoteFail._abort_node_failure
    assert TrainingLoop._handle_remote_failure is TrainingRemoteFail._handle_remote_failure
    assert TrainingLoop._remote_ppo_publish is TrainingRemoteJob._remote_ppo_publish
    assert TrainingLoop._push_submit_first is TrainingRemotePush._push_submit_first
    # 三个驱动入口的唯一所有者是 Drive（不是组合根，也不是 Job）。
    for name in ("_remote_ppo_step", "_remote_iter", "_remote_run_segment"):
        owners = [k.__name__ for k in TrainingLoop.__mro__ if name in vars(k)]
        assert owners == ["TrainingRemoteDrive"], (name, owners)
