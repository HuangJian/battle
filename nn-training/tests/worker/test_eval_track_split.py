"""拆分的**契约守卫**：评估种子池 + summary 结算永住 `worker/eval_track.py`（S5 第三刀，2026-09-27）。

`worker/eval_local.py` 只留执行面（跑一局 / 读台账）。本文件钉五件事：

1. **定义唯一**——这些名字不许在 `eval_local.py` 里再实现一遍（否则「搬了一半」）；
2. **无环 / 方向**——`eval_track` 只依赖 stdlib + `common.log`，且**不得** import `eval_local`；
3. **同一对象**——`eval_local` 的公开名字必须是 `eval_track` 的转发（不是副本）；
4. **搬走的语义没变**——固定池外种子（与 rollout 抽签区间无交）+ summary 结算（功能性用例）；
5. **它在分层里是 L1 纯逻辑**——不在 `remote_dag` 的传输账本里。

为什么单独成家：「评估用哪些种子、summary 怎么结算」是**读账本算趋势**的纯逻辑，与「怎么在
本机/云上跑一局评估」是两件事——读者（`gate_check` / `eval_a_once` / `remote/offline_eval`）
要的是「口径怎么算」，不是「评估器怎么起」。分开后纯逻辑模块不拖入运行器。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.eval_local as eval_local_mod
import worker.eval_track as eval_track_mod
from tests.helpers import remote_dag as dag

TRACK_FILE = ROOT / "worker" / "eval_track.py"
LOCAL_FILE = ROOT / "worker" / "eval_local.py"

#: 本次搬走的**定义**（常量 / 函数）——只许在 `eval_track.py` 里出现，且在 `eval_local.py`
#: 只做 `X as X` 门面（同一对象）。
MOVED_NAMES = {
    "EVAL_SEEDS",
    "_acc",
    "_ratio",
    "a_eval_seed_list",
    "report_winrate_safe",
    "settle_eval_summary",
}

#: 不住门面的内部口径（只有 `eval_track` 自己有，读者直接从这个模块取）。
TRACK_ONLY_NAMES = {
    "EVAL_DAILY_SEEDS",
    "EVAL_SEEDS_BASE",
    "MISSING_MAX",
    "ROLLOUT_SEED_MAX",
    "_missing_reasons",
    "lost_by_node",
}

#: `eval_track.py` 允许的 import 面（stdlib + 本仓唯一的日志原语 `biz.log`；多一个即红）。
#: ⚠ `typing` 是 **stdlib**（Protocol 的形状声明用）——放进 stdlib 那一侧**不放宽**仓内依赖面
#: （仓内依赖仍然只有 `common.log` 一个）；「纯逻辑底座不许拖进运行器/torch/numpy」的闸不变。
ALLOWED_IMPORTS = {
    "__future__",
    "json",
    "threading",
    "time",
    "collections.abc",
    "pathlib",
    "typing",
    "common.log",
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


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_moved_names_are_defined_in_eval_track_and_not_redefined_in_eval_local() -> None:
    """定义唯一：搬走的名字只在 `eval_track.py` 里实现（`eval_local.py` 再定义一遍 = 搬了一半）。"""
    assert _defined(TRACK_FILE) >= MOVED_NAMES | TRACK_ONLY_NAMES, sorted(
        (MOVED_NAMES | TRACK_ONLY_NAMES) - _defined(TRACK_FILE)
    )
    leftovers = MOVED_NAMES & _defined(LOCAL_FILE)
    assert leftovers == set(), f"eval_local.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_eval_local_kept_the_execution_surface() -> None:
    """反向：运行器**留在** `eval_local.py`（不然这刀搬错了对象）。

    ★ 2026-09-27（S5 第十二刀）**改判**：本断言原先还写在 `hold_for_local` 上（当时的口径
    是「尾巴策略与运行器同属本机评估运行这一事务 ⇒ 留守」）。第十二刀按「独立所有者 +
    独立触发条件」把让位/份额（尾巴）策略整族搬到 `biz/eval_yield.py`（旧路径留 `X as X`
    门面）——「留守」名单随之收窄到执行面；该族的契约守卫在
    `tests/worker/test_eval_yield_split.py`。当事口径见 `plan/nn-training-refactor.md` §5.7.2/§5.7.3。
    """
    defined = _defined(LOCAL_FILE)
    for name in ("run_local_eval_game", "run_eval_runner_capture", "eval_done_keys"):
        assert name in defined, f"eval_local.py 丢了执行面 {name}"


# ─────────────────────── ② 依赖方向：stdlib(+biz.log)，无环 ───────────────────────


def test_eval_track_imports_only_stdlib_and_the_log_primitive() -> None:
    """纯逻辑模块：不得 import `remote.*` / torch / numpy（`biz.log` 是唯一允许的仓内依赖）。"""
    extra = sorted(_imports(TRACK_FILE) - ALLOWED_IMPORTS)
    assert extra == [], f"worker/eval_track.py 引入了允许面之外的依赖：{extra}"


def test_eval_track_never_imports_eval_local() -> None:
    """★ 本刀的意义：`eval_track` 是**底座**，反向 import 运行器立刻成环。"""
    back = sorted(m for m in _imports(TRACK_FILE) if m.startswith("worker.eval_local"))
    assert back == [], f"worker/eval_track.py 反向 import 了运行器：{back}"


def test_eval_track_stays_pure_logic() -> None:
    """纯逻辑（不达传输面）：账本里它没有任何通往 `remote.*` 的路径。

    2026-09-30（刀 6）：判据从「不在 `remote_dag` 的账本里」改成**可达性**——
    `worker/` 整包入账之后，前者的写法恒为假（哑守卫）。
    """
    assert dag.reaches_transport("worker.eval_track") is False


# ───────────────────────── ③ 门面 ─────────────────────────


def test_eval_local_facade_forwards_the_same_objects() -> None:
    """门面是 `X as X` 转发 ⇒ 与 `eval_track` 里是**同一个对象**（不是副本）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(eval_local_mod, name), f"biz.eval_local 丢了门面 {name}"
        assert getattr(eval_local_mod, name) is getattr(eval_track_mod, name), (
            f"biz.eval_local.{name} 不是 biz.eval_track.{name}（转发成了副本）"
        )


def test_public_call_sites_can_still_import_from_eval_local() -> None:
    """名字是契约：旧的 `from worker.eval_local import settle_eval_summary`（多处在用）必须仍然成立。"""
    from worker.eval_local import a_eval_seed_list, settle_eval_summary

    assert a_eval_seed_list is eval_track_mod.a_eval_seed_list
    assert settle_eval_summary is eval_track_mod.settle_eval_summary


# ─────────────────── ④ 搬走的语义没变（功能性） ───────────────────────


def test_seed_pool_semantics_unchanged() -> None:
    """池外固定池：连续严格递增、与 rollout 抽签区间无交、切片与 it 无关 —— 逐条钉住。"""
    track = eval_track_mod
    assert tuple(sorted(set(track.EVAL_SEEDS))) == track.EVAL_SEEDS
    assert len(track.EVAL_SEEDS) == 200
    assert track.EVAL_SEEDS[0] > track.ROLLOUT_SEED_MAX == 2**30
    corpus = track.a_eval_seed_list(1, 100)
    for it in (2, 3, 40):
        assert track.a_eval_seed_list(it, 100) == corpus
    assert track.report_winrate_safe(None) is None
