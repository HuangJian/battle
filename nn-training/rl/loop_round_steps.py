"""loop_round_steps —— 轮内 13 步的实现（R2c-3，plan/r2-loop-task-queue §3/§4）。

轮体（原 `run_one_round` 的那 190 行顺序脚本；S4 第十九刀起该方法住 `rl/loop_lifecycle.py`）
在这里被切成 13 个方法：
一步一方法、一步一个 `RoundContext` 入参。切法**不是重新设计控制流**——除下面两处刻意
差异，每一行都是从轮体逐字搬来的，顺序与分支条件逐条保留：

1. **轮内局部量提成 `ctx.*`**（`pairs` / `dist_cfg` / `t_rollout` / `seg` / 会被整段推进的
   `it` / `eval_on_round` / `eval_rec`）：步骤之间必须共享它们，而它们**不能**长在引擎实例上
   （§2.2 无隐藏状态；单进程多课程会互相覆盖）。
2. **`break` / `return` / `it -= 1` → `StepResult`**：由驱动器施加（组合路径 = `run_one_round`
   的循环；细粒度路径 = `Supervisor` 的队列）。

**为什么一步一方法而不是一段块**：调度器要能「跑一半、让位、回来接着跑」。粒度只决定让位点
的密度，不改变语义——`run_one_round` 的步骤序列与细粒度路径**同一张表**（`loop_round.STEP_ORDER`
× `STEP_METHOD`），所以两条驱动不可能漂移。

**这里的方法不得自己决定「等外部」**（那是世界的事，由驱动器认）：步骤只返回「干完了 /
本轮该停了」。谁在等什么由 `LoopRunner` 读事实决定（远端回传到没到、eval 尾巴结没结）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import dist_common
from rl.config import course_key_of, resolve_course_quota
from rl.events import log_iter_error
from rl.log import log
from rl.loop_round import (
    COLLECT_LOCAL,
    COLLECT_NODE,
    COLLECT_SEGMENT,
    ROUND_BUNDLE_EXIT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
    STEP_METHOD,
    STEP_ORDER,
    RoundContext,
    RoundOutcome,
    StepResult,
    finish,
    resolve_collect_mode,
)
from rl.loop_steps import (
    BundleExportedError,
    SmokeVoidRoundError,
    _rollout_source,
    _run_segment_iters,
)
from rl.loop_tasks import ROUND_TASKS
from rl.loop_volume import TrainingVolume
from rl.rollout_phase import join_precollect_child, precollect_ready, spawn_next_collect


class RoundSteps(TrainingVolume):
    """轮内 13 步（mixin；与 `TrainingSteps` / `TrainingGuards` 以 `self.*` 共享引擎状态）。

    MRO 里排在最前（`TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards)`）：它只定义
    `step_*` 与本文件新引入的名字，不与既有方法重名。

    基类 `TrainingVolume`（`rl/loop_volume.py`，S4 第十八刀）= 动态采集（按样本量）编排：
    本类的 `step_course_iter`（`_iteration_pairs`）与 `step_rollout`（`_volume_active` /
    `_volume_collect_continuous`）是那一簇的**全部生产入口**，所以方向是「调用者依赖被调用者」
    （同 `class TrainingSteps(TrainingRemote)`；`TrainingLoop.__bases__` 因此一行不改）。
    （`step_volume_topup` 是保留的**空步**：离散补波退役于 VOLUME_RULE_V2，它不调那一簇。）
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    bun: Any
    # `_total` 是**展示用字符串**（`"0"` / `"∞"` / str(iters)），不是数字——见 loop_core 的赋值。
    _total: Any
    _traj_root: Any
    _traj_dir: Any
    _jsonl_path: Any
    _course_fp: Any
    _last_dist_cfg: Any
    _collect_child: Any
    _prepare_iter_dir: Any
    _hot_reload_course: Any
    _course_iter: Any
    # 动态采集的四个入口（`_iteration_pairs` / `_volume_active` / `_volume_collect_continuous` /
    # `_volume_topup`）**就是真方法**：随基类 `TrainingVolume` 继承而来（`rl/loop_volume.py`，
    # S4 第十八刀）。这里**不再**声明为 `Any`——那会遮住基类实现（mypy 也会报不兼容）。
    _evalboard_yield: Any
    _evalboard_idle: Any
    _export_offline_bundle: Any
    _remote_run_segment: Any
    _remote_iter: Any
    _rollout_phase: Any
    #: in-loop 评估链的入口（`_join_eval` / `_drain_pending_eval` 同簇）——独立实现住在
    #: `rl/loop_eval.py::TrainingEval`（S4 第十七刀）。
    _dispatch_delayed_eval: Any
    _maybe_dispatch_baseline_eval: Any
    _log_report: Any
    #: 远端 PPO 的三相驱动（实现在 `TrainingSteps`）：`None` = 已收口，否则 = 让位/停车。
    _remote_ppo_step: Callable[[RoundContext], StepResult | None]
    _export_weights: Any
    _join_eval: Any
    _record_iteration: Any
    _write_iter_stats: Any
    _rotate_cleanup: Any
    _auto_inspect: Any
    _run_inspect: Any
    _check_quota_incident: Any
    _breaker: Any
    _stop_loss: Any
    # §5 干烧熔断（结果面，loop_guards.TrainingGuards）
    _kickstart_burn: Any
    _paired_kill: Any
    _gate: Any
    _budget_hard_cut: Any
    _consec_fail: int
    _leg_abort: bool
    _node_rollout: bool
    #: 本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。
    _node_rollout_sec: float | None
    _eval_on_round: Any
    _spawned_early: Any
    _stream_meta: Any
    _agg: Any
    _report: dict
    _total_steps: int
    _train_sec_total: float
    _train_samples_total: float
    _ppo_sec: float
    _ppo_cloud_sec: float
    #: R2a：写入账本后把事件并入 `LedgerView` 的钩子（实现在 TrainingGuards）。
    _ledger_apply: Any

    # --------------------------------------------------------- 让位判据（R2c-3）

    def precollect_ready(self, it: int) -> bool:
        """本轮预采是否已可开训（**非阻塞**；单进程调度器据此决定要不要让位）。

        这是「长等待真让位」三处里的**预采**一处。语义边界刻意很窄：它只回答
        「现在能不能往下走」，**不消费句柄**——消费（置空）始终只在 `step_precollect_join`
        一处发生，否则「问一句」就变成有副作用了。

        组合路径（`run_one_round`）不需要它：那条路没人接手，步骤内部的循环等就是了。
        """
        return precollect_ready(
            getattr(self, "_collect_child", None),
            self._traj_root,
            it,
            self.args,
            course_fp=self._course_fp,
        )

    # ------------------------------------------------------------- 步骤序列

    def round_steps(self) -> list[Any]:
        """本轮步骤序列（**现算**，不缓存）：表是唯一顺序来源，改一处即两条驱动同步。

        缓存一份到实例上会让「表改了但进程里的旧列表还在」成为可能——正是要避免的漂移。
        """
        return [getattr(self, STEP_METHOD[kind]) for kind in STEP_ORDER]

    # ----------------------------------------------------------- ① 预采消费

    def step_precollect_join(self, ctx: RoundContext) -> StepResult | None:
        """消费上一轮的预采子进程句柄（**必须早于 `step_prepare_iter`**）。

        依赖方向是硬的：预采子进程产出的是本轮 `it{it}` 的 shard，而 `_prepare_iter_dir`
        靠 `completed_pairs` 看盘决定「保留续跑」还是「清场重建」——先清场再落盘就把预采
        整个作废（白烧一轮采集）。原轮体里这次 join 坐在 `run()` 的循环头（即 prepare 之前），
        位置一致。

        幂等：句柄已被消费（`None`）⇒ 立即返回——所以 `run()` 循环头那次 join 之后，
        轮内这次调用是**无副作用的 no-op**；细粒度驱动器则靠它。
        """
        self._collect_child = join_precollect_child(
            self._collect_child,
            self._traj_root,
            ctx.it,
            self.args,
            course_fp=self._course_fp,
        )
        return None

    # ------------------------------------------------------------- ② 清场

    def step_prepare_iter(self, ctx: RoundContext) -> StepResult | None:
        """本轮目录就位（断点感知：已有同 wver 完整 shard 则保留续跑，否则清空重建）。"""
        self._traj_dir = self._traj_root / f"it{ctx.it}"
        self._prepare_iter_dir(ctx.it)
        return None

    # --------------------------------------------------------- ③ 课程热加载

    def step_hot_reload(self, ctx: RoundContext) -> StepResult | None:
        """课程热加载（§2026-09-13-hot-reload）：rollout 前重读课程文件——非语料编辑下一
        iter 应用；语料身份编辑拒绝 + 控制台横幅 + 沿用启动配置。"""
        self._hot_reload_course(ctx.it)
        return None

    # --------------------------------------------------- ④ 本轮事实（M1c 上下文）

    def step_course_iter(self, ctx: RoundContext) -> StepResult | None:
        """本轮课程上下文 + 一切「每轮热读一次」的事实，全部写进 `ctx`。

        含：M1c 上下文（holder + ppo_schedule，**先于任何 shard 加载**）、(stage, seed) 批次、
        dist 配置、本机配额、采集模式与整段长度、以及 `--export-bundle` 的全离线导出决策
        （导出后 `BundleExportedError` 让本轮干净退出——本轮不训练）。
        """
        args = self.args
        it = ctx.it
        self._course_iter(it)
        log(f"[run_rl] === iteration {it}/{self._total} ===")
        ctx.pairs = self._iteration_pairs(it)
        # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
        # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
        dist_cfg = dist_common.load_dist_config()
        self._last_dist_cfg = dist_cfg
        ctx.dist_cfg = dist_cfg
        # 本机并发配额热读（每轮一次，改 rl-config 下一轮即生效，无需重启）。
        # 多课程（plan multi-course-parallel-training P4-W1 / §3.4）：
        # `courses.<课>.{workers,local_slots}` 优先、`rl.*` 回退（纯解析在
        # rl.config::resolve_course_quota，torch-free 可单测）。原语义保留：
        # CLI 显式 --local-slots 同样被 rl-config 覆盖（SSOT），0 = 关闭本机直跑
        # （2026-09-09 语义统一），无 key 不覆盖。workers 被课程配额改写时打响亮
        # 行——「课程声明 8、实跑 4」的分叉必须有人可见（DoD 断言该行）。
        args.workers, args.local_slots, _quota_line = resolve_course_quota(
            dist_cfg, course_key_of(args), args.workers, args.local_slots
        )
        if _quota_line:
            log(_quota_line)
        ctx.t_rollout = time.time()
        # yield：rollout 抢占集群 —— 关 evalboard 窗，在途 B/C 局停派新 seed。
        self._evalboard_yield()
        self._node_rollout_sec = None
        # 每轮采集报告重置：volume continuous 若把本轮 combine 进上一轮
        # _report，pure_collect 起点会被钉在历史波（§adopt_volume_report）。
        self._report = {}
        self._stream_meta = None
        # 本轮是否派发干净评估：**求值一次**并共享（原轮体在 rollout 调用点内联求值，
        # 同一 it 上是纯函数，故拆出来不改变行为）。
        ctx.eval_on_round = self._eval_on_round(it)
        # 半离线整段（kind=run；2026-09-17）：一次领走 it..end_it，节点自主跑完，
        # hub 期间失联也不影响（产物目录是交付面）。
        #
        # ★ 采集模式由 `resolve_collect_mode` **一处**裁决（段长 > 整轮上云 > 本机采样，
        #   2026-09-19 离线训练模式）：R2c-3 拆 13 步时这里只算了 `ctx.seg` 而没翻
        #   `collect_mode`，于是 kind=run 分支不可达（表面正常：本机照常采样、账本照常
        #   记账，只是云机永远领不到整段）。段长与来源的先后也必须在同一处对齐。
        src = _rollout_source(args)
        ctx.seg = _run_segment_iters(args)
        if src == "run" and not ctx.seg:
            raise SystemExit(
                "[run_rl] --rollout-src run（整段上云）需要说明段长：--run-iters >0（N 轮）"
                "或 <0（到课程末尾）——也可以写进 rl-config：courses.<课>.run_iters。"
                "缺段长时**不**替你退回本机采样（那会让「云机在跑」与「本机在跑」看起来一样）"
            )
        # M3/离线：node 或整段时本机**完全不采样**（也不预采/不补波），分别由
        # _remote_iter（kind=iter）与 _remote_run_segment（kind=run）派发。
        # eval 不动（仍在本地 hub 跑，§5.4）。
        ctx.collect_mode = resolve_collect_mode(src, ctx.seg)
        self._node_rollout = ctx.collect_mode != COLLECT_LOCAL
        ctx.node_rollout = self._node_rollout
        if getattr(args, "export_bundle", ""):
            # 全离线导出：本轮**不训练**——把 it..it+n-1 打成可上传云机的任务包后退出。
            if ctx.seg == 0:
                raise SystemExit(
                    "[run_rl] --export-bundle 需要 --run-iters 说明整段长度"
                    "（>0 = N 轮；<0 = 到课程末尾）"
                )
            self._export_offline_bundle(it, ctx.pairs, ctx.seg)
        return None

    # ------------------------------------------------------------- ⑤ 采集

    def step_rollout(self, ctx: RoundContext) -> StepResult | None:
        """采集（按 `ctx.collect_mode` 分流）：

        · `segment`（半离线整段）：一次领走 it..end_it，**推进指针**（`ctx.it` 变成本段末尾）；
        · `node`（整轮上云）：发 kind=iter job，本机不采样；
        · `local`（默认）：配额课程走**连续配额采集**（2026-09-19 VOLUME_RULE_V2，它自己
          实时读账本派批 + 软停 + 采纳报告，离散补波因此退役）；否则本机 `_rollout_phase`。
        """
        it = ctx.it
        if ctx.collect_mode == COLLECT_SEGMENT:
            self._node_rollout = True  # 本机不采样、不预采、不本地 PPO
            ctx.node_rollout = True
            ctx.it = self._remote_run_segment(it, ctx.pairs, ctx.seg)
        elif ctx.collect_mode == COLLECT_NODE:
            self._remote_iter(it, ctx.pairs)
        elif self._volume_active():
            # 连续配额采集（2026-09-19）：替代「初波 + 补波」；实时按分关差额 + 软停
            # 派发。必须在 `_log_report` 之前（本轮报告要含全部批）。
            self._volume_collect_continuous(it, ctx.dist_cfg)
        else:
            self._rollout_phase(it, ctx.pairs, ctx.dist_cfg, ctx.eval_on_round)
        return None

    def step_volume_topup(self, ctx: RoundContext) -> StepResult | None:
        """离散补波——**已退役**（2026-09-19 VOLUME_RULE_V2）。

        配额课程的采集在上一步（`step_rollout`）由 `_volume_collect_continuous`
        **一站式**收官：它自己按账本实时派批、软停、并采纳报告 ⇒ 补波无事可做；
        非配额课程下 `_volume_topup` 本来就立即返回。

        步骤本体保留：`STEP_ORDER` / `STEP_METHOD` 要求每个 kind 有实现（加一步必须
        同时加实现，见 `tests/test_loop_round.py`），且细粒度驱动器要能在这两个 kind
        之间把执行权交给别的课程。
        """
        return None

    # ------------------------------------------- ⑥ 派发评估 + 本轮报告 + 开窗

    def step_eval_dispatch(self, ctx: RoundContext) -> StepResult | None:
        """「采集已收官、PPO 还没开始」这个窗口里的三件事（原是轮体的连续三段）：

        ① 为上一轮已完成权重 W(it-1) 派发干净评估 —— 游戏藏进随后 PPO(it) 空窗。
           串行路径此前在此处派发读活指针 = W(it-1) 却标 itN（标签超前一轮）；stream/intent/m1/
           基线路径维持原语义。**半离线段例外**：段中间那些轮不在本机跑，归档里没有它们的
           权重——拿活指针（= 段尾权重）去充 W(it-1) 就是 P0 刚修掉的 eval 污染。
        ② it0 基线（bc 权重）：rollout 收官后派发，落账前每轮重试。
        ③ 本轮采集报告行 + **开 idle 窗**（集群空闲立即领批）——不能等到 join_eval 之后：
           remote PPO 可阻塞数十分钟，那时才开窗等于永假。

        三者同一窗口、同一依赖（都在采集之后、PPO 之前），故同一步；拆开也不会多出任何
        独立的重放价值（它们都无幂等判据，重跑一遍无害且必须）。
        """
        it = ctx.it
        if not ctx.seg_ran:
            self._dispatch_delayed_eval(it, ctx.dist_cfg)
        self._maybe_dispatch_baseline_eval(ctx.dist_cfg)
        self._log_report(it, ctx.t_rollout)
        self._evalboard_idle(it, ctx.dist_cfg)
        return None

    # --------------------------------------------------------------- ⑦ PPO

    def step_ppo(self, ctx: RoundContext) -> StepResult | None:
        """PPO 步：本机梯度步（上云轮跳过——节点已在跑整轮）/ **远端三相**（R2c-3）。

        远端不再是一个阻塞调用，而是「发布 → 问一句 → （未就绪就让位）→ 取结果 → 落位」：
        让位点落在「job 已发布、只是还没回」这个**真状态**上——那段时间本机确实不为它干活
        （云机在跑），所以执行权交给别的课程是免费收益。`ctx.resumable` 为假（组合路径
        `run_one_round`）时退化成阻塞取结果，与拆分前逐字节一致。

        R9（§3 单一 PPO 路径下已无「降级本机」档）：远端连败 / 确定性失败判决已落盘 ⇒
        立刻停腿，不再空转重试。
        """
        it = ctx.it
        if self._stream_meta is not None:
            # stream（采集与 PPO 波次重叠）随单一 PPO 路径退役（§3）：本机没有 PPO 窗口，
            # 也就没有「集群在 PPO 窗口闲置」这回事。留着响亮失败，绝不静默走错路。
            raise SystemExit(
                "[run_rl] stream 路径已随单一 PPO 路径退役（plan/accident.plan.md §3）："
                "PPO 恒在 hub 队列上由 worker 认领，本机不再算 PPO"
            )
        if not self._node_rollout:
            res = self._remote_ppo_step(ctx)
            if res is not None:
                return res  # 让位（或停车）
        if self._leg_abort:
            log(f"[run_rl] leg ABORTED at it{it}（远端不可用且禁用降级）")
            return finish(ROUND_STOP)
        return None

    # ------------------------------------------------------- ⑧ 导出权重

    def step_export_weights(self, ctx: RoundContext) -> StepResult | None:
        """权重导出 + 归档（幂等：已归档即跳过）。"""
        self._export_weights(ctx.it)
        return None

    # --------------------------------------------------------- ⑨ 收评估

    def step_eval_join(self, ctx: RoundContext) -> StepResult | None:
        """收本轮干净评估（软等窗口内）并**再试一次**领批（首窗被 yield/部分完成时补领）。"""
        it = ctx.it
        ctx.eval_rec = self._join_eval(it)
        self._evalboard_idle(it, ctx.dist_cfg)
        return None

    # --------------------------------------------------------- ⑩ 落账

    def step_record_iteration(self, ctx: RoundContext) -> StepResult | None:
        """★ 本轮结算：`iteration` 事件落账（**幂等铁律：账本已有该 it 的行 ⇒ 绝不重写**）
        + 配额事故巡检 + G13 duty 分子 + 样本通过量 + 每 iter 指标统计 + 自动巡检 HTML。

        duty 分子语义（原注释照搬）：本轮有效训练入账——事故轮走 `iter_error`，不经过这里
        ⇒ 不计入分子但计入墙钟分母 ⇒ 占空比下降，正是想要的语义。
        """
        args = self.args
        it = ctx.it
        self._record_iteration(it)
        self._check_quota_incident(it)
        self._train_sec_total += float(self._ppo_cloud_sec or self._ppo_sec or 0.0)
        self._train_samples_total += float(
            (self._report or {}).get("totalSamples") or self._total_steps or 0.0
        ) * float(getattr(args, "epochs", 1) or 1)
        # M1c：每 iter 指标统计落盘（非致命）
        self._write_iter_stats(it)
        # 每轮 ppo_backend 写回后自动生成巡检 HTML（intent/goal 总是生成；
        # per-tick 仅默认 traj）
        if self._auto_inspect:
            self._run_inspect(self.bun, it, traj_dir=self._traj_root)
        return None

    # --------------------------------------------------------- ⑪ 门禁

    def step_gate(self, ctx: RoundContext) -> StepResult | None:
        """四道硬边界，**顺序即优先级**（任一命中 ⇒ 本轮即整腿终点）：

        ① F4 熔断（`agg is None` 的流式轮不计连击也不告警——本来就没有新的策略更新）；
        ② 止损（用 `ctx.eval_rec`）；
        ②′ §5 干烧熔断（结果面：腿的读数连着低在起点以下；只在缰绳开着时守。
           放在止损之后、课程门之前：它比课程门急（烧的是一整天算力），
           又比过程熔断宽（要看几个评估点）。
        ②″ §5 附 配对中点杀臂（结果面：本臂 vs 同 V 对端，同 it 配对差连续 2 点 <−3pp）。
           与 ②′ 正交（一个比自己的起点，一个比对照臂），同样比课程门急。
        ③ M1 第四守卫课程结束门（无 gates 块的课程恒 False，零行为变化）；
        ④ G5 每轮预算兜底（`max_hours` 只在评估轮经门被查，非评估轮会过冲——到顶立即停车）。
        """
        it = ctx.it
        if self._agg is not None and self._breaker(it):
            return finish(ROUND_STOP)
        if self._stop_loss(it, ctx.eval_rec):
            return finish(ROUND_STOP)
        if self._kickstart_burn(it, ctx.dist_cfg):
            return finish(ROUND_STOP)
        if self._paired_kill(it, ctx.dist_cfg):
            return finish(ROUND_STOP)
        if self._gate(it):
            return finish(ROUND_STOP)
        if self._budget_hard_cut(it):
            return finish(ROUND_STOP)
        return None

    # --------------------------------------------------------- ⑫ 收尾

    def step_cleanup(self, ctx: RoundContext) -> StepResult | None:
        """轮末：目录轮转 + 双缓冲 spawn 下一轮预采 + **失败连击清零**（本轮确实跑完了）。

        M3 上云轮不预采（节点已在跑本轮的整轮；本地预采 = 双份采集）。
        """
        args = self.args
        it = ctx.it
        self._rotate_cleanup(it)
        self._collect_child = (
            None
            if self._node_rollout
            else spawn_next_collect(args, it, self._stream_meta, self._spawned_early)
        )
        self._consec_fail = 0
        return finish(ROUND_NEXT)

    # ------------------------------------------------------- 异常 → 轮终态

    def round_failure(self, e: BaseException, it: int, *, backoff: bool) -> RoundOutcome:
        """异常 → 轮终态（**组合路径与细粒度执行器共用一份判决**，逐条与现主循环一致）。

        两条驱动的唯一差异是退避：组合路径（`run_one_round`，单课程前台跑）自带
        `time.sleep(30)`；细粒度路径的退避由调度器按 `TaskResult.retry` + `resume_at` 施加
        （排队语义下在任务体里睡 30s 会把整个单进程卡住）。

        `backoff=False` 时仍会累计失败连击并落 `iter_error` 账——「失败这事」必须留痕，
        只是「什么时候再试」交给调度器。冒烟回显（`SmokeVoidRoundError`）不计连击、不睡。
        """
        if isinstance(e, BundleExportedError):
            # 全离线任务包已写出：本轮不训练、不等待，干净退出（不是失败，不计连击）。
            log(f"[run_rl] 全离线任务包导出完成：{e}——退出（上传云机后由云端自主跑完）")
            return RoundOutcome(ROUND_BUNDLE_EXIT, it)
        if isinstance(e, SmokeVoidRoundError):
            # 冒烟回显（worker --echo）：已走完全链路但权重是 init 回显——作废。
            # 不计失败连击、不 sleep；it 原地（异常从 _remote_ppo 抛出时本轮
            # 未写 iteration 事件，重试轮 _prepare_iter_dir 清场重采）。
            if getattr(self.args, "smoke", False):
                log(f"[run_rl] smoke it{it}: 冒烟回显已作废——--smoke 干净退出")
                return RoundOutcome(ROUND_SMOKE_STOP, it)
            log(f"[run_rl] it{it} 收到冒烟回显结果——本轮作废，原地重试")
            return RoundOutcome(ROUND_RETRY, it)

        self._consec_fail += 1
        kind = "SystemExit" if isinstance(e, SystemExit) else type(e).__name__
        self._ledger_apply(log_iter_error(self._jsonl_path, it, f"{kind}: {e}"))
        log(
            f"[run_rl] it{it} FAILED ({kind}: {e}); "
            f"consecutive={self._consec_fail}/5 — retry same iteration"
        )
        if not isinstance(e, SystemExit) and getattr(self, "_leg_abort", False):
            # 已经被判死腿（如远端 401/403 这类重试无意义的失败，ABORT 判决已由
            # _handle_remote_failure 落盘）——再按通用兜底重试只是重复 publish 同一 job、
            # 把停腿拖后 5×30s（x3-step 事故）。直接上抛。
            raise
        if self._consec_fail >= 5:
            raise e
        if backoff:
            time.sleep(30)
        return RoundOutcome(ROUND_RETRY, it)


# 步骤方法名与步骤表必须一一对应（加一步就得加实现，漏了在 import 期就响亮）。
assert set(STEP_METHOD) == set(ROUND_TASKS), "loop_round.STEP_METHOD 与 ROUND_TASKS 不同步"
