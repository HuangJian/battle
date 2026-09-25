"""拆分的**契约守卫**：产物出包簇（S4 第二十一刀，2026-09-25）。

## 这一刀切了什么

`rl/loop_steps.py` **667 → 541 行**：4 个方法按**判据同源**（「这一轮要给出去的东西」）搬到
`rl/loop_export.py::TrainingExport`：

| 成员 | 判据（给出去的是什么） | 调用者（入边） |
|---|---|---|
| `_ensure_ts_code` | rollout 用的 TS 运行时 zip（内容寻址，同源码只传一次） | `loop_remote._remote_ppo_publish` |
| `_volume_plan_block` | 离线计划里的**动态采集块** | `loop_export._export_offline_bundle`（半离线整段退役前还有一个呼叫者 `loop_remote._remote_run_segment`）|
| `_export_offline_bundle` | 全离线任务包（`--export-bundle`） | `loop_round_steps.step_export_offline_bundle` |
| `_export_weights` | 权重归档 | `loop_round_steps.step_export_weights` |

**本文件里唯一一条方法间调用链**（`_export_offline_bundle` → `_volume_plan_block`）在这一簇里；
搬走后 `loop_steps` **零方法间调用**（8 个成员全是叶子，各自被轮内步骤调用）——这一条由
`test_loop_steps_left_has_zero_intra_calls` 正面钉住（散文里写「谁调谁」之前先量调用点）。

## 宿主：`TrainingSteps` 的**末位**基类（追加不插队）

```
class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport):
```

调用者两处都在 MRO 更靠前：`RoundSteps`（组合根的第一个基类）· `TrainingRemote`
（`TrainingSteps` 的第一个基类）⇒ 挂 `TrainingSteps` 的末位基类即可全部解析。
依据（实测）：4 个成员名在既有混入里**零同名定义**，末位追加不会被遮罩。
代价：`TrainingSteps.__bases__` 两件套 → 三件套 ⇒ 三处旧断言（`test_loop_transport_split.py` /
`test_loop_eval_split.py` / `test_loop_lifecycle_split.py`）**演进登记**；三处的**把心**未动：
`__mro__[1] is TrainingRemote` 逐字不变、本簇**不是**组合类的直接基类。
全量 MRO 名单的唯一所有者在 `test_loop_core_tail_split.py`（S4 第二十刀那篇）。

## 本文件钉住什么

1. 四成员定义只在新家（`TrainingSteps` / `loop_steps.py` 里零同名定义）；
2. 接线是**对象级**同一（`TrainingSteps.X is TrainingExport.X`）；
3. 组装逐字：`TrainingSteps.__bases__` 三件套 + `__mro__[1]` + 本簇不在组合类直接基类里；
4. 借用声明闭集 = 派生集（含 `args`），且类体零带值槽位；
5. **入边闭集**（AST 计真实 `Call` 节点）× **出边闭集**（只有声明的 `_remote_ppo`）
   × **槽位写-读手**（`_ts_code_sha256` / `_ts_code_zip_path` 本模块写、`loop_remote` 读）；
6. 顶层 import 闭集 / 禁反向 import / DI 目标只许方法体内**延迟** import；
7. ★ **功能性**：`_volume_plan_block` 两道 mode/target 门真跑 · `_ensure_ts_code` 缓存语义真跑
   （seam = `remote.hub_client.pack_ts_code_zip`）· `_export_weights` 真跑且 `log` seam **落在本模块**
   （打旧家收不到）· `_export_offline_bundle` 无起点权重时**响亮 SystemExit** · 旧家不再吸收 patch。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

NN_ROOT = Path(__file__).resolve().parent.parent
EXPORT_PY = NN_ROOT / "rl/loop_export.py"
STEPS_PY = NN_ROOT / "rl/loop_steps.py"
ROUND_STEPS_PY = NN_ROOT / "rl/loop_round_steps.py"

CLASS = "TrainingExport"
MEMBERS = ("_ensure_ts_code", "_volume_plan_block", "_export_offline_bundle", "_export_weights")

#: 留在 `loop_steps` 的 8 个成员（本刀一个不动）。
STAYS = (
    "_commit_journal",
    "_course_iter",
    "_forensics",
    "_hot_reload_course",
    "_log_report",
    "_per_stage_quota",
    "_record_iteration",
    "_write_iter_stats",
)

#: 组装：`TrainingSteps` 的基类元组（本刀末位追加第三项）。
STEPS_BASES = ("TrainingRemote", "TrainingEval", "TrainingExport")

#: 入边闭集：成员 → {rl 模块: 呼叫点数}（`self.<成员>(` 的真实形态；新入边必须改这张表）。
#: S4 第二十二刀把 `loop_remote` 切成四簇后，两条入边各自换了落点（实现所在模块）：
#: `_ensure_ts_code` 的呼叫者在 `_remote_ppo_publish`（→ `loop_remote_job.py`），
#: `_volume_plan_block` 的另一个呼叫者曾住 `_remote_run_segment`（`loop_remote_drive.py`）——
#: 它随**半离线整段退役**（2026-09-25 并入 origin）一起消失 ⇒ 现在只剩 loop_export 自己那条链
#: （`_export_offline_bundle` → 它）。退役的正面守卫在 `tests/test_offline_leg_retired.py`。
INBOUND_CALLS: dict[str, dict[str, int]] = {
    "_ensure_ts_code": {"loop_remote_job.py": 1},
    # 只剩 loop_export 自己那条链（`_export_offline_bundle` → 它）。
    "_volume_plan_block": {"loop_export.py": 1},
    "_export_offline_bundle": {"loop_round_steps.py": 1},
    "_export_weights": {"loop_round_steps.py": 1},
}

#: 出边闭集：本模块成员调用的**兄弟**方法（混入常态的动态解析；声明块里必须有它）。
OUTBOUND_HANDS = frozenset({"_remote_ppo"})

#: 槽位写-读手：本模块**写**、别处**读**的槽（写-读手契约；读者少一个也红）。
#: S4 第二十二刀后读者在两个新家里：`_remote_ppo_publish`（Job）与 `_push_submit_node`（Push）。
SLOT_HANDS: dict[str, tuple[str, ...]] = {
    "_ts_code_sha256": ("rl/loop_remote_job.py",),
    "_ts_code_zip_path": ("rl/loop_remote_job.py", "rl/loop_remote_push.py"),
}

#: 顶层 import 闭集（非 stdlib；本模块不许长出重依赖）。
TOP_IMPORTS = frozenset({"dist_common", "rl.archive", "rl.log", "rl.modes"})
STDLIB_IMPORTS = frozenset({"pathlib", "typing"})

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "rl.loop_core",
        "rl.loop_eval",
        "rl.loop_remote",
        "rl.loop_remote_drive",
        "rl.loop_remote_fail",
        "rl.loop_remote_job",
        "rl.loop_remote_push",
        "rl.loop_round_steps",
        "rl.loop_steps",
    }
)

#: 搬走后旧家**不再持有**的模块全局（陈旧 patch 会响亮 AttributeError，而不是静默空操作）。
GONE_FROM_STEPS = ("dist_common", "backup_weights", "_MODE_BACKUP_PREFIX")

#: 本模块允许的**延迟** import 目标（方法体内；那是原有的注入面）。
DELAYED_IMPORTS = frozenset(
    {
        "remote.hub_client",  # `_ensure_ts_code` → pack_ts_code_zip（本模块唯一的 remote 触点）
        "rl.iter_job",  # `_export_offline_bundle` → build_iter_spec
        "rl.plan",  # `_export_offline_bundle` → build_plan / dump_plan / planned_iters / RUN_NODE_LABEL
        "rl.resume",  # `_volume_plan_block` → trailing_samples_per_game
        "rl.volume_waves",  # `_volume_plan_block` → volume_block
    }
)
#: 注：`rl.modes`（`_MODE_BACKUP_PREFIX`）是**顶层** import，不在上面那张表里（那张表只要
#: 「方法体内 import」，见 `_delayed_imports` 的 `- top`）。


def _cls(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _methods(path: Path, cls_name: str) -> dict[str, ast.FunctionDef]:
    return {m.name: m for m in _cls(path, cls_name).body if isinstance(m, ast.FunctionDef)}


def _self_call_counts(path: Path) -> dict[str, int]:
    """AST 计真实 `self.<attr>(…)` 调用（文档字符串/注释里提到不算——见 S4 第二十刀那条教训）。"""
    out: dict[str, int] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            out[node.func.attr] = out.get(node.func.attr, 0) + 1
    return out


def _defined_names(path: Path) -> set[str]:
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


def _delayed_imports(path: Path, cls_name: str) -> set[str]:
    """方法体内（非模块顶层）出现的 import 目标——DI 只许走这条路。"""
    top = _top_imports(path)
    inside: set[str] = set()
    for node in _methods(path, cls_name).values():
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                inside.update(a.name for a in sub.names)
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                inside.add(sub.module)
    return inside - top


def _self_attrs(path: Path, cls_name: str) -> dict[str, set[str]]:
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


def _self_assigns(path: Path, cls_name: str) -> dict[str, set[str]]:
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


# ─────────────────────────── 成员 / 接线 / 组装 ───────────────────────────


def test_members_live_in_the_new_home_only() -> None:
    """四成员定义只在新家；旧家（`TrainingSteps`）零同名定义。"""
    got = set(_methods(EXPORT_PY, CLASS))
    assert got == set(MEMBERS), f"新家成员集变了：{sorted(got ^ set(MEMBERS))}"
    left = set(_methods(STEPS_PY, "TrainingSteps"))
    assert sorted(left & set(MEMBERS)) == [], "成员又回到 TrainingSteps 了"
    assert sorted(left) == sorted(STAYS), f"留守成员集变了：{sorted(left)}"


def test_wiring_is_object_identity() -> None:
    """`TrainingSteps.X is TrainingExport.X`（同一个函数对象，不是同名副本）。"""
    import rl.loop_export as exp_mod
    from rl.loop_steps import TrainingSteps

    home = exp_mod.TrainingExport
    for name in MEMBERS:
        got = getattr(TrainingSteps, name)
        assert got is getattr(home, name), name
        assert got.__module__ == "rl.loop_export", name


def test_composition_is_exact() -> None:
    """组装逐字：基类三件套 + `__mro__[1]` + 本簇**不在**组合类直接基类里。"""
    from rl.loop_core import TrainingLoop
    from rl.loop_eval import TrainingEval
    from rl.loop_export import TrainingExport
    from rl.loop_remote import TrainingRemote
    from rl.loop_steps import TrainingSteps

    assert tuple(c.__name__ for c in TrainingSteps.__bases__) == STEPS_BASES
    assert TrainingSteps.__bases__ == (TrainingRemote, TrainingEval, TrainingExport)
    # 「追加不插队」的把心：2026-09-23 写下的第 2 位断言逐字仍成立。
    assert TrainingSteps.__mro__[1] is TrainingRemote
    # 本簇**不是**组合类的直接基类（挂的是 `TrainingSteps` 一侧）⇒ 组合类与四个「继承真混入」
    # 的测试宿主都不必改。
    assert TrainingExport not in TrainingLoop.__bases__
    assert TrainingExport in TrainingLoop.__mro__
    assert TrainingLoop._export_weights is TrainingExport._export_weights


def test_borrowed_declarations_are_exactly_the_touched_set() -> None:
    """借用声明闭集 = 派生集；类体零带值槽位。"""
    touched: set[str] = set()
    for names in _self_attrs(EXPORT_PY, CLASS).values():
        touched |= names
    own = set(_methods(EXPORT_PY, CLASS))
    top: set[str] = set()
    for n in ast.parse(EXPORT_PY.read_text(encoding="utf-8")).body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            top.add(n.name)
        elif isinstance(n, ast.Assign):
            top.update(t.id for t in n.targets if isinstance(t, ast.Name))
    borrowed = {n for n in touched - own - top if not n.startswith("__")}
    assert borrowed, "派生集为空说明重建坏了"
    import rl.loop_export as exp_mod

    assert set(exp_mod.TrainingExport.__annotations__) == borrowed, sorted(borrowed)
    body = _cls(EXPORT_PY, CLASS).body
    assert not [n for n in body if isinstance(n, ast.Assign)]
    assert not [n for n in body if isinstance(n, ast.AnnAssign) and n.value is not None]


# ─────────────────────── 入边 / 出边 / 槽位（三张表） ───────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边闭集：只有登记的调用者，且呼叫点数逐一对账（AST，见 S20 的教训）。"""
    rl_dir = NN_ROOT / "rl"
    files = sorted(rl_dir.glob("*.py"))
    calls = {p.name: _self_call_counts(p) for p in files}
    defined = {p.name: _defined_names(p) for p in files}
    for member, want in INBOUND_CALLS.items():
        got = {name: c[member] for name, c in calls.items() if member in c}
        assert got == want, f"{member} 的入边变了：{got}"
        definers = {name for name, names in defined.items() if member in names}
        assert definers == {"loop_export.py"}, f"{member} 的定义面：{sorted(definers)}"


