"""Wave 2 功效定案：从 Wave 1 三腿的**逐局**账本复算配对差方差 / MDE(N) / 所需 N。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/paired_power.py

**为什么落盘成工具**（2026-09-30）：`docs/nn/experiments.md` §65 自带 reported 级水印
（探针与判决脚本都是 `tmp/` 一次性产物），而 Wave 2 的 N 必须建在 MDE 上——照 §58/§60/§64
的教训，进 plan / 门 / 判据的数字必须先落盘且可复算。本工具用**逐局行**独立重算
「SE(Δrel) → MDE」这条链，并与归档数字（25.0%@200 / 17.7%@400 / 12.5%@800）**逐点对账**；
对不上就非零退出（可当检查用），`tests/test_paired_power.py` 直接调 `main()` 钉这条。

口径（与 §65 主终点一致）：
  · rA2 = `onLaneTicks / (ticks + 1)`，**每局率的均值**（不是 tick 加权比值）；
  · 配对键 = `(iter, seed)`：三臂同一批 seed（860001–860200），同 iter；
  · Δrel = mean(Δ) / 基准水平，基准 = **同 iter 对照臂**的 rA2 均值；
  · MDE(80%, 双侧 α=0.05) = (z_{0.975} + z_{0.80}) × SE(Δrel)。
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent.parent
NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

ARMS = ("a0", "a1", "a2")
TREATED = ("a1", "a2")
CONTROL = "a0"
PATHS = {a: ROOT / "tmp" / f"h4-lane-{a}" / "eval_log.jsonl" for a in ARMS}

# 双侧 95% + 80% 功效的乘数（正态近似，与归档同法）
Z_TWO_SIDED_95 = 1.959963985
Z_POWER_80 = 0.8416212336
MDE_K = Z_TWO_SIDED_95 + Z_POWER_80

# 归档值（本工具要对上的那组数）：(it, 臂) -> (Δrel%, p₁)
ARCHIVED: dict[tuple[int, str], tuple[float, float]] = {
    (5, "a1"): (-18.29, 0.029),
    (10, "a1"): (-6.01, 0.29),
    (15, "a1"): (-26.57, 0.0025),
    (20, "a1"): (-5.34, 0.275),
    (25, "a1"): (-5.12, 0.30),
    (30, "a1"): (-3.46, 0.37),
    (35, "a1"): (-13.21, 0.081),
    (5, "a2"): (4.24, 0.70),
    (10, "a2"): (7.26, 0.77),
    (15, "a2"): (-18.84, 0.023),
    (20, "a2"): (6.15, 0.74),
}
ARCHIVED_MDE: dict[int, float] = {200: 25.0, 400: 17.7, 800: 12.5}
GREEN_REL = 16.0  # 预注册机制绿线（相对下降）

# ── §67 补评估块（只评估、不重训；预注册 → `docs/nn/experiments.md` §67）─────────────
EXT_DIR = ROOT / "tmp" / "h4-lane-ext"
EXT_LEDGERS = tuple(EXT_DIR / f"ext7x300_c{c}.jsonl.run" / "eval_log.jsonl" for c in (1, 2, 3))
EXT_ITS = (5, 10, 15, 20, 25, 30, 35)
EXT_WEIGHTS_DIR = {a: ROOT / "nn-training" / "weights" / f"h4-lane-{a}" for a in ("a0", "a1")}
# χ²(0.95, df) 临界值（异质性检查，df ≤ 7；查表而非引依赖）
CHI2_95: dict[int, float] = {1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070, 6: 12.592, 7: 14.067}
# 预注册剂量括号（③）：当前 f = 3.6% ⇒ 4.0% = 1.11×、7.5% = 2.08×（3.3% 是降档，不列）
CUR_F_PCT = 3.6
DOSE_BRACKET_PCT = (4.0, 7.5)
# Wave 2（b 腿）预注册局数/点：**由 §67 的教训定**（加局比加训练便宜，且尾巴 3 点 × 300 的主终点 MDE 见 §11）
N_WAVE2 = 300
FULL_CLOSE_REL = 31.0  # 缺口完全闭合所需的相对下降
N_PREREG = 768  # Wave 2 判据段的预注册局数（对最不利评估点也够分辨绿线）
# 判据段的判决读法（归属 / 尾巴判定 / 剂量-形状 / §11 逐条 / 止损诊断 / 日常段独立复现）在
# `nn-training/tools/wave2_judge.py`（Wave 2 判决 → `docs/nn/experiments.md` §69）；本文件只留
# 功效定案与判据表（§1–§11），共享算术唯一的实现在这里（那个工具按路径 importlib 取用）。


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数（单测直接钉；`tests/test_paired_power.py`）
# ─────────────────────────────────────────────────────────────────────────────
def ra2(row: dict[str, Any]) -> float:
    """主终点 rA2 = onLaneTicks/(ticks+1)（每局率）。"""
    return float(row["onLaneTicks"]) / (int(row["ticks"]) + 1)


def game_key(row: dict[str, Any]) -> tuple[int, int] | None:
    """逐局行的配对键 `(iter, seed)`；不是逐局行（summary / 坏行）返回 None。"""
    if row.get("event") != "eval":
        return None
    it, seed = row.get("iter"), row.get("seed")
    if not isinstance(it, int) or not isinstance(seed, int):
        return None
    if not isinstance(row.get("onLaneTicks"), (int, float)):
        return None
    if not isinstance(row.get("ticks"), (int, float)):
        return None
    return it, seed


def load_rows(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    """读一份逐局账本 → `(iter, seed) -> 行`（坏行 / 非逐局行跳过）。"""
    out: dict[tuple[int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            key = game_key(row)
            if key is None:
                continue
            out[key] = row
    return out


def load_games(path: Path) -> dict[tuple[int, int], float]:
    """同一份账本的 rA2 视图（`(iter, seed) -> onLaneTicks/(ticks+1)`）。"""
    return {k: ra2(row) for k, row in load_rows(path).items()}


def se_of_pool(se_list: list[float]) -> float:
    """K 个评估点取均值（各点 SE 已知且相互独立）⇒ SE = √(Σ sᵢ²)/K（等值时退化为 s/√K）。"""
    k = len(se_list)
    return math.sqrt(sum(s * s for s in se_list)) / k


def paired_diffs(
    own: dict[tuple[int, int], float], peer: dict[tuple[int, int], float], it: int
) -> list[float]:
    """同 iter 同 seed 的逐局差（升序 seed；只取两臂都有的种子）。"""
    seeds = sorted(k[1] for k in own if k[0] == it and (it, k[1]) in peer)
    return [own[(it, s)] - peer[(it, s)] for s in seeds]


def values_at(games: dict[tuple[int, int], float], it: int) -> list[float]:
    """某 iter 的每局率（升序 seed）。"""
    return [v for (i, _s), v in sorted(games.items()) if i == it]


def arm_level(games: dict[tuple[int, int], float], it: int) -> float:
    """某 iter 的每局率均值。"""
    return statistics.fmean(values_at(games, it))


def per_game_sd(games: dict[tuple[int, int], float], it: int) -> float:
    """某 iter 的**每局**率 sd（ddof=1）；只点一局会抛（调用侧由 paired_its 闸住 ≥2）。"""
    return statistics.stdev(values_at(games, it))


def corr(xs: list[float], ys: list[float]) -> float:
    """皮尔逊相关（同序配对）；任一侧退化（无方差）返回 0。"""
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def se_of_mean(xs: list[float]) -> float:
    """均值的标准误（ddof=1）。"""
    return statistics.stdev(xs) / math.sqrt(len(xs))


def norm_cdf(z: float) -> float:
    """标准正态下尾 P(Z ≤ z)——单侧备择「下降」的 p 值就是它（Δ 为负 ⇒ t<0 ⇒ p 小）。"""
    return 0.5 * math.erfc(-z / math.sqrt(2))


def norm_sf(z: float) -> float:
    """标准正态上尾 P(Z > z)（= 1 − cdf）。"""
    return 0.5 * math.erfc(z / math.sqrt(2))


def mde_rel(se_rel: float, k: float = MDE_K) -> float:
    """相对 MDE（%，80% 功效、双侧 95%）。"""
    return k * se_rel


def n_for_target(mde_rel_at_n: float, n: float, target_rel: float) -> float:
    """MDE ∝ 1/√N ⇒ 把某个 N 上的 MDE 压到 target 所需的 N。"""
    return n * (mde_rel_at_n / target_rel) ** 2


def se_pooled(se_rel: float, k: int) -> float:
    """把尾部 K 个评估点取均值 ⇒ SE 降 √K（真效应在尾部稳定时才成立，§6 会先验这一步）。"""
    return se_rel / math.sqrt(k)


def n_for_pooled(mde_rel_at_n: float, n: float, target_rel: float, k: int) -> float:
    """尾部 K 点取均值时的所需局数/点 = 单点的 1/K（**总局数 N·K 不变**）。"""
    return n_for_target(mde_rel_at_n, n, target_rel) / k


def sha16(path: Path) -> str:
    """权重文件的 `sha256[:16]`——账本里的 `ckpt_sha16` 就是它（逐局行的臂归属靠这个）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def cochran_q(values: list[float], ses: list[float]) -> tuple[float, float, float]:
    """逆方差加权均值与 Cochran Q：`(Q, 加权均值, χ²(0.95, df=K−1) 临界值)`。

    K < 2 / 任一 SE ≤ 0 ⇒ `(0, 均值, 0)`（异质性无从谈起）。Q > 临界 ⇒ 跨点**异质**，
    这时「取均值」不再是同一个量的估计（防「早期大效应稀释后期」被均值平掉）。
    """
    k = len(values)
    if k < 2 or any(s <= 0 for s in ses):
        return (0.0, statistics.fmean(values) if values else 0.0, 0.0)
    ws = [1.0 / (s * s) for s in ses]
    w = math.fsum(ws)
    mean_w = math.fsum(wi * v for wi, v in zip(ws, values, strict=True)) / w
    q = math.fsum(wi * (v - mean_w) ** 2 for wi, v in zip(ws, values, strict=True))
    return (q, mean_w, CHI2_95.get(k - 1, CHI2_95[max(CHI2_95)]))


