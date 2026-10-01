"""拆分的**契约守卫**：评估的「让位/份额（尾巴）策略」永住 `biz/eval_yield.py`（S5 第十二刀，2026-09-27）。

本刀从**两处一起**切（plan §5.7.3 记的同族候选——`EvalDispatcher` 的尾巴策略与 `eval_local`
留守的那族同一所有者）：

* `biz/eval_local.py` 的 L110–L226（117 行，5 常量 + 7 判决函数 + 两段族注释）**逐字节**
  搬进新家；
* `trainer/eval_dispatch.py`（`EvalDispatcher.run`）里**只有实现、没有名字**的三个判决点提出成
  `reserve_local_slots` / `local_release_due` / `inflight_grace_cap`（原式逐项等价），派发器
  改为调用它们；`trainer/loop_eval.py` 与 `trainer/batch_runner.py` 的判据 / 份额缺省也改指新家。

本文件钉七件事：① 定义唯一（12 名只许在新家实现）② 执行面**反向留守**（不然搬错了对象）
③ 依赖面闭集（**零依赖叶子**：只许 `__future__`）④ 不得反向 import（import `biz.eval_local`
= 伸手回运行器，成环即红）⑤ 门面对象恒等（`is`，不是副本）⑥ 边缘重指 + 判决点迁移（派发器
里三条旧表达式必须消失）⑦ 语义（三条新公式 + 让位/放行/收拢判据）。

为什么单独成家（独立所有者 + 独立触发条件）：这些判决的触发者是**边界事件**——派发那一刻的
份额分档、PPO 收官的 join/交棒、下一轮 rollout 收官时的收拢、窗口到期后的在飞宽限——与
「怎么在本机跑一局评估」（子进程 / 看门狗 / 账本，`biz/eval_local.py`）零共享状态。留在 573 行
的运行器里时，派发器为一条判据就得拖入整台运行器。
"""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trainer.eval_dispatch as eval_dispatch_mod
import worker.eval_local as eval_local_mod
import worker.eval_yield as eval_yield_mod
from tests.helpers import remote_dag as dag

YIELD_FILE = ROOT / "worker" / "eval_yield.py"
LOCAL_FILE = ROOT / "worker" / "eval_local.py"
DISPATCH_FILE = ROOT / "trainer" / "eval_dispatch.py"
LOOP_EVAL_FILE = ROOT / "trainer" / "loop_eval.py"
BATCH_RUNNER_FILE = ROOT / "trainer" / "batch_runner.py"

#: 本次搬走的**定义**（常量 / 函数）——只许在 `eval_yield.py` 里出现。
MOVED_NAMES = {
    "EVAL_INFLIGHT_GRACE_SEC",
    "EVAL_JOIN_SOFT_SEC_DEFAULT",
    "EVAL_LOCAL_EARLY_EPOCHS_DEFAULT",
    "EVAL_LOCAL_RELEASE_GRACE",
    "EVAL_LOCAL_SLOTS_DEFAULT",
    "early_epoch_reached",
    "eval_join_soft_sec",
    "eval_local_early_epochs",
    "eval_tail_overran",
    "hold_for_local",
    "local_gate_release_plan",
    "release_local_gate_if_starved",
}

#: 从 `EvalDispatcher.run` 的内联判决点提出的三个公式（本刀新写，原式逐项等价）。
NEW_FORMULAS = {"reserve_local_slots", "local_release_due", "inflight_grace_cap"}

#: `eval_yield.py` 允许的 import 面（**零依赖叶子**；多一个即红）。
ALLOWED_IMPORTS = {"__future__"}