def test_outbound_hands_closed_set() -> None:
    """出边闭集：本模块只许调声明过的那一个兄弟方法。"""
    own = set(_methods(EXPORT_PY, CLASS))
    ext: set[str] = set()
    for name in MEMBERS:
        ext |= {
            c for c in _self_calls(EXPORT_PY, CLASS, name) if c not in own and not c.startswith("__")
        }
    assert ext == set(OUTBOUND_HANDS), f"出边变了：{sorted(ext)}"
    # 声明块里必须有它（混入常态：在组合实例上解析，故声明类型）。
    import rl.loop_export as exp_mod

    for hand in OUTBOUND_HANDS:
        assert hand in exp_mod.TrainingExport.__annotations__, hand


def test_slot_write_read_hands() -> None:
    """槽位写-读手：本模块写、远端两簇读（写手在这里，读者不能在别处丢）。"""
    assigns = _self_assigns(EXPORT_PY, CLASS)
    for slot, readers in SLOT_HANDS.items():
        writers = {m for m, names in assigns.items() if slot in names}
        assert writers == {"_ensure_ts_code"}, f"{slot} 的写手变了：{sorted(writers)}"
        for reader in readers:
            assert slot in (NN_ROOT / reader).read_text(encoding="utf-8"), (
                f"{slot} 的读者 {reader} 丢了"
            )


