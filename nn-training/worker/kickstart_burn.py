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
  阈值（margin/points）2026-10-02 起走**课程文件**的 `kickstart_burn` 块（字段缺失 = 下面的常量）；
  块缺席 = 模块缺省——第二刀已删 rl-config 回落读面（止损是实验设计，跟腿入库；
  DECISIONS §2026-10-02-goalnn-burn-rule-in-course-file）。
- **纯函数 + 行驱动**：`burn_verdict(rows)` 只看账本行 ⇒ 可回放、可单测、可在控制台
  用同一函数复算（不许在别处写第二份判据）。

与过程熔断（`biz/breaker.py` 的 KL/熵）**正交**：那个看更新健康度，这个看腿有没有把
起点洗回去。命中即停腿告警「疑似回锚/塌陷」——烧的是一整天算力，不是一轮。

参照物有两档（`MODE_*`，2026-09-30 起）：

- **`baseline`**：参照 = 本腿自己的 it0（上面那套，历史行为，一字未改）。它能抓**两臂一起**
  被洗回去的形态（C 事故两臂同幅），但也因此把「训练本身在飘」当成「这条腿在退」。
- **`paired`**：参照 = **对端臂的同 it 读数**（Δ = 本臂 − 对端，判据本体复用
  `worker/paired_kill.py::paired_kill_verdict` —— 同一份 Δ 判据，不写第二份）。Δ 把两臂**共享**
  的漂移减掉，只剩**可归因**于本臂的那部分。

为什么必须补 `paired`（`nn-training/tools/backtest-burn-rule.py` 用 h4-lane 三腿实测回测；
该工具可重跑，含生产实现交叉对账）：

- 三腿里**零奖励的对照臂 a0**（真零效应）逐点 pattern = `..TT.T.`，峰值 streak **2** —— 只差
  一个点就被自己的规则杀掉；被处理臂 a2 = `.TTT` ⇒ 触发。**在「三腿都没有可归因效应」的
  前提下，旧规则的假阳性 = 1/3。**
- 蒙特卡洛（每点 200 局二项噪声，7 个评估点）：无漂移下旧规则 FP 仅 0.005%，但把**对照臂
  实测轨迹**当共享真值时 FP = **49.3%** ⇒ **假阳性来自参照物（自己的起点）会飘，不是来自
  抽样噪声**。同样两个零假设下 `paired` 是 0.31% / **0.47%** ⇒ 低约 105×。
- 代价明买明卖：**无漂移**下 `paired` 反而略高（0.005% → 0.31%），因为配对差的抽样方差更大
  （σ_Δ = √2·σ ≈ 3.48pp vs 2.46pp）。三腿实测：a0 峰值 2 → a2 峰值 1，被误杀的那条腿被救回。
- 阈值**沿用现常量**（5pp / 3 点）：语义仍是「落后参照物 5pp 连 3 个评估点」，只换参照物 ⇒
  配对读下零假设 FP = 0.56%（比旧规则的 49.3% 低 88×）。

