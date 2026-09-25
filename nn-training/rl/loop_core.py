"""loop_core —— TrainingLoop：RL 迭代主循环的**组合根**（2026-09-02 从 rl/loop.py 类化）。

入口 rl/loop.py::run_training 是薄包装（构造 TrainingLoop + run()）。**主循环骨架**
（setup / run() 迭代编排 / 轮派发 / 收官 / 停车）已整体搬到
`rl/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀，2026-09-25）——本类经基类元组
继承它；本模块余下的是 `__init__` 的槽位声明与迭代目录 / 采集派发 / 基线评估 /
EvalBoard 窗这些**叶子**。

MRO（组合根，全仓唯一被实例化的类）：RoundSteps（轮内 13 步；基类 = TrainingVolume
动态采集编排）→ TrainingSteps（结算/导出/落账；基类 = TrainingRemote 远端 PPO 腿 +
TrainingEval in-loop 评估链）→ TrainingGuards（熔断/止损/轮转）→ TrainingLifecycle
（主循环骨架）→ 本类（__init__ / 迭代目录 / 采集派发 / 基线评估 / EvalBoard 窗）。
mixin 方法以 self.* 共享同一实例状态；**槽位声明全在 `__init__`**，各 mixin 只赋值/读取。

重构纪律：控制流与日志逐字节沿用旧 run_training 内联实现——每段提取为私有
方法，跨阶段共享状态放 self._*（run() 局部别名 + 实例属性，不重排执行顺序）。
"""


from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import dist_common
from common.proc import run_capture
from platform_utils import rmtree_best_effort
from rl.collect_only import precollect_snapshot_wver
from rl.log import log
from rl.loop_guards import TrainingGuards
from rl.loop_lifecycle import TrainingLifecycle

# 一轮的终态与轮内上下文（R2c-3 起由 `rl.loop_round` 定义）——本模块**原样再导出**：
# `from rl.loop_core import ROUND_NEXT` 这类既有调用点（loop_runner / CLI）不受影响。
from rl.loop_round import (
    ROUND_BUNDLE_EXIT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
    ROUND_WAIT,
    RoundContext,
    RoundOutcome,
    RoundYieldError,
)
from rl.loop_round_steps import RoundSteps
from rl.loop_steps import TrainingSteps
from rl.queue import REPO_ROOT, RUN_ID
from rl.resume import completed_pairs
from rl.rollout_phase import dispatch_rollout_phase

