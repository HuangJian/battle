#!/usr/bin/env python
"""Wave 2（b 腿）判据段判决 —— b0/b1/b2 × it30/35/40 × 300 局 @ 池外 seed0 862001。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/wave2_judge.py

**为什么独立于 `paired_power.py`**（2026-09-30）：`paired_power.py` 的职责是 Wave 1 的功效
定案与判据表（§1–§11），本文件是「b 腿判据段的判决读法」（归属 / 尾巴判定 / 剂量-形状 /
§11 逐条方向判定 / 止损诊断 / 日常段独立复现）——不同所有者、不同触发条件，且
`paired_power.py` 已贴单文件 LOC < 1000 预算（`tests/test_python_loc_budget.py`）⇒ 按
「独立所有者 + 独立触发条件」拆模块（`plan/nn-training-refactor.md` §5.7）。

**不复制任何算术**：共用面（主终点 rA2 / 端点 spec 与阈值 / 配对统计 / 逐局差 / SE /
异质性 Q / CI 两条断言 / 正态尾 / MDE 常量）全部从 `tools/paired_power.py` 取。`tools/`
不是包、没有 `__init__.py`，`from tools import …` 会让 mypy 把同一份文件当两个模块 ⇒
与 `tests/test_paired_power.py` 同款**按文件路径 importlib 加载**。

预注册（训练前冻结，课程头注 ②）= `nn-training/curricula/h4-lane-b{0,1,2}.jsonc`；
结果 → `docs/nn/experiments.md` §69；决策 → `DECISIONS.md` §2026-09-30-goalnn-wave2-verdict。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from worker.kickstart_burn import BURN_MARGIN_PP


def _load_shared() -> Any:
    """按**文件路径**加载 `tools/paired_power.py`（tools/ 非包；同 tests/ 的手法）。"""
    path = Path(__file__).with_name("paired_power.py")
    spec = importlib.util.spec_from_file_location("_paired_power_for_wave2_judge", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # 先登记再 exec
    spec.loader.exec_module(mod)
    return mod


PP = _load_shared()

# ── 判据段常量（与课程头注 ② 逐字对应）───────────────────────────────────────
ARMS = ("b0", "b1", "b2")
CONTROL = "b0"
TREATED = ("b1", "b2")
ITS = (30, 35, 40)
SEED0 = 862001
GAMES_PER_ARM = 300
JUDGE_DIR = ROOT / "tmp" / "h4-lane-judge"
W_DIR = {a: NN_ROOT / "weights" / f"h4-lane-{a}" for a in ARMS}
LEDGERS = {it: JUDGE_DIR / f"judge_it{it}.jsonl.run" / "eval_log.jsonl" for it in ITS}
# 剂量（% of killEV）：b0 = 0 是零奖励对照臂；锚 f = 3.6% = Wave 1 的 a1 档（§67 实测 −8.52%）
F_PCT: dict[str, float] = {"b0": 0.0, "b1": 7.5, "b2": 15.0}
ANCHOR_F_PCT = 3.6
LINEAR_ANCHOR_REL = -8.52
# 日常段（独立复现）：另一套 seed，与判据段不相交
DAILY_LEDGER = {a: ROOT / "tmp" / f"h4-lane-{a}" / "eval_log.jsonl" for a in ARMS}

# §11 逐条阈值（课程头注那份数字；方向 = 该线**不许**越过的方向）
#   "rel_down" = 不许跌过 −margin% rel · "rel_up" = 不许升过 +margin% rel · "abs_*" 同理（原生单位）
MARGINS: dict[str, tuple[str, float] | None] = {
    "主终点 rA2": ("rel_down", 16.0),
    "A1 终点 rA1": ("rel_down", 16.0),
    "洞守卫 静止∧在线": ("abs_up", 0.007927),
    "holdFire 守卫": ("rel_down", 30.0),
    "pass": ("abs_down", 0.03),
    "零伤局": ("rel_down", 15.4),
    "dmg/局": ("rel_down", 10.0),
    "kills/局": ("rel_down", 3.0),
    "shots/局": ("rel_down", 3.9),
    "cellsVisited": ("rel_down", 10.0),
    "stuckTicks": ("rel_up", 10.0),
    "ticks/局": None,
    "timeout": None,
}


# ── 纯函数（`tests/test_wave2_judge.py` 直接钉）──────────────────────────────
def key_map() -> tuple[dict[str, tuple[str, int]], list[str]]:
    """判据段账本的权重身份键 → `(arm, judge_it)`；键 = 权重文件 `sha256[:16]`。

    一次性评估账本里 `wver` 与 `ckpt_sha16` **逐位相等**（实测 it30：b0
    `bd00d21e3dbcb926` / b1 `02f2d3b0b7d6425d` / b2 `38bd27531a6cfbdc`）= 权重文件
    sha256 **前 16 hex**（不是全 64 hex——第一版按全 sha 建表 ⇒ 2700 行全「未知」）。
    同一臂同一 it 的权重文件不唯一（多个时间戳）⇒ 记一条问题、不猜。
    """
    out: dict[str, tuple[str, int]] = {}
    problems: list[str] = []
    for a in ARMS:
        for it in ITS:
            hits = sorted(W_DIR[a].glob(f"h4-lane-{a}.it{it}.*.json"))
            if len(hits) != 1:
                problems.append(f"{a} it{it}：权重文件 {len(hits)} 个（应 1）")
                continue
            out[hashlib.sha256(hits[0].read_bytes()).hexdigest()[:16]] = (a, it)
    return out, problems


def load_rows(path: Path, wmap: dict[str, tuple[str, int]]) -> tuple[dict[str, dict[tuple[int, int], dict[str, Any]]], int]:
    """一份判据段账本 → `{arm: {(judge_it, seed): 行}}`，返回 `(rows, 未知行数)`。

    配对键的**第一元是判据点**（来自权重文件归属），不是账本里的 `iter`——一次性评估的账本
    `iter` 恒 0（`eval_course_once.py` 的 `batch["iter"] = 0`），拿它当键会把 it30/35/40 折成一批。
    """
    out: dict[str, dict[tuple[int, int], dict[str, Any]]] = {a: {} for a in ARMS}
    unknown = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event") != "eval":
                continue
            owner = wmap.get(str(row.get("ckpt_sha16") or row.get("wver")))
            seed = row.get("seed")
            if (
                owner is None
                or not isinstance(seed, int)
                or not isinstance(row.get("ticks"), (int, float))
                or not isinstance(row.get("onLaneTicks"), (int, float))
            ):
                unknown += 1
                continue
            out[owner[0]][(owner[1], seed)] = row
    return out, unknown


def point_stats(
    rows: dict[str, dict[tuple[int, int], dict[str, Any]]],
    ep: Any,
    arm: str,
    its: tuple[int, ...] = ITS,
) -> list[tuple[int, float, float, float]]:
    """一条判据线在每个判据点的 `(it, Δ均值原生, SE_Δ 原生, 对照臂水平)`（共用面在 paired_power）。"""
    out: list[tuple[int, float, float, float]] = []
    for it in its:
        st = PP.endpoint_pair_stats(rows, ep, it, arm, control=CONTROL)
        if st is not None:
            out.append((it, st[0], st[2], st[3]))
    return out


def daily_series(path: Path, key: str = "win") -> dict[int, float]:
    """某臂日常段账本 → `{iter: 逐局均值的均值}`（缺省 `win` ⇒ winRate；`key="ra2"` ⇒ 主终点）。"""
    per: dict[int, list[float]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event") != "eval":
                continue
            it = row.get("iter")
            if not isinstance(it, int) or not isinstance(row.get("ticks"), (int, float)):
                continue
            if key == "ra2":
                if not isinstance(row.get("onLaneTicks"), (int, float)):
                    continue
                per.setdefault(it, []).append(PP.ra2(row))
            else:
                v = row.get(key)
                if isinstance(v, (int, float)):
                    per.setdefault(it, []).append(float(v))
    return {it: statistics.fmean(v) for it, v in per.items()}


def max_negative_streak(series: dict[int, float], margin: float) -> int:
    """按 iter 升序数「连续 Δ < −margin」的最长段（burn 规则的 points 口径；等号不触发）。"""
    best = run = 0
    for it in sorted(series):
        run = run + 1 if series[it] < -margin else 0
        best = max(best, run)
    return best


# 目标线（不是守卫线）：它们的 margin 是**绿线**，判语必须是「达标 / 未达标」——
# 写成「未破」会被读成过关（b2 的 +2.9% 其实是零效应）。
TARGET_LINES = ("主终点 rA2", "A1 终点 rA1")


def line_verdict(
    marg: tuple[str, float], mean_n: float, se_n: float, level: float, target: bool = False
) -> str:
    """一条线的合规判语（原生单位 + 95% CI ⇒ 分辨「已证实」与「只是没证据」）。

    `target=True`（绿线）时 rel_down 读作「达标 / 未达标」（与 §69 ④ 的预注册语言一致）；
    缺省 = 守卫线，读作「未破 / 破线」。
    """
    kind, m = marg
    lo_n = mean_n - PP.Z_TWO_SIDED_95 * se_n
    hi_n = mean_n + PP.Z_TWO_SIDED_95 * se_n
    rel = mean_n / level * 100.0 if level else 0.0
    lo_r = lo_n / level * 100.0 if level else 0.0
    hi_r = hi_n / level * 100.0 if level else 0.0
    if target and kind == "rel_down":
        if hi_r < -m:
            return f"达标且已证实（{rel:+.1f}%）"
        if rel <= -m:
            return f"达标未证实（{rel:+.1f}%）"
        return f"未达标且已证实（{rel:+.1f}%）" if lo_r > -m else f"未达标（未证实）（{rel:+.1f}%）"
    if kind == "rel_down":
        confirmed = hi_r < -m
        broke = rel <= -m
        return _wording(confirmed, broke, f"{rel:+.1f}%")
    if kind == "rel_up":
        confirmed = lo_r > m
        broke = rel >= m
        return _wording(confirmed, broke, f"{rel:+.1f}%")
    if kind == "abs_up":
        confirmed = lo_n > m
        broke = mean_n >= m
        return _wording(confirmed, broke, f"{mean_n:+.4f}")
    confirmed = hi_n < -m
    broke = mean_n <= -m
    return _wording(confirmed, broke, f"{mean_n:+.4f}")


def _wording(confirmed: bool, broke: bool, shown: str) -> str:
    if confirmed:
        return f"破线且已证实（{shown}）"
    return f"破线未证实（{shown}）" if broke else f"未破（{shown}）"


def main() -> int:
    print("=" * 104)
    print(
        "Wave 2 判据段（预注册 → docs/nn/experiments.md §69；"
        f"b0 零奖励 / b1 f={F_PCT['b1']}% / b2 f={F_PCT['b2']}%）"
    )
    print("=" * 104)
    missing = [p for p in LEDGERS.values() if not p.exists()]
    if missing:
        print(f"  判据段账本不在（缺 {len(missing)}/{len(ITS)} 份）⇒ 跳过（非失败）。")
        print(f"  预期位置：{LEDGERS[ITS[0]]}")
        return 0

    healthy = True
    wmap, problems = key_map()
    for pr in problems:
        print(f"  ✗ 权重定位：{pr} ⇒ 判据段读数不可信")
        healthy = False
    rows: dict[str, dict[tuple[int, int], dict[str, Any]]] = {a: {} for a in ARMS}
    unknown = 0
    for it in ITS:
        part, n_unknown = load_rows(LEDGERS[it], wmap)
        unknown += n_unknown
        for a in ARMS:
            rows[a].update(part[a])
    if unknown:
        print(f"  ✗ 未知权重身份键的行 {unknown} 条 ⇒ 判据段读数不可信")
        healthy = False

    # 1. 体检：局数 / seed 集合互等（配对前提）
    print(f"  {'it':>4} {'局数 b0/b1/b2':>16} {'seed 区间':>18} {'配对前提':>10}")
    for it in ITS:
        per = {a: sorted(s for i, s in rows[a] if i == it) for a in ARMS}
        ns = [len(per[a]) for a in ARMS]
        same = per["b0"] == per["b1"] == per["b2"]
        if not same or ns[0] != GAMES_PER_ARM:
            healthy = False
        rng = f"{per['b0'][0]}–{per['b0'][-1]}" if per["b0"] else "—"
        print(f"  {it:>4} {ns[0]:>5}/{ns[1]:>4}/{ns[2]:>4}  {rng:>18}  {'是' if same else '**否**':>10}")
    print(
        f"  ⇒ 未知 wver 行 {unknown}（应 0）；seed 段 {SEED0}–{SEED0 + GAMES_PER_ARM - 1}"
        "，与日常段 860001–860200 / 补评估段 861011–861310 **均不相交**"
    )
    games = {a: {k: PP.ra2(r) for k, r in rows[a].items()} for a in ARMS}

    # 2. 臂水平 + 对照臂自漂（b0 无 it0 基线 ⇒ 以 it30 为参照点）
    print()
    print("  臂水平 rA2（%）与对照臂 b0 自漂（b0 无 it0 基线 ⇒ 以 it30 为参照点）：")
    base30 = PP.arm_level(games[CONTROL], ITS[0])
    print(f"  {'it':>4} " + "".join(f"{a + ' rA2%':>9} {a + ' 漂%':>9}" for a in ARMS))
    for it in ITS:
        txt = ""
        for a in ARMS:
            lvl = PP.arm_level(games[a], it)
            txt += f"{lvl * 100:>9.3f} {(lvl / base30 - 1) * 100:>+9.1f}"
        print(f"  {it:>4} {txt}")

    # 3. 逐点配对 Δrel（主终点）
    print()
    print(f"  逐点配对 Δrel（% of 同 it {CONTROL} 水平，主终点 rA2）：")
    print(f"  {'it':>4} " + "".join(f"{'Δrel ' + a:>10} {'SE%':>7} {'t':>7} {'p₁':>7}" for a in TREATED))
    pts_rel: dict[str, list[tuple[int, float, float]]] = {a: [] for a in TREATED}
    for it in ITS:
        txt = ""
        for a in TREATED:
            st = PP.endpoint_pair_stats(rows, PP.ENDPOINTS[0], it, a, control=CONTROL)
            if st is None or st[3] == 0:
                txt += f"{'—':>10} {'—':>7} {'—':>7} {'—':>7}"
                continue
            m_rel = st[0] / st[3] * 100.0
            se_rel = st[2] / st[3] * 100.0
            t = m_rel / se_rel if se_rel else 0.0
            pts_rel[a].append((it, m_rel, se_rel))
            txt += f"{m_rel:>10.2f} {se_rel:>7.2f} {t:>7.2f} {PP.norm_cdf(t):>7.3f}"
        print(f"  {it:>4} {txt}")

    # 4. 尾巴 3 点合并（预注册主判据）+ Q 前置
    print()
    print(f"  尾巴 {len(ITS)} 点合并（预注册主判据；前置 Q ≤ χ²(0.95, df={len(ITS) - 1}) = 5.991）：")
    print(
        f"  {'臂':>4} {'Δrel% 尾 3':>11} {'SE%':>7} {'t':>7} {'p₁':>7} {'Q':>7} "
        f"{'前置':>6} {'95% CI':>20} {'判语':<24}"
    )
    tail: dict[str, tuple[float, float, float, bool]] = {}
    for a in TREATED:
        vals = [p[1] for p in pts_rel[a]]
        ses = [p[2] for p in pts_rel[a]]
        if len(vals) < 2:
            print(f"  {a:>4} ** 有效判据点不足 **")
            healthy = False
            continue
        mean_p = statistics.fmean(vals)
        se_p = PP.se_of_pool(ses)
        t = mean_p / se_p if se_p else 0.0
        p1 = PP.norm_cdf(t)
        q, ivw, crit = PP.cochran_q(vals, ses)
        ok_q = bool(crit and q <= crit)
        lo, hi, below_green, positive = PP.claim_from_pooled(mean_p, se_p)
        passed = ok_q and mean_p <= -PP.GREEN_REL and p1 < 0.05
        if not ok_q:
            verdict = "尾部不稳（Q 超限）"
        elif passed:
            verdict = "**过绿线**"
        elif below_green:
            verdict = "不到绿线已证实"
        elif positive:
            verdict = "效应为正、未排除绿线"
        else:
            verdict = "没证据"
        tail[a] = (mean_p, se_p, p1, passed)
        print(
            f"  {a:>4} {mean_p:>11.2f} {se_p:>7.2f} {t:>7.2f} {p1:>7.3f} {q:>7.2f} "
            f"{'是' if ok_q else '**否**':>6} [{lo:>+7.2f}%,{hi:>+7.2f}%] {verdict:<24}"
        )
        print(
            f"       断言：不到绿线 {below_green}（需 CI 下沿 > −{PP.GREEN_REL:.0f}%）· "
            f"效应可靠为正 {positive}（需 CI 上沿 < 0）· 逆方差加权均值 {ivw:+.2f}%"
        )

    # 5. 剂量-响应形状（线性锚 = §67 实测 f = 3.6% 档 −8.52%）
    print()
    print(f"  剂量-响应（f = wLane 对应的 killEV 份额；锚 f = {ANCHOR_F_PCT}% = Wave 1 的 a1 档）：")
    print(
        f"  {'臂':>4} {'f%':>6} {'×3.6%':>7} {'Δrel% 尾3':>10} "
        f"{'线性预测%':>10} {'实测/预测':>10} {'到 16% 需 f%':>12}"
    )
    for a in TREATED:
        if a not in tail:
            continue
        mean_p = tail[a][0]
        f = F_PCT[a]
        pred = LINEAR_ANCHOR_REL * (f / ANCHOR_F_PCT)
        ratio = mean_p / pred if pred else 0.0
        f_req = f * PP.GREEN_REL / abs(mean_p) if mean_p else math.inf
        print(
            f"  {a:>4} {f:>6.2f} {f / ANCHOR_F_PCT:>7.2f} {mean_p:>10.2f} "
            f"{pred:>10.2f} {ratio:>10.2f} {f_req:>12.2f}"
        )
    print("  ⇒ 实测/预测 < 1 ⇒ 次线性（惩罚被策略适应吸收）；「到 16% 需 f%」是一阶外推，只作方向参考。")

    # 6. §11 逐条阈值（方向 = 该线不许越过的方向）
    print()
    print(
        "  §11 逐条阈值（尾巴 3 点合并；目标线 = 主终点 rA2 / A1 终点，读作达标 / 未达标；"
        "其余读作未破 / 破线）："
    )
    print(
        f"  {'判据线':<16} {'b0 水平':>11} {'b1 Δ':>10} {'b1 判定':>16} "
        f"{'b2 Δ':>10} {'b2 判定':>16} {'阈值':<22}"
    )
    for ep in PP.ENDPOINTS:
        marg = MARGINS.get(ep.label)
        is_target = ep.label in TARGET_LINES
        shown: list[str] = []
        levels: list[float] = []
        for a in TREATED:
            pts = point_stats(rows, ep, a)
            if not pts:
                shown += ["—", "—"]
                continue
            mean_n = statistics.fmean([p[1] for p in pts])
            se_n = PP.se_of_pool([p[2] for p in pts])
            level = statistics.fmean([p[3] for p in pts])
            levels.append(level)
            shown.append(f"{mean_n / level * 100 if level else 0:+.2f}%")
            shown.append(
                line_verdict(marg, mean_n, se_n, level, target=is_target)
                if marg
                else "信息（不设阈值）"
            )
        if marg and is_target:
            marg_s = f"绿线 {marg[1]:g}"
        elif marg:
            marg_s = f"{marg[0]} {marg[1]:g}"
        else:
            marg_s = "只报频次" if ep.label == "timeout" else "信息性（不设阈值）"
        print(
            f"  {ep.label:<16} {statistics.fmean(levels) if levels else 0:>11.4g} {shown[0]:>10} "
            f"{shown[1]:>16} {shown[2]:>10} {shown[3]:>16} {marg_s:<22}"
        )
    to_cnt = {a: sum(1 for _k, r in rows[a].items() if r.get("outcome") == "max_ticks") for a in ARMS}
    total_rows = sum(len(rows[a]) for a in ARMS)
    print(
        f"  timeout 频次：b0 {to_cnt['b0']} / b1 {to_cnt['b1']} / b2 {to_cnt['b2']}"
        f"（共 {sum(to_cnt.values())} 局 / {total_rows}）"
    )

    # 7. 止损诊断（训练已收官 ⇒ kickstart_burn 只能诊断：事件 + 事后配对差）
    print()
    print(f"  止损诊断（`kickstart_burn`：METRIC=winRate / margin {BURN_MARGIN_PP}pp / points 3）：")
    print(
        f"  {'臂':>4} {'事件数':>7} {'峰值 streak':>12} {'模式':>9} "
        f"{'vs b0 配对 Δ pp 尾 3':>22} {'配对峰值 streak':>16}"
    )
    win_series: dict[str, dict[int, float]] = {}
    for a in ARMS:
        if DAILY_LEDGER[a].exists():
            win_series[a] = daily_series(DAILY_LEDGER[a])
    for a in ARMS:
        log = ROOT / "tmp" / f"h4-lane-{a}" / "training_log.jsonl"
        n_ev = 0
        peak = 0
        modes: set[str] = set()
        if log.exists():
            with log.open(encoding="utf-8") as fh:
                for line in fh:
                    if "kickstart_burn" not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(ev, dict) or ev.get("event") != "kickstart_burn":
                        continue
                    n_ev += 1
                    peak = max(peak, int(ev.get("streak") or 0))
                    modes.add(str(ev.get("mode")))
        tail_txt = "—"
        streak_txt = "—"
        if a != CONTROL and a in win_series and CONTROL in win_series:
            shared = sorted(set(win_series[a]) & set(win_series[CONTROL]))
            deltas = {it: win_series[a][it] - win_series[CONTROL][it] for it in shared}
            tail_txt = ", ".join(f"{deltas[it] * 100:+.1f}" for it in shared[-3:])
            streak_txt = str(max_negative_streak(deltas, BURN_MARGIN_PP / 100.0))
        print(
            f"  {a:>4} {n_ev:>7} {peak:>12} {','.join(sorted(modes)) or '—':>9} "
            f"{tail_txt:>22} {streak_txt:>16}"
        )
    print(
        "  ⇒ 三臂共享 `paired_rotate_seed` ⇒ 对端不唯一，生产侧回退 `baseline`（事件的 mode 可见）⇒"
        " 本批止损是旧口径警告、未触发 ABORT；右两列是**事后**按新口径（配对差）补算的诊断。"
    )

    # 8. 独立复现：日常段（另一套 seed，与判据段不相交；200 局/点）
    print()
    print(
        "  独立复现（日常段 rA2：seed 860001–860200，与判据段 862001–862300 **不相交**；"
        "每 5 轮一点、200 局/点）："
    )
    daily_ra2: dict[str, dict[int, float]] = {}
    for a in ARMS:
        if DAILY_LEDGER[a].exists():
            daily_ra2[a] = daily_series(DAILY_LEDGER[a], key="ra2")
    if all(a in daily_ra2 for a in ARMS):
        its_d = sorted(set(daily_ra2["b0"]) & set(daily_ra2["b1"]) & set(daily_ra2["b2"]))
        print(f"  {'it':>4} " + "".join(f"{a + ' rA2%':>10}" for a in ARMS) + f"{'Δrel b1':>10}{'Δrel b2':>10}")
        tail_d: dict[str, list[float]] = {a: [] for a in TREATED}
        for it in its_d:
            txt = f"  {it:>4} " + "".join(f"{daily_ra2[a][it] * 100:>10.3f}" for a in ARMS)
            for a in TREATED:
                dr = (daily_ra2[a][it] - daily_ra2["b0"][it]) / daily_ra2["b0"][it] * 100.0
                txt += f"{dr:>10.2f}"
                if it in ITS:
                    tail_d[a].append(dr)
            print(txt)
        for a in TREATED:
            if not tail_d[a] or a not in tail:
                continue
            m_d = statistics.fmean(tail_d[a])
            m_j = tail[a][0]
            print(
                f"  ⇒ 日常段尾巴 3 点均值：{a} = {m_d:+.2f}%"
                f"（判据段 {m_j:+.2f}% ⇒ {'同向' if m_d * m_j >= 0 else '**反号**'}）"
            )
    else:
        print("  日常段账本缺 ⇒ 本小节跳过")

    print()
    print(f"  体检：{'通过' if healthy else '**未通过（上面有 ✗）**'}")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
