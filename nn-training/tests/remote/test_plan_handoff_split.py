"""tests/remote/test_plan_handoff_split.py — S5 第十三刀守卫：`plan_run` 交接面下沉 `remote/plan_handoff`。

被钉的事实（六条，与既定守卫范式一致）：

  ① **定义唯一**：18 名交接面名字只在 `plan_handoff.py` 里实现（`plan_run` / `run_loop` 只做门面）；
  ② **依赖面闭集**：`plan_handoff` 只 import 白名单里的模块（多一个即红；白名单也不许腐化）；
  ③ **禁反向 import**：不得 import `remote.plan_run` / `remote.worker` / `remote.run_loop`（含延迟）；
  ④ **门面恒等**：`plan_run.X is plan_handoff.X`（18 名——`run_loop` 读到的仍是同一对象）；
  ⑤ **功能性**：`verify_plan_file` 四道门（sha / pairs_fp / start_it / 缺文件）——搬走不改语义；
  ⑥ **双命名空间事实**：`EVAL_ALTERNATE_WAIT_SEC` 的两个读点分家（`_maybe_cloud_eval` 读 `plan_run`、
     `_setup_cloud_eval` 读 `plan_handoff`），patch 一处不动另一处；写入面四槽守恒（AST 钉）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.plan_handoff as handoff_mod
import remote.plan_run as plan_run_mod
from common.protocol import ProtocolError
from remote.artifacts import sha256_bytes
from worker.plan import build_plan, dump_plan

HANDOFF_FILE = ROOT / "remote" / "plan_handoff.py"
ENGINE_FILE = ROOT / "remote" / "plan_run.py"
ENTRY_FILE = ROOT / "remote" / "run_loop.py"

#: 交接面的 19 名（与 `tests/remote/test_plan_run_split.py::HANDOFF_NAMES` 是同一份事实的两个方向：
#: 那边从「引擎划走」看，这里从「新家收下」看；改名/加名时必须一起改）。
HANDOFF_NAMES = {
    "EVAL_ALTERNATE_WAIT_SEC",
    "RunContext",
    "TS_CODE_ZIP_NAME",
    "TS_TREE_DIR",
    "_blob_roots",
    "_carry_ts_tree",
    "_eval_job_builder",
    "_eval_round_done",
    "_log_default",
    "_opt_bytes_from_manifest",
    "_read_opt_file",
    "_seed_demo_blob_cache",
    "_seed_opt_blob_cache",
    "_seed_ref_blob_cache",
    "_seed_start_checkpoint",
    "_setup_cloud_eval",
    "_stored_opt_sha",
    "_ts_tree_root",
    "open_run_context",
    "verify_plan_file",
}

#: `plan_handoff` 的允许 import 面（**闭集**）：stdlib 八 + 仓内九。
#: `remote.offline_eval` 是 `_setup_cloud_eval` 里的**函数内延迟 import**（随跨度逐字节搬来，
#: 云机 A 层评估才需要）；offline_eval 站在 L1 ⇒ L2 的 `plan_handoff` 依赖方向合法。
#:
#: 2026-10-07 加 `remote.deliver_proc`（`plan/offline-deliver-isolation.plan.md` P1）：装配点
#: 从它拿 `make_deliverer` / `DelivererLike`（进程模式那一面；另立模块是因为
#: `tests/test_python_loc_budget.py` 的单文件 <1000 代码行预算）。它是 L2：只向下依赖
#: `offline_deliver`(L1) + `common.protocol`(L0) ⇒ 对 `plan_handoff`(L5) 方向合法。
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "json",
    "pathlib",
    "shutil",
    "time",
    "typing",
    "common.logutil",
    "common.protocol",
    "common.platform_utils",
    "remote.artifacts",
    "remote.bundle",
    "remote.deliver_proc",
    "remote.offline_deliver",
    "remote.offline_eval",
    "worker.plan",
}


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


def _imports(path: Path) -> set[str]:
    """全部 import（含函数内延迟 import）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def _func_loads(path: Path, name: str) -> set[str]:
    """某个模块级函数内 `Name` 载入到的模块全局名（AST，不看文本）。"""
    tree = _tree(path)
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return {
        n.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }


