"""拆分的**契约守卫**：调度策略住在 `common/scheduling.py`（B8 第二刀，2026-11-18）。

`common/protocol.py` **1120 → 969 行**；搬走**三族共 32 名**（逐字节不动）：优先级五档 +
`job_priority` + 两个阈值 + `CLAIM_MODE_*` · hub 中介推送标记与 `PUSH_*` 节奏/冷却/告警 +
`push_worker_id_of` · 节点能力 `CAP_*` / `NODE_*` + 三个形状函数 + `pick_push_worker` /
`push_job_wants_hub_push`。

本文件把**一条契约**（「策略只有一个家，协议只做转发」）拆成四问，一次报齐：

1. **定义唯一**——这 32 名不许在 `protocol.py` 里再实现一遍；
2. **依赖面闭集（且无环）**——只准 stdlib（`collections.abc`）；**不** import `common.protocol`，
   也不碰上层包（`common/` 随 `code.zip` 解到云机）；
3. **转发同一对象**——`protocol` 的每个搬走名都是新模块的转发（`is`），历史 import 一行不改；
4. **分工没走样**——策略模块不许抄一份线协议常量（`PROTO` 只能有一个家）。

为什么合并成一条用例：这四问共用同一份「搬走名单 + 两份 AST」，分成四条只是把同一次读取拆四遍；
失败信息逐条带标签，读起来与四条用例相同。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.protocol as protocol_mod
import common.scheduling as scheduling_mod

PROTO_FILE = ROOT / "common" / "protocol.py"
SCHEDULING_FILE = ROOT / "common" / "scheduling.py"

MOVED_NAMES = {
    # 优先级（§1.4 表 + 阈值 + claim 模式）
    "CLAIM_MODE_EXCLUSIVE",
    "CLAIM_MODE_BACKUP",
    "CLAIM_MODES",
    "PRIORITY_NONE",
    "PRIORITY_LOW",
    "PRIORITY_MEDIUM",
    "PRIORITY_HIGH",
    "PRIORITY_HIGHEST",
    "PRIORITY_ORDER",
    "STRAGGLER_SEC",
    "JOB_CANCEL_POLL_SEC",
    "job_priority",
    # hub 中介推送
    "DISPATCH_HUB_PUSH",
    "PUSH_POLL_SEC",
    "PUSH_PING_SEC",
    "PUSH_DEAD_MISSES",
    "PUSH_TIMEOUT_SEC",
    "PUSH_AHEAD_DEFAULT",
    "PUSH_COOLDOWN_SEC",
    "PUSH_SLOW_UPLOAD_SEC",
    "PUSH_SLOW_RESULT_SEC",
    "PUSH_WORKER_PREFIX",
    "push_worker_id_of",
    "push_job_wants_hub_push",
    # 节点能力形状
    "CAP_GPU_PUSH",
    "CAP_OFFLINE_PUSH",
    "NODE_CAPABILITIES",
    "NODE_KINDS",
    "node_capabilities",
    "sampler_nodes",
    "push_worker_from_node",
    "pick_push_worker",
}

#: 策略模块的依赖闭集（stdlib）。
ALLOWED_IMPORTS = {"__future__", "collections.abc"}

#: 只在 `common/protocol.py` 出现的线协议常量（策略模块抄一份就是第二份真相）。
WIRE_ONLY = {"PROTO", "PLAN_PROTO", "AUTH_HEADER", "WORKER_ID_HEADER", "ROLE_HEADER"}


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


def _imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _assigned_names(path: Path) -> set[str]:
    return {
        t.id
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }


def test_scheduling_owns_the_policy_and_protocol_only_forwards() -> None:
    """策略只有一个家：定义在 `common/scheduling.py`，`common/protocol.py` 只转发同一对象。"""
    problems: list[str] = []

    defined_new = _defined(SCHEDULING_FILE)
    if MOVED_NAMES - defined_new:
        problems.append(f"① scheduling 里缺定义：{sorted(MOVED_NAMES - defined_new)}")
    leftovers = MOVED_NAMES & _defined(PROTO_FILE)
    if leftovers:
        problems.append(f"① protocol 里仍在实现这些名字（应只转发）：{sorted(leftovers)}")

    mods = _imported_modules(SCHEDULING_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    if extra:
        problems.append(f"② scheduling 依赖面越界：{extra}")
    tops = {m.split(".")[0] for m in mods}
    banned = {"torch", "numpy", "trainer", "worker", "biz", "remote", "hub", "models", "data"}
    if tops & banned:
        problems.append(f"② scheduling 依赖了上层：{sorted(tops & banned)}")
    if not mods.isdisjoint({"common.protocol"}):
        problems.append("② scheduling 反向 import 门面 ⇒ 与 protocol 互引成环")

    missing = sorted(n for n in MOVED_NAMES if not hasattr(protocol_mod, n))
    if missing:
        problems.append(f"③ protocol 门面缺名字：{missing}")
    second_copy = sorted(
        n for n in MOVED_NAMES if getattr(protocol_mod, n) is not getattr(scheduling_mod, n)
    )
    if second_copy:
        problems.append(f"③ 这些名字是第二份实现，不是转发：{second_copy}")

    leaked = sorted(_assigned_names(SCHEDULING_FILE) & WIRE_ONLY)
    if leaked:
        problems.append(f"④ scheduling 里出现了线协议常量：{leaked}")

    assert not problems, "调度/协议分工被破坏：\n  " + "\n  ".join(problems)
