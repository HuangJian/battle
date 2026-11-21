"""函数形状与 L0 纯度的预算门禁（`python.refactor.md` §8 / §P0-2 / §P0-5）。

两条护栏，都是「**存量按文件冻结、新增即红**」的口径：

1. **函数形状**：单个函数 > 200 行、或参数（posonly + args + kwonly）> 8 个。
   为什么粒度是「文件」而不是「函数名」：函数名会随重构改，文件路径稳定；存量计数
   已经是最松的判定——只要不再变胖就绿，降下来之后可以随时把基线调低。
2. **L0 纯度**：`common/**` 的模块级容器只有在**运行期会被改写**时才算违规；只读常量表
   （`KIND_TEXT`、`schema.*`）合法。判定 = 该名字出现在 `global` 语句，被 `.append/.update/…`
   改写，或被下标赋值/删除。

另有一条 `global` 语句的存量基线：L0/L1 是最不该有进程级可变态的两层。
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from tests.helpers import source_scan

ROOT = Path(__file__).resolve().parents[1]

#: 生产包（`tests/`、`e2e/` 不设形状预算：测试的体量是「覆盖了多少场景」的函数）。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "tools", "worker")

#: 阈值（`python.refactor.md` §P0-2 / §P0-3）。**只许收紧。**
FUNC_LINE_LIMIT = 200
PARAM_LIMIT = 8

#: 存量：包含 >200 行函数的文件 → 该文件里这类函数的**个数**。
#: 2026-11-19：**清零**（第 37 刀拆掉最后一个 `_remote_ppo_publish`）——此后任何 >200 行
#: 函数都是新增，直接红。
LONG_FUNCS_BASELINE: dict[str, int] = {
    # 2026-11-19 合入上游 goal-nn（74 提交）时的存量：这三处是**上游的编排面**
    # （采样节点主流程、评估节点门、BC epoch 循环），合并时只做了结构适配；
    # 拆它们要按「独立所有者 + 独立触发条件」做，见 python.refactor.md §P0-2。
    # （`common/task_client.fetch_task` 已按 P0-2/P0-3 拆成 `FetchTaskSpec` + `_task_params`，
    # `fetch_task` 只剩传输编排 ⇒ 条目删除。）
    # 2026-11-21：`worker/train/bc.py::_Train.run_epochs`（201 行）拆成
    # `run_epochs`（63，只留 epoch 编排 / 历史 / 早停 / 断点 / 回调）+
    # `_train_epoch`（79，训练内循环 + scheduler）+ `_validate`（65，验证与三个准确率）；
    # `trainer/eval_dispatch._eval_admit`（248）拆成 `_eval_admit`（21，编排）+
    # `_eval_eligible`（176，节点门那一大段能力位/fail-closed 判据）+
    # `_eval_publish_weights`（47，冻结门 + 下发 + 窗口起算）+
    # `_eval_local_fallback`（33，兜底唯一入口）；`remote/rollout_node.run`（249）拆成
    # `run`（162，装配 + 三段编排）+ `_open_channel`（50，通道决策，返回生效的 hub_url）+
    # `_supervise`（80，监护 while + 收线）。
    # ⇒ **清零**：此后任何 >200 行函数都是新增，直接红。
}

#: 存量：包含 >8 参数函数的文件 → 个数。
WIDE_FUNCS_BASELINE = {
    "common/race_metrics.py": 1,
    "common/task_key.py": 1,
    "hub/offline_ingest.py": 1,
    "hub/offline_push.py": 2,
    # 2026-11-21：`build_task_zip` 的 9 个关键字参数收成 `TaskZipSpec`
    # ⇒ 本文件宽签名归零，条目删除。
    # 2026-11-21：认领族（`claim` / `claim_outcome` / `_claim_locked`）的
    # (job_id, ttl, worker, mode, epoch, role, capabilities) 收成一个 `LeaseSpec`
    # ⇒ `hub/queue_claims.py` · `hub/queue_peer.py` · `hub/store.py` ·
    # `hub/store_leases.py` 四个文件各 1/1/1/2 条宽签名归零，条目删除。
    # P3 lazy loader 的装载参数（loader/gamma/lam/normalize/配额/mb/bundle）本就 >8。
    "worker/ppo/lazy_corpus.py": 1,
    # 上游合入的宽签名（BC 数据集）——同批登记，待收对象。
    "remote/bc_job.py": 1,
    "remote/bundle.py": 2,
    "remote/colab_bc.py": 1,
    "remote/error_report.py": 1,
    "remote/hub_client.py": 2,
    "remote/hub_http.py": 2,
    # 2026-11-19 合入上游的 relay/replay 参数（原 3 处 → 4 处）。
    "remote/job_lifecycle.py": 4,
    "remote/job_round.py": 2,
    "remote/lifecycle.py": 1,
    "remote/node_upgrade.py": 1,
    "remote/offline_boot.py": 4,
    "remote/offline_deliver.py": 1,
    "remote/offline_eval.py": 1,
    # 2026-11-21：`_base_tar_bytes` / `_landed_bytes_for` 各 9 参 →
    # `BaseTarSpec` / `LandedBytesSpec` ⇒ 本文件宽签名归零，条目删除。
    # 2026-11-21：`pack_payload_zip` 的 9 个参数收成 `PackSpec`
    # ⇒ 本文件宽签名归零，条目删除。
    "remote/payload_proc.py": 1,
    "remote/push_client.py": 1,
    "remote/push_dispatch.py": 2,
    "remote/push_register.py": 1,
    # 2026-11-19 合入上游：`run` 的交易参数 + relay 通道参数（原 1 处 → 2 处）。
    "remote/rollout_node.py": 2,
    "remote/tailscale_boot.py": 1,
    "remote/train_core.py": 3,
    "remote/upload_proc.py": 2,
    "remote/worker.py": 2,
    "tools/c9_critic_calibration.py": 1,
    "tools/p0_auc_probe.py": 1,
    "tools/spatial-probe-flatcv.py": 1,
    "tools/spatial-probe.py": 2,
    "tools/tpu-probe.py": 1,
    #: 2026-11-20：`_settle_unit` 的 15 参收成 `(self, ledger)`，通道机器搬去
    #: `trainer/batch_lanes.py` ⇒ 本文件 3 → **2**（新家没有新增宽签名）。
    "trainer/batch_runner.py": 2,
    "trainer/bc_ingest.py": 0,
    "trainer/bc_loop.py": 2,
    "trainer/dispatch.py": 1,
    "trainer/dispatch_queue.py": 1,
    "trainer/eval_dispatch.py": 3,
    "trainer/loop_plan.py": 1,
    "trainer/loop_serve.py": 1,
    "trainer/loop_transport.py": 1,
    # 2026-11-21：`reap_alloc_timeouts` 的 9 个关键字参数收成 `ReapSpec`
    # ⇒ 本文件宽签名 2 → 1（剩 `__init__`），基线只许继续调小。
    "trainer/queue_local.py": 1,
    "trainer/rollout_phase.py": 1,
    "trainer/stream.py": 1,
    "worker/bc_dispatch.py": 1,
    "worker/bc_eval.py": 2,
    "worker/breaker.py": 1,
    "worker/cmd.py": 1,
    # 2026-11-20（python.refactor.md P0-3 第一/二刀）：`EvalRound.__init__` 的 40 个关键字
    # 参数收进 `RoundSpec`（装配点 = `trainer/eval_dispatch._round_spec`），`settle_task`
    # 的 9 个结果位（含一个从没人读的 `wall_sec`）收进 `SettleOutcome` ⇒ **条目删除，
    # 本文件宽签名归零**。守 `tests/worker/test_eval_round_spec.py`。
    # 2026-11-21（P0-3 第七刀）：`run_local_eval_game`（25 参，全仓最宽）收成
    # `EvalGameSpec` ⇒ 条目删除，本文件宽签名归零。
    "worker/eval_track.py": 1,
    "worker/gate_check.py": 1,
    "worker/greedy_round.py": 1,
    "worker/iter_rollout.py": 4,
    "worker/models/student.py": 1,
    "worker/models/student_paddle.py": 1,
    "worker/plan.py": 1,
    "worker/ppo/engine.py": 1,
    "worker/ppo/engine_paddle.py": 1,
    "worker/ppo/goal.py": 1,
    "worker/ppo/intent.py": 1,
    # 2026-11-19（B8 ④）：`run_ppo` 的 13 个形参就是两个入口的契约本身
    # （ckpt/target_kl/seed/warmup/ref/kl/on_epoch_done/rng），包成对象只会把它隐形。
    "worker/ppo/loop.py": 1,
    "worker/ppo/np_core.py": 1,
    "worker/resume.py": 2,
    # 2026-11-21：`quota_sample_priority` 的 9 个位置参数收成 `QuotaSampleSpec`
    # ⇒ 本文件宽签名归零，条目删除。
    "worker/volume_quota.py": 1,
    # 2026-11-21：`plan_topup` 的 9 个关键字参数收成 `TopUpSpec`
    # ⇒ 本文件宽签名归零，条目删除。
}

#: `common/` 里**允许**在运行期被改写的模块级容器（P0-5 的存量，拆完即删）。
#: 2026-11-13：`log` / `fs` / `weights_ledger` 已改成显式对象（`LogRouter` / `JsonlLedger` /
#: `WeightsLedger`），名册里对应的三条随之删除。
#: 2026-11-14：`distribution` 的在飞登记 / 收工标记 / 重启去重 memo 搬进 `RequestRegistry` /
#: `RestartMemo`（模块只留默认实例），三条也删。
#: 2026-11-19：升级/重启簇整体搬去 `remote/node_upgrade.py`（`_REQUESTS` 留在 wire client），
#: 本条名册因此只剩 `distribution` 的 `_REQUESTS` 一个实例。
#: 2026-11-16：`platform_utils` 的 `_UNREAPED` 搬进 `UnreapedProcs` ⇒ **名册清零**。
#: 这里保持一个空表而不是删掉名册：`test_l0_allowlist_is_still_needed` 靠它继续成立，
#: 将来真有无法立刻拆的存量时，往这里加条目仍然要在上面写清日期与理由。
L0_MUTABLE_ALLOWED: dict[str, frozenset[str]] = {}

#: `global` 语句的存量（按文件计数；出现在新文件 = 红）。
GLOBAL_BASELINE = {
    "biz/reward_context.py": 2,
    "remote/payload_slim.py": 1,
    "remote/tailscale_boot.py": 1,
    "tools/forkdist.py": 2,
    "tools/task.py": 1,
}

_MUTATORS = frozenset(
    {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "setdefault",
        "update",
    }
)


def _prod_files() -> list[Path]:
    out: list[Path] = []
    for d in PROD_DIRS:
        p = ROOT / d
        if p.is_dir():
            out.extend(p.rglob("*.py"))
    return sorted(out)


def _tree(p: Path) -> ast.AST:
    return ast.parse(source_scan.read_text(str(p), "replace"), filename=str(p))


@lru_cache(maxsize=1)
def _shape_counts() -> tuple[dict[str, int], dict[str, int], int, dict[str, int]]:
    """`(长函数计数, 宽参数计数, 函数总数, global 语句计数)`；全进程只扫一次。

    四项共用**同一次** `ast.walk`：`global` 那项原来自己又 `rglob` + 解析了一遍整棵生产树
    （2026-11-20 实测本文件 2.6s → 1.4s）。
    """
    long_: dict[str, int] = {}
    wide: dict[str, int] = {}
    globs: dict[str, int] = {}
    total = 0
    for p in _prod_files():
        rel = p.relative_to(ROOT).as_posix()
        n_globals = 0
        for node in ast.walk(_tree(p)):
            if isinstance(node, ast.Global):
                n_globals += 1
                continue
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            total += 1
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            args = node.args
            nparams = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
            if length > FUNC_LINE_LIMIT:
                long_[rel] = long_.get(rel, 0) + 1
            if nparams > PARAM_LIMIT:
                wide[rel] = wide.get(rel, 0) + 1
        if n_globals:
            globs[rel] = n_globals
    return long_, wide, total, globs


def _growth(actual: dict[str, int], baseline: dict[str, int]) -> dict[str, tuple[int, int]]:
    """`file -> (实际, 基线)`，只留**超出**基线的文件（减少不是失败）。"""
    return {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }


def _module_containers(tree: ast.AST) -> dict[str, ast.AST]:
    """模块顶层被赋成容器字面量 / `dict()/list()/set()` 的名字 → 赋值节点。"""
    out: dict[str, ast.AST] = {}
    for node in getattr(tree, "body", []):
        name = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name, value = target.id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        if name is None or value is None:
            continue
        is_container = isinstance(
            value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
        ) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"dict", "list", "set"}
        )
        if is_container:
            out[name] = node
    return out


def _mutated_names(tree: ast.AST) -> set[str]:
    """运行期被改写的模块级名字：`global` / `.append(…)` / `x[i] = …`。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _MUTATORS
            and isinstance(node.func.value, ast.Name)
        ):
            names.add(node.func.value.id)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            base: ast.AST = node.value
            while isinstance(base, ast.Subscript):
                base = base.value
            if isinstance(base, ast.Name):
                names.add(base.id)
    return names


