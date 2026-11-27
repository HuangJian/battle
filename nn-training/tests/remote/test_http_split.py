"""拆分的**契约守卫**：HTTP 传输核心永住 `remote/http.py`（S4 第五步，2026-09-23）。

`remote/worker.py` 3290 → 3016 行；搬走的簇（`_opener` + `_get_opener` · `BODY_*` 阈值 ·
`_read_body` · `_POLL_WARN_AT` + `_warn_non_200` · `_request` · `_sched_headers` ·
`_get_with_retry`）是 worker 里**所有**业务功能的公共底座。

本文件钉五件事：

1. **定义唯一**——这些名字不许在 `worker.py` 里再实现一遍；
2. **无环**——`http.py` 不得 import `worker.py`（`worker` 已经 import `http` 做转发）；
3. **状态住显式对象**（P0-5，2026-11-21）：`http.openers()`（懒建 opener）与
   `http.warn_throttle()`（60s 节流）各自唯一一份，`worker` 侧不再转发这两份状态；
4. **注入点分档**（这一步最容易静默坏）：`_get_with_retry` / `_read_body` 在 `http` 命名空间
   解析 `_request` / `_reroll_decision` / `BODY_PROGRESS_MIN_SEC` ⇒ 必须 patch `remote.http`；
   而直调 `_request` 的**作业面**（`post_result` 等）在**各自定义模块**的命名空间解析 ⇒ patch
   那个模块。两个方向各一条断言，防止「patch 打偏而测试全绿」。

⚠ 2026-09-23 第八刀之后：「直调 `_request` 的作业面」已搬到 `remote/job_lifecycle.py` ⇒
档位二的注入点随它改指 `remote.job_lifecycle`；`remote.worker._request` 仍是**转发名**
（`tests/remote/test_loopback_http_no_proxy.py` 直接 import 它），但 `worker.py` 里**已无调用点**。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.http as http_mod
import remote.job_lifecycle as jl_mod
import remote.worker as worker_mod
from remote.http import WireGet
from remote.job_lifecycle import PostResultSpec
from remote.seams import PortPair
from tests.helpers import remote_dag as dag

HTTP_FILE = ROOT / "remote" / "http.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

#: 本次搬走的**定义**——只许在 `http.py` 里出现。
MOVED_NAMES = {
    "BODY_CHUNK",
    "BODY_IDLE_TIMEOUT_SEC",
    "BODY_PROGRESS_MIN_SEC",
    "BODY_TOTAL_TIMEOUT_SEC",
    "WARN_THROTTLE_SEC",
    "ProxyOpeners",
    "WarnThrottle",
    "_get_opener",
    "_get_with_retry",
    "_read_body",
    "_request",
    "_sched_headers",
    "_warn_non_200",
    "openers",
    "warn_throttle",
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


def test_worker_forwards_every_moved_name() -> None:
    """`worker` 的命名空间里每个搬走的名字都在，且**是同一个对象**。

    P0-5（2026-11-21）之后这里不再有「重绑式标量」这个例外：两份进程级状态都住对象
    （`openers()` / `warn_throttle()`），懒建与重建都在对象内部，转发名不会再停在 import
    那一刻的快照，所以 `is` 恒等断言重新成立（旧 `_opener` 的快照问题见本文件 2026-09-23 那条
    用例的注释历史）。
    """
    for name in sorted(MOVED_NAMES):
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(http_mod, name), (
            f"remote.worker.{name} 不是 remote.http.{name}（转发成了副本）"
        )


def test_http_module_has_no_global_statement_any_more() -> None:
    """★ 懒建状态收进对象后，`http.py` 里**没有 `global`**（`GLOBAL_BASELINE` 里本文件那条已删）。

    这是 P0-5 的判据本体：`global` 意味着「模块里可以有第二处写点」；`ProxyOpeners.reset()`
    与 `WarnThrottle.forget_all()` 是仅有的两个写入口，都在对象上。
    """
    globals_ = [node.names for node in ast.walk(_tree(HTTP_FILE)) if isinstance(node, ast.Global)]
    assert globals_ == [], f"http.py 又出现了 `global`：{globals_}"
    # `worker.py` 自己那处会话指纹（`_CODE`）也已对象化，但它不该
    # 重绑**本模块转发的**那两份状态（否则又会造出一个停在旧值的快照）。
    forwarded = {"_opener", "_POLL_WARN_AT", "_OPENERS", "_WARNS"}
    worker_globals = [
        names
        for node in ast.walk(_tree(WORKER_FILE))
        if isinstance(node, ast.Global)
        for names in [node.names]
        if forwarded & set(names)
    ]
    assert worker_globals == [], f"worker.py 重绑了 http 的状态：{worker_globals}"


def test_state_objects_are_single_accounts_not_forwarded_copies() -> None:
    """状态一份：`openers()` / `warn_throttle()` 每次返回**同一个实例**，且 `worker` 侧没有副本。"""
    assert http_mod.openers() is http_mod.openers()
    assert http_mod.warn_throttle() is http_mod.warn_throttle()
    # `worker` 仍在转发这两个入口（与其它搬走的名字同规），但转发的**是同一个对象**——
    # 不再有「改 worker 那边不改变行为」的快照名，所以注入点没有任何歧义。
    assert worker_mod.openers() is http_mod.openers()
    assert worker_mod.warn_throttle() is http_mod.warn_throttle()
    # 懒建只发生一次：`_get_opener()` 与 `openers().get()` 是同一个对象（reset 之前）。
    http_mod.openers().reset()
    assert http_mod._get_opener() is http_mod.openers().get()


def test_http_top_level_has_no_mutable_container_literal_at_all() -> None:
    """`http.py` 顶层**零**可变容器字面量（P0-5 之后比旧判据更严：连节流表也进了对象）。"""
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
    assert mutable == set(), f"http.py 顶层多了可变容器：{sorted(mutable)}"


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
        WireGet("http://hub", "t", "/jobs/j1/payload", timeout=1.0, attempts=1, log=lambda _m: None)
    )
    assert out == b"payload-bytes"


def test_request_seam_for_the_job_face_is_the_job_lifecycle_module(monkeypatch) -> None:
    """档位二：作业面（`post_result`）的**发请求**能力来自 `remote.job_lifecycle` 的 `WIRE` 端口。

    S4 第五步时这一档是 `worker`；第八刀把作业面搬到 `remote/job_lifecycle.py`；2026-11-28
    起它不再直调 `remote.http._request`，而是读本模块的 `WIRE`（缺省由 `PortPair` 用同一对原语
    拼出来）。判据不变：「patch 作业面的端口，同时把 `remote.http._request` 换成炸弹」必须仍然
    成功 —— 打偏的话这里看到的会是 `打偏` 而不是 200。
    """
    monkeypatch.setattr(
        http_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(jl_mod, "WIRE", PortPair(lambda spec: (200, b"{}")))
    rc = jl_mod.post_result(PostResultSpec("http://hub", "t", "jid1", {"job_id": "jid1"}, log=lambda _m: None))
    assert rc == 200


def test_worker_module_has_no_request_call_site_any_more() -> None:
    """★ 警报（第八刀）：`worker.py` 里已**没有** `_request` 的调用点。

    它仍转发 `_request`（`tests/remote/test_loopback_http_no_proxy.py` 直接 import 这个名字），
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
