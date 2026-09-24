"""拆分的**契约守卫**：`_JobStore` 的六个域混入永住 `remote/hub/store_*.py`（S4 第十四刀，2026-09-24）。

> **S4 第十五刀补记**：第十四刀只搬了**六个混入**，组合类 `_JobStore` 还留在 `hub_server.py`；
> 第十五刀（`_HubQueue` 拆分）把组合类与鉴权原语一起下沉到 `hub/store.py` / `hub/auth.py`。
> 本文件读**源码文本**的那几条因此改读新家（`STORE_MOD` / `AUTH_MOD`）——
> 「名字是契约，位置不是」对**对象**成立（`hs._JobStore` 仍解析），对**源码文本**不成立。

## 这一刀切了什么

`remote/hub_server.py` 3017 → 2072 行：`_JobStore`（1002 行 / 49 方法）按**域**拆成六个混入，
组合类只留 `__init__` / `note_worker` 与**进程级状态**：

```
class _JobStore(LedgerMixin, WireMeterMixin, SchedulingMixin, LeaseMixin,
                ResultsMixin, OfflineRoundsMixin, _AuthGuard)
  store_ledger     磁盘事实来源：job 目录 · jsonl 账本（H6 增量读）· 可领取池 · 发布 · 读回
  store_wire       M0 统一计量：每 job 的传输层实测字节（与其它簇零互调）
  store_scheduling 调度优先级：`_claimed` / `_computing` / `_ready` / `_epoch`
  store_leases     租约 · 心跳 · 毒包熔断 · job 终局（最大的一簇）
  store_results    回传落位：结果 / 失败标记 / BC 每 epoch 快照
  store_offline    离线段自回传产物（三件齐全 = 续跑锚点）
```

## 为什么是「混入」而不是「协作对象」（本文件钉的就是这三条）

1. **一把锁是类的不变式**：30/49 个方法在同一把 `_lock` 下（`_*_locked` 后缀标的就是临界区内的
   那半）。协作对象各持一把锁 = **换语义**（并发行为不同），不满足「零行为变化」；
2. **跨域互调是常态**（37/49）：`_claim_locked` → `_job_priority_locked` /
   `_collect_expired_locked` → `_drop_commitment_locked` → `_bump_epoch_locked`…混入把它们留在
   `self.X` 上 ⇒ **零 seam**；
3. **tests 直接读私有属性**（`store._leases` / `._lease_owners` / `._stale_holders` / `._claimed` /
   `._backup_authorized` / `._last_heartbeat` / `halt_workers`，20+ 处断言）——协作对象会让这些
   **全部改路**；混入是同一个对象 ⇒ 一行测试都不用改。

代价是**状态声明分散到四个 `_init_*`**（`store_results` / `store_offline` 真的没有常驻状态，
不造 `pass` 空钩）。所以 ④ 那一节既钉「谁拥有哪个字段」，也钉「没钩子的域确实零赋值」。

## 本文件钉住的东西

1. **定义唯一**：每个方法只在一个混入里实现，`_JobStore` 不得再定义；六个混入两两不重名；
2. **接线正确**：`_JobStore.X is Mixin.X`（同一函数对象）+ MRO 精确 + 类常量经 MRO 可达；
3. **注入点不变**：`_leases` / `_claimed` / `_wire` … 仍在**同一个对象**上（直读测试的前提）；
4. **状态归属唯一**：每个 `self.X = …` 只有一个主人；无钩子的混入零赋值；`__init__` 显式调用
   每个钩子**恰好一次**；混入里的裸注解不许带值（带了就是第二份实现）；
5. **依赖方向**：六个混入零上向依赖、**彼此零 import**（跨域调用经 `self`）；账本层号关系；
6. **★ 功能性**：真的建一个 store 跑一遍跨域链路（发布 → 认领 → 计量 → 读回），并证明
   **连最「独立」的簇也在同一把锁下**（持有 `_lock` 时 `wire_stats` 必须阻塞）。
"""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub.store_leases as leases_mod
import remote.hub.store_ledger as ledger_mod
import remote.hub.store_offline as offline_mod
import remote.hub.store_results as results_mod
import remote.hub.store_scheduling as scheduling_mod
import remote.hub.store_wire as wire_mod
from remote import hub_server as hs
from tests.helpers import remote_dag as dag