def _quiet(_msg: str) -> None:  # 测试日志静音
    """"""


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 `tests/remote/test_run_loop.py` 同款）：默认 rotate 模式，argv 走真 `build_rollout_cmd`。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "0-3",
        "seed_rotate": 0,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_handoff_names_are_defined_only_in_the_new_home() -> None:
    """18 名只在 `plan_handoff` 里实现（`plan_run` / `run_loop` 只做门面转发）。"""
    assert _defined(HANDOFF_FILE) >= HANDOFF_NAMES, sorted(
        HANDOFF_NAMES - _defined(HANDOFF_FILE)
    )
    moved_back = HANDOFF_NAMES & _defined(ENGINE_FILE)
    assert moved_back == set(), f"交接面名字又在 plan_run 里实现：{sorted(moved_back)}"
    in_entry = HANDOFF_NAMES & _defined(ENTRY_FILE)
    assert in_entry == set(), f"run_loop 里仍在实现交接面名字：{sorted(in_entry)}"


# ─────────────────────── ② 依赖面闭集 ───────────────────────


def test_handoff_import_surface_is_closed() -> None:
    """只许 import 白名单（多一个即红——防悄悄长依赖）；白名单也不许腐化（反向钉）。"""
    got = _imports(HANDOFF_FILE)
    extra = sorted(got - ALLOWED_IMPORTS)
    assert extra == [], f"plan_handoff 长了新依赖：{extra}（要么论证后加白名单，要么别 import）"
    unused = sorted(ALLOWED_IMPORTS - got)
    assert unused == [], f"白名单腐化（这些已不在 import 面里）：{unused}"


# ─────────────────────── ③ 禁反向 import ───────────────────────


def test_handoff_never_imports_engine_or_worker() -> None:
    """交接面**零**引用 `remote.plan_run` / `remote.worker` / `remote.run_loop`（含函数内延迟）。"""
    imported = _imports(HANDOFF_FILE)
    back = sorted(
        m
        for m in imported
        if m.startswith(("remote.plan_run", "remote.worker", "remote.run_loop"))
    )
    assert back == [], f"plan_handoff 反向 import 了 {back}（成环）"


# ───────────────────────── ④ 门面恒等 ─────────────────────────


def test_engine_facade_forwards_the_same_objects() -> None:
    """`plan_run` 留的是 `X as X` 转发 ⇒ 18 名与交接面里是**同一对象**（不是副本）。"""
    for name in sorted(HANDOFF_NAMES):
        assert getattr(plan_run_mod, name) is getattr(handoff_mod, name), (
            f"remote.plan_run.{name} 不是 remote.plan_handoff.{name}（转发成了副本）"
        )


# ───────────────────────── ⑤ 功能性 ─────────────────────────


def test_verify_plan_file_three_gates(tmp_path: Path) -> None:
    """`verify_plan_file` 四面（sha / pairs_fp / start_it / 缺文件）——搬走不改语义。"""
    plan = build_plan(_args(), it=2, iters_total=5, rotate_seed=77, log=_quiet)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    raw = dump_plan(plan)
    (job_dir / "plan.json").write_bytes(raw)
    m = {"it": 2, "plan_sha256": sha256_bytes(raw)}

    got, sha = handoff_mod.verify_plan_file(job_dir, m, log=_quiet)
    assert got["end_it"] == plan["end_it"] and sha == sha256_bytes(raw)

    # ① sha 不符（payload 损坏/串包）
    with pytest.raises(ProtocolError) as ei:
        handoff_mod.verify_plan_file(job_dir, {**m, "plan_sha256": "0" * 64}, log=_quiet)
    assert "plan.json sha256" in str(ei.value)

    # ② 全段对集指纹不符（pair_args 与 hub 侧不一致 / build_pairs 漂移）
    tampered = {**plan, "pairs_fp": "1" * 64}
    (job_dir / "plan.json").write_bytes(dump_plan(tampered))
    with pytest.raises(ProtocolError) as ei2:
        handoff_mod.verify_plan_file(
            job_dir, {"it": 2, "plan_sha256": sha256_bytes(dump_plan(tampered))}, log=_quiet
        )
    assert "对集指纹不符" in str(ei2.value)

    # ③ 计划与 job 不是同一轮的交接
    (job_dir / "plan.json").write_bytes(raw)
    with pytest.raises(ProtocolError) as ei3:
        handoff_mod.verify_plan_file(job_dir, {"it": 3, "plan_sha256": sha256_bytes(raw)}, log=_quiet)
    assert "start_it" in str(ei3.value)

    # ④ 缺计划文件（手工递送时最容易漏的一样）
    (job_dir / "plan.json").unlink()
    with pytest.raises(ProtocolError) as ei4:
        handoff_mod.verify_plan_file(job_dir, m, log=_quiet)
    assert "plan.json" in str(ei4.value)