def test_old_home_no_longer_absorbs_patches() -> None:
    """★ 旧家不再吸收 patch：搬走的模块全局在 `rl.loop_steps` 里**不存在**。"""
    import rl.loop_steps as steps_mod

    for name in GONE_FROM_STEPS:
        assert not hasattr(steps_mod, name), f"rl.loop_steps.{name} 还在——陈旧 patch 会静默失效"
    # 新家持有本簇真正用到的那些（= 真注入点）。
    import rl.loop_export as exp_mod

    assert hasattr(exp_mod, "dist_common") and hasattr(exp_mod, "log")
    assert hasattr(exp_mod, "backup_weights") and hasattr(exp_mod, "_MODE_BACKUP_PREFIX")


def test_top_level_imports_closed_no_reverse_edges() -> None:
    """顶层 import 闭集 + 禁反向边 + DI 目标只许**延迟** import。"""
    got = _top_imports(EXPORT_PY)
    assert got & FORBIDDEN_IMPORTS == set(), f"长出反向边：{got & FORBIDDEN_IMPORTS}"
    assert got >= TOP_IMPORTS, f"少了：{sorted(TOP_IMPORTS - got)}"
    extra = got - TOP_IMPORTS - STDLIB_IMPORTS - {"__future__"}
    assert extra == set(), f"顶层 import 面长出：{sorted(extra)}"
    delayed = _delayed_imports(EXPORT_PY, CLASS)
    assert delayed <= DELAYED_IMPORTS, f"方法体内多出延迟 import：{sorted(delayed - DELAYED_IMPORTS)}"


