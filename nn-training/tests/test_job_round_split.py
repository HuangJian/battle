"""拆分的**契约守卫**：`worker_loop` 的「每 job 一轮」永住 `remote/job_round.py`（S4 第十刀，2026-09-24）。

## 这一刀切了什么

`worker_loop` 原来是一个把**两种东西**织在一起的 while：轮询壳（多 hub round-robin、停机感知、
空闲退出）与**一轮**（177 行：取活已定 → 起三个旁路线程 → `run_job` → 交回传 → finally 全收）。
本刀拿走后者：

```
worker_loop（留 worker.py）  轮询 / claim / halt / idle / --once / 回传收尾（uploader.close）
    └─ run_one_round() ──►  job_round.py（L4）  旁路线程组 + run_job_fn + 交回传 + RoundOutcome
配套下沉：
    _prefetch_fill ──────►  job_round.py     （它只被这一轮启动；依赖最深 L3 ⇒ 本来就该在这层）
    settle_result ───────►  job_round.py     （原 `_result_settled` 闭包，只服务这一轮的 async 落定）
    PREFETCH_WIRE_ID / PREFETCH_ROUND_SEC ──► job_round.py
```

`remote/worker.py` **1281 → 1042** 行；新模块 **418** 行。

## 为什么是 L4（与 `train_core` 同层）

账本（`tests/helpers/remote_dag.py`）把 `LAYERS` 定义为**拓扑秩**：本模块依赖 `job_lifecycle`(L3) /
`download`(L3) / `wire`(L1) / `prefetch`·`result_upload`·`worker_proc`(L0) ⇒ 秩只能 **4**，
`worker`(L5) 站在它上面。**这不是口味问题**——`test_remote_dag.py` 的秩断言会把标错的层当场报出来。

## 本文件钉住的东西

1. **定义唯一** + **宿主里不留假门面**：`_prefetch_fill` / `PREFETCH_ROUND_SEC` 若在 `worker.py`
   留一份转发，`setattr(W, "PREFETCH_ROUND_SEC", …)` 就是**静默空操作**（测试全绿而无注入）——
   这类「看着像 seam 其实无效」比直接 `AttributeError` 危险，所以钉成**不许有**；
2. **★ 接口双向一致**——宿主调用点的位置实参个数与关键字集合 == `run_one_round` 的形参集合
   （20 个入参的搬运，**漏传一个**是这一刀最可能的失误形态）；
3. **依赖方向**——`job_round` 不得 import `worker` 或任何上层（含延迟）；顶层零可变状态、零 `global`；
4. **★ 注入点分档**（两个方向各一条**功能性**断言）：
   * `run_job_fn=run_job` 是**唯一注入点**，宿主在调用点读 `run_job` 这个**值** ⇒
     `patch remote.worker.run_job` 的 10 处一行不改（「引用即接缝」）；
   * 搬走的名字（`job_ready` / `abandon_job` / `release_job` / `report_job_failure` /
     `start_cancel_watcher` / `peek_jobs` / `download_payload` / `PREFETCH_ROUND_SEC`）解析在本模块
     ⇒ patch `remote.worker.X` 自本刀起是**空操作**；
5. **宿主不得属性式访问** `job_round.` / `JR.`（那等于换命名空间，上一条的 patch 会静默失效）；
6. **★ 宿主的账从 `RoundOutcome` 读**——`done` / `_polls_since_accept` 是宿主的，本模块不碰；
   功能性验证「uploaded ⇒ done += 1」与「stop ⇒ 整条退出」两条分支；
7. **账本关系**：`job_round` < `worker`。
"""

from __future__ import annotations

import ast
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_round as round_mod
import remote.worker as worker_mod
from remote.prefetch import PrefetchStore
from tests.helpers import remote_dag as dag

ROUND_FILE = ROOT / "remote" / "job_round.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

