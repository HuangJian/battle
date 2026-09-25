"""test_batch_runner_split — B 层执行面出包的契约守卫（S27/B3，plan §5.5.4 第三步）。

2026-09-25：`rl/batch_eval.py` 拆分的第三步 —— `BatchEvalRunner` / `dispatch_batch_bg`
（连同它独占的五个常量与 `_heartbeat`）**纯搬**到 `rl/batch_runner.py`。

本文件钉两类东西，两者都不是「搬得对」（逐字节对账在 `tmp/verify_b3.py`），而是
**搬完之后仍然成立、且只有搬完才需要守**的契约：

  ① **结构契约**：定义唯一 · 门面对象级恒等 · 执行器独占符号**不**经门面转发（响亮
     AttributeError，而不是「名字还在、没人读」的静默空操作）· 无反向边 · import 闭集 ·
     台账零手写（写面只有 `BatchStore`）。
  ② **★ 注入点契约（B3 的真理由）**：执行器的依赖注入靠**模块全局**，所以 `log` /
     `bun_version` / `run_local_eval_game` 与五个常量必须在**本模块**里被**裸名**读取 ——
     测试 `monkeypatch.setattr("rl.batch_runner.X")` 才生效；打在旧家 = 静默空操作。
     本文件按 AST 钉「裸名调用/读取」，这比「跑一遍看有没有效果」更早、更准。
  ③ **功能性**：从**新家**直接调（不经门面）—— 心跳写点 + 失败静默 · `dispatch_batch_bg`
     起的线程 · `_done_keys` 的读盘口径。

放在 `tests/` 而不是 `e2e/`：纯逻辑、无头、无网络、无真节点。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rl.batch_eval as be
import rl.batch_runner as br

RL = ROOT / "rl"
RUNNER_PATH = RL / "batch_runner.py"
EVAL_PATH = RL / "batch_eval.py"
RUNNER_SRC = RUNNER_PATH.read_text(encoding="utf-8")
EVAL_SRC = EVAL_PATH.read_text(encoding="utf-8")

#: 搬进执行器模块的成员：2 个公开名 + 1 个私有名 + 5 个执行器独占常量。
MOVED_PUBLIC = ("BatchEvalRunner", "dispatch_batch_bg")
MOVED_PRIVATE = ("_heartbeat",)
MOVED_CONSTS = (
    "BUSY_BACKOFF_CAP_SEC",
    "STUCK_GRACE_SEC",
    "RECOVER_PING_SEC",
    "NO_CONSUMER_GRACE_SEC",
    "NODE_RECOVERY_TRIES",
)
MOVED = MOVED_PUBLIC + MOVED_PRIVATE + MOVED_CONSTS

#: **不**经门面转发的符号（只被本模块读 ⇒ 转发只会制造「静默空操作」的假象）。
NOT_FORWARDED = MOVED_PRIVATE + MOVED_CONSTS

#: 执行器的依赖注入点（模块全局）。裸名读 = patch 生效；限定名读 = patch 静默失效。
DI_NAMES = ("log", "bun_version", "run_local_eval_game")

#: 新家的顶层 import 闭集（多一个也红：新依赖必须显式登记在这里）。
RUNNER_IMPORTS = {
    "__future__",
    "collections",
    "hashlib",
    "json",
    "pathlib",
    "shutil",
    "threading",
    "time",
    "typing",
    "dist_common",
    "rl.batch_plan",
    "rl.batch_store",
    "rl.eval_local",
    "rl.log",
    "rl.queue",
    "rl.queue_local",
}


# ───────────────────────────────── AST 小工具 ─────────────────────────────────


def _top_bound(src: str) -> set[str]:
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


def _top_imports(src: str) -> set[str]:
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _callee_forms(src: str, names: tuple[str, ...]) -> tuple[dict[str, int], dict[str, int]]:
    """(裸名调用计数, 限定名调用计数) —— 依赖注入能不能被 patch 就取决于前者。"""
    bare: dict[str, int] = {n: 0 for n in names}
    qualified: dict[str, int] = {n: 0 for n in names}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in bare:
            bare[f.id] += 1
        elif isinstance(f, ast.Attribute) and f.attr in qualified:
            qualified[f.attr] += 1
    return bare, qualified


def _class_member_src(src: str, cls: str) -> str:
    """一个类的全部成员源码拼接（AST unparse）—— 用来判「成员里怎么读常量」。"""
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return "\n".join(ast.unparse(m) for m in node.body)
    raise AssertionError(f"{cls} 不在 {RUNNER_PATH.name}")


def _load_names_in(fragment: str) -> set[str]:
    """片段里被**读取**的裸名（`Load` 上下文）。"""
    return {
        n.id
        for n in ast.walk(ast.parse(fragment))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }


# ───────────────────────── ① 结构：定义唯一 / 门面恒等 ─────────────────────────


def test_moved_members_are_defined_only_in_the_new_home() -> None:
    runner, old = _top_bound(RUNNER_SRC), _top_bound(EVAL_SRC)
    for name in MOVED:
        assert name in runner, f"{name} 不在 rl/batch_runner.py"
        assert name not in old, f"{name} 仍定义在 rl/batch_eval.py"


def test_facade_reexports_are_the_same_objects() -> None:
    """门面再导出必须**对象级恒等**（不是同名副本）—— 这是零迁移的全部依据。"""
    for name in MOVED_PUBLIC:
        assert hasattr(be, name), name
        assert getattr(be, name) is getattr(br, name), name


def test_runner_only_symbols_are_not_forwarded_by_the_facade() -> None:
    """执行器独占的符号**不**转发 ⇒ `rl.batch_eval.<X>` 响亮 AttributeError。

    S16/S19 记过两次的同款坑：名字还留着（转发一份）时，`monkeypatch.setattr` 打在旧家
    会**静默**变成空操作 —— 单测仍绿、生产不生效。这里正面钉「不许有这条路」。
    """
    for name in NOT_FORWARDED:
        assert hasattr(br, name), f"{name} 应住 rl/batch_runner"
        assert not hasattr(be, name), f"{name} 不该经门面转发（patch 会静默失效）"


def test_no_back_edge_to_the_old_home() -> None:
    """不得 import 旧家（成环）—— 用 **AST** 判，不用文本搜。

    本模块 docstring 里必然写着「`rl.batch_eval` 反过来再导出本模块的公开名」这类
    **合法散文**（S20 的教训：入边是语法事实，就该用语法量）。
    """
    actual = _top_imports(RUNNER_SRC)
    assert "rl.batch_eval" not in actual
    attrs = [n.attr for n in ast.walk(ast.parse(RUNNER_SRC)) if isinstance(n, ast.Attribute)]
    assert "batch_eval" not in attrs


def test_top_level_import_closure_is_exact() -> None:
    actual = _top_imports(RUNNER_SRC)
    assert actual == RUNNER_IMPORTS, {
        "多": sorted(actual - RUNNER_IMPORTS),
        "少": sorted(RUNNER_IMPORTS - actual),
    }


def test_ledger_is_never_written_by_hand() -> None:
    """执行器不许自己碰台账文件：写面只剩 `BatchStore` 的具名转移（B2 的成果）。

    两处语法量：① 不得出现 `_publish`（store 的唯一写点）；② 不得有 `x["status"] = …`
    —— 「状态机的谁都能改」正是 B2 要消灭的东西，别从执行器溜回来。
    """
    assert "_publish" not in _load_names_in(RUNNER_SRC)
    src_names = {n.id for n in ast.walk(ast.parse(RUNNER_SRC)) if isinstance(n, ast.Name)}
    assert "write_batches" not in src_names
    for node in ast.walk(ast.parse(RUNNER_SRC)):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "status"
                ):
                    raise AssertionError("执行器直接写台账 status —— 应走 BatchStore 的具名转移")


# ───────────────────── ② ★ 注入点：裸名读取（patch 生效的前提）─────────────────────


def test_di_names_are_bound_and_called_bare_in_the_runner_module() -> None:
    """三个注入点必须是**本模块的全局**、且以**裸名**调用。

    `monkeypatch.setattr("rl.batch_runner.bun_version", …)` 只改本模块的全局；调用点若写成
    `dist_common.bun_version(...)` 一类限定名，patch 就**静默失效**。这里两种形态都数。
    """
    bare, qualified = _callee_forms(RUNNER_SRC, DI_NAMES)
    assert all(n > 0 for n in bare.values()), bare
    assert all(n == 0 for n in qualified.values()), qualified
    for name in DI_NAMES:
        assert hasattr(br, name), f"{name} 没绑进 rl.batch_runner（patch 会静默失效）"


def test_runner_constants_are_read_bare_inside_the_runner_classes() -> None:
    """五个常量的读取必须发生在**执行侧的类成员**里且是裸名 —— 它们只被执行侧读。

    2026-09-25（S31/B5b）：执行侧现在有两个类 —— 开单元/收尾住 `BatchEvalRunner`，通道机器住
    `_UnitLanes`（常量读取随机器搬过去：`BUSY_BACKOFF_CAP_SEC` / `STUCK_GRACE_SEC`）。
    只扫旧类会**静默**退化成「3/5」—— 与 `test_batch_plan_split` 的入边归属者同型。
    两个类都在同一模块 ⇒ 裸名 patch 锚点（`rl.batch_runner.X`）不迁移。
    """
    loads: set[str] = set()
    for cls in ("BatchEvalRunner", "_UnitLanes"):
        loads |= _load_names_in(_class_member_src(RUNNER_SRC, cls))
    missed = [c for c in MOVED_CONSTS if c not in loads]
    assert missed == [], missed


def test_constructor_call_in_dispatch_is_a_bare_global() -> None:
    """`dispatch_batch_bg` 里的构造调用必须是裸名 `BatchEvalRunner` —— 工作调度靠它打桩。"""
    frag = "\n".join(
        ast.unparse(n)
        for n in ast.parse(RUNNER_SRC).body
        if isinstance(n, ast.FunctionDef) and n.name == "dispatch_batch_bg"
    )
    calls = [
        n.func
        for n in ast.walk(ast.parse(frag))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "BatchEvalRunner"
    ]
    assert calls, "dispatch_batch_bg 没有裸名构造 BatchEvalRunner"


# ────────────────────────── ③ 功能性：从新家直接调 ──────────────────────────


def test_heartbeat_writes_state_and_swallows_failure(monkeypatch) -> None:
    """心跳写点从新家走；失败必须**静默**（心跳绝不打断单元）。"""
    import rl.eval_heartbeat as hb

    seen: list[dict] = []
    monkeypatch.setattr(hb, "write_state", lambda **kw: seen.append(kw))
    br._heartbeat(batch_id="b1", rung=None, remaining_units=0)
    assert seen == [{"batch_id": "b1", "rung": None, "remaining_units": 0}]

    def boom(**kw):
        raise RuntimeError("board down")

    monkeypatch.setattr(hb, "write_state", boom)
    br._heartbeat(batch_id="b1")  # 不抛（心跳绝不打断单元）


def test_dispatch_batch_bg_starts_a_daemon_thread_from_the_new_home(monkeypatch) -> None:
    calls: dict = {}

    class FakeRunner:
        def __init__(self, *a, **k):
            calls["args"] = a

        def run(self) -> dict:
            calls["ran"] = True
            return {}

    monkeypatch.setattr(br, "BatchEvalRunner", FakeRunner)
    t = br.dispatch_batch_bg(
        "bun",
        None,
        Path("/nonexistent/eval_log.jsonl"),
        object(),
        {},
        {"batch_id": "b1"},
        {"rung": "c4l1"},
        0,
        1,
        "run-1",
        "epoch-1",
    )
    t.join(timeout=10)
    assert calls.get("ran") is True
    assert t.daemon is True
    assert t.name == "batcheval-u0"
    assert calls["args"][0] == "bun" and calls["args"][6] == {"rung": "c4l1"}


def test_done_keys_reads_the_eval_log_from_the_new_home(tmp_path: Path) -> None:
    """`_done_keys` 的读盘口径（坏行跳过、只认同 wver 的 eval 行）从新家生效。"""
    r = br.BatchEvalRunner.__new__(br.BatchEvalRunner)
    r.eval_log = tmp_path / "eval_log.jsonl"
    r.eval_log.write_text(
        "\n".join(
            [
                json.dumps({"event": "eval", "wver": "aa", "stage": 3, "seed": 7}),
                "{ not json",
                json.dumps({"event": "eval", "wver": "bb", "stage": 3, "seed": 8}),
                json.dumps({"event": "eval", "wver": "aa", "stage": "x", "seed": 9}),
                json.dumps({"event": "verdict", "wver": "aa", "stage": 1, "seed": 1}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert br.BatchEvalRunner._done_keys(r, "aa") == {(3, 7)}
    assert br.BatchEvalRunner._done_keys(r, "bb") == {(3, 8)}
    assert br.BatchEvalRunner._done_keys(r, "cc") == set()
