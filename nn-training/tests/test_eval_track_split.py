"""拆分的**契约守卫**：双轨种子分类 + 过拟合报警 + summary 结算永住 `biz/eval_track.py`（S5 第三刀，2026-09-27）。

`biz/eval_local.py` **870 → 573 行**；四段跨度（`EVAL_SEEDS` 池 · 双轨种子块 · `report_winrate_safe` ·
`_acc`/`_ratio`/`_read_recent_track_wrs`/`_maybe_warn_overfit`/`settle_eval_summary`）整块搬到
`biz/eval_track.py`（404 行）。本文件钉五件事：

1. **定义唯一**——这 23 个名字不许在 `eval_local.py` 里再实现一遍（否则「搬了一半」）；
2. **无环 / 方向**——`eval_track` 只依赖 stdlib + `biz.log`，且**不得** import `biz.eval_local`（反向边 = 环）；
3. **同一对象**——`eval_local` 的那些名字必须是 `eval_track` 的转发（不是副本）；
4. **搬走的语义没变**——双轨种子分段（锚点固定 / 轮转周期 3 / 无重叠）+ 过拟合判决（功能性用例）；
5. **它在分层里是 L1 纯逻辑**——不在 `remote_dag` 的传输账本里。

为什么单独成家：「双轨怎么分段、怎么判过拟合、summary 怎么结算」是**读账本算趋势**的纯逻辑，
与「怎么在本机/云上跑一局评估」（`eval_local` 的 `run_eval_runner_capture` / `run_local_eval_game`）
是两件事——读者（`gate_check` / `eval_a_once` / `remote/offline_eval`）要的是「双轨怎么算」，
不是「评估器怎么起」。分开后纯逻辑模块不拖入运行器。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.eval_local as eval_local_mod
import worker.eval_track as eval_track_mod
from tests.helpers import remote_dag as dag

TRACK_FILE = ROOT / "worker" / "eval_track.py"
LOCAL_FILE = ROOT / "worker" / "eval_local.py"

#: 本次搬走的**定义**（常量 / 函数）——只许在 `eval_track.py` 里出现。
MOVED_NAMES = {
    "DUAL_TRACK_ANCHOR",
    "DUAL_TRACK_ROTOR",
    "EVAL_SEEDS",
    "OVERFIT_GAP_PP",
    "OVERFIT_PERSIST_ROUNDS",
    "_ANCHOR_SEED_SET",
    "_ROTOR_SEED_SET",
    "_acc",
    "_maybe_warn_overfit",
    "_ratio",
    "_read_recent_track_wrs",
    "a_eval_seed_list",
    "dual_track_seeds",
    "is_anchor_seed",
    "is_rotor_seed",
    "overfit_fires",
    "overfit_gap_pp",
    "report_winrate_safe",
    "rotor_offset",
    "rotor_span",
    "settle_eval_summary",
    "should_dual_track",
    "split_anchor_rotor",
}

#: `eval_track.py` 允许的 import 面（stdlib + 本仓唯一的日志原语 `biz.log`；多一个即红）。
ALLOWED_IMPORTS = {"__future__", "json", "threading", "time", "collections.abc", "pathlib", "common.log"}


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
    assert _defined(TRACK_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(TRACK_FILE))
    leftovers = MOVED_NAMES & _defined(LOCAL_FILE)
    assert leftovers == set(), f"eval_local.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_eval_local_kept_the_execution_surface() -> None:
    """反向：运行器**留在** `eval_local.py`（不然这刀搬错了对象）。

    ★ 2026-09-27（S5 第十二刀）**改判**：本断言原先还写在 `hold_for_local` 上（当时的口径
    是「尾巴策略与运行器同属本机评估运行这一事务 ⇒ 留守」）。第十二刀按「独立所有者 +
    独立触发条件」把让位/份额（尾巴）策略整族搬到 `biz/eval_yield.py`（旧路径留 `X as X`
    门面）——「留守」名单随之收窄到执行面；该族的契约守卫在
    `tests/test_eval_yield_split.py`。当事口径见 `plan/nn-training-refactor.md` §5.7.2/§5.7.3。
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
    """名字是契约：旧的 `from biz.eval_local import settle_eval_summary`（多处在用）必须仍然成立。"""
    from worker.eval_local import dual_track_seeds, overfit_fires, settle_eval_summary

    assert dual_track_seeds is eval_track_mod.dual_track_seeds
    assert overfit_fires is eval_track_mod.overfit_fires
    assert settle_eval_summary is eval_track_mod.settle_eval_summary


# ─────────────────────── ④ 搬走的语义没变（功能性） ───────────────────────


def test_seed_pool_and_segment_split_semantics_unchanged() -> None:
    """池连续严格递增 · 锚点固定 · 轮转周期 3 · 两轨无重叠 —— 从 `eval_track` 直接读，逐条钉住。"""
    track = eval_track_mod
    assert tuple(sorted(set(track.EVAL_SEEDS))) == track.EVAL_SEEDS
    anchor = set(track.EVAL_SEEDS[: track.DUAL_TRACK_ANCHOR])
    # 锚点跨轮恒定、轮转段周期 3、且当轮两轨不相交（总量 = 锚点 + 轮转）。
    for it in (1, 2, 3, 4):
        seeds = track.dual_track_seeds(it)
        assert len(seeds) == track.DUAL_TRACK_ANCHOR + track.DUAL_TRACK_ROTOR
        assert set(seeds[: track.DUAL_TRACK_ANCHOR]) == anchor
        rotor = set(track.EVAL_SEEDS[i] for i in track.rotor_span(it))
        assert not (rotor & anchor)
    assert track.rotor_offset(1) == track.rotor_offset(4)


def test_overfit_verdict_semantics_unchanged() -> None:
    """过拟合判决：缺读数 / 轮数不足不响；持续 persist 轮且 gap ≥ 阈值才响。"""
    track = eval_track_mod
    assert track.overfit_fires([0.5] * 3, [0.5] * 3) is False
    assert track.overfit_fires([0.5] * 3, [None] * 3) is False
    assert track.overfit_fires([0.5] * 2, [0.4] * 2) is False  # 轮数 < persist
    assert track.overfit_fires([0.60] * 3, [0.50] * 3) is True
    # gap 是百分点、任一侧缺读数 → None（不伪装成 0）。
    assert track.overfit_gap_pp(0.6, 0.5) == 10.0
    assert track.overfit_gap_pp(None, 0.5) is None
    assert track.report_winrate_safe(None) is None
