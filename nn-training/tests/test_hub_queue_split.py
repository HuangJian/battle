"""拆分的**契约守卫**：`_HubQueue` 的七个域混入永住 `remote/hub/queue_*.py`（S4 第十五刀，2026-09-24）。

## 这一刀切了什么

`remote/hub_server.py` 2072 → 889 行：`_HubQueue`（1033 行 / 76 个成员）按**域**拆成七个混入，
组合类住 `hub/queue.py`，只剩 docstring + 两个类常量 + `__init__`：

```
class _HubQueue(QueueScopeMixin, QueueDiscoverMixin, QueueAuthMixin, QueueClaimsMixin,
                QueueResumeMixin, QueueObserveMixin, QueueStoreFaceMixin, _AuthGuard)
  queue_scope       课程表 · 归属路由 · 模式 · 停机达令 · worker 登记（状态的主人）
  queue_discover    自动发现三相：扫盘 / 开课标记闸 / 「在训」判据
  queue_auth        鉴权面四覆写 + halt_workers 旧名的委派（本簇**继承** `_AuthGuard`）
  queue_claims      派发与认领：轮转挑选 · peek · 熔断告警 · 合法放弃
  queue_resume      续跑锚点 · 离线段补传产物 · 课程路径
  queue_observe     观测面（只读）· job 路径解析 · 哨兵根 `_MISSING_ROOT`
  queue_store_face  job 作用域门面：与 `_JobStore` 同名同签名的那批转发
```

同一刀还把**两个组合类搬出自己的家**（这是本刀能成立的**使能缝**，不是顺手清洁）：第十四刀只搬了
`_JobStore` 的六个混入，组合类还在 `hub_server` 里；而 `queue_scope.add_course` 要**构造** store、
`_store_of` 要**注解**它，`remote/hub/*` 又不得 import `hub_server`（成环）⇒
`_AuthGuard`/`_is_loopback` → `hub/auth.py`、`_JobStore` → `hub/store.py`。

## 为什么是「混入」而不是「协作对象」（与第十四刀同源）

1. **一把锁是类的不变式**：`_lock` 有 5 个直接读者（`_serves_course` / `active_worker_count` /
   `claim_job` / `claim_next` / `note_worker`），它就是 `_AuthGuard.__init__` 建的那把。
   协作对象各持一把锁 = **换语义**；
2. **跨域互调是常态**：`claim_next` → `discover` / `_serves_course` / `mode_of` /
   `active_worker_count` / `_announce_freeze`；`queue_state` → `halt_of` / `mode_of` /
   `active_courses` / `offline_courses` / `all_halted` / `active_worker_count`；
   `resume_anchor` → `resume_sources`…混入把它们留在 `self.X` 上 ⇒ **零 seam**；
3. **tests 直读私有状态**（`hub._stores` / `._order` / `._halts` / `._modes` / `._locate_cache` /
   `._workers`，多处断言）——协作对象会让这些**全部改路**；混入是同一个对象 ⇒ **一行测试都不用改**。

## 本文件钉住的东西

1. **定义唯一**：75 个域成员各住一家，`_HubQueue` 不得再定义任何一个（组合类只组合）；
2. **接线正确**：`_HubQueue.X is Mixin.X`（同一函数对象）+ MRO 逐项 + 类常量经 MRO 可达；
3. **★ 门面契约**（`queue_store_face` 那一簇的**存在理由**）：与 `_JobStore` 同名的方法
   **逐参数对账**——28 个完全一致 + 3 个只多一个前置 `course`（课程寻址），且这份名单是**闭集**；
4. **状态归属唯一**：16 个字段的**写者集合**逐字段对账（含下标/原地变更三种写法）；
5. **带值声明只有一处**（组合类 `__init__`）+ `QueuePeer` 是**纯声明**（方法体全是 `...`）且
   与真实现逐参数一致；
6. **依赖方向**：七个混入彼此**零 import**、账本层号关系、`queue_resume` 的 `rl` 引用是**延迟**的；
7. **★ 功能性**：真的建一个 hub 跑一遍跨域链路（登记课程 → 发现 → peek/claim → 观测 → 门面），
   并证明**连最外层的门面也在同一把锁下**。
"""

from __future__ import annotations

import ast
import inspect
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub.queue_auth as auth_mix
import remote.hub.queue_claims as claims_mix
import remote.hub.queue_discover as discover_mix
import remote.hub.queue_observe as observe_mix
import remote.hub.queue_peer as peer_mod
import remote.hub.queue_resume as resume_mix
import remote.hub.queue_scope as scope_mix
import remote.hub.queue_store_face as face_mix
import remote.hub.store as store_mod
from remote import hub_server as hs
from remote.hub.queue import _HubQueue
from remote.hub.store import _JobStore
from tests.helpers import remote_dag as dag

NN_ROOT = ROOT
HUB_DIR = NN_ROOT / "remote" / "hub"
HUB_SERVER = NN_ROOT / "remote" / "hub_server.py"
QUEUE_MOD = HUB_DIR / "queue.py"

