"""拆分的**契约守卫**：作业取活 / 生命周期 / 回传面永住 `remote/job_lifecycle.py`（S4 第八刀，2026-09-23）。

搬走的一整块连续代码（17 个顶层名）：`peek_jobs` · `request_priority` · `claim_job` ·
`_priority_rank` · `acquire_job` · `job_started` · `job_ready` · `abandon_job` · `job_status` ·
`start_cancel_watcher` · `post_result` · `release_job` · `worker_tag` · `_failure_detail` ·
`job_body_error` · `report_job_failure` · `heartbeat`。`remote/worker.py` 2348 → 1805 行。

这是 S4 里 **seam 最密**的一刀（全仓对这簇有 60+ 处 `monkeypatch.setattr`），所以本文件钉得比
前几刀细：

1. **定义唯一** —— 不许在 `worker.py` 里再实现一遍；
2. **依赖方向** —— `job_lifecycle` 只许向下依赖（`common.*` / `remote.{http,wire,bulk_sched}`），
   不得 import `remote.worker`（那才是环）；
3. **转发同一对象** —— 宿主与测试都从 `remote.worker` 取这些名字；
4. **顶层零可变状态** —— 这簇是纯函数面，搬它不搬状态：顶层不许出现可变容器或 `global`；
5. ✭ **注入点分档（本刀的主坑）**，两个方向各一条**功能性**断言：
   档位一（宿主调用点，仍在 `worker.py`）：`_prefetch_fill` 里的 `peek_jobs` ⇒ patch `remote.worker`；
   档位二（簇内互调，已随簇搬走）：`acquire_job` → `peek_jobs`/`request_priority`/`claim_job`
   ⇒ patch `remote.job_lifecycle`。**两个方向都要有断言**，否则「patch 打偏而测试全绿」查不出来。
6. ✭ **`_request` 一族在 `worker.py` 已无调用点** —— `patch remote.worker._request` 从本刀起是
   **空操作**。转发名保留是因为仍有测试直接 import 它（`tests/test_loopback_http_no_proxy.py`），
   不是因为还有人调用。谁在 worker 里写出新的 `_request` 调用点，这条会红。
7. ✭ **「引用即接缝」** —— `worker_loop` 把 `post_result` 当**值**传出去
   （`ResultUploader(upload=post_result, …)`），读的同样是 `worker` 的模块全局 ⇒
   `patch remote.worker.post_result` **照旧有效**。这条最容易被误判成「worker 里没有调用点 ⇒
   那 9 处 patch 全失效了」，所以判据必须用 AST 的 **`Load`**（引用）而不是 `Call`（调用）。
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

import remote.job_lifecycle as jl_mod
import remote.worker as worker_mod
from remote.prefetch import PrefetchStore

JL_FILE = ROOT / "remote" / "job_lifecycle.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

MOVED_NAMES = {
    "_failure_detail",
    "_priority_rank",
    "abandon_job",
    "acquire_job",
    "claim_job",
    "heartbeat",
    "job_body_error",
    "job_ready",
    "job_started",
    "job_status",
    "peek_jobs",
    "post_result",
    "release_job",
    "report_job_failure",
    "request_priority",
    "start_cancel_watcher",
    "worker_tag",
}
ALLOWED_IMPORTS = {
    "common.protocol",
    "common.text",
    "remote.bulk_sched",
    "remote.http",
    "remote.wire",
}
PROJECT_ROOTS = {
    "common",
    "remote",
    "rl",
    "data",
    "train",
    "models",
    "ppo",
    "scripts",
    "dist_common",
    "platform_utils",
    "pid_probe",
    "schema",
}
#: `worker.py` 里已无**调用点**的 `_request` 一族（值仍可转发）。
NO_CALL_SITE_IN_WORKER = {
    "_bulk_pace",
    "_request",
    "_sched_headers",
    "_warn_non_200",
    "_wire_add",
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


def _names(path: Path, ctx: type[ast.expr_context]) -> set[str]:
    """该文件里以**裸名字**出现、且处于指定上下文（`Load` = 引用 / `Call` 另判）的名字。"""
    return {
        n.id
        for n in ast.walk(_tree(path))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ctx)
    }


def _imports(path: Path, *, top_only: bool = False) -> set[str]:
    nodes = _tree(path).body if top_only else list(ast.walk(_tree(path)))
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _bare_calls(path: Path) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(_tree(path))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


# ─────────────────────────── 结构契约 ───────────────────────────


def test_moved_names_are_defined_in_job_lifecycle_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `job_lifecycle.py` 里实现。"""
    assert _defined(JL_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(JL_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_job_lifecycle_does_not_import_worker_and_only_depends_downwards() -> None:
    """无环 + 白名单：不得 import `remote.worker`；模块级只许向下依赖。"""
    assert "remote.worker" not in _imports(JL_FILE), "job_lifecycle 反向 import worker ⇒ 环"
    top = _imports(JL_FILE, top_only=True)
    extra = {m for m in top if m.split(".")[0] in PROJECT_ROOTS and m not in ALLOWED_IMPORTS}
    assert extra == set(), f"job_lifecycle.py 顶层出现未登记的仓内依赖：{sorted(extra)}"


def test_worker_forwards_every_moved_name_as_the_same_object() -> None:
    """宿主与测试都从 `remote.worker` 取这些名字 ⇒ 必须是同一对象（`from x import y as y`）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(jl_mod, name), (
            f"remote.worker.{name} 不是 remote.job_lifecycle.{name}（转发成了副本）"
        )


def test_job_lifecycle_has_no_top_level_mutable_state() -> None:
    """搬走的是**纯函数面**（零模块级状态）——顶层不该多出可变容器，也不该有 `global`。"""
    mutable: set[str] = set()
    for node in _tree(JL_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == set(), f"job_lifecycle.py 顶层多了可变容器：{sorted(mutable)}"
    globals_decl = [
        node.names for node in ast.walk(_tree(JL_FILE)) if isinstance(node, ast.Global)
    ]
    assert globals_decl == [], f"job_lifecycle.py 里出现 `global`（状态该住 wire/http）：{globals_decl}"


# ───────────────────────── 注入点分档（本刀主坑） ─────────────────────────


def test_cluster_internal_seam_is_the_job_lifecycle_module(monkeypatch) -> None:
    """✭ 档位二：`acquire_job` → `peek_jobs` / `request_priority` / `claim_job` 是**簇内互调**，
    解析在 **`remote.job_lifecycle`** 的模块全局。

    「把 `remote.worker.peek_jobs` / `request_priority` / `claim_job` 全换成炸弹，只 patch
    `remote.job_lifecycle` 那一份」必须仍然成功——反过来（只 patch worker）就是本步最容易犯、
    且**测试全绿**的静默错误。
    """
    def bomb(*a, **k):
        raise AssertionError("打偏：应 patch job_lifecycle")

    for name in ("peek_jobs", "request_priority", "claim_job"):
        monkeypatch.setattr(worker_mod, name, bomb)

    monkeypatch.setattr(
        jl_mod,
        "peek_jobs",
        lambda *a, **k: ([{"job_id": "j1", "manifest": {"m": 1}, "course": "c1"}], False),
    )
    monkeypatch.setattr(
        jl_mod, "request_priority", lambda *a, **k: {"epoch": 3, "priorities": {"j1": "highest"}}
    )
    monkeypatch.setattr(
        jl_mod,
        "claim_job",
        lambda *a, **k: {"job_id": "j1", "manifest": {"m": 1}, "status": "ok"},
    )

    got = worker_mod.acquire_job("http://hub", "tok", worker_id="host:1")
    assert got is not None and got["job_id"] == "j1", got
    assert got["course"] == "c1", "claim 响应里缺的 course 由候选补上（该字段来自簇内互调）"


def test_host_call_site_still_resolves_the_worker_namespace(monkeypatch, tmp_path: Path) -> None:
    """✭ 档位一：宿主 `_prefetch_fill` 把它当**裸名字**用 ⇒ 解析在 `worker` 命名空间 ⇒
    `monkeypatch.setattr(worker_mod, "peek_jobs", …)` 仍有效
    （`tests/test_soft_hold_prefetch.py` 那三处就靠这个）。

    功能性验证：把 `remote.job_lifecycle.peek_jobs` 换成炸弹、`remote.worker.peek_jobs` 记数，
    填充器必须走 worker 那份（炸弹没炸、记数非零）。
    """
    hit: list[int] = []

    def _fake_peek(*a, **k):
        hit.append(1)
        return ([], False)

    monkeypatch.setattr(worker_mod, "peek_jobs", _fake_peek)
    monkeypatch.setattr(
        jl_mod,
        "peek_jobs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏：宿主没走 worker 那份")),
    )
    monkeypatch.setattr(worker_mod, "PREFETCH_ROUND_SEC", 0.02)

    stop = threading.Event()
    store = PrefetchStore(tmp_path / "work", budget_bytes=1 << 20)
    t = threading.Thread(
        target=worker_mod._prefetch_fill,
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
    assert hit, "宿主 `_prefetch_fill` 没调用 `remote.worker.peek_jobs`——seam 分档变了"
    assert not t.is_alive(), "填充器线程没退出"


def test_request_family_has_no_call_site_in_worker_any_more() -> None:
    """★ 警报：`_request` 一族在 `worker.py` 里已**没有调用点**。

    转发名仍在（有测试直接 import `remote.worker._request`），但「patch `remote.worker._request`
    以改变某个函数的行为」从此是**空操作**——写这类测试的人该去 patch 调用点所在模块
    （`remote.job_lifecycle` / `remote.http`）。谁在 worker 里写出新的调用点，这条会红。
    """
    src = WORKER_FILE.read_text(encoding="utf-8")
    calls = _bare_calls(WORKER_FILE)
    stale = sorted(NO_CALL_SITE_IN_WORKER & calls)
    assert stale == [], f"worker.py 里又出现这些调用了——seam 分档需重判：{stale}"
    # 转发名仍在（否则 `tests/test_loopback_http_no_proxy.py` 会 import 失败）
    assert hasattr(worker_mod, "_request") and "_request as _request" in src
    # 反向：这些调用点确实都搬到了簇里（否则上面那条是空的）
    jl_calls = _bare_calls(JL_FILE)
    assert {"_request", "_wire_add", "_bulk_pace"} <= jl_calls, sorted(
        {"_request", "_wire_add", "_bulk_pace"} - jl_calls
    )


def test_host_reference_counts_as_a_seam_keeps_post_result_patches_alive() -> None:
    """★ 「引用即接缝」：`worker_loop` 把 `post_result` 当**值**传出去
    （`ResultUploader(upload=post_result, …)`），读的仍是 `worker` 的模块全局。

    所以全仓那 9 处 `patch remote.worker.post_result` **没有**因为本刀失效——判据要用 AST 的
    `Load`（引用）而不是 `Call`（调用），否则会把它们误判成死注入。
    """
    loads: dict[str, set[str]] = {}
    for node in _tree(WORKER_FILE).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            loads[node.name] = {
                n.id
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
    assert "post_result" in loads.get("worker_loop", set()), (
        "worker_loop 不再引用 post_result ⇒ `patch remote.worker.post_result` 变空操作，"
        "那批测试要改指 remote.job_lifecycle"
    )
    # 而且它是被**传值**（关键字实参 `upload=`），不是被调用——这正是「无调用点但接缝仍在」
    # 的形态。哪天改成调用（`upload=post_result(...)`）或属性式（`jl.post_result`），语义就变了。
    assert "post_result" not in _bare_calls(WORKER_FILE), (
        "worker.py 里出现了 post_result 的调用点——该行不再是「传值引用」，分档需重判"
    )
    src = WORKER_FILE.read_text(encoding="utf-8")
    assert "upload=post_result" in src, "引用形态变了（不再是关键字实参传值）——seam 需重判"
