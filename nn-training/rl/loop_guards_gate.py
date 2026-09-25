"""loop_guards_gate —— TrainingGuardsGate mixin：课程结束门的求值与落地（S4 第二十三刀）。

**第四守卫**（plan/course-exit-and-shutdown.md §4）一整族，四条成员一条判据链：
  · `_gate`           —— 门求值（评估轮跑全门 / 非评估轮只查 duty）+ 启动期两条 warn-only；
  · `_apply_verdict`  —— 终端判决落地：**永不因门停车**（§2026-09-11 用户定案），
                         只落 `gate_verdict` 事件 + 把动作映射到远端云机，返回 False；
  · `_warn_min_train_unreachable` —— I2 预算可行性投影（log once/leg，只提示不停车）；
  · `_budget_hard_cut` —— G5 轮级兜底：max_hours 到顶立即停车（门族里**唯一**真停车路径）。

判据本体在 `rl/gate_check.py`（纯函数，写盘/停机不归它）；判决 → 云机达令的联动在组合根
（`rl/loop_guards.py::_sync_cloud_halt`），本模块只以 `self.` 调它。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rl.events import write_gate_verdict
from rl.gate_check import (
    BudgetInfo,
    count_iteration_events,
    evaluate,
    first_iter_end_ts,
    first_run_start_ts,
    load_override,
    read_trend_rows,
)
from rl.log import log


def _gate_startup_notes(args: Any, course: Any, spec: Any) -> list[str]:
    """课程门的启动期告警（预算可行性 + 判决力）。空列表 = 都健康。

    games_per_point 取**课程配置**（eval_games_per_stage），不是已落盘的行——启动时
    还没有 summary 行，而告警的意义正是"开跑前就知道这门的眼睛够不够亮"。
    """
    games = int(getattr(args, "eval_games_per_stage", 0) or 0)
    if games <= 0:
        games = int(getattr(course, "eval_games_per_stage", 0) or 0)
    notes = list(
        spec.budget_warnings(
            float(getattr(args, "max_hours", 0.0) or 0.0),
            int(getattr(args, "eval_every", 0) or 0),
        )
    )
    notes += list(spec.power_notes(games))
    return notes


class TrainingGuardsGate:
    """课程结束门 mixin（第四守卫）：求值 / 判决落地 / 预算硬断。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _jsonl_path: Any
    _course_fp: Any
    _traj_root: Any
    _gate_notes_logged: bool
    _min_train_warned: bool
    _soft_remediate_count: int
    _leg_abort: bool
    #: 共享 sink（真实现住组合根 `rl/loop_guards.py`）：账本视图增量 / 判决 → 云机达令。
    _ledger_apply: Any
    _sync_cloud_halt: Any
    _is_soft_verdict: Any
    #: 评估轮判定（真实现住 `rl/loop_dispatch.py`；`rl/loop_eval.py` 的是响亮占位）。
    _eval_on_round: Any

    def _gate(self, it: int) -> bool:
        """第四守卫：课程结束门（M1，plan/course-exit-and-shutdown.md §4）。

        评估轮跑完整求值（指标门读 eval_summary 行，没评估 = 没新数据，顺延
        一个节拍比用陈旧行硬判诚实）；**非评估轮只查 duty 门**（§385：G13 的
        输入每轮都更新，不该被 eval_every 节拍拖住——烧事故按轮数小时现形，
        而不是等下一评估点才宣布停车）。判决 → 动作映射（§2026-09-11）：
        **永不因门停车**——非 HOLD 只落 `gate_verdict` 事件作复盘记录，
        停机/恢复动作全部发到**远端云机**（REMEDIATE/PAUSE/ABORT → halt；
        HOLD/ADVANCE → resume）。真正停车只剩预算到顶（_budget_hard_cut）、
        F4 熔断、止损 2σ 等硬边界。无 gates 块的课程（老课程）恒 False——零行为变化。

        返回 True = 训练该停（当前仅预算硬断等硬边界路径返回 True）。
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

        # 首轮把两条 warn-only 打一次（§3.4-8 预算可行性 / §12.4 判决力）：
        # 与门同源、只在有 gates 的课程上出现，不进判决、不影响任何分支。
        if not getattr(self, "_gate_notes_logged", False):
            try:
                self._gate_notes_logged = True
                for note in _gate_startup_notes(args, course, spec):
                    log(f"[run_rl] {note}")
            except Exception as e:  # 告警永不影响训练
                log(f"[run_rl] gate notes skipped（{type(e).__name__}: {e}）")

        course_fp = str(self._course_fp or "")
        now = time.time()
        traj_root = Path(self._traj_root)
        try:
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
            train_samples=float(getattr(self, "_train_samples_total", 0.0) or 0.0),
            # G13 duty 输入（§385）：分母基线 = 首个完成迭代（起步热身不计账）。
            duty_baseline_ts=first_iter_end_ts(self._jsonl_path),
            duty_events=count_iteration_events(self._jsonl_path),
        )
        # I2（roadmap I-P1）：min_train_samples 可达性投影——c6-bonus 教训（门要 4M、
        # 实测 ~1.67 万样本/轮，160 轮也到不了）。开腿 ≥10 轮后用实测速率外推全腿，
        # 明显不可达就 log once/leg 大声说，让配置矛盾在烧完预算前现形（只提示不停车）。
        self._warn_min_train_unreachable(it, spec, budget)
        if not eval_this_round:
            # 非评估轮：只查 duty（数据每轮皆新；指标门无新 eval 不判）。
            try:
                res = evaluate(
                    course,
                    [],
                    budget=budget,
                    now=now,
                    override=override,
                    course_fp=course_fp,
                    decider="loop",
                    only_kinds=("duty",),
                )
            except Exception as e:
                log(f"[run_rl] gate it{it}: duty 求值异常（{type(e).__name__}: {e}）— 本轮跳过门")
                return False
            if not res.terminal:
                self._sync_cloud_halt(it, "HOLD")  # 停机条件消失 → 云机恢复
                return False  # HOLD / duty-unknown（起步期、数据不足）→ 静默继续
            return self._apply_verdict(it, res)

        try:
            rows = read_trend_rows(traj_root / "eval_log.jsonl", course_fp=course_fp)
        except Exception as e:
            log(f"[run_rl] gate it{it}: 求值输入读取失败（{type(e).__name__}: {e}）— 本轮跳过门")
            return False
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
            self._sync_cloud_halt(it, "HOLD")  # 停机条件消失 → 云机恢复
            return False
        return self._apply_verdict(it, res)

    def _warn_min_train_unreachable(self, it: int, spec: Any, budget: Any) -> None:
        """I2（roadmap I-P1）：min_train_samples 可达性投影（log once/leg，只提示不停车）。

        c6-bonus 教训：门要 4M 样本、实测 ~1.67 万/轮，160 轮也到不了——账本好看的
        累计曲线掩盖了「阈值物理不可达」。开腿 ≥10 轮后用实测速率外推全腿，明显
        不可达就大声说，让配置矛盾在烧完预算前现形（修配置或提前停腿，由人决策）。
        """
        min_samples = getattr(spec, "min_train_samples", None)
        if not min_samples or getattr(self, "_min_train_warned", False):
            return
        total_iters = int(getattr(budget, "iters", 0) or 0)
        cur = int(getattr(budget, "cur_iter", 0) or 0)
        got = float(getattr(budget, "train_samples", 0.0) or 0.0)
        if total_iters <= 0 or cur < 10 or got <= 0:
            return
        projected = got / cur * total_iters
        if projected < float(min_samples):
            self._min_train_warned = True
            log(
                f"[run_rl] WARN it{it}: gates.min_train_samples 疑似不可达——实测速率 "
                f"{got / cur:.0f}/轮 × {total_iters} 轮 ≈ {projected / 1e6:.2f}M < "
                f"{float(min_samples) / 1e6:.2f}M。修配置（降阈值/加批量/加 iters）"
                "或提前停腿，别烧到顶才发现。"
            )

    def _apply_verdict(self, it: int, res: Any) -> bool:
        """终端判决落地：**永不因门停车**（§2026-09-11 用户定案）。

        落盘 `gate_verdict` 事件（复盘记录 + exit-watchdog 的「已停车」分类源），
        把停机/恢复动作映射到远端云机，然后**返回 False 继续训练**——停车只留给
        预算到顶（_budget_hard_cut）、F4 熔断、止损等硬边界。落盘失败也照样继续
        （记录日志，绝不带病停车）。
        """
        try:
            self._ledger_apply(
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
            )
        except OSError as e:
            log(f"[run_rl] gate it{it}: gate_verdict 落盘失败（{e}）— 记录缺失，继续训练")
        log(f"[run_rl] GATE {res.verdict} it{it}: {res.reason}")
        self._sync_cloud_halt(it, res.verdict, getattr(res, "readings", None))
        # I2（roadmap）：提示类门（plateau）REMEDIATE N 次即停。c6-pickup3 6 次 /
        # c6-bonus 10 次 cloud halt 的教训：平台期每 5 轮必然复现 REMEDIATE——
        # 反复确认的"边际收益枯竭"就是停腿信号，不是继续烧钱的理由
        # （NO_CLOUD_HALT_KINDS 只是不杀云机，腿本身该停）。默认 4 次；0 = 关（旧行为）。
        if res.verdict == "REMEDIATE" and self._is_soft_verdict(
            res.verdict, getattr(res, "readings", None)
        ):
            self._soft_remediate_count = int(getattr(self, "_soft_remediate_count", 0)) + 1
            limit = int(getattr(self.args, "gate_remediate_stop_after", 4) or 0)
            if 0 < limit <= self._soft_remediate_count:
                try:
                    self._ledger_apply(
                        write_gate_verdict(
                            self._jsonl_path,
                            it,
                            "ABORT",
                            f"提示类门 REMEDIATE 已 {self._soft_remediate_count} 次（≥{limit}）"
                            "——边际收益枯竭确认，停腿（I2）",
                            decider="loop",
                        )
                    )
                except OSError as e:
                    log(f"[run_rl] gate it{it}: ABORT 落盘失败（{e}）")
                log(
                    f"[run_rl] I2 REMEDIATE-STOP it{it}: 软判决 ×{self._soft_remediate_count} "
                    f"≥ {limit} —— 停腿（gate_remediate_stop_after）"
                )
                self._leg_abort = True
                return True
        return False

    def _budget_hard_cut(self, it: int) -> bool:
        """G5 每轮兜底：max_hours 到顶立即停车（§385 审计补洞）。

        G5 预算门本身只在**评估轮**求值（_gate → evaluate），非评估轮 max_hours
        过了不会停、最多过冲 ~eval 周期（c6b 场合每 3 轮约 10-15min）。这里在
        每轮结束后硬查一次墙钟，到顶 → 落 `gate_verdict`(STOP)（exit-watchdog
        会识别为设计内停车，不标"意外退出"）→ True（调用方 break）。"""
        args = self.args
        if not getattr(args, "max_hours", 0.0):
            return False
        started = first_run_start_ts(self._jsonl_path)
        if started is None:
            return False
        elapsed = time.time() - started
        if elapsed < float(args.max_hours) * 3600:
            return False
        log(
            f"[run_rl] max_hours {args.max_hours}h 到顶（轮级硬断）it{it} "
            f"— wall {elapsed:.0f}s"
        )
        try:
            write_gate_verdict(
                self._jsonl_path,
                it,
                "STOP",
                f"max_hours {args.max_hours}h 到顶（轮级硬断）",
                decider="loop",
            )
        except OSError as e:
            log(f"[run_rl] gate_verdict 落盘失败（{e}）— 仍按 max_hours 停车")
        return True