def _globals() -> dict[str, int]:
    """每个生产文件的 `global` 语句数（派生自 `_shape_counts` 的同一次扫描）。"""
    return _shape_counts()[3]


# ── 护栏本体 ───────────────────────────────────────────────────────────────


def test_no_new_long_functions() -> None:
    long_, _, total, _ = _shape_counts()
    assert total >= 4000, f"只扫到 {total} 个函数——扫描入口坏了（ROOT={ROOT}）"
    over = _growth(long_, LONG_FUNCS_BASELINE)
    detail = "\n".join(f"  {rel}: {n} 个（基线 {base}）" for rel, (n, base) in over.items())
    assert not over, (
        f"以下文件新增了 >{FUNC_LINE_LIMIT} 行的函数：\n{detail}\n"
        "  先拆函数再谈拆文件（手法见 python.refactor.md §P0-2）；确属一次性工具的，"
        "把基线显式加一行并写理由。"
    )


def test_no_new_wide_functions() -> None:
    _, wide, _, _ = _shape_counts()
    over = _growth(wide, WIDE_FUNCS_BASELINE)
    detail = "\n".join(f"  {rel}: {n} 个（基线 {base}）" for rel, (n, base) in over.items())
    assert not over, (
        f"以下文件新增了 >{PARAM_LIMIT} 个参数的函数：\n{detail}\n"
        "  参数 >7 个就说明缺一个对象（python.refactor.md §P0-3 / §7.1）。"
    )