ROUND_FN = "run_one_round"
ROUND_FNS = ("run_one_round", "_prefetch_fill", "settle_result")
ROUND_DATA = ("RoundOutcome", "PREFETCH_WIRE_ID", "PREFETCH_ROUND_SEC")

#: 随这一轮搬走、因此**解析在 `remote.job_round`** 的名字（patch `remote.worker` 起是空操作）。
MOVED_SEAM = (
    "abandon_job",
    "download_payload",
    "job_ready",
    "peek_jobs",
    "release_job",
    "report_job_failure",
    "start_cancel_watcher",
    "PREFETCH_ROUND_SEC",
)

#: 宿主**不得**转发的名字：留着转发名 = 留一个静默空操作的 patch 目标。
FORBIDDEN_FACADES = ("_prefetch_fill", "PREFETCH_ROUND_SEC", "PREFETCH_WIRE_ID", "settle_result")

#: 本模块允许的顶层仓内依赖（多一个就说明搬多了/搬漏了）。
ALLOWED_PROJECT_IMPORTS = {
    "common.protocol",
    "remote.bulk_sched",
    "remote.download",
    "remote.job_lifecycle",
    "remote.prefetch",
    "remote.result_upload",
    "remote.wire",
    "remote.worker_proc",
}

def _upstream_modules() -> tuple[str, ...]:
    """所有层号 ≥ 本模块的 `remote/` 模块（含同层）——本模块一条边都不许指向它们。

    **从账本推**而不是写死名单：① 名单会过期；② 写死名单里出现某个 **带双引号的模块路径字面量**
    时，会被 `tests/test_subproc_util.py` 的「起真服务进程必须借端口」源码守卫误判成 spawn marker
    （它就是按带引号的字面量扫的）——从账本推就自然把这个假阳性也解了。
    """
    mine = dag.LAYERS["remote.job_round"]
    return tuple(
        sorted(m for m, lv in dag.LAYERS.items() if lv >= mine and m != "remote.job_round")
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defs(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _func(path: Path, name: str) -> ast.FunctionDef:
    return next(n for n in _tree(path).body if isinstance(n, ast.FunctionDef) and n.name == name)


def _all_imports(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _calls(fn: ast.FunctionDef) -> set[str]:
    """函数体内的调用名（AST `Call`；注释里提这些名字不算）。"""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


# ───────────────────────── ① 定义唯一 + 不留假门面 ─────────────────────────


def test_the_round_lives_in_job_round() -> None:
    """三个实现名住 `job_round.py`，且**不在** `worker.py` 里再实现一遍。"""
    defs_round, defs_worker = _defs(ROUND_FILE), _defs(WORKER_FILE)
    for name in ROUND_FNS + ROUND_DATA:
        assert name in defs_round, f"{name} 不在 remote/job_round.py"
        assert name not in defs_worker, f"{name} 在 worker.py 里被重新实现了（搬完还留一份）"


def test_the_host_keeps_no_dead_facade_for_the_moved_internals() -> None:
    """★ 宿主**不许**给搬走的内部名留转发（留了就是个静默空操作的 patch 目标）。

    `_prefetch_fill` / `PREFETCH_*` / `settle_result` 是这一轮的内部结构，不是 e2e 直接
    import 的门面（`job_ready` 那种才是）。留一份转发名 ⇒ 谁 `setattr(worker, …)` 谁就在写
    一个没人读的变量：测试全绿、注入为零。这种失败比 `AttributeError` 难查得多，所以钉死。
    """
    for name in FORBIDDEN_FACADES:
        assert not hasattr(worker_mod, name), (
            f"remote.worker.{name} 还在 —— 它是静默空操作的 patch 目标；"
            f"要拦它请 patch `remote.job_round.{name}`"
        )


def test_the_per_job_round_is_gone_from_the_polling_loop() -> None:
    """★ 轮询壳（`worker_loop`）里不许再有「一轮」的调用点——它只该调 `run_one_round`。"""
    calls = _calls(_func(WORKER_FILE, "worker_loop"))
    assert ROUND_FN in calls, "worker_loop 没调 run_one_round"
    leftovers = sorted(
        calls
        & {
            "_prefetch_fill",
            "_wire_start",
            "_wire_flush",
            "abandon_job",
            "heartbeat",
            "job_ready",
            "job_started",
            "release_job",
            "report_job_failure",
            "run_job",
            "start_cancel_watcher",
        }
    )
    assert leftovers == [], (
        f"`worker_loop` 里还有「一轮」的调用点：{leftovers} —— "
        "那一轮只在 `remote/job_round.run_one_round` 里（本刀的划分：壳管轮询/claim/idle/收尾）"
    )


# ───────────────── ② ★ 接口双向一致：调用点关键字 == 形参集合 ─────────────────


def _round_params() -> tuple[list[str], list[str]]:
    fn = _func(ROUND_FILE, ROUND_FN)
    pos = [a.arg for a in fn.args.posonlyargs] + [a.arg for a in fn.args.args]
    return pos, [a.arg for a in fn.args.kwonlyargs]


def test_the_call_site_passes_exactly_the_round_interface() -> None:
    """★ **接口双向一致**：`worker_loop` 调用点的位置实参个数与关键字集合，必须与 `run_one_round`
    的签名逐名相符。

    20 个入参的搬运，**漏传一个**是这一刀最可能的失误形态：运行时不一定立刻炸（只有走到那条
    分支才 NameError），而这条断言在**提交时**就红。反向也钉住（原里删了形参而调用点还传着）。
    """
    pos, kwonly = _round_params()
    assert pos == ["base_url", "token", "job"], pos
    calls = [
        n
        for n in ast.walk(_func(WORKER_FILE, "worker_loop"))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == ROUND_FN
    ]
    assert len(calls) == 1, f"`worker_loop` 里应只有一处 `{ROUND_FN}(...)`，实得 {len(calls)}"
    call = calls[0]
    assert len(call.args) == len(pos), f"位置实参应 {len(pos)} 个（{pos}），实得 {len(call.args)}"
    got: list[str] = []
    for k in call.keywords:
        assert k.arg is not None, "调用点用了 `**` 展开 —— 接口必须逐名显式（否则双向一致形同虚设）"
        got.append(k.arg)
    assert sorted(got) == sorted(kwonly), (
        "调用点的关键字与 `run_one_round` 形参不一致：\n"
        f"  多传/名字写错：{sorted(set(got) - set(kwonly))}\n"
        f"  漏传：{sorted(set(kwonly) - set(got))}"
    )


def test_no_keyword_is_swallowed_by_kwargs() -> None:
    """`run_one_round` **不得**用 `**kwargs` 吞掉接口（吞掉后上面那条双向一致就失去意义）。"""
    assert _func(ROUND_FILE, ROUND_FN).args.kwarg is None, "`run_one_round` 不该有 **kwargs"


# ───────────────────── ③ 依赖方向 / 顶层零状态 ─────────────────────


def test_the_round_never_imports_worker_or_anything_above_it() -> None:
    """★ `job_round`（L4）不许 import `worker`(L5) 或任何上层——**含延迟 import**。"""
    # ★ 前缀匹配会误伤 `remote.worker_proc`（L0，合法）⇒ 按**模块名**精确比
    upstream = _upstream_modules()
    assert "remote.worker" in upstream and "remote.worker_proc" not in upstream  # 尺子先自证
    bad = sorted(
        m
        for m in _all_imports(_tree(ROUND_FILE))
        if any(m == u or m.startswith(u + ".") for u in upstream)
    )
    assert bad == [], (
        f"remote/job_round.py 反向 import 了 {bad} —— 它必须在宿主**下面**（L4 < L5）；"
        "「怎么跑一份作业」由宿主注入（`run_job_fn`）；这条比账本那条强在**含延迟 import**"
    )


def test_the_round_only_touches_declared_clusters() -> None:
    """它依赖的业务簇是既定的那几个（多一个就说明又搬漏/搬多了），且账本对账全绿。"""
    dag.assert_remote_module("remote.job_round", allowed_project_imports=ALLOWED_PROJECT_IMPORTS)


def test_round_module_has_no_mutable_global_state() -> None:
    """本刀搬的是**一个轮次的执行**，不是状态：顶层不许出现可变容器或 `global`。"""
    mutable: set[str] = set()
    globals_declared: set[str] = set()
    for node in _tree(ROUND_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if isinstance(value, (ast.List, ast.Dict, ast.Set)):
                mutable.add(name)
    for walked in ast.walk(_tree(ROUND_FILE)):  # 换个名：上一个循环把 `node` narrow 成了 `stmt`
        if isinstance(walked, ast.Global):
            globals_declared.update(walked.names)
    assert mutable == set(), sorted(mutable)
    assert globals_declared == set(), sorted(globals_declared)


# ──────────────────── ④ ★ 注入点分档（两个方向）────────────────────


def test_run_job_is_read_as_a_value_at_the_call_site() -> None:
    """★ 档位一（宿主命名空间）：`run_job_fn=run_job` 必须在**调用点**读 `worker` 的模块全局**值**。

    判据是 AST 的 `Name`（引用），不是 `Call`——`run_job` 在本模块里从头到尾只是被**当值传出去**。
    正因为读的是 `worker` 的全局，`monkeypatch.setattr(remote.worker, "run_job", …)` 才照旧生效
    （功能性证明就是那 10 处既有 patch：`test_async_result_upload.py` / `test_priority_schedule.py` /
    `test_remote_hotswap.py` / `test_soft_hold_prefetch.py` —— 它们全都还在 patch `W`）。
    """
    calls = [
        n
        for n in ast.walk(_func(WORKER_FILE, "worker_loop"))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == ROUND_FN
    ]
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "run_job_fn" in kw, "调用点没传 run_job_fn"
    assert isinstance(kw["run_job_fn"], ast.Name) and kw["run_job_fn"].id == "run_job", (
        "`run_job_fn` 必须是**裸名字** `run_job`（读 worker 的模块全局）——"
        "写成 `worker.run_job` / 包一层 lambda 都会让那 10 处 patch 变成空操作"
    )


def test_the_moved_seam_resolves_in_the_round_module(monkeypatch, tmp_path: Path) -> None:
    """★ 档位二（本模块命名空间）：把 `remote.worker.peek_jobs` 换成炸弹、`remote.job_round.peek_jobs`
    记数，`_prefetch_fill` 必须走**本模块**那一份（炸弹没炸、记数非零）。

    （形状与原 `tests/test_job_lifecycle_split.py::test_host_call_site_still_resolves_the_worker_namespace`
    相同，只是 `_prefetch_fill` 已随本刀从上位宿主下沉到本模块。）
    """
    hit: list[int] = []

    def _fake_peek(*a, **k):
        hit.append(1)
        return ([], False)

    monkeypatch.setattr(round_mod, "peek_jobs", _fake_peek)
    monkeypatch.setattr(
        worker_mod,
        "peek_jobs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏：填充器不该走 remote.worker")),
    )
    monkeypatch.setattr(round_mod, "PREFETCH_ROUND_SEC", 0.02)

    stop = threading.Event()
    store = PrefetchStore(tmp_path / "work", budget_bytes=1 << 20)
    t = threading.Thread(
        target=round_mod._prefetch_fill,
        args=("http://hub", "tok", store, stop),
        kwargs={"depth": 1, "log": lambda _m: None},
        daemon=True,
    )
    t.start()
    deadline = time.time() + 5
    while not hit and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert hit, "`_prefetch_fill` 没调用 `remote.job_round.peek_jobs`——seam 分档变了"
    assert not t.is_alive(), "填充器线程没退出"


def test_the_host_never_reaches_the_round_by_attribute_access() -> None:
    """★ 警报：宿主里的 `run_one_round` 必须是**裸名字**，且不得出现 `job_round.` / `JR.` 属性访问。

    属性式访问等于换了命名空间：`patch remote.worker.run_one_round`（§④ 的功能性用例就靠它）
    以及 §② 的按名对账都会静默失效。
    """
    src = WORKER_FILE.read_text(encoding="utf-8")
    assert "job_round.run_" not in src and "JR." not in src, (
        "worker.py 出现 `job_round.xxx` / `JR.xxx` 属性访问 —— 命名空间就换了"
    )
    assert worker_mod.run_one_round is round_mod.run_one_round, "宿主没转发同一个对象"


# ──────────────────── ⑤ ★ 宿主的账从 RoundOutcome 读 ────────────────────


def test_host_counters_stay_in_the_host() -> None:
    """`done` 是宿主的计数、`polls_since_accept` 是宿主的诊断读数——它们都**只能回读**。

    `RoundOutcome.uploaded` 就是那条回读契约（搬移前是块里直接 `done += 1` / `_polls_since_accept = 0`）。
    """
    fields = {f.name for f in round_mod.RoundOutcome.__dataclass_fields__.values()}
    assert fields == {"jid", "ok", "uploaded", "stop"}, fields
    assert "done" not in _defs(ROUND_FILE), "job_round 里冒出宿主计数器 `done`"


def test_host_bookkeeping_reads_the_round_outcome(monkeypatch, tmp_path: Path) -> None:
    """★ 功能性：用一个假的 `round`/`acquire` 把宿主的**两条回读分支**都走一遍。

    * 第一轮 `uploaded=True` ⇒ 宿主必须 `done += 1`（搬移前它在块内）；
    * 第二轮 `stop=True` ⇒ 宿主必须**整条退出**（搬移前是块里的 `return done`）。
    """
    seen: list[int] = []

    def _fake_round(base_url, token, job, **kw):
        seen.append(1)
        if len(seen) == 1:
            return round_mod.RoundOutcome(jid=job["job_id"], ok=True, uploaded=True, stop=False)
        return round_mod.RoundOutcome(jid=job["job_id"], ok=False, uploaded=False, stop=True)

    n_acquired = {"n": 0}

    def _acquire(*a, **k):
        n_acquired["n"] += 1
        return {"job_id": f"j{n_acquired['n']}", "manifest": {}, "status": "ok", "lease_token": ""}

    monkeypatch.setattr(worker_mod, "run_one_round", _fake_round)
    monkeypatch.setattr(worker_mod, "acquire_job", _acquire)
    monkeypatch.setattr(worker_mod, "_release_cloud_machine", lambda *a, **k: None)

    n = worker_mod.worker_loop("http://hub", "tok", work_dir=tmp_path, poll_sec=0.01, log=lambda _m: None)
    assert len(seen) == 2, f"应当跑两轮（第二轮 stop 整条退出）：{len(seen)}"
    assert n == 1, f"只有第一轮 uploaded ⇒ done == 1；`stop=True` 必须整条退出（实得 {n}）"


# ───────────────────────── ⑥ 账本：层号就是拓扑秩 ─────────────────────────


def test_job_round_sits_below_the_host() -> None:
    """它必须在宿主**下面**（一个组装一轮，一个管轮询），且低于它依赖的业务簇之上一层。"""
    layers = dag.LAYERS
    assert layers["remote.job_round"] < layers["remote.worker"]
    assert layers["remote.job_round"] > layers["remote.job_lifecycle"]
    assert layers["remote.job_round"] == layers["remote.train_core"], (
        "它与训练核同层（同一理由：靠 L3 业务簇组装出一个单元）"
    )
