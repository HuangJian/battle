"""batch_eval — B 层批次执行（plan/rl-eval-system.md §10.3 P2）。

BatchEvalRunner：结构参考 EvalDispatcher，复用 `fetch_task(mode='eval')` /
`run_local_eval_game`；语料由 `ladder.json` + `batches.jsonl` 驱动
（不要复用 dispatch_eval_bg：EvalDispatcher 无外部语料入口，§10.3）。

语料规划与判据/门（`plan_units` / `plan_verdict_units` / `node_gate_reason` /
`is_transient_error` …）自 2026-09-25（S25/B1）起住 `rl/batch_plan.py`；本模块顶部
逐个再导出（`X as X`）⇒ 既有调用点一行不改。

台账与请求面（`consume_requests` / `claim_pending` / `mark_unit_done` / `read_*` …）自
2026-09-25（S26/B2）起住 `rl/batch_store.py` —— `BatchStore` 是**台账唯一所有者**：
「一次具名转移 = 一次事务 = 一次落盘」，锁与原子发布都在那里；本模块顶部同样逐个
再导出 ⇒ 调用点一行不改。本模块自此 = **执行面**（`BatchEvalRunner`）+ 轮内接线
（`maybe_dispatch_batch`）+ 单元行字段（`eval_census_fields` / `eval_loot_fields`）。

执行面（`BatchEvalRunner` / `dispatch_batch_bg` + 它独占的常量与 `_heartbeat`）自
2026-09-25（S27/B3）起住 `rl/batch_runner.py`；本模块顶部再导出两个公开名 ⇒ 调用点一行不改。

关键契约：
  - 节点门（§6.6）：enabled ∧ ping ∧ evalSupport ∧ stageJsonSupport ∧
    bunVersion 一致 ∧ **codeHash 一致**（= rollout 门同一判据；2026-09-17 起不再比
    ping.engineEpoch——见 dist_common.check_code_hash）——**严格拒派，不静默降级**（P2 DoD）。
  - iterId 命名空间 `{runId}.b{batchShort}u{unit}`（runner.ts）：agent taskKey
    无 policy 分量，命名空间隔离是 god/nn 不串键的唯一保证。
  - 窗口（§6.5）：只在窗口开时派新局；在途局自然跑完（taskTimeoutSec 封顶）；
    剩余局按 batch_unit 下窗续跑（units.done 进 batches.jsonl 台账）。
  - 确定性契约（§3.4/P2 DoD）：同 ckpt + 同 seed 集 + 同 stage ⇒ gameplay
    字段逐字节一致（node/elapsedSec/phase/ts 不要求）。
"""

from __future__ import annotations

import threading
from pathlib import Path

import dist_common

# 规划 / 判据面：实现已出包到 `rl/batch_plan.py`（S25/B1，纯搬）。
# 自别名逐条再导出（`X as X`，ruff `combine-as-imports = false` 下每条一行）⇒
# 既有 `from rl.batch_eval import plan_units` 等调用点一行不改，且 `batch_eval.X is batch_plan.X`。
from rl.batch_plan import BATCH_STAGE_BASE as BATCH_STAGE_BASE
from rl.batch_plan import CORPORA_JSON as CORPORA_JSON
from rl.batch_plan import EVAL_SEED0 as EVAL_SEED0
from rl.batch_plan import KIND_FOR_POLICY as KIND_FOR_POLICY
from rl.batch_plan import LADDER_JSON as LADDER_JSON
from rl.batch_plan import LEVELS_DIR as LEVELS_DIR
from rl.batch_plan import REGRESSION_EVERY as REGRESSION_EVERY
from rl.batch_plan import REPO_ROOT as REPO_ROOT
from rl.batch_plan import SEGMENT_LEN as SEGMENT_LEN
from rl.batch_plan import batch_iter_id as batch_iter_id
from rl.batch_plan import corpora_path as corpora_path
from rl.batch_plan import corpus_doc as corpus_doc
from rl.batch_plan import is_transient_error as is_transient_error
from rl.batch_plan import kind_for_policy as kind_for_policy
from rl.batch_plan import load_corpora as load_corpora
from rl.batch_plan import load_ladder as load_ladder
from rl.batch_plan import node_gate_reason as node_gate_reason
from rl.batch_plan import plan_units as plan_units
from rl.batch_plan import plan_verdict_units as plan_verdict_units
from rl.batch_plan import select_next_unit as select_next_unit
from rl.batch_plan import units_for_batch as units_for_batch

