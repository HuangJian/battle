"""test_batch_lanes_split — `_UnitLanes`（通道机器）的契约（S31/B5b）。

B5a 把 `_run` 切成相位，但机器体仍是 `_run_channels` 里 **677 行一个方法**：状态散在函数体、
11 个闭包靠**可变的容器**（`seen: set` / `settled: [0]` / `lanes: dict`）绕开闭包只读限制。
B5b 把那一整段按**状态所有者**收进 `_UnitLanes`：契约字段 → `self.*`（`__init__` 从 `_UnitPlan`
逐名接），11 个闭包 → 11 个方法，主循环 → `run()`。

本文件钉三件事：

  ① **结构契约**：类定义唯一 · 方法名闭集 · `_run_channels` 只剩一条转发 · 类里没有嵌套 def
     （闭包全部升平）· 实例属性面 == {owner} ∪ 契约(29) ∪ 状态(23) ∪ 方法(11) 的**闭集**。
  ② **台账边界**：机器不认识台账（零 `store.` / 零具名转移）—— 「对台账的唯一交代」仍在
     `BatchEvalRunner._settle_unit`（B5a 的守卫在钉它）。
  ③ **★ 新获得的可测性**：`lane_state` / `mark_tripped` / `window_open` 这些相位现在**直接可调**
     —— 拆分前它们是 677 行方法里的闭包，只能靠「真跑一个单元」间接看。
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

import rl.batch_runner as br

SRC = (ROOT / "rl" / "batch_runner.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
LANES = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_UnitLanes")
RUNNER = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "BatchEvalRunner")
LANES_METHODS = {n.name: n for n in LANES.body if isinstance(n, ast.FunctionDef)}

#: 机器的方法（**闭集**：11 个原闭包 + `__init__` + `run()`；增删都要显式改本表）。
MACHINE_METHODS = (
    "__init__",
    "lane_state",
    "bringup",
    "mark_tripped",
    "spawn_workers",
    "supervise",
    "window_open",
    "record",
    "params_for",
    "fetch_manifest",
    "worker",
    "_watch",
    "run",
)

#: 只能被外部（`run()` / `_run_channels`）调的两个：它们不以 `self.<名>` 出现。
ENTRY_METHODS = ("__init__", "run")

#: 状态块（原 `_run_channels` 开头那段）的 23 个容器（**闭集**）。
STATE_ATTRS = (
    "all_done",
    "attempts",
    "busy_tries",
    "dup_settles",
    "in_flight",
    "inflight",
    "inflight_nodes",
    "jsonl_lock",
    "lanes",
    "lock",
    "node_games",
    "node_hard_fails",
    "node_pings",
    "node_ready_at",
    "node_soft_fails",
    "nodes_ready_ever",
    "pending",
    "ready_lanes",
    "seen",
    "settled",
    "stop_watch",
    "streaks",
    "writers",
)


def _plan_fields() -> list[str]:
    """契约字段的单一来源 = `_UnitPlan` 的声明序（字段集本身由 `test_batch_runner_phases` 钉）。"""
    plan = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_UnitPlan")
    return [
        s.target.id for s in plan.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
    ]


def _self_attrs(node: ast.AST) -> list[str]:
    return [
        n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    ]


# ──────────────────────────── ① 结构契约 ────────────────────────────


def test_machine_class_is_defined_exactly_once_in_the_runner_module() -> None:
    """类只许住 `rl/batch_runner.py` 一次 —— 换家（另一个模块）要让本用例显式改。"""
    homes = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "rl").rglob("*.py")
        if p.name != "__pycache__" and "class _UnitLanes" in p.read_text(encoding="utf-8")
    )
    assert homes == ["rl/batch_runner.py"], homes
    assert SRC.count("class _UnitLanes") == 1


def test_machine_method_face_is_the_designed_closed_set() -> None:
    assert tuple(LANES_METHODS) == MACHINE_METHODS, sorted(set(LANES_METHODS) ^ set(MACHINE_METHODS))


def test_closures_are_flat_methods_not_nested_defs() -> None:
    """机器体里**不许**再有嵌套 def（11 个闭包全部升平）；执行器成员也不许再有闭包。"""
    nested_machine = [
        n.name
        for m in LANES.body
        if isinstance(m, ast.FunctionDef)
        for n in ast.walk(m)
        if isinstance(n, ast.FunctionDef) and n is not m
    ]
    assert nested_machine == [], nested_machine
    nested_runner = [
        f"{m.name}.{n.name}"
        for m in RUNNER.body
        if isinstance(m, ast.FunctionDef)
        for n in ast.walk(m)
        if isinstance(n, ast.FunctionDef) and n is not m
    ]
    assert nested_runner == [], nested_runner


def test_run_channels_only_constructs_and_runs_the_machine() -> None:
    """`BatchEvalRunner._run_channels` 只剩一条转发（相位陈述；机器体全在类里）。"""
    method = next(
        m for m in RUNNER.body if isinstance(m, ast.FunctionDef) and m.name == "_run_channels"
    )
    body = [
        st
        for st in method.body
        if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant))
    ]
    assert [ast.unparse(st) for st in body] == ["return _UnitLanes(self, plan).run()"]


def test_instance_attribute_face_is_the_closed_set() -> None:
    """实例属性面 = {owner} ∪ 契约 ∪ 状态 ∪ 方法名 —— 类不许往 `self` 上挂表外的名字。"""
    got = set(_self_attrs(LANES))
    called = tuple(m for m in MACHINE_METHODS if m not in ENTRY_METHODS)
    want = {"owner", *_plan_fields(), *STATE_ATTRS, *called}
    assert got == want, sorted(got ^ want)


def test_init_takes_the_contract_by_name_and_the_state_verbatim() -> None:
    fields = _plan_fields()
    body = LANES_METHODS["__init__"].body
    assert ast.unparse(body[0]) == "self.owner = owner"
    assert [ast.unparse(st) for st in body[1 : 1 + len(fields)]] == [
        f"self.{n} = plan.{n}" for n in fields
    ]
    targets = [
        t
        for st in body
        if isinstance(st, (ast.Assign, ast.AnnAssign))
        for t in (st.targets if isinstance(st, ast.Assign) else [st.target])
    ]
    assigned = {t.attr for t in targets if isinstance(t, ast.Attribute)}
    #  状态块大半是**带注解的**赋值（`self.pending: deque[...] = …`）⇒ 两种都要数。
    assert assigned == {"owner", *_plan_fields(), *STATE_ATTRS}, sorted(
        assigned ^ {"owner", *_plan_fields(), *STATE_ATTRS}
    )


# ──────────────────────────── ② 台账边界 ────────────────────────────


def test_machine_never_touches_the_ledger() -> None:
    """机器不认识台账：零 `store.` 具名转移、零 `mark_unit_done`/`reopen_for_resume`。"""
    names = {n.id for n in ast.walk(LANES) if isinstance(n, ast.Name)}
    assert "store" not in names, "机器里出现了 `store` —— 台账的写点又漏回来了"
    called = {
        n.func.attr
        for n in ast.walk(LANES)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not (called & {"mark_unit_done", "reopen_for_resume", "enqueue", "claim"}), sorted(called)


# ────────────────────── ③ 功能性：相位现在直接可调 ──────────────────────


def _lanes(**kw) -> br._UnitLanes:
    """只装需要的槽位（不走 `__init__`，避免为每个用例编一份 29 字段的 `_UnitPlan`）。"""
    lanes = br._UnitLanes.__new__(br._UnitLanes)
    lanes.owner = kw.get("owner", br.BatchEvalRunner.__new__(br.BatchEvalRunner))
    lanes.lanes = {}
    lanes.lock = threading.Lock()
    lanes.ready_lanes = [0]
    lanes.recover_ping_sec = kw.get("recover_ping_sec", 20.0)
    lanes.max_recovery_tries = kw.get("max_recovery_tries", 3)
    lanes.deadline = kw.get("deadline", time.time() + 60)
    return lanes


def test_lane_state_normalizes_a_node_and_registers_it_once() -> None:
    """`lane_state`：配置节点（`authKey`/`concurrency`）归一成 worker 要的形状，且**幂等**。"""
    lanes = _lanes()
    lane = lanes.lane_state({"id": "n1", "url": "http://h", "authKey": "k", "concurrency": 3})
    assert lane["nd"] == {"id": "n1", "url": "http://h", "key": "k"}
    assert lane["c"] == 3 and lane["ready"] is False and lane["given_up"] is False
    assert lanes.lanes == {"n1": lane}
    assert lanes.lane_state({"id": "n1"}) is lane, "同一个 nid 必须拿到同一个通道对象"
    # 缺 id/url ⇒ 兜底 `?`；缺 concurrency ⇒ 1（不得出现 0 槽位通道）
    odd = lanes.lane_state({"url": ""})
    assert odd["id"] == "?" and odd["c"] == 1


def test_mark_tripped_gives_up_after_max_recovery_tries(monkeypatch) -> None:
    """掉线计数：交出槽位 + 排重探；到 `max_recovery_tries` 才**认定坏节点**（响亮一行）。"""
    logged: list[str] = []
    monkeypatch.setattr(br, "log", logged.append)
    lanes = _lanes(max_recovery_tries=2)
    lane = lanes.lane_state({"id": "n1"})
    lane["ready"] = True
    lanes.ready_lanes[0] = 1

    lanes.mark_tripped(lane)
    assert lane["tripped"] is True and lane["given_up"] is False
    assert lanes.ready_lanes[0] == 0
    assert lane["next_try"] > time.time(), "必须排一次重探（否则通道永远不再被叫醒）"
    assert logged == [], "首轮不许判死"

    lane["ready"] = True
    lanes.ready_lanes[0] = 1
    lanes.mark_tripped(lane)
    assert lane["strikes"] == 2 and lane["given_up"] is True
    assert len(logged) == 1 and "n1" in logged[0]
    # 槽位计数不得被减成负数（掉线次数可以多于当前就绪数）
    lanes.mark_tripped(lane)
    assert lanes.ready_lanes[0] == 0


def test_window_open_follows_the_owner_event_and_falls_back_to_deadline() -> None:
    """关窗即停派（yield）：有事件看事件，`window_event is None` 才退化成墙钟。"""
    owner = br.BatchEvalRunner.__new__(br.BatchEvalRunner)
    owner.window_event = None
    lanes = _lanes(owner=owner, deadline=time.time() + 60)
    assert lanes.window_open() is True
    lanes.deadline = time.time() - 1
    assert lanes.window_open() is False

    owner.window_event = threading.Event()  # 未置位 = evalboard 关窗 = yield
    lanes.deadline = time.time() + 60
    assert lanes.window_open() is False, "窗关了就不许派新局（即使墙钟还早）"
    owner.window_event.set()
    assert lanes.window_open() is True
