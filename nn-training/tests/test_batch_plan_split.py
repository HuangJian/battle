"""test_batch_plan_split — B 层「批语料规划 + 判据/门」出包（S25/B1）的契约守卫。

2026-09-25：`rl/batch_eval.py`（1805 行 / 35 顶层函数）拆它的**第一步 B1** —— 纯规划 +
判据/门（23 个成员）纯搬到新模块 `rl/batch_plan.py`（`plan/nn-training-refactor.md` §5.5.4）。

本文件钉的不是「行为」（那是既有用例的事，如 `test_batch_eval` / `test_verdict_corpus` /
`test_dist_common_poll`），而是**这次搬家的契约**：

  ① **定义唯一**：23 个成员只在 `rl/batch_plan.py` 里定义，旧家不再有同名定义；
  ② **门面再导出**：`rl.batch_eval.X is rl.batch_plan.X` 逐条恒等 —— 这是「既有
     `from rl.batch_eval import plan_units` 等调用点一行不改」的机器形式；
  ③ **纯函数面**：零锁、零台账读写（`_claim_locked` / `read_batches` / `write_batches` … 一个都不许
     出现）、零类、禁反向边（不得 import 旧家）、顶层 import 闭集；
  ④ **入边闭集**：旧家剩下的调用点恰好 6 条、归属者逐条对得上（AST 计真实 `Call`）；
  ⑤ **功能性（从新家调，不经门面）**：缺一个常量 import、常量算错、行内逻辑漂了都会当场红。

放在 `tests/` 而不是 `e2e/`：纯逻辑、无头、无网络、无真进程。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common
import rl.batch_eval as be
import rl.batch_plan as bp

RL = ROOT / "rl"
PLAN_PATH = RL / "batch_plan.py"
EVAL_PATH = RL / "batch_eval.py"

PLAN_SRC = PLAN_PATH.read_text(encoding="utf-8")
EVAL_SRC = EVAL_PATH.read_text(encoding="utf-8")

#: 搬走的 23 个成员：13 个函数 + 10 个常量（含 2 个私有名）。
MOVED_FUNCS = (
    "is_transient_error",
    "node_gate_reason",
    "kind_for_policy",
    "load_ladder",
    "plan_units",
    "corpora_path",
    "load_corpora",
    "corpus_doc",
    "plan_verdict_units",
    "units_for_batch",
    "_forces_of",
    "batch_iter_id",
    "select_next_unit",
)
MOVED_CONSTS = (
    "REPO_ROOT",
    "LADDER_JSON",
    "CORPORA_JSON",
    "LEVELS_DIR",
    "REGRESSION_EVERY",
    "BATCH_STAGE_BASE",
    "EVAL_SEED0",
    "SEGMENT_LEN",
    "KIND_FOR_POLICY",
    "_KIND_CHAR",
)
MOVED = MOVED_FUNCS + MOVED_CONSTS
#: 门面要再导出的 21 个**公开**名（2 个私有名只在批规划模块内部用）。
PUBLIC_MOVED = tuple(n for n in MOVED if not n.startswith("_"))
PRIVATE_MOVED = ("_forces_of", "_KIND_CHAR")

#: 旧家剩下的调用点（AST 计真实 `Call`；归属者 = 顶层 def / 类.方法，含闭包名）。
INBOUND_CALLS: dict[str, dict[str, int]] = {
    "batch_iter_id": {"BatchEvalRunner._run": 1},
    "is_transient_error": {"BatchEvalRunner._run.worker": 1},
    "kind_for_policy": {"BatchEvalRunner.__init__": 1},
    "node_gate_reason": {"BatchEvalRunner._run.bringup": 1},
    "select_next_unit": {"maybe_dispatch_batch": 1},
    "units_for_batch": {"maybe_dispatch_batch": 1},
}

#: 新家的顶层 import 闭集（多一个也红：新依赖必须显式登记在这里）。
#: `__future__` 是每个模块都有的 annotations 声明；`pathlib` / `rl.*` 与旧家同源。
PLAN_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "os",
    "pathlib",
    "dist_common",
    "rl.jsonc",
    "rl.queue",
}

#: 批规划模块里**不许出现**的名字（锁 / 台账 / 执行器 / 进程生命周期）。
FORBIDDEN_NAMES = {
    "_claim_locked",
    "_claim_guard",
    "read_batches",
    "write_batches",
    "read_requests",
    "read_done_req_ids",
    "mark_requests_done",
    "consume_requests",
    "claim_pending",
    "mark_unit_done",
    "_persist_of",
    "_requeue",
    "_reopen_for_resume",
    "BatchEvalRunner",
    "maybe_dispatch_batch",
    "dispatch_batch_bg",
    "data_root",
    "utc_now_iso",
    "acquire_lock",
    "threading",
    "functools",
    "subprocess",
    "torch",
}


# ────────────────────────── AST 小工具（语法事实用语法量）──────────────────────────


def _top_bound(src: str) -> set[str]:
    """顶层绑定的名字：def / class / 赋值目标。"""
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _calls_by_owner(src: str, names: set[str]) -> dict[str, dict[str, int]]:
    """{被调名: {归属者: 次数}} —— 归属者 = 顶层 def 名 / `类.方法`（闭包再缀一层函数名）。"""
    out: dict[str, dict[str, int]] = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in names:
            continue
        out.setdefault(node.func.id, {})
    owners: dict[str, list[str]] = {n: [] for n in out}

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, f"{owner}.{child.name}" if owner else child.name)
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in out
            ):
                owners[child.func.id].append(owner)
            walk(child, owner)

    walk(ast.parse(src), "")
    return {name: {o: owners[name].count(o) for o in sorted(set(owners[name]))} for name in out}


def _all_names(src: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}


def _top_imports(src: str) -> set[str]:
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _ok_ping() -> dict:
    return {
        "evalSupport": True,
        "stageJsonSupport": True,
        "bunVersion": "9.9.9",
        "cpus": 2,
        "codeHash": dist_common.compute_code_hash(),
    }


# ───────────────────────────── ① 定义唯一 / ② 门面恒等 ─────────────────────────────


def test_moved_members_are_defined_only_in_the_new_home() -> None:
    plan, old = _top_bound(PLAN_SRC), _top_bound(EVAL_SRC)
    for name in MOVED:
        assert name in plan, f"{name} 不在 rl/batch_plan.py"
        assert name not in old, f"{name} 仍定义在 rl/batch_eval.py"


def test_facade_reexports_are_the_same_objects() -> None:
    """门面再导出必须**对象级恒等**（不是同名副本）—— 这是零迁移的全部依据。"""
    for name in PUBLIC_MOVED:
        assert getattr(be, name) is getattr(bp, name), name


def test_private_members_are_not_reexported() -> None:
    """两个私有名只在批规划模块内部用，不该被门面再导出（少了才知道有人在越界取）。"""
    for name in PRIVATE_MOVED:
        assert not hasattr(be, name), name
        assert hasattr(bp, name), name


def test_no_classes_in_the_plan_module() -> None:
    """纯函数面：本模块不含任何类（台账/执行器的类都留在旧家，B2/B3 另有去处）。"""
    assert not [n for n in ast.parse(PLAN_SRC).body if isinstance(n, ast.ClassDef)]


# ───────────────────────── ③ 纯函数面：闭集 / 禁反向边 / 零锁 ─────────────────────────


def test_top_level_import_closure_is_exact() -> None:
    actual = _top_imports(PLAN_SRC)
    assert actual == PLAN_IMPORTS, {"多": sorted(actual - PLAN_IMPORTS), "少": sorted(PLAN_IMPORTS - actual)}


def test_no_back_edge_no_transport_no_torch() -> None:
    """不得 import 旧家（成环）、不得 import `remote`（传输层）、不得 import torch。

    「有没有反向引用」用 **AST** 判，不用文本搜：本模块的 docstring 里必然写着
    「既有 `from rl.batch_eval import plan_units` 一行不改」这类**合法散文**（S20 的教训：
    入边是语法事实，就该用语法量）。
    """
    actual = _top_imports(PLAN_SRC)
    assert "rl.batch_eval" not in actual
    assert not {m for m in actual if m.split(".")[0] in {"remote", "torch"}}
    tree = ast.parse(PLAN_SRC)
    attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "batch_eval" not in attrs
    assert "batch_eval" not in _all_names(PLAN_SRC)


def test_zero_lock_and_zero_ledger_access() -> None:
    """锁、台账读写、执行器、进程生命周期一个都不许出现在本模块（AST 级，不看注释）。"""
    hits = _all_names(PLAN_SRC) & FORBIDDEN_NAMES
    assert hits == set(), hits


def test_old_home_has_no_back_edge_to_new_home_members_beyond_the_import() -> None:
    """旧家不得再定义这些成员（只在 import 语句里出现）—— 防「顺手又写一份」。"""
    tree = ast.parse(EVAL_SRC)
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "rl.batch_plan":
            imported |= {a.name for a in node.names}
    assert imported == set(PUBLIC_MOVED), sorted(imported ^ set(PUBLIC_MOVED))


# ───────────────────────────── ④ 入边闭集（旧家剩下的调用点）─────────────────────────────


def test_inbound_call_sites_in_the_old_home_are_the_expected_six() -> None:
    got = _calls_by_owner(EVAL_SRC, set(MOVED_FUNCS))
    assert got == INBOUND_CALLS, got


def test_moved_constants_are_only_reexported_never_used_by_the_old_home() -> None:
    """旧家对搬走的常量**一个都不再读**（全部只经 `import … as …` 再导出）。

    B1 时旧家还读一次 `REPO_ROOT`（派生 `DEFAULT_DATA_ROOT`）；S26/B2 把这个派生也交给了
    `rl/batch_store.py`（与台账同住——它描述「存储」而不是「执行」）⇒ 现在旧家 = 零次。
    本用例仍然钉「常量有没有人真的在用」：`REPO_ROOT` 的使用者现在是 `batch_store.py`，
    数它只读一次（防「顺手到处派生」）。
    """
    loads = _calls_by_owner(EVAL_SRC, set())
    assert loads == {}
    names = [
        n.id
        for n in ast.walk(ast.parse(EVAL_SRC))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    ]
    for const in MOVED_CONSTS:
        assert names.count(const) == 0, const
    store_src = (RL / "batch_store.py").read_text(encoding="utf-8")
    store_names = [
        n.id
        for n in ast.walk(ast.parse(store_src))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    ]
    assert store_names.count("REPO_ROOT") == 1
    assert 'DEFAULT_DATA_ROOT = REPO_ROOT / "dashboard" / "data" / "evalboard"' in store_src


# ────────────────────────── ⑤ 功能性：从**新家**直接调（不经门面）──────────────────────────


def test_mirrored_constants_keep_their_values() -> None:
    """四个批规划常量是**双侧镜像**（`dashboard/src/evalboard/{runner,store}.ts`）。

    值本身就是契约，所以这里写**字面量**而不是拿常量比自己 —— 否则「改常量」会同时改掉
    断言两边，探针一跑就露出「存活的变异」（本刀实测就是这么发现的）。
    """
    assert bp.BATCH_STAGE_BASE == 2000
    assert bp.EVAL_SEED0 == 860001
    assert bp.SEGMENT_LEN == 100
    assert bp.REGRESSION_EVERY == 3


def test_plan_units_runs_from_the_new_home() -> None:
    """ladder 批规划：同 seg 当前 + 下一关，k%3==2 加回归位段 0（runner.ts 镜像）。"""
    ladder = bp.load_ladder()
    assert [r["id"] for r in ladder["rungs"]][:2] == ["c4l1", "c6l1"]
    u0 = bp.plan_units(ladder, 0, 0)
    assert [u["rung"] for u in u0] == ["c4l1", "c6l1"]
    assert all(u["seed0"] == 860001 and len(u["seeds"]) == 100 for u in u0)
    assert u0[0]["stageId"] == 2000
    assert len(u0[0]["stageJsonHash"]) == 16
    u2 = bp.plan_units(ladder, 0, 2)  # k % REGRESSION_EVERY == REGRESSION_EVERY - 1
    assert [u["rung"] for u in u2] == ["c4l1", "c6l1", "c4l1"]
    assert u2[2]["seed0"] == 860001  # 回归位钉死段 0
    u17 = bp.plan_units(ladder, 3, 17)
    assert u17[0]["seed0"] == 860101  # seg = 17 % 16 = 1 ⇒ 860001 + 100


def test_plan_verdict_units_runs_from_the_new_home() -> None:
    """判决批规划：一个 unit = 一个 (ckpt × 关卡)，跨 ckpt 同 seed 集（逐局配对）。"""
    corpus = bp.corpus_doc("v-ladder-c03-p400600")
    ckpts = [{"label": "bc", "path": "nn-training/weights/bc.json"}]
    units = bp.plan_verdict_units(corpus, ckpts)
    assert len(units) == 4  # ladder-c03 的 4 关
    u0 = units[0]
    assert u0["stageId"] == 2000  # 常量值见 test_mirrored_constants_keep_their_values
    assert u0["seeds"] == [corpus["seed0"] + i for i in range(corpus["games_per_stage"])]
    assert u0["ckpt"] == "nn-training/weights/bc.json" and u0["ckpt_label"] == "bc"
    assert u0["rung"].startswith("v-ladder-c03-p400600#")
    two = bp.plan_verdict_units(corpus, [*ckpts, {"label": "it30", "path": "w/it30.json"}])
    assert len(two) == 8 and two[4]["ckpt_label"] == "it30"
    assert [u["seeds"] for u in two[:4]] == [u["seeds"] for u in two[4:]]  # 同 seed 集
    # 空 ckpts ⇒ **空展开**（不是异常）；缺 path 的 ckpt 才响亮失败（"ckpts[].path"）。
    assert bp.plan_verdict_units(corpus, []) == []
    with pytest.raises(ValueError, match=r"ckpts\[\]\.path"):
        bp.plan_verdict_units(corpus, [{"label": "x"}])


def test_units_for_batch_dispatches_ladder_vs_verdict() -> None:
    ladder = bp.units_for_batch({"kind": "ladder", "ladder_pos": 0, "k_seq": 0})
    assert [u["rung"] for u in ladder] == ["c4l1", "c6l1"]
    verdict = bp.units_for_batch(
        {
            "kind": "verdict",
            "corpus": "v-ladder-c03-p400600",
            "ckpts": [{"label": "bc", "path": "nn-training/weights/bc.json"}],
        }
    )
    assert len(verdict) == 4 and all("ckpt" in u for u in verdict)
    with pytest.raises(ValueError, match="未知判决语料"):
        bp.units_for_batch({"kind": "verdict", "corpus": "nope", "ckpts": []})


def test_select_next_unit_runs_from_the_new_home() -> None:
    units = [{"rung": "c4l1"}, {"rung": "c6l1"}, {"rung": "c4l1"}]
    u, i, one = bp.select_next_unit(units, set(), ["c6l1"])
    assert [x["rung"] for x in u] == ["c6l1"] and i == 0 and one == {"rung": "c6l1"}
    assert bp.select_next_unit(units, {0}, None)[1] == 1
    assert bp.select_next_unit(units, {0, 1, 2}, None)[1] is None
    assert bp.select_next_unit(units, set(), ["nope"])[1] is None


def test_gate_and_classification_run_from_the_new_home() -> None:
    h = dist_common.compute_code_hash()
    ok = _ok_ping()
    assert bp.node_gate_reason(ok, "9.9.9", h) is None
    assert "evalSupport" in (bp.node_gate_reason({**ok, "evalSupport": False}, "9.9.9", h) or "")
    assert "bun" in (bp.node_gate_reason({**ok, "bunVersion": "1.0.0"}, "9.9.9", h) or "")
    assert "deadbeef" in (bp.node_gate_reason({**ok, "codeHash": "deadbeef"}, "9.9.9", h) or "")
    # 背压/瞬断：B 层是**纯转发**（判据单源仍在 dist_common）
    assert bp.is_transient_error(dist_common.DistError(503, "busy")) is True
    assert bp.is_transient_error(dist_common.DistError(409, "wver not cached")) is False
    assert bp.is_transient_error(ConnectionResetError(10054, "x")) is True
    # policy → 权重桶（未知回落 'rollout'，与旧调用方逐字一致）
    assert bp.kind_for_policy("nn") == "rollout" and bp.kind_for_policy("goal") == "goal"
    assert bp.kind_for_policy("nope") == "rollout"
    assert bp.KIND_FOR_POLICY["intent-exec"] == "intent"


def test_corpora_path_env_override_runs_from_the_new_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVALBOARD_CORPORA", str(tmp_path / "c.json"))
    assert bp.corpora_path() == tmp_path / "c.json"
    (tmp_path / "c.json").write_text(json.dumps({"version": "v1", "corpora": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="形态非法"):
        bp.load_corpora()


def test_batch_iter_id_namespace_from_the_new_home() -> None:
    assert bp.batch_iter_id("run1", "some-batch-id").startswith("run1.b")
    assert len(bp.batch_iter_id("run1", "x")) == len("run1.b") + 8


def test_forces_of_is_the_ladder_stage_json_helper() -> None:
    """`_forces_of` 是 `plan_units` 写 stageJson 的一环：私有，但影响 agent 的缓存键。"""
    assert bp._forces_of({"enemies": ["basic", "power", "player"]}) == "aca"
    assert bp._forces_of({"enemies": []}) == ""