NN_ROOT = ROOT
HUB_DIR = NN_ROOT / "remote" / "hub"
HUB_SERVER = NN_ROOT / "remote" / "hub_server.py"
#: 组合类与鉴权原语的**新家**（S4 第十五刀从 `hub_server.py` 搬出）。本文件原先一律读
#: `HUB_SERVER` 取 `_JobStore` / `_AuthGuard` 的类体；搬走之后读源码的那几条要改路
#: ——「名字是契约，位置不是」对**对象**成立（`hs._JobStore` 仍可解析），对**源码文本**不成立。
STORE_MOD = HUB_DIR / "store.py"
AUTH_MOD = HUB_DIR / "auth.py"

#: 域 -> （混入类, 文件内实现的方法, 该域在 `_init_*` 里声明的状态）
DOMAINS: dict[str, tuple[type, tuple[str, ...], tuple[str, ...]]] = {
    "store_ledger": (
        ledger_mod.LedgerMixin,
        (
            "_job_dir",
            "_read_ledger",
            "_append_ledger",
            "claimable_job_ids",
            "publish",
            "job_failure",
            "get_result",
        ),
        ("_ledger_cache",),
    ),
    "store_wire": (
        wire_mod.WireMeterMixin,
        ("record_payload_sent", "record_push_wire", "record_result_recv", "wire_stats"),
        ("_wire",),
    ),
    "store_scheduling": (
        scheduling_mod.SchedulingMixin,
        (
            "_bump_epoch_locked",
            "scheduling_epoch",
            "start_job",
            "set_ready",
            "scheduling_facts",
            "_facts_locked",
            "_job_priority_locked",
            "priority_for",
            "_drop_commitment_locked",
        ),
        ("_claimed", "_computing", "_ready", "_epoch"),
    ),
    "store_leases": (
        leases_mod.LeaseMixin,
        (
            "claim",
            "claim_outcome",
            "_claim_locked",
            "abandon_job",
            "_collect_expired_locked",
            "reclaims",
            "frozen_info",
            "frozen_job_ids",
            "consume_freeze_announcement",
            "unfreeze",
            "stale_holder",
            "lease_worker",
            "inflight",
            "heartbeat",
            "release",
            "result_token_ok",
            "mark_completed",
        ),
        (
            "_leases",
            "_lease_owners",
            "_last_heartbeat",
            "_lease_workers",
            "_stale_holders",
            "_reclaims",
            "_frozen",
            "_backup_authorized",
        ),
    ),
    "store_results": (
        results_mod.ResultsMixin,
        ("store_result", "store_job_failure", "store_bc_epoch", "get_bc_resume", "get_bc_metrics"),
        (),
    ),
    "store_offline": (
        offline_mod.OfflineRoundsMixin,
        (
            "complete_rounds",
            "offline_run_dir",
            "store_offline_artifact",
            "_land_round_metrics",
            "store_offline_result",
        ),
        (),
    ),
}
MIXIN_METHODS = tuple(m for _, (_, ms, _) in DOMAINS.items() for m in ms)
STATEFUL = tuple(d for d, (_, _, st) in DOMAINS.items() if st)
STATELESS = tuple(d for d, (_, _, st) in DOMAINS.items() if not st)

#: 组合类自己留的（进程级 + 构造）：`halt_workers` / `_workers` 是**进程**概念，
#: 多课程单 hub 下 `_HubQueue` 借的就是这一份（见其 docstring ③）。
OWN_METHODS = ("__init__", "note_worker")
PROCESS_STATE = ("halt_workers", "_workers")

