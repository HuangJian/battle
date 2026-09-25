"""remote_dag —— `remote/` **内部依赖的单一账本**（2026-09-23 S4 第八刀后补）。

## 为什么要有它

S4 把 `worker.py` 拆成 `wire` / `http` / `job_fs` / `bc_job` / `download` / `job_lifecycle` 之后，
「新模块不得反向 import `remote.worker`」这句话在**六个**拆分守卫里各写了一遍（其中三个还各带一份
`ALLOWED_IMPORTS` / `PROJECT_ROOTS` 白名单）。同一件事写六遍的坏处不是啰嗦，而是**漂移**：
`tests/test_layering.py` 记过一次同类事故——首版判据只给 `remote` 展开子模块、没给 `rl` 展开，
于是两条断言在反向探针下**静默不动**（测试全绿而守卫是瞎的）。

本模块把这件事收成**一张账本**：

```
LAYERS           : 模块 → 层号（数字越小越底层）
DEFERRED_CYCLES  : 唯一允许「环」的地方（仅限延迟 import，且必须写明理由）
```

**性质（由 `tests/test_remote_dag.py` 与六个拆分守卫一起钉住）**：

1. 账本**恰好**覆盖 `remote/` 下全部生产模块（增一个不给层号 ⇒ 红；删一个还留着 ⇒ 红）；
2. 每一条**顶层** import 边都严格向下（`LAYERS[src] > LAYERS[dst]`）⇒ 顶层图是无环 DAG
   （顶层环 = 启动即 ImportError，这条没有豁免）；
3. 每一条**函数内（延迟）**import 边同样严格向下，**除非**它落在 `DEFERRED_CYCLES` 声明的环里；
4. 全图（顶层 ∪ 延迟）的环**恰好**等于 `DEFERRED_CYCLES`——多一个环红，少一个也红。

## 延迟 import 为什么也要算边

本仓的既有结论（`tests/test_layering.py` 头部）：「函数内 import 同样是一条依赖边，只是它把失败
推迟到调用时——本仓就有过『延迟 import 掩盖了循环』的先例」。所以延迟边不豁免**分层**，
只豁免**环**（且必须声明）——而 `DEFERRED_CYCLES` 今天是**空的**：全仓 `remote/` 内部**零环**
（原来那一个 `run_loop ⇄ worker` 已由「引擎下沉 `plan_run` + 调用方注入 `run_job_fn`」拆掉）。

## 判据自身的坑（抄 `test_layering` 的教训）

`from <pkg> import <mod>` 这种写法只给出裸包名，必须展开成 `<pkg>.<mod>` 才看得见那条边；
相对 import（`from . import x`）同理，必须按「本模块所在包」解析。`_collect()` 两种都处理，
并且把**解析不出来的 `remote.*` 目标**原样返回（`unresolved`）——守卫会断言它是空的，
这样「解析失败 = 静默看不见一条边」这条盲路被堵死。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOTE_DIR = ROOT / "remote"

#: `remote/` 内部的**分层账本**：数字越小越底层，每条边必须从大数指向小数。
#:
#: 这份数字不是拍脑袋排的，是**拓扑秩**（从叶子往上的最长路径长度）——所以它读起来就是架构：
#:
#: * **L0 原语/叶子**：锁、端口守卫、产物存储、bulk 调度器、bundle、net_http、prefetch、
#:   结果上传器、serve_pool、三个自包含引导模块、`hub.admin`、`colab_bc`、`hub.store_*` 里
#:   除 `store_offline` 外的五个状态混入（账本 / 计量 / 调度 / 租约 / 结果：只靠 `common.protocol`）；
#: * **L1 单层传输/落盘**：`wire`（传输账）· `job_fs`（作业工作区）· `hub_client` ·
#:   `offline_deliver` · `offline_eval` · `deliver_zip` · `iter_rollout` ·
#:   `hub.store_offline`（离线段产物：要靠 L0 的 `artifacts` + `common.fs`，比同族高一层）·
#:   `hub.task_pack`（任务包判据叶子：`offline` 与 `queue_offline` 的共同依赖）；
#: * **L2 传输核心**：`http`（所有业务簇的公共底座）· `push_client` · `plan_run`（半离线执行引擎：
#:   `worker` 与 `run_loop` 都站在它上面，它自己谁都不靠上层靠）· `hub.store`（`_JobStore`
#:   组合类：六个混入的组装，因 `store_offline` 在 L1 ⇒ 它只能是 L2）；
#: * **L3 业务簇**：`bc_job` · `download`（取字节 + **物料落地三兄弟** `_ensure_payload` /
#:   `_ensure_code` / `_ensure_ts_code`，因此也依赖 L1 的 `job_fs`）· `job_lifecycle` · `push_dispatch`；
#: * **L3 业务簇**：… · `hub.offline`（离线段面：`store` + `task_pack` ⇒ L3）；
#: * **L4 状态类/组合层**：`hub.queue`（`_HubQueue` 组合类：八个 L3 混入的组装）· `hub.result`
#:   （回传路由：要让推与拉共用同一个校验函数，因此要 `push_dispatch` L3）· `train_core`
#:   （训练核：模型/opt/kickstart/demo/PPO/产物 —— 它靠 L3 的业务簇组装出一个轮次，因此
#:   **必须在宿主下面**）· `job_round`（每 job 一轮：旁路线程组 + 注入的 `run_job_fn` +
#:   交回传 —— 与 `train_core` 同层同理由）；
#: * **L5 HTTP 面**：`hub.http_face`（`HubHandler` = 五组路由混入的组装 + 通用助手 + 来源判定；
#:   最深依赖 `hub.result`(L4) ⇒ 只能是 L5）；**与 `worker` 同层**（作业壳：网络/校验/上报）；
#: * **L6 宿主编排/引导链**：`run_loop` · `notebook_runtime` · `worker_server` ·
#:   `hub.boot`（`as_hub` / `make_server` / `main`：argparse + 锁 + 端口守卫 + 发现线程）；
#: * **L7 入口门面 / 独立引导**：`hub_server`（hub-server 进程入口 + re-export 门面，第十七刀起
#:   它自己不再有实现）· `offline_boot` · `push_bootstrap`；
#: * **L8 站在门面之上的探针**：`smoke_loopback` · `tunnel_ab_probe`（都 import `hub_server`
#:   起真服务）· `notebook_boot`（最外层，只经延迟 import 碰其它模块）。
LAYERS: dict[str, int] = {
    "remote._instance_lock": 0,
    "remote._port_guard": 0,
    "remote.artifacts": 0,
    "remote.bulk_sched": 0,
    "remote.worker_proc": 0,
    "remote.bundle": 0,
    "remote.colab_bc": 0,
    "remote.hub.admin": 0,
    # 鉴权原语（S4 第十五刀）：`_is_loopback` + `_AuthGuard`（D9 闭锁）从 `hub_server` 下沉到这里。
    # 只靠标准库 ⇒ L0；`hub.store`（组合类）与 `hub.queue_auth`（鉴权域混入）都站在它上面。
    "remote.hub.auth": 0,
    "remote.hub.blob": 0,
    "remote.hub.schedule": 0,
    # 状态类拆分（S4 第十四刀）：`_JobStore` 的六个域混入。五个只靠协议层；`store_offline`
    # 另需 `remote.artifacts`（L0）⇒ 高一档住 L1。它们彼此**零 import**（跨域调用经 `self`）。
    "remote.hub.store_ledger": 0,
    "remote.hub.store_scheduling": 0,
    "remote.hub.store_leases": 0,
    "remote.hub.store_results": 0,
    "remote.hub.store_wire": 0,
    "remote.net_http": 0,
    "remote.prefetch": 0,
    # 任务包新鲜度门 / 缺包自愈门 / 清单读数的**纯判据**（合并 origin 时新拆的叶子）：只靠
    # `common.protocol` + `net_http`(L0) ⇒ **L1**。放在这里（而不是挂在 `hub/http_face`）是因为
    # 它有两个低层读者：`hub.offline`（取包端点）与 `hub.queue_offline`（离线清单）。
    "remote.hub.task_pack": 1,

    "remote.result_upload": 0,
    "remote.serve_pool": 0,
    "remote.tailscale_boot": 0,
    "remote.deliver_zip": 1,
    "remote.hub_client": 1,
    "remote.hub.store_offline": 1,
    # `_JobStore` 组合类（S4 第十五刀）：第十四刀把六个域混入拆到 `hub/store_*.py`，本刀把组合类
    # 本身也从 `hub_server` 搬出来 —— 不是对称好看，而是 `_HubQueue` 的课程表域要**构造** store、
    # `_store_of` 要**注解**它，而 `remote/hub/*` 不得 import `hub_server`（成环）。
    # 它依赖六个混入（最深 `store_offline` L1）⇒ 拓扑秩 **L2**。
    "remote.hub.store": 2,
    # `_HubQueue` 的七个域混入（S4 第十五刀）：全部站在 `hub.store`（L2）上 —— 不是「都往
    # 高层次凑」，而是每一簇都要**注解** `_stores` / `_solo` / `_store_of` 的形状，而
    # `from __future__ import annotations` 只推迟求值，mypy 仍要模块级能解析那个名字；
    # 又不能用 `TYPE_CHECKING` 包（`remote_dag._collect` 把 `if` 体当**顶层**边，会造成上向边）。
    # 于是八个都是 **L3**。
    "remote.hub.queue_auth": 3,
    "remote.hub.queue_claims": 3,
    "remote.hub.queue_discover": 3,
    "remote.hub.queue_observe": 3,
    # 离线任务清单 + 租约域（合并 origin 时新拆的混入）：要 `hub.store`(L2) 与
    # `hub.task_pack`(L1) ⇒ 与其余七个同秩 **L3**。
    "remote.hub.queue_offline": 3,
    "remote.hub.queue_resume": 3,
    "remote.hub.queue_scope": 3,
    "remote.hub.queue_store_face": 3,
    # 离线段面：并入 origin 的新语义后它要 `hub.store`(L2，注解/构造 `_JobStore`) 与
    # `hub.task_pack`(L1，过期门判据) ⇒ 从 L0 升到 **L3**（仍在 `hub.http_face` L5 之下）。
    "remote.hub.offline": 3,
    "remote.iter_rollout": 1,
    "remote.job_fs": 1,
    "remote.offline_deliver": 1,
    "remote.offline_eval": 1,
    "remote.wire": 1,
    "remote.http": 2,
    "remote.plan_run": 2,
    "remote.push_client": 2,
    "remote.bc_job": 3,
    "remote.download": 3,
    "remote.job_lifecycle": 3,
    "remote.push_dispatch": 3,
    # `QueuePeer`（S4 第十五刀）：七个混入的**共同声明面**（只声明跨域方法的真签名，不带实现）。
    # 它**不能**声明 `_store_of`（那要 import `hub.store` ⇒ 本模块 L3 ⇒ 七个混入 ≥L4 ⇒
    # `hub.queue` L5 ⇒ `hub_server` L6，与 `smoke_loopback`(L6) 同层而后者 import 前者）——
    # 那条名字由调用它的三簇自己声明。所以本模块只靠 `common.protocol` +
    # `store_leases`（`ClaimOutcome`）⇒ **L1**。
    "remote.hub.queue_peer": 1,
    "remote.hub.queue": 4,
    "remote.hub.result": 4,
    "remote.job_round": 4,
    "remote.train_core": 4,
    # HTTP 面（S4 第十六刀）：`HubHandler` 本体 + 通用助手 + 来源判定从 `hub_server` 搬到这里。
    # 它组装五组路由混入（最深 `hub.result` L4）⇒ 秩算出来是 **L5**（不是「随便挑一层」，
    # 而是 1 + max(deps)）；`boot` 与 `hub_server` 因此分别在 L6 / L7。
    "remote.hub.http_face": 5,
    "remote.worker": 5,
    # 引导链（S4 第十六刀）：`as_hub` / `make_server` / `main`。它站在 L5 的 `http_face` 上 ⇒ **L6**。
    # 读者是 `hub_server`（L7）—— 方向是「入口 → 引导链 → HTTP 面 → 路由混入」，不反向。
    "remote.hub.boot": 6,
    "remote.notebook_runtime": 6,
    "remote.run_loop": 6,
    "remote.worker_server": 6,
    # hub-server 入口 + 门面（S4 第十六刀收口）：本模块自己**零实现**，只剩 re-export 与
    # `python -m remote.hub_server` 的分发。依赖最深到 `hub.boot`(L6) ⇒ **L7**。
    "remote.hub_server": 7,
    "remote.offline_boot": 7,
    "remote.push_bootstrap": 7,
    # 站在门面之上的探针：都 import `hub_server` 起真服务 ⇒ 随它 ****L6 → L8**（第十六刀的级联）。
    "remote.smoke_loopback": 8,
    "remote.tunnel_ab_probe": 8,
    "remote.notebook_boot": 8,
    # 一次性修复工具（origin 侧新增）：把**已经落地的回传轮**补做课程侧落位。它 import
    # `remote.hub_server`（拿 `_JobStore` 起真 store）——与探针同一个位置（L8，站在门面上）。
    "remote.backfill_offline": 8,
}

#: 允许的环（键 = 参与环的模块集合，值 = 为什么这是对的）。
#:
#: **今天为空**——这是「引擎下沉 + 调用方注入」的结果，不是碰巧：原来 `remote/` 内部唯一那个环
#: （`run_loop ⇄ worker`）已拆掉：执行引擎下沉到 `remote/plan_run.py`（L2，**不** import
#: `worker`），`worker` 与 `run_loop` 都从它上面拿 `verify_plan_file` / `run_plan_job`，而「一轮
#: 怎么跑」由调用方**注入**（`worker` 传 `run_job_fn=run_job`；CLI 侧传 `_real_run_job`）。
#:
#: 机制保留是有用的：真要再引入一个环，必须写在这里 + 在 DECISIONS 里论证，且守卫会把它归入
#: 「已声明」（而不是静静绿着）。但 `test_remote_dag.py` 里那条**全图零环**的断言比它更严：
#: 即使有声明也会红——因为本仓已经有「下沉 + 注入」这个手段，无需再用环换任何东西。
DEFERRED_CYCLES: dict[frozenset[str], str] = {}

#: 三个**自包含引导模块**（从 GitHub raw 单独拉取，cell 拿到 `code.zip` 之前就要 import）：
#: 它们**顶层不得** import 任何 `remote.*`（见 `remote/__init__.py`）。延迟 import 允许
#: （那正是「先拉起自己、再拉别人」的实现方式）。
STANDALONE_BOOT_MODULES = ("remote.tailscale_boot", "remote.notebook_boot", "remote.offline_boot")


def project_roots() -> set[str]:
    """nn-training 根下的**仓内顶层包/模块名**（`common` / `remote` / `rl` / `dist_common` …）。

    动态推导（而不是写死一份清单）：新加一个顶层包不会让白名单判据静默失效。
    """
    out = {p.stem for p in ROOT.glob("*.py")}
    out |= {p.name for p in ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return out


def remote_modules() -> dict[str, Path]:
    """`remote/` 下的**生产模块**（dotted name → 路径）。

    `__init__.py` 不入账：它是包门面/文档，不是依赖图的节点（`remote/hub/__init__.py` 同理）。
    """
    out: dict[str, Path] = {}
    for p in sorted(REMOTE_DIR.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        rel = p.relative_to(REMOTE_DIR).with_suffix("").as_posix()
        out["remote." + rel.replace("/", ".")] = p
    return out


def _package_of(module: str) -> str:
    """模块所在的包（`remote.hub.admin` → `remote.hub`）。"""
    return module.rsplit(".", 1)[0]


def _collect(path: Path, module: str, known: set[str]) -> tuple[set[str], set[str], set[str]]:
    """AST 扫全部 import 节点 → `(顶层边, 延迟边, 解析不出的 remote 目标)`。

    覆盖四种写法：`import remote.x` · `from remote.x import y` · `from remote import x` ·
    相对 `from . import x` / `from .x import y`。延迟边 = 出现在**函数/类体内**的 import。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top: set[str] = set()
    deferred: set[str] = set()
    unresolved: set[str] = set()

    def add(target: str, depth: int) -> None:
        if target == module:
            return
        (top if depth == 0 else deferred).add(target)

    def resolve(module_name: str | None, level: int, names: list[str], depth: int) -> None:
        """把一条 `ImportFrom` 展开成它可能指向的子模块（含 `from <pkg> import <mod>`）。"""
        if level:
            base = _package_of(module)
            for _ in range(level - 1):
                base = _package_of(base)
            prefix = f"{base}.{module_name}" if module_name else base
        else:
            prefix = module_name or ""
        if not prefix.startswith("remote"):
            return
        raw = [prefix] if prefix else []
        raw += [f"{prefix}.{n}" for n in names]
        for cand in raw:
            if cand in known:
                add(cand, depth)

    def walk(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, depth + 1)
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.startswith("remote"):
                        if alias.name in known:
                            add(alias.name, depth)
                        else:
                            unresolved.add(alias.name)
            elif isinstance(child, ast.ImportFrom) and (
                child.level or (child.module or "").startswith("remote")
            ):
                names = [a.name for a in child.names if a.name != "*"]
                resolve(child.module, child.level, names, depth)
                if child.level == 0 and child.module not in known and child.module != "remote":
                    unresolved.add(str(child.module))
            walk(child, depth)

    walk(tree, 0)
    return top, deferred, unresolved


