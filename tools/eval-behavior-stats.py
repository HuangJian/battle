"""eval-behavior-stats.py —— NN 战斗行为统计：死局（命耗尽）vs 活局（通关）对照表。

用途：回答"怎么死的"——把一轮 eval 里 gameover 局和 stage_clear 局的行为指标并排，
看死局富集什么、缺什么。零杀/超时局另单列一行，不混入死活对比。

用法（仓根）：
    python3 tools/eval-behavior-stats.py --course y5-crown --iter 125
    python3 tools/eval-behavior-stats.py --course y5-pace --iter 115 --stage

口径（与训练门逐字同，三条纪律来自 2026-10-16 y5 横盘诊断的自我审查；
明细见 `reports/y5-winrate-plateau-20261016.md` §2/§8）：
- "/千"率分母 = Σ(ticks+1)（headline 口径）；" /局" = 局均值；占比 = 占 enemyHits。
- **总量 vs 归一**：死局比活局短（y5 收敛带实测 1592 vs 2254 拍，−29%），总量对比全是局长假象
  （S2 停总量"死局更少"即误判；W4 贴脸 nearSq raw 总量"死局更低"同理）。看行为一律看率/占比，
  S4（停占比）才是 S2 的诚实版。
- **均值 vs 中位**：零膨胀列（D2–D4/E1/G1/超时中伤）中位塌到 0，均值被少数重伤局拉走。
  本表主列仍是均值（与门/headline 可比），中位为 0 而均值非 0 的行打 ※零膨胀标——
  读数以方向为准，不读倍数。
- **定义量 ≠ 行为量**：D1（`playerHits`）在 28046 个死亡局上与 `playerDeaths` **100% 相等**
  ⇒ 那一行的倍数（~140x）是"1 命死了就是死了"的同义反复，**不要当信号读**。
- 数据源 = tmp/<课>/eval_log.jsonl 的 `event=eval` 行；只读不写。
- 死 = outcome gameover（命耗尽：lives 0、基地完好）；活 = stage_clear。
- gameover 样本 <10 局打 ⚠（比值不稳，只看方向）。
- 进不了本表的（eval 行没有逐步列）：选中动作置信 −logπ、V(t)、hurt 窗口增量——
  那是 rollout shard 的地盘（`tmp/y5-autopsy*.py` 管）：本表只到局 FIN 行的聚合列。

行为解释（每条见 METRICS 注，口径已和用户对齐十次， summarized）：
- 背击/侧击是**奖**：我从背后/侧面打中敌人 = 无伤杀敌（敌人还不了手），多多益善。
  死局少 = 正面硬换血（dmg 3 倍的来源）。
- 贴脸是**上游**：4 格内时长本身不罚（near×2 无差别税是死刑前科），看的是"贴了之后"——
  贴了被围（encl）、贴了被火线盖（threat/hurt）、贴了中了不跑（postHit）。
- 连续挨打（bursts/maxDamage120/damageWhileLow）是**结果形态**：脱离失败的时间投影。
  E0 已判死刑（docs/nn/experiments.md §73：低血天花板＋致死一击不在口径，定价无效），
  本脚本只做诊断列，不做定价依据。
- 中了不跑（postHitLaneTicks）是**行为**：挨打后还站在火线上，和 threat/encl 同族
  （"在哪"税）。v11 列已存在（idx75），定价前先补人类分离度，本机不可跑。
- 被包围/蹲角落/威胁lane：站位税已覆盖（wEncl 全场最轻 8.6，加压候选；wCorner/wThreat 重税）。
- 拾取：活局多吃；死局少吃可能是"死得早没机会吃"（因果待查：加码前先时序配对，
  拾取差是否先于死亡）。
"""

from __future__ import annotations

import argparse
import json
import sys

