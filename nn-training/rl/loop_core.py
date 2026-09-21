"""loop_core —— TrainingLoop：RL 迭代主循环（2026-09-02 从 rl/loop.py 类化）。

入口 rl/loop.py::run_training 是薄包装（构造 TrainingLoop + run()）。本模块
承载主循环骨架：setup / run() 迭代编排 / 迭代目录 / 采集派发接线；单轮结算与
梯度步在 TrainingSteps mixin（rl/loop_steps.py），训练护栏在 TrainingGuards
mixin（rl/loop_guards.py）——mixin 方法以 self.* 共享 TrainingLoop 实例状态。

重构纪律：控制流与日志逐字节沿用旧 run_training 内联实现——每段提取为私有
方法，跨阶段共享状态放 self._*（run() 局部别名 + 实例属性，不重排执行顺序）。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import rmtree_best_effort
from rl.breaker import CIRCUIT_EXIT_CODE
from rl.collect_only import precollect_snapshot_wver
from rl.course import build_pairs, resolve_rotate_seed
from rl.events import write_run_complete, write_run_start
from rl.log import log
from rl.loop_guards import TrainingGuards

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
from rl.loop_steps import TrainingSteps, kickstart_coef
from rl.queue import REPO_ROOT, RUN_ID
from rl.reports import combine_reports
from rl.resume import (
    completed_pairs,
    settled_stage_totals,
    trailing_samples_per_game,
)
from rl.rollout_phase import dispatch_rollout_phase, join_precollect_child
from rl.train_ledger import LedgerSpec, load_ledger

# 一轮的终态（`ROUND_*`）与 `RoundOutcome` 见 `rl/loop_round`（顶部已导入再导出）：
# 步骤 mixin（`rl/loop_round_steps`）也需要它们，而它被本模块 import——留在本模块会成环。

#: `run()` 撞上 `ROUND_WAIT` 时的再问间隔（秒）。单课程驱动器是阻塞语义（退避后再问同一轮），
#: 不是让位——真正的让位在 supervisor（`rl/loop_runner` 的 `waiting()`，间隔 `poll_interval`）。
WAIT_RETRY_SEC = 5.0


def _course_file_fp(args) -> str | None:
    """D14 语料血缘：课程文件 sha256；无课程返回 None（旧行为不过滤）。

    torch-free（hub 远程分支也调用——只读文件字节）。与
    remote/hub_client.publish_job 的 course_fp 同算法，两端必须一致。
    字节源 = 启动期冻结（args.course_frozen_bytes）——mid-run 热加载编辑不改血缘。
    """
    import hashlib

    course = getattr(args, "course_obj", None)
    if course is None:
        return None
    frozen = getattr(args, "course_frozen_bytes", None)
    if frozen:
        return hashlib.sha256(frozen).hexdigest()
    path = getattr(args, "course_path", "") or ""
    if not path:
        from rl.config import resolve_course

        try:
            path = str(resolve_course(course.name))
        except Exception:
            return None
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def run_inspect(bun: str, it: int, traj_dir: Path) -> None:
    """每轮 ppo_backend 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    显式传 --traj-dir（intent/goal 的非默认 traj 也要能出巡检 HTML）。"""
    try:
        subprocess.run(
            [
                bun,
                "tools/diag/rl-hourly-inspect.ts",
                "--up-to",
                str(it),
                "--traj-dir",
                str(traj_dir),
            ],
            cwd=str(REPO_ROOT),
            timeout=180,
            capture_output=True,
            text=True,
            **_POPEN_NO_WINDOW,
        )
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")


def _kickstart_startup_check(args: Any, start_it: int) -> float:
    """BC 缰绳重启自检（纯逻辑，可单测；IO 仅读存在性 + 写一行日志）。

    ① bc 文件启动期即查（换机器漏同步权重 ≠ 静默裸奔；失败指到 in-use 备份）；
    ② 返回并落日志 kk(start_it)（衰减按 run 原点续算，重启不再回满额）。
    kickstart 未启用时返回 0.0 且零副作用。
    """
    if not bool(getattr(args, "kickstart_ref", False)):
        return 0.0
    bc_path = str(getattr(args, "bc", "") or "")
    if not bc_path or not Path(bc_path).exists():
        raise SystemExit(
            f"[run_rl] kickstart_ref 要求课程 bc 权重存在（换机器漏同步？）：{bc_path!r}——"
            "从 nn-training/weights/in-use/ 常备备份恢复（§379），禁裸奔启动"
        )
    kk0 = kickstart_coef(args, start_it)
    log(
        f"[run_rl] kickstart: ref={bc_path} "
        f"kk(start_it={start_it})={kk0:.6g}（run 原点衰减，resume 不复位）"
    )
    return kk0


def should_park_on_done(args, smoke_void: bool) -> bool:
    """ALL DONE 后停车还是退出：--smoke 作废干净退出 / --exit-on-done → 退出
    （旧行为：前台脚本/预演等待进程结束）；其余一律停车不断进程。"""
    if smoke_void:
        return False
    return not bool(getattr(args, "exit_on_done", False))


class TrainingLoop(RoundSteps, TrainingSteps, TrainingGuards):
    """RL 迭代主循环（run_training 的 OO 化；run() 为入口，失败重试内置）。

    MRO：RoundSteps（轮内 13 步，R2c-3）→ TrainingSteps（结算/PPO/导出/eval join/落账）
    → TrainingGuards（熔断/止损/轮转）→ 本类（setup / 迭代编排 / 目录 / 采集派发）。
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

    def run(self) -> None:
        args = self.args
        self._setup()
        it = self._start_it - 1
        smoke_void = False  # --smoke 作废干净退出：收官后仍退出进程（预演等待结束）
        while args.iters <= 0 or it < args.iters:
            it += 1
            # 吞吐 T4：本轮开头检查预采子进程产出（句柄消费后归零）
            self._collect_child = join_precollect_child(
                self._collect_child, self._traj_root, it, args, course_fp=self._course_fp
            )
            if self._deadline is not None and time.time() >= self._deadline:
                log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
                break
            outcome = self.run_one_round(it)
            it = outcome.it  # 段跑会推进 it；必须以返回值为准
            if outcome.status == ROUND_BUNDLE_EXIT:
                return
            if outcome.status == ROUND_SMOKE_STOP:
                smoke_void = True
                break
            if outcome.status == ROUND_STOP:
                break
            if outcome.status == ROUND_RETRY:
                it -= 1
            if outcome.status == ROUND_WAIT:
                # 单课程驱动器的让位（RL 的 `run_one_round` 今天从不返回它——本分支是**语义
                # 完整性**：等外部事实时不能把这一轮当成跑完（那会跳轮），也不能原地空转烧 CPU）。
                # 退避一小段再来问同一轮；这仍是阻塞式单课程语义（真正的让位在 supervisor 那边）。
                it -= 1
                time.sleep(WAIT_RETRY_SEC)

        # P0 收官 drain（用户指令：最终轮立即 eval）：循环结束（跑满/break/预算）
        # 后，为最新已完成且无完整 summary 的评估轮权重派发并等收官。smoke 轮跳过。
        if not smoke_void:
            self._drain_pending_eval()
        if self._tripped is not None:
            sys.exit(CIRCUIT_EXIT_CODE)
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")
        if not should_park_on_done(args, smoke_void):
            return
        self._park_after_completion(it)

    def _run_inspect(self, *args: Any, **kwargs: Any) -> None:
        """自动巡检的**委托点**（步骤 mixin 不能 import 本模块，否则成环）。

        保留 `rl.loop_core.run_inspect` 作为可替换点（`rl.loop.py` 再导出它、测试也替换它）。
        """
        run_inspect(*args, **kwargs)

    def run_one_round(self, it: int) -> RoundOutcome:
        """跑一轮（R2c-2 从 `run()` 抽出；R2c-3 起由**步骤表**驱动，仍是同一套控制流）。

        组合路径 = 依次施加 `rl.loop_round.STEP_ORDER` 里的每一步：某一步给出终态
        （门 / 熔断 / 止损 / 预算 / 停腿 / 冒烟 / 全离线导出）即返回，否则一路到 `cleanup`
        （它返回 `ROUND_NEXT`）。细粒度路径（`LoopRunner.run_step`）走**同一张表**，只是每步
        之间可以把执行权交给别的课程——两条驱动因此不可能漂移（加一步必须同时进表）。

        `ctx` = 轮内状态（`pairs` / `dist_cfg` / `t_rollout` / `seg` / `eval_rec`，以及会被
        半离线整段推进的 `it`）。语义与抽取前逐条一致：5 连击重试、冒烟作废、段跑推进 it、
        异常分类，全部在 `round_failure` 里**与细粒度执行器共用一份判决**。

        返回：`RoundOutcome(status, ctx.it)`；`it` 可能大于入参（半离线整段一次推进多轮）
        ——调用方**必须**用返回的 it 继续，否则会重跑已跑过的段。
        """
        ctx = RoundContext(it=it)
        try:
            for step in self.round_steps():
                res = step(ctx)
                if res is None:
                    continue
                if res.is_final:
                    return RoundOutcome(res.outcome or ROUND_NEXT, ctx.it)
                if res.is_wait:
                    # 组合路径没有让位点（单课程前台跑，没人接手）。步骤自己要求让位 = 设计
                    # 走岔了 ⇒ 响亮报错，而不是静默停住或空转。R2c-3 余下部分把 `wait_job` /
                    # eval 尾巴 / 预采子进程真轮询化时，必须同时给组合路径加 `ROUND_WAIT`。
                    raise RoundYieldError(
                        f"步骤在组合路径里要求让位（{res.reason}）——组合路径没有让位点，"
                        "请走 Supervisor 细粒度驱动"
                    )
            return RoundOutcome(ROUND_NEXT, ctx.it)
        except RoundYieldError:
            # 设计走岔 ≠ 一次失败：不落 iter_error、不计连击、不重试（否则它会伪装成
            # 普通引擎异常被吞进重试阶梯，症状是「每轮都重试同一轮」）。
            raise
        except (SystemExit, Exception) as e:
            # `SystemExit` 不是 `Exception`（基类是 BaseException），故并列捕获——与抽取前的
            # 四个 except 分支（BundleExportedError / SmokeVoidRoundError / SystemExit /
            # Exception）覆盖同一集合，分类判决搬进 `round_failure`。退避只在组合路径生效
            # （调度器的退避是 `TaskResult.retry` 的 resume_at，不在任务体里睡 30s）。
            return self.round_failure(e, ctx.it, backoff=True)

    def finish_course(self, it: int) -> None:
        """**本课程**收官（一轮跑满）的三件事，不含停车循环（R2d 拆分）。

        ① 本地停止采集（收敛在飞预采子进程，不留孤儿空烧）；② 向云机下发停机指示
        （PAUSE，能自停的释配额）；③ 账本落 `run_complete` 事件（console「已完成」横幅
        派生源）。

        **为什么从 `_park_after_completion` 里拆出来**：停车（死循环等重启）的语义前提是
        「这个进程就是这门课」。单进程多课程（`rl/loop_serve.py`）下停车会**冻住所有课**，
        所以那条路径只做本方法、调度器把该课队列置 `done`；单课程前台入口 = 本方法 + 停车
        （进程即该课）。两条路径因此共用同一份收官副作用（不会一边落 `run_complete`、
        另一边忘了）。
        """
        args = self.args
        total = args.iters if args.iters > 0 else "∞"
        # 在飞预采子进程（门判决等中途 break 时可能残留）：收敛掉，不留孤儿空烧。
        # 正常跑满时 spawn_next_collect 已因 it==iters 短路，child 恒为 None。
        child = getattr(self, "_collect_child", None)
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    log(f"[run_rl] 停车：收敛在飞预采子进程（it{it + 1} 未开跑）")
            except Exception as e:  # 收敛失败不阻断停车
                log(f"[run_rl] 停车：预采子进程收敛失败（{type(e).__name__}: {e}）")
            self._collect_child = None
        self._sync_cloud_halt(it, "PAUSE")
        reason = f"正常收官（it{it}/{total}），本地停采、云机已停机"
        try:
            write_run_complete(self._jsonl_path, it, int(args.iters or 0), reason)
        except OSError as e:
            log(f"[run_rl] 停车：run_complete 落账失败（{e}）— 仅横幅派生缺失，继续停车")
        log(
            f"[run_rl] 正常完成（it{it}/{total}）——本地停止采集，进程停车不断开；"
            "EvalBoard B 批照常认领；改大 iters 后经控制台 停止→启动 以继续训练"
        )

    def _park_after_completion(self, it: int) -> None:
        """正常收官（ALL DONE）→ 停车不断进程（2026-09-12 用户定案）。

        三件事（收敛采集 / 云机 PAUSE / `run_complete` 落账）在 `finish_course` 里；本方法
        = 它 + **永久停车**：期间 EvalBoard B 批照常认领（idle 窗常开、机器本就空闲；直连节点
        派发，不受 hub 云停机影响）——本地训练采集已停，只服务评估。中断（Ctrl-C/SIGTERM
        语义）干净返回。冒烟/--exit-on-done 不进这里。
        """
        self.finish_course(it)
        parked_min = 0
        while True:
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                log("[run_rl] 停车中收到中断——干净退出")
                return
            parked_min += 1
            # 停车期认领（60s 粒度）：复用 idle 窗逻辑——窗常开（无 rollout 抢占），
            # 有在途单元则只保窗不重复领，无则认领最早 pending 批；dist_cfg 每轮热读
            # （节点变更下一分钟即生效）。异常自吞（认领失败不影响停车）。
            try:
                try:
                    dist_cfg = dist_common.load_dist_config()
                except Exception:
                    dist_cfg = None
                self._evalboard_idle(it, dist_cfg)
            except Exception as e:
                log(f"[run_rl] 停车期认领失败（忽略）：{type(e).__name__}: {e}")
            if parked_min % 60 == 0:
                log(f"[run_rl] parked（已停车 {parked_min // 60}h）：无采集，等待重启")

    # ---------------------------------------------------------------- 启动

    def _setup(self) -> None:
        args = self.args

        # ===== 训练路径延迟导入（B7，2026-09-02）：collect-only 子进程已提前 return，
        # 此刻起才允许拉起 torch / ppo.* / models.*（CPU 上 ~3-8s 的 torch 加载不再
        # 出现在每轮的双缓冲预采子进程里）。=====
        #
        # ===== 单一 PPO 路径（2026-09-21，plan/accident.plan.md §3）：hub 免 torch（D2）=====
        # PPO 恒在 hub 队列上由 worker 认领执行 ⇒ 训练进程**永不**建 model/opt/ref 栈：
        # 不 import torch / ppo.*，模型与优化器零加载。C 腿式灾难（误配 → 本机 CPU PPO 慢 13
        # 小时）在结构上不可能：loop 自己没有计算能力，误配无处发生。
        import numpy as np

        np.random.seed(args.seed)
        self.ppo_backend = None
        self._model = None
        self._opt = None
        self._device = None
        self._ref_model = None
        self._bc_ref = None
        self._ppo_mod = None
        self._ppo_goal = None
        self._ppo_intent = None
        self._save_weights_json = None
        log(
            "[run_rl] single PPO path: hub torch-free (D2) — PPO runs on a claimed worker; "
            "rollout/eval stay local"
        )
        self._setup_common()

    def _setup_common(self) -> None:
        """两模式（local/remote）共享的启动尾部：traj 目录 / rotateSeed / run_start 账本 /
        日志 / eval 稀疏化 / 断点续跑定位。remote 分支在跳过 torch 构建后也走这里。"""
        args = self.args
        traj_root = Path(args.traj)
        traj_root.mkdir(parents=True, exist_ok=True)
        self._traj_root = traj_root
        self._jsonl_path = traj_root / "training_log.jsonl"
        # D14 语料血缘：课程文件 sha256（None = 非课程运行，不过滤——旧行为字节不变）
        self._course_fp = _course_file_fp(args)

        # R2a（plan/r2-loop-task-queue §5）：**一次扫描**得到账本视图——续跑指针、累计量、
        # 熔断连击、提示类判决次数全由它重建（旧实现是 5 个扫描器各读一遍全文件）。
        # 视图同时是本进程的增量账本：写事件的调用点随后 apply_event ⇒ 永不重扫。
        # 用户裁决（2026-09-18）：门禁语义 = 扫账本，指标按课缓存、只在开课/续跑读一遍。
        self._ledger = load_ledger(self._jsonl_path, LedgerSpec.from_args(args))
        # 续跑继承 rotateSeed：已有 run_start 历史 → 沿用其 rotateSeed（课程连续 → it 续跑时
        # 下轮 (stage,seed) 与已落盘局一致 → 断点续跑剔除生效，不重跑已完成局）。
        # 全新开始（无 jsonl 历史，例如用户清空重建）才用当前时刻抖动种子。
        prev_rs = self._ledger.rotate_seed
        rotate_seed, rs_source = resolve_rotate_seed(
            args.seed, getattr(args, "rotate_seed", None), prev_rs, int(time.time())
        )
        if rs_source == "explicit":
            log(f"[run_rl] rotateSeed explicit override={rotate_seed} (paired-course mode; prev={prev_rs})")
        elif rs_source == "inherited":
            log(f"[run_rl] resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
        self._rotate_seed = rotate_seed
        # G13 duty 的分子：从账本重算累计有效训练（Σ 真训练秒）。
        # 进程内存累计重启会归零 → 占空比被低估 → 误报"在烧事故"；账本是 SSOT。
        self._train_sec_total = self._ledger.train_sec_total
        self._train_samples_total = self._ledger.train_samples_total
        # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
        # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。
        write_run_start(self._jsonl_path, args, rotate_seed)

        log(
            f"[run_rl] mode={args.mode} "
            f"iters={'infinite' if args.iters <= 0 else args.iters}"
            + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
            + " "
            + (
                f"curriculum={args.curriculum_stages} start={args.curriculum_start} "
                f"every={args.curriculum_every} grow={args.curriculum_grow}"
                if args.curriculum_stages
                else f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
                f"of {args.total_stages} (full coverage every "
                f"{-(-args.total_stages // args.rotate_stages)} iters)"
                if args.rotate_stages > 0
                else f"stages={args.stages} seeds={args.seeds}"
            )
            + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
            f"workers={args.workers} keepIters={args.keep_iters}"
        )
        log(f"training_log: {self._jsonl_path}")
        log(f"[run_rl] runId={RUN_ID}")

        # 自动巡检：per-tick 仅对默认 traj 生效（巡检脚本默认读 tmp/rl-traj）；
        # intent/goal 总是生成巡检 HTML（run_inspect 显式传 --traj-dir）。
        auto_inspect = (
            True
            if args.mode in ("intent", "goal")
            else traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
        )
        if auto_inspect:
            log(
                "[run_rl] per-iteration auto-inspection ENABLED (HTML report after each ppo_backend)"
            )
        self._auto_inspect = auto_inspect

        self._deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
        self._total = "∞" if args.iters <= 0 else str(args.iters)
        self._prev_entropy = None
        # consec_fail（重试连击）**不跨重启继承**：它是单腿内的进程级护栏（5 连击即抛），
        # 继承会让「重启即秒死」（一次失败就撞上历史 5 连击）。只在视图里观测。
        self._consec_fail = 0
        # F4 连击从账本继承（与 ent_peak §339 同理）：连击是**连续**计数，只有下一轮的
        # kl/entropy 再越线才继续，故继承既真又无害；不继承则重启即清零。
        self._kl_streak = self._ledger.kl_streak  # F4: consecutive iters with kl >= KL_BREAK
        self._ent_streak = self._ledger.ent_streak
        # F4 ENT 相对崩塌基线（2026-09-06）：本轮之前见过的最大熵。None = 无历史
        # （冷启动首轮）→ breaker 退回绝对电平判定。续跑时从 training_log.jsonl 回读，
        # 否则每次重启 peak 归零，it9 会被当成"首轮"白送一次连击。
        self._ent_peak: float | None = self._ledger.ent_peak
        self._stop_loss_streak = (
            self._ledger.stop_loss_streak
        )  # P1-9: 止损连击（新 stop_loss 事件）
        # I2（提示类门 REMEDIATE ×N 停腿）：计**整条账本**，不随进程重启洗白
        # （2026-09-18 修）。旧实现在内存里，重启后 4 次确认可以从头再来。
        self._soft_remediate_count = self._ledger.soft_remediate_count(
            TrainingGuards.NO_CLOUD_HALT_KINDS
        )
        if self._soft_remediate_count:
            log(
                f"[run_rl] resume: inherited soft-REMEDIATE count={self._soft_remediate_count} "
                "（I2 停腿判据读账本，重启不洗白）"
            )
        self._tripped = None
        # it 断点续跑：--start-it 显式，否则自动 = 账本最后完成迭代 + 1
        start_it = args.start_it if args.start_it is not None else self._ledger.next_it
        if start_it > 1:
            log(
                f"[run_rl] resume: continuing from iteration {start_it} "
                f"(weights resume from {args.out})"
            )
            # ENT 相对崩塌基线续跑继承（§339）：已在上面从视图赋值，这里只回显。
            if self._ent_peak is not None:
                log(
                    f"[run_rl] resume: inherited entropy peak={self._ent_peak:.3f} (F4 ENT baseline)"
                )
        self._start_it = start_it
        _kickstart_startup_check(args, start_it)
        # 吞吐 T3：eval 稀疏化周期（默认 1 = 每轮，字节一致；>1 = 每 N 轮一次）。
        # 2026-09-03 修正：`or 1` 曾把显式 eval_every=0 吞成 1（想关闭 eval 却变成
        # 每轮都跑——课程 _s5t 测试期实测）。现在 0 表示关闭；默认（cli/rl-config
        # 未给时 = 1）仍每轮，字节一致。
        self._eval_every = int(getattr(args, "eval_every", 1) or 0)
        # 吞吐 T3：eval 绝对迭代点集（复用 run_rl_intent 的 eval_at 语义；空 = 不启用该维）。
        self._eval_at_set = {
            int(x) for x in str(getattr(args, "eval_at", "") or "").split(",") if x.strip()
        }
        # 吞吐 T4：预采子进程句柄与「本轮已提前 spawn」标记（run() 迭代期读写）

    # -------------------------------------------------------------- 迭代步骤

    def _eval_on_round(self, it: int) -> bool:
        """吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
        三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。"""
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
            completed_pairs(traj_dir, wver, extra_wver=extra_wver, course_fp=self._course_fp)
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

    def _evalboard_idle(self, it: int, dist_cfg: dict | None) -> None:
        """训练空闲窗（rollout 收官后 / A-eval 收官后）：开窗并认领最早 pending 批。

        与 A-eval 轮解耦（2026-09-11 用户）：采集结束后即可领（含 remote PPO
        等待期）；rollout 开始时 _evalboard_yield 关窗暂停。已有在途单元则只开窗不重复领。
        """
        self._eb_window.set()
        # R4-G1 心跳：开窗（后续单元起止由 batch_eval 续写 batch/unit/rung）。
        try:
            from rl.eval_heartbeat import write_state

            write_state(window_open=True)
        except Exception:
            pass
        t_prev = self._eb_thread
        if t_prev is not None and t_prev.is_alive():
            return
        try:
            from rl.batch_eval import maybe_dispatch_batch

            t = maybe_dispatch_batch(
                self.bun,
                self.args.out,
                self._traj_dir,
                self.args,
                dist_cfg or {},
                RUN_ID,
                it,
                window_event=self._eb_window,
            )
            self._eb_thread = t
            if t is not None:
                log(f"[batcheval] idle window it{it}: claimed unit (thread={t.name})")
        except Exception as e:
            log(f"[batcheval] idle claim failed (ignored): {type(e).__name__}: {e}")

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
    # plan/dynamic-rollout-volume.plan.md：课程用 target_transitions 代替固定局数做
    # 配额，轮中按**已结算 transitions** 逐关补波。纯逻辑在 rl/volume_waves.py，
    # 账本在 rl/resume.settled_stage_totals；本节只做接线 + 日志 + WAL。
    #
    # v1 边界（计划 §3-P0/P1 明确）：只接串行路径；stream/double-buffer 保持老语义
    # （不再动它是为了让双缓冲那套墙钟优化不被这一版搅动）。

    def _volume_active(self) -> bool:
        """本课程是否开了动态采集（`target_transitions > 0`；缺席 = 一个函数都不调）。"""
        return int(getattr(self.args, "target_transitions", 0) or 0) > 0

    def _volume_stages(self) -> list[int]:
        """动态采集的关集 = 课程声明的 stages（分关配额的分母）。

        门控窗口（curriculum/rotate）与配额分关 v1 不兼容：两者的「本轮实际采样关」
        是 it 的函数，而配额按固定关集反解——硬凑会让分关达标线与现实不符。响亮
        SystemExit 而不是静默取一个关集（静默错分 = 采集量对不上目标，最难发现那种）。
        """
        args = self.args
        if (
            str(getattr(args, "curriculum_stages", "") or "")
            or int(getattr(args, "rotate_stages", 0) or 0) > 0
        ):
            raise SystemExit(
                "[volume] target_transitions 与 --curriculum-stages / --rotate-stages 的"
                "门控窗口 v1 不兼容（配额按课程声明的关集分关）——改用显式 --stages"
            )
        from rl.volume_waves import parse_stages_arg

        raw = str(getattr(args, "stages", "") or "").strip()
        # P2-c（2026-09-15）：原先是 `parse_range(str(... or "0-3"))` —— 缺 --stages
        # 时静默退成硬编码 4 关。实测本腿 args.stages 恒有值（launch 期从课程
        # stages 派生）所以没踩到，但静默猜关数 = 分关配额分母错、采集量对不上
        # 目标而不报错。改成响亮退出：拿不到显式关集就别开动态采集。
        # `parse_stages_arg`（`rl.volume_waves` 的共享解析器，训练侧
        # `_per_stage_quota` 同源）对空串/不可解析串抛 ValueError（不是返回空集）
        # ⇒ 这里先挡空串、再兜住 ValueError，两条路都收敛到同一条 SystemExit 文案。
        if not raw:
            raise SystemExit(
                "[volume] --stages 缺席，无法分关配额（动态采集不接受硬编码 fallback "
                "关集——请显式传 --stages，或关掉 target_transitions）"
            )
        try:
            stages = parse_stages_arg(raw)
        except ValueError as exc:
            raise SystemExit(
                f"[volume] --stages={raw!r} 无法解析为关号列表（{exc}）——动态采集的"
                "分关配额需要一个明确的关集"
            ) from exc
        if not stages:
            raise SystemExit(f"[volume] --stages={raw!r} 解析为空集，无法分关配额（请显式传关号）")
        return stages

    def _volume_est_samples(self) -> int:
        """局均 **samples** 估计：jsonl 的 trailing 均值（可 replay），无历史落课程声明值。

        量纲（2026-09-15 T9）：samples（jsonl 的 `samples` 字段 = nSamples 之和），
        **不是 ticks**——后者差 K 倍（x3 实测 samples/ticks≈0.1007），会让第二轮起
        采量偏离 10×（旧 `trailing_ticks_per_game` 就是这条 bug 的一半）。
        """
        declared = int(getattr(self.args, "est_samples_per_game", 0) or 0)
        return trailing_samples_per_game(self._jsonl_path, window=5, fallback=declared)

    def _iteration_pairs(self, it: int) -> list[tuple[int, int]]:
        """本轮对局表：动态采集串行路径**不预排初波**（连续配额实时派发）；其余 build_pairs。

        volume 仍返回按全局 est 均分的保守表——仅供 `rollout_src=node` / export_bundle
        等需要固定 pairs 的路径；串行 ` _volume_collect_continuous` **不用**这张表。
        """
        if not self._volume_active():
            self._volume_target = None
            self._volume_collected = None
            return build_pairs(self.args, it, self._rotate_seed)
        from rl.volume_quota import target_per_stage
        from rl.volume_waves import initial_games, wave_pairs

        args = self.args
        stages = self._volume_stages()
        est = self._volume_est_samples()
        target = int(args.target_transitions)
        g0 = initial_games(target, len(stages), est)
        self._volume_target = target
        self._volume_g0 = g0
        self._volume_est = est
        self._volume_waves = 0
        self._volume_capped = False
        self._volume_stage_ests = None  # 连续采集启动时现算
        pairs = wave_pairs(self._rotate_seed, it, {s: g0 for s in stages}, 0)
        log(
            f"[volume] it{it}: continuous quota mode target={target} "
            f"per_stage={target_per_stage(target, len(stages))} est_global={est} "
            f"（node/export 预排表 {len(pairs)} 局；串行路径按差额实时派发）"
        )
        return pairs

    def _volume_stage_ests_map(self) -> dict[int, int]:
        """分关 est_s：近轮盘上 shard 局均 nSamples，缺省回退全局 est。"""
        from rl.resume import trailing_stage_samples_per_game
        from rl.volume_waves import parse_stages_arg

        stages = self._volume_stages()
        fallback = int(self._volume_est or self._volume_est_samples() or 1)
        raw = str(getattr(self.args, "stages", "") or "")
        try:
            stages = parse_stages_arg(raw) if raw else stages
        except ValueError:
            pass
        return trailing_stage_samples_per_game(
            self._traj_root, stages, window_iters=3, fallback=fallback
        )

    def _dispatch_volume_wave(
        self, it: int, pairs: list[tuple[int, int]], dist_cfg: dict | None
    ) -> dict:
        """补波派发（复用首波同一派发路径，不另起调度器——计划 §3-P1）。

        `eval_on_round=False`：补波不重复派 eval（本轮评估已在首波派发/延迟派发处理，
        多派一次 = 重复评估 + 重复占集群）。stream 句柄一律丢弃（补波只走串行路径）。
        """
        (report, stream_meta, _thread, _gate, _child, _early) = dispatch_rollout_phase(
            self.args,
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
            False,
            course_fp=self._course_fp,
        )
        if stream_meta is not None:
            raise SystemExit("[volume] 补波落到 stream 路径——v1 只支持串行路径")
        return report

    def _volume_journal_replay(self, it: int) -> Any:
        """读 WAL：把本迭代的波次预算推到现在，并返回**未闭环**的那一波（待重放）。

        为何不能只靠账本重算（2026-09-15 e2e 证伪）：`wave_idx` 是**决策**而不是账本的
        函数——它同时是种子流的键（`[rotate_seed, tag, it, stage, wave]`）。重启后计数器
        若从 1 重头，就会用 wave-1 的种子去补 wave-3 的缺口 = 同观测史、不同波次序列
        （正是 plan §2.3.1 要禁止的「重新抛硬币」）。所以：
          · 波次预算按 WAL 续算（跨重启**不重领额度**，防「崩了就拿新一轮 3 波」）；
          · 停在波次中间（有 start 无 finish 的最后那一波）→ 原样重放它的对局表
            （同 wave_idx ⇒ 同种子流；已结算的由调度器剔除，缺口原样补齐）。
        """
        from rl.volume_waves import parse_wave_records

        path = Path(self._traj_dir) / "commit_journal.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        recs = parse_wave_records(lines, it)
        if not recs:
            return None
        last = recs[max(recs)]
        self._volume_waves = max(self._volume_waves, last.wave_idx + 1)
        return None if last.finished else last

    def _volume_topup(self, it: int, dist_cfg: dict | None) -> None:
        """按已结算 transitions 逐关补波直到达标/触顶（计划 §2.2.3/2.2.4）。

        账本口径：`settled_stage_totals`（只数已落盘 manifest 的 nSamples）——掉局零
        样本天然触发补采；超时局的 transitions 是真实 on-policy 数据，计入。
        每波决策进 WAL（`volume_wave` 相位）：预算跨重启续算（不重领额度），停在波次
        中间的那一波按 WAL 的对局表原样重放。**不能只按账本重算**——`wave_idx` 是决策
        而非账本的函数，它同时是种子流的键（首版「纯函数就够、无需重放」的假设已被
        e2e 证伪，见 `_volume_journal_replay`）。
        """
        if not self._volume_active():
            return
        args = self.args
        if self._stream_meta is not None:
            log("[volume] 流式路径 v1 不补波（保持老语义）——本轮只按初波结算，配额缺口不在本轮补齐")
            return
        if int(getattr(args, "collect_only", 0) or 0):
            return
        import dist_common
        from rl.volume_waves import (
            DEFAULT_MAX_WAVES,
            WAVE_PHASE,
            plan_topup,
            wave_pairs,
            wave_round_key,
        )

        stages = self._volume_stages()
        est = self._volume_est
        cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        target = int(args.target_transitions)
        journal = self._commit_journal()
        collected_total = 0
        # 重启续跑：先重放 WAL 里那一波未完成的决策（同 wave_idx + 同对局表 ⇒ 同种子流；
        # 已结算的由调度器剔除），再按账本继续后面的波。
        replay = self._volume_journal_replay(it)
        if replay is not None and replay.games:
            replay_pairs = wave_pairs(self._rotate_seed, it, replay.games, replay.wave_idx)
            log(
                f"[volume] it{it}: WAL 重放未完成的补波 w{replay.wave_idx} "
                f"games={replay.games} → {len(replay_pairs)} 局（同种子流，不重抛硬币）"
            )
            replay_report = self._dispatch_volume_wave(it, replay_pairs, dist_cfg)
            self._report = combine_reports([self._report, replay_report])
        while True:
            wver = dist_common.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir, wver, extra_wver=self._extra_wver, course_fp=self._course_fp
            )
            collected = {s: totals.get(s, (0, 0))[1] for s in stages}
            games_done = {s: totals.get(s, (0, 0))[0] for s in stages}
            collected_total = sum(collected.values())
            plan = plan_topup(
                stages=stages,
                collected=collected,
                target_transitions=target,
                est_samples_per_game=est,
                waves_done=self._volume_waves,
                games_done=games_done,
                max_waves=DEFAULT_MAX_WAVES,
                max_games_per_stage=cap,
                initial_g0=self._volume_g0,
            )
            if not plan.games_by_stage:
                met = [s for s, why in plan.stopped.items() if why == "quota_met"]
                unmet = {s: why for s, why in plan.stopped.items() if why != "quota_met"}
                if any(why == "game_cap" for why in unmet.values()):
                    # 硬顶 = 配额未满但停采。必须响亮：否则「采够了」与「踩顶了」在
                    # 日志上长得一模一样，而这正是长短局失衡 + est 偏差的指纹。
                    self._volume_capped = True
                    log(
                        f"[volume] WARN it{it}: 触单关局数硬顶"
                        f"（cap={cap or self._volume_g0 * 4}）但配额未满——"
                        f"shortfall={plan.shortfall}；已停采，iteration 事件打 capped 标"
                    )
                log(
                    f"[volume] it{it}: 补波收官 waves={self._volume_waves} "
                    f"collected={collected_total}/{target} samples "
                    f"达标关={len(met)}/{len(stages)}" + (f" 未达标={unmet}" if unmet else "")
                )
                break
            pairs = wave_pairs(self._rotate_seed, it, plan.games_by_stage, plan.wave_idx)
            round_key = wave_round_key(it, plan.wave_idx)
            journal.start(
                WAVE_PHASE,
                round_key,
                wave_idx=plan.wave_idx,
                games={str(s): n for s, n in plan.games_by_stage.items()},
                collected={str(s): collected[s] for s in stages},
                shortfall={str(s): plan.shortfall.get(s, 0) for s in stages},
                target=target,
                est=est,
            )
            log(
                f"[volume] it{it}: 补波 w{plan.wave_idx} "
                f"games={plan.games_by_stage}（缺口 {plan.shortfall}）→ {len(pairs)} 局"
            )
            wave_report = self._dispatch_volume_wave(it, pairs, dist_cfg)
            # 报告合并（既有多轮聚合口径：combine_reports 吃单轮报告，与远端单局摘要同构）
            self._report = combine_reports([self._report, wave_report])
            self._volume_waves = plan.wave_idx + 1
            journal.finish(
                WAVE_PHASE,
                round_key,
                games={str(s): n for s, n in plan.games_by_stage.items()},
            )
            if plan.capped:
                # 硬顶 = 配额未满但停采：必须响亮（否则「采够了」与「踩顶了」在日志上
                # 长得一模一样，而这正是长短局失衡 + est 偏差的指纹）。
                self._volume_capped = True
                log(
                    f"[volume] WARN it{it}: 触单关局数硬顶（cap={cap or self._volume_g0 * 4}）"
                    f"但配额未满——shortfall={plan.shortfall}；已停采该关，"
                    "iteration 事件打 transitions_capped"
                )
        self._volume_collected = collected_total
        # it 级 rollout 聚合（2026-09-19）：combine_reports 已把各波
        # weights_dist_start_ts / collect_end_ts 压成 pure_collect_sec
        # （首波分发 → 全部样本齐）。多波时打一条便于对照 dashboard。
        if self._volume_waves > 1 and self._report.get("pure_collect_sec") is not None:
            agg_ok = self._report.get("rollout_collect_aggregated")
            log(
                f"[volume] it{it}: rollout 聚合 waves={self._report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._report['pure_collect_sec']}"
                f"（首波分发→样本齐；aggregated={agg_ok}）"
            )

    def _volume_collect_continuous(self, it: int, dist_cfg: dict | None) -> None:
        """配额感知连续采集（2026-09-19 用户指令；VOLUME_RULE_V2）。

        **退役离散补波**：loop 读账本 → 按各关 `quota - collected - inflight*est_s`
        决定下一批局数 → 派发 → 再读账本，直到各关达标 / game_cap / batch 安全阀。
        即将足额（软停）不再派；差额大的关多派。种子 = `(it, stage, 本轮第k局)`。

        §15.5：相对 wave 规则的新语料语义 —— 课程若从 wave 迁到 continuous，
        应 fresh `--out/--traj`（用户已知）。
        """
        if not self._volume_active():
            return
        args = self.args
        if self._stream_meta is not None:
            log("[volume] 流式路径不支持连续配额 v2（保持 stream 老语义）")
            from rl.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        if int(getattr(args, "collect_only", 0) or 0):
            from rl.reports import adopt_volume_report

            self._report = adopt_volume_report(None)
            return
        import dist_common
        from rl.resume import settled_stage_totals
        from rl.volume_quota import (
            DEFAULT_MAX_BATCHES,
            continuous_pairs,
            default_game_cap,
            plan_continuous_batch,
            target_per_stage,
        )

        stages = self._volume_stages()
        target = int(args.target_transitions)
        quota = target_per_stage(target, len(stages))
        est_global = int(self._volume_est or self._volume_est_samples() or 1)
        est_s = self._volume_stage_ests_map()
        self._volume_stage_ests = est_s
        game_cap = int(getattr(args, "max_games_per_stage", 0) or 0)
        if game_cap <= 0:
            game_cap = default_game_cap(quota, est_global)
        max_batches = int(getattr(args, "volume_max_batches", 0) or DEFAULT_MAX_BATCHES)
        course_fp = self._course_fp
        extra_wver = self._extra_wver
        start_idx = {s: 0 for s in stages}
        combined: dict | None = None
        self._volume_capped = False
        self._volume_waves = 0

        while self._volume_waves < max_batches:
            wver = dist_common.weights_fingerprint(args.out)
            totals = settled_stage_totals(
                self._traj_dir, wver, extra_wver=extra_wver, course_fp=course_fp
            )
            collected = {s: int(totals.get(s, (0, 0))[1]) for s in stages}
            games_done = {s: int(totals.get(s, (0, 0))[0]) for s in stages}
            # 同步 dispatch：批间无在飞；批内调度器自己竞速。软停用批前账本。
            inflight = {s: 0 for s in stages}
            plan = plan_continuous_batch(
                stages=stages,
                collected=collected,
                inflight=inflight,
                target_transitions=target,
                ests=est_s,
                games_done=games_done,
                game_cap=game_cap,
                fallback_est=est_global,
            )
            if not plan.games_by_stage:
                break
            pairs = continuous_pairs(
                self._rotate_seed, it, plan.games_by_stage, start_idx
            )
            for s, n in plan.games_by_stage.items():
                start_idx[s] = start_idx.get(s, 0) + int(n)
            self._volume_waves += 1
            short_note = {s: plan.shortfall.get(s, 0) for s in stages if plan.shortfall.get(s, 0)}
            log(
                f"[volume] it{it}: batch{self._volume_waves} "
                f"games={plan.games_by_stage}（差额 {short_note} "
                f"est_s={ {s: est_s.get(s) for s in plan.games_by_stage} }）"
            )
            wave_report = self._dispatch_volume_wave(it, pairs, dist_cfg)
            combined = (
                wave_report if combined is None else combine_reports([combined, wave_report])
            )
            if plan.capped:
                self._volume_capped = True

        wver = dist_common.weights_fingerprint(args.out)
        totals = settled_stage_totals(
            self._traj_dir, wver, extra_wver=extra_wver, course_fp=course_fp
        )
        collected_total = sum(int(totals.get(s, (0, 0))[1]) for s in stages)
        unmet = {
            s: quota - int(totals.get(s, (0, 0))[1])
            for s in stages
            if int(totals.get(s, (0, 0))[1]) < quota
        }
        if unmet:
            self._volume_capped = True
            log(
                f"[volume] WARN it{it}: 连续配额未满 batches={self._volume_waves}/{max_batches} "
                f"unmet={unmet} per_stage_quota={quota} game_cap={game_cap}——"
                f"est 持续低估或触硬顶（非静默；pooled={collected_total}/{target}）"
            )
        stage_stats = {
            s: {
                "collected": int(totals.get(s, (0, 0))[1]),
                "games": int(totals.get(s, (0, 0))[0]),
                "est_s": int(est_s.get(s, est_global)),
            }
            for s in stages
        }
        log(
            f"[volume] it{it}: continuous 收官 batches={self._volume_waves} "
            f"collected={collected_total}/{target} 达标关="
            f"{len(stages) - len(unmet)}/{len(stages)} stats={stage_stats}"
        )
        self._volume_collected = collected_total
        # 报告真源 = 本轮盘上 shard（与 settled_stage_totals 同源）；wave 只补时间锚点。
        # 禁止 combine 进上一轮 _report（pure_collect 起点会钉在历史波，§adopt_volume_report）。
        # 配额已满重启 ⇒ batches=0，若只 adopt(combined=None) 会把除零保护的 winRate=0
        # 写进账本（x20-steady it76 / §107）——必须从 shard 回填 outcomes。
        from rl.reports import merge_volume_report
        from rl.resume import resumed_manifests

        disk_reports = resumed_manifests(
            self._traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
        )
        if stages:
            stage_set = {int(s) for s in stages}
            filtered: list[dict] = []
            for r in disk_reports:
                st = r.get("stage")
                if st is None or int(st) in stage_set:
                    filtered.append(r)
            disk_reports = filtered
        self._report = merge_volume_report(combined, disk_reports)
        if self._volume_waves > 0 and self._report.get("pure_collect_sec") is not None:
            log(
                f"[volume] it{it}: rollout 聚合 batches={self._report.get('rollout_collect_waves', self._volume_waves)} "
                f"pure_collect_sec={self._report['pure_collect_sec']}"
                f"（首批分发→样本齐；aggregated={self._report.get('rollout_collect_aggregated')}）"
            )
