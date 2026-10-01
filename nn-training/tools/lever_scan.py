#!/usr/bin/env python
"""塑形杠杆扫描 —— 用已有账本回答「下一个奖励项该定价什么」。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/lever_scan.py

**为什么**（2026-09-30）：Wave 2 判出「剂量轴到顶」（`docs/nn/experiments.md` §69）⇒ 下一步要在
**别的塑形杠杆**里选一个。选之前必须先看数，否则又是「先立项后找证据」。本工具只吃已在盘上的
账本（判据段 2700 局 + Wave 2 日常段 + c05 同款杠杆腿），产九张表：

  §1 面板体检        — 局数 / 臂 / 结局分布（先把分母钉住）
  §2 税基尺寸表      — 每个可定价量**实际占多少 tick**（空转的杠杆在这里就被筛掉）
  §3 剂量扫描        — b0/b1/b2 的 14 列逐列 Δ（**换了个方式付账**的直接证据）
  §4 结局解剖        — gameover vs stage_clear 的逐 tick 归一差（**什么区分胜负**）
  §5 相位解剖        — 开局窗 vs 后段的伤害密度（pooled Σ伤害/Σtick；按结局分层）
  §6 逐局相关        — 哪个量与承伤/胜负同向（探索性，含混淆标注）
  §7 同款杠杆实测    — c05 的开局窗腿 / 零伤腿趋势（防重复花钱）
  §9 致死余量        — 承伤与胜负的耦合有多松（清关局能扛多少）
  §10 行动分布       — NN 选了哪些动作（STOP 缺口的直接读数）

**口径纪律**：本工具是**探索**，不是判据——这些数**不进** DoD / 门 / 阈值（AGENTS §0.2：人类数字
不得反推训练侧门限；本工具根本不吃人类语料）。**不复制算术**：账本归属与键来自 `tools/wave2_judge.py`
（它再按路径取 `tools/paired_power.py` 的共用面），相关系数用 `paired_power.corr`。
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from common.jsonc import load as jsonc_load


def _load_tool(name: str) -> Any:
    """按**文件路径**加载 `tools/<name>.py`（tools/ 非包；同 tests/ 的手法）。"""
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_{name}_for_lever_scan", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


WJ = _load_tool("wave2_judge")
PP = WJ.PP

# 剂量扫描与结局解剖要看的列（都是**已落盘**的账本列；没有的列不许现编）
SCAN_COLS: tuple[str, ...] = (
    "onLaneTicks",
    "onLaneMoveTicks",
    "onLaneHoldFireTicks",
    "onLaneExemptTicks",
    "ticks",
    "kills",
    "playerShots",
    "cellsVisited",
    "playerDamageTaken",
    "dmgFirst600",
    "dangerTicks",
    "stuckTicks",
    "firstKillTick",
    "playerHpRatio",
)

# c05 同款杠杆腿（顺带把「已花钱的杠杆」读数算出来，防重复立项）
C05_LEGS: tuple[tuple[str, str], ...] = (
    ("h5a-earlydmg", "开局窗承伤定价 -wEarlyDmg*dmgFirst600"),
    ("h5b-clean", "零伤清关奖 wClean*where(clear && dmg==0)"),
)


# ── 纯函数（`tests/test_lever_scan.py` 直接钉）───────────────────────────────
def per_tick(row: dict[str, Any], key: str) -> float:
    """每 tick 率（分母 `ticks+1`，与主终点 rA2 同式）。"""
    return float(row[key]) / (int(row["ticks"]) + 1)


def mean_col(rows: list[dict[str, Any]], key: str) -> float:
    """逐局均值（缺列按 0，与账本口径一致：v9 前的行没有方位列）。"""
    return statistics.fmean([float(r.get(key) or 0) for r in rows]) if rows else 0.0


def sum_col(rows: list[dict[str, Any]], key: str) -> float:
    return float(sum(float(r.get(key) or 0) for r in rows))


def tax_base_share(rows: list[dict[str, Any]], num_key: str, den_key: str = "ticks") -> float:
    """`Σnum / Σden`（pooled 占比，%）——「这笔税实际压在多少 tick 上」。"""
    den = sum_col(rows, den_key)
    return sum_col(rows, num_key) / den * 100.0 if den else 0.0


def cohens_d(a: list[float], b: list[float]) -> float:
    """两组的标准化均值差（合并 sd，ddof=1）；任一组 <2 或零方差 ⇒ 0。"""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    sd_a, sd_b = statistics.stdev(a), statistics.stdev(b)
    pooled = ((sd_a * sd_a * (len(a) - 1) + sd_b * sd_b * (len(b) - 1)) / (len(a) + len(b) - 2)) ** 0.5
    return (statistics.fmean(a) - statistics.fmean(b)) / pooled if pooled else 0.0


def phase_densities(
    rows: list[dict[str, Any]], window: int = 600
) -> tuple[float, float, float, float]:
    """`(开局密度, 窗后密度, 窗内占时长, 窗内占伤害)`——全部 **pooled**（Σ伤害/Σtick）。

    ⚠ 逐局均值口径有**短局截尾**陷阱：阵亡局多在 600t 内死、窗后只剩几十 tick ⇒ 后段密度虚高。
    pooled 含全部局、分母不编（空组 ⇒ 0）。
    """
    total_ticks = sum(int(r["ticks"]) for r in rows)
    early_ticks = sum(min(int(r["ticks"]), window) for r in rows)
    late_ticks = total_ticks - early_ticks
    total_dmg, early_dmg = sum_col(rows, "playerDamageTaken"), sum_col(rows, "dmgFirst600")
    late_dmg = max(total_dmg - early_dmg, 0.0)
    return (
        early_dmg / early_ticks if early_ticks else 0.0,
        late_dmg / late_ticks if late_ticks else 0.0,
        early_ticks / total_ticks if total_ticks else 0.0,
        early_dmg / total_dmg if total_dmg else 0.0,
    )


#: 已记录的人类「开局段 STOP 率」——**引用**（h5a-earlydmg/h5b-clean 课程头注，勿当本工具产出）
HUMAN_OPENING_STOP_PCT = 54.5


def quantile(values: list[float], p: float) -> float:
    """经验分位（升序取 ceil(p·n)−1 位置；空列表 ⇒ 0）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(p * len(ordered))))
    return ordered[idx]