#: 域 -> （混入类, 该域实现的成员）。七个域的并集**恰好**是拆分前 `_HubQueue` 的 75 个域成员
#: （第 76 个是组合类的 `__init__`）。
DOMAINS: dict[str, tuple[type, tuple[str, ...]]] = {
    "queue_scope": (
        scope_mix.QueueScopeMixin,
        (
            "halt_of",
            "all_halted",
            "set_halt",
            "add_course",
            "_adopt_solo",
            "_registry",
            "note_worker",
            "active_worker_count",
            "courses",
            "course_of",
            "_store_of",
            "mode_of",
            "offline_courses",
            "set_mode",
            "active_courses",
        ),
    ),
    "queue_discover": (
        discover_mix.QueueDiscoverMixin,
        ("discover", "_serves_course", "_course_dir_live"),
    ),
    "queue_auth": (
        auth_mix.QueueAuthMixin,
        ("halt_workers", "auth_failure", "auth_success", "is_blocked", "blocked_remaining"),
    ),
    "queue_claims": (
        claims_mix.QueueClaimsMixin,
        (
            "claim_next",
            "_announce_freeze",
            "claimable_job_ids",
            "claim",
            "peek_jobs",
            "_manifest_summary",
            "claim_job",
            "priority_view",
            "abandon",
        ),
    ),
    "queue_resume": (
        resume_mix.QueueResumeMixin,
        (
            "traj_root",
            "course_dir",
            "resume_sources",
            "merge_eval_rows",
            "resume_anchor",
            "task_pack_path",
            "offline_progress",
            "locate_offline_course",
            "store_offline_artifact",
            "store_offline_result",
        ),
    ),
    "queue_observe": (
        observe_mix.QueueObserveMixin,
        (
            "queue_state",
            "_job_dir",
            "job_root_of",
            "shared_code_zip",
            "epoch_of",
            "job_status",
            "job_priority_of",
            "reclaims",
            "frozen_info",
            "consume_freeze_announcement",
            "unfreeze",
            "frozen_jobs",
            "lease_expires_in",
            "last_heartbeat_ago",
        ),
    ),
    "queue_store_face": (
        face_mix.QueueStoreFaceMixin,
        (
            "start_job",
            "set_ready",
            "heartbeat",
            "release",
            "result_token_ok",
            "store_result",
            "store_job_failure",
            "job_failure",
            "get_result",
            "mark_completed",
            "record_payload_sent",
            "record_result_recv",
            "record_push_wire",
            "wire_stats",
            "store_bc_epoch",
            "get_bc_resume",
            "get_bc_metrics",
            "append_ledger",
        ),
    ),
}
DOMAIN_METHODS = tuple(m for _, (_, ms) in DOMAINS.items() for m in ms)
MIXIN_CLASSES = tuple(cls for cls, _ in DOMAINS.values())

#: 组合类自己留的（进程级 + 构造）。
OWN_METHODS = ("__init__",)
#: 组合类里那个「形参缺省值要在类体作用域解析」的别名（`__init__` 因此逐字节不动）。
OWN_CONSTS = ("BC_EPOCH_BODY_MAX", "DISCOVER_FRESH_SEC")

#: ★ 门面契约：与 `_JobStore` 同名的方法**逐参数完全一致**的那批（`inspect.signature` 对账）。
#: 前四个是鉴权面（`_JobStore` 经 `_AuthGuard` 继承，`_HubQueue` 覆写后转发）——所以它们也在
#: 这份名单里：门面不只是 `queue_store_face` 那一簇，而是**整个 `_HubQueue` 的对外承诺**。
FACADE_SAME_SIG = (
    "_job_dir",
    "auth_failure",
    "auth_success",
    "blocked_remaining",
    "claim",
    "consume_freeze_announcement",
    "frozen_info",
    "get_bc_metrics",
    "get_bc_resume",
    "get_result",
    "heartbeat",
    "is_blocked",
    "job_failure",
    "mark_completed",
    "note_worker",
    "reclaims",
    "record_payload_sent",
    "record_push_wire",
    "record_result_recv",
    "release",
    "result_token_ok",
    "set_ready",
    "start_job",
    "store_bc_epoch",
    "store_job_failure",
    "store_result",
    "unfreeze",
    "wire_stats",
)

#: ★ 门面契约的**例外三条**：同名，但多一个**前置 `course`**。理由：store 是**每课程一份**
#: （`store_offline_*` 与 `claimable_job_ids` 都作用在某一门课上），而队列要**跨课程寻址**，
#: 所以队列侧先收课程名再转发。名字照旧、语义只多一层寻址 —— 这三条是本刀明确**不**统一的。
FACADE_COURSE_EXTRA = ("claimable_job_ids", "store_offline_artifact", "store_offline_result")

#: ★ 门面的**改名**转发（同名承诺在 store 侧名字不同）。`abandon` 是唯一一条。
FACADE_RENAMED = {"abandon": "abandon_job"}

#: ★ 同名面里**不是转发门面**的那一条：`note_worker`。队列自己**就是**登记表的拥有者
#: （`_registry()` 在单课程时取出 store 的 `_workers`、多课程时用自己的）—— 它不把活交给
#: store，而是直接写生效的那张表。签名与 `_JobStore.note_worker` 相同是因为两边的目标
#: 是**同一份进程级状态**（多课程单 hub 下共享）。这条例外单独写明，不让名单暗示它也是转发。
FACADE_OWN = ("note_worker",)

#: 两个鉴权字段的声明点不在组合类体里：它们在 `hub/auth.py::_AuthGuard.__init__`（`_HubQueue.__init__`
#: 末尾调它 —— 这是**同一个对象**的两段 `__init__`，不是两个对象）。
AUTH_GUARD_FIELDS = frozenset({"_auth_blocked_until", "_auth_fail"})

#: 状态归属表：字段 -> 写它的方法集合（**三种写法都算**：`self.X = …` / `self.X[k] = …` /
#: `self.X.append(...)`）。这条是「状态声明分散到七个模块之后，谁动它」的唯一对账面。
STATE_WRITERS: dict[str, frozenset[str]] = {
    "_auth_blocked_until": frozenset({"_adopt_solo"}),
    "_auth_fail": frozenset({"_adopt_solo"}),
    "_cursor": frozenset({"__init__", "claim_job", "claim_next"}),
    "_discover_fresh": frozenset({"__init__"}),
    "_discover_last": frozenset({"__init__", "discover"}),
    "_discover_root": frozenset({"__init__"}),
    "_halt_default": frozenset({"__init__", "_adopt_solo", "set_halt"}),
    "_halts": frozenset({"__init__", "set_halt"}),
    "_locate_cache": frozenset({"__init__", "course_of"}),
    "_modes": frozenset({"__init__", "add_course", "set_mode"}),
    "_no_marker_warned": frozenset({"__init__", "_serves_course"}),
    "_now": frozenset({"__init__"}),
    "_order": frozenset({"__init__", "add_course"}),
    "_solo": frozenset({"__init__", "_adopt_solo"}),
    "_stores": frozenset({"__init__", "add_course"}),
    "_workers": frozenset({"__init__", "_adopt_solo"}),
}

