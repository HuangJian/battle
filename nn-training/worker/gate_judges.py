"""gate_judges —— 课程结束门的「判决项面」（S5 第十五刀，2026-09-27）。

一家持有 `_Partial`/`_Ctx` 词汇 · 11 种 kind 的判决函数与统计助手 · 注册表
`_JUDGES_NO_TEACHER`，以及**判决项接口** `_eval_one`（对单条 `GateRule` 独立求值——
引擎 `evaluate` 的两趟调度与 `only_kinds=("duty",)` 过滤都经它）。输入只来自
`biz.gate_inputs`（单向：inputs ← judges ← check）。

与 `biz/gate_check` 同源四条红线：**纯函数**（运行期零 torch/numpy；类型只 TYPE_CHECKING
取 `biz.config`）/ **确定性**（时间由 `_Ctx.now` 注入）/ **趋势单源 = settled
`eval_summary` 行** / **sustain 去重按 (course_fp, wver)**。
"""


from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worker.gate_inputs import BudgetInfo, EvalRow, _row_from_summary

if TYPE_CHECKING:  # 运行时期望零 biz.config 依赖（红线随家，见模块 docstring）。
    from worker.config import GateRule, GatesSpec


#: G13 duty 最小完成迭代数：不足不判（unknown）——开门即响的防护（§385）。
DUTY_MIN_EVENTS = 2


@dataclass(frozen=True)
class _Partial:
    """kind 求值器的中间结果（fired/completion 语义统一后转 RuleReading）。"""

    fired: bool
    completion: float
    reason: str
    unknown: bool = False
    dormant: bool = False
    route: str | None = None


@dataclass(frozen=True)
class _Ctx:
    spec: GatesSpec
    rows: tuple[EvalRow, ...]
    teacher_win_rate: float
    now: float | None
    budget: BudgetInfo | None


# --------------------------------------------------------------------------- 通用统计


