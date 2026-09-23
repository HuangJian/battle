"""拆分的**契约守卫**：半离线执行引擎永住 `remote/plan_run.py`（2026-09-23 拆 `run_loop ⇄ worker` 环）。

## 这一刀解决了什么

`remote/` 的依赖账本（`tests/helpers/remote_dag.py`）里原来唯一被登记的环是 `run_loop ⇄ worker`：
`worker.run_job` 在 kind=run 的尾巴上要 `verify_plan_file` / `run_plan_job`（都在 `run_loop`），
而 `run_loop` 每跑一轮又要回头调 `worker.run_job`。两侧都是**函数内延迟 import**（不是为了偷懒，
是为了 `run_loop` 顶层保持 torch-light）。

拆法不是「把两个函数搬下去」——量过之后才知道那两个函数的传递闭包**就是整个执行引擎**
（`RunContext` + 单轮执行 + 主循环，约 **963 行**）。所以是**引擎整块下沉**到 L2：

```
plan_run（L2；不 import worker / run_loop）        ← 引擎：计划交接 + 运行上下文 + 单轮 + 主循环
   ↑                              ↑
worker（L4，kind=run 尾巴）      run_loop（L5，CLI / 独立续跑 / 门面）
   └─ run_job_fn=run_job ─┘  └─ run_job_fn=_real_run_job ─┘   ← 「一轮怎么跑」由调用方注入
```

于是 `remote/__init__` 里那个**尾部的 `_real_run_job` 兜底被删除**：引擎不再替调用方决定用谁的
job 执行器（那正是反向 import 的成因）。守卫钉的就是这条——**引擎里一旦出现 `_real_run_job`
（或任何 `remote.worker` / `remote.run_loop` 的 import），环就回来了**。

## 注入点分档（本仓的老规矩，这里第一次是「拆环」而不是「拆文件」）

| 名字 | 调用点解析在 | patch 目标 |
|---|---|---|
| `iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …（引擎读的模块全局） | **`remote.plan_run`** | **`plan_run`** |
| 引擎的公开名（`run_plan_job` / `verify_plan_file` / `RunContext` / `_drive` …） | `run_loop` 只做**门面转发** | 取名字可以，**patch 无效**（同一对象） |
| `worker.run_job` 侧：`plan_run.run_plan_job(..., run_job_fn=run_job)` | — | — |

`tests/test_run_loop.py` 里那条 `monkeypatch.setattr(run_loop_mod, "iter_spec", spy)` 已随本刀迁到
`plan_run`（`run_loop` **不再转发** `iter_spec` ⇒ patch 它是 AttributeError，**响亮**而不是静默）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.plan_run as plan_run_mod
import remote.run_loop as run_loop_mod
import remote.worker as worker_mod
from tests.helpers import remote_dag as dag

ENGINE_FILE = ROOT / "remote" / "plan_run.py"
ENTRY_FILE = ROOT / "remote" / "run_loop.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

#: 搬进引擎的名字（引擎的**自有名**；不含它从别处 import 的东西）。
ENGINE_NAMES = {
    "ITER_RETRIES",
    "RunContext",
    "TS_CODE_ZIP_NAME",
    "TS_TREE_DIR",
    "_blob_roots",
    "_carry_ts_tree",
    "_checkpoint",
    "_close_eval",
    "_combined",
    "_drive",
    "_encode_opt",
    "_eval_job_builder",
    "_eval_round_done",
    "_log_default",
    "_maybe_cloud_eval",
    "_opt_bytes_from_manifest",
    "_opt_bytes_from_result",
    "_read_opt_file",
    "_run_iteration",
    "_run_with_retries",
    "_seed_demo_blob_cache",
    "_seed_start_checkpoint",
    "_setup_cloud_eval",
    "_stored_opt_sha",
    "_ts_tree_root",
    "_weight_bytes",
    "open_run_context",
    "run_plan_job",
    "runner_timeout",
    "verify_plan_file",
    "with_rollout_workers",
    # 下面两个 `_drive` 用、但它们是 run_loop 时代的同族助手
}
#: 留在入口的名字（只有入口用它）。
ENTRY_NAMES = {
    "_real_run_job",
    "_require_offline_runtime",
    "_resolve_hub_token",
    "apply_resume_overlay",
    "ensure_ts_cache_layout",
    "load_planned_manifest",
    "main",
    "run_standalone",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imports(path: Path) -> set[str]:
    """全部 import（含函数内延迟 import）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_engine_names_live_in_plan_run_and_not_redefined_in_run_loop() -> None:
    """引擎的名字只在 `plan_run.py` 里实现（`run_loop` 只剩入口自己的 + 门面转发）。"""
    assert _defined(ENGINE_FILE) >= ENGINE_NAMES, sorted(ENGINE_NAMES - _defined(ENGINE_FILE))
    leftovers = ENGINE_NAMES & _defined(ENTRY_FILE)
    assert leftovers == set(), f"run_loop.py 里仍在实现引擎的名字（应只做转发）：{sorted(leftovers)}"
    # 反向：入口自己的名字不该跑到引擎里去
    bleed = ENTRY_NAMES & _defined(ENGINE_FILE)
    assert bleed == set(), f"入口的名字出现在引擎里（划分错了）：{sorted(bleed)}"


