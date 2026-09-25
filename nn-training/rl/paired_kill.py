"""配对**中点杀臂**（结果面，plan/accident.plan.md 附 §5，2026-09-21）。

事故原文一行：「中点杀臂条件（连续 2 点 <−3pp）在 it25+it30 触发，**当时无人执行**（凌晨）……
事后看执行也改变不了 verdict（两臂收敛同带），但**规则 Trustee 缺席 = 规则不存在**：
下次中点必须有人/闹钟。」

机械判据就是把那个「人」换成一个不需要醒着的守卫。三要素与计划原文一字不差：

- **同 it 对齐**：只比两臂**都**有读数的 it（每个 it 取**末条**——续腿/重开以最新为准），
  因为配对差 Δ 只有在 `(rotateSeed, it)` 一致时才有意义（§2.5 的配对前提）。
- **Δ = 本臂 − 对端**（百分点）；负 = 本臂更差。
- **尾部连续 2 个点 Δ < −3pp** ⇒ 响亮 + 停腿 + 落 `paired_kill` 事件（阈值走执行面
  `courses.<课>.paired_kill`，缺席用模块常量——与 `kickstart_burn` 同规）。

为什么是「尾部连续」而不是「历史上出现过」：一旦本臂追上，计数必须归零（与
`rl/breaker.py` 的 KL/熵连击、`rl/kickstart_burn.py` 的低点连击同一语义：中途反弹一次
即清零）。为什么阈值 3pp / 2 点：评估分母 50–200 局时 ±2pp 属正常抖动，而计划的中点
判据实测是在 it25+it30 触发（相隔一个评估节拍）——两点连着低才是趋势，单点不是。

本模块**纯函数**（数字进、判定出）：可回放、可单测、控制台可用同一函数复算
（不许在别处写第二份 Δ 判据）。IO 与停腿动作在 `rl/loop_guards.py::_paired_kill`。
"""

from __future__ import annotations

from typing import Any, NamedTuple

#: 连续多少个**对齐**评估点 Δ 都低于阈值才杀臂（执行面常量；rl-config 可覆盖）。
PAIRED_KILL_POINTS = 2

#: Δ 的阈值（百分点，本臂 − 对端）。3pp 是噪声带之外、又远小于 C 事故里两臂的真实分岔。
PAIRED_KILL_MARGIN_PP = 3.0

#: 读哪个读数（`eval_summary` 行的字段名）。与 `kickstart_burn` 同一个字段（干净评估胜率）。
METRIC = "winRate"

#: 浮点边界容差：`0.37 - 0.40 = -0.030000000000000027` 会被 `< -0.03` 判成「低于阈值」，
#: 而那是**恰好压在阈值上**（不杀）。阈值是 pp 量级，1e-9 远小于任何真实分岔，
#: 只用来吃掉二进制浮点的表示误差——不让它替操作员做杀臂决定。
_EPS = 1e-9


class PairedKillVerdict(NamedTuple):
    """判定结果（NamedTuple：可落账、可断言、可原样进日志）。"""

    tripped: bool
    #: 尾部连续「Δ 低于阈值」的点数。
    streak: int
    #: 尾部那个点的 Δ（百分点）；无对齐点 → None。
    delta_pp: float | None
    #: 尾部那个点的 it。
    it: int | None
    #: 尾部那个点的两臂读数。
    own: float | None
    peer: float | None
    #: 对齐点数是否够判（`>= points`）；不够 → 不判、不杀（数据不足不是证据）。
    enough: bool
    reason: str


def readings_by_iter(rows: list[Any] | tuple[Any, ...]) -> dict[int, float]:
    """`eval_summary` 行 → `{it: 读数}`（每个 it 取**末条**；缺读数/坏行跳过）。"""
    out: dict[int, float] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        it = r.get("iter")
        if not isinstance(it, int) or isinstance(it, bool):
            continue
        v = r.get(METRIC)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue  # unknown 不当 0（同 kickstart_burn 的纪律）
        out[int(it)] = float(v)
    return out


