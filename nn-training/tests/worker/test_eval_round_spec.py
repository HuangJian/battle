"""`worker/eval_arbiter.RoundSpec` —— `EvalRound` 的单一装载对象（P0-3，2026-11-20）。

旧形状：`EvalRound.__init__` 有 **40 个关键字参数**（`python.refactor.md` §P0-3 清单里最大
的一个）。那些值本来就住在两处 —— 派发器配置（`EvalDispatcher.self`）与每轮的派生状态
（`trainer/eval_dispatch._EvalRun`）—— 构造时又被逐个摊开一次，于是「加一个装载值」要改
四处（`_EvalRun` 字段 / `_round_spec` / `EvalRound.__init__` 形参 / 赋值），漏一处就是运行期
`AttributeError`，而 40 个关键字参数的名字在调用点根本读不过来。

本文件钉四件事：

1. **形状**：`EvalRound.__init__` 只收 `(self, spec)`，`RoundSpec` 是 frozen + kw_only；
2. **契约**：字段表逐字冻结（加装载值 = 同时改这里与 `_round_spec`，两处都看得见）；
3. **语义**：装载值只读、轮内状态现建（`pending` 是 `todo` 的副本、`node_caps` 按本机槽位）；
4. **装配点唯一**：生产面只有 `trainer/eval_dispatch.py` 构造 `RoundSpec`，且 `EvalRound(...)`
   调用只传一个位置参数 —— 不许把字段摊回去（否则第 2 条就白钉了）。
"""

from __future__ import annotations

import ast
import inspect
import threading
from dataclasses import FrozenInstanceError, fields
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from worker.eval_arbiter import LOCAL_NODE, EvalRound, RoundClock, RoundSpec

ROOT = Path(__file__).resolve().parents[2]
#: 生产面（与 `tests/test_python_shape_budget.py` 同口径；`tools/` 是脚本层，不在内）。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")

#: 一轮 eval 的全部装载值。**改这张表就是在改契约**：同时改 `RoundSpec` 与本表，
#: 并确认 `_round_spec` 给了新字段一个来源。
EXPECTED_FIELDS = (
    "seq",
    "it",
    "iter_id",
    "eval_iter_id",
    "wver",
    "key16",
    "course_fp",
    "args",
    "bun",
    "eval_dir",
    "eval_jsonl",
    "meta_path",
    "task_timeout",
    "window",
    "post_budget",
    "snapshot_path",
    "local_slots",
    "fallback_only",
    "reserved",
    "local_gate",
    "fail_streak_max",
    "soft_streak_max",
    "req_scope",
    "total",
    "pairs",
    "todo",
    "weights_kind",
    "weights_bytes",
    "attempts_max",
    "attempts_transient_max",
    "record_retry_max",
    "reuse_node",
    "row_builder",
    "append_row",
    "meta_ok",
    "record_failed_meta",
    "fail_recorder",
    "local_fn",
    "abort_scope",
    "log",
)


def _spec(**overrides: Any) -> RoundSpec:
    """一份装上可辨识哨兵值的 spec（默认值全是惰性回调，不起线程、不碰网络）。"""
    base: dict[str, Any] = {
        "seq": 7,
        "it": 11,
        "iter_id": "iter-11",
        "eval_iter_id": "iter-11ev",
        "wver": "w" * 64,
        "key16": "w" * 16,
        "course_fp": "fp-c1",
        "args": object(),
        "bun": "1.2.3",
        "eval_dir": Path("eval-dir"),
        "eval_jsonl": Path("eval_log.jsonl"),
        "meta_path": Path("dist-agent-meta.jsonl"),
        "task_timeout": 900.0,
        "window": 1200.0,
        "post_budget": 300.0,
        "snapshot_path": "eval-dir/_eval_frozen_weights.json",
        "local_slots": 3,
        "fallback_only": True,
        "reserved": 1,
        "local_gate": threading.Event(),
        "fail_streak_max": 3,
        "soft_streak_max": 9,
        "req_scope": "iter-11ev:fp-c1",
        "total": 2,
        "pairs": [(0, 1), (0, 2)],
        "todo": [(0, 2)],
        "weights_kind": "eval",
        "weights_bytes": None,
        "attempts_max": 2,
        "attempts_transient_max": 3,
        "record_retry_max": 4,
        "reuse_node": "reuse",
        "row_builder": lambda *_a, **_k: None,
        "append_row": lambda *_a, **_k: None,
        "meta_ok": lambda *_a, **_k: None,
        "record_failed_meta": lambda *_a, **_k: None,
        "fail_recorder": lambda *_a, **_k: None,
        "local_fn": lambda *_a, **_k: None,
        "abort_scope": lambda: None,
        "log": lambda _m: None,
    }
    base.update(overrides)
    return RoundSpec(**base)


