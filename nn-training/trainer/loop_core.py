"""loop_core —— TrainingLoop：RL 迭代主循环的**组合根**（2026-09-02 从 trainer/loop.py 类化）。


入口 trainer/loop.py::run_training 是薄包装（构造 TrainingLoop + run()）。**本类已是纯组合类**
（S4 第二十刀，2026-09-25）：全部 20 个方法都搬进了四个基类混入，余下的**只有**
`__init__`（槽位声明，状态归属的唯一真相）与 `_run_inspect`（唯一的结构性例外：
它与模块级 `run_inspect` 必须同住一个模块，而那个模块不能 import 本模块当基类 ⇒ 成环）。


MRO（组合根，全仓唯一被实例化的类）：RoundSteps（轮内 13 步；基类 = TrainingVolume
动态采集编排 + TrainingBaseline it0 基线评估 + TrainingIterDir 本轮目录与产出健康 +
TrainingDispatch 本轮派发与让位）→ TrainingSteps（结算/导出/落账；基类 = TrainingRemote
远端 PPO 腿 + TrainingEval in-loop 评估链）→ TrainingGuards（熔断/止损/轮转）→
TrainingLifecycle（主循环骨架）→ 本类（__init__ + _run_inspect）。
mixin 方法以 self.* 共享同一实例状态；**槽位声明全在 `__init__`**，各 mixin 只赋值/读取。


重构纪律：控制流与日志逐字节沿用旧 run_training 内联实现——每段提取为私有
方法，跨阶段共享状态放 self._*（run() 局部别名 + 实例属性，不重排执行顺序）。
"""


from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from common.log import log
from common.proc import run_capture
from trainer.dispatch_queue import REPO_ROOT
from trainer.loop_guards import TrainingGuards
from trainer.loop_lifecycle import TrainingLifecycle
from trainer.loop_round_steps import RoundSteps
from trainer.loop_steps import TrainingSteps

# 一轮的终态与轮内上下文（R2c-3 起由 `biz.loop_round` 定义）——本模块**原样再导出**：
# `from trainer.loop_core import ROUND_NEXT` 这类既有调用点（loop_runner / CLI）不受影响。
from worker.loop_round import (
    ROUND_BUNDLE_EXIT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SKIP,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
    ROUND_WAIT,
    RoundContext,
    RoundOutcome,
    RoundYieldError,
)

#: 本模块的公开面。`ROUND_*` / `RoundOutcome` / `RoundContext` / `RoundYieldError` 是
#: **再导出**（定义在 `biz.loop_round`，`trainer/loop_runner.py` 从这里取）——S4 第十九刀后本模块
#: 自己不再使用它们（用量随主循环骨架搬去 `trainer/loop_lifecycle.py`），故必须列进 `__all__`，
#: 否则 ruff F401 会把这条有意的再导出当成死 import（同族写法见 `trainer/loop.py`）。
__all__ = [
    "ROUND_BUNDLE_EXIT",
    "ROUND_NEXT",
    "ROUND_RETRY",
    "ROUND_SKIP",
    "ROUND_SMOKE_STOP",
    "ROUND_STOP",
    "ROUND_WAIT",
    "RoundContext",
    "RoundOutcome",
    "RoundYieldError",
    "TrainingLoop",
    "run_inspect",
]


# 注：`build_pairs` / `combine_reports` / `settled_stage_totals` / `trailing_samples_per_game`
# 的用途随动态采集那一节搬去了 `trainer/loop_volume.py`（S4 第十八刀）——本模块不再需要它们。


# 一轮的终态（`ROUND_*`）与 `RoundOutcome` 见 `biz/loop_round`（顶部已导入再导出）：
# 步骤 mixin（`trainer/loop_round_steps`）也需要它们，而它被本模块 import——留在本模块会成环。
#
# 主循环骨架（run / run_one_round / finish_course / _park_after_completion / _setup /
# _setup_common / _evalboard_idle）与随行的 7 个模块级定义已搬到
# `trainer/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀）——见该模块头注的宿主判据：
# 入边（`_evalboard_idle` 被 TrainingRemote / RoundSteps 以 self. 调）把新混入锁在
# **组合根**，因为这两个 sibling 的祖先集交集为空。


