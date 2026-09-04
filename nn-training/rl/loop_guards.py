"""loop_guards —— TrainingGuards mixin：训练护栏（2026-09-02 从 rl/loop_core.py 拆出）。

F4 熔断（breaker_update 门控 + circuit_break 落账 + KL/熵漂移告警）、止损判定
（D4 泛化 + P1-9 统计显著连续 2 轮）、keepIters 目录轮转。纯逻辑在 rl/breaker.py
与 rl/stop_loss.py；本模块只做「读训练状态 → 判门 → 落日志/事件」的编排。

由 TrainingLoop(TrainingSteps, TrainingGuards) 混入；依赖的实例属性（_agg、
_report、_kl_streak、_tripped 等）在 TrainingLoop.__init__/迭代方法中赋值，
此处仅声明类型。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rl.breaker import (
    ENT_COLLAPSE_DROP,
    KL_BREAK,
    KL_BREAK_CONSEC,
    KL_WARN,
    breaker_update,
)
from rl.events import write_circuit_break
from rl.log import log
from rl.stop_loss import eval_sigma, stop_loss_hit


class TrainingGuards:
    """训练护栏 mixin：熔断 / 止损 / 目录轮转。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _agg: Any
    _report: dict
    _jsonl_path: Any
    _kl_streak: int
    _ent_streak: int
    _tripped: Any
    _prev_entropy: Any
    _stop_loss_streak: int
    _traj_root: Any

    def _breaker(self, it: int) -> bool:
        """F4 熔断 + KL/熵漂移告警（纯逻辑在 rl/breaker.py）。返回 True = 熔断停车。"""
        args = self.args
        agg = self._agg
        assert agg is not None  # 调用方已门控（agg None 的轮不进此方法）
        # intent/goal 用放宽的 KL 熔断阈值（原 intent_rl 专属 --kl-break
        # 0.6 / --kl-break-consec 3，避免误熔断 Bug D；per-tick 用默认 0.15/3）。
        _kl_break = args.kl_break if args.mode in ("intent", "goal") else KL_BREAK
        _kl_consec = args.kl_break_consec if args.mode in ("intent", "goal") else KL_BREAK_CONSEC
        kl_streak, ent_streak, tripped_now = breaker_update(
            self._kl_streak,
            self._ent_streak,
            kl=agg["kl"],
            entropy=agg["entropy"],
            win_rate=self._report["winRate"],
            kl_break=_kl_break,
            kl_consec=_kl_consec,
        )
        self._kl_streak = kl_streak
        self._ent_streak = ent_streak
        if tripped_now is not None:
            self._tripped = tripped_now
        if self._tripped is not None:
            write_circuit_break(
                self._jsonl_path, it, self._tripped, agg, kl_streak, ent_streak, self._report, args
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
                    try:
                        shutil.rmtree(
                            old, ignore_errors=True
                        )  # 沙箱删除保护拦截时跳过（磁盘轮转降级）
                    except BaseException:
                        pass
            # H9：清理旧 job 目录（已完成的 job 不再需要 payload 与结果文件）
            if getattr(args, "ppo", "local") == "remote":
                job_root = Path(
                    getattr(args, "remote_job_root", "")
                    or str(self._traj_root / "remote-jobs")
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
                            try:
                                shutil.rmtree(jd, ignore_errors=True)
                            except BaseException:
                                pass
