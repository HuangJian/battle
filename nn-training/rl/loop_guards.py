"""loop_guards —— TrainingGuards mixin：训练护栏（2026-09-02 从 rl/loop_core.py 拆出）。

F4 熔断（breaker_update 门控 + circuit_break 落账 + KL/熵漂移告警）、止损判定
（D4 泛化 + P1-9 统计显著连续 2 轮）、**课程结束门（M1 第四守卫，plan/
course-exit-and-shutdown.md §4）**、keepIters 目录轮转。纯逻辑在 rl/breaker.py、
rl/stop_loss.py 与 rl/gate_check.py；本模块只做「读训练状态 → 判门 → 落日志/事件」
的编排。

由 TrainingLoop(TrainingSteps, TrainingGuards) 混入；依赖的实例属性（_agg、
_report、_kl_streak、_tripped 等）在 TrainingLoop.__init__/迭代方法中赋值，
此处仅声明类型。
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from platform_utils import rmtree_best_effort
from rl.breaker import (
    ENT_BREAK,
    ENT_BREAK_CONSEC,
    ENT_BREAK_MAX_WINRATE,
    ENT_COLLAPSE_DROP,
    KL_BREAK,
    KL_BREAK_CONSEC,
    KL_WARN,
    breaker_update,
)
from rl.events import write_circuit_break, write_gate_verdict
from rl.gate_check import (
    BudgetInfo,
    evaluate,
    first_run_start_ts,
    load_override,
    read_trend_rows,
)
from rl.log import log
from rl.stop_loss import eval_sigma, stop_loss_hit
from rl.workdir_sweep import sweep_failed_wave_dirs


def _is_nonfinite(v: Any) -> bool:
    """NaN/±inf 检测：NaN 与任何阈值比较恒 False（熔断与告警永不触发），
    inf 则相反（一路狂触发）。两者都是数值崩坏，出现即熔断，不走连击。"""
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


class TrainingGuards:
    """训练护栏 mixin：熔断 / 止损 / 目录轮转。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _agg: Any
    _report: dict
    _jsonl_path: Any
    _kl_streak: int
    _ent_streak: int
    _ent_peak: float | None
    _tripped: Any
    _prev_entropy: Any
    _stop_loss_streak: int
    _traj_root: Any
    _traj_dir: Any
    _course_fp: Any
    _eval_on_round: Any

    def _breaker(self, it: int) -> bool:
        """F4 熔断 + KL/熵漂移告警（纯逻辑在 rl/breaker.py）。返回 True = 熔断停车。"""
        args = self.args
        agg = self._agg
        assert agg is not None  # 调用方已门控（agg None 的轮不进此方法）
        # NaN 检测（plan §8 M0）：NaN 与任何阈值比较恒 False → KL/ENT 熔断与告警
        # **永不触发**（数值崩坏时训练会带着坏权重一路跑到 iters 上限）。必须在
        # 进 breaker_update 之前显式拦，且不走连击——出现即熔断。
        nan_keys = [k for k in ("policy", "value", "entropy", "kl") if _is_nonfinite(agg.get(k))]
        if nan_keys:
            self._tripped = f"non-finite agg[{','.join(nan_keys)}]"
        # intent/goal 用放宽的 KL 熔断阈值（原 intent_rl 专属 --kl-break
        # 0.6 / --kl-break-consec 3，避免误熔断 Bug D；per-tick 用默认 0.15/3）。
        _kl_break = args.kl_break if args.mode in ("intent", "goal") else KL_BREAK
        _kl_consec = args.kl_break_consec if args.mode in ("intent", "goal") else KL_BREAK_CONSEC
        # ENT 三阈值全课程可配（热启动课程下调 ent_break 收紧保护）；ent_peak 为
        # 本轮之前的历史最大熵——相对崩塌判定的基线（None = 冷启动首轮，退回绝对判定）。
        kl_streak, ent_streak, tripped_now = breaker_update(
            self._kl_streak,
            self._ent_streak,
            kl=agg["kl"],
            entropy=agg["entropy"],
            win_rate=self._report["winRate"],
            kl_break=_kl_break,
            kl_consec=_kl_consec,
            ent_break=getattr(args, "ent_break", ENT_BREAK),
            ent_consec=getattr(args, "ent_break_consec", ENT_BREAK_CONSEC),
            ent_max_winrate=getattr(args, "ent_break_max_winrate", ENT_BREAK_MAX_WINRATE),
            ent_peak=self._ent_peak,
        )
        self._kl_streak = kl_streak
        self._ent_streak = ent_streak
        self._ent_peak = (
            agg["entropy"] if self._ent_peak is None else max(self._ent_peak, agg["entropy"])
        )
        if tripped_now is not None:
            self._tripped = tripped_now
        if self._tripped is not None:
            write_circuit_break(
                self._jsonl_path, it, self._tripped, agg, kl_streak, ent_streak, self._report, args
            )
            # ds-P1-1：熔断同写 ABORT 判决——lattice 的 ABORT 项不能只有人工
            # override 一条路，否则 notebook/执行面在真正的崩塌场景读不到判决。
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"circuit-break: {self._tripped}",
                decider="loop",
            )
            log(f"[run_rl] CRITICAL CIRCUIT-BREAK it{it}: {self._tripped}")
            log(
                f"[run_rl] training PAUSED; weights kept at {args.out}; "
                f"inspect policy behavior before relaunching"
            )
            return True

        if agg["kl"] > KL_WARN:
            log(
                f"[run_rl] WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast; "
                f"consider lower lr/epochs"
            )
        if (
            self._prev_entropy is not None
            and self._prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP
        ):
            log(
                f"[run_rl] WARNING entropy dropped {self._prev_entropy - agg['entropy']:.3f} "
                f"in one iteration (now {agg['entropy']:.3f}) — possible premature convergence"
            )
        self._prev_entropy = agg["entropy"]
        return False

    def _stop_loss(self, it: int, eval_rec) -> bool:
        """止损判定（D4 泛化，仅 intent/goal 生效）：eval_summary 的 Δ（相对 baseline）
        在 stop-loss-at 迭代 ≤ stop-loss-delta → 停车。P1-9：Δ 须统计显著（≤ −2σ）
        且**连续 2 轮**才停车。返回 True = 停车。"""
        args = self.args
        if stop_loss_hit(args.mode, args.stop_loss_at, args.stop_loss_delta, it, eval_rec):
            assert eval_rec is not None  # stop_loss_hit 已保证非 None（delta 可读）
            self._stop_loss_streak += 1
            sigma = eval_sigma(eval_rec)
            log(
                f"STOP-LOSS: iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                f"(σ={'--' if sigma is None else f'{sigma:.4f}'}, "
                f"z·σ={'--' if sigma is None else f'{2.0 * sigma:.4f}'}) "
                f"streak={self._stop_loss_streak}/2 — waiting for confirmation"
            )
        else:
            self._stop_loss_streak = 0
        if self._stop_loss_streak >= 2:
            assert eval_rec is not None
            stop_reason = (
                f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                f"<= {args.stop_loss_delta:+.4f} (显著: Δ≤−2σ) × 2 轮 — stop-loss"
            )
            log(f"STOP-LOSS CONFIRMED: {stop_reason}")
            return True
        return False

    def _gate(self, it: int) -> bool:
        """第四守卫：课程结束门（M1，plan/course-exit-and-shutdown.md §4）。

        只在**本轮派发了干净评估**时求值（门读 eval_summary 行，没评估 = 没新数据，
        顺延一个节拍比用陈旧行硬判诚实）。判决 → 动作按 §4.3：
        HOLD 继续；其余写 `gate_verdict` 事件后优雅 break（在途 shard 已结算、
        账本对账完整）。无 gates 块的课程（老课程）恒 False——零行为变化。

        返回 True = 训练该停（调用方 break）。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        spec = getattr(course, "gates", None)
        if spec is None:
            return False
        try:
            eval_this_round = bool(self._eval_on_round(it))
        except AttributeError:
            eval_this_round = False
        if not eval_this_round:
            return False

        course_fp = str(self._course_fp or "")
        now = time.time()
        traj_root = Path(self._traj_root)
        try:
            rows = read_trend_rows(traj_root / "eval_log.jsonl", course_fp=course_fp)
            override = load_override(traj_root.parent / str(spec.override_file))
        except Exception as e:
            log(f"[run_rl] gate it{it}: 求值输入读取失败（{type(e).__name__}: {e}）— 本轮跳过门")
            return False

        budget = BudgetInfo(
            started_at=first_run_start_ts(self._jsonl_path),
            max_hours=float(getattr(args, "max_hours", 0.0) or 0.0),
            iters=int(getattr(args, "iters", 0) or 0),
            cur_iter=it,
            train_sec=float(getattr(self, "_train_sec_total", 0.0) or 0.0),
        )
        try:
            res = evaluate(
                course,
                rows,
                budget=budget,
                now=now,
                override=override,
                course_fp=course_fp,
                decider="loop",
            )
        except Exception as e:
            log(f"[run_rl] gate it{it}: 求值异常（{type(e).__name__}: {e}）— 本轮跳过门")
            return False

        if not res.terminal:
            log(f"[run_rl] gate it{it}: HOLD — {res.reason}")
            return False

        try:
            write_gate_verdict(
                self._jsonl_path,
                it,
                res.verdict,
                res.reason,
                route=res.route,
                readings=[r.to_dict() for r in res.readings],
                override=dict(res.override) if res.override else None,
                seeds=res.seeds,
                decider="loop",
            )
        except OSError as e:
            log(f"[run_rl] gate it{it}: gate_verdict 落盘失败（{e}）— 仍按判决停车")
        log(f"[run_rl] GATE {res.verdict} it{it}: {res.reason}")
        return True

    def _rotate_cleanup(self, it: int) -> None:
        """keepIters 目录轮转（沙箱删除保护拦截时静默降级，磁盘轮转照旧）。

        H9（review-hy）：远程模式的 job 目录（remote-jobs/<job_id>/）不在 it{N}/
        下，keep_iters 轮转不会带走它。每轮 payload.zip + result.json + ppo_ckpt_remote
        线性增长。扩展清理：扫描 job 目录，清理已完成且迭代 <= it - keep_iters 的 job。
        """
        args = self.args
        if args.keep_iters > 0:
            for old in self._traj_root.glob("it*"):
                try:
                    n_old = int(old.name[2:])
                except ValueError:
                    continue
                if n_old <= it - args.keep_iters:
                    # 沙箱删除保护拦截时跳过（磁盘轮转降级）
                    rmtree_best_effort(old, ignore_errors=True)
            # H9：清理旧 job 目录（已完成的 job 不再需要 payload 与结果文件）
            if getattr(args, "ppo", "local") == "remote":
                job_root = Path(
                    getattr(args, "remote_job_root", "") or str(self._traj_root / "remote-jobs")
                )
                if job_root.exists():
                    cutoff = it - args.keep_iters
                    for jd in job_root.iterdir():
                        if not jd.is_dir():
                            continue
                        mf = jd / "manifest.json"
                        if not mf.exists():
                            continue
                        try:
                            mm = json.loads(mf.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        jit = mm.get("it")
                        if not isinstance(jit, int):
                            continue
                        if jit <= cutoff and (jd / "result" / "result.json").exists():
                            rmtree_best_effort(jd, ignore_errors=True)
            # §374 同步（2026-09-08）：本地采样波次目录收敛——本轮已全部结算（无在飞
            # 子进程），清失败/废弃局的孤儿 w* 目录（无 _rl_report.json：部分 shard +
            # rollout.log 是死重，PPO/resume 都不消费）。完整波次目录是语料，永不删
            # （resume 依赖）；删除失败（沙箱保护/占用）跳过，训练照常。
            try:
                swept = sweep_failed_wave_dirs(self._traj_dir, log=log)
                if swept:
                    log(f"[run_rl] workdir-sweep it{it}: {swept} failed wave dir(s) removed")
            except BaseException:
                pass
