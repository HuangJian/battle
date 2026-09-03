"""loop_core —— TrainingLoop：RL 迭代主循环（2026-09-02 从 rl/loop.py 类化）。

入口 rl/loop.py::run_training 是薄包装（构造 TrainingLoop + run()）。本模块
承载主循环骨架：setup / run() 迭代编排 / 迭代目录 / 采集派发接线；单轮结算与
梯度步在 TrainingSteps mixin（rl/loop_steps.py），训练护栏在 TrainingGuards
mixin（rl/loop_guards.py）——mixin 方法以 self.* 共享 TrainingLoop 实例状态。

重构纪律：控制流与日志逐字节沿用旧 run_training 内联实现——每段提取为私有
方法，跨阶段共享状态放 self._*（run() 局部别名 + 实例属性，不重排执行顺序）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.breaker import CIRCUIT_EXIT_CODE
from rl.collect_only import precollect_snapshot_wver
from rl.course import build_pairs
from rl.events import log_iter_error, write_run_start
from rl.log import log
from rl.loop_guards import TrainingGuards
from rl.loop_steps import TrainingSteps
from rl.modes import get_backend
from rl.queue import REPO_ROOT, RUN_ID
from rl.resume import completed_pairs, last_completed_iter, last_rotate_seed
from rl.rollout_phase import (
    dispatch_rollout_phase,
    join_precollect_child,
    spawn_next_collect,
)


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


class TrainingLoop(TrainingSteps, TrainingGuards):
    """RL 迭代主循环（run_training 的 OO 化；run() 为入口，失败重试内置）。

    MRO：TrainingSteps（结算/PPO/导出/eval join/落账）→ TrainingGuards（熔断/
    止损/轮转）→ 本类（setup / 迭代编排 / 目录 / 采集派发）。
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
        self._rollout_sec = 0.0
        self._ppo_sec = 0.0
        self._total_steps = 0
        self._chunks_n = 0
        # 迭代期共享状态（在对应阶段方法内赋值；此处先声明供 mypy/阅读定位）
        self._traj_dir: Path = self._traj_root
        self._extra_wver: str | None = None
        self._report: dict = {}
        self._stream_meta: dict | None = None
        self._eval_thread: threading.Thread | None = None
        self._eval_gate: threading.Event | None = None
        self._kl_cum = None
        self._halted_flag = False
        self._dropped_games = None
        self._load_sec = None
        self._tail_drain_sec = None
        self._waves_n = None
        self._eval_join_sec = 0.0

    # ------------------------------------------------------------------ 编排

    def run(self) -> None:
        args = self.args
        self._setup()
        it = self._start_it - 1
        while args.iters <= 0 or it < args.iters:
            it += 1
            # 吞吐 T4：本轮开头检查预采子进程产出（句柄消费后归零）
            self._collect_child = join_precollect_child(
                self._collect_child, self._traj_root, it, args
            )
            if self._deadline is not None and time.time() >= self._deadline:
                log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
                break
            self._traj_dir = self._traj_root / f"it{it}"
            try:
                self._prepare_iter_dir(it)
                # M1c：本轮课程上下文（holder + ppo_schedule）——先于任何 shard 加载
                self._course_iter(it)
                log(f"[run_rl] === iteration {it}/{self._total} ===")
                pairs = build_pairs(args, it, self._rotate_seed)
                # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
                # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
                dist_cfg = dist_common.load_dist_config()
                t_rollout = time.time()
                self._rollout_phase(it, pairs, dist_cfg, self._eval_on_round(it))
                self._log_report(it, t_rollout)
                self._serial_ppo(it)
                self._export_weights(it)
                eval_rec = self._join_eval(it)
                self._record_iteration(it)
                # M1c：每 iter 指标统计落盘（非致命）
                self._write_iter_stats(it)
                # 每轮 ppo_backend 写回后自动生成巡检 HTML（intent/goal 总是生成；
                # per-tick 仅默认 traj）
                if self._auto_inspect:
                    run_inspect(self.bun, it, traj_dir=self._traj_root)
                # F4 circuit breaker（纯逻辑在 rl/breaker.py）。agg 为 None 的轮
                # （流式 checkpoint-complete，无任何梯度步）不计连击也不告警——
                # 本来就没有发生新的策略更新。break (not raise)：下方 except 会吞掉重试。
                if self._agg is not None and self._breaker(it):
                    break
                if self._stop_loss(it, eval_rec):
                    break
                self._rotate_cleanup(it)
                # 吞吐 T4：双缓冲 spawn 下一轮预采（下一轮开头 join）
                self._collect_child = spawn_next_collect(
                    args, it, self._stream_meta, self._spawned_early
                )
                self._consec_fail = 0
            except SystemExit as e:
                self._consec_fail += 1
                log_iter_error(self._jsonl_path, it, f"SystemExit: {e}")
                log(
                    f"[run_rl] it{it} FAILED (SystemExit: {e}); "
                    f"consecutive={self._consec_fail}/5 — retry same iteration"
                )
                if self._consec_fail >= 5:
                    raise
                time.sleep(30)
                it -= 1  # 原地重试同一迭代（resume 保留已完成 shard + ppo_backend ckpt，不重跑已完局）
            except Exception as e:
                self._consec_fail += 1
                log_iter_error(self._jsonl_path, it, f"{type(e).__name__}: {e}")
                log(
                    f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); "
                    f"consecutive={self._consec_fail}/5 — retry same iteration"
                )
                if self._consec_fail >= 5:
                    raise
                time.sleep(30)
                it -= 1  # 同上：失败迭代不前跳，杜绝静默跳轮丢语料

        if self._tripped is not None:
            sys.exit(CIRCUIT_EXIT_CODE)
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")

    # ---------------------------------------------------------------- 启动

    def _setup(self) -> None:
        args = self.args

        # ===== 训练路径延迟导入（B7，2026-09-02）：collect-only 子进程已提前 return，
        # 此刻起才允许拉起 torch / ppo.* / models.*（CPU 上 ~3-8s 的 torch 加载不再
        # 出现在每轮的双缓冲预采子进程里）。=====
        import numpy as np
        import torch

        import ppo.engine as ppo_mod
        import ppo.goal as ppo_goal
        import ppo.intent as ppo_intent
        from data.weights_io import save_weights_json
        from rl.model_build import build_model

        self._ppo_mod = ppo_mod
        self._ppo_goal = ppo_goal
        self._ppo_intent = ppo_intent
        self._save_weights_json = save_weights_json

        np.random.seed(args.seed)
        self.ppo_backend = get_backend(args.mode)

        device = torch.device("cpu")
        model = build_model(args.bc, args.out, mode=args.mode, workers=args.workers)
        model.to(device)
        self._device = device
        self._model = model
        ref_model = None
        if args.mode in ("intent", "goal") and args.kickstart_kl > 0:
            # kickstarting 参考策略：B′ 冻结快照（须在 build_model 完成 init-from 落盘
            # args.out 之后构建）。warmup 冻结主干+三头 → 策略与 B′ 一致。
            ref_model = self.ppo_backend.build_rl_net(args.out)
            if args.mode == "goal":
                self._ppo_goal.load_goal_weights(ref_model, args.out)
            else:
                self._ppo_intent.load_intent_weights(ref_model, args.out)
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
        self._ref_model = ref_model
        # M1c 冻结层/头（plan §7）：freeze/freeze_heads 前缀表 → requires_grad=False，
        # 优化器只收可训参数（前缀 = name.startswith，前缀间不得父子歧义，见单测）。
        freeze_prefixes = list(getattr(args, "freeze", []) or []) + list(
            getattr(args, "freeze_heads", []) or []
        )
        n_frozen = 0
        if freeze_prefixes:
            for n, p_ in model.named_parameters():
                if any(n.startswith(pre) for pre in freeze_prefixes):
                    p_.requires_grad = False
                    n_frozen += 1
            log(
                f"[run_rl] freeze: {n_frozen} params frozen by prefixes "
                f"{freeze_prefixes}（优化器只含可训参数）"
            )
        trainable = [p_ for p_ in model.parameters() if p_.requires_grad]
        if not trainable:
            raise SystemExit(f"[run_rl] freeze 前缀 {freeze_prefixes} 冻结了全部参数——没有可训参数")
        self._opt = torch.optim.Adam(trainable, lr=args.lr)
        if n_frozen == 0 and freeze_prefixes:
            log(f"[run_rl] WARN freeze prefixes matched nothing: {freeze_prefixes}")

        traj_root = Path(args.traj)
        traj_root.mkdir(parents=True, exist_ok=True)
        self._traj_root = traj_root
        self._jsonl_path = traj_root / "training_log.jsonl"

        # 续跑继承 rotateSeed：已有 run_start 历史 → 沿用其 rotateSeed（课程连续 → it 续跑时
        # 下轮 (stage,seed) 与已落盘局一致 → 断点续跑剔除生效，不重跑已完成局）。
        # 全新开始（无 jsonl 历史，例如用户清空重建）才用当前时刻抖动种子。
        prev_rs = last_rotate_seed(self._jsonl_path)
        if prev_rs is not None:
            rotate_seed = prev_rs
            log(f"[run_rl] resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
        else:
            rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2**32)
        self._rotate_seed = rotate_seed
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
        self._consec_fail = 0
        self._kl_streak = 0  # F4: consecutive iters with kl >= KL_BREAK
        self._ent_streak = (
            0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < MAX_WINRATE
        )
        self._stop_loss_streak = 0  # P1-9: 统计显著止损（Δ≤−2σ）的连续轮数，≥2 才停车
        self._tripped = None
        # it 断点续跑：--start-it 显式，否则自动 = 日志最后一个完成迭代 + 1
        start_it = (
            args.start_it
            if args.start_it is not None
            else (last_completed_iter(self._jsonl_path) + 1)
        )
        if start_it > 1:
            log(
                f"[run_rl] resume: continuing from iteration {start_it} "
                f"(weights resume from {args.out})"
            )
        self._start_it = start_it
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
        have_resume = bool(completed_pairs(traj_dir, wver, extra_wver=extra_wver))
        if have_resume:
            traj_dir.mkdir(parents=True, exist_ok=True)
            log(
                f"[run_rl] resume iteration {it}: keeping existing shards + ppo_backend checkpoint"
                + (f" (precollect snapshot wver {extra_wver[:12]}…)" if extra_wver else "")
            )
        else:
            if traj_dir.exists():
                try:
                    shutil.rmtree(traj_dir)  # 沙箱删除保护拦截时跳过（保留旧目录，训练照常）
                except BaseException:
                    pass
            traj_dir.mkdir(parents=True)

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
            )
        )
        self._report = report
        self._stream_meta = stream_meta
        self._eval_thread = eval_thread
        self._eval_gate = eval_gate
        self._collect_child = collect_child
        self._spawned_early = spawned_early