# 执行面：实现已出包到 `rl/batch_runner.py`（S27/B3）。自别名再导出 ⇒ 调用点一行不改、
# `batch_eval.BatchEvalRunner is batch_runner.BatchEvalRunner`。
from rl.batch_runner import BatchEvalRunner as BatchEvalRunner
from rl.batch_runner import dispatch_batch_bg as dispatch_batch_bg

# 台账 / 请求面：实现已出包到 `rl/batch_store.py`（S26/B2，状态收进唯一所有者）。
# 自别名逐条再导出（与 batch_plan / batch_runner 同款：ruff `combine-as-imports = false`
# 下每条一行）⇒ 既有 `from rl.batch_eval import claim_pending` 等调用点一行不改，
# 且 `batch_eval.X is batch_store.X`。
from rl.batch_store import DEFAULT_DATA_ROOT as DEFAULT_DATA_ROOT
from rl.batch_store import REQUESTS_DONE_FILE as REQUESTS_DONE_FILE
from rl.batch_store import REQUESTS_FILE as REQUESTS_FILE
from rl.batch_store import BatchStore as BatchStore
from rl.batch_store import claim_pending as claim_pending
from rl.batch_store import consume_requests as consume_requests
from rl.batch_store import data_root as data_root
from rl.batch_store import mark_requests_done as mark_requests_done
from rl.batch_store import mark_unit_done as mark_unit_done
from rl.batch_store import read_batches as read_batches
from rl.batch_store import read_done_req_ids as read_done_req_ids
from rl.batch_store import read_requests as read_requests
from rl.batch_store import utc_now_iso as utc_now_iso
from rl.batch_store import write_batches as write_batches
from rl.log import log

# ── 规划 / 判据面：实现已出包到 `rl/batch_plan.py`（2026-09-25 B1，纯搬）────────
# `REPO_ROOT`、四个批规划常量（阶梯 / 语料 / 关卡目录 + 三档镜像值）与全部规划/判据函数
# 都住 `rl/batch_plan.py`；顶部 import 逐个再导出（`X as X`）⇒ 既有调用点一行不改、
# `batch_eval.X is batch_plan.X` 恒真。

# `is_transient_error`（背压/瞬断的 B 层薄转发，单一实现仍在 `dist_common`）已搬到
# `rl/batch_plan.py`（顶部再导出）。

# 执行器独占的五个常量（背压退避封顶 / 收尾僵死 / 重探间隔 / 零消费者宽限 / 恢复轮次）随
# `BatchEvalRunner` 搬到 `rl/batch_runner.py`（S27/B3）—— **它们只被执行器读**，故本模块
# **不再转发**：对 `rl.batch_eval.<常量>` 的 setattr / getattr 会**响亮** AttributeError，
# 而不是「名字还在、没人读」的静默空操作（S16/S19 记过两次的坑）。


# `node_gate_reason`（节点门判据；纯函数，单测覆盖）已搬到 `rl/batch_plan.py`（顶部再导出）。


# `KIND_FOR_POLICY` / `kind_for_policy`（policy → agent 权重桶）已搬到 `rl/batch_plan.py`
# （顶部再导出）。


#: 一次性评估入口专用桶。**为什么不蹭 'rollout'**（2026-09-19 实测事故）：节点侧
#: 同 kind 权重文件按保留份数收敛（`workdir-cleanup.WEIGHT_FILES_KEEP = 4`），而训练
#: 作业每轮向 'rollout' 桶 POST 一份新权重 ⇒ 一次性评估那份固定权重在几秒内就被扫掉；
#: 但另一支 agent 进程（共享同一 `tmp/dist-agent`）的内存桶仍说它 cached ⇒ client 收到
#: "kept" 后所有任务在子进程里 ENOENT 退出（agent 直接断连 = client 见 WinError 10054），
#: 重试耗尽 ⇒ 单元 0/50 settled（`local_slots: 0` 时整批 0 行、exit 1）。
#: 独立 kind 让评估权重自成一桶（该 kind 下只有它一份）⇒ 训练作业的 churn 扫不到它。
ONESHOT_EVAL_KIND = "eval"


# `utc_now_iso` / `data_root`（含 `DEFAULT_DATA_ROOT`）已搬到 `rl/batch_store.py`
# （S26/B2，与台账同住 —— 它们描述的是「存储」而不是「执行」）；顶部再导出 ⇒
# 调用点一行不改。



# ── 批语料规划（纯函数面）───────────────────────────────────────────────────
# `load_ladder` / `plan_units` / `corpora_path` / `load_corpora` / `corpus_doc` /
# `plan_verdict_units` / `units_for_batch` / `_forces_of` / `batch_iter_id` 已搬到
# `rl/batch_plan.py`（顶部再导出）—— 本模块只剩台账 / 执行 / 接线。