def action_shares(rows: list[dict[str, Any]]) -> list[float] | None:
    """`moveHist` 5 桶的 pooled 占比（%）；桶 = [STOP, ↑, ↓, ←, →]。全缺 ⇒ None。"""
    tot = [0.0] * 5
    for r in rows:
        mh = r.get("moveHist")
        if isinstance(mh, list) and len(mh) == 5:
            for i in range(5):
                tot[i] += float(mh[i] or 0)
    s = sum(tot)
    return [t / s * 100 for t in tot] if s else None


def group_by_outcome(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按 `outcome` 分组（实测取值 = `stage_clear` / `gameover`）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r.get("outcome")), []).append(r)
    return out


def leg_trend(ledger: Path) -> list[tuple[int, int, float, float, float]]:
    """一条腿的日常段 → `[(iter, n, pass率, dmg/局, dmgFirst600/局)]`（按 iter 升序）。"""
    per: dict[int, list[dict[str, Any]]] = {}
    if not ledger.exists():
        return []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("event") != "eval":
            continue
        it = row.get("iter")
        if isinstance(it, int):
            per.setdefault(it, []).append(row)
    return [
        (it, len(g), mean_col(g, "win"), mean_col(g, "playerDamageTaken"), mean_col(g, "dmgFirst600"))
        for it, g in sorted(per.items())
    ]