def test_entry_keeps_the_entry_only_names() -> None:
    """入口留下的就是「只有入口用」的那几个（CLI / 独立续跑 / 真 worker 注入）。"""
    assert _defined(ENTRY_FILE) >= ENTRY_NAMES, sorted(ENTRY_NAMES - _defined(ENTRY_FILE))


# ─────────────────────── ② 依赖方向：环不许回来 ───────────────────────


def test_plan_run_never_imports_worker_or_run_loop() -> None:
    """★ 本刀的全部意义：引擎里**不许**出现 `remote.worker` / `remote.run_loop`（含延迟 import）。"""
    imported = _imports(ENGINE_FILE)
    back = sorted(m for m in imported if m.startswith(("remote.worker", "remote.run_loop")))
    assert back == [], (
        f"remote/plan_run.py 反向 import 了 {back} —— `run_loop ⇄ worker` 那个环会立刻回来；"
        "「一轮怎么跑」必须由调用方用 run_job_fn 注入（见本文件头部）"
    )
    # 也不许碰 rl 之外的回合层：引擎在 L2，`rl.plan` 是它的正常依赖，但不得 import 入口
    assert "remote.run_loop" not in imported


def test_engine_has_no_worker_fallback_any_more() -> None:
    """★ 引擎里那个 `_real_run_job` 兜底必须**不存在**（它就是环的成因）。

    判据分三层，缺一不可：
      ① `plan_run` 里没有 `_real_run_job` 这个名字（连引用都没有）；
      ② `_run_iteration` 里没有 `... or _real_run_job` 这种回落；
      ③ 取而代之的是一条**响亮**的 RuntimeError（漏注入时立即炸，而不是静默跑错执行器）。
    """
    tree = _tree(ENGINE_FILE)
    # ① 代码里不得引用这个名字（docstring 里提到它不算——判据必须走 AST 而不是文本匹配）
    loads = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    assert "_real_run_job" not in loads, "引擎代码里又引用了 `_real_run_job`（= 反向 import worker）"
    imported = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "_real_run_job" not in imported
    # ② `run_job = ctx.run_job_fn`——**没有** `or …` 回落
    run_iter = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_iteration"
    )
    assigns = [
        n
        for n in ast.walk(run_iter)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "run_job" for t in n.targets)
    ]
    assert len(assigns) == 1, f"`_run_iteration` 里 run_job 的赋值应只有一处，实得 {len(assigns)}"
    assert ast.unparse(assigns[0].value) == "ctx.run_job_fn", (
        f"`_run_iteration` 里又出现了回落：run_job = {ast.unparse(assigns[0].value)}"
    )
    # ③ 漏注入时**响亮报错**（而不是 None() 静默炸在别处）
    assert "run_job_fn 未注入" in ENGINE_FILE.read_text(encoding="utf-8")


def test_worker_tail_imports_the_engine_and_injects_itself() -> None:
    """worker 侧的 kind=run 尾巴：延迟 import **引擎**，并把**自己**传进去。

    这两条合起来才是「拆环」：`worker → plan_run`（向下）+ 注入 `run_job_fn`（不回指）。
    """
    src = WORKER_FILE.read_text(encoding="utf-8")
    assert "from remote.plan_run import verify_plan_file" in src, "worker 的 plan 校验没指向引擎"
    assert "from remote.plan_run import run_plan_job" in src, "worker 的 plan 尾巴没指向引擎"
    assert (
        "run_job_fn=run_job," in src
    ), "worker 没把**自己**注入引擎（漏了它 = 云机上 kind=run 直接 RuntimeError）"
    tree = _tree(WORKER_FILE)
    worker_imports = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("remote.run_loop")
    }
    assert worker_imports == set(), f"worker 又直接 import 了入口模块：{sorted(worker_imports)}"