#: 六个混入 + 两个「搬出后新增的邻居」允许的仓内依赖（多一个就说明又搬漏/搬多了）。
ALLOWED_IMPORTS = {
    # 鉴权原语（S4 第十五刀）：只靠标准库（`time` / `threading`）⇒ 零仓内依赖。
    "remote.hub.auth": set(),
    # 组合类的**新家**（S4 第十五刀）：六个混入 + 鉴权原语，全是向下。
    "remote.hub.store": {
        "remote.hub.auth",
        "remote.hub.store_ledger",
        "remote.hub.store_leases",
        "remote.hub.store_offline",
        "remote.hub.store_results",
        "remote.hub.store_scheduling",
        "remote.hub.store_wire",
    },
    "remote.hub.store_ledger": {"common.protocol"},
    "remote.hub.store_wire": set(),
    "remote.hub.store_scheduling": {"common.protocol"},
    "remote.hub.store_leases": {"common.protocol"},
    "remote.hub.store_results": {"common.protocol"},
    "remote.hub.store_offline": {"common.fs", "common.protocol", "remote.artifacts"},
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _cls(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)


def _own_defs(path: Path, cls_name: str) -> set[str]:
    """类体内**自己定义**的方法名（不看裸注解：注解不产生 `__dict__` 条目）。"""
    return {
        n.name for n in _cls(_tree(path), cls_name).body if isinstance(n, ast.FunctionDef)
    }


def _init_state(path: Path, cls_name: str, hook: str) -> set[str]:
    """`_init_*` 里 `self.<attr> = …` 的字段名（赋值才算拥有）。"""
    node = next(
        n
        for n in _cls(_tree(path), cls_name).body
        if isinstance(n, ast.FunctionDef) and n.name == hook
    )
    out: set[str] = set()
    for c in ast.walk(node):
        targets = (
            c.targets
            if isinstance(c, ast.Assign)
            else ([c.target] if isinstance(c, ast.AnnAssign) else [])
        )
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                out.add(t.attr)
    return out


def _self_assignments(path: Path, cls_name: str) -> set[str]:
    """整个类（含所有方法）里所有 `self.<attr> = …` 的字段名。"""
    out: set[str] = set()
    for m in _cls(_tree(path), cls_name).body:
        if not isinstance(m, ast.FunctionDef):
            continue
        for c in ast.walk(m):
            targets = (
                c.targets
                if isinstance(c, ast.Assign)
                else ([c.target] if isinstance(c, ast.AnnAssign) else [])
            )
            for t in targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                ):
                    out.add(t.attr)
    return out


# ───────────────────── ① 定义唯一 ─────────────────────


def test_every_method_lives_in_exactly_one_mixin() -> None:
    """每个方法只在一个混入里**实现**；`_JobStore` 不得再定义任何一个（组合类只组合）。"""
    own = _own_defs(STORE_MOD, "_JobStore")
    assert sorted(own & set(MIXIN_METHODS)) == [], (
        f"这些方法又回到 _JobStore 了：{sorted(own & set(MIXIN_METHODS))}"
    )
    for name in OWN_METHODS:
        assert name in own, f"_JobStore 少了 {name}"

    seen: dict[str, str] = {}
    for domain, (cls, methods, _) in DOMAINS.items():
        defined = _own_defs(HUB_DIR / f"{domain}.py", cls.__name__)
        missing = sorted(set(methods) - defined)
        assert missing == [], f"{domain}.py 少了 {missing}"
        for m in defined & set(MIXIN_METHODS):
            assert m not in seen, f"{m} 同时住 {seen[m]} 与 {domain}（实现不唯一）"
            seen[m] = domain
    assert len(MIXIN_METHODS) == 47, len(MIXIN_METHODS)
    assert len(seen) == 47, len(seen)
    assert len(MIXIN_METHODS) + len(OWN_METHODS) == 49, "旧 _JobStore 共 49 个方法"