def _mean(rows: Sequence[EvalRow], metric: str) -> float | None:
    vals: list[float] = [v for v in (r.metric(metric) for r in rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _slope(vals: Sequence[float]) -> float:
    """最小二乘斜率（等距 x = 0..n-1）；n<2 返回 0.0。"""
    n = len(vals)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(vals) / n
    den = sum((i - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return sum((i - mx) * (v - my) for i, v in enumerate(vals)) / den


def _sustain(ctx: _Ctx, judge: Callable[[EvalRow], str | None]) -> tuple[float, str, bool]:
    """sustain 窗口判定（§4.4 复算口径）：judge 返回 None = 该行达标。

    返回 (completion, reason, unknown)。窗口不足 sustain → completion 按实际达标
    数计，reason 点名"数据不足"，永不满 1.0（门不放行）。

    `unknown`：窗口里的未达标**全部**源于缺数据（judge 返回 "缺 …"/"分母为零"）
    → 判不了，而不是"策略不行"——缺数据只阻塞依赖它的 ADVANCE，不构成反向判决
    （§4.5）。窗口为空同样算 unknown（一条 summary 都没有）。
    """
    sustain = max(1, int(ctx.spec.sustain))
    window = list(ctx.rows[-sustain:])
    fails = [j for j in (judge(r) for r in window) if j]
    n_pass = len(window) - len(fails)
    completion = n_pass / sustain
    if len(window) < sustain:
        reason = f"{n_pass}/{sustain} 轮达标（数据不足：窗口仅 {len(window)} 轮）"
    else:
        reason = f"{n_pass}/{sustain} 轮达标"
    if fails:
        reason += f"；最近未达标：{fails[-1]}"
    unknown = (not window) or bool(
        fails and all(f.startswith("缺 ") or "分母为零" in f for f in fails)
    )
    return completion, reason, unknown


def _window(rule: GateRule, ctx: _Ctx, n: int) -> list[EvalRow] | None:
    """取最近 n 个去重后的 summary 行；不足 n → None（数据不足，不判）。"""
    if n <= 0:
        return None
    rows = list(ctx.rows[-n:])
    return rows if len(rows) >= n else None


def _halves(rule: GateRule, ctx: _Ctx, n: int) -> tuple[list[EvalRow], list[EvalRow]] | None:
    """窗口前后半（plateau/hack 口径）：n 必须 ≥2，返回 (前半, 后半)。"""
    rows = _window(rule, ctx, n)
    if rows is None or len(rows) < 2:
        return None
    k = len(rows) // 2
    return rows[:k], rows[k:]


# --------------------------------------------------------------------------- kind 求值器


def _eval_wins_mastery(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G1：窗口内胜率 ≥ rel_teacher × 教师胜率，连续 sustain 轮。

    2026-09-11 评审新增两个可选附加条件（§12.4 effect size）：
      * `min_gain_pp`：还须 ≥ `spec.baseline_win_rate + min_gain_pp/100`——
        防"400 局把 1pp 变成显著但无意义"；
      * `require_rising`：sustain 窗口内胜率最小二乘斜率须 ≥ 0（同向）。
    """
    rel = rule.rel_teacher if rule.rel_teacher is not None else 1.0
    thr = rel * ctx.teacher_win_rate
    baseline = getattr(ctx.spec, "baseline_win_rate", None)
    floor = (
        (baseline + rule.min_gain_pp / 100.0)
        if baseline is not None and rule.min_gain_pp is not None
        else None
    )

    def judge(r: EvalRow) -> str | None:
        if r.win_rate is None:
            return "本轮无 winRate"
        if r.win_rate < thr:
            return f"winRate {r.win_rate:.3f} < {thr:.3f}"
        if floor is not None and r.win_rate < floor:
            return f"winRate {r.win_rate:.3f} < 起点{baseline:.2f}+{rule.min_gain_pp}pp={floor:.3f}"
        return None

    if rule.pool_window is not None:
        return _wins_mastery_pooled(rule, ctx, thr=thr, floor=floor, baseline=baseline)

    completion, reason, unknown = _sustain(ctx, judge)
    if rule.require_rising:
        wrs = [
            r.win_rate for r in ctx.rows[-max(1, int(ctx.spec.sustain)) :] if r.win_rate is not None
        ]
        if len(wrs) >= 2:
            s = _slope(wrs)
            if s < 0:
                completion = min(completion, 1.0 - 1.0 / max(1, int(ctx.spec.sustain)))
                reason += f"；窗口胜率斜率 {s:+.4f} < 0（非同向，§12.4）"
        else:
            reason += "；同向性待第二个数据点"
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"胜率 ≥ {thr:.3f}（{rel}×教师 {ctx.teacher_win_rate:.3f}）：{reason}",
        unknown=unknown,
    )


def _wins_mastery_pooled(
    rule: GateRule,
    ctx: _Ctx,
    *,
    thr: float,
    floor: float | None,
    baseline: float | None,
) -> _Partial:
    """G1 池化判据（2026-09-11 判决力修复）：合并最近 `pool_window` 个点的局数再判。

    为什么必须池化：100 局/点的 SE≈4.3pp，而门要求的效果量只有 5pp —— 逐点判等于
    用噪声判噪声（真 +7pp 也仅约 12% 概率三连过）。池化不花一分额外评估时间：
    3×100 局 ⇒ SE≈2.6pp。sustain 的"复现"语义靠**窗口证据 + 斜率同向**保留。
    """
    k = max(1, int(rule.pool_window or 1))
    rows = list(ctx.rows[-k:])
    if len(rows) < k:
        return _Partial(
            False, 0.0, f"池化窗口仅 {len(rows)}/{k} 轮（数据不足，不判）", unknown=True
        )
    games = sum(int(r.games or 0) for r in rows)
    if games <= 0:
        return _Partial(False, 0.0, "池化窗口缺 games/wins（不判）", unknown=True)
    wins = sum(int(r.wins or 0) for r in rows)
    p = wins / games
    se = math.sqrt(max(0.0, p * (1.0 - p)) / games)
    head = (
        f"池化 {k} 轮 {games} 局 {wins} 胜 = {p:.3f}"
        f"（SE {100 * se:.1f}pp，95%CI ±{196 * se:.1f}pp）"
    )
    # 2026-09-13 P0：判决力自检（历史事故——c6-pickup3 启动时就报过「要求 5pp，但 300 局的
    # SE=2.7pp > 2.5pp，该门分辨不出自己要求的效果」，却只当提示放过；c6-bonus 沿用同款
    # G1，全程 0.275–0.302 离门槛差 7–10pp，从未有希望达标，白烧 74 轮）。
    # 现在把「要求的效果量 vs 当前样本量能分辨的最小效应」写进每次判定的 reason。
    z_pe = float(rule.conf_z or 1.645)
    gain = float(rule.min_gain_pp or 0.0) / 100.0
    if gain > 0:
        mde = z_pe * se  # 当前样本量下可分辨的最小效应（单侧 conf_z）
        if gain < mde:
            need = math.ceil(p * (1.0 - p) * (z_pe / gain) ** 2) if gain > 0 else 0
            head += (
                f"；⚠ 判决力不足：要求 +{rule.min_gain_pp}pp，但 {games} 局只分辨得出"
                f" ≥{100 * mde:.1f}pp（需 ≈{need} 局，即 eval_games×pool_window 提到该量级）"
            )
    fails: list[str] = []
    if p < thr:
        fails.append(f"{p:.3f} < 教师线 {thr:.3f}")
    target: float | None = None
    if floor is not None:
        target = floor
        if p < floor:
            fails.append(f"{p:.3f} < 起点 {float(baseline or 0.0):.2f}+{rule.min_gain_pp}pp={floor:.3f}")
    z = float(rule.conf_z or 0.0)
    if z > 0 and target is not None and p - z * se < target:
        fails.append(
            f"效应未高于噪声：{p:.3f} − {z:g}×{se:.3f} = {p - z * se:.3f} < {target:.3f}"
        )
    if rule.require_rising:
        wrs = [r.win_rate for r in rows if r.win_rate is not None]
        if len(wrs) >= 2:
            s = _slope(wrs)
            if s < 0:
                fails.append(f"窗口胜率斜率 {s:+.4f} < 0（非同向，§12.4）")
        else:
            fails.append("同向性待第二个数据点")
    if fails:
        return _Partial(False, 0.0, f"{head}；未达标：{'；'.join(fails)}")
    return _Partial(True, 1.0, f"{head}；达标（教师线 {thr:.3f}）")


def _eval_skill_floor(rule: GateRule, ctx: _Ctx, teacher: Any) -> _Partial:
    """G2：0 杀占比 / 场均杀（教师相对）/ 被击中（教师相对）三项全过。"""
    max_zkf = rule.max_zero_kill_frac
    min_kills = (rule.min_kills_rel or 0.0) * float(teacher.kills)
    max_phits = (
        (rule.max_phits_rel * float(teacher.phits)) if rule.max_phits_rel is not None else None
    )

    def judge(r: EvalRow) -> str | None:
        if max_zkf is not None:
            if r.zero_kill_frac is None:
                return "缺 zero_kill_frac"
            if r.zero_kill_frac > max_zkf:
                return f"0 杀占比 {r.zero_kill_frac:.3f} > {max_zkf:.3f}"
        if rule.min_kills_rel is not None:
            if float(teacher.kills) <= 0:
                return "教师 kills=0（分母为零，子项跳过）"
            if r.kills_mean is None:
                return "缺 kills_mean"
            if r.kills_mean < min_kills:
                return f"场均杀 {r.kills_mean:.2f} < {min_kills:.2f}"
        if max_phits is not None:
            if float(teacher.phits) <= 0:
                return "教师 phits=0（分母为零，子项跳过）"
            if r.phits_mean is None:
                return "缺 phits_mean"
            if r.phits_mean > max_phits:
                return f"被击中 {r.phits_mean:.2f} > {max_phits:.2f}"
        return None

    completion, reason, unknown = _sustain(ctx, judge)
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"技能地板（0杀≤{max_zkf} / 杀≥{min_kills:.2f} / 被击中≤{max_phits}）：{reason}",
        unknown=unknown,
    )


def _eval_teacher_parity(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G10：|胜率 − 教师胜率| ≤ tol_pp（pp 口径），连续 sustain 轮。"""
    tol = (rule.tol_pp if rule.tol_pp is not None else 5.0) / 100.0

    def judge(r: EvalRow) -> str | None:
        if r.win_rate is None:
            return "本轮无 winRate"
        if abs(r.win_rate - ctx.teacher_win_rate) > tol:
            return f"|{r.win_rate:.3f} − {ctx.teacher_win_rate:.3f}| > {tol:.3f}"
        return None

    completion, reason, unknown = _sustain(ctx, judge)
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"教师触顶：{reason}",
        unknown=unknown,
    )


def _eval_plateau(rule: GateRule, ctx: _Ctx, completions: Mapping[str, float]) -> _Partial:
    """G4：窗口前后半均值差 ≤ 容差（各 metric 全满足）= 边际收益枯竭 → 分流。"""
    n = int(rule.window_rounds or 0)
    halves = _halves(rule, ctx, n)
    if halves is None:
        return _Partial(
            fired=False,
            completion=0.0,
            unknown=True,
            reason=f"窗口 {n} 轮数据不足（现有 {len(ctx.rows)} 轮）",
        )
    first, second = halves
    tol_pp = rule.tol_pp if rule.tol_pp is not None else 8.0
    tol_kills = rule.tol_kills if rule.tol_kills is not None else 0.5
    # 缺失的 metric 跳过（只判有的）——要求至少判了一项。理由：kills_mean 这类
    # 技能子指标要等 summary 富化后才在盘（旧语料一律缺），若因它缺就把整个平台
    # 门变 unknown，门在旧腿上等于焊死（c6 回溯实测：只看 win_rate 时 G4 会在
    # it30 判枯竭 → REMEDIATE，而"缺 kills 就 unknown"让它全程 HOLD）。
    checked: list[str] = []
    for m in rule.metrics or ["win_rate"]:
        a = _mean(first, m)
        b = _mean(second, m)
        if a is None or b is None:
            continue
        tol = tol_pp / 100.0 if m in ("win_rate", "timeout_frac") else tol_kills
        if abs(b - a) > tol:
            return _Partial(
                fired=False,
                completion=0.0,
                reason=f"{m} 前后半 {a:.4f}→{b:.4f}（Δ{b - a:+.4f}）仍超容差 {tol:.4f}——尚未枯竭",
            )
        checked.append(m)
    if not checked:
        return _Partial(
            fired=False, completion=0.0, unknown=True, reason="窗口内所有 metric 均无数据（不判）"
        )
    route = _route_by_completion(rule, completions)
    return _Partial(
        fired=True,
        completion=1.0,
        reason=f"窗口 {n} 轮 {','.join(checked)} 变化均在容差内（边际收益枯竭）→ {route}",
        route=route,
    )


def _eval_budget(rule: GateRule, ctx: _Ctx, completions: Mapping[str, float]) -> _Partial:
    """G5：预算到顶（max_hours / iters）→ STOP，route 按 advance_if 完成度填。"""
    if ctx.now is None or ctx.budget is None:
        return _Partial(
            fired=False, completion=0.0, unknown=True, reason="缺 now 或 budget（预算门不判）"
        )
    why = ctx.budget.exhaust_reason(ctx.now)
    if why is None:
        return _Partial(fired=False, completion=0.0, reason="预算未到顶")
    route = _route_by_completion(rule, completions)
    return _Partial(fired=True, completion=1.0, reason=f"预算到顶：{why} → {route}", route=route)


def _eval_course_valid(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G7：超时占比超限**且**窗口斜率 > 0（严格单调改斜率，ds-P2-1）→ REMEDIATE。"""
    max_tf = rule.max_timeout_frac
    rows = ctx.rows
    if not rows:
        return _Partial(False, 0.0, unknown=True, reason="无 summary 行")
    latest = rows[-1]
    if latest.timeout_frac is None:
        return _Partial(False, 0.0, unknown=True, reason="缺 timeout_frac（不判）")
    if max_tf is not None and latest.timeout_frac <= max_tf:
        return _Partial(
            False,
            0.0,
            reason=f"超时占比 {latest.timeout_frac:.3f} ≤ {max_tf:.3f}（课程未失效）",
        )
    n = max(2, int(rule.rising_rounds or 3))
    win = list(rows[-n:])
    vals = [r.timeout_frac for r in win if r.timeout_frac is not None]
    if len(vals) < 2:
        return _Partial(False, 0.0, unknown=True, reason="超时序列不足 2 点（斜率不可算）")
    s = _slope(vals)
    if s <= 0:
        return _Partial(
            False,
            0.0,
            reason=f"超时占比 {latest.timeout_frac:.3f} 超限但斜率 {s:+.4f} ≤ 0（未恶化）",
        )
    return _Partial(
        True,
        1.0,
        reason=f"超时占比 {latest.timeout_frac:.3f} 超限且近 {len(vals)} 轮斜率 {s:+.4f} > 0",
    )


def _eval_hack(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G9：拾取↑ 且 击杀↓（双向条件，P0-3）→ PAUSE（ABORT 留给人工 override）。"""
    n = int(rule.window_rounds or 0)
    halves = _halves(rule, ctx, n)
    if halves is None:
        return _Partial(
            False,
            0.0,
            unknown=True,
            reason=f"窗口 {n} 轮数据不足（现有 {len(ctx.rows)} 轮）",
        )
    first, second = halves
    pu_a, pu_b = _mean(first, "pickup_mean"), _mean(second, "pickup_mean")
    k_a, k_b = _mean(first, "kills_mean"), _mean(second, "kills_mean")
    if None in (pu_a, pu_b, k_a, k_b):
        return _Partial(False, 0.0, unknown=True, reason="缺 pickup_mean/kills_mean（不判）")
    assert pu_a is not None and pu_b is not None and k_a is not None and k_b is not None
    up_rel = rule.pickup_up_rel if rule.pickup_up_rel is not None else 0.5
    down_rel = rule.kills_down_rel if rule.kills_down_rel is not None else 0.2
    pu_up = pu_b >= pu_a * (1.0 + up_rel)
    k_down = k_b <= k_a * (1.0 - down_rel) if k_a > 0 else k_b <= k_a
    if not (pu_up and k_down):
        return _Partial(
            False,
            0.0,
            reason=f"单向：拾取 {pu_a:.2f}→{pu_b:.2f}（需 ≥+{up_rel:.0%}）"
            f"/ 击杀 {k_a:.2f}→{k_b:.2f}（需 ≤−{down_rel:.0%}）",
        )
    return _Partial(
        True,
        1.0,
        reason=f"拾取 {pu_a:.2f}→{pu_b:.2f} 且 击杀 {k_a:.2f}→{k_b:.2f}（疑似 hack）",
    )


def _eval_cross_course(
    rule: GateRule, ctx: _Ctx, all_rows: Sequence[Mapping[str, Any]]
) -> _Partial:
    """G3/G8（跨课）：按 `course` 名匹配 trend_rows 里的异课行。

    首期两门默认休眠（§3.3）。启用时：无该课行 = 值班缺勤 → unknown，**只阻塞
    依赖它的 ADVANCE，不触发任何反向判决**（§4.5）。
    """
    target = str(rule.course or "")
    if not target:
        return _Partial(False, 0.0, unknown=True, reason="未配 course")
    sub = [
        r
        for r in (
            _row_from_summary(x)
            for x in all_rows
            if str(x.get("course") or x.get("course_name") or "") == target
        )
        if r is not None
    ]
    if not sub:
        return _Partial(
            False, 0.0, unknown=True, reason=f"无课程 '{target}' 的 summary 行（值班缺勤）"
        )
    latest = sub[-1]
    if rule.kind == "transfer":
        need_wins = rule.min_wins
        need_kills = rule.min_kills
        if need_wins is not None and latest.wins < int(need_wins):
            return _Partial(
                False, 0.0, reason=f"'{target}' 胜 {latest.wins} < {need_wins}（迁移未达标）"
            )
        if need_kills is not None and (latest.kills_mean or 0.0) < float(need_kills):
            return _Partial(
                False, 0.0, reason=f"'{target}' 场均杀 {latest.kills_mean} < {need_kills}"
            )
        return _Partial(True, 1.0, reason=f"'{target}' 迁移达标（单次，不需 sustain）")
    # retention
    min_wr = rule.min_win_rate if rule.min_win_rate is not None else 0.85
    if latest.win_rate is not None and latest.win_rate < min_wr:
        return _Partial(
            True,
            1.0,
            reason=f"'{target}' 胜率 {latest.win_rate:.3f} < {min_wr:.3f}（回退）",
        )
    return _Partial(False, 0.0, reason=f"'{target}' 胜率 {latest.win_rate} ≥ {min_wr:.3f}（保持）")


def _eval_dependency(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G11：kind 保留，M3 实现（§3.3）——恒为 dormant，永不触发判决。"""
    return _Partial(False, 0.0, dormant=True, reason="dependency 未实现（M3）")


def _eval_duty(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G13 事故熔断（2026-09-11 评审新增；§385 修复口径）：有效训练占空比过低 → REMEDIATE。

    c6 的病灶形态：6.5h 墙钟里只有 ~1h 训练（事故/排队吃掉 5.5h）。墙钟预算门
    （G5）看不出来——"3h 全是事故、训练 40min"在 G5 眼里只是"还没到顶"。

    口径（§385 修复）：
      duty = Σ ppo_cloud_sec / (now − 首个完成迭代的结束时刻)
      - 分母基线自**首个完成迭代**起算——开腿冷启动（it1 常磨数十分钟）与停车重启
        死时间是常态起步成本，不写进事故分母；否则 it1 的债终身稀释占空比，门不停
        自激（2026-09-11 c6b-margin：duty 0.06→0.13，每个评估轮必停）。
      - 分子不变：Σ 云端自报真训练秒（跨重启从迭代事件重算）。
      - 最小样本守卫：完成迭代 < `DUTY_MIN_EVENTS` → unknown（不判，防开门即响）。
      - 无 duty 基线数据（旧调用方）→ 回退终身口径（started_at 起算），行为不变。
    """
    frac = rule.min_train_frac if rule.min_train_frac is not None else 0.3
    if ctx.budget is None or ctx.now is None:
        return _Partial(False, 0.0, unknown=True, reason="缺 budget/now（duty 门不判）")
    b = ctx.budget
    if b.duty_baseline_ts is not None:
        if b.duty_events < DUTY_MIN_EVENTS:
            return _Partial(
                False,
                0.0,
                unknown=True,
                reason=f"完成迭代 {b.duty_events} < {DUTY_MIN_EVENTS}（起步期不判）",
            )
        baseline: float | None = b.duty_baseline_ts
    else:
        baseline = b.started_at  # 老调用方回退终身口径
    if baseline is None:
        return _Partial(False, 0.0, unknown=True, reason="无 duty 基线（duty 门不判）")
    wall = float(ctx.now) - baseline
    if wall <= 0:
        return _Partial(False, 0.0, unknown=True, reason="无墙钟基线（duty 门不判）")
    duty = b.train_sec / wall
    if duty >= frac:
        return _Partial(False, 0.0, reason=f"有效训练占空比 {duty:.2f} ≥ {frac:.2f}（健康）")
    hours = b.train_sec / 3600.0
    return _Partial(
        True,
        1.0,
        reason=(
            f"有效训练占空比 {duty:.2f} < {frac:.2f}"
            f"（{hours:.2f}h 训练 / {wall / 3600.0:.2f}h 墙钟·自首个完成迭代起）——腿在烧事故，复诊"
        ),
    )


def _route_by_completion(rule: GateRule, completions: Mapping[str, float]) -> str:
    """G4/G5 分流：advance_if 里**最弱一环**的完成度 ≥ advance_frac → ADVANCE。

    取 min 而非均值：任一前置门没站稳就不放行（保守口径）。目标门本身是分流门
    （理论上会成环）时按 0.0 计并在 reason 里点名。
    """
    if not rule.advance_if:
        # 没写 advance_if = 无人替 ADVANCE 背书 → 保守走 REMEDIATE。
        return "REMEDIATE"
    frac = rule.advance_frac if rule.advance_frac is not None else 1.0
    weakest = min(completions.get(gid, 0.0) for gid in rule.advance_if)
    return "ADVANCE" if weakest >= frac else "REMEDIATE"


_JUDGES_NO_TEACHER: dict[str, Callable[[GateRule, _Ctx], _Partial]] = {
    "wins_mastery": _eval_wins_mastery,
    "teacher_parity": _eval_teacher_parity,
    "course_valid": _eval_course_valid,
    "hack": _eval_hack,
    "duty": _eval_duty,
    "dependency": _eval_dependency,
}


def _eval_one(
    rule: GateRule,
    ctx: _Ctx,
    all_rows: Sequence[Mapping[str, Any]],
    spec: GatesSpec,
    completions: Mapping[str, float] | None = None,
) -> _Partial:
    """单门分发（未知 kind 已在解析期拦掉；这里兜底为 dormant，不崩训练）。"""
    comp = completions or {}
    if not rule.enabled:
        return _Partial(False, 0.0, dormant=True, reason="休眠（enabled=false）")
    if rule.kind in _JUDGES_NO_TEACHER:
        return _JUDGES_NO_TEACHER[rule.kind](rule, ctx)
    if rule.kind == "skill_floor":
        return _eval_skill_floor(rule, ctx, spec.teacher)
    if rule.kind == "plateau":
        return _eval_plateau(rule, ctx, comp)
    if rule.kind == "budget":
        return _eval_budget(rule, ctx, comp)
    if rule.kind in ("transfer", "retention"):
        return _eval_cross_course(rule, ctx, all_rows)
    return _Partial(False, 0.0, dormant=True, reason=f"kind '{rule.kind}' 未实现")
