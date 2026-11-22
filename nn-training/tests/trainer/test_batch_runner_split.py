"""test_batch_runner_split — B 层执行面出包的契约守卫（S27/B3，plan §5.5.4 第三步）。

2026-09-25：`trainer/batch_eval.py` 拆分的第三步 —— `BatchEvalRunner` / `dispatch_batch_bg`
（连同它独占的五个常量与 `_heartbeat`）**纯搬**到 `trainer/batch_runner.py`。

本文件钉两类东西，两者都不是「搬得对」（逐字节对账在 `tmp/verify_b3.py`），而是
**搬完之后仍然成立、且只有搬完才需要守**的契约：

  ① **结构契约**：定义唯一 · 门面对象级恒等 · 执行器独占符号**不**经门面转发（响亮
     AttributeError，而不是「名字还在、没人读」的静默空操作）· 无反向边 · import 闭集 ·
     台账零手写（写面只有 `BatchStore`）。
  ② **★ 注入点契约（B3 的真理由）**：执行器的依赖注入靠**模块全局**，所以 `log` /
     `TOOLCHAIN` / `run_local_eval_game` 与五个常量必须在**本模块**里被**裸名**读取 ——
     测试 `monkeypatch.setattr("trainer.batch_runner.X")` 才生效；打在旧家 = 静默空操作。
     本文件按 AST 钉「裸名调用/读取」，这比「跑一遍看有没有效果」更早、更准。
     `bun_version` 自 2026-11-21（§P0-8）起走 `common.proc.Toolchain` 端口：裸名读的是
     `TOOLCHAIN`，版本探测是它的字段（`TOOLCHAIN.bun_version(...)`）。
  ③ **功能性**：从**新家**直接调（不经门面）—— 心跳写点 + 失败静默 · `dispatch_batch_bg`
     起的线程 · `_done_keys` 的读盘口径。

**2026-11-20 第二次换家**：通道机器（`_UnitLanes` / `_UnitPlan`）连同它独占的两个常量整块搬去
`trainer/batch_lanes.py`（本模块 LOC 顶在 999/1000，收尾的 `UnitLedger` 没地方落）。契约随之变
**两家对照**：定义唯一、注入点裸名、常量读取都按「谁裸名读、谁必须绑定」逐家判，且**交叉转发
也算红**（`br.STUCK_GRACE_SEC` / `bl.TOOLCHAIN` 那种转发 = patch 打哪家都可能静默空操作）。

放在 `tests/` 而不是 `e2e/`：纯逻辑、无头、无网络、无真节点。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.proc
import common.runtime
import trainer.batch_eval as be
import trainer.batch_lanes as bl
import trainer.batch_runner as br

RL = ROOT / "trainer"
RUNNER_PATH = RL / "batch_runner.py"
LANES_PATH = RL / "batch_lanes.py"
EVAL_PATH = RL / "batch_eval.py"
RUNNER_SRC = RUNNER_PATH.read_text(encoding="utf-8")
LANES_SRC = LANES_PATH.read_text(encoding="utf-8")
EVAL_SRC = EVAL_PATH.read_text(encoding="utf-8")

#: 搬进执行器模块的成员：2 个公开名 + 1 个私有名 + 5 个执行器独占常量。
MOVED_PUBLIC = ("BatchEvalRunner", "dispatch_batch_bg")
MOVED_PRIVATE = ("_heartbeat",)
#: 2026-11-20：五个执行侧常量跟着「谁读它」分两家 —— 开单元/收尾那三个留在执行器，
#: 通道机器独占的两个（背压上限 / 收尾僵死宽限）随机器搬去 `trainer/batch_lanes.py`。
RUNNER_CONSTS = (
    "RECOVER_PING_SEC",
    "NO_CONSUMER_GRACE_SEC",
    "NODE_RECOVERY_TRIES",
)
LANES_CONSTS = (
    "BUSY_BACKOFF_CAP_SEC",
    "STUCK_GRACE_SEC",
)
MOVED_CONSTS = (*RUNNER_CONSTS, *LANES_CONSTS)
MOVED = (*MOVED_PUBLIC, *MOVED_PRIVATE, *MOVED_CONSTS)
#: 机器的契约（机器 + 入参契约 + 收尾台账）也住新家：旧家一个都不许再定义。
LANES_OWNED = ("_UnitLanes", "_UnitPlan", "UnitLedger", *LANES_CONSTS)

#: **不**经门面转发的符号（只被本模块读 ⇒ 转发只会制造「静默空操作」的假象）。
NOT_FORWARDED = (*MOVED_PRIVATE, *MOVED_CONSTS)

#: 依赖注入点（模块全局），按**读它的模块**分家。裸名读 = patch 生效；限定名读 = 静默失效。
RUNNER_DI = ("log",)
LANES_DI = ("log", "_record_agent_meta", "run_local_eval_game")
DI_NAMES = (*RUNNER_DI, *LANES_DI)

#: 新家的顶层 import 闭集（多一个也红：新依赖必须显式登记在这里）。
RUNNER_IMPORTS = {
    "__future__",
    # 2026-11-22（P0-3 第四十三刀）：`RunnerSpec` 是 frozen + kw_only 的参数对象
    # （stdlib `dataclasses`，零新依赖边）。
    "dataclasses",
    "hashlib",
    "json",
    "pathlib",
    "shutil",
    "threading",
    "time",
    "common.distribution",
    # 2026-11-17（§P1-6 第四批）：单元线程归 `Runtime`（`batcheval-u<idx>-*`），
    # `common.runtime` 是 L0、stdlib-only 的并发所有者。
    "common.runtime",
    # 2026-11-16 登记（B8 第一刀）：codeHash 实现搬到 `common/codehash.py`，
    # `compute_code_hash()` 的入边随之拆成两条；其余分发面仍走 `common.distribution`。
    "common.codehash",
    # 2026-11-19 登记（B8 第十一刀）：取任务 / 结果校验 → `common.task_client`，
    # 权重下发 → `common.weights_client`；执行器按新家 import（`node_request` 漏斗与
    # `load_dist_config` 仍在 `common.distribution`）。
    "common.task_client",
    "common.weights_client",
    # 2026-11-15 登记：节点门参考 hash 改以 self 自报为准（rollout/eval/batch/BC 四条腿
    # 共用），判据单独成模块 —— `common/distribution.py` 已到 LOC 预算。
    "common.codehash_ref",
    # 2026-10-15 登记（离线 delta 事故）：nn 腿读盘前先做**读侧落地**
    # （`remote/weights_standard.read_standard` —— 活动权重可能是离线腿回传的 delta 容器
    # `BWD1`，hub 原样落位；容器会让本机指纹与节点 cache 键对不上、节点侧 `JSON.parse` 直接报
    # `Unexpected identifier "BWD1"`）。顶层名是**裸包 `remote`**。
    "remote",
    # 2026-11-14：规划面（B 层纯函数）搬去 `biz/`（见 DECISIONS
    # §2026-11-14-batch-plan-belongs-to-biz）—— 执行器照旧从**本模块**裸名取用。
    "biz.batch_plan",
    "trainer.batch_store",
    # 2026-09-27（S5 第十二刀）登记：本机份额缺省 `EVAL_LOCAL_SLOTS_DEFAULT` 随「让位/
    # 份额」族搬到 `biz/eval_yield.py` —— 执行器从判决面取份额，不再为一条缺省拖入运行器
    # （运行器那行仍在：`run_local_eval_game` 是本机槽位的执行面）。
    "worker.eval_yield",
    "common.log",
    # 2026-11-14 登记（`trainer.dispatch_queue` 的 re-export 面退役）：`bun_version` 回
    # `common.proc`（缺省 fallback 就是 `"?"`）。`_record_agent_meta` 自 2026-11-20 起只被
    # 机器读 ⇒ 它绑在 `trainer.batch_lanes`（`monkeypatch.setattr(br, …)` 已失效）。
    "common.proc",
    # 2026-11-20：通道机器出包后，执行器只多一条**出边**（机器在 L0/L1 层，单向无环）；
    # `worker.agent_meta` / `trainer.queue_local` / `common.fs` / `collections` / `typing` /
    # `worker`（`node_identity`）/ `worker.eval_local`（五个注入名）随之搬去那个模块。
    "trainer.batch_lanes",
}

#: 机器的顶层 import 闭集（同样多一个也红）。
LANES_IMPORTS = {
    "__future__",
    "collections",
    "dataclasses",
    "pathlib",
    "threading",
    "time",
    "typing",
    "biz.batch_plan",
    "common.distribution",
    "common.fs",
    "common.log",
    "common.runtime",
    "common.task_client",
    "common.weights_client",
    "trainer.queue_local",
    # 裸包 `worker` = `from worker import node_identity`（门指纹的同一性账本）。
    "worker",
    "worker.agent_meta",
    "worker.eval_local",
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
    """顶层 import 的**全点分名**集合（两种形状同口径）。

    `import a.b` 取 `a.b`，`from a.b import c` 也取 `a.b` —— 两条都记全路径。
    2026-09-30（刀 2）修正：原先 `ast.Import` 那一支取 `a.name.split(".")[0]`，
    只在「顶层模块都是单段名」时与 `ImportFrom` 同口径；`dist_common` 下沉成
    `common.distribution` 后，全仓第一次出现 `import common.distribution`，
    这一支就只记下裸 `common`，与本集合里点分名的写法对不上。
    """
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
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
    runner, lanes, old = _top_bound(RUNNER_SRC), _top_bound(LANES_SRC), _top_bound(EVAL_SRC)
    for name in MOVED_PUBLIC + MOVED_PRIVATE + RUNNER_CONSTS:
        assert name in runner, f"{name} 不在 trainer/batch_runner.py"
        assert name not in lanes, f"{name} 不得在机器家里再定义一份"
    for name in LANES_OWNED:
        assert name in lanes, f"{name} 不在 trainer/batch_lanes.py"
        assert name not in runner, f"{name} 仍定义在 trainer/batch_runner.py（机器没搬干净）"
    for name in MOVED:
        assert name not in old, f"{name} 仍定义在 trainer/batch_eval.py"


def test_facade_reexports_are_the_same_objects() -> None:
    """门面再导出必须**对象级恒等**（不是同名副本）—— 这是零迁移的全部依据。"""
    for name in MOVED_PUBLIC:
        assert hasattr(be, name), name
        assert getattr(be, name) is getattr(br, name), name


def test_runner_only_symbols_are_not_forwarded_by_the_facade() -> None:
    """执行器独占的符号**不**转发 ⇒ `trainer.batch_eval.<X>` 响亮 AttributeError。

    S16/S19 记过两次的同款坑：名字还留着（转发一份）时，`monkeypatch.setattr` 打在旧家
    会**静默**变成空操作 —— 单测仍绿、生产不生效。这里正面钉「不许有这条路」。
    """
    for name in NOT_FORWARDED:
        home = br if name in RUNNER_CONSTS + MOVED_PRIVATE else bl
        assert hasattr(home, name), f"{name} 应住 {home.__name__}"
        assert not hasattr(be, name), f"{name} 不该经门面转发（patch 会静默失效）"
    # 两家之间也不许互转：`br.STUCK_GRACE_SEC` / `bl.TOOLCHAIN` 这种名字在、没人读的转发
    # 是同一个坑的变体（patch 打哪家都可能静默空操作）。
    assert not hasattr(br, "STUCK_GRACE_SEC"), "机器常量不许在执行器再绑一份"
    assert not hasattr(bl, "TOOLCHAIN"), "版本门端口只被开单元读 ⇒ 只绑执行器"


def test_no_back_edge_to_the_old_home() -> None:
    """不得 import 旧家（成环）—— 用 **AST** 判，不用文本搜。

    本模块 docstring 里必然写着「`trainer.batch_eval` 反过来再导出本模块的公开名」这类
    **合法散文**（S20 的教训：入边是语法事实，就该用语法量）。
    """
    actual = _top_imports(RUNNER_SRC)
    assert "trainer.batch_eval" not in actual
    attrs = [n.attr for n in ast.walk(ast.parse(RUNNER_SRC)) if isinstance(n, ast.Attribute)]
    assert "batch_eval" not in attrs


def test_top_level_import_closure_is_exact() -> None:
    for src, allowed, home in (
        (RUNNER_SRC, RUNNER_IMPORTS, "trainer/batch_runner.py"),
        (LANES_SRC, LANES_IMPORTS, "trainer/batch_lanes.py"),
    ):
        actual = _top_imports(src)
        assert actual == allowed, {
            "家": home,
            "多": sorted(actual - allowed),
            "少": sorted(allowed - actual),
        }


def test_machine_module_has_no_back_edge_to_the_runner() -> None:
    """新家单向：机器不得 import 执行器（成环）；执行器面走 `LaneOwner` 结构协议。"""
    assert _top_imports(LANES_SRC) & {"trainer.batch_runner"} == set()
    attrs = {n.attr for n in ast.walk(ast.parse(LANES_SRC)) if isinstance(n, ast.Attribute)}
    assert attrs & {"batch_runner"} == set()


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


def test_di_names_are_bound_and_called_bare_per_home() -> None:
    """注入点必须是**读它的那个模块**的全局、且以**裸名**调用。

    `monkeypatch.setattr("trainer.batch_runner.log", …)` 只改那一个模块的全局；调用点若写成
    `common.log.log(...)` 一类限定名，patch 就**静默失效**。逐家数两种形态。
    """
    for src, names, mod in ((RUNNER_SRC, RUNNER_DI, br), (LANES_SRC, LANES_DI, bl)):
        bare, qualified = _callee_forms(src, names)
        assert all(n > 0 for n in bare.values()), (mod.__name__, bare)
        assert all(n == 0 for n in qualified.values()), (mod.__name__, qualified)
        for name in names:
            assert hasattr(mod, name), f"{name} 没绑进 {mod.__name__}（patch 会静默失效）"


def test_bun_version_is_read_through_the_port() -> None:
    """版本门探测走 `common.proc.Toolchain` 端口，不再绑模块级 `bun_version`（§P0-8，2026-11-21）。

    两个量：① 端口是**模块全局**（裸名 load ⇒ `monkeypatch.setattr(br, "TOOLCHAIN", …)` 生效）；
    ② 模块里**没有**第二套接缝（再绑一个 `bun_version` 裸名 = 两条 patch 路径，改一处另一处静默）。
    """
    assert not hasattr(br, "bun_version"), "版本探测已收进端口；模块里再绑裸名 = 两套接缝"
    assert br.TOOLCHAIN is common.proc.TOOLCHAIN
    loads = _load_names_in(RUNNER_SRC)
    assert "TOOLCHAIN" in loads
    assert "bun_version" not in loads


def test_constants_are_read_bare_in_the_class_that_uses_them() -> None:
    """常量的读取必须发生在**读它的那个类的成员**里且是裸名。

    2026-09-25（S31/B5b）：执行侧两个类 —— 开单元/收尾住 `BatchEvalRunner`，通道机器住
    `_UnitLanes`；2026-11-20 机器出包后**两个类还分住两个模块**，而 `monkeypatch.setattr`
    的锚点是模块级的 ⇒ 「只扫旧类、只扫旧模块」都会静默退化成子集。
    """
    runner_loads = _load_names_in(_class_member_src(RUNNER_SRC, "BatchEvalRunner"))
    lanes_loads = _load_names_in(_class_member_src(LANES_SRC, "_UnitLanes"))
    missed_runner = [c for c in RUNNER_CONSTS if c not in runner_loads]
    missed_lanes = [c for c in LANES_CONSTS if c not in lanes_loads]
    assert missed_runner == [], missed_runner
    assert missed_lanes == [], missed_lanes
    # 交叉读取 = 常量搬错家（只会在运行期炸，patch 也失效）
    assert not [c for c in LANES_CONSTS if c in runner_loads]
    assert not [c for c in RUNNER_CONSTS if c in lanes_loads]


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
    import worker.eval_heartbeat as hb

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
        def __init__(self, spec):
            calls["spec"] = spec
            #: `dispatch_batch_bg` 现在用**单元自己的** Runtime 起线程（§P1-6 第四批）：
            #: 名字 = `<scope>-run`，FakeRunner 也得有一份才像真的。
            self._rt = common.runtime.Runtime(scope="batcheval-u0")

        def run(self) -> dict:
            calls["ran"] = True
            return {}

    monkeypatch.setattr(br, "BatchEvalRunner", FakeRunner)
    t = br.dispatch_batch_bg(
        br.RunnerSpec(
            bun="bun",
            rl_path=None,
            eval_log=Path("/nonexistent/eval_log.jsonl"),
            args=object(),
            cfg={},
            batch={"batch_id": "b1"},
            unit={"rung": "c4l1"},
            unit_idx=0,
            unit_of=1,
            run_id="run-1",
            engine_epoch="epoch-1",
        )
    )
    t.join(timeout=10)
    assert calls.get("ran") is True
    assert t.daemon is True
    assert t.name == "batcheval-u0-run"
    # 端口按**同一份 spec** 转手（旧写法是 13 个位置参数；这里钉住它不再退回位置手传）
    spec = calls["spec"]
    assert spec.bun == "bun" and spec.unit == {"rung": "c4l1"} and spec.unit_of == 1


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
    aa = br.BatchEvalRunner._done_keys(r, "aa")
    bb = br.BatchEvalRunner._done_keys(r, "bb")
    cc = br.BatchEvalRunner._done_keys(r, "cc")
    assert aa == {(3, 7)}
    assert bb == {(3, 8)}
    assert cc == set()