#: 七个混入 + 声明面允许的仓内依赖（多一个就说明又搬漏/搬多了）。
#: `queue_resume` 不在表里：它有一处**延迟** `rl` 引用（见 `test_only_resume_touches_rl`）。
ALLOWED_IMPORTS = {
    "remote.hub.queue_scope": {"common.protocol", "remote.hub.queue_peer", "remote.hub.store"},
    "remote.hub.queue_discover": {"common.protocol", "remote.hub.queue_peer", "remote.hub.store"},
    "remote.hub.queue_auth": {
        "remote.hub.auth",
        "remote.hub.queue_peer",
        "remote.hub.store",
    },
    "remote.hub.queue_claims": {
        "common.protocol",
        "remote.hub.queue_peer",
        "remote.hub.store",
        "remote.hub.store_leases",
    },
    "remote.hub.queue_observe": {
        "common.protocol",
        "remote.hub.queue_peer",
        "remote.hub.store",
    },
    "remote.hub.queue_store_face": {"remote.hub.queue_peer", "remote.hub.store"},
    "remote.hub.queue_peer": {"common.protocol", "remote.hub.store_leases"},
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _cls(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name)


def _own_defs(path: Path, cls_name: str) -> set[str]:
    """类体内**自己定义**的方法名（不看裸注解：注解不产生 `__dict__` 条目）。"""
    return {n.name for n in _cls(_tree(path), cls_name).body if isinstance(n, ast.FunctionDef)}


def _own_consts(path: Path, cls_name: str, *, valued: bool = True) -> set[str]:
    """类体里的类级赋值名（`valued=False` 时连裸注解一起收）。"""
    out: set[str] = set()
    for n in _cls(_tree(path), cls_name).body:
        if isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and (n.value is not None or not valued):
            assert isinstance(n.target, ast.Name)
            out.add(n.target.id)
    return out


_MUTATORS = frozenset({"append", "extend", "clear", "pop", "update", "add", "discard", "remove", "setdefault"})


def _self_attr(node: ast.AST) -> str | None:
    """`self.X` → "X"（读取 / 赋值 / 下标 / 方法宿主四种形态都认）。"""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _writers(path: Path, cls_name: str) -> dict[str, set[str]]:
    """类里每个方法**写**了哪些 `self.X` —— 三种写法都算（这是本文件第 ④ 节的核心判据）。"""
    out: dict[str, set[str]] = {}
    for m in _cls(_tree(path), cls_name).body:
        if not isinstance(m, ast.FunctionDef):
            continue
        for sub in ast.walk(m):
            attr: str | None = None
            if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store):
                attr = _self_attr(sub)
            elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                attr = _self_attr(sub.value)
            elif (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in _MUTATORS
            ):
                attr = _self_attr(sub.func.value)
            if attr:
                out.setdefault(attr, set()).add(m.name)
    return out


# ───────────────────── ① 定义唯一 ─────────────────────


def test_every_domain_method_lives_in_exactly_one_mixin() -> None:
    """75 个域成员各住一家；`_HubQueue` 不得再定义任何一个（组合类只组合）。"""
    # 74 个**不重名**的域成员（`halt_workers` 是属性对，一个名字两个 FunctionDef）。
    assert len(DOMAIN_METHODS) == len(set(DOMAIN_METHODS)) == 74, len(DOMAIN_METHODS)
    seen: dict[str, str] = {}
    for domain, (cls, methods) in DOMAINS.items():
        defined = _own_defs(HUB_DIR / f"{domain}.py", cls.__name__)
        # `halt_workers` 是属性对（getter + setter）⇒ 定义名比方法名多一个。
        missing = sorted(set(methods) - defined)
        assert missing == [], f"{domain}.py 少了 {missing}"
        for m in set(methods):
            assert m not in seen, f"{m} 同时住 {seen[m]} 与 {domain}（实现不唯一）"
            seen[m] = domain
    assert len(seen) == 74, len(seen)

    own = _own_defs(QUEUE_MOD, "_HubQueue")
    assert sorted(own) == list(OWN_METHODS), (
        f"组合类又定义了自己的方法：{sorted(set(own) - set(OWN_METHODS))}"
    )
    # 反向：整个类不得再在 hub_server 里出现（定义唯一是双向的），一个成员也不行。
    assert not any(
        isinstance(n, ast.ClassDef) and n.name == "_HubQueue" for n in _tree(HUB_SERVER).body
    ), "hub_server 又定义了一份 _HubQueue"
    hs_src = HUB_SERVER.read_text(encoding="utf-8")
    for m in DOMAIN_METHODS:
        assert f"def {m}(" not in hs_src, f"hub_server 里还有 _HubQueue::{m} 的实现（搬漏）"


def test_the_eight_mixins_do_not_share_any_realized_name() -> None:
    """七个混入的**实现名**（方法 + 类常量）两两不交（同名才会让 MRO 顺序变成语义）。"""
    seen: dict[str, str] = {}
    for domain, (cls, _) in DOMAINS.items():
        path = HUB_DIR / f"{domain}.py"
        realized = _own_defs(path, cls.__name__) | _own_consts(path, cls.__name__)
        for name in realized:
            assert seen.get(name, domain) == domain, f"{name} 同时住 {seen[name]} 与 {domain}"
            seen[name] = domain
    # 74 个域成员名 + 两个发现类常量（`halt_workers` 的 setter 与 getter 同名，不另算一项）
    expect = set(DOMAIN_METHODS) | {"DISCOVER_FRESH_SEC", "DISCOVER_SCAN_MIN_SEC"}
    assert len(seen) == 76 and set(seen) == expect, (len(seen), sorted(set(seen) ^ expect))


# ───────────────────── ② 接线正确 ─────────────────────


def test_the_composed_class_is_wired_to_the_mixins_by_object_identity() -> None:
    """★ 对象级证明：`_HubQueue.X is Mixin.X`（不是同名副本，也不是漏接了一个）。"""
    for domain, (cls, methods) in DOMAINS.items():
        for m in methods:
            assert getattr(_HubQueue, m) is getattr(cls, m), f"{domain}::{m} 没接上 _HubQueue"


def test_the_mro_is_exactly_the_declared_order() -> None:
    """MRO 逐项对账。**顺序是语义**：同名时靠前者赢，所以按「越底层越靠后」排。

    `QueuePeer` / `Protocol` / `Generic` 出现在 `_AuthGuard` 之后是必然的：七个混入都继承
    那个 Protocol（共同声明面）。
    """
    assert [c.__name__ for c in _HubQueue.__mro__] == [
        "_HubQueue",
        *[cls.__name__ for cls, _ in DOMAINS.values()],
        "_AuthGuard",
        "QueuePeer",
        "Protocol",
        "Generic",
        "object",
    ]
    assert _HubQueue.__bases__ == (*MIXIN_CLASSES, hs._AuthGuard)