# (编号, 显示名, 取值, 口径, 行为解释)
# 取值：("game", f) 局均值；("rate", f) /千率；占比类用 "game" + share。
# f 收一行 eval row，返回数字或 None（None 跳过）。
METRICS = [
    # ── 进攻质量：背击/侧击是奖（无伤杀敌）， far 无故事 ──
    ("B1", "背击数/局", "game", lambda r: r.get("backHits", 0),
     "我从敌人背后打中的发数。背后打=敌人还不了手=无伤杀敌，多多益善（奖不是税）。死局少=正面硬换血。"),
    ("B2", "侧击数/局", "game", lambda r: r.get("sideHits", 0),
     "侧面打中的发数，同背击（奖）。"),
    ("B3", "背击占比", "game",
     lambda r: r.get("backHits", 0) / r["enemyHits"] if r.get("enemyHits") else None,
     "高质量击杀占比。局长归一后仍低=真不会绕（不是没机会绕）。"),
    ("B4", "侧击占比", "game",
     lambda r: r.get("sideHits", 0) / r["enemyHits"] if r.get("enemyHits") else None,
     "同上。1.1x 级弱分离不认，方向对也不开价。"),
    ("B5", "远射命中/局", "game", lambda r: r.get("farHits", 0),
     "远射命中。两边都≈0，无故事。"),
    ("B6", "正面豁免/局", "game", lambda r: r.get("frontHitsExempt", 0),
     "正面打中但被免伤（无敌/护盾/擦过）。背/侧击之外的第四类：正面硬刚还被免=白给。"),
    ("B7", "对枪开火/局", "game", lambda r: r.get("aimIgnited", 0),
     "和敌人对枪的开火数（用户定义）。敢不敢对枪：死局少=火力被压住，结合火压 S3 看。"),
    # ── 瞄准纪律：公式计价了，这里看死活 ──
    ("A1", "瞄准命中/局", "game", lambda r: r.get("aimHits", 0),
     "瞄准后打中的发数（wHit/wAcc 已计价）。死局少=没打中就死了。"),
    ("A2", "瞄准脱靶/局", "game", lambda r: r.get("aimMisses", 0),
     "瞄准了没打中（wMiss 已税）。死局脱靶多=慌。"),
    ("A3", "平均命中距离", "game",
     lambda r: r.get("aimHitDistSum", 0) / r["aimHits"] if r.get("aimHits") else None,
     "命中时的平均敌距。死局近=顶脸开火（A05 的 eval 版）。"),
    ("A4", "打砖/局", "game", lambda r: r.get("aimBricks", 0),
     "打中地形。死局高=乱开火。"),
    # ── 挨打：频率 vs 时间形态（E0 死刑区，只诊断）──
    ("D1", "扣血次数/局", "game", lambda r: r.get("playerHits", 0),
     "挨了几下（频率）。死局≈1.0、活局≈0——命只有一条的证据。"),
    ("D2", "连续挨打段数/局", "game", lambda r: r.get("damageBursts", 0),
     "短窗连续掉血的段数=脱离失败的结果形态。E0死刑（§73）：定价无效，只诊断。"),
    ("D3", "120拍最大掉血", "game", lambda r: r.get("maxDamage120", 0),
     "120拍窗口最大掉血，同 D2（E0 死刑区）。"),
    ("D4", "残血又挨", "game", lambda r: r.get("damageWhileLow", 0),
     "残血时又挨的打，同 D2（E0 死刑区）。看着馋，不开价。"),
    # ── 站位：贴脸是上游，被围/火线/角落是已有税 ──
    ("W1", "危险格/千", "rate", lambda r: r.get("dangerTicks", 0),
     "站在危险格里的时间。crown 死局 4.5x=开局逛进危险区。"),
    ("W2", "威胁lane/千", "rate", lambda r: r.get("threatTicks", 0),
     "站在敌人火线覆盖上的时间。已有税 wThreat=120（重税）。死局 3x=税听不见或改不动。"),
    ("W3", "贴脸时长/千", "rate", lambda r: r.get("nearEnemy4Ticks", 0),
     "4格内时长=贴多久（不加权：一格和四格等权计数）。本身不罚（near×2前科）；死活同量=时长无辜。"),
    ("W4", "贴脸率·加权/千", "rate", lambda r: r.get("nearSqSum", 0),
     "nearSq 加权=贴多近（一格权重远大于四格）。主2门线 110 的本体；比较贴脸一律用它，不用 W3。"),
    ("W5", "被包围加权/千", "rate", lambda r: r.get("enclWeightTicks", 0),
     "≥2条敌火线夹击的时间。已有税 wEncl=8.6（全场最轻）+死局2x=加压候选（剂量安全）。"),
    ("W6", "最大包围数", "game", lambda r: r.get("enclMax", 0),
     "同时最多几面包夹。死活≈2.6 无差=包围烈度不分生死，分的是包围时长（W5）。"),
    ("W7", "蹲角落加权/千", "rate", lambda r: r.get("cornerWeightTicks", 0),
     "贴角落区时间。已有税 wCorner=110（重税）。"),
    ("W8", "火线承伤加权/千", "rate", lambda r: r.get("hurtWeight", 0),
     "暴露状态下挨打。总量税 wHurt=110（信号摊在分母里变稀→死局6-7x却改不动=刚性，加码排队）。"),
    # ── 中了不跑：行为税候选 ──
    ("P1", "中了不跑/局", "game", lambda r: r.get("postHitLaneTicks", 0),
     "挨打后还站在火线上（中了不跑）。v11列已存在（idx75），定价前先补人类分离度。"),
    # ── 发育与节奏：拾取因果待查，首杀看慢热 ──
    ("G1", "拾取/局", "game", lambda r: r.get("powerUpsCollected", 0),
     "吃道具/升级。死局少吃可能是死得早（因果待查：加码前先时序配对，拾取差是否先于死亡）。"),
    ("G2", "首杀拍", "game", lambda r: r.get("firstKillTick"),
     "第一个击杀在第几拍=开局慢热度。crown死局慢24%，别处无差。"),
    ("G3", "逛格/局", "game", lambda r: r.get("cellsVisited", 0),
     "活动量。死局少=局短+龟缩。"),
    # ── 苟系：总量看死局更少是局短假象，归一化后死局更爱停（Q3 在 eval 独立复现）──
    ("S1", "卡死/局", "game", lambda r: r.get("stuckTicks", 0),
     "撞墙顶住不动（总量！死局短所以显得少，诚实版看 S4）。"),
    ("S2", "停/局", "game", lambda r: r.get("stopTicks", 0),
     "无输入停（总量！同 S1 被局短掩盖；S4 归一化后死局 0.38 vs 活局 0.30，站桩实锤）。"),
    ("S3", "火压/局", "game", lambda r: r.get("fireHeldTicks", 0),
     "想开火被压住的拍。死局少=死前开火本来就少。"),
    ("S4", "停占比", "game",
     lambda r: r.get("stopTicks", 0) / (r.get("ticks", 0) + 1) if r.get("ticks") else None,
     "停拍占存活比（S2 的归一化诚实版）。死局更高=末端站桩，Q3（末 20 步 STOP 率）在 eval 复现。"),
    # ── 开局与包围拆解：crown 开局逛 + encl 罚时长还是罚烈度 ──
    ("E1", "开局600承伤", "game", lambda r: r.get("dmgFirst600", 0),
     "开局 600 拍挨的血（h5a 税列）。crown 死局开局逛的直接证据。"),
    ("E2", "被1面包围/千", "rate", lambda r: r.get("encl1Ticks", 0),
     "单面火线接触。死活无差=正常交战距离，不罚。"),
    ("E3", "被2面包围/千", "rate", lambda r: r.get("encl2Ticks", 0),
     "两面包夹。encl 拆解：死局死在哪一档，指导加压罚时长还是罚烈度。"),
    ("E4", "被3+面包围/千", "rate", lambda r: r.get("encl3pTicks", 0),
     "三面以上围死。同上。W6（最大包围数）无差，答案应该在时长档里。"),
    ("E5", "lane站/千", "rate", lambda r: r.get("onLaneTicks", 0),
     "站在 lane 上的时间（lane 行为学四件套之首）。"),
    ("E6", "lane豁免/千", "rate", lambda r: r.get("onLaneExemptTicks", 0),
     "lane 上但被豁免（冻/盾）的时间。"),
    ("E7", "lane移动开火/千", "rate", lambda r: r.get("onLaneMoveTicks", 0),
     "lane 上移动中开火（A08 的 lane 版）。"),
    ("E8", "lane持火/千", "rate", lambda r: r.get("onLaneHoldFireTicks", 0),
     "lane 上按住火力的时间。"),
    ("E9", "一次停几拍", "game",
     lambda r: r.get("stopTicks", 0) / len(r["stopRuns"]) if r.get("stopRuns") else None,
     "单次停留均长（stopRuns 是数组，取长度）。死局停得少，但一次停多久？"),
    # ── 发育结构：拾取拆包（运气/星/等级/关键道具）──
    ("G4", "道具刷新/局", "game", lambda r: r.get("powerUpsSpawned", 0),
     "刷了几个道具。运气对照：死局刷新少=运气差，先排除运气论。"),
    ("G5", "星星/局", "game", lambda r: r.get("starsCollected", 0),
     "吃到的星（升级核心，wFrStar 已计价）。"),
    ("G6", "终局等级", "game", lambda r: r.get("playerLevel", 0),
     "死时的坦克等级。死局等级低=没长大就死。"),
    ("G7", "关键道具/局", "game",
     lambda r: r.get("puGotFreeze", 0) + r.get("puGotBomb", 0) + r.get("puGotTank", 0),
     "冻/炸弹/加命拾取（翻盘三件套）。死局缺哪个？"),
    # ── dashboard 节奏四维 ──
    ("T1", "tempo", "game", lambda r: (r.get("dims") or {}).get("tempo"),
     "整局节奏分。死局低=全程被动。"),
    ("T2", "开局tempo", "game", lambda r: (r.get("dims") or {}).get("openingTempo"),
     "开局节奏分。crown 死局开局逛的第三证据（E1/G2 之外）。"),
    ("T3", "清场速度分", "game", lambda r: (r.get("dims") or {}).get("clearSpeed"),
     "死局无（没通关），只看活局分布。"),
    ("T4", "机动分", "game", lambda r: (r.get("dims") or {}).get("mobility"),
     "机动分。死局低=跑不动（被围困）还是不想跑？结合逛格 G3 看。"),
    # ── 基础 ──
    ("K0", "局长/局", "game", lambda r: r.get("ticks", 0),
     "局长拍。死局 1400-1700=第三个敌前后倒下；活局 2100+。"),
    ("K1", "击杀/局", "game", lambda r: r.get("kills", 0), "死局 1.8-2.4=第三个敌前后倒下。"),
    ("K2", "开火/局", "game", lambda r: r.get("playerShots", 0),
     "死局 8-10 发贴 fear 线（<8）：开火抑制和早死互为因果，watch 级。"),
    ("K3", "承伤/局", "game", lambda r: r.get("playerDamageTaken", 0),
     "死局≈200=活局 3 倍。"),
]


