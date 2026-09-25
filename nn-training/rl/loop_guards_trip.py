"""loop_guards_trip —— TrainingGuardsTrip mixin：**过程面**硬边界（S4 第二十三刀）。

「更新过程健不健康」这条轴上的两条否决信号，都是**连击式**（连续 N 轮才动作）：
  · `_breaker`   —— F4 熔断（NaN/±inf 出现即熔断，不走连击；KL/熵连击）+ 漂移告警；
  · `_stop_loss` —— D4 泛化止损（Δ ≤ −2σ 且**连续 2 轮**才确认停车）。

与 `rl/loop_guards_leg.py`（结果面：本腿退回了吗 / 比对照臂差吗）的分工是**同一条
「该不该停」轴上的两极**：这里看**更新过程**（kl / ent / Δ 显著度），那里看**结果有没
有退回去**，两者正交、都要。判据本体在 `rl/breaker.py` / `rl/stop_loss.py`（纯函数），
本模块只做「读训练状态 → 判 → 落账/日志」。

由 `rl/loop_guards.py::TrainingGuards` 继承（方向：调用者依赖被调用者）；两者共用的
sink `self._ledger_apply` 留在组合根。依赖的实例属性此处仅声明类型。
"""

from __future__ import annotations

import math
from typing import Any

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
from rl.events import write_circuit_break, write_gate_verdict, write_stop_loss
from rl.log import log
from rl.stop_loss import eval_sigma, stop_loss_hit


def _num_or_none(v: Any) -> float | None:
    """数值化（bool / 非数值 → None）——**只用于观测字段落账**，绝不参与判定。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _is_nonfinite(v: Any) -> bool:
    """NaN/±inf 检测：NaN 与任何阈值比较恒 False（熔断与告警永不触发），
    inf 则相反（一路狂触发）。两者都是数值崩坏，出现即熔断，不走连击。"""
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


class TrainingGuardsTrip:
    """过程面硬边界 mixin（F4 熔断 / 止损）：连击式，看更新过程健不健康。"""

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
    #: 共享 sink：写入账本后并入 `LedgerView`（真实现住组合根 `rl/loop_guards.py`）。
    _ledger_apply: Any

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
            self._ledger_apply(
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"circuit-break: {self._tripped}",
                    decider="loop",
                )
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
        且**连续 2 轮**才停车。返回 True = 停车。

        R2a：连击的每次**状态转移**落 `stop_loss` 事件（命中 / 从 >0 回落），
        使「已确认一次」在重启后仍成立——否则重启即归零，白跑一整轮。
        """
        args = self.args
        if stop_loss_hit(args.mode, args.stop_loss_at, args.stop_loss_delta, it, eval_rec):
            assert eval_rec is not None  # stop_loss_hit 已保证非 None（delta 可读）
            self._stop_loss_streak += 1
            self._ledger_apply(
                write_stop_loss(
                    self._jsonl_path, it, self._stop_loss_streak, _num_or_none(eval_rec.get("delta"))
                )
            )
            sigma = eval_sigma(eval_rec)
            log(
                f"STOP-LOSS: iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                f"(σ={'--' if sigma is None else f'{sigma:.4f}'}, "
                f"z·σ={'--' if sigma is None else f'{2.0 * sigma:.4f}'}) "
                f"streak={self._stop_loss_streak}/2 — waiting for confirmation"
            )
        elif self._stop_loss_streak:
            # 只在**从 >0 回落**时落账（每轮都写会把账本淹掉；0 → 0 无需记录）。
            self._stop_loss_streak = 0
            self._ledger_apply(write_stop_loss(self._jsonl_path, it, 0))
        if self._stop_loss_streak >= 2:
            assert eval_rec is not None
            stop_reason = (
                f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                f"<= {args.stop_loss_delta:+.4f} (显著: Δ≤−2σ) × 2 轮 — stop-loss"
            )
            log(f"STOP-LOSS CONFIRMED: {stop_reason}")
            return True
        return False
