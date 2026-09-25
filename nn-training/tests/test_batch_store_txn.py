"""test_batch_store_txn — B 层「台账唯一所有者」的契约守卫（S26/B2，plan §5.5.5）。

2026-09-25：`rl/batch_eval.py` 拆分的第二步 B2 —— 台账 / 请求面收进新模块
`rl/batch_store.py` 的 `BatchStore`（**一次具名转移 = 一次事务 = 一次落盘**）。

本文件钉两类东西，两者都不是「搬得对」：

  ① **结构契约**：定义唯一 · 门面对象级恒等 · 私有 seam 不再转发 · store **不缓存台账** ·
     `status` 赋值点闭集 = 五个具名转移 · 落盘唯一写点 = `_publish` · 公开方法面 = 设计面。
  ② **★ 功能性**（设计 §5.5.5 的五条 + 一条锁语义）：`of==0` 不判 done · `aborted` 只回填
     不复活 · `requeue` 不复活 · **改了必落盘 / 没改不落盘** · 落盘原子（转移级）·
     锁忙时整轮不消费也**不标记**（否则请求会被标成已消费却没建批 = 丢请求）。

放在 `tests/` 而不是 `e2e/`：纯逻辑、无头、无网络、无真进程。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.batch_eval as be
import rl.batch_store as bs

RL = ROOT / "rl"
STORE_PATH = RL / "batch_store.py"
EVAL_PATH = RL / "batch_eval.py"
STORE_SRC = STORE_PATH.read_text(encoding="utf-8")
EVAL_SRC = EVAL_PATH.read_text(encoding="utf-8")

#: 搬进 store 的公开名（**必须**经 `rl.batch_eval` 再导出 ⇒ 调用点与测试一行不改）。
REEXPORTED = (
    "DEFAULT_DATA_ROOT",
    "REQUESTS_DONE_FILE",
    "REQUESTS_FILE",
    "BatchStore",
    "claim_pending",
    "consume_requests",
    "data_root",
    "mark_requests_done",
    "mark_unit_done",
    "read_batches",
    "read_done_req_ids",
    "read_requests",
    "utc_now_iso",
    "write_batches",
)

#: 私有 seam：**不**再经门面转发（它们是私有面，调用点已改到 store 上）。
PRIVATE_SEAMS = ("_persist_of", "_requeue", "_reopen_for_resume")

#: 设计里的公开方法面（多一个/少一个都要显式改本表 —— 这是「写面闭集」的机器形式）。
TRANSITIONS = {
    "enqueue",
    "enqueue_verdict",
    "abort",
    "claim",
    "set_units_of",
    "mark_unit_done",
    "requeue",
    "reopen_for_resume",
}
READ_FACE = {"all", "get", "done_units"}
REQUEST_FACE = {
    "pending_requests",
    "done_request_ids",
    "mark_requests_done",
    "consume_requests",
}

#: 允许给 `status` 赋值的五个具名转移（其余谁都别碰 —— 这就是 B2 的全部理由）。
STATUS_WRITERS = {
    "BatchStore.abort",
    "BatchStore.claim",
    "BatchStore.mark_unit_done",
    "BatchStore.requeue",
    "BatchStore.reopen_for_resume",
}


# ───────────────────────────────── AST 小工具 ─────────────────────────────────


def _top_bound(src: str) -> set[str]:
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def _subscript_store_owners(src: str, key: str) -> list[str]:
    """`x["key"] = …` 的归属者（`类.方法` / 顶层函数名）。"""
    out: list[str] = []

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, f"{owner}.{child.name}" if owner else child.name)
                continue
            targets: list[ast.expr] = []
            if isinstance(child, ast.Assign):
                targets = list(child.targets)
            elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                targets = [child.target]
            for t in targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == key
                ):
                    out.append(owner or "<module>")
            walk(child, owner)

    walk(ast.parse(src), "")
    return sorted(out)


def _public_methods(src: str, cls: str) -> set[str]:
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            }
    raise AssertionError(f"{cls} 不在 {STORE_PATH.name}")


def _non_test_py() -> list[Path]:
    skip = {".venv", "tmp", "tests", "e2e", "__pycache__", "ipynb", "weights"}
    return [p for p in sorted(ROOT.rglob("*.py")) if not (set(p.relative_to(ROOT).parts) & skip)]


def _wreq(root: Path, *reqs: dict) -> None:
    with open(root / bs.REQUESTS_FILE, "a", encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")


def _ledger(tmp_path: Path) -> list[dict]:
    """从**盘上**重读（别信内存里的对象 —— 那正是本模块要消灭的东西）。"""
    return bs.read_batches(tmp_path)


def _seed(tmp_path: Path, *rows: dict) -> None:
    bs.write_batches(tmp_path, list(rows))


def _pub_counter(monkeypatch) -> dict[str, int]:
    """数一数「发布了几次」（`_publish` 是唯一写点 ⇒ 它被调几次 = 落盘几次）。"""
    holder = {"n": 0}
    orig = bs.BatchStore._publish

    def counting(self: bs.BatchStore, batches: list[dict]) -> None:
        holder["n"] += 1
        orig(self, batches)

    monkeypatch.setattr(bs.BatchStore, "_publish", counting)
    return holder


# ───────────────────────── ① 结构契约：定义唯一 / 门面恒等 ─────────────────────────


def test_store_members_are_defined_only_in_the_new_home() -> None:
    store, old = _top_bound(STORE_SRC), _top_bound(EVAL_SRC)
    for name in ("BatchStore", *[n for n in REEXPORTED if not n.isupper()], "read_requests"):
        assert name in store, f"{name} 不在 rl/batch_store.py"
        assert name not in old, f"{name} 仍定义在 rl/batch_eval.py"


def test_facade_reexports_are_the_same_objects() -> None:
    """门面再导出必须**对象级恒等**（不是同名副本）—— 这是零迁移的全部依据。"""
    for name in REEXPORTED:
        assert hasattr(be, name), name
        assert getattr(be, name) is getattr(bs, name), name


def test_private_seams_are_not_forwarded_by_the_facade() -> None:
    """三个私有 seam 不再转发；store 上只剩它们的具名转移形态。"""
    for name in PRIVATE_SEAMS:
        assert not hasattr(be, name), name
    assert not hasattr(bs, "_persist_of")  # 并入 set_units_of，永久消失
    assert hasattr(bs, "write_batches")  # 播种缝仍在（测试用它铺台账）
    for name in ("requeue", "reopen_for_resume"):
        assert hasattr(bs.BatchStore, name), name


def test_public_method_face_is_exactly_the_designed_one() -> None:
    """写面 = 八个具名转移 · 读面 = 三个 · 请求面 = 四个；多一个都要显式改本表。"""
    got = _public_methods(STORE_SRC, "BatchStore")
    assert got == TRANSITIONS | READ_FACE | REQUEST_FACE, sorted(got ^ (TRANSITIONS | READ_FACE | REQUEST_FACE))


def test_status_assignments_live_only_in_the_named_transitions() -> None:
    """全仓非测试 python 里，`x["status"] = …` 的赋值点闭集 = 五个具名转移。

    （S20 的 `_self_assigns` 教训：状态机的「谁都能改」用语法量钉，别用文本搜。）
    """
    hits: dict[str, list[str]] = {}
    for p in _non_test_py():
        owners = _subscript_store_owners(p.read_text(encoding="utf-8"), "status")
        if owners:
            hits[str(p.relative_to(ROOT))] = owners
    assert hits == {"rl/batch_store.py": sorted(STATUS_WRITERS)}, hits


def test_ledger_publish_is_the_single_writer() -> None:
    """`_publish` 是 `batches.jsonl` 的唯一写点；它的调用者闭集 = 转移 + 播种缝。"""
    callers: dict[str, list[str]] = {}
    for p in _non_test_py():
        src = p.read_text(encoding="utf-8")
        if "_publish(" not in src:
            continue
        owners: list[str] = []

        def walk(node: ast.AST, owner: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, f"{owner}.{child.name}" if owner else child.name)
                    continue
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "_publish"
                ):
                    owners.append(owner or "<module>")
                walk(child, owner)

        walk(ast.parse(src), "")
        if owners:
            callers[str(p.relative_to(ROOT))] = sorted(set(owners))
    allowed = [*sorted(TRANSITIONS), "write_batches"]
    assert set(callers) == {"rl/batch_store.py"}, callers
    assert set(callers["rl/batch_store.py"]) <= {"BatchStore." + t for t in allowed} | {"write_batches"}, (
        callers
    )
    # 台账文件的字面写手：只有 `_publish` 那一段出现 `os.replace` + `BATCHES_FILE`
    pub = ast.parse(STORE_SRC)
    for node in ast.walk(pub):
        if isinstance(node, ast.FunctionDef) and node.name == "_publish":
            body = ast.unparse(node)
            assert "os.replace" in body and "BATCHES_FILE" in body
            break
    else:
        raise AssertionError("_publish 不见了")


# ───────────────────────── ② store 只持有 root（不缓存台账）─────────────────────────


def test_store_holds_only_the_root_and_never_caches_the_ledger(tmp_path: Path) -> None:
    """★ 「不缓存」是跨进程语义的前提：另一个 trainer 进程刚写的批必须立刻可见。"""
    store = bs.BatchStore(tmp_path)
    assert store.all() == []
    # 进程外的写者（直接改盘）⇒ store 必须看得见
    bs.write_batches(tmp_path, [{"batch_id": "b-out", "status": "pending", "units": {"of": 1, "done": []}}])
    assert [b["batch_id"] for b in store.all()] == ["b-out"]
    assert store.get("b-out") is not None
    # 实例上除 root 外没有别的状态（防「顺手加个内存缓存」）
    assert set(vars(store)) == {"root"}


def test_default_root_follows_evalboard_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    assert bs.data_root() == tmp_path
    assert bs.BatchStore().root == tmp_path
    assert bs.DEFAULT_DATA_ROOT == bs.REPO_ROOT / "dashboard" / "data" / "evalboard"


# ───────────────────────── ③ ★ 功能性：两条特例语义 ─────────────────────────


def test_claim_resumes_an_orphaned_running_batch(tmp_path: Path) -> None:
    """孤儿批续跑：进程重启 / yield 后留下的 `running` ∧ 仍有未完成 unit ⇒ 必须能被重新认领。

    （反探针实测：删掉这个分支全绿 —— 既有 `test_queue_claim_done_cycle` 里那条「running +
    incomplete 也可被 claim」走的其实是 `pending` 分支（`mark_unit_done` 已把它改回 pending），
    所以这条路径此前**无人覆盖**。）
    """
    _seed(tmp_path, {"batch_id": "b-orphan", "status": "running", "units": {"of": 3, "done": [0]}})
    store = bs.BatchStore(tmp_path)
    got = store.claim()
    assert got is not None and got["batch_id"] == "b-orphan"
    assert _ledger(tmp_path)[0]["status"] == "running"
    assert len(_ledger(tmp_path)) == 1
    # 已跑完的 / 已中止的都不再被认领
    _seed(
        tmp_path,
        {"batch_id": "b-orphan", "status": "running", "units": {"of": 3, "done": [0]}},
        {"batch_id": "b-done", "status": "done", "units": {"of": 1, "done": [0]}},
        {"batch_id": "b-ab", "status": "aborted", "units": {"of": 1, "done": []}},
    )
    store.claim()
    assert [b["status"] for b in _ledger(tmp_path)] == ["running", "done", "aborted"]
    _seed(tmp_path, {"batch_id": "b-done", "status": "done", "units": {"of": 1, "done": [0]}})
    assert store.claim() is None
    # `of == 0`（未定型）**不算**「未完成」：认领后到 `set_units_of` 之间就停在这个窗口，
    # 若判它可领，另一个进程会在原主派发前把整批抢走（双派）。代价是「崩在窗口里」的批
    # 只能等 `abort` —— 这是**旧实现逐字相同**的行为（差分探针已对账），本刀不改。
    _seed(tmp_path, {"batch_id": "b-of0", "status": "running", "units": {"of": 0, "done": []}})
    assert store.claim() is None


def test_mark_unit_done_does_not_finalize_when_of_is_unset(tmp_path: Path) -> None:
    """★ `units.of == 0`（未定型）⇒ 不判 done，回 pending 等下窗续跑。

    判决批先建（`of=0`）、`plan_verdict_units` 展平后才 `set_units_of` —— 这中间结算的
    unit 若把批标成 done，整批剩余 unit 就永远跑不到了。
    """
    _seed(tmp_path, {"batch_id": "b1", "status": "running", "units": {"of": 0, "done": []}})
    store = bs.BatchStore(tmp_path)
    store.mark_unit_done("b1", 0, {"n1": 1})
    b = _ledger(tmp_path)[0]
    assert b["status"] == "pending", "of==0 不得判 done"
    assert b["units"]["done"] == [0]
    # 定型后同样的结算才允许收批
    store.set_units_of("b1", 1)
    store.mark_unit_done("b1", 0, {"n1": 1})
    assert _ledger(tmp_path)[0]["status"] == "done"


def test_aborted_batch_only_backfills_node_dist(tmp_path: Path) -> None:
    """★ `aborted` 批的在途 unit 只回填 `node_dist`，不复活、不追加 done。"""
    _seed(tmp_path, {"batch_id": "b1", "status": "aborted", "units": {"of": 2, "done": [0]}})
    bs.BatchStore(tmp_path).mark_unit_done("b1", 1, {"local": 100})
    b = _ledger(tmp_path)[0]
    assert b["status"] == "aborted"
    assert b["units"]["done"] == [0]
    assert b["node_dist"] == {"local": 100}


def test_requeue_never_revives_aborted(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"batch_id": "b-ab", "status": "aborted", "units": {"of": 1, "done": []}},
        {"batch_id": "b-run", "status": "running", "units": {"of": 2, "done": [0]}},
    )
    store = bs.BatchStore(tmp_path)
    store.requeue("b-ab")
    store.requeue("b-run")
    st = {b["batch_id"]: b["status"] for b in _ledger(tmp_path)}
    assert st == {"b-ab": "aborted", "b-run": "pending"}
    # 不存在的批：什么都不做（也不落盘）
    store.requeue("nope")
    assert len(_ledger(tmp_path)) == 2


def test_reopen_for_resume_only_touches_running(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"batch_id": "b1", "status": "running", "units": {"of": 2, "done": [0]}},
        {"batch_id": "b2", "status": "pending", "units": {"of": 2, "done": []}},
    )
    store = bs.BatchStore(tmp_path)
    store.reopen_for_resume("b1")
    store.reopen_for_resume("b2")
    rows = {b["batch_id"]: b for b in _ledger(tmp_path)}
    assert rows["b1"]["status"] == "pending" and rows["b1"]["units"]["done"] == [0]
    assert rows["b2"]["status"] == "pending"


# ───────────────────── ④ ★ 功能性：改了必落盘 / 没改不落盘 ─────────────────────


def test_transitions_publish_only_when_something_changed(tmp_path: Path, monkeypatch) -> None:
    """dirty-tracking 的两个方向都要钉：**该落的必须落**（否则重启后少一批）、
    **没改的不许落**（否则每次 idle 都整文件重写一遍 —— 六种旧策略里有两处就是这样）。"""
    _seed(
        tmp_path,
        {
            "batch_id": "b1",
            "status": "pending",
            "units": {"of": 3, "done": []},
            "node_dist": {},
        },
    )
    store = bs.BatchStore(tmp_path)
    n = _pub_counter(monkeypatch)

    store.set_units_of("b1", 3)  # 已经是 3 ⇒ 不改
    assert n["n"] == 0
    store.set_units_of("b1", 4)  # 改了 ⇒ 必须落盘
    assert n["n"] == 1 and _ledger(tmp_path)[0]["units"]["of"] == 4

    claimed = store.claim()  # pending → running
    assert claimed is not None and n["n"] == 2
    assert _ledger(tmp_path)[0]["status"] == "running"
    store.claim()  # 已 running 且未完成 ⇒ 续跑，状态没变 ⇒ 不落盘
    assert n["n"] == 2

    store.mark_unit_done("b1", 0, {"n1": 1})  # done 变 + node_dist 变 ⇒ 落盘
    assert n["n"] == 3
    store.mark_unit_done("b1", 0, {"n1": 1})  # 重复同一 unit、同一 dist ⇒ 不落盘
    assert n["n"] == 3
    assert _ledger(tmp_path)[0]["units"]["done"] == [0]
    store.mark_unit_done("b1", 0, {"n1": 9})  # only node_dist 变 ⇒ 仍必须落盘
    assert n["n"] == 4 and _ledger(tmp_path)[0]["node_dist"] == {"n1": 9}

    assert store.abort("nope") is False  # 没命中 ⇒ 不落盘
    assert n["n"] == 4
    assert store.abort("b1") is True
    assert n["n"] == 5 and _ledger(tmp_path)[0]["status"] == "aborted"
    store.requeue("b1")  # aborted 不复活 ⇒ 不落盘
    assert n["n"] == 5
    store.reopen_for_resume("b1")  # 不是 running ⇒ 不落盘
    assert n["n"] == 5
    assert _ledger(tmp_path)[0]["status"] == "aborted"


def test_publish_is_atomic_for_named_transitions(tmp_path: Path, monkeypatch) -> None:
    """落盘原子（转移级）：live 文件**从不被以 'w' 打开** ⇒ 读者读不到中间态。

    与 `test_batch_eval.py::test_batch_ledger_publish_is_atomic` 同一套确定性手法 ——
    那里打的是播种缝 `write_batches`，这里打的是转移本身（生产路径）。
    """
    live = tmp_path / bs.BATCHES_FILE
    _seed(tmp_path, {"batch_id": "b1", "status": "pending", "units": {"of": 1, "done": []}})
    seen: list[int] = []
    real_open = Path.open

    def hooked_open(self: Path, mode: Any = "r", *a: Any, **kw: Any) -> Any:
        f = real_open(self, mode, *a, **kw)
        if self == live and "w" in mode:
            seen.append(len(bs.read_batches(tmp_path)))
        return f

    monkeypatch.setattr(Path, "open", hooked_open)
    batch = bs.BatchStore(tmp_path).claim()
    assert batch is not None
    bs.BatchStore(tmp_path).mark_unit_done("b1", 0, {"n1": 2})

    assert seen == [], f"转移期间 live 文件被原地截断，读者读到 {seen} 批"
    assert _ledger(tmp_path)[0]["status"] == "done"


# ─────────────── ⑤ ★ 功能性：锁忙 ⇒ 整轮不消费也**不标记**（不丢请求）───────────────


def _hold_lock(root: Path) -> Path:
    """模拟「另一个活进程持锁」：写本进程 PID（`_pid_alive` 为真）。"""
    lp = root / "claim.lock"
    lp.write_text(f"{os.getpid()}|{sys.executable}|0", encoding="utf-8")
    return lp


def test_lock_busy_consumes_nothing_and_marks_nothing(tmp_path: Path, monkeypatch) -> None:
    """**本刀最想留下的回归守卫**：锁忙时「请求」绝不能被标成已消费。

    如果把锁只放在单个具名转移上，`enqueue` 拿不到锁会返回 None —— 与「去重跳过」
    不可区分 ⇒ `consume_requests` 会把请求标成已消费却**没建批**（丢请求、无日志）。
    所以整轮消费必须一把锁（`_tx`），锁忙时整轮跳过、请求留给下一 idle 窗。
    """
    monkeypatch.setattr(bs, "_CLAIM_WAIT_SEC", 0.2)  # 不真等 2s
    lp = _hold_lock(tmp_path)
    _wreq(
        tmp_path,
        {
            "req_id": "q1",
            "kind": "enqueue",
            "ts": "t",
            "course": "c",
            "rung_from": "r",
            "ckpt": "k",
        },
    )
    counts = bs.consume_requests(tmp_path)
    assert counts == {"consumed": 0, "enqueued": 0, "aborted": 0, "skipped": 0}
    assert _ledger(tmp_path) == []
    assert bs.read_done_req_ids(tmp_path) == set(), "锁忙不得标记请求已消费"
    assert bs.claim_pending(tmp_path) is None
    assert lp.exists(), "未取得锁不得删除持有人文件"

    # 放掉锁之后同一份请求照常物化（幂等重放）
    lp.unlink()
    assert bs.consume_requests(tmp_path)["enqueued"] == 1
    assert len(_ledger(tmp_path)) == 1


def test_claim_consumes_requests_inside_the_same_lock(tmp_path: Path) -> None:
    """`claim()` 内嵌 `consume_requests()` 走可重入 `_tx`（拿不到锁时整轮跳过 ⇒ 这里能跑通）。"""
    _wreq(
        tmp_path,
        {
            "req_id": "q1",
            "kind": "enqueue",
            "ts": "t",
            "course": "c4-margin",
            "rung_from": "c4l1",
            "ckpt": "w/a.json",
        },
    )
    batch = bs.claim_pending(tmp_path)
    assert batch is not None and batch["status"] == "running"
    assert batch["course"] == "c4-margin"
    assert not (tmp_path / "claim.lock").exists()


# ───────────────────────── ⑥ 功能性：enqueue 的幂等 / 白名单 ─────────────────────────


def test_enqueue_is_idempotent_and_spec_whitelisted(tmp_path: Path) -> None:
    store = bs.BatchStore(tmp_path)
    b1 = store.enqueue(course="c", rung_from="r", ckpt="k", rogue=1, trigger="auto-ladder")
    assert b1 is not None
    assert b1["status"] == "pending" and b1["units"] == {"of": 2, "done": []}
    assert b1["trigger"] == "auto-ladder"
    assert "rogue" not in b1, "未知请求字段不得进台账"
    assert b1["created_ts"].endswith("Z") and len(b1["created_ts"]) == 24
    # 同 key pending 已存在 ⇒ None（不重复建批）
    assert store.enqueue(course="c", rung_from="r", ckpt="k") is None
    assert len(_ledger(tmp_path)) == 1
    # 物化判据（「已有一份更晚的批」）—— 必须先把 pending 去重让开，否则本条永远命中上一条
    assert store.claim() is not None
    assert store.enqueue(course="c", rung_from="r", ckpt="k", req_ts="2000-01-01T00:00:00.000Z") is None
    # 请求时间在未来 ⇒ 未物化 ⇒ 允许建（旧批已跑完/被领走时这是唯一的续跑路径）
    later = store.enqueue(course="c", rung_from="r", ckpt="k", req_ts="2999-01-01T00:00:00.000Z")
    assert later is not None and len(_ledger(tmp_path)) == 2


def test_enqueue_verdict_key_is_label_sequence_sensitive(tmp_path: Path) -> None:
    store = bs.BatchStore(tmp_path)
    ck = [{"label": "bc", "path": "w/bc.json"}]
    assert store.enqueue_verdict(corpus="v1", ckpts=ck) is not None
    assert store.enqueue_verdict(corpus="v1", ckpts=ck) is None  # 同键
    assert store.enqueue_verdict(corpus="v1", ckpts=[{"path": "w/bc.json"}, {"path": "w/x.json"}]) is not None
    # 键对 ckpt **顺序**敏感（同批多权重的配对语义）：换序 ⇒ 另一份批
    a, b = {"label": "a", "path": "w/a.json"}, {"label": "b", "path": "w/b.json"}
    assert store.enqueue_verdict(corpus="v2", ckpts=[a, b]) is not None
    assert store.enqueue_verdict(corpus="v2", ckpts=[a, b]) is None  # 同序 ⇒ 去重
    assert store.enqueue_verdict(corpus="v2", ckpts=[b, a]) is not None  # 换序 ⇒ 不重
    rows = _ledger(tmp_path)
    assert len(rows) == 4
    assert {r["units"]["of"] for r in rows} == {0}, "判决批建批时 of 恒 0（未定型）"
    assert rows[1]["kind"] == "verdict" and rows[1]["trigger"] == "verdict"


def test_consume_requests_counts_and_idempotence(tmp_path: Path) -> None:
    """请求翻译层：坏请求/未知 kind 被标记消费但不建批；ladder_* 留给 console ticker。"""
    _wreq(
        tmp_path,
        {"req_id": "q-bad", "kind": "enqueue", "ts": "t", "course": "c"},
        {"req_id": "q-unk", "kind": "whatever", "ts": "t"},
        {"req_id": "q-l1", "kind": "ladder_start", "course": "c"},
        {"req_id": "q-ok", "kind": "enqueue", "ts": "t", "course": "c", "rung_from": "r", "ckpt": "k"},
    )
    c = bs.consume_requests(tmp_path)
    assert c == {"consumed": 3, "enqueued": 1, "aborted": 0, "skipped": 2}
    assert bs.read_done_req_ids(tmp_path) == {"q-bad", "q-unk", "q-ok"}
    assert len(_ledger(tmp_path)) == 1
    assert bs.consume_requests(tmp_path) == {"consumed": 0, "enqueued": 0, "aborted": 0, "skipped": 0}


@pytest.mark.parametrize("name", ["data_root", "utc_now_iso"])
def test_dir_helpers_run_from_the_new_home(name: str) -> None:
    fn = getattr(bs, name)
    if name == "utc_now_iso":
        s = fn()
        assert len(s) == 24 and s.endswith("Z") and s[10] == "T" and s[19] == "."
    else:
        assert fn().name == "evalboard"