#: 读者把它们从哪取（边缘重指：不再经运行器 `biz.eval_local`）。
READERS = {
    "trainer/eval_dispatch.py": (
        "EVAL_LOCAL_SLOTS_DEFAULT",
        "hold_for_local",
        "release_local_gate_if_starved",
        "reserve_local_slots",
        "local_release_due",
        "inflight_grace_cap",
    ),
    "trainer/loop_eval.py": (
        "eval_join_soft_sec",
        "eval_tail_overran",
        "eval_local_early_epochs",
        "local_gate_release_plan",
    ),
    "trainer/batch_runner.py": ("EVAL_LOCAL_SLOTS_DEFAULT",),
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    """该文件里 `def` / `class` / 顶层赋值定义的函数名 / 类名 / 变量名。"""
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
    """全部 import（含函数内延迟 import）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _import_sources(path: Path) -> dict[str, str]:
    """名字 -> 它从哪个模块 import 进来（含延迟 import）；同名多处取最后一次。"""
    out: dict[str, str] = {}
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = node.module
    return out


# ───────────────────────── ① 定义唯一 / ② 反向留守 ─────────────────────────


def test_moved_names_are_defined_in_eval_yield_and_not_redefined_in_eval_local() -> None:
    """定义唯一：搬走的名字只在新家实现（旧家里再定义一遍 = 搬了一半）。"""
    defined_here = _defined(YIELD_FILE)
    assert defined_here >= MOVED_NAMES | NEW_FORMULAS, sorted(MOVED_NAMES - defined_here)
    leftovers = (MOVED_NAMES | NEW_FORMULAS) & _defined(LOCAL_FILE)
    assert leftovers == set(), f"eval_local.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_eval_local_kept_the_execution_surface() -> None:
    """反向：运行器与它的重试配额**留在** `eval_local.py`（不然这刀搬错了对象）。"""
    defined = _defined(LOCAL_FILE)
    for name in (
        "run_local_eval_game",
        "run_eval_runner_capture",
        "eval_done_keys",
        "baseline_summary_landed",
        "EVAL_TASK_ATTEMPTS",
    ):
        assert name in defined, f"eval_local.py 丢了执行面 {name}"


# ─────────────────────── ③④ 依赖方向：零依赖叶子，无环 ───────────────────────


def test_eval_yield_is_a_zero_dependency_leaf() -> None:
    """纯判决模块：**不 import 任何模块**（`__future__` 之外多一个即红——连 `biz.log` 都不需要）。"""
    extra = sorted(_imports(YIELD_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"worker/eval_yield.py 引入了依赖：{extra}"


def test_eval_yield_never_imports_the_runner() -> None:
    """★ 本刀的意义：判决面是**底座**，反向 import 运行器（`worker.eval_local`）立刻成环。"""
    back = sorted(m for m in _imports(YIELD_FILE) if m.startswith("worker.eval_local"))
    assert back == [], f"worker/eval_yield.py 反向 import 了仓内模块：{back}"


def test_eval_yield_stays_pure_logic() -> None:
    """纯逻辑（不达传输面）：账本里它没有任何通往 `remote.*` 的路径。

    2026-09-30（刀 6）：判据从「不在 `remote_dag` 的账本里」改成**可达性**——
    `worker/` 整包入账之后，前者的写法恒为假（哑守卫）。
    """
    assert dag.reaches_transport("worker.eval_yield") is False


# ───────────────────────── ⑤ 门面 ─────────────────────────


def test_eval_local_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `eval_yield` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(eval_local_mod, name), f"biz.eval_local 丢了门面 {name}"
        assert getattr(eval_local_mod, name) is getattr(eval_yield_mod, name), (
            f"biz.eval_local.{name} 不是 biz.eval_yield.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_eval_local() -> None:
    """名字是契约：旧写法 `from biz.eval_local import hold_for_local` 必须仍然成立。"""
    from worker.eval_local import eval_join_soft_sec, hold_for_local, release_local_gate_if_starved

    assert hold_for_local is eval_yield_mod.hold_for_local
    assert eval_join_soft_sec is eval_yield_mod.eval_join_soft_sec
    assert release_local_gate_if_starved is eval_yield_mod.release_local_gate_if_starved


def test_eval_dispatch_namespace_still_exposes_the_verdicts() -> None:
    """e2e 里写的是 `ed.hold_for_local(...)`（`ed` = `trainer.eval_dispatch`）——那条路不许断。"""
    assert eval_dispatch_mod.hold_for_local is eval_yield_mod.hold_for_local


# ───────────────── ⑥ 边缘重指 + 判决点迁移（旧表达式必须消失） ─────────────────


def test_readers_take_the_verdicts_from_eval_yield() -> None:
    """判据读者从新家取（不再经 573 行的运行器）——逐模块逐名钉住。"""
    for rel, names in READERS.items():
        src = _import_sources(ROOT / rel)
        bad = sorted(n for n in names if src.get(n) != "worker.eval_yield")
        assert bad == [], f"{rel} 里这些名字的来源不是 biz.eval_yield：{bad}"


def test_eval_dispatch_no_longer_imports_policy_from_the_runner() -> None:
    """派发器只从运行器拿执行面（runner / 账本 / 重试配额），判据一律走新家。"""
    src = _import_sources(DISPATCH_FILE)
    for name in ("hold_for_local", "release_local_gate_if_starved", "EVAL_LOCAL_SLOTS_DEFAULT"):
        assert src.get(name) == "worker.eval_yield", name
    for name in ("run_local_eval_game", "eval_row", "eval_done_keys", "EVAL_TASK_ATTEMPTS"):
        assert src.get(name) == "worker.eval_local", name


def test_eval_dispatch_inline_formulas_are_gone() -> None:
    """三个判决点原先内联在 `run()` 闭包里：旧表达式必须消失（只有实现没有名字 = 本刀切的对象）。"""
    src = DISPATCH_FILE.read_text(encoding="utf-8")
    for old in (
        "                min(local_slots, total)\n                if (snapshot_path is not None",
        "time.time() >= deadline - EVAL_LOCAL_RELEASE_GRACE",
        "float(min(task_timeout, EVAL_INFLIGHT_GRACE_SEC))",
    ):
        assert old not in src, f"判决点没提干净：{old.strip()[:48]!r}"
    for new in (
        "reserved = reserve_local_slots(",
        "local_release_due(time.time(), deadline),",
        "grace_end = time.time() + inflight_grace_cap(task_timeout)",
    ):
        assert new in src, f"判决点没接上：{new!r}"


def test_loop_eval_no_longer_reaches_the_runner_for_policy() -> None:
    """边界侧（收拢 / 交棒 / 放行档）不得再 import 运行器——它只要判据。"""
    assert "worker.eval_local" not in _imports(LOOP_EVAL_FILE)


# ─────────────────────── ⑦ 语义（功能用例，逐条钉住） ───────────────────────


def test_hold_for_local_semantics() -> None:
    """节点「关门则让位」：只在预留区内 hold；gate 已放行 / 进宽限 / 无预留 / 队列空 ⇒ 不 hold。"""
    y = eval_yield_mod
    assert y.hold_for_local(2, 2, False, False) is True  # 余量 ≤ 预留 ⇒ 留给本机
    assert y.hold_for_local(3, 2, False, False) is False  # 余量超过预留 ⇒ 节点继续取
    assert y.hold_for_local(6, 2, True, False) is False  # gate 已放行
    assert y.hold_for_local(2, 2, False, True) is False  # 宽限期强制释放
    assert y.hold_for_local(0, 2, False, False) is False  # 队列空
    assert y.hold_for_local(6, 0, False, False) is False  # 份额 0 = 旧行为


def test_release_local_gate_semantics() -> None:
    """无远端节点时立刻开闸本机份额（2026-09-15 x3-power：否则 600s 零局）；有节点 / 无 gate 不动。"""
    y = eval_yield_mod
    gate = threading.Event()
    assert y.release_local_gate_if_starved(gate, []) is True
    assert gate.is_set() is True
    live = threading.Event()
    assert y.release_local_gate_if_starved(live, [{"id": "self"}]) is False
    assert live.is_set() is False
    assert y.release_local_gate_if_starved(None, []) is False


def test_release_plan_and_early_epoch_semantics() -> None:
    """放行档：本机不跑 PPO（远端 PPO / 整轮上云 / stream）⇒ immediate；否则末 early 个 epoch。"""
    y = eval_yield_mod
    assert y.local_gate_release_plan(
        ppo_remote=False, node_rollout=True, stream_round=False, early_epochs=1
    ) == "immediate"
    assert y.local_gate_release_plan(
        ppo_remote=True, node_rollout=False, stream_round=False, early_epochs=1
    ) == "immediate"
    assert y.local_gate_release_plan(
        ppo_remote=False, node_rollout=False, stream_round=True, early_epochs=1
    ) == "immediate"
    assert y.local_gate_release_plan(
        ppo_remote=False, node_rollout=False, stream_round=False, early_epochs=1
    ) == "last_epoch"
    assert y.local_gate_release_plan(
        ppo_remote=False, node_rollout=False, stream_round=False, early_epochs=0
    ) == "on_join"
    assert y.early_epoch_reached(2, 4, 1) is False  # 第 3 个 epoch 完成才到点
    assert y.early_epoch_reached(3, 4, 1) is True
    assert y.early_epoch_reached(1, 4, 0) is False  # 0 = 不放行
    assert y.early_epoch_reached(1, 4, 9) is True  # 提前量 > epochs 不为负


def test_join_soft_sec_and_tail_overran_semantics() -> None:
    """应急旋钮缺省 0（不站等）、坏值回落、负值夹 0；越窗判据以尾巴**自己的**窗口为基准。"""
    y = eval_yield_mod
    assert y.EVAL_JOIN_SOFT_SEC_DEFAULT == 0.0
    assert y.eval_join_soft_sec(None) == 0.0
    assert y.eval_join_soft_sec({"evalJoinSoftSec": 12.5}) == 12.5
    assert y.eval_join_soft_sec({"evalJoinSoftSec": -5}) == 0.0
    assert y.eval_join_soft_sec({"evalJoinSoftSec": "junk"}) == 0.0
    assert y.eval_join_soft_sec({"evalJoinSoftSec": float("nan")}) == 0.0
    assert y.eval_local_early_epochs({"evalLocalEarlyEpochs": 3}) == 3
    assert (
        y.eval_local_early_epochs({"evalLocalEarlyEpochs": "junk"})
        == y.EVAL_LOCAL_EARLY_EPOCHS_DEFAULT
    )
    assert y.eval_tail_overran(100.0, 1500.0, 200.0) is False
    assert y.eval_tail_overran(100.0, 1500.0, 1601.0) is True
    assert y.eval_tail_overran(100.0, 0.0, 99999.0) is False  # window<=0 = 无基准，不判越窗


def test_reserve_local_slots_semantics() -> None:
    """尾段预留量（原式逐项等价）：快照缺席 / gate 未接线 / 份额 0 ⇒ 0；否则 min(份额, 总数)。"""
    y = eval_yield_mod
    assert y.reserve_local_slots(10, 4, snapshot_ready=True, gate_wired=True) == 4
    assert y.reserve_local_slots(3, 4, snapshot_ready=True, gate_wired=True) == 3  # 不超过总数
    assert y.reserve_local_slots(10, 4, snapshot_ready=False, gate_wired=True) == 0
    assert y.reserve_local_slots(10, 4, snapshot_ready=True, gate_wired=False) == 0
    assert y.reserve_local_slots(10, 0, snapshot_ready=True, gate_wired=True) == 0
    assert y.EVAL_LOCAL_SLOTS_DEFAULT == 4  # 份额缺省（policy.evalLocalSlots 可覆写）


def test_local_release_due_semantics() -> None:
    """宽限强制释放点：`now >= deadline - EVAL_LOCAL_RELEASE_GRACE`（宽限期一进就放行）。"""
    y = eval_yield_mod
    deadline = 1000.0
    grace = y.EVAL_LOCAL_RELEASE_GRACE
    assert grace == 300
    assert y.local_release_due(deadline - grace - 1.0, deadline) is False
    assert y.local_release_due(deadline - grace, deadline) is True  # 边界含等号
    assert y.local_release_due(deadline + 5.0, deadline) is True


def test_inflight_grace_cap_semantics() -> None:
    """窗口到期后等「在飞局落账」的上界 = min(taskTimeoutSec, EVAL_INFLIGHT_GRACE_SEC)（兜底不空等）。"""
    y = eval_yield_mod
    assert y.EVAL_INFLIGHT_GRACE_SEC == 120
    assert y.inflight_grace_cap(900.0) == 120.0
    assert y.inflight_grace_cap(30.0) == 30.0
    assert y.inflight_grace_cap(120.0) == 120.0