def test_round_constructor_takes_one_object() -> None:
    """40 个关键字参数 → `spec` 一个（形状就是本刀的全部目的）。"""
    params = list(inspect.signature(EvalRound.__init__).parameters)
    assert params == ["self", "spec"], (
        f"`EvalRound.__init__` 只许收 (self, spec)，现在是 {params} —— "
        f"新的装载值进 `RoundSpec`（见 python.refactor.md §P0-3）"
    )


def test_round_spec_field_table_is_frozen() -> None:
    """字段表 = 契约：加/删装载值必须同时改表（否则这份守卫就只是记忆）。"""
    assert tuple(f.name for f in fields(RoundSpec)) == EXPECTED_FIELDS


def test_round_spec_is_keyword_only_and_frozen() -> None:
    # kw_only：按字段表问（真的没人能按位置传参）；frozen：`setattr` 必须被拒。
    assert all(f.kw_only for f in fields(RoundSpec)), "RoundSpec 的字段必须全部 kw_only"
    spec = _spec()
    with pytest.raises(FrozenInstanceError):  # 直接调 setter：frozen 的守卫必须喊
        spec.__setattr__("total", 99)


def test_round_builds_its_runtime_state_from_the_spec() -> None:
    """装载值照抄、轮内状态现建：两张表不许共享可变对象。"""
    logged: list[str] = []
    spec = _spec(log=logged.append)
    rnd = EvalRound(spec)

    assert rnd.spec is spec
    assert (rnd.seq, rnd.it, rnd.total, rnd.local_slots) == (7, 11, 2, 3)
    assert rnd.pairs is spec.pairs, "任务表按引用（调用方仍拿它算账）"
    # 待办复制成 deque：轮跑起来后 spec.todo 的变动不得改到本轮的发牌队列。
    assert list(rnd.pending) == [(0, 2)]
    assert rnd.pending is not spec.todo
    spec.todo.append((1, 1))
    assert list(rnd.pending) == [(0, 2)]
    # 本机许可按槽位数初值；窗口钟由 window + post_budget 现建。
    assert rnd.node_caps == {LOCAL_NODE: 3}
    assert isinstance(rnd.clock, RoundClock)
    assert (rnd.clock.window, rnd.clock.post_budget) == (1200.0, 300.0)
    assert not rnd.clock.paused and not rnd.clock.started
    # 注入的回调接线：按**可观察行为**验（`log()` 是唯一带公开入口的那个；
    # 其余几个是私有名，断言它们会把测试钉到实现形状上，G5 也不许）。
    rnd.log("[eval] hi")
    assert logged == ["[eval] hi"]
    assert rnd.local_fn is spec.local_fn
    # 轮内状态不来自 spec：每轮自己一份。
    assert rnd.closed is False and rnd.stop_grants is False
    assert not rnd.settled.is_set() and not rnd.local_stopped.is_set()
    assert rnd.nodes == {} and rnd.attempts == {} and rnd.landed == set()


@lru_cache(maxsize=1)
def _call_sites() -> dict[str, list[tuple[str, ast.Call]]]:
    """生产面里 `RoundSpec(...)` / `EvalRound(...)` 的调用点 → `{名字: [(文件, 节点)]}`。

    先按**字节**筛文件（整棵生产树的 AST 实测 ~5s，而这两个名字只活在两三个文件里），
    命中后才解析 —— 与 `test_no_print_in_prod` / `test_contract_sniff` 同一预筛法。
    """
    names = ("RoundSpec", "EvalRound")
    out: dict[str, list[tuple[str, ast.Call]]] = {n: [] for n in names}
    for d in PROD_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            text = p.read_text(encoding="utf-8", errors="replace")
            if not any(n in text for n in names):
                continue
            rel = p.relative_to(ROOT).as_posix()
            for node in ast.walk(ast.parse(text, filename=str(p))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    hits = out.get(node.func.id)
                    if hits is not None:
                        hits.append((rel, node))
    return out


def test_production_assembles_the_spec_in_one_place() -> None:
    """装配点唯一 ⇒ 值只有一处来源，改字段名不会漏掉某个调用点。"""
    owners = sorted({rel for rel, _call in _call_sites()["RoundSpec"]})
    assert owners == ["trainer/eval_dispatch.py"], (
        f"`RoundSpec` 只许在 trainer/eval_dispatch.py 装配，现在还有：{owners}"
    )


def test_production_passes_one_object_to_eval_round() -> None:
    """`EvalRound(...)` 只传一个位置参数、零关键字：字段不许摊回调用点。"""
    bad = [
        f"{rel}:{call.lineno} args={len(call.args)} kw={len(call.keywords)}"
        for rel, call in _call_sites()["EvalRound"]
        if call.keywords or len(call.args) != 1
    ]
    assert not bad, "`EvalRound(...)` 必须只收一个位置参数：\n  " + "\n  ".join(bad)