def test_queue_auth_inherits_the_guard_so_its_unbound_calls_type_check() -> None:
    """`queue_auth` 是本刀唯一一个**继承** `_AuthGuard` 的混入。

    理由是可执行的：它的四个覆写要调 `_AuthGuard.auth_failure(self, …)` 这种**未绑定**写法
    （显式点名要哪一份实现，不走 MRO），而 `self` 必须真是个 `_AuthGuard` 才过得了 mypy。
    """
    assert auth_mix.QueueAuthMixin.__bases__ == (hs._AuthGuard, peer_mod.QueuePeer)
    for m in ("auth_failure", "auth_success", "is_blocked", "blocked_remaining"):
        src = inspect.getsource(getattr(auth_mix.QueueAuthMixin, m))
        assert f"_AuthGuard.{m}(self" in src, f"{m} 不再点名 `_AuthGuard` 的那一份实现"
    # `_HubQueue.__init__` 仍显式调 `_AuthGuard.__init__`（`_lock` 与 `_now` 的唯一来源）。
    assert "_AuthGuard.__init__(self, now_fn)" in QUEUE_MOD.read_text(encoding="utf-8")


def test_class_constants_are_reachable_through_the_mro() -> None:
    """类常量随簇搬走，但**名字是契约**（`hub.DISCOVER_FRESH_SEC` / `hub.BC_EPOCH_BODY_MAX`）。"""
    assert _HubQueue.DISCOVER_FRESH_SEC == discover_mix.QueueDiscoverMixin.DISCOVER_FRESH_SEC == 3600.0
    assert _HubQueue.DISCOVER_SCAN_MIN_SEC == discover_mix.QueueDiscoverMixin.DISCOVER_SCAN_MIN_SEC == 2.0
    assert _HubQueue.BC_EPOCH_BODY_MAX == _JobStore.BC_EPOCH_BODY_MAX == 4 * 1024 * 1024
    assert set(OWN_CONSTS) <= _own_consts(QUEUE_MOD, "_HubQueue")


# ────────────── ③ ★ 门面契约（`queue_store_face` 那一簇的存在理由） ──────────────
#
# `_HubQueue` 的类 docstring 写着「对外的 job 作用域方法与 `_JobStore` **同名同签名**」。
# 下面三条把这句话变成**可执行断言**——而不是一句随人漂的承诺。
#
# 侦察时逐条量了 31 个同名方法，分三档（名单写死在文件头，**闭集**）：
#   * 28 个逐参数完全一致（`FACADE_SAME_SIG`）；
#   * 3 个只多一个前置 `course`（`FACADE_COURSE_EXTRA`：store 是每课程一份，队列要跨课程寻址）；
#   * `abandon` 在 store 侧叫 `abandon_job`（`FACADE_RENAMED`）。
# 换出去、或漏接一条，下面立刻红。


def _params(fn: object) -> list[tuple[str, str, str]]:
    """签名的可比形状：`(参数名, 参数种类, 默认值)` 三元组列表。"""
    sig = inspect.signature(fn)  # type: ignore[arg-type]
    return [
        (
            p.name,
            p.kind.name,
            "<req>" if p.default is inspect.Parameter.empty else repr(p.default),
        )
        for p in sig.parameters.values()
    ]


def _callable_names(cls: type) -> set[str]:
    return {n for n in dir(cls) if not n.startswith("__") and callable(getattr(cls, n))}


def test_the_same_name_surface_is_exactly_the_declared_closed_set() -> None:
    """★ 同名面是**闭集**：换出去一条（或新冒出来一条）立刻红。"""
    common = _callable_names(_JobStore) & _callable_names(_HubQueue)
    declared = set(FACADE_SAME_SIG) | set(FACADE_COURSE_EXTRA)
    assert common == declared, (
        f"门面名单漂了：新增 {sorted(common - declared)}，消失 {sorted(declared - common)}"
    )
    assert len(declared) == 31, len(declared)
    # 改名那一条**不是**同名（store 侧没有 `abandon`）⇒ 它不属于这个闭集，另处单独钉。
    assert "abandon" in FACADE_RENAMED and "abandon" not in common
    assert "abandon_job" in _callable_names(_JobStore)


def test_the_identical_signature_facade_is_byte_for_byte_same_signature() -> None:
    """★ 28 条「名字 + 参数名 + 参数种类 + 默认值」逐项相等。

    这条在本刀里是真能抓住东西的：门面有 6 个形参全用关键字传的转发（如 `claim`）、
    有 3 个纯 `if st: st.X(...)`（无返回）、有 1 个带参注解返回值；漏一个或改一个名字就红。
    """
    assert len(FACADE_SAME_SIG) == 28
    for name in FACADE_SAME_SIG:
        want = _params(getattr(_JobStore, name))
        got = _params(getattr(_HubQueue, name))
        assert got == want, f"{name} 的签名不再是逐参数一致：\n  store={want}\n  hub  ={got}"


def test_the_course_addressed_three_differ_only_by_the_leading_course_param() -> None:
    """★ 三条例外：同名，且**只**多一个前置 `course`——不得多也不得少。"""
    assert len(FACADE_COURSE_EXTRA) == 3
    for name in FACADE_COURSE_EXTRA:
        want = _params(getattr(_JobStore, name))
        got = _params(getattr(_HubQueue, name))
        assert got[0][0] == "self" and got[1][0] == "course", f"{name} 的首个业务参数不是 course"
        assert got[2:] == want[1:], f"{name} 除 course 之外的参数不一致：\n  {want}\n  {got}"
        assert got[1][2] == "<req>", f"{name} 的 course 不该有默认值（跨课程寻址必须显式）"


