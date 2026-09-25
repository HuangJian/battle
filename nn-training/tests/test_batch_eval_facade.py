"""test_batch_eval_facade — `rl/batch_eval.py` 作为**门面**的契约（S28/B4 收尾）。

B1–B3（plan §5.5.4）把规划面 / 台账面 / 执行面依次搬出，`batch_eval.py` 退成
「常量 + `maybe_dispatch_batch` + 逐个再导出」。本文件钉两类东西：

  ① **门面契约**（结构）：模块级 `def` 闭集 = {`maybe_dispatch_batch`} · 再导出表**闭集**且
     对象级恒等 · 刻意**不**转发的名字在门面上**响亮** AttributeError · 门面**不改写** store
     交回的台账 dict（每次台账变更都经具名转移 —— 门面不是第二写者）。
  ② **`maybe_dispatch_batch` 的直接功能性**：此前它只被轮内（`loop_lifecycle._evalboard_idle` /
     `loop_dispatch._evalboard_yield`）间接覆盖，`tests/` 里**没有一条**直接调用 —— 而它是门面里
     唯一还活着的逻辑。这里覆盖「认领 → 规划 → 定型 → 起线程」主线 + **三条 requeue 路径**
     （模式不适 / 规划失败 / nn 缺权重）+ 判决批的权重取 unit 而非批次级 `rl_path`。

放在 `tests/` 而不是 `e2e/`：纯逻辑、无头、无网络、无真进程（`dispatch_batch_bg` 被打桩）。
"""

from __future__ import annotations

import ast
import sys
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.batch_eval as be
import rl.batch_plan as bp
import rl.batch_runner as br
import rl.batch_store as bs

EVAL_PATH = ROOT / "rl" / "batch_eval.py"
EVAL_SRC = EVAL_PATH.read_text(encoding="utf-8")
TREE = ast.parse(EVAL_SRC)

#: 门面里允许存在的模块级 `def` —— **闭集**（门面不是放逻辑的地方；加一条都要显式改本表）。
FACADE_DEFS = {"maybe_dispatch_batch"}

#: 门面自己定义的模块级常量（其余名字一律是进口）。
FACADE_CONSTS = {"ONESHOT_EVAL_KIND"}

