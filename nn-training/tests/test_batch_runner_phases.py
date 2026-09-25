"""test_batch_runner_phases — `BatchEvalRunner._run` 的**相位契约**（S30/B5a）。

`_run` 原本是 821 行一个方法（状态块 + 11 个闭包 + 主循环 + 收尾三闸）。B5a 只做**相位边界**：

    _run            → 只有相位，没有判断
    _open_unit      → 单元开头：配置/权重/语料 → `_UnitPlan`（29 字段的**对外契约**）
    _run_channels   → 通道机器（状态 + 11 个闭包 + 主循环；本步**逐字节不动**）
    _settle_unit    → 收尾三闸 + **对台账的唯一交代**
    _log_provenance → 参与度账（谁跑的必须自证）

本文件钉两件事：

  ① **契约闭合**（结构）：`_UnitPlan` 字段 == `_open_unit` 的返回实参 == 机器取值段取的名字，
     且取值段是**恒等映射**（`x = plan.x`）；`_run` 体只有 3 条语句（相位，不是逻辑）；
     收尾/账块的参数表是**闭集**（多一个少一个都要显式改本表）。
  ② **★ 新获得的可测性**（拆分前做不到）：`_settle_unit` 与 `_log_provenance` 现在**直接可调**
     —— 台账交代（`mark_unit_done` / `reopen_for_resume`）、「任何失败只记日志绝不抛出」、
     两条响亮告警，都能用假状态断言，不必真跑一个单元。
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
from rl.batch_store import BatchStore

SRC = (ROOT / "rl" / "batch_runner.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
CLS = next(
    n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "BatchEvalRunner"
)
METHODS = {n.name: n for n in CLS.body if isinstance(n, ast.FunctionDef)}
PLAN = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_UnitPlan")
#: 机器体（原 `_run_channels` 体内：状态 + 11 个闭包 + 主循环）自 S31/B5b 起住 `_UnitLanes`。
LANES = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "_UnitLanes")


def _methods_of(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

#: `_UnitPlan` 字段（**闭集**：多一个/少一个都要显式改本表，与 `_open_unit` 同步）。
PLAN_FIELDS = (
    "unit",
    "kind",
    "unit_lives",
    "unit_level",
    "stage_params",
    "total",
    "todo",
    "t_start",
    "status_timeout",
    "task_timeout",
    "fail_streak_max",
    "busy_retry_limit",
    "busy_backoff_sec",
    "recover_ping_sec",
    "no_consumer_grace",
    "max_recovery_tries",
    "iter_id",
    "weights_bytes",
    "wver",
    "key16",
    "local_bun",
    "code_hash_local",
    "enabled_nodes",
    "local_slots",
    "local_weights",
    "local_on",
    "deadline",
    "trace",
    "req_scope",
)

#: `_settle_unit` 的 keyword-only 参数（= 机器要交出来的全部东西；闭集）。
SETTLE_PARAMS = (
    "lock",
    "seen",
    "nodes_ready_ever",
    "writers",
    "dup_settles",
    "stop_watch",
    "all_done",
    "node_games",
    "node_soft_fails",
    "node_hard_fails",
    "threads",
    "unit",
    "req_scope",
    "todo",
    "t_start",
)

#: `_log_provenance` 的参数（闭集）。
PROV_PARAMS = ("unit", "node_games", "node_soft_fails", "node_hard_fails", "nodes_ready_ever")


# ────────────────────────── ① 契约闭合（结构）──────────────────────────


def test_unit_plan_fields_are_exactly_the_declared_contract() -> None:
    got = tuple(
        s.target.id
        for s in PLAN.body
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
    )
    assert got == PLAN_FIELDS, sorted(set(got) ^ set(PLAN_FIELDS))


def test_open_unit_returns_the_contract_and_the_lanes_unpack_it_identically() -> None:
    """`_UnitPlan(...)` 的实参 == 字段集；取值段逐条 `self.x = plan.x`（名字写错会静默换值）。

    2026-09-25（S31/B5b）：取值段**换家** —— 原住在 `_run_channels` 开头的元组解包，现在住在
    `_UnitLanes.__init__`（机器体收进类 ⇒ 契约由构造器逐名接）。本用例随之改成「类里每个字段
    都以 `self.<字段>` 被读到」—— 少取一个 ⇒ 任一方法一跑就 AttributeError。
    """
    ret = [
        n
        for n in ast.walk(METHODS["_open_unit"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_UnitPlan"
    ]
    assert len(ret) == 1, "`_open_unit` 必须恰好一处构造 `_UnitPlan`"
    kwargs = [k.arg for k in ret[0].keywords]
    assert len(kwargs) == len(ret[0].keywords) and None not in kwargs, "构造必须全用关键字"
    assert sorted(str(k) for k in kwargs) == sorted(PLAN_FIELDS)

    init = _methods_of(LANES)["__init__"]
    body = list(init.body)
    assert ast.unparse(body[0]) == "self.owner = owner", "第一条把执行器存成 `self.owner`"
    assigns = [ast.unparse(st) for st in body[1 : 1 + len(PLAN_FIELDS)]]
    assert assigns == [f"self.{n} = plan.{n}" for n in PLAN_FIELDS], \
        "契约字段必须逐条恒等映射 `self.X = plan.X`（名字写错或漏一个会静默换值/AttributeError）"
    # 每个契约名都必须真被读到（`self.<字段>`）—— 只赋值不读 = 契约退化成摆设
    used = {
        n.attr
        for n in ast.walk(LANES)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }
    assert not set(PLAN_FIELDS) - used, sorted(set(PLAN_FIELDS) - used)


def test_run_is_only_the_three_phase_wiring() -> None:
    """`_run` 只讲相位：`plan = _open_unit()` → 早退透传 → `_run_channels(plan)`。"""
    body = list(METHODS["_run"].body)
    if ast.get_docstring(METHODS["_run"]) is not None:
        body = body[1:]  # docstring 先丢掉（相位陈述不算逻辑）
    assert len(body) == 3, [type(s).__name__ for s in body]
    assign, check, ret = body
    assert isinstance(assign, ast.Assign)
    called = assign.value
    assert isinstance(called, ast.Call) and isinstance(called.func, ast.Attribute)
    assert called.func.attr == "_open_unit"
    assert isinstance(check, ast.If) and "isinstance" in ast.unparse(check.test)
    assert isinstance(ret, ast.Return)
    back = ret.value
    assert isinstance(back, ast.Call) and isinstance(back.func, ast.Attribute)
    assert back.func.attr == "_run_channels"
    # 相位里不许有任何下标赋值 / 台账写点（状态与写点都在被调者里）
    assert not [n for n in ast.walk(METHODS["_run"]) if isinstance(n, ast.Subscript)]


def test_settle_and_provenance_signatures_are_closed_sets() -> None:
    settle = {a.arg for a in METHODS["_settle_unit"].args.kwonlyargs}
    assert settle == set(SETTLE_PARAMS), sorted(settle ^ set(SETTLE_PARAMS))
    assert METHODS["_settle_unit"].args.args == [] or [
        a.arg for a in METHODS["_settle_unit"].args.args
    ] == ["self"]
    assert METHODS["_settle_unit"].args.kwonlyargs, "收尾参数必须 keyword-only"
    prov = {a.arg for a in METHODS["_log_provenance"].args.kwonlyargs}
    assert prov == set(PROV_PARAMS), sorted(prov ^ set(PROV_PARAMS))

    # 调用点也是全关键字且名字对齐。
    # 2026-09-25（S31/B5b）：`_settle_unit` 的调用点从 `_run_channels` 换到 `_UnitLanes.run`
    # （机器体收进类；调用仍是 `self.owner._settle_unit(...)` ⇒ 归属者换了、参数表没换）。
    for call, params in (
        (
            next(
                n
                for n in ast.walk(_methods_of(LANES)["run"])
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_settle_unit"
            ),
            SETTLE_PARAMS,
        ),
        (
            next(
                n
                for n in ast.walk(METHODS["_settle_unit"])
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_log_provenance"
            ),
            PROV_PARAMS,
        ),
    ):
        assert [k.arg for k in call.keywords] == list(params), "调用必须按参数表逐名传值"


# ──────────────────── ② 功能性：收尾/账块现在直接可测 ────────────────────


def _runner(**kw) -> br.BatchEvalRunner:
    """只装需要的槽位（不跑 `__init__`，避免拉起真实派发上下文）。"""
    r = br.BatchEvalRunner.__new__(br.BatchEvalRunner)
    r.batch = kw.get("batch", {"batch_id": "b1"})
    r.unit_idx = kw.get("unit_idx", 0)
    r.unit_of = kw.get("unit_of", 2)
    r.eval_log = kw.get("eval_log", Path("/nonexistent/eval_log.jsonl"))
    r.bun = "bun"
    r.args = kw.get("args", object())
    r.cfg = kw.get("cfg", {"policy": {}})
    r.unit = kw.get("unit", {"rung": "c4l1", "seeds": [1], "stageId": 2000})
    r.rl_path = kw.get("rl_path")
    r.policy = kw.get("policy", "nn")
    r.run_id = "run-1"
    r.engine_epoch = "epoch-1"
    r.kind = "eval"
    r.window_event = None
    return r


def _settle(
    r: br.BatchEvalRunner,
    *,
    todo: list[tuple[int, int]],
    seen: set[tuple[int, int]],
    node_games: dict[str, int] | None = None,
) -> dict:
    """用**显式字面量**调收尾（`**dict` 会让 mypy 失去每参数类型 ⇒ 这里不省那几行）。"""
    return br.BatchEvalRunner._settle_unit(
        r,
        lock=threading.Lock(),
        seen=set(seen),
        nodes_ready_ever=set(),
        writers=[0],
        dup_settles=[0],
        stop_watch=threading.Event(),
        all_done=threading.Event(),
        node_games={"local": 2} if node_games is None else node_games,
        node_soft_fails={},
        node_hard_fails={},
        threads=[],
        unit={"rung": "c4l1"},
        req_scope="batcheval:test",
        todo=list(todo),
        t_start=time.time(),
    )


def test_settle_marks_the_unit_done_and_records_node_dist(tmp_path, monkeypatch) -> None:
    """全结算 ⇒ `mark_unit_done(unit_idx, node_dist)` —— 这是收尾对台账的正面交代。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    store = BatchStore(tmp_path)
    store.enqueue(course="c4", rung_from="c4l1", ckpt="w")
    (bid,) = [b["batch_id"] for b in store.all()]
    store.set_units_of(bid, 1)
    lines: list[str] = []
    monkeypatch.setattr(br, "log", lines.append)

    out = _settle(
        _runner(batch={"batch_id": bid}),
        todo=[(3, 100), (3, 101)],
        seen={(3, 100), (3, 101)},
    )
    assert out == {"settled": 2, "total": 2, "dropped": 0}
    (row,) = store.all()
    assert row["units"]["done"] == [0] and row["status"] == "done"
    assert row["node_dist"] == {"local": 2}
    assert any("DONE settled=2/2" in s for s in lines)


