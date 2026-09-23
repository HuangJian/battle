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
   而直调 `_request` 的**宿主**函数（`post_result` 等）仍解析在 `worker` 命名空间 ⇒ patch
   `remote.worker` 照旧有效。两个方向各一条断言，防止「patch 打偏而测试全绿」。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.http as http_mod
import remote.worker as worker_mod

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


def _modules_imported(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def test_moved_names_are_defined_in_http_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `http.py` 里实现。"""
    assert _defined(HTTP_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(HTTP_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_http_does_not_import_worker() -> None:
    """无环：`http.py` 不得反向 import `worker.py`；也不得碰 `rl`（L2 传输层）。"""
    imported = _modules_imported(HTTP_FILE)
    assert "remote.worker" not in imported, "remote/http.py 反向 import remote.worker ⇒ 环"
    assert "rl" not in {m.split(".")[0] for m in imported}, "http 是 L2 传输层，不得碰 rl"


def test_worker_forwards_every_moved_name() -> None:
    """`worker` 的命名空间里每个搬走的名字都在，且函数/常量是**同一个对象**。"""
    for name in MOVED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(http_mod, name), (
            f"remote.worker.{name} 不是 remote.http.{name}（转发成了副本）"
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


def test_request_seam_for_host_callers_is_still_the_worker_module(monkeypatch) -> None:
    """档位二：直调 `_request` 的**宿主**函数（`post_result`）解析在 **worker** 命名空间。

    所以「patch `remote.worker._request`，同时把 `remote.http._request` 换成炸弹」
    必须仍然成功——这证明我们只迁移了该迁移的那一部分 seam。
    """
    monkeypatch.setattr(
        http_mod, "_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(worker_mod, "_request", lambda *a, **k: (200, b"{}"))
    rc = worker_mod.post_result("http://hub", "t", "jid1", {"job_id": "jid1"}, log=lambda _m: None)
    assert rc == 200


def test_body_progress_threshold_is_read_from_the_http_module(monkeypatch) -> None:
    """`BODY_PROGRESS_MIN_SEC` 的注入点 = `remote.http`（`_read_body` 读的是本模块全局）。"""

    class _Resp:
        headers = {"Content-Length": "20"}

        def __init__(self) -> None:
            self._chunks = [b"a" * 10, b"b" * 10]

        def read(self, _n: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

    seen: list[tuple[int, int, float]] = []
    monkeypatch.setattr(worker_mod, "BODY_PROGRESS_MIN_SEC", 1e9)  # 打偏：不该生效
    monkeypatch.setattr(http_mod, "BODY_PROGRESS_MIN_SEC", 0.0)
    out = worker_mod._read_body(
        _Resp(), idle_timeout=45.0, total_timeout=None, progress=lambda g, t, e: seen.append((g, t, e))
    )
    assert out == b"a" * 10 + b"b" * 10
    assert [s[0] for s in seen] == [10, 20]