# ─────────────── ⑥ 双命名空间 / 槽位契约 ───────────────


def test_eval_alternate_wait_dual_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个读点分家：patch 一处**不动**另一处（`test_offline_eval_wiring` 打的是引擎家）。"""
    assert plan_run_mod.EVAL_ALTERNATE_WAIT_SEC is handoff_mod.EVAL_ALTERNATE_WAIT_SEC
    assert float(handoff_mod.EVAL_ALTERNATE_WAIT_SEC) == 300.0
    monkeypatch.setattr(plan_run_mod, "EVAL_ALTERNATE_WAIT_SEC", 0.3)
    assert float(handoff_mod.EVAL_ALTERNATE_WAIT_SEC) == 300.0  # 打引擎家 ⇒ 交接面读者不受影响
    monkeypatch.setattr(handoff_mod, "EVAL_ALTERNATE_WAIT_SEC", 1.5)
    assert float(plan_run_mod.EVAL_ALTERNATE_WAIT_SEC) == 0.3  # 打交接面 ⇒ 引擎读者不受影响


def test_dual_readers_read_their_own_home() -> None:
    """读点所在的**家**要分开：`_maybe_cloud_eval` 在引擎、`_setup_cloud_eval` 在交接面。"""
    assert "EVAL_ALTERNATE_WAIT_SEC" in _func_loads(ENGINE_FILE, "_maybe_cloud_eval")
    assert "EVAL_ALTERNATE_WAIT_SEC" in _func_loads(HANDOFF_FILE, "_setup_cloud_eval")
    assert "EVAL_ALTERNATE_WAIT_SEC" not in _func_loads(HANDOFF_FILE, "verify_plan_file")


def test_eval_slot_writers_live_in_handoff() -> None:
    """写入面四槽守恒：`_setup_cloud_eval` 写 `ctx.eval_on_cloud/eval_plan/eval_slots/eval_runner`；
    引擎侧三个读者仍在（装配在交接面、驱动/收线在引擎——两半通过槽位耦合）。"""
    tree = _tree(HANDOFF_FILE)
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_setup_cloud_eval"
    )
    writers = {
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.ctx, ast.Store)
        and isinstance(n.value, ast.Name)
        and n.value.id == "ctx"
    }
    assert {"eval_on_cloud", "eval_plan", "eval_slots", "eval_runner"} <= writers, sorted(writers)
    engine_src = ENGINE_FILE.read_text(encoding="utf-8")
    for reader in ("_maybe_cloud_eval", "runner_timeout", "_close_eval"):
        assert f"def {reader}(" in engine_src, f"引擎侧读者 {reader} 不见了（槽位契约要重判）"


# ─────────────── ⑦ 补传收线顺序（换实现不许动调用方）───────────────


def test_deliver_final_always_precedes_close_delivery() -> None:
    """`plan_run` 的收尾路径：`deliver_final` 必在 `close_delivery` **之前**（逐路径成对）。

    这是数据面契约：收线在前 = 段末摘要在 hub 侧缺失，而那就是 `submit_final` 存在的唯一理由
    （失败收尾那条还带更短的预算，`plan_run.py` 的三条路径 = `:492/498` · `:533/534` · `:544/550`）。
    换实现（进程内线程 → 独立子进程，2026-10-07）**不该改调用方** ⇒ 本闸钉在引擎源码上（不钉某个实现）。
    末尾两条计数断言防「空过」：三条路径 × 2 次调用。
    """
    seq: list[str] = []
    for fn in [n for n in ast.walk(_tree(ENGINE_FILE)) if isinstance(n, ast.FunctionDef)]:
        calls = sorted(
            (n.lineno, n.func.attr)
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("deliver_final", "close_delivery")
        )
        names = [name for _, name in calls]
        if not names:
            continue
        assert names[0] == "deliver_final", f"`{fn.name}` 先收线再投段末摘要：{names}"
        assert len(names) % 2 == 0, f"`{fn.name}` 的 final/close 不成对：{names}"
        for a, b in zip(names[0::2], names[1::2], strict=True):
            assert (a, b) == ("deliver_final", "close_delivery"), (
                f"`{fn.name}` 的补传收线顺序乱了（段末摘要会丢）：{names}"
            )
        seq += names
    assert seq.count("close_delivery") == 3, f"`plan_run` 的收尾路径数变了（现为 3）：{seq}"
    assert len(seq) == 6, f"final/close 配对数变了：{seq}"

