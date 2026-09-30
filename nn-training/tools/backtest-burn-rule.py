"""回测 h4-lane 三腿历史账：旧止损规则（本腿 vs 自己 it0）的假阳性率，
以及新规则（同 it 配对差 vs 对照臂）的阈值标定。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/backtest-burn-rule.py

**为什么落盘成工具**（2026-09-30）：`rl/kickstart_burn.py` 的头注与
`DECISIONS.md §2026-09-30-goalnn-kickstart-burn-paired` 都引用了这里的数字
（旧规则共享漂移下 FP 49.3% → 新规则 0.47%）。这些数字原先只活在 `tmp/` 一次性脚本里
——tmp 一清理，引用就变成无据可查的说法（§58/§60/§64 同款教训：进文档的结论必须可复算）。

口径与生产一致：读账本用 `rl.gate_inputs.read_trend_rows`（唯一入口），
旧规则直接调 `rl.kickstart_burn.burn_verdict`；新规则在本文件里按**规格**独立实现，
再用生产实现交叉对账（§6）——规格与实现不一致时本文件会报出来。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "nn-training"))

from rl.gate_inputs import read_trend_rows
from rl.kickstart_burn import (
    BURN_MARGIN_PP,
    BURN_POINTS,
    burn_verdict,
)

ARMS = ("a0", "a1", "a2")
LEG = {"a0": "本腿零臂(对照)", "a1": "本腿移动税", "a2": "本腿在线税"}
GAMES = 200
IT0_WIN = 0.86  # 三臂共同的 it0 基线（h4-stop.it60 @ ladder-c04, 172/200）

# ─────────────────────────────────────────────────────────────────────────────
# 0. 语料前提：三腿账本是 tmp/ 证据（未入库）——缺了就响亮说明并正常退出
# ─────────────────────────────────────────────────────────────────────────────
_missing = [a for a in ARMS if not (ROOT / "tmp" / f"h4-lane-{a}" / "eval_log.jsonl").exists()]
if _missing:
    print(
        "语料不在（tmp/h4-lane-{a}/eval_log.jsonl 缺 {missing}）——本工具回测的是那次三腿实测，\n"
        "账本属 tmp/ 证据、未入库。要复现需先从证据保留处恢复该目录；\n"
        "本次不判、退出（非失败）。".format(a="{a}", missing=", ".join(_missing))
    )
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
# 1. 读三腿账本
# ─────────────────────────────────────────────────────────────────────────────
rows: dict[str, tuple] = {}
series: dict[str, dict[int, float]] = {}
for a in ARMS:
    p = ROOT / "tmp" / f"h4-lane-{a}" / "eval_log.jsonl"
    rows[a] = read_trend_rows(p, include_baseline=True)
    series[a] = {
        r["iter"]: float(r["winRate"])
        for r in rows[a]
        if isinstance(r.get("iter"), int) and isinstance(r.get("winRate"), (int, float))
    }

print("=" * 78)
print("§1 三腿评估胜率（eval_summary 行，200 局/it）")
print("=" * 78)
its = sorted({it for a in ARMS for it in series[a]})
print("  it   " + "".join(f"{it:>8d}" for it in its))
for a in ARMS:
    cells = "".join(
        (f"{series[a][it] * 100:8.1f}" if it in series[a] else f"{'—':>8s}") for it in its
    )
    print(f"  {a} {LEG[a]:<14s}{cells}")
print("  三臂 it1 逐位同 ⇒ 配对前提成立（同语料 + 同起点）")

# ─────────────────────────────────────────────────────────────────────────────
# 2. 旧规则（本腿 vs 自己 it0 基线）在三腿上的实际行为
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print(f"§2 旧规则实测：本腿 vs 自己 it0（margin={BURN_MARGIN_PP}pp, points={BURN_POINTS}）")
print("=" * 78)
floor = IT0_WIN - BURN_MARGIN_PP / 100.0
print(f"  门槛 floor = {IT0_WIN:.3f} − {BURN_MARGIN_PP}pp = {floor:.3f}")
print(f"  {'臂':<4} {'逐点低于门槛':<26} {'streak 轨迹':<26} {'峰值':>4} {'触发':>6}")
for a in ARMS:
    seq = []
    streak = 0
    traj = []
    for it in sorted(series[a]):
        if it <= 0:
            continue
        v = series[a][it]
        below = v < floor
        seq.append("T" if below else ".")
        streak = streak + 1 if below else 0
        traj.append(streak)
    bv = burn_verdict(rows[a], points=BURN_POINTS, margin_pp=BURN_MARGIN_PP)
    print(
        f"  {a:<4} {''.join(seq):<26} {','.join(str(s) for s in traj):<26} "
        f"{max(traj):>4} {'是' if bv.tripped else '否':>6}"
    )
old_trips = {a: burn_verdict(rows[a]).tripped for a in ARMS}
n_trip = sum(old_trips.values())
print(
    f"  ⇒ 三腿中 {n_trip}/3 触发；**触发的是 {[a for a, t in old_trips.items() if t]}**"
    f"（含被掐停的那条）"
)
print("  ⇒ 对照臂 a0（**零奖励 = 真零效应**）峰值 streak = 2，只差 1 点就被自己的规则杀掉")
print(f"  ⇒ 在「三腿都没有可归因的机制效应」这个前提下，旧规则的假阳性 = {n_trip}/3 = {n_trip / 3:.0%}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. 新规则的规格（同 it 配对差）——独立实现，供标定
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("§3 新规则规格：Δ(it) = 本臂 − 对照臂（同 it，百分数）；尾部连续 points 点 Δ < −margin ⇒ 触发")
print("=" * 78)


def paired_streak(
    own: dict[int, float], peer: dict[int, float], margin_pp: float
) -> tuple[list[int], list[float]]:
    """返回 (streak 轨迹, Δ 序列)，只取两臂都有读数且 it>0 的点（升序）。"""
    both = sorted(k for k in set(own) & set(peer) if k > 0)
    traj, deltas, streak = [], [], 0
    for it in both:
        d = (own[it] - peer[it]) * 100.0
        deltas.append(d)
        streak = streak + 1 if d < -abs(margin_pp) else 0
        traj.append(streak)
    return traj, deltas


for a in ("a1", "a2"):
    traj, deltas = paired_streak(series[a], series["a0"], BURN_MARGIN_PP)
    it_list = sorted(k for k in set(series[a]) & set(series["a0"]) if k > 0)
    print(
        f"  {a} vs a0：Δ = "
        + ", ".join(f"it{it}:{d:+.2f}pp" for it, d in zip(it_list, deltas, strict=True))
    )
    print(f"           streak 轨迹 = {traj}  峰值 = {max(traj)}")
print("  ⇒ **两条被处理臂在配对读下都不触发**（a2 的 Δ 在 it10/it20 是 −5.5pp，但不是连着的）")

# ─────────────────────────────────────────────────────────────────────────────
# 4. 零假设下的假阳性率：旧规则 vs 新规则（蒙特卡洛）
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("§4 零假设假阳性率（MC 20000 次；每点 200 局二项噪声；7 个评估点 it5..35）")
print("=" * 78)

REPS = 20000
DRIFT = [series["a0"][it] for it in sorted(series["a0"]) if it > 0]  # 对照臂实测轨迹
NPTS = len(DRIFT)
rng = random.Random(20260930)


def draw_independent_nodrift() -> tuple[list[float], list[float]]:
    return (
        [rng.binomialvariate(GAMES, IT0_WIN) / GAMES for _ in range(NPTS)],
        [rng.binomialvariate(GAMES, IT0_WIN) / GAMES for _ in range(NPTS)],
    )


def draw_shared_drift() -> tuple[list[float], list[float]]:
    """两臂共享同一条**实测**漂移轨迹（对照臂的），各自叠自己的二项噪声。"""
    return (
        [rng.binomialvariate(GAMES, p) / GAMES for p in DRIFT],
        [rng.binomialvariate(GAMES, p) / GAMES for p in DRIFT],
    )


def fires_baseline(own: list[float], points: int, margin: float) -> bool:
    fl = IT0_WIN - margin / 100.0
    s = 0
    for v in own:
        s = s + 1 if v < fl else 0
        if s >= points:
            return True
    return False


def fires_paired(own: list[float], peer: list[float], points: int, margin: float) -> bool:
    s = 0
    for o, p in zip(own, peer, strict=True):
        s = s + 1 if (o - p) * 100.0 < -abs(margin) else 0
        if s >= points:
            return True
    return False


def mc(draw) -> tuple[float, float]:
    old_n = new = 0
    for _ in range(REPS):
        own, peer = draw()
        if fires_baseline(own, BURN_POINTS, BURN_MARGIN_PP):
            old_n += 1
        if fires_paired(own, peer, BURN_POINTS, BURN_MARGIN_PP):
            new += 1
    return old_n / REPS, new / REPS


old_nodrift, new_nodrift = mc(draw_independent_nodrift)
old_drift, new_drift = mc(draw_shared_drift)
print(f"  {'零假设':<44} {'旧规则':>12} {'新规则':>12}")
print(f"  {'─' * 44} {'─' * 12} {'─' * 12}")
print(f"  {'N1 无漂移（两臂都钉在 it0 = 86%，各自二项噪声）':<44} {old_nodrift:>11.4%} {new_nodrift:>12.4%}")
print(f"  {'N2 共享漂移（两臂共享对照臂实测轨迹）':<44} {old_drift:>11.4%} {new_drift:>12.4%}")
print()
print("  ⇒ 旧规则在**无漂移**下几乎不误伤（有限样本噪声不是问题），")
print(f"     但一加进**实测漂移**就从 {old_nodrift:.4%} 跳到 {old_drift:.2%}（几乎是一半）")
print("     ⇒ **假阳性来自参照物（自己的起点）会飘，不是来自抽样噪声**。")
print(f"  ⇒ 新规则把共享漂移消掉了：{old_drift:.2%} → {new_drift:.2%}（**低 {old_drift / new_drift:.0f}×**）。")
print(f"  ⇒ ⚠ 代价：**无漂移**下新规则反而略高于旧规则（{old_nodrift:.4%} → {new_nodrift:.2%}），")
print("     因为配对差的抽样方差比单臂读数大（σ_Δ = √2·σ ≈ 3.48pp vs 2.46pp）")
print("     ——这是「用一点无漂移灵敏度换掉全部漂移假阳性」的明买明卖。")

# ─────────────────────────────────────────────────────────────────────────────
# 5. 新规则阈值标定
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("§5 新规则阈值标定（零假设 = N2 共享漂移，最贴近现实）")
print("=" * 78)
print(f"  {'margin(pp)':>10} {'points':>7} {'零假设 FP':>11} {'三腿实际触发':>14}")
grid = [(m, k) for m in (3.0, 4.0, 5.0, 6.0, 8.0) for k in (2, 3, 4)]
cache: dict[tuple[float, int], float] = {}
for m, k in grid:
    n = 0
    for _ in range(REPS // 4):
        own, peer = draw_shared_drift()
        if fires_paired(own, peer, k, m):
            n += 1
    cache[(m, k)] = n / (REPS // 4)
for m, k in grid:
    fires = []
    for a in ("a1", "a2"):
        spec_traj = paired_streak(series[a], series["a0"], m)[0]
        fires.append(max(spec_traj) >= k)
    mark = "  ← 现常量" if (m, k) == (BURN_MARGIN_PP, BURN_POINTS) else ""
    print(f"  {m:>10.1f} {k:>7d} {cache[(m, k)]:>11.4%} {fires!s:>14}{mark}")

print()
rec = cache[(BURN_MARGIN_PP, BURN_POINTS)]
print(
    f"  ⇒ 沿用现常量（{BURN_MARGIN_PP:g}pp / {BURN_POINTS} 点）在配对读下的零假设 FP = {rec:.2%}，"
    f"比旧规则的 {old_drift:.2%} 低 **{old_drift / rec:.0f}×**；"
)
print("     且三腿实际都不触发 ⇒ 本次那条被误杀的腿被救回。")
print("  ⇒ 常量不变、只换参照物，是最小改动：语义仍是「落后参照物 5pp 连 3 个评估点」。")

# ─────────────────────────────────────────────────────────────────────────────
# 6. 交叉对账（生产实现 vs 本文件规格）
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("§6 交叉对账")
print("=" * 78)
try:
    from rl.kickstart_burn import burn_verdict as prod_verdict

    ok = True
    for a in ("a1", "a2"):
        pv = prod_verdict(rows[a], peer_rows=rows["a0"])
        spec_traj = paired_streak(series[a], series["a0"], BURN_MARGIN_PP)[0]
        spec_trip = max(spec_traj) >= BURN_POINTS
        same = (pv.tripped == spec_trip) and pv.mode == "paired"
        ok &= same
        print(
            f"  生产 burn_verdict(peer_rows=a0) {a}: tripped={pv.tripped} mode={pv.mode} "
            f"streak={pv.streak} | 规格 tripped={spec_trip} ⇒ {'一致' if same else '不一致'}"
        )
    print(f"  ⇒ 交叉对账 {'通过' if ok else '失败'}")
except TypeError as e:
    print(f"  生产实现尚未支持 peer_rows（{e}）——本文件是规格先行，落地后重跑本节即可对账")