def test_every_facade_method_actually_forwards_to_the_store() -> None:
    """★ 门面的**活性**：每条都真的把活交给一个 store（不是空实现/不再转发）。

    判据不看名字看**结构**：方法体里恰好一处「拿到 store」的取用，且转发目标名等于声明值
    （同名或 `FACADE_RENAMED` 里的改名）。取用有三种写法，**都是同一个东西**（一个 store 对象）：

    * `self._store_of(job_id)`   —— 按 job 认领（`queue_store_face`）；
    * `self._stores[course]`     —— 按课程子集寻址（`queue_resume`）；
    * `self._stores.get(course)` —— 同上，缺课程时得 `None`（`queue_claims`）；
    * `self._solo`               —— 单课程直通（`queue_auth` 的鉴权四则）。

    `FACADE_OWN` 那一条（`note_worker`）**不在**这份名单里 —— 它的判据在下一段单独写。
    """
    owner = {m: d for d, (_, ms) in DOMAINS.items() for m in ms}
    targets: dict[str, str] = dict.fromkeys(
        (*FACADE_SAME_SIG, *FACADE_COURSE_EXTRA), "<same-name>"
    )
    targets.update(FACADE_RENAMED)
    # 异常名单里的条目**仍受签名承诺约束**（它在 `FACADE_SAME_SIG` 里），只是不当转发。
    for own in FACADE_OWN:
        assert own in FACADE_SAME_SIG, f"{own} 不在同名闭集里 —— 它就没有签名承诺可谈"
        targets.pop(own)
    for name, want in targets.items():
        domain = owner[name]
        cls = DOMAINS[domain][0]
        path = HUB_DIR / f"{domain}.py"
        tree = _tree(path)
        fn = next(
            n
            for n in ast.walk(_cls(tree, cls.__name__))
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        called: list[str] = []
        taken = 0
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Subscript) and _self_attr(sub.value) == "_stores":
                taken += 1
                continue
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr == "_store_of":
                taken += 1
                continue
            # `self._stores.get(course)`：与下标同源（缺课程得 None），也是「拿到 store」。
            if (
                isinstance(f, ast.Attribute)
                and f.attr == "get"
                and _self_attr(f.value) == "_stores"
            ):
                taken += 1
                continue
            if not isinstance(f, ast.Attribute):
                continue
            # 只收「在某个 store 上调的方法」：`st.X(...)` / `self._solo.X(...)` /
            # `self._stores[course].X(...)` / `_AuthGuard.X(self, ...)`（鉴权簇走最后那条
            # —— 它是 `_AuthGuard` 的未绑定调用，效果同样落在 store 上）。
            recv = f.value
            if isinstance(recv, ast.Name):
                if recv.id in ("st", "store", "_AuthGuard"):
                    called.append(f.attr)
            elif _self_attr(recv) == "_solo":
                taken += 1
                called.append(f.attr)
            elif isinstance(recv, ast.Subscript) and _self_attr(recv.value) == "_stores":
                called.append(f.attr)
        assert taken == 1, f"{domain}::{name} 的 store 取用不是恰好一次（{taken}）"
        expect = name if want == "<same-name>" else want
        # 分支可以不止一条（鉴权四则是「单课程直通 / 回退到 `_AuthGuard`」两路，同一个名字），
        # 但**每一条**都必须叫声明的那个名字 —— 混一个别的名字进来就红。
        assert called and set(called) == {expect}, (
            f"{domain}::{name} 转发到了 {sorted(set(called))}，期望 [{expect}]"
        )


def test_the_one_non_forwarding_same_name_method_owns_its_state() -> None:
    """★ `FACADE_OWN` 的判据是**反面**：它不取 store，而是直接写生效的登记表。

    `note_worker` 的 `_registry()` 在单课程时取 store 的 `_workers`、多课程时取自己的
    —— 所以它必须**碰不到** `_store_of` / `_stores` / `_solo`，否则就与下一段的
    「状态归属唯一」对不上（那张表有两个主人）。
    """
    assert FACADE_OWN == ("note_worker",)
    fn = next(
        n
        for n in ast.walk(_cls(_tree(HUB_DIR / "queue_scope.py"), "QueueScopeMixin"))
        if isinstance(n, ast.FunctionDef) and n.name == "note_worker"
    )
    picks = [sub for sub in ast.walk(fn) if isinstance(sub, ast.Call)]
    assert [p.func.attr for p in picks if isinstance(p.func, ast.Attribute)] == [
        "strip",
        "_registry",
        "_now",
    ], [ast.unparse(p) for p in picks]
    assert not any(
        isinstance(sub, ast.Attribute) and _self_attr(sub) in ("_solo", "_stores")
        for sub in ast.walk(fn)
    )