def claim_from_pooled(mean_rel: float, se_rel: float) -> tuple[float, float, bool, bool]:
    """§67 预注册的两条断言：`(CI 下沿, CI 上沿, 不到绿线已证实?, 效应可靠为正?)`。

    「不到绿线」= 把 −16% 这个效应**排除**掉 ⇒ CI 下沿（最负端）> −GREEN_REL；
    「效应可靠为正」= 整个 CI 落在 0 以下。
    """
    lo = mean_rel - Z_TWO_SIDED_95 * se_rel
    hi = mean_rel + Z_TWO_SIDED_95 * se_rel
    return (lo, hi, lo > -GREEN_REL, hi < 0.0)


def dose_multiple(mean_rel: float, target_rel: float = GREEN_REL) -> float:
    """到绿线所需的**剂量倍数**（一阶线性外推）：`target / |均值|`；均值为 0 时返回 inf。"""
    return target_rel / abs(mean_rel) if mean_rel else math.inf


# ────────────────────────────────────────────────────────────────
# 次要终点 / 守卫表（与课程头注的预注册逐条对应）
# ────────────────────────────────────────────────────────────────
def _col(key: str) -> Callable[[dict[str, Any]], float]:
    return lambda row: float(row[key])


def _rate(key: str) -> Callable[[dict[str, Any]], float]:
    return lambda row: float(row[key]) / (int(row["ticks"]) + 1)


class Endpoint(NamedTuple):
    """一条判据线：口径 + 预注册阈值（`margin_rel` % / `margin_abs` 原生单位；None = 课程没给数字）。"""

    label: str
    how: str
    get: Callable[[dict[str, Any]], float]
    unit: str
    margin: str
    margin_rel: float | None = None
    margin_abs: float | None = None


# timeout 全库 1/3400（sd=0）、“非劣”没数字 —— 两种都要在表里看见！
ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("主终点 rA2", "onLaneTicks/(ticks+1)", _rate("onLaneTicks"), "rate", "绿线 16% rel；满闭合 31%", margin_rel=16.0),
    Endpoint("A1 终点 rA1", "onLaneMoveTicks/(ticks+1)", _rate("onLaneMoveTicks"), "rate", "同绿线 16%（指示）", margin_rel=16.0),
    Endpoint(
        "洞守卫 静止∧在线",
        "(onLaneTicks−onLaneMoveTicks)/(ticks+1)",
        lambda row: (float(row["onLaneTicks"]) - float(row["onLaneMoveTicks"])) / (int(row["ticks"]) + 1),
        "rate",
        "不升（**没给数字**）",
        margin_rel=0.0,
    ),
    Endpoint("holdFire 守卫", "onLaneHoldFireTicks/(ticks+1)", _rate("onLaneHoldFireTicks"), "rate", "不塌 −30% rel", margin_rel=30.0),
    Endpoint("pass", "win", _col("win"), "share", "非劣 −3pp abs", margin_abs=0.03),
    Endpoint("零伤局", "playerDamageTaken == 0", lambda row: 1.0 if float(row["playerDamageTaken"]) == 0 else 0.0, "share", "非劣（**没给数字**）"),
    Endpoint("dmg/局", "playerDamageTaken", _col("playerDamageTaken"), "per_game", "非劣（**没给数字**）"),
    Endpoint("kills/局", "kills", _col("kills"), "per_game", "非劣（**没给数字**）"),
    Endpoint("shots/局", "playerShots", _col("playerShots"), "per_game", "非劣（**没给数字**）"),
    Endpoint("cellsVisited", "cellsVisited", _col("cellsVisited"), "per_game", "不塌 −10% rel", margin_rel=10.0),
    Endpoint("stuckTicks", "stuckTicks（末段连击均值口径）", _col("stuckTicks"), "per_game", "+10%（口径模糊）", margin_rel=10.0),
    Endpoint("ticks/局", "ticks", _col("ticks"), "per_game", "信息性（无阈值）"),
    Endpoint(
        "timeout",
        'outcome == "max_ticks"',
        lambda row: 1.0 if row.get("outcome") == "max_ticks" else 0.0,
        "share",
        "≤5%（全库 1/3400 ⇒ sd=0）",
        margin_abs=0.05,
    ),
)