对端不唯一时**不猜**（多臂家族里「谁是控」是实验设计，机器不替人挑）⇒ 退回 `baseline` 并响亮
提示（见 `worker/loop_guards_leg.py::_burn_paired_rows`）。
"""

from __future__ import annotations

from typing import Any, NamedTuple

from worker.paired_kill import paired_kill_verdict

#: 连续多少个评估点仍低于基线才停腿（执行面常量；课程文件 `kickstart_burn.points` 优先，
#: 块缺席 = 本常量——2026-10-02 迁移 + 第二刀，见模块头）。
#: 3 而不是 1：单点有噪声（评估分母 50–200 局，±5pp 属正常抖动），三点连着低才是趋势——
#: 与事故的读数节奏对得上（it1 就低，it25/it30 已肉眼可见，本可在 it5 前后停）。
BURN_POINTS = 3

#: 「低于基线多少」才算（百分点）。5pp 是噪声带之上、又远小于 C 事故那次的 ~15pp 崩幅。
BURN_MARGIN_PP = 5.0

#: 读哪个读数（`eval_summary` 行的字段名）。**winRate** = 干净评估胜率，与控制台
#: 配对基线同口径。
METRIC = "winRate"

#: 参照物 = 本腿自己的 it0（历史行为）。
MODE_BASELINE = "baseline"
#: 参照物 = 对端臂同 it 读数（同网格配对差，判据本体复用 `worker/paired_kill.py`）。
MODE_PAIRED = "paired"
#: 缺省：能解析出**唯一**同 V 对端就走 `paired`，否则回 `baseline`。
MODE_AUTO = "auto"

#: 合法模式闭集（脏值一律回 `MODE_AUTO`，不拿坏配置停腿）。
MODES: tuple[str, ...] = (MODE_AUTO, MODE_BASELINE, MODE_PAIRED)


class BurnVerdict(NamedTuple):
    """判定结果（NamedTuple：可落账、可断言、可原样进日志）。

    `mode`/`delta_pp`/`peer` 三个新字段都带缺省值 ⇒ 既有五元位置构造一字不改。
    `baseline` 在两种模式下**同义**（本腿 it0 读数，控制台按它跟账本对账）；配对模式额外带
    `delta_pp`（尾部对齐点的 Δ，百分点）与 `peer`（对端在该点的读数）。
    """

    tripped: bool
    streak: int
    baseline: float | None
    last: float | None
    reason: str
    #: `MODE_BASELINE` / `MODE_PAIRED`（实际生效的那个，不是配置里写的 `auto`）。
    mode: str = MODE_BASELINE
    #: 配对模式：尾部**对齐**点的 Δ（百分点，本臂 − 对端）；未配对 → None。
    delta_pp: float | None = None
    #: 配对模式：对端在该点的读数；未配对 → None。
    peer: float | None = None


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
    peer_rows: list[Any] | tuple[Any, ...] | None = None,
    peer_name: str = "",
) -> BurnVerdict:
    """账本行 → 干烧判定。**同一份输入永远同一份输出**（回放/单测/控制台复算同源）。

    只数**尾部连续**的低点（`streak`）：中间反弹一次即清零——「连续 N 点」的语义与
    `biz/breaker.py` 的 KL/熵熔断一致（`*_CONSEC`），也与事故回顾里「中点杀臂连续 2 点」
    的执行口径一致。

    读数缺失的行（`winRate` 为 None，例如该轮 0 局）**既不计数也不清零**：它没有读数，
    拿它当「低于基线」会误停腿，拿它清零又会把真趋势打断。

    `peer_rows is not None` ⇒ 走 `MODE_PAIRED`：参照物换成**对端臂的同 it 读数**，判据本体
    **整段委托** `worker.paired_kill.paired_kill_verdict`（同一份 Δ 判据，不在这里写第二份）；
    `baseline`/`last` 两格仍填本腿自己的 it0 与尾部读数（与 `baseline` 模式同义，控制台照旧
    对账），Δ 与对端读数另放 `delta_pp`/`peer`。`peer_rows is None` ⇒ `MODE_BASELINE`，
    **历史行为一字未改**。
    """
    if peer_rows is not None:
        pv = paired_kill_verdict(rows, peer_rows, points=points, margin_pp=margin_pp)
        reason = ""
        if pv.tripped:
            who = peer_name or "对端"
            reason = (
                f"配对参照（{who}）：{pv.reason}"
                "——参照物已换成同 it 对端（同网格配对差），不再拿本腿自己的起点判"
            )
        return BurnVerdict(
            pv.tripped,
            pv.streak,
            baseline_reading(rows),
            pv.own,
            reason,
            MODE_PAIRED,
            pv.delta_pp,
            pv.peer,
        )
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


def _block_dict(block: Any) -> dict | None:
    """课程止损块 → 普通 dict；None/坏值 → None。

    读面消费 `args` 上的**启动物化快照**（`flat_overrides` 以 `model_dump()` 落成 dict =
    restart-only 的冻结面）；这里认 dict 与 pydantic 块两种形态——单测可直接喂模型。
    """
    if isinstance(block, dict):
        return block
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def burn_mode(block: Any) -> tuple[str, str]:
    """执行面模式：课程文件 `kickstart_burn.{mode,peer}`——**块存在即权威**；块缺席 → 缺省。

    块由 `biz.course_spec.KickstartBurnBlock` 解析期强校验（脏值拒课）；这里仍按「未知/缺省 →
    模块缺省」读。块存在但字段没写 ⇒ 用模块缺省（第二刀起 rl-config 回落已删，只有这一条路）。
    `peer` = 显式指定对照臂的课程名（多臂家族里机器不替人挑「谁是控」）。
    """
    d = _block_dict(block)
    if d is None:
        return (MODE_AUTO, "")
    m = d.get("mode")
    mode = m if isinstance(m, str) and m in MODES else MODE_AUTO
    p = d.get("peer")
    peer = p.strip() if isinstance(p, str) and p.strip() else ""
    return (mode, peer)


def burn_overrides(block: Any) -> tuple[float, int]:
    """执行面阈值：课程文件 `kickstart_burn.{margin_pp,points}`——**块存在即权威**；块缺席 → 常量。

    块存在但字段没写 ⇒ 模块缺省（第二刀起 rl-config 回落已删，避免「半块 + 半旧值」的第三态）；
    脏值已在解析期拒课，这里仍保留「未知 → 缺省」的兜底纪律（不拿坏配置停腿）。
    """
    d = _block_dict(block)
    if d is None:
        return (BURN_MARGIN_PP, BURN_POINTS)
    m = d.get("margin_pp")
    p = d.get("points")
    margin = float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else BURN_MARGIN_PP
    pts = int(p) if isinstance(p, int) and not isinstance(p, bool) and p > 0 else BURN_POINTS
    return (margin, pts)
