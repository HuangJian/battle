"""batch_eval — B 层批次门面（plan/rl-eval-system.md §10.3 P2 · plan/nn-training-refactor.md §5.5）。

**本模块是门面，不是实现**：常量 + `maybe_dispatch_batch`（轮内接线）+ 逐个再导出下面三家
的公开名（`X as X`）⇒ 既有 import 点一行不改。四步拆分见 plan §5.5.4，契约守卫见
`tests/test_batch_eval_facade.py`。

| 面 | 实现在哪 | 搬出日期 |
|---|---|---|
| 规划 / 判据 / 门（`plan_units` · `plan_verdict_units` · `units_for_batch` · `node_gate_reason` · `is_transient_error` · `kind_for_policy` + 桶常量） | `rl/batch_plan.py` | 2026-09-25 S25/B1（纯搬） |
| 台账 + 请求面（`BatchStore` 八个具名转移 · 锁 · 原子发布 · `data_root` / `utc_now_iso`） | `rl/batch_store.py` | 2026-09-25 S26/B2（状态收进唯一所有者） |
| 执行面（`BatchEvalRunner` · `dispatch_batch_bg` + 它们独占的常量与 `_heartbeat`） | `rl/batch_runner.py` | 2026-09-25 S27/B3（纯搬） |
| 接线 `maybe_dispatch_batch` + 常量 `ONESHOT_EVAL_KIND` | **本模块** | —— |

**刻意不经门面转发的**（转发了只会制造「名字还在、没人读」的静默空操作，S16/S19 记过两次）：
执行器独占的五个常量 + `_heartbeat` · store 的内部文件名常量 · 三个台账私有 seam
（`_persist_of` / `_requeue` / `_reopen_for_resume` —— 调用点已改到 store 的具名转移上）。
它们在门面上是**响亮**的 AttributeError，而不是一个静默生效的 patch 锚点。

关键契约（原文照搬 —— 别在门面里改语义）：
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

# 再导出约定：`from <home> import X as X` 自别名，逐条一行（ruff `combine-as-imports = false`）。
# 这是「已有 import 点一行不改」与 `batch_eval.X is <home>.X` 的全部依据；
# 面与面的分工见顶部 docstring 的表，闭集守卫见 tests/test_batch_eval_facade.py。
# 规划 / 判据面 → `rl/batch_plan.py`（S25/B1）
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

# 执行面 → `rl/batch_runner.py`（S27/B3）
from rl.batch_runner import BatchEvalRunner as BatchEvalRunner
from rl.batch_runner import dispatch_batch_bg as dispatch_batch_bg

# 台账 / 请求面 → `rl/batch_store.py`（S26/B2）
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

#: 一次性评估入口专用桶。**为什么不蹭 'rollout'**（2026-09-19 实测事故）：节点侧
#: 同 kind 权重文件按保留份数收敛（`workdir-cleanup.WEIGHT_FILES_KEEP = 4`），而训练
#: 作业每轮向 'rollout' 桶 POST 一份新权重 ⇒ 一次性评估那份固定权重在几秒内就被扫掉；
#: 但另一支 agent 进程（共享同一 `tmp/dist-agent`）的内存桶仍说它 cached ⇒ client 收到
#: "kept" 后所有任务在子进程里 ENOENT 退出（agent 直接断连 = client 见 WinError 10054），
#: 重试耗尽 ⇒ 单元 0/50 settled（`local_slots: 0` 时整批 0 行、exit 1）。
#: 独立 kind 让评估权重自成一桶（该 kind 下只有它一份）⇒ 训练作业的 churn 扫不到它。
ONESHOT_EVAL_KIND = "eval"


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
    # 定型只走 store 的具名转移。这里**不**再就地改 `batch`（旧实现「就地改台账再落盘」的残留：
    # store 交回的是**认领时的快照**，执行器只读它的 batch_id/iter（`unit_of` 走参数传入）
    # ⇒ 就地改只是「第二写者」的假象，门面契约见 tests/test_batch_eval_facade.py。
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