#: 再导出表：`name → 家`。与源码里 `from <home> import X as X` 的自别名逐条对应。
#: 多一个/少一个都必须显式改本表 —— 这是「门面再导出面」的机器形式（零迁移的全部依据）。
PLAN_EXPORTS = (
    "BATCH_STAGE_BASE",
    "CORPORA_JSON",
    "EVAL_SEED0",
    "KIND_FOR_POLICY",
    "LADDER_JSON",
    "LEVELS_DIR",
    "REGRESSION_EVERY",
    "REPO_ROOT",
    "SEGMENT_LEN",
    "batch_iter_id",
    "corpora_path",
    "corpus_doc",
    "is_transient_error",
    "kind_for_policy",
    "load_corpora",
    "load_ladder",
    "node_gate_reason",
    "plan_units",
    "plan_verdict_units",
    "select_next_unit",
    "units_for_batch",
)
STORE_EXPORTS = (
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
RUNNER_EXPORTS = ("BatchEvalRunner", "dispatch_batch_bg")

HOMES = {"rl.batch_plan": PLAN_EXPORTS, "rl.batch_store": STORE_EXPORTS, "rl.batch_runner": RUNNER_EXPORTS}
HOME_MODULES = {"rl.batch_plan": bp, "rl.batch_store": bs, "rl.batch_runner": br}

#: 刻意**不**经门面转发的名字。它们只被新家的代码读，所以转发只会制造
#: 「名字还在旧家、没人读」的静默空操作（S16/S19 记过两次的坑）⇒ 在门面上必须**响亮**
#: AttributeError。
NOT_FORWARDED = (
    # 执行器独占的五个常量 + 心跳（只被 `rl/batch_runner` 自己读）
    "BUSY_BACKOFF_CAP_SEC",
    "STUCK_GRACE_SEC",
    "RECOVER_PING_SEC",
    "NO_CONSUMER_GRACE_SEC",
    "NODE_RECOVERY_TRIES",
    "_heartbeat",
    # store 的内部文件名常量（消费者用 store 的路径口径，不自己拼）
    "BATCHES_FILE",
    "BATCHES_TMP_FILE",
    # 台账私有 seam（调用点已改到 store 的具名转移上）
    "_persist_of",
    "_requeue",
    "_reopen_for_resume",
)


# ─────────────────────────────── AST 小工具 ───────────────────────────────


def _module_defs() -> set[str]:
    return {
        n.name
        for n in TREE.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _module_assigns() -> set[str]:
    out: set[str] = set()
    for n in TREE.body:
        if isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return out


def _self_alias_exports() -> set[str]:
    """`from <home> import X as X` 的自别名名（本仓再导出的唯一形态）。"""
    out: set[str] = set()
    for n in TREE.body:
        if isinstance(n, ast.ImportFrom):
            out |= {a.asname for a in n.names if a.asname == a.name}
    return out


def _facade_fn() -> ast.FunctionDef:
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == "maybe_dispatch_batch":
            return n
    raise AssertionError("maybe_dispatch_batch 不在门面里")


# ─────────────────────────────── ① 门面契约 ───────────────────────────────


def test_facade_defines_only_the_wiring_hook() -> None:
    """门面里的模块级 `def` 闭集 = {maybe_dispatch_batch}：B1–B3 之后这里不该再有逻辑。"""
    assert _module_defs() == FACADE_DEFS, sorted(_module_defs())
    assert _module_assigns() == FACADE_CONSTS, sorted(_module_assigns())


def test_reexport_table_is_closed() -> None:
    """再导出面是**闭集**：源码里的自别名集合必须与表逐名相等（双向）。"""
    expected = set(PLAN_EXPORTS) | set(STORE_EXPORTS) | set(RUNNER_EXPORTS)
    assert _self_alias_exports() == expected, sorted(_self_alias_exports() ^ expected)


def test_reexports_are_object_identical() -> None:
    """零迁移的全部依据：门面上的名字必须**对象级恒等**（不是同名副本）。"""
    for home, names in HOMES.items():
        mod = HOME_MODULES[home]
        for name in names:
            assert getattr(be, name) is getattr(mod, name), f"{home}.{name}"


def test_not_forwarded_names_are_loudly_absent() -> None:
    """刻意不转发的名字在门面上 AttributeError（而不是「名字还在、没人读」）。"""
    for name in NOT_FORWARDED:
        assert not hasattr(be, name), f"{name} 不该经门面转发"


def test_facade_writes_no_ledger_state() -> None:
    """门面**不**改写 store 交回的台账 dict —— 台账的唯一写者是 `BatchStore` 的具名转移。

    语法量而不是文本搜（S20 的 `_self_assigns` 教训）：`maybe_dispatch_batch` 体里
    **没有任何下标赋值**（`x[...] = …` / `x.setdefault(...)[...] = …`），且三次台账变更
    都是 `store.<具名转移>(…)` 调用。B2 之后这里曾留着一行 `batch.setdefault("units", {})["of"]`
    —— 那是旧实现「就地改台账再落盘」的残留（store 已经不读它，执行器也只读 batch_id/iter）。
    """
    fn = _facade_fn()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                chain = t
                while isinstance(chain, (ast.Call, ast.Attribute, ast.Subscript)):
                    if isinstance(chain, ast.Subscript):
                        raise AssertionError(
                            f"门面里出现下标赋值（第 {t.lineno} 行）——台账变更只能走具名转移"
                        )
                    chain = chain.func if isinstance(chain, ast.Call) else chain.value
    # 正向：三次台账变更都经 store 的具名转移调用。
    called = {
        n.func.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "store"
    }
    assert {"claim", "requeue", "set_units_of"} <= called, sorted(called)


# ──────────────────────── ② maybe_dispatch_batch 直接功能性 ────────────────────────


def _stub_dispatch(monkeypatch):
    """打桩 `dispatch_batch_bg`（在**门面**命名空间里 —— 它读的是自己模块的全局名）。"""
    calls: list[tuple] = []

    def fake(*a, **k):
        calls.append(a)
        return threading.Thread(target=lambda: None, name="fake-unit")

    monkeypatch.setattr(be, "dispatch_batch_bg", fake)
    return calls


def _weights(tmp_path: Path) -> str:
    w = tmp_path / "w.json"
    w.write_text("{}", encoding="utf-8")
    return str(w)


def _call(tmp_path: Path, *, rl_path: str | None = None, mode: str = "per-tick"):
    return be.maybe_dispatch_batch(
        "9.9.9",
        rl_path,
        tmp_path / "traj",
        types.SimpleNamespace(mode=mode),
        {},
        "run1",
        1,
    )


def _ledger(tmp_path: Path) -> list[dict]:
    return bs.read_batches(tmp_path)


def test_dispatch_claims_plans_sizes_and_starts_one_unit(tmp_path, monkeypatch) -> None:
    """主线：认领 → 规划（k%3==2 ⇒ 3 个 unit）→ 回填 `units.of` → 起一条执行线程。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    w = _weights(tmp_path)
    store = bs.BatchStore(tmp_path)
    batch = store.enqueue(course="c4", rung_from="c4l1", ckpt=w, ladder_pos=0, k_seq=2)
    assert batch is not None
    calls = _stub_dispatch(monkeypatch)

    t = _call(tmp_path, rl_path=w)
    assert t is not None and len(calls) == 1
    bun, rl_path, eval_log, _args, _cfg, passed, unit, unit_idx, unit_of, run_id, _epoch, policy, *_ = calls[0]
    assert bun == "9.9.9"
    assert rl_path == w
    assert eval_log == tmp_path / "eval_log.jsonl"
    assert passed["batch_id"] == batch["batch_id"]
    assert unit["rung"] == "c4l1" and unit_idx == 0 and unit_of == 3
    assert run_id == "run1" and policy == "nn"
    # 定型落台账（of 2 → 3）+ 认领已置 running
    (row,) = _ledger(tmp_path)
    assert row["status"] == "running" and row["units"]["of"] == 3


def test_no_pending_batch_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    calls = _stub_dispatch(monkeypatch)
    assert _call(tmp_path, rl_path=_weights(tmp_path)) is None
    assert calls == []


def test_nn_unit_is_requeued_under_a_non_per_tick_mode(tmp_path, monkeypatch) -> None:
    """`--mode goal` 下 nn 单元的 B 层语义不适用 ⇒ 退回队列（不静默跑错）。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    w = _weights(tmp_path)
    bs.BatchStore(tmp_path).enqueue(course="c4", rung_from="c4l1", ckpt=w, ladder_pos=0)
    calls = _stub_dispatch(monkeypatch)
    assert _call(tmp_path, rl_path=w, mode="goal") is None
    assert calls == []
    (row,) = _ledger(tmp_path)
    assert row["status"] == "pending"


def test_plan_failure_requeues(tmp_path, monkeypatch) -> None:
    """规划抛错（ladderPos 越界）⇒ 退回队列，不把批卡在 running。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    w = _weights(tmp_path)
    bs.BatchStore(tmp_path).enqueue(course="c4", rung_from="c4l1", ckpt=w, ladder_pos=999)
    calls = _stub_dispatch(monkeypatch)
    assert _call(tmp_path, rl_path=w) is None
    assert calls == []
    (row,) = _ledger(tmp_path)
    assert row["status"] == "pending"


def test_nn_unit_without_weights_requeues(tmp_path, monkeypatch) -> None:
    """nn 单元没有批次级 `rl_path` 且 unit 也没有 ckpt ⇒ 退回队列。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    bs.BatchStore(tmp_path).enqueue(course="c4", rung_from="c4l1", ckpt="", ladder_pos=0)
    calls = _stub_dispatch(monkeypatch)
    assert _call(tmp_path, rl_path=None) is None
    assert calls == []
    (row,) = _ledger(tmp_path)
    assert row["status"] == "pending"


def test_a_batch_whose_filter_matches_no_unit_is_not_left_silent(tmp_path, monkeypatch) -> None:
    """`only_rungs` 一个都没命中本次 plan（例如 ladder 在入队与派发之间被改过、或只剩
    一个已跑完的 rung）⇒ **记日志 + 退回队列**，与三条兄弟路径（模式不适 / 规划失败 /
    缺权重）同形。

    反例（修前）：静默 `return None`。该批留在 `running`，而 `claim` 的判据是
    `pending ∨ (running ∧ of>0 ∧ len(done)<of)`，`of` 建批时已默认 2 ⇒ **每个 idle 窗都
    被再认领一次，永不发车、不落一行日志、状态永远 running**。
    """
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    w = _weights(tmp_path)
    bs.BatchStore(tmp_path).enqueue(
        course="c4", rung_from="c4l1", ckpt=w, ladder_pos=0, only_rungs=["nope"]
    )
    lines: list[str] = []
    monkeypatch.setattr(be, "log", lines.append)
    calls = _stub_dispatch(monkeypatch)

    assert _call(tmp_path, rl_path=w) is None
    assert calls == []
    (row,) = _ledger(tmp_path)
    assert row["status"] == "pending", "无可跑 unit 的批必须退回队列（否则永远卡在 running）"
    assert any("only_rungs" in s for s in lines), f"必须响亮（实测日志 {lines}）"


def test_verdict_batch_takes_weights_from_the_unit_not_rl_path(tmp_path, monkeypatch) -> None:
    """判决批：权重在 unit 上（多 ckpt 同批）⇒ 批次级 `rl_path` 只对 ladder 批生效。

    `rl_path` 这里**故意给另一份权重**：所以断言「发车用的是 unit 的 ckpt」有区分力
    （把批次级放在前面也能过一次 —— 那是反探针 ⑬ 抓出来的弱断言）。
    """
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    w = _weights(tmp_path)
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    store = bs.BatchStore(tmp_path)
    batch = store.enqueue_verdict(
        corpus="v-ladder-c03-p400600", ckpts=[{"path": w, "label": "A"}]
    )
    assert batch is not None
    calls = _stub_dispatch(monkeypatch)

    t = _call(tmp_path, rl_path=str(other), mode="goal")
    assert t is not None and len(calls) == 1
    _bun, rl_path, _eval_log, _args, _cfg, _b, unit, unit_idx, unit_of, *_ = calls[0]
    assert unit["ckpt"] == w and rl_path == w != str(other) and unit_idx == 0
    assert unit_of == len(bp.plan_verdict_units(bp.corpus_doc("v-ladder-c03-p400600"), [{"path": w, "label": "A"}]))
    (row,) = _ledger(tmp_path)
    assert row["status"] == "running"