def test_shape_metric_semantics() -> None:
    """口径钉死：参数只数 posonly/args/kwonly，长度含 def 与末行。"""
    src = (
        "def f(a, b, c, d, e, g, h, i, j, *args, k=None, **kw):\n"
        "    if a:\n"
        "        return 1\n"
        "    return 2\n"
    )
    fn: ast.FunctionDef = ast.parse(src).body[0]  # type: ignore[assignment]
    assert len(fn.args.posonlyargs) + len(fn.args.args) + len(fn.args.kwonlyargs) == 10
    assert (fn.end_lineno or fn.lineno) - fn.lineno + 1 == 4


@lru_cache(maxsize=1)
def _common_state() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """`common/**` 每个文件的 `(模块级容器名, 运行期被改写的名字)`；两次检查共用一次解析。

    `test_common_has_no_new_mutable_module_state` 与 `test_l0_allowlist_is_still_needed`
    原来各 `rglob` + 解析一遍 `common/`（约 100 个文件），现在只付一次。
    """
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for p in sorted((ROOT / "common").rglob("*.py")):
        tree = _tree(p)
        out[p.relative_to(ROOT).as_posix()] = (
            frozenset(_module_containers(tree)),
            frozenset(_mutated_names(tree)),
        )
    return out


def test_common_has_no_new_mutable_module_state() -> None:
    bad: dict[str, list[str]] = {}
    for rel, (containers, mutated) in _common_state().items():
        offenders = sorted(containers & mutated - L0_MUTABLE_ALLOWED.get(rel, frozenset()))
        if offenders:
            bad[rel] = offenders
    detail = "\n".join(f"  {rel}: {names}" for rel, names in bad.items())
    assert not bad, (
        "`common/` 是 L0：模块级可变状态必须清零（common/__init__.py 契约 #4）。\n"
        f"新增违规：\n{detail}\n"
        "  改法：显式对象 + 注入（python.refactor.md §P0-5），不要把状态塞回模块顶层。"
    )


def test_l0_allowlist_is_still_needed() -> None:
    """豁免会过期：某个允许项已经不再被改写 ⇒ 从 `L0_MUTABLE_ALLOWED` 删掉。"""
    for rel, allowed in sorted(L0_MUTABLE_ALLOWED.items()):
        containers, mutated = _common_state()[rel]
        still = allowed & mutated & containers
        stale = sorted(allowed - still)
        assert not stale, f"{rel} 的豁免已过期（不再被改写）：{stale} —— 从名册里删掉"


def test_no_new_global_statements() -> None:
    actual = _globals()
    over = _growth(actual, GLOBAL_BASELINE)
    detail = "\n".join(f"  {rel}: {n} 条（基线 {base}）" for rel, (n, base) in over.items())
    assert not over, (
        f"新增了 `global` 语句：\n{detail}\n"
        "  进程级可变态使并发正确性不可证明（python.refactor.md §P0-5）。"
    )