def test_the_mixins_do_not_share_any_defined_name() -> None:
    """六个混入的**实现名**（方法 + 类常量）两两不交（同名才会让 MRO 顺序变成语义）。"""
    owners: dict[str, str] = {}
    for domain, (cls, _, _) in DOMAINS.items():
        path = HUB_DIR / f"{domain}.py"
        names = set(_own_defs(path, cls.__name__))
        for n in _cls(_tree(path), cls.__name__).body:
            if isinstance(n, ast.Assign):
                names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                names.add(n.target.id)
        for m in names:
            assert m not in owners, f"{m} 在 {owners[m]} 与 {domain} 里各定义了一份"
            owners[m] = domain
    assert {"OFFLINE_DIR", "RESUME_PARTS", "BC_EPOCH_BODY_MAX"} <= set(owners)


# ───────────────────── ② 接线（对象级） ─────────────────────


def test_the_mixins_are_wired_into_the_store() -> None:
    """★ 对象级证明：`_JobStore.X is Mixin.X`（不是同名副本，也不是漏接了一个）。"""
    for domain, (cls, methods, _) in DOMAINS.items():
        for m in methods:
            assert getattr(hs._JobStore, m) is getattr(cls, m), f"{domain}::{m} 没接上 _JobStore"


def test_the_mro_is_exactly_the_six_mixins_plus_auth_guard() -> None:
    """MRO 逐项对账——多一个少一个都要红（否则「组合」只是口头上的）。"""
    assert [c.__name__ for c in hs._JobStore.__mro__] == [
        "_JobStore",
        "LedgerMixin",
        "WireMeterMixin",
        "SchedulingMixin",
        "LeaseMixin",
        "ResultsMixin",
        "OfflineRoundsMixin",
        "_AuthGuard",
        "object",
    ]
    want_bases = (*[cls for cls, _, _ in DOMAINS.values()], hs._AuthGuard)
    assert hs._JobStore.__bases__ == want_bases


def test_the_moved_class_constants_stay_reachable_through_the_mro() -> None:
    """`_HubQueue` 与测试按 `_JobStore.X` 取这几个常量（它们随簇搬走，但**名字是契约**）。"""
    assert hs._JobStore.OFFLINE_DIR == offline_mod.OfflineRoundsMixin.OFFLINE_DIR == "offline"
    assert hs._JobStore.RESUME_PARTS == ("weights.json", "opt.tar", "row.json")
    assert hs._JobStore.BC_EPOCH_BODY_MAX == 4 * 1024 * 1024
    assert hs._JobStore.BC_RESUME_NAME == "bc-resume.json"
    # 熔断阈值与 ClaimOutcome 也随租约簇搬走，但 `remote.hub_server` 仍是取名字的入口
    assert hs.FREEZE_AFTER_RECLAIMS == leases_mod.FREEZE_AFTER_RECLAIMS == 3
    assert hs.ClaimOutcome is leases_mod.ClaimOutcome
    assert hs._JobStore.BC_EPOCH_BODY_MAX == hs._HubQueue.BC_EPOCH_BODY_MAX


# ───────────────────── ③ 注入点不变（同一个对象） ─────────────────────


def test_the_private_state_still_lives_on_one_object(tmp_path: Path) -> None:
    """★ 直读测试的前提：19 个私有状态全在**同一个** `_JobStore` 实例上（协作对象会拆散它）。"""
    store = hs._JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")
    for domain, (_, _, state) in DOMAINS.items():
        for name in state:
            assert hasattr(store, name), f"{domain} 的 {name} 不在实例上"
    for name in ("job_root", "jsonl_path", *PROCESS_STATE):
        assert hasattr(store, name), name
    # 通用助手面（`_AuthGuard`）也照旧
    for name in ("auth_failure", "auth_success", "is_blocked", "blocked_remaining"):
        assert callable(getattr(store, name)), name


# ───────────────────── ④ 状态归属唯一 ─────────────────────