def paired_kill_verdict(
    own_rows: list[Any] | tuple[Any, ...],
    peer_rows: list[Any] | tuple[Any, ...],
    *,
    points: int = PAIRED_KILL_POINTS,
    margin_pp: float = PAIRED_KILL_MARGIN_PP,
) -> PairedKillVerdict:
    """两臂账本行 → 中点杀臂判定。**同一份输入永远同一份输出**。

    对齐口径：it 升序取**交集**（两臂都有读数的那些点），只看尾部连续段。`it <= 0` 的点
    （it0 基线评估 = 课程 bc 权重）不参与——那是两臂共同的起点，Δ≈0 且与「中点在退」无关。
    """
    own = {k: v for k, v in readings_by_iter(own_rows).items() if k > 0}
    peer = {k: v for k, v in readings_by_iter(peer_rows).items() if k > 0}
    both = sorted(set(own) & set(peer))
    if len(both) < points:
        return PairedKillVerdict(
            False, 0, None, None, None, None, False, f"两臂对齐的评估点只有 {len(both)} 个（需 {points}）"
        )
    floor = -abs(margin_pp) / 100.0
    streak = 0
    last: tuple[int, float, float] | None = None
    for it in both:
        d = own[it] - peer[it]
        last = (it, own[it], peer[it])
        streak = streak + 1 if d < floor - _EPS else 0
    assert last is not None  # both 非空 ⇒ last 必被赋值
    it, o, p = last
    delta_pp = (o - p) * 100.0
    if streak >= points:
        return PairedKillVerdict(
            True,
            streak,
            delta_pp,
            it,
            o,
            p,
            True,
            f"同 it 配对差连续 {streak} 个点 < −{abs(margin_pp):.1f}pp"
            f"（it{it}: 本臂 {o * 100:.1f}% vs 对端 {p * 100:.1f}% = {delta_pp:+.1f}pp）"
            "——中点杀臂条件成立（本臂在退，且不是单点噪声）",
        )
    return PairedKillVerdict(False, streak, delta_pp, it, o, p, True, "")


def paired_kill_overrides(dist_cfg: dict | None, course_key: str) -> tuple[float, int]:
    """执行面阈值覆盖：`courses.<课>.paired_kill.{margin_pp,points}`；缺席 → 常量。

    与 `kickstart_burn.burn_overrides` 同规：**放 rl-config 不放课程文件**——课程文件参与
    `course_fp` 血缘（D14），而这是执行面策略（哪条腿该在什么分岔下被杀是运行决策）。
    """
    block = (((dist_cfg or {}).get("courses") or {}).get(course_key) or {}) if course_key else {}
    pk = block.get("paired_kill") if isinstance(block, dict) else None
    if not isinstance(pk, dict):
        return (PAIRED_KILL_MARGIN_PP, PAIRED_KILL_POINTS)
    m = pk.get("margin_pp")
    p = pk.get("points")
    margin = (
        float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else PAIRED_KILL_MARGIN_PP
    )
    pts = int(p) if isinstance(p, int) and not isinstance(p, bool) and p > 0 else PAIRED_KILL_POINTS
    return (margin, pts)


def paired_kill_self_kill(dist_cfg: dict | None, course_key: str) -> bool:
    """本臂命中时是否真停（`courses.<课>.paired_kill.self_kill`；缺席/写坏 → True）。

    2026-09-25 C-0 事故：对称自杀把**对照臂**杀了（对照落后 = 加权臂领先，正是加权要证明的；
    而终点配对 verdict 需要两条臂都活着）。对照臂设 `self_kill: false`：判据照算、streak
    照落账，只是不停车——"输了"照样记录，"死了"不行。

    只有显式 `False` 才关（缺席保持现状对称行为：已有课程零变化；非 bool 不当 False，
    与上面 margin/points 的脏值纪律同源）。
    """
    block = (((dist_cfg or {}).get("courses") or {}).get(course_key) or {}) if course_key else {}
    pk = block.get("paired_kill") if isinstance(block, dict) else None
    if not isinstance(pk, dict):
        return True
    v = pk.get("self_kill")
    return v is not False