def test_loop_steps_left_has_zero_intra_calls() -> None:
    """★ 搬走后 `loop_steps` **零方法间调用**（本刀切掉的是它最后一条链）。"""
    per_method = _self_attrs(STEPS_PY, "TrainingSteps")
    own = set(per_method)
    for name, names in per_method.items():
        intra = {n for n in names & own if n != name}
        assert intra == set(), f"{name} 与 {sorted(intra)} 又连上了"


# ─────────────────────────────── ★ 功能性 ───────────────────────────────


def _stub(**kw: Any) -> Any:
    import types

    base = dict(mode="per-tick", target_transitions=0, stages="", out="w.json", iters=3)
    base.update(kw)
    return types.SimpleNamespace(args=types.SimpleNamespace(**base), _jsonl_path=None)


def test_volume_plan_block_gates_run_from_the_new_home() -> None:
    """★ `_volume_plan_block` 两道门真跑：非 per-tick / target≤0 一律 None（无声返回）。"""
    from rl.loop_export import TrainingExport

    fn = cast(Any, TrainingExport._volume_plan_block)
    assert fn(_stub(mode="intent", target_transitions=100)) is None  # mode 门
    assert fn(_stub(mode="per-tick", target_transitions=0)) is None  # target 门


def test_ensure_ts_code_cache_semantics(monkeypatch, tmp_path) -> None:
    """★ `_ensure_ts_code` 真跑：seam = `remote.hub_client.pack_ts_code_zip`（**延迟** import 的面）。

    顺带钉住 S4 的老事故形状：seam 若被搬到这里却仍打旧家命名空间，会**静默空操作**——
    所以这里直接断言「注入点就是实现模块」。
    """
    import remote.hub_client as hub_client
    from rl.loop_export import TrainingExport

    calls: list[str] = []

    def fake_pack(repo_root: Any, zip_path: Any, *, log: Any) -> str:
        calls.append(str(zip_path))
        return "deadbeef"

    monkeypatch.setattr(hub_client, "pack_ts_code_zip", fake_pack)
    obj = _stub()
    obj._ts_code_sha256 = ""
    cast(Any, TrainingExport._ensure_ts_code)(obj, str(tmp_path), log=lambda m: None)
    assert obj._ts_code_sha256 == "deadbeef"
    assert obj._ts_code_zip_path == Path(tmp_path) / "ts_code.zip"
    cast(Any, TrainingExport._ensure_ts_code)(obj, str(tmp_path), log=lambda m: None)  # 第二次
    assert len(calls) == 1, "缓存没生效——第二次又打了一遍 zip"