def test_each_init_hook_owns_its_fields_and_nobody_else_does() -> None:
    """★ 字段只许一个主人：`_init_*` 的赋值集合 == 声明的清单，且六个域两两不交。"""
    owners: dict[str, str] = {}
    for domain in STATEFUL:
        cls, _, declared = DOMAINS[domain]
        path = HUB_DIR / f"{domain}.py"
        got = _init_state(path, cls.__name__, f"_init_{domain.removeprefix('store_')}")
        assert got == set(declared), f"{domain} 的 `_init_*` 赋值 {sorted(got)} != 声明 {sorted(declared)}"
        for name in got:
            assert name not in owners, f"{name} 同时被 {owners[name]} 与 {domain} 声明"
            owners[name] = domain
    # 进程级那两件住组合类，不归任何域
    for name in PROCESS_STATE:
        assert name not in owners, f"{name} 是进程级状态，不该被域声明"


def test_a_domain_without_a_hook_really_has_no_state() -> None:
    """★ 没钩子的域必须**真的**零状态（不造 `pass` 空钩的对价：这条正面断言）。"""
    for domain in STATELESS:
        cls, _, _ = DOMAINS[domain]
        path = HUB_DIR / f"{domain}.py"
        assigned = _self_assignments(path, cls.__name__)
        assert assigned == set(), f"{domain} 冒出了常驻状态 {sorted(assigned)} —— 那就该有钩子"
        hooks = [n.name for n in _cls(_tree(path), cls.__name__).body if isinstance(n, ast.FunctionDef)]
        assert not [h for h in hooks if h.startswith("_init_")], f"{domain} 不该有钩子"


def test_the_init_calls_every_hook_exactly_once() -> None:
    """组合类的 `__init__` **逐个显式**调用钩子（不用 `super()` 链：顺序要读得出来）。"""
    src = STORE_MOD.read_text(encoding="utf-8")
    init = next(
        n
        for n in _cls(_tree(STORE_MOD), "_JobStore").body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    calls: list[str] = []
    for n in ast.walk(init):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and len(n.args) == 1
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id == "self"
        ):
            calls.append(f"{f.value.id}.{f.attr}")
    want = sorted(f"{DOMAINS[d][0].__name__}._init_{d.removeprefix('store_')}" for d in STATEFUL)
    assert sorted(c for c in calls if c.endswith(tuple(f"_init_{d.removeprefix('store_')}" for d in STATEFUL))) == want, calls
    for domain in STATEFUL:
        cls, _, _ = DOMAINS[domain]
        call = f"{cls.__name__}._init_{domain.removeprefix('store_')}(self)"
        assert src.count(call) == 1, f"{call} 在 hub/store.py 里出现 {src.count(call)} 次"
    # `_lock` 只有一个来源（`_AuthGuard.__init__`）——旧 `_JobStore.__init__` 自建的那把
    # 会被它覆盖掉（一个被丢弃的锁对象），第十四刀顺手删了；这条钉住不再长回来。
    assert "_lock" not in _init_state(STORE_MOD, "_JobStore", "__init__"), (
        "组合类又在自建锁——`_lock` 由 `_AuthGuard.__init__` 提供"
    )
    assert "_lock" in _init_state(AUTH_MOD, "_AuthGuard", "__init__"), "`_AuthGuard` 不再建锁"


def test_mixin_declarations_are_annotations_or_owned_constants() -> None:
    """混入类体里带注解的名字只有两种合法形态：

    * **裸注解**（`_leases: dict[str, float]`，无值）= 兄弟簇/组合类提供，本模块只声明；
    * **类常量**（`RESUME_PARTS: tuple[str, ...] = (…)`）= 本簇自己拥有——但那就必须**只有一份**
      （全六个混入里同名带值的只允许出现一次）。
    """
    valued: dict[str, str] = {}
    for domain, (cls, _, _) in DOMAINS.items():
        for n in _cls(_tree(HUB_DIR / f"{domain}.py"), cls.__name__).body:
            if not (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)):
                continue
            name = n.target.id
            if n.value is None:
                continue
            assert name not in valued, f"{domain}::{name} 与 {valued[name]} 各定义了一份（值不唯一）"
            valued[name] = domain
    # 唯一一个「带注解又有值」的常量（`tuple[str, ...]` 需要一个注解才写得清）；其余常量
    # 是无注解的普通 `Assign`，它们的唯一性由上一节的「实现名两两不交」一并钉住。
    assert sorted(valued) == ["RESUME_PARTS"], sorted(valued)
    assert valued["RESUME_PARTS"] == "store_offline"