def test_the_missing_store_default_is_the_declared_one_per_method(tmp_path: Path) -> None:
    """★ 缺归属时的返回值是**逐方法不同**的（这正是不能收成一个通用 `__getattr__` 的理由）。

    函数性地量：对**未知 job_id** 调一次，返回值必须等于声明值。这条同时钉住了
    `known` 反向那面：`False` / `None` / `{}` / `[]` / `0` 各自不能换成另一个假值。
    """
    hub = _HubQueue({"": _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")}, order=[""])
    unknown = "f" * 16
    expected: list[tuple[str, tuple[object, ...], object]] = [
        ("start_job", (unknown,), False),
        ("set_ready", (unknown,), False),
        ("heartbeat", (unknown, "tok"), False),
        ("release", (unknown, "tok"), False),
        ("result_token_ok", (unknown, "tok"), False),
        ("store_result", (unknown, {}), False),
        ("store_job_failure", (unknown, {}), False),
        ("store_bc_epoch", (unknown, {}), False),
        ("append_ledger", (unknown, {}), None),
        ("job_failure", (unknown,), None),
        ("get_result", (unknown,), None),
        ("frozen_info", (unknown,), None),
        ("consume_freeze_announcement", (unknown,), None),
        ("unfreeze", (unknown,), None),
        ("reclaims", (unknown,), 0),
        ("wire_stats", (unknown,), {}),
        ("get_bc_metrics", (unknown,), []),
    ]
    seen: list[str] = []
    for name, args, want in expected:
        got = getattr(hub, name)(*args)
        assert got == want and type(got) is type(want), (name, got, want)
        seen.append(name)
    assert len(seen) == 17
    # `_job_dir` 走**哨兵路径**（返回类型不同，单独断言）：路径存在与否就是「有没有这个 job」。
    assert not (hub._job_dir(unknown) / "manifest.json").exists()
    # `claim` 未知 job → None（与 `claimable_job_ids` 空表同一语义）
    assert hub.claim(unknown) is None
    assert hub.claimable_job_ids("") == []


# ────────────── ④ 状态归属（声明在七个模块里，但账只有一张） ──────────────


def test_the_state_writer_table_matches_reality() -> None:
    """★ 16 个字段的**写者集合**逐字段对账（含下标赋值与 `self.X.append(...)` 三种写法）。

    为什么需要它：状态声明分散到七个文件之后，「谁动它」是最容易漂的事。而**只数
    `self.X = …` 会瞎掉一半**——`_locate_cache` / `_halts` / `_modes` / `_order` /
    `_no_marker_warned` 全部是下标或方法式变更，只看赋值会得到「只有 `__init__` 写」的假表。
    """
    actual: dict[str, set[str]] = {}
    for domain, (cls, _) in DOMAINS.items():
        for attr, who in _writers(HUB_DIR / f"{domain}.py", cls.__name__).items():
            actual.setdefault(attr, set()).update(who)
    for attr, who in _writers(QUEUE_MOD, "_HubQueue").items():
        actual.setdefault(attr, set()).update(who)
    assert {k: frozenset(v) for k, v in actual.items()} == STATE_WRITERS, {
        k: (sorted(actual.get(k, set())), sorted(STATE_WRITERS.get(k, frozenset())))
        for k in set(actual) | set(STATE_WRITERS)
        if frozenset(actual.get(k, set())) != STATE_WRITERS.get(k, frozenset())
    }
    # `_cursor` 是唯一的**跨簇写**（`claim_next`/`claim_job` 都在 `queue_claims`）：
    # 它是「轮转起点」，只有真正拿到活的那一方才推进（R2-C2）——离开这张表就能看出来。
    assert STATE_WRITERS["_cursor"] == {"__init__", "claim_job", "claim_next"}


def test_only_the_composed_init_declares_state_with_values() -> None:
    """★ 带值声明只在组合类 `__init__`；七个混入的状态一律是**裸注解**。

    这**与第十四刀刻意不同**（那时状态拆到四个 `_init_*` 钩子）：这里 `__init__` 只有 44 行
    而且几处咬合（`_solo` 决定 `_now`；`_discover_root` 决定 `_discover_last` 初值；
    `_adopt_solo` 运行期还要把三个字段从 store 搬到 `self`），按域切只会把直线扯成跳转。
    """
    for domain, (cls, _) in DOMAINS.items():
        path = HUB_DIR / f"{domain}.py"
        valued = _own_consts(path, cls.__name__)
        # 类常量随簇走（发现簇两个）；除此之外不得带值。
        allowed = {"DISCOVER_FRESH_SEC", "DISCOVER_SCAN_MIN_SEC"} if domain == "queue_discover" else set()
        assert valued == allowed, f"{domain} 冒出了带值声明：{sorted(valued - allowed)}"
    # 组合类的类体只有那两个常量（`_JobStore.BC_EPOCH_BODY_MAX` 的同源转发 + 形参缺省值）。
    assert _own_consts(QUEUE_MOD, "_HubQueue") == set(OWN_CONSTS)
    # ★ 反面对账：`__init__` 里的状态声明**逐条带值**——裸注解（"这就是我的域"）只住七个混入。
    init = next(
        n
        for n in _cls(_tree(QUEUE_MOD), "_HubQueue").body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    bare: set[str] = set()
    for n in init.body:
        if not isinstance(n, ast.AnnAssign) or n.value is not None:
            continue
        tgt = n.target
        assert isinstance(tgt, ast.Attribute), ast.unparse(n)
        bare.add(tgt.attr)
    assert bare == set(), f"组合类 `__init__` 里出现无值声明：{sorted(bare)}"
    # 面正对账：`__init__` 里出现的实例字段**恰好**是状态表的键集（多一个就是漏登记的字段）。
    declared: set[str] = set()
    for n in init.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute):
            declared.add(n.target.attr)
        elif isinstance(n, ast.Assign):
            declared |= {t.attr for t in n.targets if isinstance(t, ast.Attribute)}
    # 两个鉴权字段的声明点在 `hub/auth.py::_AuthGuard.__init__`（组合类在末尾调它）——
    # 所以本类体里该少这两个，其余 14 个必须逐条在。
    want = set(STATE_WRITERS) - AUTH_GUARD_FIELDS
    assert declared == want, (
        f"`__init__` 申报的字段与状态表对不上：多 {sorted(declared - want)}，"
        f"少 {sorted(want - declared)}"
    )


# ────────────── ⑤ 共同声明面 `QueuePeer` ──────────────


def test_queue_peer_is_declarations_only() -> None:
    """★ `QueuePeer` 是**纯声明**：每个方法体只有 `...`，无一个实现（否则就是第二份实现）。"""
    body = _cls(_tree(HUB_DIR / "queue_peer.py"), "QueuePeer").body
    funcs = [n for n in body if isinstance(n, ast.FunctionDef)]
    assert len(funcs) == 74, len(funcs)  # 76 个成员 - `__init__` - `_store_of`（见下一条）
    for n in funcs:
        # 只滤掉文档字符串：`...` 也是 `Expr(Constant)`，滤它就把声明本身滤没了（本守卫
        # 第一版就是这么错的 —— `halt_of` 带 docstring 才暴露出来）。
        stmts = [
            s
            for s in n.body
            if not (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)
            )
        ]
        assert len(stmts) == 1 and isinstance(stmts[0], ast.Expr), f"QueuePeer.{n.name} 不是纯声明"
        assert isinstance(stmts[0].value, ast.Constant) and stmts[0].value.value is Ellipsis, n.name
    assert "_store_of" not in {n.name for n in funcs}, (
        "`_store_of` 不该进 QueuePeer：那要 import hub.store ⇒ queue_peer 升 L3 ⇒ "
        "混入 ≥L4 ⇒ hub.queue L5 ⇒ hub_server L6，与 smoke_loopback(L6) 同层"
    )
    # 它因此只能靠协议层 + `store_leases`（ClaimOutcome）⇒ L1
dag.assert_remote_module("remote.hub.queue_peer", allowed_project_imports=ALLOWED_IMPORTS["remote.hub.queue_peer"])


def test_queue_peer_signatures_match_the_real_implementations() -> None:
    """★ 声明面与真实现**逐参数一致**：漂了就是一个在骗 mypy 的柱子。"""
    for domain, (cls, methods) in DOMAINS.items():
        for name in methods:
            real = getattr(cls, name, None)
            declared = getattr(peer_mod.QueuePeer, name, None)
            if real is None or declared is None:
                continue
            # `halt_workers` 是 property（旧名的委派），不是方法 ⇒ 没有可比的 `signature`。
            if not callable(real) or not callable(declared):
                assert name == "halt_workers", f"{name} 不可调用，且不在已知例外里"
                continue
            assert _params(real) == _params(declared), (
                f"{domain}::{name} 与 QueuePeer 的声明不一致：\n  {_params(real)}\n  {_params(declared)}"
            )


def test_the_renamed_forward_is_declared_where_it_lives() -> None:
    """`abandon` 是本刀唯一的**改名**转发（store 侧叫 `abandon_job`）—— 它住 `queue_claims`，
    不是门面簇：它要在转发之外做「release 租约 + 清可见性 + **零** reclaim」的语义保证。"""
    src = (HUB_DIR / "queue_claims.py").read_text(encoding="utf-8")
    assert "st.abandon_job(job_id, worker_id)" in src
    assert "def abandon" not in (HUB_DIR / "queue_store_face.py").read_text(encoding="utf-8")


# ────────────── ⑥ 依赖方向 / 账本 ──────────────


def test_only_resume_touches_rl_and_only_lazily() -> None:
    """六个混入的仓内依赖是登记过的那些；`queue_resume` 例外但**只准延迟**。

    `merge_eval_rows` 要 `from rl.eval_local import append_eval_rows` —— 它本来就在
    `hub_server` 的函数体内（第十四刀没动它），搬簇时原样带过来。`assert_remote_module`
    的口径是「传输/落盘层保持 L2-pure」，所以这一簇单独按「延迟 + 只此一处」正面钉住。
    """
    for mod, allowed in ALLOWED_IMPORTS.items():
        dag.assert_remote_module(mod, allowed_project_imports=allowed)

    resume = HUB_DIR / "queue_resume.py"
    tree = _tree(resume)
    top_rl = {
        n.module
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("rl")
    }
    assert top_rl == set(), f"queue_resume 顶层碰了 rl：{top_rl}"
    lazy: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef,)):
            for sub in ast.walk(n):
                if isinstance(sub, ast.ImportFrom) and (sub.module or "").startswith("rl"):
                    lazy.append(f"{n.name}->{sub.module}")
    assert lazy == ["merge_eval_rows->rl.eval_local"], lazy
    for other in DOMAINS:
        if other == "queue_resume":
            continue
        src = (HUB_DIR / f"{other}.py").read_text(encoding="utf-8")
        assert "rl." not in src and "rl import" not in src, f"{other} 碰了 rl"


