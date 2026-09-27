"""拆分的**契约守卫**：BC job 回传消费（轮询会话 + 指标/eval/账本写入 + 节奏常量）永住
`rl/bc_ingest.py`（S5 第四刀，2026-09-27）。

`rl/bc_loop.py` **1433 → 1213 行**；两段跨度（轮询/退避常量 + 「账本（事件写入）+ 等待」连续整段）
逐字节搬到 `rl/bc_ingest.py`（291 行）。本文件钉六件事：

1. **定义唯一**——这 11 个名字不许在 `bc_loop.py` 里再实现一遍（否则「搬了一半」）；
2. **反向**——编排面（引擎 / 采集 / 发布 / 训练 / 归档 / 盘上 job）必须**留守** `bc_loop.py`；
3. **无环**——`bc_ingest` 不得 import `rl.bc_loop`（回传消费是底座，反向 import 编排 = 环）；
4. **门面**——`bc_loop` 的那些名字必须是 `bc_ingest` 的转发（不是副本）；
5. **monkeypatch 点没断**——`bc_loop.time`（共享 stdlib 模块对象）与 `bc_loop.wait_bc_round`
   仍是同一对象（现有用例打的就是它们）；
6. **回传语义没变（功能性）**——`BcWait.poll_once` 的 ready/pending/transient 三态分类。

为什么单独成家：它是**读远端回传**的面（HTTP 轮询 + 退避 + 账本写入），与「一轮怎么跑
（采集/发布 → 落位归档）」耦合度低；单课程入口（`wait_bc_round`）与 supervisor 的让位引擎
（`BcLoop.run_one_round` → `BcWait.poll_once`）共用这份会话 ⇒ 必须能被两条驱动路径同时依赖。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.bc_ingest as bc_ingest_mod
import rl.bc_loop as bc_loop_mod

INGEST_FILE = ROOT / "rl" / "bc_ingest.py"
LOOP_FILE = ROOT / "rl" / "bc_loop.py"

#: 本次搬走的**定义**（常量 / 类 / 函数）——只许在 `bc_ingest.py` 里出现。
MOVED_NAMES = {
    "IDLE_WARN_SEC",
    "POLL_SEC",
    "POLL_MAX_SEC",
    "READY",
    "PENDING",
    "TRANSIENT",
    "BcWait",
    "ingest_bc_metrics",
    "ledger_bc_epoch",
    "run_epoch_eval",
    "wait_bc_round",
}

#: `bc_ingest.py` 允许的 import 面（stdlib + 本仓协议/BC 数据面/日志；多一个即红）。
ALLOWED_IMPORTS = {
    "__future__",
    "json",
    "time",
    "dataclasses",
    "pathlib",
    "common.protocol",
    # S5 第五刀（2026-09-27）：HTTP 面下沉 `remote/hub_http.py`；`_request` 的所有者搬了家，
    # 本模块（函数内 import 那个注入点）随之改指。
    "remote.hub_http",
    "rl.bc_config",
    "rl.bc_eval",
    "rl.bc_ledger",
    "rl.log",
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
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _imported_from(path: Path, module: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.update(a.name for a in node.names)
    return out


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_moved_names_are_defined_in_bc_ingest_and_not_redefined_in_bc_loop() -> None:
    assert _defined(INGEST_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(INGEST_FILE))
    leftovers = MOVED_NAMES & _defined(LOOP_FILE)
    assert leftovers == set(), f"bc_loop.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


# ───────────────────────── ② 反向：编排面留守 ─────────────────────────


def test_bc_loop_kept_the_orchestration_surface() -> None:
    """引擎 / 采集 / 发布 / 训练 / 归档 / 盘上 job / CLI 必须留在 `bc_loop.py`（不然这刀搬错了对象）。"""
    defined = _defined(LOOP_FILE)
    for name in (
        "BcLoop",
        "BcRuntime",
        "train_local_bc",
        "collect_corpus",
        "publish_bc_job",
        "archive_round",
        "find_round_job",
        "finish_all_rounds",
        "resolve_bc_runtime",
        "bc_argparser",
    ):
        assert name in defined, f"bc_loop.py 丢了编排面 {name}"


# ─────────────────────── ③ 依赖方向：允许面闭集，无环 ───────────────────────


def test_bc_ingest_imports_stay_within_the_allowed_surface() -> None:
    extra = sorted(_imports(INGEST_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"rl/bc_ingest.py 引入了允许面之外的依赖：{extra}"


def test_bc_ingest_never_imports_bc_loop() -> None:
    """★ 本刀的意义：回传消费是**底座**，反向 import 编排立刻成环。"""
    back = sorted(m for m in _imports(INGEST_FILE) if m.startswith("rl.bc_loop"))
    assert back == [], f"rl/bc_ingest.py 反向 import 了编排模块：{back}"


# ───────────────────────── ④ 门面 ─────────────────────────


def test_bc_loop_facade_forwards_the_same_objects() -> None:
    for name in sorted(MOVED_NAMES):
        assert hasattr(bc_loop_mod, name), f"rl.bc_loop 丢了门面 {name}"
        assert getattr(bc_loop_mod, name) is getattr(bc_ingest_mod, name), (
            f"rl.bc_loop.{name} 不是 rl.bc_ingest.{name}（转发成了副本）"
        )


def test_facade_uses_exactly_the_declared_names() -> None:
    """门面是 `X as X` 且**只**列这 11 个（多列一个 = 悄悄扩大了搬家面）。"""
    assert _imported_from(LOOP_FILE, "rl.bc_ingest") == MOVED_NAMES


# ─────────────────── ⑤ monkeypatch 点没断（既有用例打的） ───────────────────


def test_monkeypatch_points_survive_the_move() -> None:
    """`bc_loop.time.sleep`（e2e / test_bc_course 打它）与 `bc_loop.wait_bc_round` 仍可用。

    `bc_loop.time` 是**共享的 stdlib 模块对象** ⇒ patch 它等于 patch `bc_ingest.time`；
    若搬走后 `bc_loop.time` 消失或不指向同一对象，那些用例会静默变真等待/失效。
    """
    import time as time_mod

    assert bc_loop_mod.time is time_mod
    assert bc_ingest_mod.time is time_mod
    assert bc_loop_mod.wait_bc_round is bc_ingest_mod.wait_bc_round
    # 编排面仍能构造 BcWait（BcLoop.__init__ 的 `_waits: dict[int, BcWait]` 靠它注解）
    assert bc_loop_mod.BcWait is bc_ingest_mod.BcWait


# ─────────────────────── ⑥ 回传语义没变（功能性） ───────────────────────


def test_poll_once_classifies_ready_pending_transient(tmp_path: Path, monkeypatch) -> None:
    """三态分类：200+dict → ready；404 → pending；5xx/网络错 → transient（且翻倍退避）。

    分类住在这个抛出点上是**唯一**能用的判据——「还没好」与「永远好不了」必须分开，
    混在一起就是一个静默挂死的等待环。
    """
    from remote import hub_http

    reply: dict[str, object] = {"status": 200, "body": b"{}"}

    def fake(_base, _token, _path, timeout=0.0):
        return reply["status"], reply["body"]

    from rl.bc_config import load_bc_course

    # S5 第五刀：`_request` 的宿主是 `remote.hub_http`（patch `hub_client` 的转发名不再有效）。
    monkeypatch.setattr(hub_http, "_request", fake)
    jsonl = tmp_path / "training_log.jsonl"
    course = load_bc_course("bc-c4-v3")  # pending 路径要进 ingest_bc_metrics → 读 course.eval
    wait = bc_ingest_mod.BcWait(jid="j1")

    reply["status"], reply["body"] = 200, json.dumps({"job_id": "j1"}).encode("utf-8")
    assert wait.poll_once(
        hub_url="http://hub", token="t", jsonl_path=jsonl, it=1, course=course, cfg=None,
        logger=lambda _m: None,
    )[0] == bc_ingest_mod.READY

    reply["status"], reply["body"] = 404, b"{}"
    assert wait.poll_once(
        hub_url="http://hub", token="t", jsonl_path=jsonl, it=1, course=course, cfg=None,
        logger=lambda _m: None,
    )[0] == bc_ingest_mod.PENDING
    assert wait.err_streak == 0  # 404 = 正常排队，清连击

    reply["status"] = 503
    assert wait.poll_once(
        hub_url="http://hub", token="t", jsonl_path=jsonl, it=1, course=course, cfg=None,
        logger=lambda _m: None,
    )[0] == bc_ingest_mod.TRANSIENT
    assert wait.err_streak == 1
    assert wait.backoff_sec() == bc_ingest_mod.POLL_SEC


def test_backoff_is_exponential_and_capped() -> None:
    wait = bc_ingest_mod.BcWait(jid="j")
    wait.err_streak = 1
    assert wait.backoff_sec() == bc_ingest_mod.POLL_SEC
    wait.err_streak = 3
    assert wait.backoff_sec() == min(bc_ingest_mod.POLL_SEC * 4, bc_ingest_mod.POLL_MAX_SEC)
    wait.err_streak = 99
    assert wait.backoff_sec() == bc_ingest_mod.POLL_MAX_SEC