def test_entry_injects_the_real_worker_and_keeps_it_deferred() -> None:
    """入口侧的注入：`run_standalone` 传 `_real_run_job`，而它自己仍然**函数内**延迟 import worker。

    「延迟」是原来就有的性质（`run_loop` 顶层保持 torch-light，`worker` 拖整条 torch 链），
    拆环不能把它弄丢——所以这条与 `plan_run` 的分层断言配套存在。
    """
    src = ENTRY_FILE.read_text(encoding="utf-8")
    assert "run_job_fn=run_job_fn or _real_run_job" in src, "入口没有再兜底真 worker"
    assert "remote.worker" not in _top_level_imports(ENTRY_FILE), (
        "run_loop 顶层 import 了 worker ⇒ 顶层不再 torch-light"
    )
    # 功能上真的能转发：把 worker.run_job 换成替身，`_real_run_job` 必须调到它
    seen: list[tuple] = []

    def fake(*a, **kw):
        seen.append((a, kw))
        return {"ok": 1}

    orig = worker_mod.run_job
    worker_mod.run_job = fake  # _real_run_job 每次现取 worker 的模块全局
    try:
        out = run_loop_mod._real_run_job("u", "t", {"job_id": "j"})
    finally:
        worker_mod.run_job = orig
    assert out == {"ok": 1} and len(seen) == 1, "`_real_run_job` 没有把调用转给 worker.run_job"


def _top_level_imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


# ───────────────────────── ③ 门面 ─────────────────────────


def test_run_loop_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与引擎里是**同一个对象**（不是副本）。"""
    for name in sorted(ENGINE_NAMES):
        if not hasattr(run_loop_mod, name):
            continue  # 不是所有引擎名都需要门面（只转发被外部引用的那些）
        assert getattr(run_loop_mod, name) is getattr(plan_run_mod, name), (
            f"remote.run_loop.{name} 不是 remote.plan_run.{name}（转发成了副本）"
        )


def test_facade_covers_every_engine_name_the_entry_reads() -> None:
    """★ 门面**不许漏**：入口代码读到的每个引擎名都必须在 `run_loop` 的命名空间里（否则 F821）。

    判据是动态的（从入口源码里取 `Name` 引用），所以将来在入口里多用一个引擎函数而忘了加门面，
    这条会红——而不是等到运行时 NameError。
    """
    entry_needs = {
        n.id for n in ast.walk(_tree(ENTRY_FILE)) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    engine_attrs = set(dir(plan_run_mod))
    missing = sorted(n for n in (entry_needs & engine_attrs) if not hasattr(run_loop_mod, n))
    assert missing == [], f"入口读到的引擎名没有门面（会 NameError）：{missing}"


def test_iter_spec_seam_is_the_engine_module() -> None:
    """★ 注入点分档：`iter_spec` 的**调用点在引擎里** ⇒ 必须 patch `remote.plan_run`。

    三条一起看才是完整判据：
      ① 调用点在 `plan_run`（AST：`_run_iteration` 里 `iter_spec(...)`）；
      ② `run_loop` **不再转发**它（`hasattr` 为假 ⇒ 打错模块是 **AttributeError**，响亮失败，
         而不是「patch 上了但没人读」那种静默失效）；
      ③ 测试里那条 seam **跟着迁了**（读 `tests/test_run_loop.py` 的源码句）：仍然写
         `setattr(plan_run_mod, "iter_spec"`，且**没有任何** `setattr(run_loop_mod, "iter_spec"`。
         功能性的全链用例就是它（`test_run_standalone_loads_course_snapshot_into_iter_spec`）。
    """
    run_iter = next(
        n for n in _tree(ENGINE_FILE).body if isinstance(n, ast.FunctionDef) and n.name == "_run_iteration"
    )
    called = {
        n.func.id
        for n in ast.walk(run_iter)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "iter_spec" in called, "`_run_iteration` 不再直接调 `iter_spec`（seam 需重判）"
    assert not hasattr(run_loop_mod, "iter_spec"), (
        "run_loop 又转发了 iter_spec ⇒ 打错模块的 patch 会**静默失效**"
    )
    test_src = (ROOT / "tests" / "test_run_loop.py").read_text(encoding="utf-8")
    assert 'setattr(plan_run_mod, "iter_spec"' in test_src, "seam 没跟着迁到 remote.plan_run"
    assert 'setattr(run_loop_mod, "iter_spec"' not in test_src, "还有测试在打入口模块的 iter_spec（空操作）"
    # 且引擎里确实有这个模块级名字（叫得响的依赖，不是局部变量瓶）
    imported = {
        a.name
        for n in _tree(ENGINE_FILE).body
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    assert "iter_spec" in imported, "引擎不再从 `rl.plan` 取 iter_spec（seam 需重判）"


def test_deferred_cycle_ledger_is_empty_now() -> None:
    """★ 环的账本现在**空**，且全图无环——本刀的目标状态（比「声明相等」更强）。"""
    assert dag.DEFERRED_CYCLES == {}, f"账本里还有声明的环：{sorted(dag.DEFERRED_CYCLES)}"
    top, deferred, _ = dag.graph()
    assert dag.cycles({m: set(top[m]) | set(deferred[m]) for m in top}) == []
    assert dag.LAYERS["remote.plan_run"] < dag.LAYERS["remote.worker"] < dag.LAYERS["remote.run_loop"], (
        "分层不对：引擎必须在 worker / run_loop **下面**（它是两者共同的底座）"
    )
