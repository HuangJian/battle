"""C8 —— z11 四腿的 **seed 级**重算（`plan/classic35-path.plan.md` §5 C8）。

**要解决的问题（ds H1）**：A 层评估池 `EVAL_SEEDS[:100]` 与 `it` 无关
（`worker/eval_track.py:78-95`），一门课 41 个评估点会**复用同一批 400 个
`(stage, seed)` 单元**。旧脚本 `tmp/z11-check.py:128-146` 的配对键是
`(it, stage, seed)`，把 16,400 局当独立样本做一次 McNemar ⇒ **同一批 seed
被计 41 次**，p 只是上界。

**C8 口径（本文件事前钉死，跑数之前写定）**

* **统计单位 = `(stage, seed)` 单元**，`it` 是重复测量，不是样本。
* **窗口** = 所有参与比较的腿都**完整**评估过的 iter 交集（默认完整 = 400 个
  唯一 `(stage, seed)`）。交集对每门腿相同，避免「谁多跑几轮谁占便宜」。
* **主读数（run-average）** = 每个 seed 在窗口内的 win 均值；对 400 个 seed 做
  **配对 sign 检验（精确二项）** 与 **配对 bootstrap 95% CI**。
* **副读数（endpoint）** = 窗口最后一个完整 iter 的逐局 win，同法配对；它对应
  §4 的「单点 400 局」口径，也是 7.7pp MDE 那张表适用的 estimand。
* **MDE(80%, 双侧 95%)** = `(z.975 + z.80) × sd(配对差) / √n`；单点二项口径另
  报 `2.8016 × √(p_discordant / n)`，便于与旧 7.7pp 估计对账。
* **旧 p 的复现值**同时输出（`(it, stage, seed)` 全量 McNemar），只作「上界」
  对照，**不得用于判决**。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/z11_seed_level.py
    bash tools/githook/nn-py-safe.sh nn-training/tools/z11_seed_level.py \
        --legs z11-null=tmp/z11-null,z11-cost=tmp/z11-cost \
        --out tmp/p0-c8/seed_level.json --md tmp/p0-c8/seed_level.md

只读：不写账本、不改课程、不起训练。bootstrap 用固定种子，输出可逐字节复算。
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# 入口脚本自举（脚本模式下 sys.path[0] 是脚本目录，仓内模块要放回 nn-training 根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import house_parser, run_main

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LEGS: dict[str, Path] = {
    "z11-null": ROOT / "tmp" / "z11-null",
    "z11-cost": ROOT / "tmp" / "z11-cost",
    "z11-step": ROOT / "tmp" / "z11-step",
    "z11-ent": ROOT / "tmp" / "z11-ent",
}
CONTROL = "z11-null"
EXPECT_GAMES = 400
BOOT_N = 10_000
BOOT_SEED = 20261107
# 双侧 95% + 80% 功效的正态乘数（与 tools/paired_power.py 同式）
MDE_K = 1.959963985 + 0.8416212336


# ─────────────────────────────────────────────────────────────────────────────
# 读账本 / 完整性
# ─────────────────────────────────────────────────────────────────────────────
def game_key(row: dict[str, Any]) -> tuple[int, int, int] | None:
    """逐局行的键 `(iter, stage, seed)`；非 eval / 坏字段返回 None。"""
    if row.get("event") != "eval":
        return None
    it = row.get("iter")
    stage = row.get("stage")
    seed = row.get("seed")
    if not isinstance(it, int) or isinstance(it, bool):
        return None
    if not isinstance(stage, int) or isinstance(stage, bool):
        return None
    if not isinstance(seed, int) or isinstance(seed, bool):
        return None
    win = row.get("win")
    if isinstance(win, bool):
        win = int(win)
    if not isinstance(win, (int, float)):
        return None
    return it, stage, seed


def load_eval_rows(path: Path) -> tuple[dict[tuple[int, int, int], float], dict[str, int]]:
    """读一份 `eval_log.jsonl` → `(it, stage, seed) -> win`（重复行后者覆盖）。"""
    rows: dict[tuple[int, int, int], float] = {}
    stats = {"lines": 0, "skipped": 0, "duplicates": 0}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            stats["lines"] += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue
            if not isinstance(row, dict):
                stats["skipped"] += 1
                continue
            key = game_key(row)
            if key is None:
                stats["skipped"] += 1
                continue
            if key in rows:
                stats["duplicates"] += 1
            win = row["win"]
            if isinstance(win, bool):
                win = float(win)
            rows[key] = float(win)
    return rows, stats


def iter_counts(rows: dict[tuple[int, int, int], float]) -> dict[int, int]:
    """每个 iter 的唯一逐局行数。"""
    counts: dict[int, int] = defaultdict(int)
    for it, _stage, _seed in rows:
        counts[it] += 1
    return dict(counts)


def complete_iters(
    rows: dict[tuple[int, int, int], float], expect_games: int = EXPECT_GAMES
) -> list[int]:
    """唯一行数 = `expect_games` 的 iter（升序）；半截评估点（如 ent 末点）剔除。"""
    counts = iter_counts(rows)
    return sorted(it for it, n in counts.items() if n == expect_games)


def common_iters(
    legs: dict[str, dict[tuple[int, int, int], float]], expect_games: int = EXPECT_GAMES
) -> list[int]:
    """所有腿都完整的 iter 交集（C8 的唯一窗口口径）。"""
    if not legs:
        return []
    sets = [set(complete_iters(rows, expect_games)) for rows in legs.values()]
    return sorted(set.intersection(*sets))


# ─────────────────────────────────────────────────────────────────────────────
# 聚合 / 配对
# ─────────────────────────────────────────────────────────────────────────────
def per_seed_win(
    rows: dict[tuple[int, int, int], float], iters: list[int]
) -> dict[tuple[int, int], tuple[float, int]]:
    """每个 `(stage, seed)` 在窗口内的 win 均值与局数。"""
    want = set(iters)
    acc: dict[tuple[int, int], float] = defaultdict(float)
    n: dict[tuple[int, int], int] = defaultdict(int)
    for (it, stage, seed), win in rows.items():
        if it not in want:
            continue
        k = (stage, seed)
        acc[k] += win
        n[k] += 1
    return {k: (acc[k] / n[k], n[k]) for k in sorted(acc)}


def paired_diffs(
    treated: dict[tuple[int, int], tuple[float, int]],
    control: dict[tuple[int, int], tuple[float, int]],
) -> tuple[list[tuple[int, int]], list[float]]:
    """两腿共有的 `(stage, seed)` 上的配对差（treated − control）。"""
    keys = sorted(set(treated) & set(control))
    return keys, [treated[k][0] - control[k][0] for k in keys]


def sign_test(diffs: list[float]) -> dict[str, Any]:
    """配对 sign 检验（精确二项，双侧；零差记为 tie 并剔除）。"""
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    ties = len(diffs) - pos - neg
    n = pos + neg
    if n == 0:
        return {"pos": pos, "neg": neg, "ties": ties, "n": 0, "p": 1.0}
    k = min(pos, neg)
    tail: float = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return {"pos": pos, "neg": neg, "ties": ties, "n": n, "p": min(1.0, 2.0 * tail)}


def bootstrap_mean_ci(
    values: list[float], n_boot: int = BOOT_N, seed: int = BOOT_SEED
) -> tuple[float, float]:
    """配对/单样本均值的 percentile bootstrap 95% CI（固定 RNG，可复算）。"""
    if not values:
        return (float("nan"), float("nan"))
    if len(set(values)) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = [statistics.fmean(rng.choices(values, k=n)) for _ in range(n_boot)]
    means.sort()
    lo = means[int(0.025 * (n_boot - 1))]
    hi = means[int(0.975 * (n_boot - 1))]
    return (lo, hi)


def mde80(sd_diff: float, n: int) -> float:
    """配对设计的 80% 功效 MDE（绝对值，与 win 同量纲）。"""
    if n <= 0 or sd_diff <= 0:
        return 0.0 if sd_diff == 0 else float("nan")
    return MDE_K * sd_diff / math.sqrt(n)


def mcnemar_exact(n01: int, n10: int) -> float:
    """McNemar 精确二项双侧 p（`n01` / `n10` 为两个方向的 discordant 计数）。"""
    n = n01 + n10
    if n == 0:
        return 1.0
    k = min(n01, n10)
    tail: float = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def naive_mcnemar(
    treated: dict[tuple[int, int, int], float],
    control: dict[tuple[int, int, int], float],
    iters: list[int],
) -> dict[str, Any]:
    """旧口径复现：把 `(it, stage, seed)` 当独立样本（p 是上界，只作对照）。"""
    want = set(iters)
    keys = sorted(set(treated) & set(control))
    keys = [k for k in keys if k[0] in want]
    n01 = sum(1 for k in keys if control[k] == 0 and treated[k] == 1)
    n10 = sum(1 for k in keys if control[k] == 1 and treated[k] == 0)
    return {"pairs": len(keys), "n01": n01, "n10": n10, "p": mcnemar_exact(n01, n10)}


def icc_oneway(matrix: list[list[float]]) -> float:
    """单向随机效应 ICC（平衡矩阵；行 = seed，列 = iter），截断到 [0, 1]。

    用来量化「同一 seed 跨 iter 的重复测量相关」，并给出有效样本量
    `n / (1 + (k−1)·ICC)`。它不改变 C8 的 seed 级配对，只解释旧 p 为何虚高。
    """
    n = len(matrix)
    if n < 2:
        return 0.0
    k = len(matrix[0])
    if k < 2 or any(len(row) != k for row in matrix):
        return 0.0
    grand = statistics.fmean(x for row in matrix for x in row)
    row_means = [statistics.fmean(row) for row in matrix]
    ssb = k * sum((m - grand) ** 2 for m in row_means)
    ssw = sum((x - statistics.fmean(row)) ** 2 for row in matrix for x in row)
    msb = ssb / (n - 1)
    msw = ssw / (n * (k - 1))
    den = msb + (k - 1) * msw
    if den <= 0:
        return 0.0
    return min(1.0, max(0.0, (msb - msw) / den))


def icc_matrix(
    rows: dict[tuple[int, int, int], float], iters: list[int]
) -> tuple[list[list[float]], list[tuple[int, int]]]:
    """控制腿的 seed × iter 平衡矩阵（按 stage,seed 排序；缺格抛错）。"""
    seeds = sorted({(stage, seed) for _it, stage, seed in rows})
    want = set(iters)
    idx = {(it, stage, seed): win for (it, stage, seed), win in rows.items() if it in want}
    matrix: list[list[float]] = []
    for stage, seed in seeds:
        row = [idx[(it, stage, seed)] for it in iters]
        matrix.append(row)
    return matrix, seeds


# ─────────────────────────────────────────────────────────────────────────────
# 单腿比较
# ─────────────────────────────────────────────────────────────────────────────
def compare_leg(
    name: str,
    rows: dict[tuple[int, int, int], float],
    control_rows: dict[tuple[int, int, int], float],
    iters: list[int],
    *,
    n_boot: int,
    boot_seed: int,
) -> dict[str, Any]:
    """一门腿 vs 控制腿：run-average 主读数 + endpoint 副读数 + 旧 p 对照。"""
    treated = per_seed_win(rows, iters)
    control = per_seed_win(control_rows, iters)
    keys, diffs = paired_diffs(treated, control)
    delta = statistics.fmean(diffs) if diffs else float("nan")
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    lo, hi = bootstrap_mean_ci(diffs, n_boot, boot_seed)
    sign = sign_test(diffs)

    end_it = iters[-1] if iters else -1
    end_t = {
        (stage, seed): (win, 1) for (it, stage, seed), win in rows.items() if it == end_it
    }
    end_c = {
        (stage, seed): (win, 1)
        for (it, stage, seed), win in control_rows.items()
        if it == end_it
    }
    end_keys, end_diffs = paired_diffs(end_t, end_c)
    end_sign = sign_test(end_diffs)
    end_n01 = sum(1 for k in end_keys if end_c[k][0] == 0 and end_t[k][0] == 1)
    end_n10 = sum(1 for k in end_keys if end_c[k][0] == 1 and end_t[k][0] == 0)
    end_discordant = (end_n01 + end_n10) / len(end_keys) if end_keys else float("nan")
    end_sd = statistics.stdev(end_diffs) if len(end_diffs) > 1 else 0.0
    end_lo, end_hi = bootstrap_mean_ci(end_diffs, n_boot, boot_seed)

    return {
        "leg": name,
        "seeds": len(keys),
        "games_per_seed": len(iters),
        "run_average": {
            "delta": delta,
            "ci95": [lo, hi],
            "sd_diff": sd,
            "mde80": mde80(sd, len(keys)),
            "sign": sign,
        },
        "endpoint": {
            "iter": end_it,
            "delta": statistics.fmean(end_diffs) if end_diffs else float("nan"),
            "ci95": [end_lo, end_hi],
            "sd_diff": end_sd,
            "mde80": mde80(end_sd, len(end_keys)),
            "discordant_rate": end_discordant,
            "mcnemar_binary_mde80": (
                MDE_K * math.sqrt(end_discordant / len(end_keys))
                if end_keys and end_discordant == end_discordant
                else float("nan")
            ),
            "mcnemar": {"n01": end_n01, "n10": end_n10, "p": mcnemar_exact(end_n01, end_n10)},
            "sign": end_sign,
        },
        "naive_pooled": naive_mcnemar(rows, control_rows, iters),
    }


def build_report(
    legs: dict[str, dict[tuple[int, int, int], float]],
    control: str,
    *,
    expect_games: int = EXPECT_GAMES,
    n_boot: int = BOOT_N,
    boot_seed: int = BOOT_SEED,
    leg_stats: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """C8 全量结果（纯函数；`main()` 只负责读盘与落盘）。"""
    if control not in legs:
        raise KeyError(f"control leg 不在 legs 里: {control}")
    iters = common_iters(legs, expect_games)
    if not iters:
        raise ValueError("没有一条完整的公共 iter —— 检查 --expect-games 或账本")
    control_rows = legs[control]
    matrix, seeds = icc_matrix(control_rows, iters)
    icc = icc_oneway(matrix)
    design = 1 + (len(iters) - 1) * icc
    effective_obs = len(seeds) / design if seeds and design else 0.0

    comparisons = {
        name: compare_leg(
            name, rows, control_rows, iters, n_boot=n_boot, boot_seed=boot_seed
        )
        for name, rows in legs.items()
        if name != control
    }
    sds = [c["run_average"]["sd_diff"] for c in comparisons.values() if c["seeds"] > 1]
    pooled_sd = math.sqrt(statistics.fmean(s * s for s in sds)) if sds else 0.0
    n_seeds = len(seeds)

    return {
        "schema": "p0-c8-seed-level/v1",
        "pre_registration": {
            "statistical_unit": "(stage, seed)",
            "window": "所有腿都完整评估过的 iter 交集",
            "complete_point": f"{expect_games} unique (stage, seed) rows",
            "primary": "per-seed win mean over window; paired sign + bootstrap CI",
            "secondary": "last complete iter; per-seed binary win; McNemar",
            "bootstrap": {"n": n_boot, "seed": boot_seed},
            "mde": "(z.975 + z.80) * sd(paired diff) / sqrt(n_seeds)",
        },
        "control": control,
        "window": {
            "iters": iters,
            "n_iters": len(iters),
            "seeds": n_seeds,
            "games": len(iters) * n_seeds,
            "icc_control": icc,
            "design_effect": design,
            "equivalent_independent_points": (
                len(iters) / design if design else float("nan")
            ),
            "effective_binary_observations": effective_obs,
        },
        "legs": {
            name: {
                "points_total": len(iter_counts(rows)),
                "points_complete": len(complete_iters(rows, expect_games)),
                "points_in_window": sum(1 for it in iter_counts(rows) if it in set(iters)),
                "load": (leg_stats or {}).get(name, {}),
            }
            for name, rows in legs.items()
        },
        "comparisons": comparisons,
        "panel_mde80": {
            "run_average_pooled_sd": pooled_sd,
            "at_400_seeds": mde80(pooled_sd, n_seeds),
            "at_800_seeds": mde80(pooled_sd, n_seeds * 2),
            "at_1600_seeds": mde80(pooled_sd, n_seeds * 4),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────────────────────────────────────────
def _pp(x: float) -> str:
    return f"{x * 100:+.2f}pp" if x == x else "n/a"


def _pct(x: float) -> str:
    return f"{x * 100:.2f}pp" if x == x else "n/a"


def render_markdown(res: dict[str, Any]) -> str:
    """把结果渲染成可直接贴进 `reports/` 的 Markdown。"""
    w = res["window"]
    ctrl = res["control"]
    out: list[str] = [
        "# C8 —— z11 四腿 seed 级重算",
        "",
        "> 口径事前钉死：统计单位 = `(stage, seed)`；窗口 = 四腿都完整的 iter 交集；",
        "> 主读数 = 每 seed 的窗口均值（配对 sign + bootstrap 95% CI）；",
        "> 副读数 = 窗口末个完整 iter 的逐局 win（单点 400 局口径）。",
        "> 旧 `(it, stage, seed)` 全量 McNemar 只作**上界对照**，不用于判决。",
        "",
        "## 0. 复用审计",
        "",
        f"- 控制腿：`{ctrl}`；窗口 **{w['n_iters']} 个完整 iter** × "
        f"**{w['seeds']} 个唯一 `(stage, seed)`** = {w['games']:,} 局。",
        f"- 同一 seed 跨 iter 的重复测量相关：**ICC = {w['icc_control']:.4f}**；"
        f"设计效应 **{w['design_effect']:.2f}×** ⇒ {w['n_iters']} 个点只值约 "
        f"**{w['equivalent_independent_points']:.1f} 个独立点**（不是 {w['n_iters']} 个）。",
        f"- 旧全量 McNemar 的等效二项观测 ≈ **{w['effective_binary_observations']:.0f}**"
        f"（只用于解释旧 p；C8 判决仍用 {w['seeds']} 个 seed 均值，不是这个数）。",
        "- ⇒ 旧脚本把 16,400 局当独立样本，p 值最多被放大到「上界」；"
        "真判决只认下表的 seed 级数字。",
        "",
        "## 1. 主读数（run-average，seed 级配对）",
        "",
        "| 腿 | seeds | Δ win | 95% CI | sign +/−/tie | sign p | 配对 SD | MDE(80%)@400 |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, c in res["comparisons"].items():
        ra = c["run_average"]
        s = ra["sign"]
        out.append(
            f"| `{name}` | {c['seeds']} | **{_pp(ra['delta'])}** | "
            f"[{_pp(ra['ci95'][0])}, {_pp(ra['ci95'][1])}] | "
            f"{s['pos']}/{s['neg']}/{s['ties']} | {s['p']:.4g} | "
            f"{ra['sd_diff']:.4f} | ±{_pct(ra['mde80'])} |"
        )
    out += [
        "",
        "## 2. 副读数（endpoint = 末个完整 iter，单点 400 局）",
        "",
        "| 腿 | iter | Δ win | 95% CI | McNemar n01:n10 | p | discordant | 单点 MDE(80%) |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for name, c in res["comparisons"].items():
        ep = c["endpoint"]
        mc = ep["mcnemar"]
        out.append(
            f"| `{name}` | {ep['iter']} | **{_pp(ep['delta'])}** | "
            f"[{_pp(ep['ci95'][0])}, {_pp(ep['ci95'][1])}] | "
            f"{mc['n01']}:{mc['n10']} | {mc['p']:.4g} | "
            f"{ep['discordant_rate'] * 100:.1f}% | ±{_pct(ep['mcnemar_binary_mde80'])} |"
        )
    out += [
        "",
        "## 3. 旧口径对照（`(it, stage, seed)` 全量；**上界，不判决**）",
        "",
        "| 腿 | 配对数 | n01:n10 | p（上界） |",
        "|---|---:|---:|---:|",
    ]
    for name, c in res["comparisons"].items():
        np_ = c["naive_pooled"]
        out.append(f"| `{name}` | {np_['pairs']:,} | {np_['n01']}:{np_['n10']} | {np_['p']:.4g} |")
    m = res["panel_mde80"]
    out += [
        "",
        "## 4. 本面板的真实 MDE",
        "",
        f"- run-average（配对 SD 合并 {m['run_average_pooled_sd']:.4f}）："
        f"**400 seeds → ±{_pct(m['at_400_seeds'])}**、"
        f"800 → ±{_pct(m['at_800_seeds'])}、1,600 → ±{_pct(m['at_1600_seeds'])}。",
        "- 单点二项口径见 §2 的「单点 MDE」列；它才是 `plan/classic35-path.plan.md` §4 "
        "「400 → 7.7pp」那张表该用的量。",
        "",
        "## 5. 判读规则",
        "",
        "- 主读数 CI 含 0 **且** |Δ| < run-average MDE ⇒ 写「**不可分辨**」，不写「无效」。",
        "- 单点 |Δ| < 单点 MDE ⇒ 同样写「不可分辨」；P3/P4 的单点判据用这一列。",
        "- `z11-ent` 的窗口与 null 取交集（0–200），不用它多跑的 201+ 轮；"
        "那部分只在报告中作描述性读数，不进配对判决。",
        "",
    ]
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def parse_legs(spec: str) -> dict[str, Path]:
    """`name=dir,name=dir` → `name -> dir/eval_log.jsonl`。"""
    out: dict[str, Path] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            raise SystemExit(f"坏的 --legs 项: {item!r}（要 name=dir）")
        p = Path(path)
        out[name] = p / "eval_log.jsonl" if p.is_dir() else p
    return out


def main() -> int:
    ap = house_parser(description="C8: z11 四腿 seed 级重算（只读）")
    ap.add_argument(
        "--legs",
        default=",".join(f"{n}={p}" for n, p in DEFAULT_LEGS.items()),
        help="逗号分隔的 name=dir（或 name=eval_log.jsonl）",
    )
    ap.add_argument("--control", default=CONTROL, help="控制腿名")
    ap.add_argument("--expect-games", type=int, default=EXPECT_GAMES, help="完整评估点的唯一局数")
    ap.add_argument("--boot", type=int, default=BOOT_N, help="bootstrap 次数")
    ap.add_argument("--boot-seed", type=int, default=BOOT_SEED, help="bootstrap RNG 种子")
    ap.add_argument("--out", default="tmp/p0-c8/seed_level.json", help="JSON 输出")
    ap.add_argument("--md", default="tmp/p0-c8/seed_level.md", help="Markdown 输出")
    args = ap.parse_args()

    paths = parse_legs(args.legs)
    legs: dict[str, dict[tuple[int, int, int], float]] = {}
    stats: dict[str, dict[str, int]] = {}
    for name, path in paths.items():
        if not path.exists():
            print(f"✗ 缺账本: {name} -> {path}", file=sys.stderr)
            return 2
        rows, st = load_eval_rows(path)
        legs[name] = rows
        stats[name] = st
    try:
        res = build_report(
            legs,
            args.control,
            expect_games=args.expect_games,
            n_boot=args.boot,
            boot_seed=args.boot_seed,
            leg_stats=stats,
        )
    except (KeyError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    out = Path(args.out)
    md = Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    print(f"✓ {out}")
    print(f"✓ {md}")
    for name, c in res["comparisons"].items():
        ra, ep = c["run_average"], c["endpoint"]
        print(
            f"  {name}: run-avg {_pp(ra['delta'])} "
            f"[{_pp(ra['ci95'][0])},{_pp(ra['ci95'][1])}] sign p={ra['sign']['p']:.4g} "
            f"| endpoint it{ep['iter']} {_pp(ep['delta'])} p={ep['mcnemar']['p']:.4g}"
        )
    return 0


if __name__ == "__main__":
    run_main(main)