def test_settle_reopens_the_batch_when_partial(tmp_path, monkeypatch) -> None:
    """部分结算（yield/超时）⇒ `reopen_for_resume`：**不**标 unit done，下窗续跑。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    store = BatchStore(tmp_path)
    store.enqueue(course="c4", rung_from="c4l1", ckpt="w")
    (bid,) = [b["batch_id"] for b in store.all()]
    store.claim()  # 置 running（reopen 只对 running 生效）
    lines: list[str] = []
    monkeypatch.setattr(br, "log", lines.append)

    out = _settle(
        _runner(batch={"batch_id": bid}), todo=[(3, 100), (3, 101)], seen={(3, 100)}
    )
    assert out == {"settled": 1, "total": 2, "dropped": 1}
    (row,) = store.all()
    assert row["status"] == "pending" and row["units"]["done"] == []
    assert any("partial" in s for s in lines)


def test_settle_swallows_store_failures(tmp_path, monkeypatch) -> None:
    """B 层的规矩：**任何失败只记日志、绝不抛出**（训练主链零风险）。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    lines: list[str] = []

    def boom(*a, **k):
        raise RuntimeError("ledger locked")

    monkeypatch.setattr(br, "BatchStore", boom)
    monkeypatch.setattr(br, "log", lines.append)
    out = _settle(_runner(), todo=[(3, 100)], seen={(3, 100)})
    assert out["dropped"] == 0  # 正常返回，异常没外泄
    assert any("mark_unit_done failed" in s for s in lines)


