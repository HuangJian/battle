"""拆分的**契约守卫**：产物出包簇（S4 第二十一刀，2026-09-25）。

## 这一刀切了什么

`trainer/loop_steps.py` **667 → 541 行**：4 个方法按**判据同源**（「这一轮要给出去的东西」）搬到
`trainer/loop_export.py::TrainingExport`：

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

1. 五成员定义只在新家（`TrainingSteps` / `loop_steps.py` 里零同名定义）；
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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from tests.helpers import source_scan

NN_ROOT = Path(__file__).resolve().parents[2]
EXPORT_PY = NN_ROOT / "trainer/loop_export.py"
STEPS_PY = NN_ROOT / "trainer/loop_steps.py"
ROUND_STEPS_PY = NN_ROOT / "trainer/loop_round_steps.py"

#: P0-1 第三刀（2026-11-28）：这一簇从混入改成**协作对象** `BundleExporter`。
CLASS = "BundleExporter"
#: 本簇成员（含 2026-11-19 新增的公开读数 `ts_code_view`：G5 第十四批 —— 缓存槽位以前
#: 只有用例伸手进去问，现在有一个不触发打包的只读面；成员集故意逐格列举，加一个就得改这里）。
MEMBERS = (
    "ensure_ts_code",
    "ensure_replay_converter",
    "volume_plan_block",
    "export_offline_bundle",
    "export_weights",
    "ts_code_view",
)

#: 留在 `loop_steps` 的 9 个成员（本刀一个不动）。
#: ★ 2026-10-19 追加一格 `_record_rollout`：采集收官即刻落账（控制台走势图不等 PPO 回传）。
#:   它必须与 `_record_iteration` 同住 `loop_steps`（判据同源 = 写 `training_log.jsonl` 的结算簇），
#:   理由同 `_write_iter_stats`——两处各写一遍落账口径必然漂。
STAYS = (
    "_commit_journal",
    "_course_iter",
    "_forensics",
    "_hot_reload_course",
    "_log_report",
    "_per_stage_quota",
    "_record_iteration",
    "_record_rollout",
    "_write_iter_stats",
    # P0-1 第六刀前置步：结算账本的只读读数面与唯一写面（两者同住本文件）。
    "iter_ledger",
    "land_iter_ledger",
)

#: 组装：`TrainingSteps` 的基类元组（本刀末位追加第三项）。
#: P0-1 第三刀：`TrainingExport` 出列 ⇒ 两件套。
STEPS_BASES = ("TrainingRemote", "TrainingEval")

#: 入边闭集：**转发壳** → {rl 模块: 呼叫点数}（`self.<壳>(` 的真实形态；新入边必须改这张表）。
#: 壳的实现住在协作对象；调用侧（`loop_remote_job` / `loop_round_steps`）与搬家前逐字相同。
INBOUND_CALLS: dict[str, dict[str, int]] = {
    "_ensure_ts_code": {"loop_remote_job.py": 1},
    # P2：replay 转换器 bundle 的懒构建（`_remote_ppo_publish` 在 corpus_fmt=replay 时调）。
    "_ensure_replay_converter": {"loop_remote_job.py": 1},
    "_export_offline_bundle": {"loop_round_steps.py": 1},
    "_export_weights": {"loop_round_steps.py": 1},
}

#: 出边闭集：协作对象调宿主的方法 —— 只许 `BundleHost` **声明过**的那一个
#: （`remote_ppo` = 远端 PPO 的组合入口；调用时解析 ⇒ 既有 patch 仍命中）。
OUTBOUND_HANDS = frozenset({"remote_ppo"})

#: 槽位读者：TS 运行时缓存改由**公开读数** `ts_code_view()` 读（两个远端簇各一处）。
SLOT_HANDS: dict[str, tuple[str, ...]] = {
    "ts_code_view": ("trainer/loop_remote_job.py", "trainer/loop_remote_push.py"),
}

#: 顶层 import 闭集（非 stdlib；本模块不许长出重依赖）。
#: ★ 2026-10-15 登记一格 `common.protocol`（`plan/offline-open-ended-iters-and-loud-export` §3.1 J1）：
#: 无限课那道的响亮行要报**缺省段长**到底是多少，而那个数只能从**同一个常量**读——再抄一份
#: 到本模块就等于把「预判与真闸不许漂」的那一条（同 plan §3.4「同口径对账」）自己拆了。
#: `common.*` 是 L0，不构成反向边（`FORBIDDEN_IMPORTS` 一个不沾）。
#: ★ 2026-10-15 登记一格 `remote`（离线 delta 事故）：包的**起点权重**取自 `args.out`，而
#: 离线腿逐轮回传的是 delta 容器（`BWD1`，hub 原样落位）—— 云机要把它当权重装载，容器会让
#: 整段在 PPO 之前 `JSON.parse` 报 `Unexpected identifier "BWD1"`。落地调用是
#: `remote/weights_standard.ensure_standard_weights`（幂等；整份只读 4 字节）。顶层名是
#: **裸包 `remote`**（`from remote import weights_standard`）；`FORBIDDEN_IMPORTS` 一个不沾。
TOP_IMPORTS = frozenset(
    {
        "common.distribution",
        "worker.archive",
        "common.log",
        # 2026-11-16：仓根单源（`common/paths.py`）—— 此前这里写 `Path(__file__).parents[2]`,
        # 数层数与 `hub/store_offline.py` 是同一类静默故障。
        "common.paths",
        "worker.modes",
        "common.protocol",
        # P2：replay 转换器 bundle 的构建（显式 UTF-8 + Windows 无黑窗）。
        "common.proc",
        "remote",
    }
)
#: `dataclasses` 随 `TsCodeView` 进来（2026-11-19）；`hashlib` 随 replay 转换器（2026-11-15）；
#: `pathlib` 原文就有（`Path`），只是在头部注里没被点名。
STDLIB_IMPORTS = frozenset({"collections.abc", "hashlib", "os", "pathlib", "typing", "dataclasses"})

#: 反向边（禁）：成环或把叶子拉回编排上游。
FORBIDDEN_IMPORTS = frozenset(
    {
        "trainer.loop_core",
        "trainer.loop_eval",
        "trainer.loop_remote",
        "trainer.loop_remote_drive",
        "trainer.loop_remote_fail",
        "trainer.loop_remote_job",
        "trainer.loop_remote_push",
        "trainer.loop_round_steps",
        "trainer.loop_steps",
    }
)

#: 搬走后旧家**不再持有**的模块全局（陈旧 patch 会响亮 AttributeError，而不是静默空操作）。
#: 旧家**全局名**名单（`hasattr(mod, name)` 的口径 = 绑定名，不是点分模块路径）——
#: `dist_common` 随 2026-09-30 刀 2 下沉后，`import common.distribution` 绑定的名字就是 `common`。
GONE_FROM_STEPS = ("common", "backup_weights", "_MODE_BACKUP_PREFIX")

#: 本模块允许的**延迟** import 目标（方法体内；那是原有的注入面）。
DELAYED_IMPORTS = frozenset(
    {
        # `_ensure_ts_code` → published_ts_code_zip（本模块唯一的 remote 触点；
        # 2026-10-10 P1 起 TS 运行时也走集群会话快照，原注入点 `remote.hub_client` 随之下移）
        "remote.code_snapshot",
        "worker.iter_job",  # `_export_offline_bundle` → build_iter_spec
        "worker.plan",  # `_export_offline_bundle` → build_plan / dump_plan / planned_iters / RUN_NODE_LABEL
        "worker.resume",  # `_volume_plan_block` → trailing_samples_per_game
        "worker.volume_waves",  # `_volume_plan_block` → volume_block
        # 2026-10-27（plan/it-stage-ratation §3.3）：窗口在**建块之前**算好（`window_from_args`）
        # —— 三条腿共用一份；本模块只读，不反向被 worker 依赖。
        "worker.stage_rotation",
    }
)
#: 注：`biz.modes`（`_MODE_BACKUP_PREFIX`）是**顶层** import，不在上面那张表里（那张表只要
#: 「方法体内 import」，见 `_delayed_imports` 的 `- top`）。


def _cls(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _methods(path: Path, cls_name: str) -> dict[str, ast.FunctionDef]:
    return {m.name: m for m in _cls(path, cls_name).body if isinstance(m, ast.FunctionDef)}


def _self_call_counts(path: Path, only: frozenset[str] | None = None) -> Mapping[str, int]:
    """AST 计真实 `self.<attr>(…)` 调用（文档字符串/注释里提到不算——见 S4 第二十刀那条教训）。

    实现搬进 `tests.helpers.source_scan.self_call_counts`（缓存版）：本用例对两棵业务树（`trainer/` + `biz/`）
    跑两遍解析（入边 + 定义面），同一个模块里还有别处也要同一份数据。

    `only` = 只关心这些名字（白名单）；入边闭集那几条传它就走上廉价子串预筛，
    与判据无关的文件（两棵业务树里的绝大多数）连 `ast.parse` 都不做。
    """
    return source_scan.self_call_counts(str(path), only)


def _defined_names(path: Path, only: frozenset[str] | None = None) -> frozenset[str]:
    return source_scan.top_level_defs(str(path), only)


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
    """五成员定义只在新家；旧家（`TrainingSteps`）零同名定义。"""
    got = set(_methods(EXPORT_PY, CLASS)) - {"__init__"}
    assert got == set(MEMBERS), f"新家成员集变了：{sorted(got ^ set(MEMBERS))}"
    left = set(_methods(STEPS_PY, "TrainingSteps"))
    assert sorted(left & set(MEMBERS)) == [], "成员又回到 TrainingSteps 了"
    assert sorted(left) == sorted(STAYS), f"留守成员集变了：{sorted(left)}"


def test_assembly_is_a_single_line_and_not_on_the_mro() -> None:
    """协作对象：`__init__` 里一行装配；它**不在** MRO（不再是基类）。"""
    import trainer.loop_export as exp_mod
    from trainer.loop_core import TrainingLoop

    assert exp_mod.BundleExporter.__module__ == "trainer.loop_export"
    assert exp_mod.BundleExporter not in TrainingLoop.__mro__
    asm = [
        n
        for n in _methods(NN_ROOT / "trainer/loop_core.py", "TrainingLoop")["__init__"].body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "exporter" for t in n.targets)
    ]
    assert len(asm) == 1
    val = asm[0].value
    assert isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == CLASS


def test_composition_is_exact() -> None:
    """组装逐字：基类两件套 + `__mro__[1]` + 本簇**不在**组合类直接基类里。"""
    from trainer.loop_core import TrainingLoop
    from trainer.loop_eval import TrainingEval
    from trainer.loop_export import BundleExporter
    from trainer.loop_remote import TrainingRemote
    from trainer.loop_steps import TrainingSteps

    assert tuple(c.__name__ for c in TrainingSteps.__bases__) == STEPS_BASES
    assert TrainingSteps.__bases__ == (TrainingRemote, TrainingEval)
    # 「追加不插队」的把心：2026-09-23 写下的第 2 位断言逐字仍成立。
    assert TrainingSteps.__mro__[1] is TrainingRemote
    # 本簇**不是**组合类的直接基类，也（P0-1 第三刀后）不在 MRO 里。
    assert BundleExporter not in TrainingLoop.__bases__
    assert BundleExporter not in TrainingLoop.__mro__
    assert TrainingLoop.__bases__[1] is TrainingSteps


def test_host_surface_is_declared_and_owned_state_is_object_local() -> None:
    """宿主读面 = `BundleHost` 声明集；4 个缓存槽只在本对象的 `__init__`。"""
    import trainer.loop_export as exp_mod
    from trainer.loop_core import TrainingLoop

    proto = exp_mod.BundleHost
    declared = set(proto.__annotations__) | {
        n for n, v in vars(proto).items() if isinstance(v, property)
    } | {
        n
        for n, v in vars(proto).items()
        if callable(v) and not isinstance(v, property) and not n.startswith("__")
    }
    assert _host_reads(EXPORT_PY, CLASS) == declared, sorted(_host_reads(EXPORT_PY, CLASS) ^ declared)
    owned = {
        t.attr
        for node in _methods(EXPORT_PY, CLASS)["__init__"].body
        for t in (
            list(node.targets)
            if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
    }
    assert owned == {
        "_host",
        "_ts_code_sha256",
        "_ts_code_zip_path",
        "_replay_converter_sha256",
        "_replay_converter_path",
    }, sorted(owned)
    for name in declared - {"args", "bun", "remote_ppo"}:
        assert isinstance(getattr(TrainingLoop, name, None), property), name
    assert callable(getattr(TrainingLoop, "remote_ppo", None))
    # 组合根不再声明这四个缓存槽（双真相）。
    core_slots: set[str] = set()
    for names in _self_attrs(NN_ROOT / "trainer/loop_core.py", "TrainingLoop").values():
        core_slots |= names
    assert sorted(owned - {"_host"} & core_slots) == []


def _host_reads(path: Path, cls_name: str) -> set[str]:
    out: set[str] = set()
    for node in _methods(path, cls_name).values():
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Attribute)
                and n.value.attr == "_host"
                and isinstance(n.value.value, ast.Name)
                and n.value.value.id == "self"
            ):
                out.add(n.attr)
    return out


# ─────────────────────── 入边 / 出边 / 槽位（三张表） ───────────────────────


def test_inbound_hands_closed_set() -> None:
    """入边闭集：只有登记的调用者，且呼叫点数逐一对账（AST，见 S20 的教训）。"""
    files = source_scan.logic_py_files(str(NN_ROOT))
    only = frozenset(INBOUND_CALLS)
    calls = {p.name: _self_call_counts(p, only) for p in files}
    defined = {p.name: _defined_names(p, only) for p in files}
    for member, want in INBOUND_CALLS.items():
        got = {name: c[member] for name, c in calls.items() if member in c}
        assert got == want, f"{member} 的入边变了：{got}"
        # P0-1 第三刀：壳的定义面在组合根，实现只在协作对象。
        definers = {name for name, names in defined.items() if member in names}
        assert definers == {"loop_core.py"}, f"{member} 的定义面：{sorted(definers)}"


def test_outbound_hands_closed_set() -> None:
    """出边闭集：协作对象只调 `BundleHost` 声明过的宿主方法（当前仅 `remote_ppo`）。"""
    import trainer.loop_export as exp_mod

    proto_methods = {
        n
        for n, v in vars(exp_mod.BundleHost).items()
        if callable(v) and not isinstance(v, property) and not n.startswith("__")
    }
    ext = {
        n.func.attr
        for name in MEMBERS
        for n in ast.walk(_methods(EXPORT_PY, CLASS)[name])
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "_host"
    }
    assert ext == set(OUTBOUND_HANDS), f"出边变了：{sorted(ext)}"
    assert set(OUTBOUND_HANDS) <= proto_methods, "协议里没声明它——宿主方法调用就成了暗式依赖"


def test_slot_write_read_hands() -> None:
    """槽位写-读手：缓存写在协作对象，读者只许走 `ts_code_view()`（两个远端簇各一处）。"""
    assigns = _self_assigns(EXPORT_PY, CLASS)
    for slot in ("_ts_code_sha256", "_ts_code_zip_path"):
        writers = {m for m, names in assigns.items() if slot in names}
        assert writers == {"__init__", "ensure_ts_code"}, f"{slot} 的写手变了：{sorted(writers)}"
    for hand, readers in SLOT_HANDS.items():
        for reader in readers:
            text = (NN_ROOT / reader).read_text(encoding="utf-8")
            assert hand in text, f"{hand} 的读者 {reader} 丢了"
    # 远端簇不再直接读那两个私有槽（它们已搬进对象）。
    for reader in ("trainer/loop_remote_job.py", "trainer/loop_remote_push.py"):
        text = (NN_ROOT / reader).read_text(encoding="utf-8")
        assert "_ts_code_sha256" not in text and "_ts_code_zip_path" not in text, reader


def test_old_home_no_longer_absorbs_patches() -> None:
    """★ 旧家不再吸收 patch：搬走的模块全局在 `trainer.loop_steps` 里**不存在**。"""
    import trainer.loop_steps as steps_mod

    for name in GONE_FROM_STEPS:
        assert not hasattr(steps_mod, name), f"trainer.loop_steps.{name} 还在——陈旧 patch 会静默失效"
    # 新家持有本簇真正用到的那些（= 真注入点）。
    import trainer.loop_export as exp_mod

    assert hasattr(exp_mod, "common") and hasattr(exp_mod, "log")
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
    """★ `loop_steps` 的方法间调用只剩**结算账本的唯一写面**（P0-1 第六刀前置步）。

    本刀切掉的是导出那条链；`_log_report` → `land_iter_ledger(fields)` 是新增的唯一
    具名落位入口（写面收口），除此之外仍然零互调。
    """
    per_method = _self_attrs(STEPS_PY, "TrainingSteps")
    own = set(per_method)
    landing = {"land_iter_ledger"}
    for name, names in per_method.items():
        intra = {n for n in names & own if n != name} - landing
        assert intra == set(), f"{name} 与 {sorted(intra)} 又连上了"


# ─────────────────────────────── ★ 功能性 ───────────────────────────────


def _stub(**kw: Any) -> Any:
    """最小宿主 + 协作对象（P0-1 第三刀）：`BundleExporter` 只读 `BundleHost` 那几个面。"""
    import types

    from trainer.loop_export import BundleExporter

    base: dict[str, Any] = dict(
        mode="per-tick",
        target_transitions=0,
        stages="",
        out="w.json",
        iters=3,
        rotate_seed=0,
    )
    base.update(kw)
    host = types.SimpleNamespace(
        args=types.SimpleNamespace(**base),
        bun="bun",
        rotate_seed=int(base["rotate_seed"] or 0),
        traj_root=Path("."),
        jsonl_path=None,
        remote_ppo=lambda *a, **k: {},
    )
    return BundleExporter(host)


def test_volume_plan_block_gates_run_from_the_new_home() -> None:
    """★ `_volume_plan_block` 两道门真跑：非 per-tick / target≤0 一律 None（无声返回）。

    `it` 是 2026-10-21 评审 P0 加的（当时导出侧会读到本轮的空目录，分关 est 全变 fallback）；
    两道门都在用它**之前**返回，所以门内行为与旧签名逐字一致。
    """
    assert _stub(mode="intent", target_transitions=100).volume_plan_block(1) is None  # mode 门
    assert _stub(mode="per-tick", target_transitions=0).volume_plan_block(1) is None  # target 门


def test_ensure_ts_code_cache_semantics(monkeypatch, tmp_path) -> None:
    """★ `_ensure_ts_code` 真跑：seam = `remote.code_snapshot.published_ts_code_zip`（**延迟** import 的面）。

    顺带钉住 S4 的老事故形状：seam 若被搬到这里却仍打旧家命名空间，会**静默空操作**——
    所以这里直接断言「注入点就是实现模块」。★ 2026-10-10（P1）：TS 运行时也走集群会话快照，
    注入点从 `remote.hub_client.pack_ts_code_zip` 上移到 `remote.code_snapshot`（`_ensure_ts_code`
    只与那一个函数打交道：快照优先 / 缺失回落都封在它里面）。
    """
    import remote.code_snapshot as code_snapshot
    from trainer.loop_export import TsCodeView

    calls: list[str] = []

    def fake_published(job_root: Any, *, repo_root: Any, log: Any) -> tuple[Path, str, bool]:
        calls.append(str(job_root))
        return Path(job_root) / "ts_code.zip", "deadbeef", False

    monkeypatch.setattr(code_snapshot, "published_ts_code_zip", fake_published)
    # 真实例（不是 `_stub()` 的 SimpleNamespace）：缓存要经公开读数 `ts_code_view()` 问
    # （G5 第十四批） —— 裸 `__new__` 的实例没有 `__init__` 赋值，正好等于「还没建」。
    obj = _stub()
    assert obj.ts_code_view() == TsCodeView(sha256="", zip_path=None), "初态 = 未建"
    obj.ensure_ts_code(str(tmp_path), log=lambda m: None)
    view = obj.ts_code_view()
    assert view.sha256 == "deadbeef"
    assert view.zip_path == Path(tmp_path) / "ts_code.zip"
    obj.ensure_ts_code(str(tmp_path), log=lambda m: None)  # 第二次
    assert len(calls) == 1, "缓存没生效——第二次又走了一遍发布路径"


def test_ensure_ts_code_falls_back_through_the_snapshot_seam(monkeypatch, tmp_path) -> None:
    """★ 真跑到底（不打桩）：快照不可知 ⇒ per-course 打包落在 job_root，且**不建锚**。

    这是 `_ensure_ts_code` 与快照模块之间的真接线（上面那条只钉注入点）：
    `BCITY_CODE_SNAPSHOT_DIR` 指到一个空目录 ⇒ 走 `published_ts_code_zip` 的回落支，
    真打一份 zip 出来（seam = `code_snapshot.pack_ts_code_zip`，**延迟** import 的面）。
    """
    import remote.code_snapshot as code_snapshot

    snap_dir = tmp_path / "no-snapshot"
    monkeypatch.setenv("BCITY_CODE_SNAPSHOT_DIR", str(snap_dir))
    lines: list[str] = []

    def fake_pack_ts(repo_root: Any, zip_path: Any, *, log: Any) -> str:
        Path(zip_path).parent.mkdir(parents=True, exist_ok=True)
        Path(zip_path).write_bytes(b"PK\x03\x04-ts")
        return "cafebabe"

    monkeypatch.setattr(code_snapshot, "pack_ts_code_zip", fake_pack_ts)
    job_root = tmp_path / "traj" / "course" / "remote-jobs"
    obj = _stub()
    obj.ensure_ts_code(str(job_root), log=lines.append)
    view = obj.ts_code_view()
    assert view.sha256 == "cafebabe"
    assert view.zip_path == job_root / "ts_code.zip"
    assert view.zip_path is not None and view.zip_path.is_file()
    # 回落必须**响亮**（形态判据：只看 WARN 前缀，不钉整句话——文本断言预算只许降），且绝不建锚
    assert any(m.startswith("[run_rl] WARN: 集群代码快照") for m in lines), lines
    assert not (snap_dir / code_snapshot.META_NAME).exists()


def test_export_weights_runs_and_log_seam_lands_here(monkeypatch, tmp_path) -> None:
    """★ `_export_weights` 真跑，且 `log` seam **在本模块**（打旧家 `trainer.loop_steps.log` 收不到）。"""
    import trainer.loop_export as exp_mod
    import trainer.loop_steps as steps_mod

    seen: list[str] = []
    old_seen: list[str] = []
    monkeypatch.setattr(exp_mod, "log", lambda m: seen.append(str(m)))
    monkeypatch.setattr(steps_mod, "log", lambda m: old_seen.append(str(m)))
    monkeypatch.setattr(exp_mod, "backup_weights", lambda *a, **k: "bak/it1.json")

    obj = _stub(out=str(tmp_path / "w.json"), mode="per-tick")
    obj._host.args.backup_prefix = "x"
    obj._host.args.backup_dir = ""
    obj.export_weights(1)
    assert any("already landed" in m for m in seen), seen
    assert any("archived" in m for m in seen), seen
    assert old_seen == [], "打 trainer.loop_steps.log 竟收到了——seam 的落点不对"


def test_export_offline_bundle_fails_loud_without_start_weights(tmp_path, monkeypatch) -> None:
    """★ `_export_offline_bundle` 剩下的那道响亮门真跑：**没有起点权重** ⇒ `SystemExit`。

    ★ 2026-10-15（J1，`plan/offline-open-ended-iters-and-loud-export` §3.1）：课程 `iters<=0`
    **不再是**拒因（无限课改发**开区间段**；那条新口径钉在
    `tests/trainer/test_offline_leg_retired.py::test_infinite_course_exports_an_open_ended_segment`
    ——用哨兵异常证明控制流真的走到了 `build_plan`），所以本用例改钉同函数里**另一道**真能断言
    的响亮退出：包里没有起点的任务等于没任务。

    指纹替成「读不到」：真盘上文件缺失时它是**响亮** `FileNotFoundError`（不是返回 falsy）⇒
    那道门在生产盘上永远走不到，替身才让它回落到同一条 SystemExit。
    """
    import trainer.loop_export as lx

    monkeypatch.setattr(lx.common.distribution, "weights_fingerprint", lambda _p: "")
    obj = _stub(out=str(tmp_path / "w.json"), iters=3)
    obj._host.rotate_seed = 0
    obj._host.bun = "bun"
    try:
        obj.export_offline_bundle(1, [(1, 1)], 2)
    except SystemExit as e:
        assert "没有起点权重" in str(e), e
    else:  # pragma: no cover - 失败分支
        raise AssertionError("没有响亮退出")
