"""`PackTriggers`（P0-5，2026-11-21）：三本触发账的写入口、隔离性与清场。

为什么值得单独钉：这三本账原来是模块级 dict + 模块级锁，`hub/offline.py` 与
`hub/task_pack.py` 两个模块都在往里写，`hub/server.py` 还把其中一本当门面再导出。
现在形状是「一个对象 + 三组写入口」，本文件钉住三件旧形状答不出的事：

1. **三本账互不串味**：过期腿与缺包腿在同一门课上各自记账（同一窗里最多推 2 次），
   自动交接腿的失败缓存不影响另两本；
2. **清场是逐本、逐课可做的**：`clear_stale` / `clear_miss` / `clear_handoff` 不误伤邻账；
3. **内部字典不交出去**：`snapshot()` 是拷贝（用例/观测改它不该影响判据）。

另加两条结构判据：模块顶层不再有可变容器字面量，`hub/offline.py` 不再 import 私有账名
（否则「对象化」只是一层新皮肤）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from hub.task_pack import (
    TASK_AUTO_HANDOFF_THROTTLE_SEC,
    TASK_AUTO_HANDOFF_TRIGGER_LIMIT,
    TASK_BUNDLE_FAILURE_WINDOW_SEC,
    TASK_BUNDLE_STATUS_POLL_SEC,
    TASK_PACK_MISS_TRIGGER_LIMIT,
    TASK_PACK_STALE_THROTTLE_SEC,
    TASK_PACK_STALE_TRIGGER_LIMIT,
    PackTriggers,
)

NN_ROOT = Path(__file__).resolve().parents[2]

#: 旧形状的模块级名字：谁再 import 它们，就是把「谁都能写」搬回来了。
PRIVATE_LEDGERS = (
    "_TASK_PACK_TRIGGERS",
    "_TASK_PACK_MISS_TRIGGERS",
    "_TASK_AUTO_HANDOFF_TRIGGERS",
    "_TASK_PACK_LOCK",
)


def _trigger_all(t: PackTriggers, course: str, start: float) -> list[str]:
    """对着过期腿按「刚好越过节流窗」的节奏连推，收集每次的 verdict。"""
    verdicts: list[str] = []
    now = start
    for _ in range(TASK_PACK_STALE_TRIGGER_LIMIT + 1):
        verdict, _warn = t.stale_gate(course, now=now, stale="包起点 ≠ 当前权重")
        verdicts.append(verdict)
        now += TASK_PACK_STALE_THROTTLE_SEC + 1.0
    return verdicts


# ───────────────────────── ① 三本账互不串味 ─────────────────────────


def test_the_three_legs_count_their_own_windows() -> None:
    t = PackTriggers()
    assert t.stale_gate("c1", now=1000.0, stale="过期")[0] == "trigger"
    # 同一个窗里，缺包腿**不被**过期腿的计数吃掉（这是刻意的两本账，不是 bug）。
    assert t.miss_gate("c1", now=1000.0, reason="缺包 p") == "trigger"
    snap = t.snapshot()
    assert snap["stale"]["c1"]["count"] == 1.0
    assert snap["stale"]["c1"]["last"] == 1000.0
    assert snap["miss"]["c1"]["count"] == 1.0
    assert snap["handoff"] == {}


def test_the_stale_leg_throttles_then_gives_up_with_one_warning() -> None:
    t = PackTriggers()
    verdicts = _trigger_all(t, "c1", 1000.0)
    assert verdicts[:TASK_PACK_STALE_TRIGGER_LIMIT] == ["trigger"] * TASK_PACK_STALE_TRIGGER_LIMIT
    assert verdicts[-1] == "give_up"

    # `warned` 只翻一次面：到顶后第二次仍是 give_up，但不再要求打日志。
    _verdict, warn = t.stale_gate("c1", now=10_000.0, stale="过期")
    assert warn is False
    # 窗内（没越过节流）连第一次 give_up 都不会重算到 trigger。
    verdict, _warn = t.stale_gate("c1", now=10_000.5, stale="过期")
    assert verdict == "give_up"


def test_the_miss_leg_has_its_own_upper_bound() -> None:
    t = PackTriggers()
    now = 1000.0
    verdicts = []
    for _ in range(TASK_PACK_MISS_TRIGGER_LIMIT + 1):
        verdicts.append(t.miss_gate("c1", now=now, reason="缺包 p"))
        now += TASK_PACK_STALE_THROTTLE_SEC + 1.0
    assert verdicts[:TASK_PACK_MISS_TRIGGER_LIMIT] == ["trigger"] * TASK_PACK_MISS_TRIGGER_LIMIT
    assert verdicts[-1] == "give_up"


# ───────────────────────── ② 自动交接腿的顺序 ─────────────────────────


def test_handoff_decision_prefers_known_failure_over_count() -> None:
    t = PackTriggers()
    assert t.decision("c1", now=1000.0) == "trigger"
    t.note_trigger("c1", now=1000.0)
    assert t.decision("c1", now=1005.0) == "throttled"
    # 越过节流窗 ⇒ 又能触发（计数还没到顶）。
    assert t.decision("c1", now=1000.0 + TASK_AUTO_HANDOFF_THROTTLE_SEC + 1.0) == "trigger"

    # 已知失败优先于计数：它给的是静态拒因，再推也还是同一条。
    t.note_failure("c1", "iters 非法", 2000.0)
    assert t.decision("c1", now=2000.0 + TASK_BUNDLE_FAILURE_WINDOW_SEC - 1.0) == "failed"
    assert t.failure("c1") == ("iters 非法", 2000.0)
    # 失败窗过期后回到计数判据（此时计数已到 3 ⇒ give_up）。
    for _ in range(TASK_AUTO_HANDOFF_TRIGGER_LIMIT):
        t.note_trigger("c1", now=3000.0)
    assert t.decision("c1", now=2000.0 + TASK_BUNDLE_FAILURE_WINDOW_SEC + 1.0) == "give_up"


def test_handoff_poll_window_is_rate_limited_and_needs_a_trigger_first() -> None:
    t = PackTriggers()
    # 从没触发过 ⇒ 读接口不该凭空向外发请求。
    assert t.poll_window("c1", now=1000.0) == (False, 0.0)
    t.note_trigger("c1", now=1000.0)
    assert t.poll_window("c1", now=1001.0) == (True, 1000.0)
    # 窗内第二次不问。
    assert t.poll_window("c1", now=1001.0 + TASK_BUNDLE_STATUS_POLL_SEC - 1.0) == (False, 0.0)
    assert t.poll_window("c1", now=1001.0 + TASK_BUNDLE_STATUS_POLL_SEC + 1.0) == (True, 1000.0)


# ───────────────────────── ③ 清场与快照 ─────────────────────────


def test_clear_is_per_course_and_per_leg() -> None:
    t = PackTriggers()
    for course in ("c1", "c2"):
        t.stale_gate(course, now=1000.0, stale="过期")
        t.miss_gate(course, now=1000.0, reason="缺包 p")
        t.note_trigger(course, now=1000.0)

    t.clear_stale("c1")
    snap = t.snapshot()
    assert set(snap["stale"]) == {"c2"}
    assert set(snap["miss"]) == {"c1", "c2"}, "清一本账不该误伤邻账"
    assert set(snap["handoff"]) == {"c1", "c2"}

    t.clear_miss("")
    t.clear_handoff("")
    snap = t.snapshot()
    assert snap["miss"] == {} and snap["handoff"] == {}
    assert set(snap["stale"]) == {"c2"}


def test_snapshot_hands_out_copies() -> None:
    t = PackTriggers()
    t.stale_gate("c1", now=1000.0, stale="过期")
    snap = t.snapshot()
    snap["stale"]["c1"]["count"] = 99.0
    snap["stale"].pop("c1")
    assert t.snapshot()["stale"]["c1"]["count"] == 1.0, "内部字典被用例改动了"


# ───────────────────────── ④ 结构判据 ─────────────────────────


def test_task_pack_defines_no_module_level_mutable_container() -> None:
    """模块顶层不许再有可变容器字面量（账本必须住在 `PackTriggers` 里）。"""
    tree = ast.parse((NN_ROOT / "hub/task_pack.py").read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        value = node.value
        is_container = isinstance(value, ast.Dict | ast.List | ast.Set)
        if not is_container and isinstance(value, ast.Call) and not value.args:
            func = value.func
            is_container = isinstance(func, ast.Name) and func.id in ("dict", "list", "set")
        # 全大写 = 查表常量（`TASK_STATE_RANK` 这类只读表，审计口径同款豁免）；
        # dunder（`__all__`）不算状态。
        if is_container and not any(n.startswith("__") or n.isupper() for n in names):
            offenders.append(f"{node.lineno}: {'/'.join(names)}")
    assert offenders == [], offenders


def test_offline_gate_no_longer_imports_the_private_ledgers() -> None:
    """两道门走 `pack_triggers()`；再 import 私有账名 = 又把写权散出去。"""
    tree = ast.parse((NN_ROOT / "hub/offline.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
    assert not (imported & set(PRIVATE_LEDGERS)), sorted(imported & set(PRIVATE_LEDGERS))