def test_provenance_is_loud_when_remote_never_participated(monkeypatch) -> None:
    """两条响亮告警：远端 0 参与但本机跑成 ⇒ 警告「实际全本地」；本机也 0 局 ⇒ 点名权重桶。"""
    lines: list[str] = []
    monkeypatch.setattr(br, "log", lines.append)
    prov = br.BatchEvalRunner._log_provenance
    r = _runner()

    prov(
        r,
        unit={"rung": "c4l1"},
        node_games={"local": 5},
        node_soft_fails={"n1": 2},
        node_hard_fails={"n2": 1},
        nodes_ready_ever={"n1", "n2"},
    )
    joined = "\n".join(lines)
    assert "provenance: local=5" in joined
    assert "背压计数 n1=2｜真失败计数 n2=1" in joined
    assert "远端 0 参与" in joined and "**全本地**" in joined
    assert "0 局跑成" not in joined

    lines.clear()
    prov(
        r,
        unit={"rung": "c4l1"},
        node_games={},
        node_soft_fails={},
        node_hard_fails={},
        nodes_ready_ever={"n1"},
    )
    assert "0 局跑成" in "\n".join(lines)

    # 没有就绪过的节点 ⇒ 什么都不报（不制造噪声）
    lines.clear()
    prov(
        r,
        unit={"rung": "c4l1"},
        node_games={"local": 1},
        node_soft_fails={},
        node_hard_fails={},
        nodes_ready_ever=set(),
    )
    assert len(lines) == 1  # 只剩 provenance 一行