def graph() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """整张图：`(顶层边, 延迟边, 解析不出的目标)`，三者都按模块分组。"""
    known = set(remote_modules())
    top: dict[str, set[str]] = {}
    deferred: dict[str, set[str]] = {}
    unresolved: dict[str, set[str]] = {}
    for module, path in remote_modules().items():
        t, d, u = _collect(path, module, known)
        top[module] = t
        deferred[module] = d
        if u:
            unresolved[module] = u
    return top, deferred, unresolved


def cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan SCC → 只返回**环**（自环或参与它的集合 > 1）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []

    def strong(v: str) -> None:
        work: list[tuple[str, Iterator[str]]] = [(v, iter(sorted(edges.get(v, ())))) ]
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        while work:
            node, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    break
                if w in on_stack:
                    low[node] = min(low[node], index[w])
            else:
                work.pop()
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or node in edges.get(node, ()):
                        found.append(sorted(comp))
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])

    for v in sorted(edges):
        if v not in index:
            strong(v)
    return found


def _in_declared_cycle(src: str, dst: str) -> bool:
    return any({src, dst} <= members for members in DEFERRED_CYCLES)


def layering_violations(
    top: dict[str, set[str]], deferred: dict[str, set[str]]
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """返回 `(顶层违规, 延迟违规)`：每条都是 `(src, dst, 人类可读的原因)`。"""
    top_bad: list[tuple[str, str, str]] = []
    deferred_bad: list[tuple[str, str, str]] = []
    for src, dsts in sorted(top.items()):
        for dst in sorted(dsts):
            if _in_declared_cycle(src, dst):
                top_bad.append(
                    (src, dst, "顶层边落在已声明的延迟环里——环只许出现在函数内延迟 import")
                )
            elif LAYERS[src] <= LAYERS[dst]:
                top_bad.append(
                    (src, dst, f"L{LAYERS[src]} → L{LAYERS[dst]} 不是严格向下（顶层必须单调向下）")
                )
    for src, dsts in sorted(deferred.items()):
        for dst in sorted(dsts):
            if _in_declared_cycle(src, dst):
                continue
            if LAYERS[src] <= LAYERS[dst]:
                deferred_bad.append(
                    (
                        src,
                        dst,
                        f"L{LAYERS[src]} → L{LAYERS[dst]} 不是严格向下；"
                        + (
                            f"若这是新的环，先想清楚再登记 DEFERRED_CYCLES（现已声明 {len(DEFERRED_CYCLES)} 个）"
                            if DEFERRED_CYCLES
                            else "本仓现在的形态是「下沉 + 参数注入」，`remote/` 内部零环"
                        ),
                    )
                )
    return top_bad, deferred_bad


def undeclared_cycles(
    top: dict[str, set[str]], deferred: dict[str, set[str]]
) -> tuple[list[list[str]], list[frozenset[str]]]:
    """返回 `(没声明的环, 声明了但已不存在的环)`——两个方向都要空。"""
    full = {m: set(top[m]) | set(deferred[m]) for m in top}
    declared = set(DEFERRED_CYCLES)
    actual = {frozenset(c) for c in cycles(full)}
    return sorted(c for c in cycles(full) if frozenset(c) not in declared), sorted(
        d for d in declared if d not in actual
    )


def module_deps(module: str, known: set[str] | None = None) -> set[str]:
    """单模块的**顶层** intra-remote 依赖（其余守卫拿它做单模块视角的对账）。"""
    known = known or set(remote_modules())
    top, _, _ = _collect(remote_modules()[module], module, known)
    return top


def module_project_imports(module: str, *, top_only: bool = True) -> set[str]:
    """单模块的仓内依赖（含 `common.*` / `rl.*` 等，不只 remote）。

    `top_only=True`（缺省）只看**模块级** import——白名单这类「这个模块声明依赖谁」的话
    只对顶层有意义；延迟 import 的方向由 `layering_violations` 统一对账。
    """
    tree = ast.parse(remote_modules()[module].read_text(encoding="utf-8"))
    nodes = tree.body if top_only else list(ast.walk(tree))
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    roots = project_roots()
    return {m for m in out if m.split(".")[0] in roots}


def assert_remote_module(
    module: str,
    *,
    allowed_project_imports: set[str] | None = None,
    allowed_rl: set[str] | None = None,
) -> None:
    """单模块视角的账本对账（**实现只有这一处**，六个拆分守卫都调它）。

    检查三件事：
      ① 该模块已在 `LAYERS` 里登记；
      ② 它的每条**顶层** intra-remote 边都严格向下——原先散在六个守卫里的
         「不得反向 import `remote.worker`」的**一般化**：不再特指 `worker`，而是
         「不许指向任何同层/上层模块」；延迟边的方向由全图对账（`layering_violations`）管；
      ③ **任何** import（含延迟）都不得碰 `rl`——传输/落盘/作业层保持 L2-pure，
         编排只许出现在 L4+ 的入口模块（`run_loop` / `smoke_loopback` / `hub_client` /
         `offline_eval` 这几个已登记在 `test_layering.py` 的口径里）。
         `allowed_rl` 是**点名豁免**（缺省空）：只有「这个模块必须落盘/归档一份课程侧产物，
         而那条路径的解析/备份实现住在 `rl/` 里」这种情形才该用，且只准列**纯逻辑**模块
         （stdlib-only、不达 remote ⇒ 不构成环；判据见 `test_layering.RL_ORCHESTRATION`）；
      ④ （传了白名单时）顶层仓内依赖 ⊆ 白名单。
    """
    known = set(remote_modules())
    assert module in known, f"{module} 不在 remote/ 下（账本对账前先确认模块名）"
    assert module in LAYERS, f"{module} 没在 tests/helpers/remote_dag.py 的 LAYERS 里登记层号"
    mine = LAYERS[module]
    upward = sorted(
        (d, LAYERS.get(d)) for d in module_deps(module, known) if LAYERS.get(d, -1) >= mine
    )
    assert upward == [], (
        f"{module}（L{mine}）反向 import 了同层/上层：{upward}——"
        "remote/ 内部的边必须严格向下（见 tests/test_remote_dag.py 的分层账本）"
    )
    rl = sorted(m for m in module_project_imports(module, top_only=False) if m.split(".")[0] == "rl")
    unlisted = sorted(m for m in rl if m not in (allowed_rl or set()))
    assert unlisted == [], (
        f"{module} 碰了未登记的 `rl`：{unlisted}——传输/落盘/作业层保持 L2-pure（延迟 import 也算一条边）"
    )
    if allowed_project_imports is None:
        return
    top = module_project_imports(module)
    extra = sorted(m for m in top if m not in allowed_project_imports)
    assert extra == [], f"{module} 顶层出现未登记的仓内依赖：{extra}"