def test_export_weights_runs_and_log_seam_lands_here(monkeypatch, tmp_path) -> None:
    """★ `_export_weights` 真跑，且 `log` seam **在本模块**（打旧家 `rl.loop_steps.log` 收不到）。"""
    import rl.loop_export as exp_mod
    import rl.loop_steps as steps_mod
    from rl.loop_export import TrainingExport

    seen: list[str] = []
    old_seen: list[str] = []
    monkeypatch.setattr(exp_mod, "log", lambda m: seen.append(str(m)))
    monkeypatch.setattr(steps_mod, "log", lambda m: old_seen.append(str(m)))
    monkeypatch.setattr(exp_mod, "backup_weights", lambda *a, **k: "bak/it1.json")

    obj = _stub(out=str(tmp_path / "w.json"), mode="per-tick")
    obj.args.backup_prefix = "x"
    obj.args.backup_dir = ""
    cast(Any, TrainingExport._export_weights)(obj, 1)
    assert any("already landed" in m for m in seen), seen
    assert any("archived" in m for m in seen), seen
    assert old_seen == [], "打 rl.loop_steps.log 竟收到了——seam 的落点不对"


def test_export_offline_bundle_fails_loud_without_iters(tmp_path) -> None:
    """★ `_export_offline_bundle` 的响亮门真跑：课程没声明 iters ⇒ `SystemExit`（不猜终点）。

    为什么测这道门而不测「无起点权重」那道：后者第一刀就落到
    `dist_common.weights_fingerprint(args.out)`——**文件不存在时它响亮 `FileNotFoundError`**
    （不是返回 falsy），所以「无起点权重」在盘上根本走不到（既有行为，本刀不动它）；
    而 iters 这道门在第 **一** 行，是真正能断言的响亮退出。
    """
    from rl.loop_export import TrainingExport

    obj = _stub(out=str(tmp_path / "w.json"), iters=0)
    obj._rotate_seed = 0
    obj.bun = "bun"
    try:
        cast(Any, TrainingExport._export_offline_bundle)(obj, 1, [(1, 1)], 2)
    except SystemExit as e:
        assert "iters" in str(e), e
    else:  # pragma: no cover - 失败分支
        raise AssertionError("没有响亮退出")