def test_open_unit_short_circuits_without_weights(tmp_path, monkeypatch) -> None:
    """nn 单元没有权重 ⇒ 直接 0 局返回（**相位短路**，不是抛错也不是干等）。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    lines: list[str] = []
    monkeypatch.setattr(br, "log", lines.append)
    out = br.BatchEvalRunner._open_unit(_runner())
    assert out == {"settled": 0, "total": 0, "dropped": 0}
    assert any("without weights" in s for s in lines)


def test_open_unit_short_circuits_when_every_pair_is_settled(tmp_path, monkeypatch) -> None:
    """全部 pair 已结算 ⇒ `settled == total` 直返（断点续跑的幂等面）。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    log_path = tmp_path / "eval_log.jsonl"
    weights = tmp_path / "w.json"
    weights.write_bytes(b"{}")
    wver = "deadbeef" * 8  # 指纹固定 => `key16 = wver[:16]` 可预期
    monkeypatch.setattr(br.dist_common, "weights_fingerprint", lambda p: wver)
    log_path.write_text(
        "\n".join(
            [
                f'{{"event": "eval", "wver": "{wver[:16]}", "stage": 2000, "seed": 7}}',
                f'{{"event": "eval", "wver": "{wver[:16]}", "stage": 2000, "seed": 8}}',
            ]
        ),
        encoding="utf-8",
    )
    lines: list[str] = []
    monkeypatch.setattr(br, "log", lines.append)
    r = _runner(
        eval_log=log_path,
        rl_path=str(weights),
        unit={"rung": "c4l1", "seeds": [7, 8], "stageId": 2000},
    )
    out = br.BatchEvalRunner._open_unit(r)
    assert out == {"settled": 2, "total": 2, "dropped": 0}
    assert any("already settled" in s for s in lines)
    assert isinstance(out, dict)