def load_eval_rows(course: str, it: int | None) -> tuple[list[dict], int]:
    """读 tmp/<课>/eval_log.jsonl，取某 it 的 eval 行；it=None 取最新有 summary 的轮。"""
    path = f"tmp/{course}/eval_log.jsonl"
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        sys.exit(f"no such course log: {path}")
    sums = []
    rows = []
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get("event") == "eval_summary":
                sums.append(o.get("iter"))
            elif o.get("event") == "eval":
                rows.append(o)
    if it is None:
        if not sums:
            sys.exit(f"{course}: no eval_summary found")
        it = max(sums)
    sel = [r for r in rows if r.get("iter") == it]
    if not sel:
        sys.exit(f"{course} it{it}: no eval rows (summaries: {sorted(sums)[-8:]})")
    return sel, it


def mean(vals: list) -> float | None:
    vs = [v for v in vals if v is not None and isinstance(v, (int, float))]
    if not vs:
        return None
    return sum(vs) / len(vs)


def median(vals: list) -> float | None:
    vs = sorted(v for v in vals if v is not None and isinstance(v, (int, float)))
    if not vs:
        return None
    m = len(vs) // 2
    return vs[m] if len(vs) % 2 else (vs[m - 1] + vs[m]) / 2


def zero_inflated(vals: list) -> bool:
    """零膨胀：中位塌到 0 而均值非 0（D2–D4/E1/G1 类）——倍数不读，只读方向。"""
    md, mn = median(vals), mean(vals)
    return md == 0 and (mn or 0) > 0


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return "—"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def main() -> None:
    ap = argparse.ArgumentParser(description="NN 战斗行为统计：死局 vs 活局")
    ap.add_argument("--course", required=True, help="课程名（tmp/<课>/eval_log.jsonl）")
    ap.add_argument("--iter", type=int, default=None, help="轮次；缺省最新 eval 轮")
    ap.add_argument("--stage", action="store_true", help="附分关胜/死/超时表")
    args = ap.parse_args()

    rows, it = load_eval_rows(args.course, args.iter)
    go = [r for r in rows if r.get("outcome") == "gameover"]
    cl = [r for r in rows if r.get("outcome") == "stage_clear"]
    to = [r for r in rows if r.get("outcome") == "max_ticks"]
    print(f"{args.course} it{it}: 共 {len(rows)} 局 = 通关 {len(cl)} / 命耗尽 {len(go)} / 超时 {len(to)}")
    if len(go) < 10:
        print("⚠ 死局 <10，比值只看方向不看大小")
    denom_go = sum(r.get("ticks", 0) + 1 for r in go) or 1
    denom_cl = sum(r.get("ticks", 0) + 1 for r in cl) or 1

    print(f"\n{'编号':<6}{'指标':<16}{'死局':>10}{'活局':>10}{'死/活':>8}  行为解释（一句话）")
    for code, name, kind, f, plain in METRICS:
        if kind == "game":
            gv, cv = [f(r) for r in go], [f(r) for r in cl]
            a, b = mean(gv), mean(cv)
            tag = " ※零膨胀" if (zero_inflated(gv) or zero_inflated(cv)) else ""
        else:
            a = sum(v for v in (f(r) for r in go) if v is not None) / denom_go * 1000
            b = sum(v for v in (f(r) for r in cl) if v is not None) / denom_cl * 1000
            tag = ""
        ratio = f"{a / b:.2f}x" if (a is not None and b not in (None, 0)) else "—"
        print(f"{code:<6}{name:<16}{fmt(a):>10}{fmt(b):>10}{ratio:>8}  {plain}{tag}")

    if to:
        k = mean([r.get("kills", 0) for r in to])
        s = mean([r.get("stuckTicks", 0) for r in to])
        td = [r.get("playerDamageTaken", 0) for r in to]
        print(f"\n超时 {len(to)} 局画像：击杀 {fmt(k)}（差一口气）、卡死 {fmt(s)}、"
              f"中伤中位 {fmt(median(td))}/均值 {fmt(mean(td))}"
              f"（中位 0＝一半超时局毫发无损找不着北，POMDP 不是菜）。")

    if args.stage:
        print("\n分关：胜率（死/超）")
        by: dict = {}
        for r in rows:
            by.setdefault(r.get("stage"), []).append(r)
        for st in sorted(by):
            rs = by[st]
            w = sum(1 for r in rs if r.get("cleared")) / len(rs)
            g = sum(1 for r in rs if r.get("outcome") == "gameover")
            t = sum(1 for r in rs if r.get("outcome") == "max_ticks")
            print(f"  {st}: 胜 {w:.2f}（死{g}/超{t}）")


if __name__ == "__main__":
    main()