# ── 台账 / 请求面：实现在 `rl/batch_store.py`（2026-09-25 S26/B2）──────────────────────
# 锁（原 `_claim_guard` / `_claim_locked` → `BatchStore._tx`）、台账读改写
# （`read_batches` / `write_batches` / `_persist_of` / `_requeue` / `_reopen_for_resume`
# → **八个具名转移**）与请求面（`read_requests` / `read_done_req_ids` /
# `mark_requests_done` / `consume_requests`）都收进 `BatchStore` ——
# **一次转移 = 一次事务 = 一次落盘**（设计见 plan/nn-training-refactor.md §5.5.2）。
# 公开名在顶部再导出（`X as X`）⇒ 既有调用点与测试一行不改；三个私有 seam
# （`_persist_of` / `_requeue` / `_reopen_for_resume`）**不**再转发 —— 它们是私有面，
# 调用点已改到 store 上（`set_units_of` / `requeue` / `reopen_for_resume`）。


# ── 执行面：`BatchEvalRunner` / `dispatch_batch_bg` 已出包到 `rl/batch_runner.py`
# （2026-09-25 S27/B3，纯搬：一个 100 局单元 = 通道机器 + 尾段竞速 + 背压重排 + 收尾三闸）。
# 公开名在顶部再导出（`X as X`）⇒ 既有 `from rl.batch_eval import BatchEvalRunner` 一行不改
# 且 `batch_eval.X is batch_runner.X`；它独占的常量与 `_heartbeat` **不**转发（见上）。


def maybe_dispatch_batch(
    bun: str,
    rl_path: str | None,
    traj_dir: Path,
    args,
    cfg: dict,
    run_id: str,
    it: int,
    window_event: threading.Event | None = None,
) -> threading.Thread | None:
    """B 层轮次分配钩子（§6.4）：调用方保证 `not eval_on_round`（A/B 确定性分配，
    无饥饿）。认领最早 pending 批，跑其中第一个未完成单元；无批/无单元 → None。

    per-tick + policy nn：rl_path 必需（学生权重）；policy god：权重无关，
    任何模式可跑。intent/goal + policy nn：B 层 nn 单元语义不适用（意图权重另
    桶），拒绝并记日志（未来扩展点，不静默跑错）。
    """
    # store 现建现用：只持有 root（**不缓存台账**，见 rl/batch_store 模块 docstring），
    # root 在**调用时**解析 —— 与原来的 `data_root()` 同口径。
    store = BatchStore()
    batch = store.claim()
    if batch is None:
        return None
    # 判决批（P2）：语料来自注册表，unit 自带 ckpt ⇒ 不需要 rl_path，也不吃 mode
    # 分歧（显式判决与训练模式无关：这一局的权重就是 unit 的 ckpt）。
    verdict = str(batch.get("kind") or "ladder") == "verdict"
    policy = str(batch.get("policy", "nn"))
    if not verdict and policy == "nn" and getattr(args, "mode", "per-tick") != "per-tick":
        log(
            f"[batcheval] batch {batch.get('batch_id')}: nn unit 不适用于 mode={args.mode} — 退回队列"
        )
        store.requeue(str(batch.get("batch_id")))
        return None
    try:
        units = units_for_batch(batch)
    except Exception as e:
        log(f"[batcheval] plan 失败 ({e}) — 退回队列")
        store.requeue(str(batch.get("batch_id")))
        return None
    units, nxt, unit = select_next_unit(
        units, set((batch.get("units") or {}).get("done", [])), batch.get("only_rungs")
    )
    if nxt is None or unit is None:
        return None
    # 判决批的权重在 unit 上（多 ckpt 同批）；ladder 批回落批次级 rl_path。
    unit_weights = str(unit.get("ckpt") or "") or rl_path
    if policy == "nn" and not unit_weights:
        log("[batcheval] nn unit without weights — 退回队列")
        store.requeue(str(batch.get("batch_id")))
        return None
    epoch = dist_common.compute_engine_epoch()
    eval_log = traj_dir.parent / "eval_log.jsonl"
    batch.setdefault("units", {})["of"] = len(units)
    store.set_units_of(str(batch.get("batch_id")), len(units))
    return dispatch_batch_bg(
        bun,
        unit_weights,
        eval_log,
        args,
        cfg,
        batch,
        unit,
        nxt,
        len(units),
        run_id,
        epoch,
        policy,
        window_event,
        str(batch.get("init_sha16", "")),
    )


# `select_next_unit`（下一待跑单元的确定性挑选）已搬到 `rl/batch_plan.py`（顶部再导出）。