#: 本模块的公开面。`ROUND_*` / `RoundOutcome` / `RoundContext` / `RoundYieldError` 是
#: **再导出**（定义在 `rl.loop_round`，`rl/loop_runner.py` 从这里取）——S4 第十九刀后本模块
#: 自己不再使用它们（用量随主循环骨架搬去 `rl/loop_lifecycle.py`），故必须列进 `__all__`，
#: 否则 ruff F401 会把这条有意的再导出当成死 import（同族写法见 `rl/loop.py`）。
__all__ = [
    "ROUND_BUNDLE_EXIT",
    "ROUND_NEXT",
    "ROUND_RETRY",
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
# 的用途随动态采集那一节搬去了 `rl/loop_volume.py`（S4 第十八刀）——本模块不再需要它们。


# 一轮的终态（`ROUND_*`）与 `RoundOutcome` 见 `rl/loop_round`（顶部已导入再导出）：
# 步骤 mixin（`rl/loop_round_steps`）也需要它们，而它被本模块 import——留在本模块会成环。
#
# 主循环骨架（run / run_one_round / finish_course / _park_after_completion / _setup /
# _setup_common / _evalboard_idle）与随行的 7 个模块级定义已搬到
# `rl/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀）——见该模块头注的宿主判据：
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
# `rl/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀，2026-09-25）。方向：入边
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
        self._rollout_sec = 0.0
        # M3（rollout 上云）：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。
        # 每轮开头复位——_write_iter_stats / _log_report 靠它区分两种口径。
        self._node_rollout_sec: float | None = None
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

    def _check_quota_incident(self, it: int) -> None:
        """配额事故告警（plan P4-W3 / §3.4）：连续 2 轮零 shard 落盘 → 响亮警告行。

        多课程切分下若某课本机槽位被压到 0（或与别课抢核失败），表现为该课 traj
        连续无 shard：训练看似在跑、实则在烧空转墙钟。计数是 per-course 的（每个
        trainer 进程一本课），console 日志页直接可见本行（不建新通道）。

        M3：`rollout_src=node` 轮**本地本来就该零 shard**（采集在节点上，跑完即毁）——
        不排除就会每轮大喊「检查配额」（假事故），把真事故的告警淹掉。
        """
        if getattr(self, "_node_rollout", False):
            self._zero_shard_streak = 0
            return
        try:
            n = sum(1 for _ in self._traj_dir.rglob("rl_s*_seed*"))
        except OSError:
            n = 0
        self._zero_shard_streak = 0 if n else self._zero_shard_streak + 1
        if self._zero_shard_streak >= 2:
            log(
                f"[quota] WARN it{it}: 连续 {self._zero_shard_streak} 轮零 shard 落盘 "
                f"(course={getattr(self.args, 'course_name', '') or 'nocourse'}) — "
                "检查 courses.<课>.workers/local_slots 配额或节点可用性"
            )

    # ------------------------------------------------------------------ 编排
    #
    # run / run_one_round / finish_course / _park_after_completion / _setup / _setup_common /
    # _evalboard_idle 已搬到 `rl/loop_lifecycle.py::TrainingLifecycle`（S4 第十九刀）——它们
    # 在组合实例上照旧解析（本类继承了该混入）；`_run_inspect` 留在这里是因为
    # `run_inspect` 是文档化的可替换点。

    def _run_inspect(self, *args: Any, **kwargs: Any) -> None:
        """自动巡检的**委托点**（步骤 mixin 不能 import 本模块，否则成环）。

        保留 `rl.loop_core.run_inspect` 作为可替换点（`rl.loop.py` 再导出它、测试也替换它）。
        """
        run_inspect(*args, **kwargs)

        # 吞吐 T4：预采子进程句柄与「本轮已提前 spawn」标记（run() 迭代期读写）

    # -------------------------------------------------------------- 迭代步骤

    def _eval_on_round(self, it: int) -> bool:
        """吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
        三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。

        本实现 MRO 胜过 `TrainingEval._eval_on_round` 的占位（`rl/loop_eval.py`，
        S4 第十七刀）——占位存在是为了「MRO 被改坏就响亮失败」。"""
        args = self.args
        if args.mode == "per-tick":
            return (
                int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                and self._eval_every > 0
                and (self._eval_every == 1 or it % self._eval_every == 0)
                and (not self._eval_at_set or it in self._eval_at_set)
            )
        return it in self._eval_at_set

    def _baseline_eval_weights(self, dist_cfg: dict | None) -> str | None:
        """it0 基线可用的 bc 权重路径；前置条件不足返回 None（纯判断，零副作用）。

        条件：per-tick 课程 / 有课程上下文 / in-loop eval 已开启 / dist 有 enabled
        节点（`nodes=[]` 的纯本地路径本就不派 A-eval）/ bc 权重文件在盘上。
        """
        args = self.args
        if args.mode != "per-tick":
            return None
        if getattr(args, "course_obj", None) is None:
            return None
        if int(getattr(args, "eval_games_per_stage", 0) or 0) <= 0:
            return None
        if self._eval_every <= 0:
            return None
        nodes = (dist_cfg or {}).get("nodes") or []
        if not any(n.get("enabled", True) for n in nodes):
            return None
        bc = str(getattr(args, "bc", "") or "")
        if not bc or not Path(bc).exists():
            return None
        return bc

    def _maybe_dispatch_baseline_eval(self, dist_cfg: dict | None) -> None:
        """it0 基线评估（bc 权重）——rollout 收官后派发，**落账前每轮重试**。

        为什么（2026-09-12 用户）：in-loop eval 的配对基准此前恒取日志里**第一条**
        eval 行，而那条基准随 run 起点漂移（resume 时首条可能是 it50，配对比的是
        中途两点，不是"学会了多少"）。改为恒定补一条 it0 = 课程 bc 权重的干净评估：
        跨腿可比，且与 `gates.baseline_win_rate` 同口径。

        重试语义（2026-09-13 评审修订）：只要 eval_log 里尚无**同 bc 指纹**的 it0
        summary（`baseline_summary_landed`），每轮 rollout 收官后都尝试派发——首次
        派发撞上节点瞬时全挂/权重 POST 全失败时（EvalDispatcher 只记日志跳过），
        下一轮自动补派，而不是等进程重启。落账即停（wver 缓存于
        `_baseline_landed_wver`）；summary 带 dropped 也算落账（缺口在控制台诚实
        显示为「缺N」，不为填缺口无限重跑失败局）。

        本地参与：复用当轮 `self._eval_gate`（与 A-eval 同一把门，PPO 收官
        `_join_eval` 置位）——基线本地局与 A-eval 一样让位 PPO，且快照文件名按流
        分流（eval_dispatch 侧），并发重试轮不互相覆写。

        幂等：在飞线程即跳过；跨重启/重试由 `iter == 0` 的已评估键去重，只补缺口。
        失败绝不抛出——基线是观测设施，不得拖垮训练主线。
        """
        t_prev = self._baseline_eval_thread
        if t_prev is not None and t_prev.is_alive():
            return
        try:
            bc = self._baseline_eval_weights(dist_cfg)
            if bc is None:
                return
            from rl.eval_local import baseline_summary_landed

            try:
                wver16 = dist_common.weights_fingerprint(bc)[:16]
            except OSError:
                return  # bc 读不了（检查后被删？）：不派，派发侧同样会失败
            if wver16 == self._baseline_landed_wver:
                return
            if baseline_summary_landed(self._traj_dir, wver16):
                self._baseline_landed_wver = wver16
                return
            from rl.eval_dispatch import dispatch_eval_bg
            from rl.eval_local import BASELINE_EVAL_ITER

            self._baseline_eval_thread = dispatch_eval_bg(
                self.bun,
                bc,
                self._traj_dir,
                self.args,
                dist_cfg or {},
                iter_id=f"{RUN_ID}.{BASELINE_EVAL_ITER}",
                it=BASELINE_EVAL_ITER,
                local_gate=self._eval_gate,
                baseline=True,
            )
            log(
                f"[eval] it0 baseline dispatched（bc 权重：{bc}）——"
                "落账前每轮重试，结果见后续 [eval] 行"
            )
        except Exception as e:  # 基线派发失败不影响训练
            log(f"[eval] WARN it0 baseline dispatch failed (non-fatal): {type(e).__name__}: {e}")

    def _prepare_iter_dir(self, it: int) -> None:
        """rollout/ppo_backend 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
        保留续跑（跳过已完成局 + 续 ppo_backend checkpoint）；否则清空重建。"""
        args = self.args
        traj_dir = self._traj_dir
        wver = dist_common.weights_fingerprint(args.out)
        # 吞吐 T4 提前预采：上一轮若在 epoch3 已 spawn，本轮对账还需接受快照 wver
        # （θ_{N,e3} ≈ θ_N 于最后 1 个 epoch 前）——否则预采首波被当"未完成"清场。
        extra_wver = precollect_snapshot_wver(args.out, it)
        self._extra_wver = extra_wver
        have_resume = bool(
            completed_pairs(
                traj_dir,
                wver,
                extra_wver=extra_wver,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
        )
        if have_resume:
            traj_dir.mkdir(parents=True, exist_ok=True)
            log(
                f"[run_rl] resume iteration {it}: keeping existing shards + ppo_backend checkpoint"
                + (f" (precollect snapshot wver {extra_wver[:12]}…)" if extra_wver else "")
            )
        else:
            if traj_dir.exists():
                # 沙箱删除保护拦截时跳过（保留旧目录，训练照常）
                rmtree_best_effort(traj_dir)
            traj_dir.mkdir(parents=True)

    def _evalboard_yield(self) -> None:
        """rollout 抢占：关窗让出集群。在途 B/C 局停派新 seed（window_event 清位）。"""
        self._eb_window.clear()
        t = self._eb_thread
        if t is not None and t.is_alive():
            # 短等在途局收尾；不阻塞训练主链（超时即走，剩余 seed 下窗续跑）。
            t.join(timeout=15.0)
        self._eb_thread = None
        # R4-G1 心跳：关窗 + 关窗时刻（console 只读，用来显示「训练忙碌中已等 N 分钟」）。
        try:
            from rl.eval_heartbeat import now_ms, write_state

            write_state(window_open=False, last_window_closed_ts=now_ms())
        except Exception:
            pass

    def _rollout_phase(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None, eval_on_round: bool
    ) -> None:
        """单轮采集派发（三路：dist 流式 / dist 串行 / 纯本地），结果落 self._report。"""
        args = self.args
        (report, stream_meta, eval_thread, eval_gate, collect_child, spawned_early) = (
            dispatch_rollout_phase(
                args,
                self.bun,
                dist_cfg,
                it,
                self._traj_dir,
                pairs,
                self._jsonl_path,
                self._model,
                self._opt,
                self._device,
                self.ppo_backend,
                self.update_kwargs,
                self._start_it,
                self._ref_model,
                self._extra_wver,
                eval_on_round,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
        )
        self._report = report
        self._stream_meta = stream_meta
        self._eval_thread = eval_thread
        self._eval_gate = eval_gate
        self._collect_child = collect_child
        self._spawned_early = spawned_early

    # ------------------------------------------------- 动态采集（按样本量）
    #
    # 这一节（9 个成员 / 445 行，`_volume_active` … `_volume_collect_continuous`）已整体搬到
    # `rl/loop_volume.py::TrainingVolume`（S4 第十八刀）。方向是「调用者依赖被调用者」：生产
    # 入口全在 `RoundSteps`（`step_course_iter` → `_iteration_pairs`；`step_rollout` →
    # `_volume_active` / `_volume_collect_continuous`）⇒ 那是基类，本类经它继承可见——
    # 组合实例上的 `self._volume_*` 解析与搬家前逐字相同。