def test_the_mixins_never_import_each_other_nor_the_host() -> None:
    """★ 跨域调用**一律经 `self`**：混入之间零 import，也不得反向 import 组装模块。"""
    siblings = {f"remote.hub.{d}" for d in DOMAINS}
    host_key = hs.__name__  # 从**对象**取名字，不写带引号的字面量（`test_subproc_util` 的 spawn marker）
    for domain in DOMAINS:
        tree = _tree(HUB_DIR / f"{domain}.py")
        got: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                got.add(n.module)
            elif isinstance(n, ast.Import):
                got.update(a.name for a in n.names)
        assert siblings & got == set(), f"{domain}.py import 了兄弟混入 {siblings & got}"
        assert host_key not in got, f"{domain}.py 反向 import 了组装模块"


def test_the_layers_match_the_ledger() -> None:
    """层号是算出来的：七个混入 L3（站在 `queue_peer`(L1) + `hub.store`(L2) 上），
    组合类 L4，组装模块 L5 —— 而 `smoke_loopback` 是 L6，它 import 组装模块，必须仍严格向下。"""
    for domain in DOMAINS:
        assert dag.LAYERS[f"remote.hub.{domain}"] == 3, domain
    assert dag.LAYERS["remote.hub.queue_peer"] == 1
    assert dag.LAYERS["remote.hub.store"] == 2
    assert dag.LAYERS["remote.hub.queue"] == 4
    assert dag.LAYERS[hs.__name__] == 5
    assert dag.LAYERS["remote.smoke_loopback"] == 6


def test_the_sentinel_moved_with_its_only_reader() -> None:
    """`_MISSING_ROOT` 的唯一读者是 `_job_dir` ⇒ 它随簇搬进 `queue_observe`，宿主不再留一份。"""
    assert observe_mix._MISSING_ROOT.name == "hub-queue-missing"
    assert not hasattr(hs, "_MISSING_ROOT"), "hub_server 还留着一份 _MISSING_ROOT（搬漏）"
    assert "_MISSING_ROOT = " not in HUB_SERVER.read_text(encoding="utf-8")
    # `DISCOVER_SCAN_SEC`（后台兜底线程的节拍）只被 `main` 的 argparse 缺省值用 ⇒ **留在宿主**，
    # 与发现簇的 `DISCOVER_SCAN_MIN_SEC`（派发路径上的最小间隔闸）是两件事。
    assert hs.DISCOVER_SCAN_SEC == 5.0
    assert "DISCOVER_SCAN_SEC = " in HUB_SERVER.read_text(encoding="utf-8")


# ────────────── ⑦ ★ 功能性（跳出结构，真的跑） ──────────────