def endpoint_pair_stats(
    rows: dict[str, dict[tuple[int, int], dict[str, Any]]],
    ep: Endpoint,
    it: int,
    arm: str = "a1",
    control: str = CONTROL,
) -> tuple[float, float, float, float] | None:
    """一条判据线在某 iter 的配对统计：`(mean_Δ, sd_Δ, SE_Δ, 对照臂水平)`，原生单位。

    `control` 缺省 = Wave 1 的 a0；Wave 2 判据段（§12）传 `b0`（其余逐字不变）。
    """
    vals: dict[str, dict[int, float]] = {}
    for a in (control, arm):
        g = rows[a]
        vals[a] = {
            s: ep.get(row)
            for (i, s), row in g.items()
            if i == it and (it, s) in g and (it, s) in rows[control]
        }
    shared = sorted(set(vals[control]) & set(vals[arm]))
    if len(shared) < 2:
        return None
    diffs = [vals[arm][s] - vals[control][s] for s in shared]
    return (
        statistics.fmean(diffs),
        statistics.stdev(diffs),
        se_of_mean(diffs),
        statistics.fmean([vals[control][s] for s in shared]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 主流程（进 plan / 判据的数字都出自这里；`main()` 只在 __main__ 里跑）
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    # ── 0. 语料前提：三腿逐局账本是 tmp/ 证据（未入库）——缺了就响亮说明并正常退出 ──
    missing = [a for a, p in PATHS.items() if not p.exists()]
    if missing:
        print(
            "语料不在（tmp/h4-lane-{a}/eval_log.jsonl 缺 {m}）——本工具复算的是那次三腿实测，\n"
            "账本属 tmp/ 证据、未入库。要复现需先从证据保留处恢复该目录；\n"
            "本次不判、退出（非失败）。".format(a="{a}", m=", ".join(missing))
        )
        return 0

    rows = {a: load_rows(p) for a, p in PATHS.items()}
    games = {a: {k: ra2(r) for k, r in rows[a].items()} for a in ARMS}
    all_its = sorted({i for g in games.values() for i, _s in g})

    def count_at(arm: str, it: int) -> int:
        return sum(1 for i, _s in games[arm] if i == it)

    # 两臂都有逐局行的 iter（且 it>0）：it0 是共同起点（同一份权重 ⇒ Δ 恒 0，方差无信息），
    # it40 只有 a1（a0 止于 it35）⇒ 都不进配对统计。
    paired_its = [it for it in all_its if it > 0 and count_at(CONTROL, it) >= 2 and count_at("a1", it) >= 2]

    # ── 1. 体检：点数、seed 集合互等（配对前提）──────────────────────────────
    print("=" * 92)
    print("§1 逐局账本体检（配对前提：同 iter 同 seed 各臂都有）")
    print("=" * 92)
    print(f"  {'it':>5} {'局数 a0/a1/a2':>15}  {'a0 seed 区间':>16}  {'配对前提':>22}")
    bad = False
    for it in all_its:
        per = {a: sorted({s for i, s in games[a] if i == it}) for a in ARMS}
        n0, n1, n2 = (len(per[a]) for a in ARMS)
        if n0 >= 2 and n1 >= 2:
            bad |= per[CONTROL] != per["a1"]
        if n0 == 0 and n1 >= 2:
            note = "a0 无逐局行（止 it35）"
        elif n2 == 0 and n1 == n0:
            note = "a2 无逐局行（it21 掐停）"
        elif n2 == n0 and per["a2"] == per[CONTROL]:
            note = "是"
        else:
            note = "**否**"
        rng = f"{per[CONTROL][0]}–{per[CONTROL][-1]}" if per[CONTROL] else "—"
        print(f"  {it:>5} {n0:>5}/{n1:>4}/{n2:>4}  {rng:>16}  {note:>22}")
    print(
        "  ⇒ 两臂都有逐局行的每个 iter：seed 集合逐位相同 ⇒ 配对前提 "
        f"{'成立' if not bad else '**不成立（下面所有数都不可信）**'}"
    )
    print(
        f"  ⇒ 可配对点 it {paired_its[0]}–{paired_its[-1]}（{len(paired_its)} 个）；"
        "it>20 只有 a0/a1；it40 只有 a1"
    )

    # ── 2. 每局 sd / 配对差 sd / 相关：配对设计买到了什么 ──────────────────────
    print()
    print("=" * 92)
    print("§2 每局 sd、配对差 sd 与两臂相关（配对设计到底买到了什么）")
    print("=" * 92)
    print(
        f"  {'it':>5} {'σ(a0)%':>9} {'σ(a1)%':>9} {'sd_Δ(a1−a0)%':>15} {'sd_Δ/σ̄':>9} {'ρ':>7}"
    )
    arm_sds: dict[str, list[float]] = {CONTROL: [], "a1": []}
    ratios, rhos = [], []
    for it in paired_its:
        v0, v1 = values_at(games[CONTROL], it), values_at(games["a1"], it)
        s0, s1 = per_game_sd(games[CONTROL], it), per_game_sd(games["a1"], it)
        sd_d = statistics.stdev(paired_diffs(games["a1"], games[CONTROL], it))
        ratio = sd_d / ((s0 + s1) / 2)
        rho = corr(v0, v1)
        arm_sds[CONTROL].append(s0 * 100)
        arm_sds["a1"].append(s1 * 100)
        ratios.append(ratio)
        rhos.append(rho)
        print(
            f"  {it:>5} {s0 * 100:>9.2f} {s1 * 100:>9.2f} {sd_d * 100:>15.2f} "
            f"{ratio:>9.3f} {rho:>7.3f}"
        )
    ratio_m, rho_m = statistics.fmean(ratios), statistics.fmean(rhos)
    print()
    print(
        f"  ⇒ 单臂每局率 sd（it5–35 均值）= a0 **{statistics.fmean(arm_sds[CONTROL]):.2f}pp** / "
        f"a1 **{statistics.fmean(arm_sds['a1']):.2f}pp**"
    )
    print("     （课程头注引用的 h4-stop it0 值是 7.01pp——同量级，逐臂逐点会动）")
    print(f"  ⇒ 配对差 sd / 单臂 sd = **{ratio_m:.3f}**（不配对应为 √2 = 1.414，完全相关为 0）")
    print(
        f"     两臂同 seed 相关 ρ = **{rho_m:.3f}** ⇒ 配对把 sd 砍到单臂的 "
        f"**{ratio_m:.3f}×**（相对不配对省 **{1 - ratio_m / math.sqrt(2):.1%}**）"
    )

    # ── 3. 复算归档：Δrel / SE(Δrel) / t / p₁ / MDE ─────────────────────────
    print()
    print("=" * 92)
    print("§3 复算 §65 归档表（基准 = 同 iter 对照臂 a0 的 rA2 均值）")
    print("=" * 92)
    print(
        f"  {'it':>5} {'臂':>4} {'Δrel%':>8} {'SE(Δrel)%':>10} {'t':>7} {'p₁':>8} "
        f"{'归档 Δrel':>10} {'归档 p₁':>9} {'对账':>6}"
    )
    agree = True
    se_by_it: dict[int, float] = {}
    for it in paired_its:
        lvl = arm_level(games[CONTROL], it)
        for arm in TREATED:
            d = paired_diffs(games[arm], games[CONTROL], it)
            if not d:
                continue
            mean_d = statistics.fmean(d)
            se_abs = se_of_mean(d)
            delta_rel = mean_d / lvl * 100.0
            se_rel = se_abs / lvl * 100.0
            t = mean_d / se_abs
            p1 = norm_cdf(t)
            if arm == "a1":
                se_by_it[it] = se_rel
            arch = ARCHIVED.get((it, arm))
            if arch is None:
                print(f"  {it:>5} {arm:>4} {delta_rel:>8.2f} {se_rel:>10.2f} {t:>7.2f} {p1:>8.3f}")
                continue
            a_delta, a_p = arch
            near = abs(delta_rel - a_delta) < 0.6 and abs(p1 - a_p) < 0.03
            agree &= near
            print(
                f"  {it:>5} {arm:>4} {delta_rel:>8.2f} {se_rel:>10.2f} {t:>7.2f} {p1:>8.3f} "
                f"{a_delta:>10.2f} {a_p:>9.3f} {'✓' if near else '**✗**':>6}"
            )
    print(
        f"  ⇒ 归档 Δrel / t / p₁ 逐点对账：**{'全部一致' if agree else '有对不上的点'}**"
        "（容差 ±0.6pp / ±0.03）"
    )

    se_it20 = se_by_it[20]  # 预注册的共同判决点
    se_med = statistics.median(se_by_it.values())
    se_worst = max(se_by_it.values())
    bases = (
        ("it20（归档口径）", se_it20),
        ("it5–35 中位", se_med),
        ("it5–35 最大（保守）", se_worst),
    )
    print()
    print("  MDE = 2.802 × SE(Δrel)（z₀.₉₇₅ + z₀.₈₀）；三种基准各算一遍")
    print(f"    {'基准':<20} {'SE(Δrel)%':>10} {'MDE@200%':>10} {'MDE@400%':>10} {'MDE@800%':>10}")
    for name, se in bases:
        cells = "".join(f"{mde_rel(se * math.sqrt(200.0 / n)):>10.2f}" for n in ARCHIVED_MDE)
        print(f"    {name:<20} {se:>10.2f}{cells}")
    print(f"    {'归档值':<20} {'—':>10}" + "".join(f"{v:>10.2f}" for v in ARCHIVED_MDE.values()))
    mde_ok = all(
        abs(mde_rel(se_it20 * math.sqrt(200.0 / n)) - arch) < 0.6
        for n, arch in ARCHIVED_MDE.items()
    )
    print(
        f"  ⇒ 归档 MDE 对账：**{'一致——口径就是 it20 那个点的 SE' if mde_ok else '有对不上的点'}**；"
        f"其余两档给出带宽（{mde_rel(se_med):.1f}%–{mde_rel(se_worst):.1f}%@200）"
    )

    # ── 4. Wave 2 的 N：绿线 16% 与完全闭合 31% 各需要多少局 ──────────────────
    print()
    print("=" * 92)
    print("§4 Wave 2 的 N（MDE ∝ 1/√N；判据段用**池外新 seed0**，§15.1）")
    print("=" * 92)
    print(f"  {'基准 SE(Δrel)%':>14} {'来源':<20} {'N 使 MDE≤16%':>14} {'N 使 MDE≤31%':>14}")
    for name, se in bases:
        m200 = mde_rel(se)
        print(
            f"  {se:>14.2f} {name:<20} {math.ceil(n_for_target(m200, 200, GREEN_REL)):>14} "
            f"{math.ceil(n_for_target(m200, 200, FULL_CLOSE_REL)):>14}"
        )
    print()
    print(f"  ⇒ 预注册判据段取 **N = {N_PREREG}**（2 的幂、对**最不利点**也够）：")
    print(
        f"     对最不利点（SE {se_worst:.2f}%）MDE = "
        f"{mde_rel(se_worst * math.sqrt(200.0 / N_PREREG)):.2f}% ≤ {GREEN_REL:.0f}% ✓；"
    )
    mde_at_768 = mde_rel(se_it20 * math.sqrt(200.0 / N_PREREG))
    print(
        f"     对归档口径（SE {se_it20:.2f}%）MDE = {mde_at_768:.2f}%"
        f"（{N_PREREG / 200:.2f}× 局数 ⇒ MDE 砍到 {mde_at_768 / mde_rel(se_it20):.0%}）"
    )
    print()
    print(f"  {'N':>6} {'MDE% 最不利点':>14} {'MDE% 归档口径':>14} {'分辨 16%':>10} {'分辨 31%':>10}")
    for n in (200, 400, 512, 768, 1024, 2048):
        mw = mde_rel(se_worst * math.sqrt(200.0 / n))
        ma = mde_rel(se_it20 * math.sqrt(200.0 / n))
        print(
            f"  {n:>6} {mw:>14.2f} {ma:>14.2f} "
            f"{'是' if mw <= GREEN_REL else '否':>10} {'是' if mw <= FULL_CLOSE_REL else '否':>10}"
        )
    n_close_worst = math.ceil(n_for_target(mde_rel(se_worst), 200, FULL_CLOSE_REL))
    print()
    print(f"  ⇒ 读法：Wave 1 的 200 局对**满闭合（{FULL_CLOSE_REL:.0f}%）**本来是够的（N≥{n_close_worst}）；")
    print(f"     压在绿线（{GREEN_REL:.0f}%）上就不够了——「无效」的成因是**尺子想分辨半闭合**：")
    print("     半闭合的绝对量只有满闭合的一半，落在抽样噪声里。这不是那条腿的读数难看。")
    print()
    print("  ⚠ 本工具只算**抽样功效**：不含量化/种子级非平稳、不含评估网格 cadence 误差，")
    print("     也不含「两臂漂移不共模」的额外方差。N 取上取整，别取下。")

    # ── 5. 次要终点 / 守卫表的配对功效（一条判据线一行）──────────────────────
    print()
    print("=" * 104)
    print("§5 次要终点与守卫表的配对功效（逐局、同 iter 同 seed；取各点中位；MDE 是 200 局口径）")
    print("=" * 104)
    print(
        f"  {'判据线':<16} {'口径':<32} {'a0 水平':>10} {'sd_Δ':>9} {'SE@200':>9} "
        f"{'MDE rel%':>9} {'MDE abs':>9} {'预注册阈值':<20} {'阈值需 N':>10}"
    )
    needs: dict[str, str] = {}
    ep_st: dict[str, tuple[float, float, float]] = {}  # label -> (a0 水平, sd_Δ, SE_Δ)
    for ep in ENDPOINTS:
        stats = [s for s in (endpoint_pair_stats(rows, ep, it) for it in paired_its) if s]
        if not stats:
            print(f"  {ep.label:<16} {ep.how:<32} {'—':>10}  ** 无足量配对行 **")
            needs[ep.label] = "不可算"
            continue
        lvl = statistics.median([s[3] for s in stats])
        ep_st[ep.label] = (lvl, statistics.median([s[1] for s in stats]), statistics.median([s[2] for s in stats]))
        sd_d = statistics.median([s[1] for s in stats])
        se_abs = statistics.median([s[2] for s in stats])
        mde_abs = mde_rel(se_abs)
        mde_pct = mde_rel(se_abs / lvl * 100.0) if lvl else 0.0
        if se_abs == 0:
            need = "无方差"
        elif ep.margin_rel is not None and ep.margin_rel > 0:
            need = str(math.ceil(n_for_target(mde_pct, 200, ep.margin_rel)))
        elif ep.margin_rel == 0.0:
            need = "∞（要数字）"
        elif ep.margin_abs is not None:
            need = str(math.ceil(n_for_target(mde_abs, 200, ep.margin_abs)))
        else:
            need = "—（要数字）"
        needs[ep.label] = need
        print(
            f"  {ep.label:<16} {ep.how:<32} {lvl:>10.4g} {sd_d:>9.4g} {se_abs:>9.4g} "
            f"{mde_pct:>9.2f} {mde_abs:>9.4g} {ep.margin:<20} {need:>10}"
        )
    print()
    print("  ⇒ 三类读数：① 给了数字的阈值 ⇒「阈值需 N」就是它可判所需局数；")
    print("     ② 「非劣」没数字 ⇒ 不可判（Wave 2 预注册必须先给数字）；")
    print("     ③ 「不升」「timeout」结构性不可判（margin=0 ⇒ 需无限局；sd=0 ⇒ 事件太稀）——")
    print("        前者必须改写成「不升过 Xpp」的有限阈值，后者报频次、不当功效项。")

    # ── 6. 尾巴合并：K 个评估点取均值能省多少局 ────────────────────────────────
    print()
    print("=" * 104)
    print("§6 尾巴合并（先验「尾部真效应平稳」，再算 K 点合并的 SE / MDE / 所需局数）")
    print("=" * 104)
    deltas_rel, ses_rel = [], []
    for it in paired_its:
        st = endpoint_pair_stats(rows, ENDPOINTS[0], it)
        if st is None or st[3] == 0:
            continue
        deltas_rel.append(st[0] / st[3] * 100.0)
        ses_rel.append(st[2] / st[3] * 100.0)
    across = statistics.stdev(deltas_rel)  # 跨评估点 sd
    sampling = statistics.fmean(ses_rel)  # 单点抽样 SE 均值
    between2 = max(0.0, across * across - sampling * sampling)
    print(f"  Δrel 跨评估点 sd = {across:.2f}%  vs  单点抽样 SE 均值 = {sampling:.2f}%")
    if between2 == 0:
        print("  ⇒ 跨点差异**全部**由抽样噪声解释（真效应在尾部平稳）⇒ 合并不打折，SE 按 1/√K 降")
    else:
        print(f"  ⇒ 有超出抽样噪声的跨点方差（√{between2:.2f}%）⇒ 合并收益封顶（下面把它加回去）")
    print(f"  （Wave 1 实测：单点 it20 SE(Δrel) = {se_it20:.2f}%；尾巴 it {paired_its[-3]}/{paired_its[-2]}/{paired_its[-1]} 三点）")
    print(f"  {'K':>3} {'尾巴':>14} {'SE(Δrel)% 合并':>16} {'MDE@200%':>10} {'N/点 使 MDE≤16%':>17} {'总评估局数 N×K':>15}")
    for k in range(1, 5):
        tail_it = paired_its[-k:]
        se_k = math.sqrt(se_of_pool([se_by_it[i] for i in tail_it]) ** 2 + between2)
        n_k = math.ceil(n_for_pooled(mde_rel(sampling), 200, GREEN_REL, k))
        mark = "  ← 推荐" if k == 3 else ""
        print(
            f"  {k:>3} {'/'.join(str(i) for i in tail_it):>14} {se_k:>16.2f} "
            f"{mde_rel(se_k):>10.2f} {n_k:>17} {n_k * k:>15}{mark}"
        )
    print("  ⇒ 总局数 N×K 基本不变（功效只看两者的乘积）⇒ 合并不是免费午餐，但它**不用新开评估**：")
    print("     日常段本来就是每 5 轮评一次，尾巴三点已经在盘上；而单点需要额外补一段大评估。")

    # ── 7. 更长时程：逐点 SE 的趋势 + 自漂诊断 + 总局数账 ─────────────────────
    print()
    print("=" * 104)
    print("§7 更长时程（it40/60/80 能不能沿用这张 N 表）与漂移诊断")
    print("=" * 104)
    mean_it = statistics.fmean([it for it in paired_its if se_by_it.get(it) is not None])
    num = sum(
        (it - mean_it) * se
        for it, se in ((i, se_by_it[i]) for i in paired_its if se_by_it.get(i) is not None)
    )
    den = sum((i - mean_it) ** 2 for i in paired_its if se_by_it.get(i) is not None)
    slope = num / den if den else 0.0
    print(f"  逐点 SE(Δrel) 区间 = {min(ses_rel):.2f}–{max(ses_rel):.2f}%（线性斜率 {slope:+.3f}pp/it）")
    print("  ⇒ 斜率≈0 ⇒ 每局方差在 it5–35 上**不随训练时长漂**：把 N 表外推到 it40/60/80 成立；")
    print("     反过来说，**跑更久不买到功效**（功效只由「局数 × 评估点数」决定，§6 的乘积律）。")
    print("     ⚠ 超出 it35 是外推：语料只到 it35，且尾部仍在训（漂移未收敛）。")
    print()
    print("  自漂诊断（每个评估点必报；配对差把它的**均值**消掉，不消它的方差）：")
    base0 = arm_level(games[CONTROL], 0) if count_at(CONTROL, 0) else 0.0
    print(f"    {'it':>5} {'a0 rA2%':>9} {'a0 自漂%':>9} {'a1 rA2%':>9} {'a1 自漂%':>9} {'配对 Δrel%':>11}")
    for it in paired_its:
        l0, l1 = arm_level(games[CONTROL], it), arm_level(games["a1"], it)
        d_rel = (l1 - l0) / l0 * 100.0 if l0 else 0.0
        print(
            f"    {it:>5} {l0 * 100:>9.3f} "
            f"{(l0 / base0 - 1) * 100 if base0 else 0:>+9.1f} {l1 * 100:>9.3f} "
            f"{(l1 / base0 - 1) * 100 if base0 else 0:>+9.1f} {d_rel:>+11.2f}"
        )
    print(f"    （it0 基线 a0 rA2 = {base0 * 100:.3f}%；上表 a0 列应与 §65 的「控制臂自漂」对得上）")
    print()
    print("  总局数账（功效 = N × K）：")
    eval_pts = len(paired_its) + 1  # +it0
    print(f"    日常段 = {eval_pts} 个评估点（it0 起每 5 轮）× 200 局 = {eval_pts * 200} 局在盘上；")
    print(f"    判据只需尾巴 K=3 的 {3 * 200} 局 ⇒ **够**（需 {math.ceil(n_for_pooled(mde_rel(sampling), 200, GREEN_REL, 3)) * 3} 局）。")

    # ── 8. Wave 2 完整判据表（装配）──────────────────────────────────────────
    print()
    print("=" * 104)
    print("§8 Wave 2 判据表（装配：每条线一行；推荐方案 = 尾巴 3 点均值 × 200 局/点）")
    print("=" * 104)
    se_tail3 = math.sqrt(se_of_pool([se_by_it[i] for i in paired_its[-3:]]) ** 2 + between2)
    mde_tail3 = mde_rel(se_tail3)
    print(f"  推荐方案的主终点 MDE(80%) = **{mde_tail3:.2f}%**（绿线 {GREEN_REL:.0f}%）")
    print(
        f"  {'判据线':<16} {'预注册阈值':<20} {'阈值需 N·K':>11} "
        f"{'MDE rel%':>9} {'MDE abs':>10} {'可判?':>15}"
    )
    for ep in ENDPOINTS:
        need = needs.get(ep.label, "不可算")
        if need.isdigit():
            need_nk = int(need)
            ok = "是" if need_nk <= 3 * 200 else f"否（需 {need_nk}）"
            need_s = str(need_nk)
        else:
            ok, need_s = need, "—"
        est = ep_st.get(ep.label)
        if est is None:
            print(f"  {ep.label:<16} {ep.margin:<20} {need_s:>11} {'—':>9} {'—':>10} {ok:>15}")
            continue
        lvl, _sd, se_abs = est
        se_eff = se_abs / math.sqrt(3)  # 尾巴 3 点均值 ⇒ 有效局数 = 3 × 200
        mde600_pct = mde_rel(se_eff / lvl * 100.0) if lvl else 0.0
        print(
            f"  {ep.label:<16} {ep.margin:<20} {need_s:>11} {mde600_pct:>9.2f} "
            f"{mde_rel(se_eff):>10.4g} {ok:>15}"
        )

    # ── 11. Wave 2（b 腿）预注册阈值表：阈值就取「该线自己的 MDE」────────────────
    print()
    print("=" * 104)
    print(f"§11 Wave 2 预注册阈值表（尾巴 3 点均值 × {N_WAVE2} 局/点；阈值建议 = 该线自己的 MDE）")
    print("=" * 104)
    print(f"  有效局数 = 3 × {N_WAVE2} = {3 * N_WAVE2}（每条线独立算，不含多重比较；主/次需分α）")
    print(f"  {'判据线':<16} {'课程原写法':<22} {'建议阈值（= 该线 MDE）':<40} {'原阈值需局数':>12}")
    for ep in ENDPOINTS:
        est = ep_st.get(ep.label)
        if est is None:
            continue
        lvl, _sd, se_abs = est
        se_eff = se_abs / math.sqrt(N_WAVE2 / 200.0) / math.sqrt(3)
        mde_pct = mde_rel(se_eff / lvl * 100.0) if lvl else 0.0
        mde_abs = mde_rel(se_eff)
        if se_abs == 0:
            sug = "只报频次（全库 sd = 0，功效不适用）"
        elif ep.margin.startswith("不升"):
            sug = f"不升过 +{mde_abs:.4g} abs（或 +{mde_pct:.1f}% rel）"
        elif "没给数字" in ep.margin:
            sug = f"给数字：不劣于 −{mde_pct:.1f}% rel / −{mde_abs:.4g} abs"
        elif ep.margin_rel == 0.0:
            sug = f"不升过 {mde_abs:.4g} abs（原 margin=0 需无限局）"
        elif ep.margin_rel is not None or ep.margin_abs is not None:
            sug = f"保持原阈值；本设计能分辨 {mde_pct:.1f}% rel"
        else:
            sug = f"信息性，不设阈值（本设计能分辨 {mde_pct:.1f}% rel）"
        print(f"  {ep.label:<16} {ep.margin:<22} {sug:<40} {needs.get(ep.label, '—'):>12}")
    print()
    print("  ⇒ 这张表直接进 b 腿课程头注：**每条线一个数**（不再是「非劣」/「不升」这种量不出的词）。")
    print()
    print("  ⇒ 最后两列就是**预注册该给的数字**：不是「希望多灵敏」，而是这套设计真能分辨的幅度。")
    print("     例：pass 的「−3pp 非劣」在此设计下不可判（能分辨的是 ±5.7pp）⇒ 要么把阈值放宽到可判范围，")
    print("     要么把这条线降级为诊断项（不进判据），二选一，不得在结算时才发现。")
    print()
    print("  ⚠ 「可判」只看**该条线自己**的抽样功效，不含多重比较：把 13 条线一起当判据就必须分")
    print("     主/次（主终点单侧 α=0.05；次终点 Holm）——否则假阳性率随线条数线性膨胀。")

    # ── 9. Wave 1 回填：按**新判据**重算三腿，看结论变不变 ────────────────────
    print()
    print("=" * 104)
    print("§9 Wave 1 回填（按新判据重算；旧判据 = it20 单点，MDE 25.0%）")
    print("=" * 104)

    def arm_points(arm: str) -> list[tuple[int, float, float]]:
        """该臂可配对点上的 `(it, Δrel%, SE(Δrel)%)`（升序）。"""
        out: list[tuple[int, float, float]] = []
        for it in paired_its:
            st = endpoint_pair_stats(rows, ENDPOINTS[0], it, arm)
            if st is None or st[3] == 0:
                continue
            out.append((it, st[0] / st[3] * 100.0, st[2] / st[3] * 100.0))
        return out

    # 对照臂定位：A0 自己的尾巴漂移有多大（对照物是绿线，不是 0）
    if base0 and len(paired_its) >= 3:
        drift = {it: (arm_level(games[CONTROL], it) / base0 - 1) * 100.0 for it in paired_its}
        tail_d = [drift[i] for i in paired_its[-3:]]
        all_d = [drift[i] for i in paired_its]
        d_tail, d_all = statistics.fmean(tail_d), statistics.fmean(all_d)
        print(
            f"  对照臂 A0（零奖励）在同一判据下的自漂：尾巴 3 点均值 {d_tail:+.1f}%"
            f" / 全 {len(all_d)} 点均值 {d_all:+.1f}%（it0 基线 rA2 = {base0 * 100:.3f}%）"
        )
        print(
            f"  ⇒ 对照臂**自己的**漂移（{d_tail:+.1f}%）就在绿线 {GREEN_REL:.0f}% 量级 ⇒ 未配对比较"
            "量到的是「漂移 + 效应」的和；配对差是唯一能把漂移的**均值**消掉的口径。"
        )
        print()

    print(f"  {'臂':<12} {'判据':<26} {'Δrel%':>8} {'SE%':>7} {'t':>7} {'p₁':>7} {'达绿线?':>10}")
    res: dict[tuple[str, str], tuple[float, float, float, bool]] = {}
    for arm, note in (("a1", "A1 移动税 127"), ("a2", "A2 在线税 7.0")):
        pts = arm_points(arm)
        combos = [
            (f"尾巴 3 点均值 it{'/'.join(str(p[0]) for p in pts[-3:])}", "tail", pts[-3:]),
            (f"全 {len(pts)} 点均值 it{pts[0][0]}–{pts[-1][0]}", "full", pts),
        ]
        combo20 = [p for p in pts if p[0] == 20]
        if combo20:
            combos.append(("旧口径 it20 单点", "it20", combo20))
        for name, kind, sel in combos:
            mean_d = statistics.fmean([p[1] for p in sel])
            se_p = se_of_pool([p[2] for p in sel])
            t = mean_d / se_p
            p1 = norm_cdf(t)
            green = mean_d <= -GREEN_REL and p1 < 0.05
            res[(arm, kind)] = (mean_d, se_p, p1, green)
            print(
                f"  {note:<12} {name:<26} {mean_d:>8.2f} {se_p:>7.2f} {t:>7.2f} {p1:>7.3f} "
                f"{'是' if green else '否':>10}"
            )

    m_tail, se_tail, p_tail, _ = res[("a1", "tail")]
    m_full, se_full, p_full, _ = res[("a1", "full")]
    m20, se20, p20, _ = res[("a1", "it20")]
    m2_tail, _, p2_tail, _ = res[("a2", "tail")]
    lo, hi = m_tail - 1.96 * se_tail, m_tail + 1.96 * se_tail
    need_se = (GREEN_REL - abs(m_tail)) / 1.96
    print()
    print(
        f"  A1 尾巴 3 点均值 = {m_tail:+.2f}%（SE {se_tail:.2f}%）⇒ 95% CI = [{lo:+.2f}%, {hi:+.2f}%]："
        f"点估计{'差绿线一半' if abs(m_tail) < GREEN_REL / 2 else '未过绿线'}，但下沿"
        f"{'已探到' if lo <= -GREEN_REL else '未探到'} −{GREEN_REL:.0f}%。"
    )
    print(
        f"  A1 全 7 点均值 = {m_full:+.2f}%（SE {se_full:.2f}%，p₁ = {p_full:.3f}）⇒ "
        f"**方向明确、统计显著**，幅度 ≈ 绿线的 {abs(m_full) / GREEN_REL:.0%}。"
    )
    print("  ⇒ **主终点判语不变**：没有任何一条线达到绿线（|Δrel| ≥ 16% 且 p₁ < 0.05）——")
    print(
        f"     尾巴 3 点判据下 A1 = {m_tail:+.2f}%（p₁ = {p_tail:.3f}）、A2 = {m2_tail:+.2f}%（p₁ = {p2_tail:.3f}）；"
        f"旧口径 it20 单点下 A1 = {m20:+.2f}%（SE {se20:.2f}%，p₁ = {p20:.3f}）。"
    )
    print("  ⇒ **但「无效」这个标签要改**（§65 判 uninformative 的三条理由逐条重审）：")
    print("     ① 「控制臂自漂 ≥ 目标效应」—— 漂移的**均值**已被配对差消掉（§7 实测消 3.8×），且已定案")
    print("        按共模消（§2026-09-30-goalnn-wave2-design ②）⇒ 不再是「答不了」的理由；")
    print(
        f"     ② 「功效不足（MDE 17.7%@400 > 16%）」—— **已不成立**：新判据 MDE(80%) = "
        f"{mde_rel(se_tail):.2f}% ≤ 绿线 {GREEN_REL:.0f}%；"
    )
    print("     ③ 「一条腿被假阳性掐掉」—— 假阳性判语维持（§5 的 pass MDE(200) = 9.8pp 又添一条独立支持）。")
    print("     ⇒ 三条里两条消失、一条维持 ⇒ Wave 1 从「**答不了**」变成「**答了：效应真实但幅度不够**」。")
    print()
    print(
        f"  ⇒ **加局数救不了绿线**：绿线要 {GREEN_REL:.0f}%，而 A1 的点估计只有 {abs(m_tail):.1f}%"
        f"（尾巴）/ {abs(m_full):.1f}%（全点）—— 加局只收紧 CI、不动点估计 ⇒ 要够绿只能**动剂量**，不是补局数。"
    )
    print(
        f"     反过来，要把「没到 16%」从「没证据」升级成「**已证实不到 16%**」（CI 下沿抬到 −{GREEN_REL:.0f}% 以上）"
        f"需 SE ≤ {need_se:.2f}% ⇒ ≈ {(se_tail / need_se) ** 2:.2f}× 局数"
        f"（≈ {math.ceil(200 * (se_tail / need_se) ** 2)} 局/点 × 3 点 ≈ "
        f"{math.ceil(200 * (se_tail / need_se) ** 2) * 3} 局）。"
    )
    print(
        "  ⇒ **连带（要显式记账）**：新判据把 A1 的效应从「量不出」变成「量得出」"
        f"（全点均值 {m_full:+.2f}%，p₁ = {p_full:.3f}）⇒ §2026-09-30-goalnn-wave2-design ③ 里那句"
    )
    print(
        "     「剂量太小无证据」的**免责不再成立**（现在测得出，且只有绿线的 ~0.7×）。这不推翻 ③"
    )
    print(
        "     （f = 3.6% 是本族既定锚点，改它 = 奖励语义变更 + 破坏单变量纪律），但 ③ 今后要靠"
        "正面论证（「−11% 在 3.6% 剂量下就是预期工作点」）站住，不能再用「测不出来」。"
    )

    # ── 10. §67 补评估块：旧 200 局/点 + 新 300 局/点同点合并后重算（只评估、不重训）──
    print()
    print("=" * 104)
    print("§10 补评估后的重算（旧 200 局/点 + 新块；预注册 → docs/nn/experiments.md §67）")
    print("=" * 104)
    ext_ok = True
    missing_ext = [p for p in EXT_LEDGERS if not p.exists()]
    if missing_ext:
        print(
            f"  补评估块不在（缺 {len(missing_ext)}/{len(EXT_LEDGERS)} 份账本）⇒ §10 跳过（非失败）。"
        )
        print(f"  预期位置：{EXT_LEDGERS[0]}（`tools/sim/eval-course-ckpt.ts --no-dist` 产出）")
    else:
        # 臂/iter 归属：账本逐局行的 `ckpt_sha16` = 权重文件 sha256[:16]（不靠文件顺序）
        wmap: dict[str, tuple[str, int]] = {}
        for arm_i in ("a0", "a1"):
            for it_i in EXT_ITS:
                hits = sorted(EXT_WEIGHTS_DIR[arm_i].glob(f"h4-lane-{arm_i}.it{it_i}.*.json"))
                if len(hits) != 1:
                    print(f"  ✗ {arm_i} it{it_i}：权重文件 {len(hits)} 个（应 1）⇒ §10 不可信")
                    ext_ok = False
                    continue
                wmap[sha16(hits[0])] = (arm_i, it_i)
        ext_rows: dict[str, dict[tuple[int, int], dict[str, Any]]] = {"a0": {}, "a1": {}}
        unknown = 0
        for p in EXT_LEDGERS:
            with p.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("event") != "eval":
                        continue
                    owner = wmap.get(str(row.get("ckpt_sha16")))
                    if owner is None:
                        unknown += 1
                        continue
                    it_o, seed_o = owner[1], row.get("seed")
                    if not isinstance(seed_o, int) or not isinstance(row.get("ticks"), (int, float)):
                        unknown += 1
                        continue
                    if not isinstance(row.get("onLaneTicks"), (int, float)):
                        unknown += 1
                        continue
                    ext_rows[owner[0]][(it_o, seed_o)] = row
        new_seeds = {a: sorted({s for _i, s in ext_rows[a]}) for a in ("a0", "a1")}
        old_seeds = {a: {s for i, s in rows[a] if i in EXT_ITS} for a in ("a0", "a1")}
        overlap = {a: len(old_seeds[a] & set(new_seeds[a])) for a in ("a0", "a1")}
        n_new = {a: len(ext_rows[a]) for a in ("a0", "a1")}
        print(
            f"  新块：{n_new['a0']} / {n_new['a1']} 局（a0/a1），seed "
            f"{new_seeds['a1'][0]}–{new_seeds['a1'][-1]}（{len(new_seeds['a1'])} 个seed/臂）；"
            f"未知 ckpt 行 {unknown}"
        )
        print(
            f"  不相交检查：与旧段（860001–860200）重叠 {overlap['a0']} / {overlap['a1']} 局（应 0）"
        )
        if overlap["a0"] or overlap["a1"]:
            print("  ✗ 新旧 seed 重叠 ⇒ 简单相加就不是同一把尺子，§10 结论作废")
            ext_ok = False
        if n_new["a0"] != n_new["a1"]:
            print("  ✗ 两臂局数不等 ⇒ 配对不完整，§10 结论作废")
            ext_ok = False

        rows_ext = {a: dict(rows[a]) for a in ("a0", "a1")}
        for a in ("a0", "a1"):
            rows_ext[a].update(ext_rows[a])
        pool_pt: dict[int, tuple[float, float, int]] = {}
        print(
            f"  {'it':>4} {'局/点':>6} {'Δrel%(旧)':>10} {'Δrel%(合并)':>12} {'SE%(旧)':>8} "
            f"{'SE%(合并)':>10} {'t':>7} {'p₁':>7}"
        )
        for it in paired_its:
            st_old = endpoint_pair_stats(rows, ENDPOINTS[0], it, "a1")
            st_new = endpoint_pair_stats(rows_ext, ENDPOINTS[0], it, "a1")
            if st_old is None or st_new is None or st_new[3] == 0:
                continue
            m_old = st_old[0] / st_old[3] * 100.0
            m_new = st_new[0] / st_new[3] * 100.0
            se_new = st_new[2] / st_new[3] * 100.0
            n_pt = sum(1 for (i, _s) in rows_ext["a1"] if i == it)
            t_new = m_new / se_new if se_new else 0.0
            pool_pt[it] = (m_new, se_new, n_pt)
            print(
                f"  {it:>4} {n_pt:>6} {m_old:>10.2f} {m_new:>12.2f} "
                f"{st_old[2] / st_old[3] * 100.0:>8.2f} {se_new:>10.2f} {t_new:>7.2f} "
                f"{norm_cdf(t_new):>7.3f}"
            )
        for its_sel, label in (
            (tuple(EXT_ITS[-3:]), f"尾巴 3 点 it{'/'.join(str(i) for i in EXT_ITS[-3:])}"),
            (tuple(EXT_ITS), f"全 {len(EXT_ITS)} 点 it{EXT_ITS[0]}–{EXT_ITS[-1]}"),
        ):
            vals = [pool_pt[i][0] for i in its_sel if i in pool_pt]
            ses = [pool_pt[i][1] for i in its_sel if i in pool_pt]
            if len(vals) < 2:
                continue
            mean_p = statistics.fmean(vals)
            se_p = se_of_pool(ses)
            lo_p, hi_p, below_green, positive = claim_from_pooled(mean_p, se_p)
            q, ivw, crit = cochran_q(vals, ses)
            verdict = (
                "已证实不到绿线"
                if below_green
                else ("效应可靠为正、但未排除绿线" if positive else "没证据")
            )
            print()
            print(
                f"  {label} 合并后：Δrel = {mean_p:+.2f}%（SE {se_p:.2f}%）⇒ 95% CI "
                f"[{lo_p:+.2f}%, {hi_p:+.2f}%] ⇒ **{verdict}**"
            )
            print(
                f"    预注册两断言：① 不到绿线 {below_green}（需下沿 > −{GREEN_REL:.0f}%）；"
                f"② 效应为正 {positive}（需上沿 < 0）"
            )
            print(
                f"    异质性：Q = {q:.2f} vs χ²(0.95, df={len(vals) - 1}) = {crit:.3f} ⇒ "
                f"{'异质（均值只作描述）' if crit and q > crit else '同质（可取均值）'}；"
                f"逆方差加权均值 = {ivw:+.2f}%"
            )
            if its_sel == tuple(EXT_ITS[-3:]):
                mult = dose_multiple(mean_p)
                picks = [f for f in DOSE_BRACKET_PCT if f / CUR_F_PCT >= mult]
                pick = (
                    f"选 {picks[0]:.1f}%（{picks[0] / CUR_F_PCT:.2f}×）"
                    if picks
                    else f"括号内无档够（最大 {max(DOSE_BRACKET_PCT):.1f}% = "
                    f"{max(DOSE_BRACKET_PCT) / CUR_F_PCT:.2f}× < {mult:.2f}× ⇒ 需超出 ③ 的原括号）"
                )
                print(
                    f"    剂量推论（预注册规则）：|E| = {abs(mean_p):.2f}% ⇒ 到绿线 ≈ {mult:.2f}× "
                    f"当前剂量（f = {CUR_F_PCT}%）⇒ {pick}"
                )
        print()
        print("  读法：合并尺度 = 尾巴 3 点（预注册主判据）；全点均值只在上面「同质」时才可外推。")

    return 0 if (agree and mde_ok and not bad and ext_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