def run_inspect(bun: str, it: int, traj_dir: Path) -> None:
    """每轮 ppo_backend 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    显式传 --traj-dir（intent/goal 的非默认 traj 也要能出巡检 HTML）。"""
    try:
        run_capture(
            [
                bun,
                "tools/diag/rl-hourly-inspect.ts",
                "--up-to",
                str(it),
                "--traj-dir",
                str(traj_dir),
            ],
            cwd=REPO_ROOT,
            timeout=180,
        )
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")


# ---------------------------------------------------------------------------------------
# 主循环骨架（run / run_one_round / finish_course / _park_after_completion / _setup /
# _setup_common / _evalboard_idle）与随行的 7 个模块级定义（WAIT_RETRY_SEC / _course_file_fp /
# kickstart 自检两件 / KICKSTART_DEFAULT_WARN / 对照行 / should_park_on_done）已整体搬到
# `trainer/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀，2026-09-25）。方向：入边
# （`_evalboard_idle` 被 TrainingRemote / RoundSteps 调）把宿主锁在**组合根**，见该模块头注。
# ---------------------------------------------------------------------------------------


class TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards, TrainingLifecycle):
    """RL 迭代主循环（run_training 的 OO 化；run() 为入口，失败重试内置）。

    MRO：RoundSteps（轮内 13 步，R2c-3；自身的基类 = TrainingVolume 动态采集编排）→
    TrainingSteps（结算/导出/落账；自身的基类 = TrainingRemote 远端 PPO 腿 + TrainingEval
    in-loop 评估链）→ TrainingGuards（熔断/止损/轮转）→ TrainingLifecycle（主循环骨架，
    S4 第十九刀；它的 inbound 手 `_evalboard_idle` 要求它在两个 sibling 之上——见该类头注）
    → 本类（__init__ 槽位声明 / 迭代目录 / 采集派发 / 基线评估 / EvalBoard 窗）。
    """

    def __init__(self, args, ppo_backend, bun, update_kwargs) -> None:
        self.args = args
        self.ppo_backend = ppo_backend  # _setup 内按 args.mode 重新解析（原 run_training 同款覆盖）
        self.bun = bun
        self.update_kwargs = update_kwargs
        # ---- 实例状态声明（_setup / 迭代阶段方法赋值；先声明类型供 mypy/阅读）----
        self._start_it = 0
        self._collect_child: subprocess.Popen | None = None
        self._spawned_early = False
        self._agg = None
        self._tripped = None
        #: 「这一轮为什么收工」的唯一记录（`run_complete.cause/detail` 的来源，2026-10-15）。
        #: **唯一写点** = 各停车判据自身（`_breaker` / `_stop_loss` / `_kickstart_burn` /
        #: `_paired_kill` / `_gate` 的 I2 停腿 / `_budget_hard_cut`），**唯一读点** =
        #: `loop_lifecycle.finish_course`。空 ⇒ 按「跑满声明轮数」兜底；无限课程 ⇒ `unknown`
        #: （绝不编原因）。每轮只会有一个判据命中（`step_guards` 命中即 `finish(ROUND_STOP)`），
        #: 所以是普通赋值、不需要「先到先得」的判定。
        self._stop_cause = ""
        self._stop_detail = ""
        self._prev_entropy = None
        self._consec_fail = 0
        self._kl_streak = 0
        self._ent_streak = 0
        self._stop_loss_streak = 0
        self._auto_inspect = False
        self._deadline: float | None = None
        self._total = "0"
        self._eval_every = 1
        self._eval_at_set: set[int] = set()
        self._model: Any = None
        self._opt: Any = None
        self._device: Any = None
        self._ref_model: Any = None
        self._ppo_mod: Any = None
        self._ppo_goal: Any = None
        self._ppo_intent: Any = None
        self._save_weights_json: Any = None
        self._traj_root = Path(args.traj)
        self._jsonl_path: Path = self._traj_root / "training_log.jsonl"
        #: M3（plan/ppo-job-lifecycle-ownership §4）：重发意图的「坏文件只喊一次」去重。
        self._redo_err_note = ""
        # M2（plan/ppo-job-lifecycle-ownership §3）：腿启动 = 上一代 runId 的活整体作废
        # （写账本 + 兑现到磁盘）。**只在这里**发生（不是发布时）——publish 前的
        # `cancel_stale_jobs` 仍是兜底。任何失败都只降级，绝不阻断开课。
        #
        # ★ 2026-10-17（plan/export-must-not-cancel-live-job §3，事故 A）：**撤销权只归会派活的
        #   进程**。M2 的语义是「重启了就该从头再来」，只有会派活、会等回传的腿才配下这个判断；
        #   `--export-bundle` 的只读导出腿也构造主循环，却既不派活也不等回传（打完包即退），
        #   它一启动就把在线腿正在等的活判成「上一代残留」——一次跨进程误伤
        #   （2026-10-16 y5-hide：那门课再没被接单 3h+）。
        try:
            from remote.hub_client import cancel_jobs_of_previous_runs
            from trainer.dispatch_queue import RUN_ID, dispatches_jobs

            _job_root = Path(
                getattr(args, "remote_job_root", "") or (self._traj_root / "remote-jobs")
            )
            if _job_root.exists() and dispatches_jobs(args):
                cancel_jobs_of_previous_runs(
                    self._jsonl_path,
                    _job_root,
                    RUN_ID,
                    hub_url=str(getattr(args, "remote_hub_url", "") or ""),
                    hub_token=str(getattr(args, "remote_token", "") or ""),
                    log=log,
                )
        except Exception as e:  # 清理失败不能阻断开关课（下一拍/下一进程还有机会）
            log(f"[run_rl] WARN 启动清理上一 runId 残留未完成（不影响开课）：{type(e).__name__}: {e}")
        self._rotate_seed = 0
        # 动态采集（plan/dynamic-rollout-volume）：None = 本轮课程未开该模式，
        # 事件字段随之留空（additive，旧行无此键）；开启时 = 目标/已结算 transitions。
        self._volume_target: int | None = None
        self._volume_collected: int | None = None
        #: 本轮已跑的波次数（初波 = 1）与初波每关局数（硬顶默认值依赖它）。
        self._volume_waves = 0
        self._volume_g0 = 0
        self._volume_est = 0
        self._volume_capped = False
        self._volume_stage_ests: dict[int, int] | None = None
        #: 分关采集账（规则版本 + 分关 collected/games/est_s/cap_games + 缺口）；连续配额
        #: 收官时写，`_record_iteration` 读进 iteration 事件。**必须在 `__init__` 初值**：
        #: 只在 `_volume_collect_continuous` 里赋值会让「未开该模式 / stream / collect-only」
        #: 的轮在落账时 AttributeError（e2e 当场抓到，2026-10-07）。
        self._volume_stage_stats: dict | None = None
        self._rollout_sec = 0.0
        # M3（rollout 上云）：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。
        # 每轮开头复位——_write_iter_stats / _log_report 靠它区分两种口径。
        self._node_rollout_sec: float | None = None
        # 本轮是否在节点采集：**必须在 `__init__` 初值**（2026-10-02 事故）。
        # 它此前只在 `step_course_iter` 里赋值，于是「引擎被 EnginePool 驱逐后重建、
        # 而调度器队列已走到 ppo/cleanup」时，`step_ppo` 裸读它会 AttributeError
        # ⇒ 该课每秒重启一次、一轮都跑不完（h4-hurt-f75/f150 实测）。
        # 初值 False = 本地轮：即便真读到了早值，也只是把采集判成本地，不改变采集行为。
        self._node_rollout: bool = False
        self._ppo_sec = 0.0
        # M0 统一计量（iteration 事件的 wire 子字典）：由 TrainingSteps._remote_ppo 赋值，
        # 本地/旧路径从不赋值——读取一律走 getattr(self, "_wire", None)。
        self._total_steps = 0
        self._chunks_n = 0
        # 迭代期共享状态（在对应阶段方法内赋值；此处先声明供 mypy/阅读定位）
        self._traj_dir: Path = self._traj_root
        self._extra_wver: str | None = None
        self._report: dict = {}
        self._stream_meta: dict | None = None
        #: 接管等待的**去重文案**（★P1-1，plan §3.5；★M2 换词）：被接管的课 = 等待不是收官
        #: ⇒ 每拍重问同一轮，那行日志会每 `poll_interval` 重放一遍（比今天更吵）。记下上一条，
        #: **同一状态只喊一次**（状态变了——接管解除 / 换了一门课——就再喊）。
        self._held_note: str = ""
        #: 降级（plan/offline-hold-stale-fallback §3-S1c）：去重键 = 原因（`""` = 没降级），
        #: 与写进 `iteration` 事件的 `_collect_fallback` 同一个值。两者都**不是**采集模式：
        #: `collect_mode` 仍只有 local|node|held（`_node_rollout` 靠它决定本机跑不跑 PPO）。
        self._fallback_note: str = ""
        self._collect_fallback: str = ""
        self._eval_thread: threading.Thread | None = None
        self._eval_gate: threading.Event | None = None
        # 2026-09-17：未收官的 eval 尾巴 (thread, 派发时刻)——由下一轮 rollout 收官时
        # 收拢（_sweep_eval_tail，不站等固定秒数）。
        self._eval_tail: tuple[threading.Thread, float] | None = None
        self._eval_tail_start: float | None = None
        # it0 基线评估（bc 权重）：rollout 收官后派发、落账前每轮重试（见
        # _maybe_dispatch_baseline_eval）。线程只作在飞守卫，不参与 join；
        # _baseline_landed_wver = 已落账基线的权重指纹（命中即不再派）。
        self._baseline_eval_thread: threading.Thread | None = None
        self._baseline_landed_wver: str | None = None
        # EvalBoard idle 窗（2026-09-11 用户）：置位 = 可派 B/C 批；清位 = yield 给 rollout。
        # 与 A-eval 轮解耦——训练不在 rollout/eval 时才领取队列。
        self._eb_window = threading.Event()
        self._eb_thread: threading.Thread | None = None
        self._kl_cum = None
        self._halted_flag = False
        # R9（2026-09-10 c6 it50 事故）：远端失败计数 / 已降级 / 停腿标记。
        self._remote_fail = 0
        self._leg_abort = False
        # G13 duty 的分子：累计有效训练秒（Σ 真训练秒 ppo_cloud_sec）。
        # 初值从账本重算（跨重启不被低估——否则重启后占空比误报"在烧事故"）。
        self._train_sec_total = 0.0
        # min_train_samples 的分子：累计**样本通过量** Σ(samples × epochs)。
        self._train_samples_total = 0.0
        self._dropped_games = None
        self._load_sec = None
        self._tail_drain_sec = None
        self._waves_n = None
        self._eval_join_sec = 0.0
        # 配额事故计数（plan P4-W3）：连续零 shard 落盘的轮数（每课一进程一本）。
        self._zero_shard_streak = 0

    # ------------------------------------------------------------------ 编排
    #
    # 本类原本的四簇方法**全部**已搬进基类混入（它们在组合实例上照旧解析）：
    #   动态采集（按样本量；9 成员 / 445 行）→ `trainer/loop_volume.py::TrainingVolume`
    #     （S4 第十八刀；方向「调用者依赖被调用者」：生产入口全在 RoundSteps）。
    #   主循环骨架（setup / run 编排 / 轮派发 / 收官 / 停车；7 成员）→
    #     `trainer/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀；宿主判据见该模块头注）。
    #   剩余的 7 个叶子分三簇（S4 第二十刀，2026-09-25）：
    #     it0 基线评估 → `trainer/loop_baseline.py::TrainingBaseline` · 本轮目录与产出健康 →
    #     `trainer/loop_iter_dir.py::TrainingIterDir` · 本轮派发与让位（采集三路 / A-eval
    #     稀疏化 / EvalBoard 关窗）→ `trainer/loop_dispatch.py::TrainingDispatch`；三簇都挂
    #     `RoundSteps` 一侧（唯一 mixin 调用者），组合根因此不必再长基类。
    #
    # **本类余下的唯一方法**是 `_run_inspect`：它与模块级 `run_inspect` 必须同住一个模块
    # （`_run_inspect` 按**模块全局**解析 `run_inspect`），而那个模块不能 import `trainer.loop_core`
    # ——本模块 import 它当基类 ⇒ 成环。故这对搭档只能住组合根（可替换点 = `trainer.loop_core.run_inspect`）。

    def _run_inspect(self, *args: Any, **kwargs: Any) -> None:
        """自动巡检的**委托点**（步骤 mixin 不能 import 本模块，否则成环）。

        保留 `trainer.loop_core.run_inspect` 作为可替换点（`trainer.loop.py` 再导出它、测试也替换它）。
        """
        run_inspect(*args, **kwargs)


