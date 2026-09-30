"""gate_check —— 课程结束门求值器（plan/course-exit-and-shutdown.md §3/§4，M1）。

分层（S5 第十五刀，2026-09-27）：输入读数面 → `biz.gate_inputs`；判决项面 →
`biz.gate_judges`；本模块 = 引擎（两趟调度 + ADVANCE 附加条件 + CLI）+ 全量门面。

职责边界：**只判不停、只算不写**。`evaluate()` 是纯函数（禁 torch/numpy，单测
断言），写盘与停机分别是 `loop_guards_gate`（gate_verdict 事件）与执行面的事。

四条红线
--------
1. **纯函数**：无 torch/numpy；无隐式文件读取（`read_*` 是显式入口）。
2. **确定性**：时间一律由 `now` 注入（§4.7），预算门单测全程 frozen now。
3. **趋势单源 = settled `eval_summary` 行**（§3.3 v0.4）：逐局行不进判决，
   聚合均值由 `eval_local.settle_eval_summary` 落进 summary 行。
4. **sustain 去重按 (course_fp, wver)**（§4.4）：同 wver 重跑（崩溃恢复/手动
   重放）只保留最新一条，不虚增连续通过计数。

与计划的两处偏差（均经实现期实测确认，属严格增强）
--------------------------------------------------
* §4.4 原设计把 sustain 计数建在 gate_log 的历史 pass 行上（求值器要吃外部状态）。
  本实现改为**从 trend_rows 复算**：窗口 = 最近 `sustain` 个**不同 wver** 的
  summary 行（已去重），全部满足该门的逐行判据才放行。语义等价（同 wver 重跑
  不计数），但求值器保持纯函数、无隐藏状态、崩溃重放天然幂等。
* §4.4「ADVANCE 需窗口内 ≥2 个不同 seed 集」：仅当窗口行**携带** seed 标识
  （`seed_fp`）时才校验；全部缺失记 `seeds: unknown` **且放行**——否则历史语料
  （无该字段）会让 ADVANCE 永久不可达，等于把门焊死。

CLI（薄壳，§4.1）只做 dry-run 与崩溃恢复重放：
`python -m biz.gate_check --course c6b-margin --traj <dir> [--dry-run]`
exit 码见 `EXIT_CODES`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.platform_utils import force_utf8_stdio
from worker.eval_local import rotor_offset

# ---- 输入面 + 判决面已下沉（S5 第十五刀，2026-09-27）-------------------------------
# 实现搬家、名字留门面：全仓 `from biz.gate_check import …` 与外部调用点一行不改
# （「名字是契约，位置不是」）。判决面 / 输入面读的模块全局解析在新家——要 patch
# 请去 `biz.gate_judges.*` / `biz.gate_inputs.*`（patch 门面不会生效）。
from worker.gate_inputs import (
    _OVERRIDE_VERDICTS as _OVERRIDE_VERDICTS,
)
from worker.gate_inputs import (
    _TS_FMT as _TS_FMT,
)
from worker.gate_inputs import (
    BudgetInfo as BudgetInfo,
)
from worker.gate_inputs import (
    EvalRow as EvalRow,
)
from worker.gate_inputs import (
    GateOverrideError as GateOverrideError,
)
from worker.gate_inputs import (
    _num as _num,
)
from worker.gate_inputs import (
    _row_from_summary as _row_from_summary,
)
from worker.gate_inputs import (
    count_iteration_events as count_iteration_events,
)
from worker.gate_inputs import (
    first_iter_end_ts as first_iter_end_ts,
)
from worker.gate_inputs import (
    first_run_start_ts as first_run_start_ts,
)
from worker.gate_inputs import (
    load_override as load_override,
)
from worker.gate_inputs import (
    normalize_rows as normalize_rows,
)
from worker.gate_inputs import (
    read_trend_rows as read_trend_rows,
)
from worker.gate_inputs import (
    sum_train_samples as sum_train_samples,
)
from worker.gate_inputs import (
    sum_train_sec as sum_train_sec,
)
from worker.gate_judges import (
    _JUDGES_NO_TEACHER as _JUDGES_NO_TEACHER,
)
from worker.gate_judges import (
    DUTY_MIN_EVENTS as DUTY_MIN_EVENTS,
)
from worker.gate_judges import (
    _Ctx as _Ctx,
)
from worker.gate_judges import (
    _eval_budget as _eval_budget,
)
from worker.gate_judges import (
    _eval_course_valid as _eval_course_valid,
)
from worker.gate_judges import (
    _eval_cross_course as _eval_cross_course,
)
from worker.gate_judges import (
    _eval_dependency as _eval_dependency,
)
from worker.gate_judges import (
    _eval_duty as _eval_duty,
)
from worker.gate_judges import (
    _eval_hack as _eval_hack,
)
from worker.gate_judges import (
    _eval_one as _eval_one,
)
from worker.gate_judges import (
    _eval_plateau as _eval_plateau,
)
from worker.gate_judges import (
    _eval_skill_floor as _eval_skill_floor,
)
from worker.gate_judges import (
    _eval_teacher_parity as _eval_teacher_parity,
)
from worker.gate_judges import (
    _eval_wins_mastery as _eval_wins_mastery,
)
from worker.gate_judges import (
    _halves as _halves,
)
from worker.gate_judges import (
    _mean as _mean,
)
from worker.gate_judges import (
    _Partial as _Partial,
)
from worker.gate_judges import (
    _route_by_completion as _route_by_completion,
)
from worker.gate_judges import (
    _slope as _slope,
)
from worker.gate_judges import (
    _sustain as _sustain,
)
from worker.gate_judges import (
    _window as _window,
)
from worker.gate_judges import (
    _wins_mastery_pooled as _wins_mastery_pooled,
)


def _lazy_config() -> tuple[Any, Any, Any]:
    """延迟导入 biz.config，返回 (GATE_SPLIT, GatesSpec, resolve_course)。

    **为什么延迟**：`biz.config` → `biz.reward_library` → **numpy**（课程公式求值确实
    需要它），而求值器的红线是「禁 torch/numpy」——它活在评估线程与监控脚本里，
    不该为一个判决付依赖加载。类型走 `TYPE_CHECKING`，运行时只在真正求值时导入
    （主进程里 biz.config 早已加载，开销为零）。
    """
    from worker.config import GATE_SPLIT, GatesSpec, resolve_course

    return GATE_SPLIT, GatesSpec, resolve_course


# --------------------------------------------------------------------------- 常量

#: §4.2 优先级 lattice（求值器内定死，单测锁死）：
#: override > ABORT > PAUSE > STOP > REMEDIATE > ADVANCE > HOLD。
VERDICT_PRIORITY: dict[str, int] = {
    "HOLD": 0,
    "ADVANCE": 1,
    "REMEDIATE": 2,
    "STOP": 3,
    "PAUSE": 4,
    "ABORT": 5,
}

#: CLI exit 码（§4.1，notebook cell switch 用；不是系统主协议）。
EXIT_CODES: dict[str, int] = {
    "HOLD": 0,
    "ADVANCE": 10,
    "REMEDIATE": 20,
    "ABORT": 30,
    "PAUSE": 40,
    "STOP": 50,
}


# --------------------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class RuleReading:
    """单门的读数（审计用：判决 + 完成度 + 原因，全保留）。"""

    rule_id: str
    kind: str
    #: 单值判决（分流声明折叠为 ADVANCE）。
    verdict: str
    #: 门条件成立（sustain 未满足也算 fired——fired 与 released 分离）。
    fired: bool
    #: 可放行（fired + sustain 满 + ADVANCE 附加条件）。
    released: bool
    #: 完成度 = 近 sustain 窗内达标轮数 / sustain（§3.4-4 全局唯一定义）。
    completion: float
    reason: str
    enabled: bool = True
    #: 休眠（enabled=false）或 kind 未实现（dependency，M3）。
    dormant: bool = False
    #: 数据不足/缺字段 → 不判，且**不触发任何反向判决**（§4.5）。
    unknown: bool = False
    #: G4/G5 分流结果（ADVANCE/REMEDIATE）；非分流门为 None。
    route: str | None = None
    #: verdict 本体是分流声明（G4）——**只有它**的 route 才是判决本身；
    #: G5 的 route 只是路由建议（§3.3：STOP 附带去向），判决仍是 STOP。
    split: bool = False

    @property
    def effective_verdict(self) -> str:
        """进 lattice 的判决：分流门取 `route`，其余取本体（route 仅作元信息）。"""
        return (self.route or self.verdict) if self.split else self.verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "kind": self.kind,
            "verdict": self.verdict,
            "fired": self.fired,
            "released": self.released,
            "completion": round(self.completion, 4),
            "reason": self.reason,
            "enabled": self.enabled,
            "dormant": self.dormant,
            "unknown": self.unknown,
            "route": self.route,
            "split": self.split,
        }


@dataclass(frozen=True)
class GateResult:
    """一次求值的结果。"""

    verdict: str
    reason: str
    readings: tuple[RuleReading, ...] = ()
    route: str | None = None
    override: Mapping[str, Any] | None = None
    #: "ok"（≥2 seed 集）/ "insufficient"（阻塞 ADVANCE）/ "unknown"（放行）。
    seeds: str = "unknown"
    now: float | None = None
    health: Mapping[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        """非 HOLD = 训练该停（§4.3 判决→动作映射）。"""
        return self.verdict != "HOLD"

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.verdict, 0)

    def to_event(self, it: int, decider: str = "loop") -> dict[str, Any]:
        """`gate_verdict` 事件体（进 training_log.jsonl）。"""
        return {
            "event": "gate_verdict",
            "iter": it,
            "time": time.strftime(_TS_FMT),
            "verdict": self.verdict,
            "route": self.route,
            "reason": self.reason,
            "seeds": self.seeds,
            "decider": decider,
            "readings": [r.to_dict() for r in self.readings],
            "override": dict(self.override) if self.override else None,
        }


# --------------------------------------------------------------------------- 主入口


def evaluate(
    course: Any,
    trend_rows: Sequence[Mapping[str, Any]],
    health: Mapping[str, Any] | None = None,
    budget: BudgetInfo | None = None,
    now: float | None = None,
    *,
    override: Mapping[str, Any] | None = None,
    course_fp: str = "",
    decider: str = "loop",
    only_kinds: Sequence[str] | None = None,
) -> GateResult:
    """课程结束门求值（纯函数，§4.1）。

    Args:
        course: `CourseConfig`（读 `.gates`）或直接传 `GatesSpec`；无 gates → HOLD。
        trend_rows: settled `eval_summary` 行（原始 dict，时间序）。
        health: 预留（§3.3 末：健康门不经求值器，由 loop_guards 直接判）。
        budget: G5 输入；None → 预算门 unknown。
        now: epoch 秒；None → `time.time()`（单测注入 frozen now）。
        override: G12 人工改向（最高优先级）；非法由 `load_override` 抛。
        course_fp: 本课课程指纹；用于行过滤与 sustain 去重键。
        decider: "loop" | "notebook" | "cli"（记入事件，血缘可归因）。
        only_kinds: 非 None 时只求值这些 kind（§385：非评估轮单独查 duty）；
            其余规则全部当作休眠跳过。eval rows 为空时依赖 rows 的门自然 unknown。

    Returns:
        GateResult：verdict ∈ {HOLD, ADVANCE, REMEDIATE, STOP, PAUSE, ABORT}。
    """
    now_v = time.time() if now is None else float(now)
    GATE_SPLIT, GatesSpec, _ = _lazy_config()
    spec = getattr(course, "gates", None)
    if spec is None and isinstance(course, GatesSpec):
        spec = course
    if spec is None:
        return GateResult(
            verdict="HOLD",
            reason="课程无 gates 块（门关闭，老课程行为不变）",
            now=now_v,
            health=health,
        )

    rows = normalize_rows(trend_rows, course_fp)
    teacher_wr = spec.teacher.wins / spec.teacher.games if spec.teacher.games else 0.0
    ctx = _Ctx(spec=spec, rows=rows, teacher_win_rate=teacher_wr, now=now_v, budget=budget)

    # ---- pass 1：非分流门（完成度不依赖 advance_if）----
    completions: dict[str, float] = {}
    partials: dict[str, _Partial] = {}
    for rule in spec.rules:
        if rule.kind in ("plateau", "budget"):
            continue
        if only_kinds is not None and rule.kind not in only_kinds:
            continue
        partials[rule.id] = _eval_one(rule, ctx, trend_rows, spec)
        completions[rule.id] = partials[rule.id].completion

    # ---- pass 2：分流门（route 读 pass 1 的完成度）----
    for rule in spec.rules:
        if rule.kind not in ("plateau", "budget"):
            continue
        if only_kinds is not None and rule.kind not in only_kinds:
            continue
        p = _eval_one(rule, ctx, trend_rows, spec, completions)
        partials[rule.id] = p
        completions[rule.id] = p.completion

    # ---- ADVANCE 附加条件：advance_requires 全绿 + 窗口 ≥2 seed 集 ----
    by_rule = {r.id: r for r in spec.rules}
    advance_ready = all(
        completions.get(gid, 0.0) >= 1.0
        for gid in spec.advance_requires
        if by_rule.get(gid) is not None and by_rule[gid].enabled
    )
    window = list(rows[-max(1, int(spec.sustain)) :])
    seed_fps = {r.seed_fp for r in window if r.seed_fp}
    if not seed_fps:
        seeds_state = "unknown"
    elif len(seed_fps) >= 2:
        seeds_state = "ok"
    else:
        seeds_state = "insufficient"
    seeds_ok = seeds_state != "insufficient"

    # ---- ADVANCE 附加条件之四：样本通过量（§12.4：数据不够下结论 ≠ 事故）----
    min_samples = getattr(spec, "min_train_samples", None)
    train_ok = True
    train_note = ""
    if budget is not None and min_samples is not None:
        got = budget.train_samples
        if got < float(min_samples):
            train_ok = False
            train_note = f"（样本通过量 {got / 1e6:.2f}M < {float(min_samples) / 1e6:.2f}M）"

    readings: list[RuleReading] = []
    #: 达标但被 ADVANCE 附加条件挡住的门（观测必须自带牙齿：HOLD 也要说清差在哪）。
    blocked_advance: list[str] = []
    for rule in spec.rules:
        if rule.id not in partials:  # §385 only_kinds 过滤掉的规则不参与判决
            continue
        p = partials[rule.id]
        verdict = "ADVANCE" if rule.verdict == GATE_SPLIT else rule.verdict
        released = p.fired and p.completion >= 1.0
        if rule.verdict == GATE_SPLIT:
            # 分流门：fired 即放行，实际判决取 route
            released = p.fired
        if not rule.enabled:
            released = False
        elif released and (p.route if rule.verdict == GATE_SPLIT else verdict) == "ADVANCE":
            # ADVANCE 附加条件（§4.4 + §12.4）：advance_requires 全绿 + 窗口 ≥2 seed 集
            # + 样本通过量 ≥ min_train_samples。
            # 分流门按**实际 route** 判（route=REMEDIATE 不需 advance_requires 背书，
            # 否则平台期永远放不出 REMEDIATE——c6 回溯实测正是卡在这里）。
            gates_ok = advance_ready and seeds_ok and train_ok
            if not gates_ok:
                blocked_advance.append(
                    f"{rule.id}"
                    + ("" if advance_ready else "(advance_requires 未全绿)")
                    + ("" if seeds_ok else f"(seed 集 {seeds_state})")
                    + (train_note if not train_ok else "")
                )
            released = gates_ok
        readings.append(
            RuleReading(
                rule_id=rule.id,
                kind=rule.kind,
                verdict=verdict,
                fired=p.fired,
                released=released,
                completion=p.completion,
                reason=p.reason,
                enabled=rule.enabled,
                dormant=p.dormant or not rule.enabled,
                unknown=p.unknown,
                route=p.route,
                split=rule.verdict == GATE_SPLIT,
            )
        )

    # ---- lattice：同轮多门取最高优先级 ----
    best: RuleReading | None = None
    for rd in readings:
        if not rd.released:
            continue
        if best is None:
            best = rd
            continue
        # 同优先级时取**带 route 的那条**（分流门信息更全：既给判决又给去向）。
        key_new = (VERDICT_PRIORITY.get(rd.effective_verdict, 0), rd.route is not None)
        key_old = (VERDICT_PRIORITY.get(best.effective_verdict, 0), best.route is not None)
        if key_new > key_old:
            best = rd
    verdict = "HOLD"
    route: str | None = None
    if best is not None:
        verdict = best.effective_verdict
        route = best.route
        reason = f"{best.rule_id}({best.kind}): {best.reason}"
        if not advance_ready and verdict == "ADVANCE":
            verdict = "HOLD"
            reason += "（advance_requires 未全绿 → HOLD）"
        if not seeds_ok and verdict == "ADVANCE":
            verdict = "HOLD"
            reason += "（窗口 seed 集 <2 → HOLD）"
    elif blocked_advance:
        # 门已达标、只差附加条件——HOLD 也要说清差在哪一环（否则屏幕上只有沉默）。
        reason = f"ADVANCE 待放行（{'，'.join(blocked_advance)}）→ HOLD"
    else:
        reason = "无门放行（HOLD）"

    ov = dict(override) if override else None
    if ov is not None:
        ov_verdict = str(ov.get("verdict") or "HOLD")
        verdict = ov_verdict
        reason = f"G12 override → {ov_verdict}：{ov.get('reason', '')}".strip()
        route = ov.get("route")

    return GateResult(
        verdict=verdict,
        reason=reason,
        readings=tuple(readings),
        route=route,
        override=ov,
        seeds=seeds_state,
        now=now_v,
        health=health,
    )


# --------------------------------------------------------------------------- CLI（薄壳）


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="worker.gate_check",
        description="课程结束门 dry-run / 崩溃恢复重放（判决只写 stdout，零写盘）",
    )
    p.add_argument("--course", required=True, help="课程名或 jsonc 路径")
    p.add_argument("--traj", required=True, help="traj 根目录（读 eval_log.jsonl）")
    p.add_argument(
        "--log",
        default="training_log.jsonl",
        help="预算基线来源（相对 traj；默认 training_log.jsonl）",
    )
    p.add_argument("--decider", default="cli", choices=["cli", "loop", "notebook"])
    p.add_argument("--json", action="store_true", help="输出完整 GateResult JSON")
    p.add_argument("--dry-run", action="store_true", help="显式声明零写盘（默认就是）")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """薄壳 CLI（§4.1）。exit 码 = `EXIT_CODES`；override 非法 = 2。"""
    # 子进程字节流恒 UTF-8（压过 PYTHONIOENCODING/PYTHONUTF8/代码页）——配对消费方
    # （tests/subproc_util.run_utf8 / agent）的显式 utf-8 解码，跨沙箱确定性契约。
    force_utf8_stdio()
    from worker.config import load_course  # 延迟导入：CLI 路径才需要

    args = build_cli().parse_args(argv)
    _, _, resolve_course = _lazy_config()
    course_path = resolve_course(args.course)
    course = load_course(course_path)
    traj = Path(args.traj)
    # 与 `course_fp_for_args` 同算法（D14），否则读不到带 fp 的 summary 行。
    try:
        fp = hashlib.sha256(Path(course_path).read_bytes()).hexdigest()
    except OSError:
        fp = ""
    rows = read_trend_rows(traj / "eval_log.jsonl", course_fp=fp)
    started = first_run_start_ts(traj / args.log)
    budget = BudgetInfo(
        started_at=started,
        max_hours=float(getattr(course, "max_hours", 0.0) or 0.0),
        iters=int(getattr(course, "iters", 0) or 0),
        cur_iter=int(getattr(course, "_cur_iter", 0) or 0),
        # G13 duty 输入（§385）：分子与分母基线都从账本取，CLI 与 loop 同源。
        train_sec=sum_train_sec(traj / args.log),
        duty_baseline_ts=first_iter_end_ts(traj / args.log),
        duty_events=count_iteration_events(traj / args.log),
    )
    override = None
    if course.gates is not None:
        ov_path = traj.parent / str(course.gates.override_file)
        override = load_override(ov_path)
    res = evaluate(
        course, rows, budget=budget, override=override, course_fp=fp, decider=args.decider
    )
    if args.json:
        print(
            json.dumps(
                {
                    "verdict": res.verdict,
                    "route": res.route,
                    "reason": res.reason,
                    "seeds": res.seeds,
                    "readings": [r.to_dict() for r in res.readings],
                },
                # 机器通道 = 纯 ASCII（\uXXXX 转义）：对消费方的解码编码完全免疫
                # （裸 text=True 父进程 / agent 自带解码器都读不坏）；人类可读走
                # 非 --json 分支。曾用 ensure_ascii=False 在 zh-CN Windows 上与
                # GBK 解码父进程互炸——见 docs/nn/engineering.md §3。
                ensure_ascii=True,
                indent=2,
            )
        )
    else:
        print(f"verdict={res.verdict} route={res.route} seeds={res.seeds}")
        print(f"reason: {res.reason}")
        for r in res.readings:
            print(
                f"  - {r.rule_id:<4} {r.kind:<14} fired={r.fired!s:<5} "
                f"released={r.released!s:<5} completion={r.completion:.2f} {r.reason}"
            )
        for note in _notes(course, rows):
            print(f"  ! {note}")
    return res.exit_code


def _notes(course: Any, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """§3.4-8 预算可行性 + §12.4 判决力（两条 warn-only，调用方负责打印）。

    `budget_warnings` 自 2026-09-11 前**从未接线**（只有单测在调）——这里一并接上。
    """
    spec = getattr(course, "gates", None)
    if spec is None:
        return []
    games_per_point = int(rows[-1].get("games") or 0) if rows else 0  # 原始行 dict
    notes = list(
        spec.budget_warnings(
            float(getattr(course, "max_hours", 0.0) or 0.0),
            int(getattr(course, "eval_every", 0) or 0),
        )
    )
    notes += list(spec.power_notes(games_per_point))
    notes += _rotation_notes(rows)
    return notes


def _rotation_notes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """双轨轮转退化告警（P2-a，2026-09-15；warn-only）。

    `rotor_offset` 周期 3 ⇒ 轮转轨要真正转起来，两次 A-eval 的 iter 间隔必须**不**是
    3 的倍数。`--eval-every 3/6/9` 或 `--eval-at '30,60,90'` 会让每次落到同一段 ⇒
    轮转轨退化为固定段、"没见过"的那部分种子永远没被评估（过拟合报警失去对照）。
    只在确实能看出病灶时报警（≥3 个评估点、全落同一段），不误伤正常腿。
    """
    points: list[tuple[int, int]] = []
    for r in rows:
        if r.get("event") != "eval_summary":
            continue
        it = r.get("iter")
        wr = r.get("rotor_wr")
        if not isinstance(it, int) or not isinstance(wr, (int, float)):
            continue
        points.append((it, rotor_offset(max(1, it))))
    if len(points) < 3:
        return []
    if len({off for _, off in points}) > 1:
        return []
    its = [it for it, _ in points]
    return [
        f"双轨轮转退化：{len(points)} 个评估点（iter {its[0]}..{its[-1]}）全部落在同一轮转段 "
        f"(下标 {points[0][1]})——两次 eval 的 iter 间隔是 3 的倍数（--eval-every / "
        "--eval-at 所致）。轮转轨形同固定段，过拟合报警失去对照；改用非 3 倍数的间隔"
        "（如 eval_every=2/4/5）"
    ]


if __name__ == "__main__":
    raise SystemExit(main())
