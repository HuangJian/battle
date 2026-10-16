"""test_admin_mem.py — `GET /admin/mem`（2026-10-17，plan/hub-server-memory-slim L3）。

**为什么要有它**：hub 连跑 35h 后 RSS 停在 6.2GB 那次，「内存里躺着什么」**没有任何读数**——
只能靠任务管理器 + 猜（先怀疑线程泄漏，实测线程 64 正常；再怀疑账本，实测 81KB）。
`/admin/mem` 就是把这个猜的过程变成一次 GET。

本文件钉三件事：

1. `rss_bytes()` 在本平台**真的读得出正数**（读不到返回 0 是它的降级，但不能恒为 0）；
2. `mem_readout()` 的形状固定，且**随账本行数走**（不是常量——常量读数等于没读）；
3. 路由挂上了（`/admin/mem` ⇒ `_admin_mem`），且**鉴权**与其它 admin 端点同源。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.admin import rss_bytes
from hub.http_face import HubHandler
from hub.queue import _HubQueue
from hub.store import _JobStore

HTTP_FACE = ROOT / "hub" / "http_face.py"


def _hub(tmp_path: Path, courses: list[str]) -> tuple[_HubQueue, dict[str, _JobStore]]:
    stores = {c: _JobStore(tmp_path / c / "remote-jobs", tmp_path / c / "training_log.jsonl") for c in courses}
    return _HubQueue(stores, order=list(stores)), stores


def test_rss_bytes_is_positive_or_zero_and_never_raises() -> None:
    """读数要么 > 0（本平台支持），要么 0（不支持）；**绝不抛**（观测面不许打挂端点）。"""
    v = rss_bytes()
    assert isinstance(v, int) and v >= 0
    # 本仓门禁跑在 Windows / Linux 上，两侧实现都有（Windows：psapi；POSIX：/proc/self/statm）
    assert v > 0, "本平台应能读出 RSS（读不到说明平台分支坏了，读数会恒为 0 → 等于没读）"


def test_mem_readout_shape_is_fixed(tmp_path: Path) -> None:
    """形状固定：控制台/RCA 脚本按名取值，缺一个键就该炸而不是静默 None。"""
    hub, _stores = _hub(tmp_path, ["a", "b"])
    out = hub.mem_readout()
    assert set(out) == {
        "courses",
        "ledger_rows",
        "locate_n",
        "leases_n",
        "workers_n",
        "dispatch_n",
        "prefetch_n",
        "offline_disks_n",
    }
    assert out["courses"] == 2
    assert out["ledger_rows"] == {"a": 0, "b": 0}, "没读过的课 = 0 行（不预读）"


def test_mem_readout_tracks_ledger_rows(tmp_path: Path) -> None:
    """★ 读数必须**随真实状态走**：往 a 的账本里写两行 job_pending，`ledger_rows["a"]` 就该是 2。

    这是「读数 vs 常量」的分界：一个恒定的读数比没有更危险（它会被当成证据）。
    """
    hub, stores = _hub(tmp_path, ["a", "b"])
    for i in range(2):
        stores["a"]._append_ledger({"event": "job_pending", "job_id": f"j{i}", "ts": 1.0})
    _ = stores["a"]._read_ledger()  # 触发一次读（H6 缓存随之建起）
    out = hub.mem_readout()
    assert out["ledger_rows"]["a"] == 2, out["ledger_rows"]
    assert out["ledger_rows"]["b"] == 0, "别把别的课的账本也算进来"


def test_admin_mem_route_is_wired_and_auth_gated() -> None:
    """路由表里有 `/admin/mem`，且它调的是 `_admin_mem`（不是撞名）。

    ★ 鉴权：`_admin_mem` 里第一行就是 `_auth_ok()`——与其余 admin 端点同源（不验 token 的
    观测端点等于把进程内存/课程结构对公网公开）。
    """
    tree = ast.parse(HTTP_FACE.read_text(encoding="utf-8"))
    hit = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Constant) and c.value == "/admin/mem" for c in n.comparators)
    ]
    assert hit, "do_GET 里没有 /admin/mem 这一支"
    # 鉴权：用 **AST** 钉「第一条语句就是 `_auth_ok()` 闸」，而不是在源码里找子串
    # （本仓第四次撞上「读源码文本的守卫」——前三次见 `tests/hub/test_hub_entry_split.py`
    # 与 `tests/tools/test_subproc_util.py`；预算由 `test_source_text_assert_budget` 守着）。
    adm = ast.parse((ROOT / "hub" / "admin.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(adm) if isinstance(n, ast.FunctionDef) and n.name == "_admin_mem")
    # 跳过 docstring（`ast.Expr(Constant(str))`）——断言的是**第一条可执行语句**。
    stmts = [
        n
        for n in fn.body
        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]
    assert isinstance(stmts[0], ast.If), f"第一条语句必须是鉴权闸：{ast.unparse(stmts[0])}"
    assert ast.unparse(stmts[0].test) == "not self._auth_ok()", ast.unparse(  # type: ignore[union-attr]
        stmts[0].test  # type: ignore[union-attr]
    )
    assert hasattr(HubHandler, "_admin_mem")