def test_process_state_names_are_declared_only_by_the_composed_class() -> None:
    """`halt_workers` / `_workers` 的**赋值**只许出现在组合类里（域混入最多裸注解声明）。"""
    for domain, (cls, _, _) in DOMAINS.items():
        assigned = _self_assignments(HUB_DIR / f"{domain}.py", cls.__name__)
        for name in PROCESS_STATE:
            assert name not in assigned, f"{domain} 动了进程级状态 {name}"
    combo = _self_assignments(STORE_MOD, "_JobStore")
    for name in PROCESS_STATE:
        assert name in combo, f"组合类没声明进程级状态 {name}"


# ───────────────────── ⑤ 依赖方向 / 账本 ─────────────────────


def test_the_mixins_only_import_downward() -> None:
    """六个混入（+ `hub.auth` / `hub.store`）的仓内依赖是登记过的那些（多一个就说明搬漏/搬多了）。"""
    for mod, allowed in ALLOWED_IMPORTS.items():
        dag.assert_remote_module(mod, allowed_project_imports=allowed)


def test_the_composed_class_moved_below_the_mixins() -> None:
    """★ 第十五刀的使能缝：组合类的家必须在六个混入**上面**、在宿主**下面**。

    这是「下游要构造/注解 `_JobStore`，而 `remote/hub/*` 不得 import `hub_server`」那条约束的
    机械化形式——若哪天有人把 `_JobStore` 搬回 `hub_server`，层号算术当场对不上。
    """
    for domain in DOMAINS:
        assert dag.LAYERS[f"remote.hub.{domain}"] < dag.LAYERS["remote.hub.store"], domain
    assert dag.LAYERS["remote.hub.auth"] < dag.LAYERS["remote.hub.store"]
    assert dag.LAYERS["remote.hub.store"] < dag.LAYERS[hs.__name__]
    # 组合类的类体只剩三样：docstring · 两个成员 · 零个「域」方法
    body = _cls(_tree(STORE_MOD), "_JobStore").body
    kinds = [
        type(n).__name__ for n in body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]
    assert kinds == ["FunctionDef", "FunctionDef"], kinds


def test_the_mixins_never_import_each_other() -> None:
    """★ 跨域调用**一律经 `self`**：混入之间零 import（有边就不是「一个对象一把锁」了）。"""
    siblings = {f"remote.hub.{d}" for d in DOMAINS}
    for domain in DOMAINS:
        tree = _tree(HUB_DIR / f"{domain}.py")
        got = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                got.add(n.module)
            elif isinstance(n, ast.Import):
                got.update(a.name for a in n.names)
        assert siblings & got == set(), f"{domain}.py import 了兄弟混入 {siblings & got}"
        # 组装模块的名字从**对象**取（`hs.__name__`），不写带引号的字面量：
        # `tests/test_subproc_util.py` 按带引号的 argv 元素扫「起真服务进程」，写死会误伤
        # （第十/十一刀连踩两次，这是第三次）。从对象取还更强：改名也不会失效。
        assert hs.__name__ not in got, f"{domain}.py 反向 import 了组装模块"


def test_the_layers_match_the_ledger() -> None:
    """层号是算出来的：五个只靠协议层（L0），`store_offline` 另需 `artifacts` ⇒ L1，全在宿主下面。"""
    host_key = hs.__name__  # 不写带引号的组装模块名（`test_subproc_util` 的 spawn marker）
    for domain in DOMAINS:
        lv = dag.LAYERS[f"remote.hub.{domain}"]
        assert lv < dag.LAYERS[host_key], domain
    assert dag.LAYERS["remote.hub.store_offline"] == 1
    for domain in (*STATEFUL, "store_results"):
        assert dag.LAYERS[f"remote.hub.{domain}"] == 0, domain