def main() -> int:
    wmap, problems = WJ.key_map()
    for pr in problems:
        print(f"  ✗ 权重定位：{pr} ⇒ 面板不可信")
    rows_by_arm: dict[str, list[dict[str, Any]]] = {a: [] for a in WJ.ARMS}
    unknown = 0
    if any(not p.exists() for p in WJ.LEDGERS.values()):
        print("判据段账本不在 tmp/h4-lane-judge/ ⇒ 扫描跳过（非失败）")
        return 0
    for it in WJ.ITS:
        part, n_unknown = WJ.load_rows(WJ.LEDGERS[it], wmap)
        unknown += n_unknown
        for a in WJ.ARMS:
            rows_by_arm[a].extend(part[a].values())
    print("=" * 100)
    print("塑形杠杆扫描（探索性读数；**不进** DoD/门/阈值，AGENTS §0.2）")
    print("=" * 100)
    print(f"  语料：判据段 {sum(len(v) for v in rows_by_arm.values())} 局（未知权重键 {unknown} 行）")

    # §1 面板体检
    print()
    print("§1 面板体检（分母先钉住）")
    print(f"  {'臂':>4} {'局数':>6} {'清关':>6} {'阵亡':>6} {'阵亡率':>8} {'ticks/局':>9} {'dmg/局':>8}")
    for a in WJ.ARMS:
        g = group_by_outcome(rows_by_arm[a])
        n = len(rows_by_arm[a])
        n_clear, n_dead = len(g.get("stage_clear", [])), len(g.get("gameover", []))
        print(
            f"  {a:>4} {n:>6} {n_clear:>6} {n_dead:>6} {n_dead / n * 100 if n else 0:>7.1f}% "
            f"{mean_col(rows_by_arm[a], 'ticks'):>9.0f} {mean_col(rows_by_arm[a], 'playerDamageTaken'):>8.2f}"
        )
    print(f"  （其他 outcome 取值：{sorted(set().union(*[set(group_by_outcome(v)) for v in rows_by_arm.values()]))}）")

    # §2 税基尺寸表
    print()
    print("§2 税基尺寸表（每笔税**实际压在多少 tick 上**；pooled = Σnum/Σticks）")
    print(f"  {'可定价量':<22} {'占 ticks':>10} {'占 lane':>9} {'每局均值':>10} {'现役公式是否已用':<18}")
    ref = rows_by_arm["b1"]
    lane_sum = sum_col(ref, "onLaneTicks")
    usage = {
        "onLaneTicks": "否（A2 曾试，判无效）",
        "onLaneMoveTicks": "**是**（Wave 1/2 的 wLane）",
        "onLaneHoldFireTicks": "否（A1/A2 只做反证门）",
        "onLaneExemptTicks": "否（豁免子集，净价可用）",
        "dangerTicks": "否（c20 同族已判负）",
        "dmgFirst600": "否（c05 h5a 已试）",
        "playerDamageTaken": "**是**（wDmg 0.03）",
        "stuckTicks": "否",
    }
    for key in ("onLaneTicks", "onLaneMoveTicks", "onLaneHoldFireTicks", "onLaneExemptTicks", "dangerTicks", "dmgFirst600", "playerDamageTaken", "stuckTicks"):
        share = tax_base_share(ref, key)
        of_lane = sum_col(ref, key) / lane_sum * 100 if lane_sum else 0.0
        print(
            f"  {key:<22} {share:>9.3f}% {of_lane:>8.2f}% {mean_col(ref, key):>10.2f} "
            f"{usage.get(key, '—'):<18}"
        )
    print(
        "  ⇒ 读法：**现役 lane 税的税基只占 0.25% 的 tick**（在线 ∧ 移动），其余 lane 时间（静止/架枪）免费；"
    )
    print("     而 lane 全家只有 4.6% 的 tick ⇒ 这条轴「单位力度大、可动空间小」是结构性的。")

    # §3 剂量扫描
    print()
    print("§3 剂量扫描（尾 3 点均值；Δ = 相对 b0）——「罚得更重之后，它把钱花在哪了」")
    print(f"  {'列':<22} {'b0':>10} {'b1':>10} {'b2':>10} {'Δb1':>8} {'Δb2':>8} {'形状':<12}")
    for key in SCAN_COLS:
        m = {a: mean_col(rows_by_arm[a], key) for a in WJ.ARMS}
        d1 = (m["b1"] / m["b0"] - 1) * 100 if m["b0"] else 0.0
        d2 = (m["b2"] / m["b0"] - 1) * 100 if m["b0"] else 0.0
        same_dir = d1 * d2 > 0
        shape = "同向单调" if same_dir and abs(d2) > abs(d1) else ("同向非单调" if same_dir else "反向")
        print(
            f"  {key:<22} {m['b0']:>10.3f} {m['b1']:>10.3f} {m['b2']:>10.3f} "
            f"{d1:>7.1f}% {d2:>7.1f}% {shape:<12}"
        )
    print("  ⇒ 两档最醒目的付账方式：onLaneExemptTicks +131% / +110%（在线∧冻/盾翻倍）——")
    print("     或为更多冻/盾、或为更会选择豁免窗上线；账本无冻/盾时长列 ⇒ 两者不可分辨（重开 lane 轴前先补列）。")

    # §4 结局解剖
    print()
    print("§4 结局解剖（b1；清关 vs 阵亡；**逐 tick 归一**，避免「死得早所以量少」的假差）")
    g = group_by_outcome(rows_by_arm["b1"])
    clear, dead = g.get("stage_clear", []), g.get("gameover", [])
    print(f"  {'量':<26} {'清关':>10} {'阵亡':>10} {'d':>7}")
    pairs: tuple[tuple[str, str], ...] = (
        ("lane 率（/tick）", "onLaneTicks"),
        ("在线∧移动 率（/tick）", "onLaneMoveTicks"),
        ("危险 tick 率（/tick）", "dangerTicks"),
        ("卡死 tick 率（/tick）", "stuckTicks"),
        ("承伤/tick", "playerDamageTaken"),
        ("开局承伤/tick", "dmgFirst600"),
        ("kills", "kills"),
        ("ticks", "ticks"),
        ("firstKillTick", "firstKillTick"),
        ("cellsVisited", "cellsVisited"),
    )
    for label, key in pairs:
        as_rate = key not in ("ticks", "kills", "firstKillTick", "cellsVisited")
        scale = 100.0 if as_rate else 1.0
        a_vals = [float(r.get(key) or 0) * scale if not as_rate else per_tick(r, key) * scale for r in clear]
        b_vals = [float(r.get(key) or 0) * scale if not as_rate else per_tick(r, key) * scale for r in dead]
        txt_a = f"{statistics.fmean(a_vals):>9.3f}%" if as_rate else f"{statistics.fmean(a_vals):>10.2f}"
        txt_b = f"{statistics.fmean(b_vals):>9.3f}%" if as_rate else f"{statistics.fmean(b_vals):>10.2f}"
        print(f"  {label:<26} {txt_a} {txt_b} {cohens_d(a_vals, b_vals):>7.2f}")
    print("  ⇒ d = 标准化差（合并 sd）；|d| 越大越说明该量区分胜负（探索性）。")
    print("     ⚠ kills 与胜负**定义性重合**（clear = 全歼口径）⇒ 它的大 d 只当一致性检查，不当瓶颈证据。")

    # §5 相位解剖
    print()
    print("§5 相位解剖（开局 600 tick vs 窗后；pooled = Σ伤害/Σtick，按结局分层）")
    print(f"  {'分层':<14} {'开局密度':>10} {'窗后密度':>10} {'开局/窗后':>10} {'窗内占时长':>10} {'窗内占伤害':>10}")
    for label, grp in (("清关", clear), ("阵亡", dead), ("全体", rows_by_arm["b1"])):
        early, late, tick_share, dmg_share = phase_densities(grp)
        print(
            f"  {label:<14} {early:>10.4f} {late:>10.4f} {early / late if late else 0:>10.2f} "
            f"{tick_share:>9.1%} {dmg_share * 100:>9.1f}%"
        )
    print("  ⇒ 「开局/窗后」>1 ⇒ 开局相位更危险；≈1 ⇒ 伤害按时间摊平、固定窗口不是热点。")
    print("     阵亡组 <1（伤害堆在死前）是「阵亡」的定义性后果之一，不是可定价的窗口。")

    # §6 逐局相关
    print()
    print("§6 逐局相关（探索性；含混淆：lane 率与 ticks 强负相关 ⇒ 短局被高估；kills 行 = 定义性重合）")
    print(f"  {'臂':>4} {'r(lane率,dmg)':>14} {'r(lane率,kills)':>16} {'r(dmg,bad结局)':>15} {'r(kills,bad结局)':>17}")
    for a in WJ.ARMS:
        rs = rows_by_arm[a]
        lane_rate = [per_tick(r, "onLaneTicks") for r in rs]
        dmg = [float(r.get("playerDamageTaken") or 0) for r in rs]
        kills = [float(r.get("kills") or 0) for r in rs]
        bad = [1.0 if r.get("outcome") == "gameover" else 0.0 for r in rs]
        print(
            f"  {a:>4} {PP.corr(lane_rate, dmg):>14.3f} {PP.corr(lane_rate, kills):>16.3f} "
            f"{PP.corr(dmg, bad):>15.3f} {PP.corr(kills, bad):>17.3f}"
        )

    # §7 同款杠杆实测（c05 腿；防重复花钱）
    print()
    print("§7 同款杠杆实测（c05 腿的日常段；it0 → 末点）——「这个杠杆买到行为了吗、买到胜负了吗」")
    for course, desc in C05_LEGS:
        path = NN_ROOT / "curricula" / f"{course}.jsonc"
        ledger = ROOT / "tmp" / course / "eval_log.jsonl"
        rows_t = leg_trend(ledger)
        cfg = jsonc_load(str(path)) if path.exists() else {}
        print(f"  {course}（{cfg.get('level')}·iters {cfg.get('iters')}）：{desc}")
        if not rows_t:
            print("    账本缺 ⇒ 跳过")
            continue
        it0, it_last = rows_t[0], rows_t[-1]
        dl_pct = (it_last[3] / it0[3] - 1) * 100 if it0[3] else 0.0
        d6_pct = (it_last[4] / it0[4] - 1) * 100 if it0[4] else 0.0
        print(
            f"    点 {len(rows_t)} 个（it{it0[0]}–it{it_last[0]}，n={it_last[1]}/点）："
            f"pass {it0[2]:.4f}→{it_last[2]:.4f}（{(it_last[2] - it0[2]) * 100:+.1f}pp）· "
            f"dmg {it0[3]:.1f}→{it_last[3]:.1f}（{dl_pct:+.1f}%）· "
            f"dmgFirst600 {it0[4]:.1f}→{it_last[4]:.1f}（{d6_pct:+.1f}%）"
        )
    print("  ⇒ 读法：**机制动了 ≠ 胜负动了**——买到的行为若不在胜负瓶颈上，过绿线照样无望。")

    # §9 致死余量：承伤与胜负的耦合有多松
    print()
    print("§9 致死余量（承伤 vs 胜负的耦合；lives=1）")
    print(f"  {'臂':>4} {'结局':<13} {'n':>5} {'p10':>7} {'p50':>7} {'p90':>7} {'max':>7} {'hp率均':>8}")
    for a in WJ.ARMS:
        for oc in ("stage_clear", "gameover"):
            grp = [r for r in rows_by_arm[a] if r.get("outcome") == oc]
            if not grp:
                continue
            dmg = [float(r.get("playerDamageTaken") or 0) for r in grp]
            print(
                f"  {a:>4} {oc:<13} {len(grp):>5} {quantile(dmg, 0.1):>7.0f} {quantile(dmg, 0.5):>7.0f} "
                f"{quantile(dmg, 0.9):>7.0f} {max(dmg):>7.0f} {mean_col(grp, 'playerHpRatio'):>8.3f}"
            )
    grp_d = [r for r in rows_by_arm["b1"] if r.get("outcome") == "gameover"]
    grp_c = [r for r in rows_by_arm["b1"] if r.get("outcome") == "stage_clear"]
    lo_death = min(float(r.get("playerDamageTaken") or 0) for r in grp_d) if grp_d else 0.0
    hi_clear = max(float(r.get("playerDamageTaken") or 0) for r in grp_c) if grp_c else 0.0
    print(
        f"  ⇒ b1：阵亡局承伤**下界 {lo_death:.0f}**，而清关局**上界 {hi_clear:.0f}** ⇒ 承伤总量不单调决定胜负。"
    )
    print("     口径核实：`playerDamageTaken` = 非致死**真实扣血**累计（致死一击/星盾期不推）⇒ 总量是「挨过多少」而非当前血量；")
    print("     `repair` 回血不回滚累计（默认 normal 掉落池，回复 100）⇒ 伤害类价若想改胜负，口径应落「速率/临近致死」而非总量。")

    # §10 行动分布
    print()
    print("§10 行动分布（moveHist 池化占比；桶 = [STOP, ↑, ↓, ←, →]）")
    print(f"  {'分层':<18} {'STOP':>7} {'↑':>7} {'↓':>7} {'←':>7} {'→':>7} {'idle率':>8}")
    for a in WJ.ARMS:
        sh = action_shares(rows_by_arm[a])
        idle = statistics.fmean([per_tick(r, "idleTicks") for r in rows_by_arm[a] if r.get("idleTicks") is not None]) * 100
        if sh:
            print(f"  {a:<18} " + "".join(f"{v:>6.2f}%" for v in sh) + f" {idle:>7.2f}%")
    for oc in ("stage_clear", "gameover"):
        sh = action_shares([r for r in rows_by_arm["b1"] if r.get("outcome") == oc])
        if sh:
            print(f"  b1·{oc:<15} " + "".join(f"{v:>6.2f}%" for v in sh) + f" {'—':>8}")
    print(
        f"  ⇒ NN 几乎**从不选 STOP**（0.0x% 量级）；而仓库**已记录**的人类「开局段 STOP 率」= "
        f"{HUMAN_OPENING_STOP_PCT}%（引用：h5a/h5b 课程头注）"
    )
    print("     ⚠ 这是**引用**不是本工具产出；且 eval 侧 `stopRuns` 覆盖太低（段数 ≈ 0.03/局）⇒ 立腿前先补测量。")

    print()
    print("§8 结论与排序 → `docs/nn/experiments.md` §70（本文只出数，不立法）")
    return 0 if unknown == 0 and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