def _make_hub(tmp_path: Path, *, courses: dict[str, _JobStore] | None = None) -> _HubQueue:
    stores = courses if courses is not None else {"": _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")}
    return _HubQueue(stores, order=list(stores))


def test_cross_domain_chain_works_on_one_object(tmp_path: Path) -> None:
    """★ 跨域链路真的连通（经 `self`），落点全在**同一个**对象上。

    链路：`add_course`（课程表簇）→ `discover`（发现簇，因 `_discover_root` 已给）→
    `peek_jobs`（认领簇，要读 `_serves_course`/`mode_of`/`_manifest_summary`）→
    `queue_state`（观测簇，要读另四簇）→ `reload_lock` 那一层（门面簇）。
    """
    root = tmp_path / "traj"
    (root / "c1" / "remote-jobs").mkdir(parents=True)
    (root / "c1" / "training-enabled.txt").write_text("", encoding="utf-8")
    hub = _HubQueue({}, order=[], discover_root=root)
    assert hub.courses() == []
    added = hub.discover()
    assert added == ["c1"], added
    assert hub.courses() == ["c1"]
    assert "c1" in hub._stores and hub._modes["c1"] == "online"

    # 发布一份 job（走 store 本体 —— 队列不做发布）并让它可领
    st = hub._stores["c1"]
    jid = "a" * 16
    st.publish(jid, {"payload_sha256": "0" * 64}, b"PK\x03\x04fake")
    assert hub.claimable_job_ids("c1") == [jid]
    assert hub.course_of(jid) == "c1"  # 路由簇（一次 fs 探测 + 缓存）
    assert hub._locate_cache[jid] == "c1"

    peeked = hub.peek_jobs(worker_id="w1", n=1)
    assert [p["job_id"] for p in peeked] == [jid], peeked
    assert peeked[0]["course"] == "c1"
    assert hub._workers.get("w1")  # `peek_jobs` 顺带喂了避让链的登记表（R2-2）

    got = hub.claim_next(worker_id="w1")
    assert got is not None and got[0] == "c1" and got[1] == jid, got
    assert hub._cursor == "c1"  # 轮转游标只由真正拿到活的那一方推
    assert st._leases.get(jid), "认领没在 store 上设租约"
    # 门面簇（最后一层）在同一对象上看得见前四簇的成果
    assert hub.result_token_ok(jid, got[2]) is True
    assert hub.epoch_of(jid) >= 1

    qs = hub.queue_state()
    assert qs["courses"]["c1"]["pending"] == [] and qs["cursor"] == "c1"
    assert qs["active_courses"] == 1


def test_one_lock_governs_even_the_outermost_facade(tmp_path: Path) -> None:
    """★ 同一把锁：持有 `hub._lock` 时，**最外层的门面**（零互调的那一簇）也必须被挡住。

    这是「一把锁是不变式」那条判据的功能性对账：若哪天有人给某一簇配了自己的锁，这条会挂。
    `note_worker` 是直接临界区，`claimable_job_ids`（门面）则要先经 `_stores` 再进 store 的锁
    —— 两者都必须停在同一把 `hub._lock` 后面。
    """
    hub = _make_hub(tmp_path)
    hub.note_worker("w0")
    done = threading.Event()

    def _reader() -> None:
        hub.note_worker("w1")
        done.set()

    hub._lock.acquire()
    try:
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        assert not done.wait(0.3), "note_worker 没被 hub._lock 挡住 ⇒ 它不是同一把锁"
    finally:
        hub._lock.release()
    assert done.wait(5.0), "释放锁之后读者仍没动静"
    assert "w1" in hub._registry()


def test_adopt_solo_carries_process_state_across_the_domains(tmp_path: Path) -> None:
    """★ `_adopt_solo` 是**唯一**跨簇搬状态的写入点（停机 / worker 登记 / 鉴权计数三簇）。

    它存在的理由是一条真事故：单课程时这三份状态住在那一份 store 里（既有用例直接预热
    store 字段），一旦课程数变成 2，“不搬”就是「多发现一门课，把停机达令、worker 登记、
    鉴权闭锁全悄悄清了」。这条用例把它变成可执行的。
    """
    st = _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")
    st.halt_workers = True
    st._workers["w9"] = 123.0
    st._auth_fail["203.0.113.9"] = 4
    st._auth_blocked_until["203.0.113.9"] = 9e9
    hub = _HubQueue({"": st}, order=[""])
    assert hub.halt_of("") is True and hub._solo is st  # 单课程：一切借 store
    assert hub._registry() is st._workers

    # 第二门课出现 ⇒ 状态必须搬到自己身上，而**值不变**
    (tmp_path / "traj" / "c2" / "remote-jobs").mkdir(parents=True)
    (tmp_path / "traj" / "c2" / "training-enabled.txt").write_text("", encoding="utf-8")
    hub._discover_root = tmp_path / "traj"
    assert hub.discover() == ["c2"]
    assert hub._solo is None
    assert hub.halt_of("") is True, "停机达令被多发现一门课静默清了"
    assert hub._registry().get("w9") == 123.0, "worker 登记被清了"
    assert hub._auth_fail.get("203.0.113.9") == 4, "鉴权计数被清了"
    assert hub.is_blocked("203.0.113.9") is True, "鉴权闭锁被清了"


def test_the_facade_reaches_a_real_store(tmp_path: Path) -> None:
    """★ 门面不是柱子：发布 → 认领 → 心跳 → 回传 → 读回，五步全经**门面**落在真 store 上。

    与第 ③ 节的结构对账互补：那条证「结构上是转发」，这条证「功能上真的到达」。
    """
    hub = _make_hub(tmp_path)
    st = hub._stores[""]
    jid = "b" * 16
    st.publish(jid, {"payload_sha256": "0" * 64}, b"PK\x03\x04fake")
    tok = hub.claim(jid, worker_id="w1")
    assert tok, "门面 claim 没拿到租约"
    assert hub.heartbeat(jid, tok) is True
    assert hub.start_job(jid, "w1") is True
    assert hub.set_ready(jid, "w1") is True
    assert hub.store_result(jid, {"ok": True}) is True
    assert hub.get_result(jid) == {"ok": True}
    hub.record_payload_sent(jid, 12)
    hub.record_result_recv(jid, 3)
    assert hub.wire_stats(jid) == {"sent_bytes": 12, "recv_bytes": 3}
    hub.mark_completed(jid)
    assert hub.append_ledger is not None  # 存在（唯一调用方是 `/jobs/{id}/fail`）
    # 而 store 的**私有名**（`_append_ledger` / `claim_outcome` / `abandon_job`）**不经门面**：
    # 那是 store 的内部面，队列层只暴露它自己的那套（守卫第 ③ 节的闭集断言把这条钉住）。
    assert not hasattr(_HubQueue, "claim_outcome")
    assert not hasattr(_HubQueue, "_append_ledger")
    assert not hasattr(_HubQueue, "publish")
