"""kickstart 干烧熔断（结果面，plan/accident.plan.md §5.2，2026-09-21）。

事故：C 双臂从收敛权重（it175 起跑）以 `kk(1)=1` 满 kickstart 复活，it1 kl=0.90、
锚主导更新连烧 30 轮（惜败项被淹没），两臂从 ~34 洗回 ~50；锚释放后平摆、又跑 140 轮
没爬出来。两臂 ×12h ≈ 一整天算力，产出只有「无结论」。**过程熔断全程没响**——kl/ent
都是正常的；坏的是**结果**：这条腿的读数比它的起点（课程 bc 权重）还低。

所以本模块看结果，判据三条：

- **基线不靠人填**：`eval_log.jsonl` 里的 **it0 行**就是课程 bc 权重（= 缰绳锚定的
  同一份权重）在开课前跑的干净评估（`loop_core._maybe_dispatch_baseline_eval`）——
  同口径 ⇒ 「比基线还低」= 训练把它拉回去了，正是要治的那件事。
- **落执行面，不进课程 gates 块**：阶梯课程一律不配 gates（`ladder_factory` 的 I2：
  G4 cloud halt 自杀教训）；课程侧只在注释里写基线读数，机器读的是**实测**行。
  阈值（margin/points）走 rl-config 的 `courses.<课>.kickstart_burn`，缺席即下面的常量。
- **纯函数 + 行驱动**：`burn_verdict(rows)` 只看账本行 ⇒ 可回放、可单测、可在控制台
  用同一函数复算（不许在别处写第二份判据）。

与过程熔断（`rl/breaker.py` 的 KL/熵）**正交**：那个看更新健康度，这个看腿有没有把
起点洗回去。命中即停腿告警「疑似回锚/塌陷」——烧的是一整天算力，不是一轮。
"""

from __future__ import annotations

from typing import Any, NamedTuple

#: 连续多少个评估点仍低于基线才停腿（执行面常量；rl-config 可覆盖）。
#: 3 而不是 1：单点有噪声（评估分母 50–200 局，±5pp 属正常抖动），三点连着低才是趋势——
#: 与事故的读数节奏对得上（it1 就低，it25/it30 已肉眼可见，本可在 it5 前后停）。
BURN_POINTS = 3

#: 「低于基线多少」才算（百分点）。5pp 是噪声带之上、又远小于 C 事故那次的 ~15pp 崩幅。
BURN_MARGIN_PP = 5.0

#: 读哪个读数（`eval_summary` 行的字段名）。**winRate** = 干净评估胜率，与控制台
#: 配对基线同口径。
METRIC = "winRate"


class BurnVerdict(NamedTuple):
    """判定结果（NamedTuple：可落账、可断言、可原样进日志）。"""

    tripped: bool
    streak: int
    baseline: float | None
    last: float | None
    reason: str


def baseline_reading(rows: list[Any] | tuple[Any, ...]) -> float | None:
    """基线 = 最后一条 it0 行（`iter <= 0`）的读数；无该行 → None（不判、不停腿）。

    为什么要「最后一条」：重开一腿会重跑一次 it0 基线评估（同一份 bc 权重、不同种子），
    取最新一条即「本腿的起点」。旧行留在文件里不影响（只取最新）。
    """
    base: float | None = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        it = r.get("iter")
        if not isinstance(it, int) or it > 0:
            continue
        v = _reading(r)
        if v is not None:
            base = v
    return base


def _reading(row: dict) -> float | None:
    """行内读数（缺失/非数值/bool → None = unknown，**不当 0**）。"""
    v = row.get(METRIC)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def burn_verdict(
    rows: list[Any] | tuple[Any, ...],
    *,
    points: int = BURN_POINTS,
    margin_pp: float = BURN_MARGIN_PP,
) -> BurnVerdict:
    """账本行 → 干烧判定。**同一份输入永远同一份输出**（回放/单测/控制台复算同源）。

    只数**尾部连续**的低点（`streak`）：中间反弹一次即清零——「连续 N 点」的语义与
    `rl/breaker.py` 的 KL/熵熔断一致（`*_CONSEC`），也与事故回顾里「中点杀臂连续 2 点」
    的执行口径一致。

    读数缺失的行（`winRate` 为 None，例如该轮 0 局）**既不计数也不清零**：它没有读数，
    拿它当「低于基线」会误停腿，拿它清零又会把真趋势打断。
    """
    base = baseline_reading(rows)
    if base is None:
        return BurnVerdict(False, 0, None, None, "无 it0 基线行（读不到起点）——不判")
    floor = base - margin_pp / 100.0
    streak = 0
    last: float | None = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        it = r.get("iter")
        if not isinstance(it, int) or it <= 0:
            continue  # it0 = 基线本身，不参与计数
        v = _reading(r)
        if v is None:
            continue  # unknown：不计数、不清零（见 docstring）
        last = v
        streak = streak + 1 if v < floor else 0
    if streak >= points:
        return BurnVerdict(
            True,
            streak,
            base,
            last,
            f"连续 {streak} 个评估点低于基线 {margin_pp:.1f}pp"
            f"（基线 {base * 100:.1f}%，最新 {0.0 if last is None else last * 100:.1f}%）"
            "——疑似回锚/塌陷（锚主导更新把起点洗了回去）",
        )
    return BurnVerdict(False, streak, base, last, "")


def burn_overrides(dist_cfg: dict | None, course_key: str) -> tuple[float, int]:
    """执行面阈值覆盖：`courses.<课>.kickstart_burn.{margin_pp,points}`；缺席 → 常量。

    放在 rl-config 而**不**放课程文件：课程文件参与 course_fp 血缘（D14），且 §5.2 的
    裁决是「执行面配置、课程只写注释」。与 `resolve_course_quota` 的机器配额同规。
    """
    block = (((dist_cfg or {}).get("courses") or {}).get(course_key) or {}) if course_key else {}
    kb = block.get("kickstart_burn") if isinstance(block, dict) else None
    if not isinstance(kb, dict):
        return (BURN_MARGIN_PP, BURN_POINTS)
    m = kb.get("margin_pp")
    p = kb.get("points")
    margin = float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else BURN_MARGIN_PP
    pts = int(p) if isinstance(p, int) and not isinstance(p, bool) and p > 0 else BURN_POINTS
    return (margin, pts)
