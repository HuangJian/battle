"""拆分的**契约守卫**：HTTP 传输核心永住 `remote/http.py`（S4 第五步，2026-09-23）。

`remote/worker.py` 3290 → 3016 行；搬走的簇（`_opener` + `_get_opener` · `BODY_*` 阈值 ·
`_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` ·
`_get_with_retry`）是 worker 里**所有**业务功能的公共底座。

本文件钉五件事：

1. **定义唯一**——这些名字不许在 `worker.py` 里再实现一遍；
2. **无环**——`http.py` 不得 import `worker.py`（`worker` 已经 import `http` 做转发）；
3. **转发同一对象** + **状态一份**（`worker._POLL_WARN_AT` 与 `http._POLL_WARN_AT` 同一个 dict）；
4. **注入点分档**（这一步最容易静默坏）：`_get_with_retry` / `_read_body` 在 `http` 命名空间
   解析 `_request` / `_reroll_decision` / `BODY_PROGRESS_MIN_SEC` ⇒ 必须 patch `remote.http`；
   而直调 `_request` 的**作业面**（`post_result` 等）在**各自定义模块**的命名空间解析 ⇒ patch
   那个模块。两个方向各一条断言，防止「patch 打偏而测试全绿」。

⚠ 2026-09-23 第八刀之后：「直调 `_request` 的作业面」已搬到 `remote/job_lifecycle.py` ⇒
档位二的注入点随它改指 `remote.job_lifecycle`；`remote.worker._request` 仍是**转发名**
（`tests/test_loopback_http_no_proxy.py` 直接 import 它），但 `worker.py` 里**已无调用点**。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.http as http_mod
import remote.job_lifecycle as jl_mod
import remote.worker as worker_mod
from tests.helpers import remote_dag as dag

HTTP_FILE = ROOT / "remote" / "http.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

#: 本次搬走的**定义**——只许在 `http.py` 里出现。
MOVED_NAMES = {
    "BODY_CHUNK",
    "BODY_IDLE_TIMEOUT_SEC",
    "BODY_PROGRESS_MIN_SEC",
    "BODY_TOTAL_TIMEOUT_SEC",
    "_POLL_WARN_AT",
    "_get_opener",
    "_get_with_retry",
    "_opener",
    "_read_body",
    "_request",
    "_sched_headers",
    "_warn_non_200",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    """`def` / `class` / 顶层赋值定义的函数名 / 类名 / 变量名。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_moved_names_are_defined_in_http_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `http.py` 里实现。"""
    assert _defined(HTTP_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(HTTP_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_http_sits_below_its_remote_dependencies() -> None:
    """无环 + 分层：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得 import `remote.worker`」的特指断言；现在是一般化的
    「顶层 intra-remote 边必须严格向下」+「任何 import 不得碰 `rl`」，
    与 `tests/test_remote_dag.py` 的整图对账共用**同一份实现**。
    """
    dag.assert_remote_module("remote.http")


#: 转发名会**停在旧值**的宿主状态（`global` 重绑）：`_get_opener()` 里 `global _opener` 懒建，
#: 于是 `worker._opener` 永远是 import 那一刻的快照。对它们只能断言「名字在」，
#: 不能断言 `is` —— 那会随**文件顺序**红绿翻转（见下面那条语义用例）。
REBOUND_NAMES = {"_opener"}


def test_worker_forwards_every_moved_name() -> None:
    """`worker` 的命名空间里每个搬走的名字都在，且**函数/常量**是同一个对象。

    `REBOUND_NAMES`（重绑式标量）只查「名字还在」：它们的注入点是所有者模块（`remote.http`），
    转发名只是兼容壳。
    """
    for name in sorted(MOVED_NAMES - REBOUND_NAMES):
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(http_mod, name), (
            f"remote.worker.{name} 不是 remote.http.{name}（转发成了副本）"
        )
    for name in sorted(REBOUND_NAMES):
        assert hasattr(worker_mod, name), f"remote.worker 丢了转发名 {name}"


def test_opener_forwarding_name_is_a_snapshot_by_design_not_an_identity() -> None:
    """★ `_opener` 是**重绑式**宿主状态（`http._get_opener` 里 `global _opener` 懒建）⇒
    转发名 `worker._opener` 会停在 import 时的值。

    这条原先写成了 `is` 恒等断言，于是红绿取决于**文件顺序**（先跑的测试有没有触发懒建）：
    `pytest tests/test_priority_schedule.py tests/test_http_split.py` 红、单跑本文件绿
    ——于 2026-09-23 第八刀时实测到（HEAD 上同样可复现，与本刀无关）。
    现在钉住的是与顺序无关的**语义**：重绑只发生在 `http`（唯一所有者），`worker` 侧只有转发
    ⇒ `_opener` 的注入点只能是 `remote.http`（`remote.worker._opener` 改了不改变任何行为）。
    """
    get_opener = next(
        node
        for node in _tree(HTTP_FILE).body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_opener"
    )
    rebinds = [
        node.names
        for node in ast.walk(get_opener)
        if isinstance(node, ast.Global) and "_opener" in node.names
    ]
    assert rebinds, "`_get_opener` 不再重绑 `_opener` ⇒ 懒建语义变了，注入点需重判"
    worker_globals = [
        node.names
        for node in ast.walk(_tree(WORKER_FILE))
        if isinstance(node, ast.Global) and "_opener" in node.names
    ]
    assert worker_globals == [], f"worker.py 里重绑了 `_opener`（所有者只能是 http）：{worker_globals}"
    assert "_opener as _opener" in WORKER_FILE.read_text(encoding="utf-8"), (
        "worker 侧 `_opener` 不再是转发名（有测试/宿主 import 它）"
    )


def test_poll_warn_state_stays_a_single_account_across_both_entry_points() -> None:
    """状态仍是一份账：`_POLL_WARN_AT` 跨两个入口是同一个 dict（原地可变）。"""
    http_mod._POLL_WARN_AT.clear()
    worker_mod._POLL_WARN_AT["http://hub:500"] = 1.0
    assert http_mod._POLL_WARN_AT == {"http://hub:500": 1.0}
    http_mod._POLL_WARN_AT.clear()


def test_http_top_level_mutable_container_is_only_the_warn_throttle() -> None:
    """`http.py` 的顶层可变容器只有 `_POLL_WARN_AT`——不该顺手多带一份共享状态。"""
    mutable: set[str] = set()
    for node in _tree(HTTP_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == {"_POLL_WARN_AT"}, f"http.py 顶层多了可变容器：{sorted(mutable)}"


def test_request_seam_for_the_moved_callers_is_the_http_module(
    monkeypatch,
) -> None:
    """档位一：`_get_with_retry`（在 http）解析 `_request` 于 **http** 命名空间。

    所以「patch `remote.http._request`，同时把 `remote.worker._request` 换成炸弹」
    必须仍然成功——反过来（只 patch worker）是这一步最容易犯的静默错误。
    """
    monkeypatch.setattr(
        worker_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(http_mod, "_request", lambda *a, **k: (200, b"payload-bytes"))
    out = worker_mod._get_with_retry(
        "http://hub", "t", "/jobs/j1/payload", timeout=1.0, attempts=1, log=lambda _m: None
    )
    assert out == b"payload-bytes"


def test_request_seam_for_the_job_face_is_the_job_lifecycle_module(monkeypatch) -> None:
    """档位二：直调 `_request` 的**作业面**（`post_result`）解析在**定义它的模块**命名空间。

    S4 第五步时这一档是 `worker`；第八刀把作业面搬到 `remote/job_lifecycle.py` 后，注入点
    随它改指该模块。所以「patch `remote.job_lifecycle._request`，同时把 `remote.http._request`
    换成炸弹」必须仍然成功。
    """
    monkeypatch.setattr(
        http_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(jl_mod, "_request", lambda *a, **k: (200, b"{}"))
    rc = jl_mod.post_result("http://hub", "t", "jid1", {"job_id": "jid1"}, log=lambda _m: None)
    assert rc == 200


def test_worker_module_has_no_request_call_site_any_more() -> None:
    """★ 警报（第八刀）：`worker.py` 里已**没有** `_request` 的调用点。

    它仍转发 `_request`（`tests/test_loopback_http_no_proxy.py` 直接 import 这个名字），
    但「patch `remote.worker._request` 期望某个函数行为改变」从此是**空操作**——写这类测试
    的人应该去 patch `remote.job_lifecycle` / `remote.http`（看调用点在哪）。
    """
    src = WORKER_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_request" not in calls, "worker.py 里又出现了 `_request` 调用点——seam 分档需重判"
    assert hasattr(worker_mod, "_request"), "`_request` 的转发名不该消失（有测试 import 它）"


def test_body_progress_threshold_is_read_from_the_http_module(monkeypatch) -> None:
    """`BODY_PROGRESS_MIN_SEC` 的注入点 = `remote.http`（`_read_body` 读的是本模块全局）。"""

    class _Resp:
        headers = {"Content-Length": "20"}

        def __init__(self) -> None:
            self._chunks = [b"a" * 10, b"b" * 10]

        def read(self, _n: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

    seen: list[tuple[int, int, float]] = []
    monkeypatch.setattr(worker_mod, "BODY_PROGRESS_MIN_SEC", 1e9)  # 打偏：不该生效（转发名）
    monkeypatch.setattr(http_mod, "BODY_PROGRESS_MIN_SEC", 0.0)
    out = worker_mod._read_body(
        _Resp(), idle_timeout=45.0, total_timeout=None, progress=lambda g, t, e: seen.append((g, t, e))
    )
    assert out == b"a" * 10 + b"b" * 10
    assert [s[0] for s in seen] == [10, 20]