def test_the_offline_writer_moved_with_its_only_caller() -> None:
    """`_write_bytes` 的唯一调用方是离线簇 ⇒ 它随簇搬走，`hub_server` 不再留一份。"""
    assert hasattr(offline_mod, "_write_bytes"), "store_offline 少了 _write_bytes"
    assert not hasattr(hs, "_write_bytes"), "hub_server 还留着一份 _write_bytes（搬漏）"
    def _calls(path: Path) -> int:
        return sum(
            1
            for n in ast.walk(_tree(path))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_write_bytes"
        )

    assert _calls(HUB_DIR / "store_offline.py") == 5, "离线簇的 _write_bytes 调用点变了"
    for other in DOMAINS:
        if other != "store_offline":
            assert _calls(HUB_DIR / f"{other}.py") == 0, f"{other} 里冒出了 _write_bytes 调用"
    assert "def _write_bytes" not in HUB_SERVER.read_text(encoding="utf-8"), (
        "hub_server 又定义了一份 _write_bytes"
    )


# ───────────────────── ⑥ ★ 功能性 ─────────────────────


def test_cross_domain_call_chain_works_on_one_object(tmp_path: Path) -> None:
    """★ 跨域链路真的连通（经 `self`）：发布 → 认领 → 计量，落点全在**同一个**对象上。

    这条是「混入 vs 协作对象」的正面证明：`publish`（账本簇）→ `claim_outcome`（租约簇，内部又经
    `self._job_priority_locked` 读调度簇的 `_claimed` / `self._job_dir` 读账本簇）→
    `record_payload_sent`（计量簇），三簇的状态都写在同一个实例上。
    """
    store = hs._JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")
    jid = "j" * 16
    store.publish(jid, {"payload_sha256": "0" * 64}, b"payload-bytes")
    rows = [e for e in store._read_ledger() if e.get("job_id") == jid]
    assert rows and rows[0]["event"] == "job_pending", f"publish 没写 job_pending：{rows}"
    assert (store.job_root / jid).is_dir(), "publish 没落 job 目录"
    assert store._ledger_cache[0] > 0, "账本缓存没记住文件 size（H6 增量读）"

    out = store.claim_outcome(jid, mode="exclusive", worker_id="w1")
    assert out.ok, out
    # 租约簇自己那份
    assert store._leases.get(jid), "claim 没设租约"
    assert store._lease_owners.get(jid) == out.token, "claim 没记 owner"
    assert store._last_heartbeat.get(jid), "claim 没记心跳"
    # 调度簇那份（跨域：`_claim_locked` 里经 `self` 推 epoch / 写 `_claimed`）
    assert (store._claimed.get(jid) or {}).get("worker") == "w1", "claim 没写调度簇的 _claimed"
    assert store._epoch >= 1, "claim 没推 epoch"

    store.record_payload_sent(jid, 10)
    assert store.wire_stats(jid) == {"sent_bytes": 10}
    store.record_result_recv(jid, 4)
    assert store._wire[jid]["recv_bytes"] == 4


def test_one_lock_governs_even_the_most_independent_domain(tmp_path: Path) -> None:
    """★ 同一把锁：持有 `store._lock` 时，计量簇（零互调的那块）也必须被挡住。

    这是「一把锁是不变式」那条判据的功能性对账——若哪天有人给某个簇配了自己的锁，这条会挂：
    它证明 `_lock` 是**同一个对象**，而不只是「同名属性」。
    """
    store = hs._JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")
    done = threading.Event()

    def _reader() -> None:
        store.wire_stats("j" * 16)
        done.set()

    store._lock.acquire()
    try:
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        assert not done.wait(0.3), "wire_stats 没被 store._lock 挡住 ⇒ 它不是同一把锁"
    finally:
        store._lock.release()
    assert done.wait(5.0), "释放锁之后读者仍没动静"
