"""C2 — z10.it233 shard 的 ΣthCost 分布（`plan/classic35-path.plan.md` §5 C2）。

**问题**：P3-A 要给基地威胁流定价，但 z10-t 的判据「ΣthCost 降 ≥15%」从未有读数；
校验上界（`thShotsAtBaseCum` 3000 / `thCost*` 4000）又太高，按它定剂量会让任何有意义
的事件价越过 reward 闸。本工具只读现成 RL shard 的 metrics idx85–88，给出**实测**
每局总成本的分位与按关分布，作为 P3-A 的剂量输入。

**口径**
* 每局 `ΣthCost = metrics[N, 85] + … + metrics[N, 88]`；四列是逐决策累计成本流，
  末行即整局总量（不是逐行求和）。
* `p50/p95/p99` 按**局**统计（不是决策步）；CI 用按局 bootstrap（固定种子）。
* `w@18 = 18 / p95`：把「p95 局的总成本」压到 `0.3 × wWin=18` 的隐含单价。
  这只是剂量换算，不是开课决定；A 臂是否用该单价由 P3 判据决定。
* 只读：不写 shard、不起训练。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/p0_thcost.py \\
        --shards tmp/p0-c9/rollouts \\
        --out tmp/p0-thcost/thcost.json --md tmp/p0-thcost/thcost.md
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from biz.reward_library import METRIC_INDEX, METRICS_DIM, METRICS_VERSION
from common.cli import house_parser, run_main
from tools.p0_common import find_shards, read_manifest
from tools.p0_common import fmt_float as _f

THCOST_NAMES: tuple[str, ...] = (
    "thCostBreach",
    "thCostBreachAimed",
    "thCostDirect",
    "thCostDirectAimed",
)
THCOST_INDICES: tuple[int, ...] = tuple(METRIC_INDEX[n] for n in THCOST_NAMES)
SHARD_FILES = ("metrics.npy", "manifest.json")
DOSE_BUDGET = 18.0  # 0.3 × wWin=60；plan §8-A 的 p95 上限
DEFAULT_BOOT = 2000
DEFAULT_BOOT_SEED = 20261108


@dataclass(frozen=True)
class CostGame:
    """一局在末行的四轴累计成本。"""

    stage: int
    seed: int
    decisions: int
    total: float
    axes: tuple[float, ...]


def load_shard(
    dirpath: str,
    *,
    expected_metrics_version: int = METRICS_VERSION,
    expected_dim: int = METRICS_DIM,
) -> CostGame:
    """读一个 shard 的 metrics 末行，返回四轴成本与总成本。"""
    m = read_manifest(dirpath)
    stage = m.get("stage")
    seed = m.get("seed")
    if not isinstance(stage, int) or not isinstance(seed, int):
        raise ValueError(f"{dirpath}: manifest 缺 int stage/seed")
    mver = m.get("metrics_version")
    if mver is not None and int(mver) != int(expected_metrics_version):
        raise ValueError(
            f"{dirpath}: metrics_version={mver} != {expected_metrics_version}——"
            "语料与当前成本列不同源"
        )
    metrics = np.asarray(np.load(os.path.join(dirpath, "metrics.npy")), dtype=np.float64)
    if metrics.ndim != 2 or metrics.shape[1] != expected_dim:
        raise ValueError(
            f"{dirpath}: metrics 应为 [N+1,{expected_dim}]，收到 {metrics.shape}"
        )
    if metrics.shape[0] < 2:
        raise ValueError(f"{dirpath}: metrics 只有表头，没有决策行")
    axes = tuple(float(v) for v in metrics[-1, list(THCOST_INDICES)])
    if not np.isfinite(axes).all():
        raise ValueError(f"{dirpath}: thCost 含非有限值")
    if any(v < 0.0 for v in axes):
        raise ValueError(f"{dirpath}: thCost 含负值 {axes}")
    return CostGame(
        stage=int(stage),
        seed=int(seed),
        decisions=int(metrics.shape[0] - 1),
        total=float(sum(axes)),
        axes=axes,
    )


def summarize(values: np.ndarray) -> dict[str, float | int]:
    """按局统计；空数组抛错，单局 sd=0。"""
    x = np.asarray(values, dtype=np.float64).reshape(-1)
    if x.size == 0:
        raise ValueError("没有可统计的局")
    if not np.isfinite(x).all():
        raise ValueError("成本含非有限值")
    sd = float(np.std(x, ddof=1)) if x.size > 1 else 0.0
    return {
        "n": int(x.size),
        "mean": float(x.mean()),
        "sd": sd,
        "min": float(x.min()),
        "p50": float(np.percentile(x, 50)),
        "p90": float(np.percentile(x, 90)),
        "p95": float(np.percentile(x, 95)),
        "p99": float(np.percentile(x, 99)),
        "max": float(x.max()),
        "zero_frac": float(np.mean(x == 0.0)),
    }


def _axes_summary(games: list[CostGame]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    for i, name in enumerate(THCOST_NAMES):
        st = summarize(np.array([g.axes[i] for g in games], dtype=np.float64))
        out[name] = {k: st[k] for k in ("n", "mean", "p50", "p95", "p99", "max")}
    return out


def bootstrap_ci(
    values: np.ndarray, *, reps: int = DEFAULT_BOOT, seed: int = DEFAULT_BOOT_SEED
) -> dict[str, list[float] | None]:
    """按局重采样 p50/p95 的 95% CI；固定种子可复算。"""
    x = np.asarray(values, dtype=np.float64).reshape(-1)
    if reps <= 0 or x.size < 2:
        return {"p50": None, "p95": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, size=(int(reps), x.size))
    p50 = np.percentile(x[idx], 50, axis=1)
    p95 = np.percentile(x[idx], 95, axis=1)
    return {
        "p50": [float(np.percentile(p50, 2.5)), float(np.percentile(p50, 97.5))],
        "p95": [float(np.percentile(p95, 2.5)), float(np.percentile(p95, 97.5))],
    }


def _dose(st: dict[str, float | int]) -> dict[str, float | None]:
    p95 = float(st["p95"])
    return {
        "budget": DOSE_BUDGET,
        "p95_total": p95,
        "implied_w": (DOSE_BUDGET / p95) if p95 > 0.0 else None,
    }


def build_report(
    games: list[CostGame],
    *,
    root: str,
    n_boot: int = DEFAULT_BOOT,
    boot_seed: int = DEFAULT_BOOT_SEED,
) -> dict[str, Any]:
    if not games:
        raise ValueError("没有可用 shard")
    stages = sorted({g.stage for g in games})
    per_stage: dict[str, Any] = {}
    for stage in stages:
        subset = [g for g in games if g.stage == stage]
        values = np.array([g.total for g in subset], dtype=np.float64)
        st = summarize(values)
        per_stage[str(stage)] = {
            "summary": st,
            "axes": _axes_summary(subset),
            "ci95": bootstrap_ci(values, reps=n_boot, seed=boot_seed + stage),
            "dose": _dose(st),
            "decisions_mean": float(np.mean([g.decisions for g in subset])),
        }
    pooled_values = np.array([g.total for g in games], dtype=np.float64)
    pooled_st = summarize(pooled_values)
    decisions = np.array([g.decisions for g in games], dtype=np.float64)
    return {
        "schema": "c2-thcost-v1",
        "source": root,
        "metrics_version": METRICS_VERSION,
        "columns": {
            "index": list(THCOST_INDICES),
            "names": list(THCOST_NAMES),
            "total": "sum of the four final-row values",
        },
        "shards": {
            "games": len(games),
            "stages": stages,
            "per_stage": {str(s): sum(1 for g in games if g.stage == s) for s in stages},
            "decisions_mean": float(decisions.mean()),
            "decisions_min": int(decisions.min()),
            "decisions_max": int(decisions.max()),
            "decisions_total": int(decisions.sum()),
        },
        "pooled": {
            "summary": pooled_st,
            "axes": _axes_summary(games),
            "ci95": bootstrap_ci(pooled_values, reps=n_boot, seed=boot_seed),
            "dose": _dose(pooled_st),
        },
        "per_stage": per_stage,
        "bootstrap": {"reps": int(n_boot), "seed": int(boot_seed), "cluster": "game"},
        "boundary": (
            "z10.it233 的 4 关窗口 / 20k 拍训练期 rollout；不是 w1-null 的 36k 拍"
            "面板，也不是 z10-t 训练后的策略。"
        ),
    }


def render_markdown(res: dict[str, Any]) -> str:
    sh = res["shards"]
    pool = res["pooled"]
    ps = pool["summary"]
    out = [
        "# C2 — ΣthCost 分布（z10.it233 基线）",
        "",
        f"- 来源：`{res['source']}`",
        f"- 数据：**{sh['games']} 局** · 分关 "
        + " · ".join(f"{k}:{v}" for k, v in sh["per_stage"].items())
        + f" · metrics v{res['metrics_version']}",
        f"- 决策/局：mean **{_f(sh['decisions_mean'], 1)}** · "
        f"range **{sh['decisions_min']}–{sh['decisions_max']}** · "
        f"total **{sh['decisions_total']}**",
        f"- 列：`{', '.join(res['columns']['names'])}`（idx "
        f"{', '.join(str(i) for i in res['columns']['index'])}，末行求和）",
        f"- pooled：p50 **{_f(ps['p50'])}** · p95 **{_f(ps['p95'])}** · "
        f"p99 **{_f(ps['p99'])}** · max **{_f(ps['max'])}**",
        "",
        "## 1. 按关分布",
        "",
        "| 关 | 局 | 决策/局 | mean | p50 | p95 | p99 | max | 零成本局 | p95 95% CI | w@18 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for stage, v in res["per_stage"].items():
        st = v["summary"]
        ci = v["ci95"]["p95"]
        ci_s = f"[{_f(ci[0])}, {_f(ci[1])}]" if ci else "n/a"
        out.append(
            f"| {stage} | {st['n']} | {_f(v['decisions_mean'], 1)} | {_f(st['mean'])} | "
            f"{_f(st['p50'])} | {_f(st['p95'])} | {_f(st['p99'])} | {_f(st['max'])} | "
            f"{_f(st['zero_frac'], 3)} | {ci_s} | {_f(v['dose']['implied_w'], 4)} |"
        )
    ci = pool["ci95"]["p95"]
    ci_s = f"[{_f(ci[0])}, {_f(ci[1])}]" if ci else "n/a"
    out.append(
        f"| **pooled** | {ps['n']} | {_f(sh['decisions_mean'], 1)} | {_f(ps['mean'])} | "
        f"{_f(ps['p50'])} | {_f(ps['p95'])} | {_f(ps['p99'])} | {_f(ps['max'])} | "
        f"{_f(ps['zero_frac'], 3)} | {ci_s} | {_f(pool['dose']['implied_w'], 4)} |"
    )
    out += [
        "",
        "## 2. 四轴 p95",
        "",
        "| 关 | " + " | ".join(THCOST_NAMES) + " |",
        "|---|" + "---:|" * len(THCOST_NAMES),
    ]
    for stage, v in res["per_stage"].items():
        ax = v["axes"]
        out.append(
            f"| {stage} | "
            + " | ".join(_f(ax[n]["p95"]) for n in THCOST_NAMES)
            + " |"
        )
    ax = pool["axes"]
    out.append(
        "| **pooled** | "
        + " | ".join(_f(ax[n]["p95"]) for n in THCOST_NAMES)
        + " |"
    )
    out += [
        "",
        "## 3. 判读",
        "",
        f"- pooled p95 = **{_f(ps['p95'])}**；把它压到 `0.3×wWin=18` 的隐含单价 "
        f"`wThCost = **{_f(pool['dose']['implied_w'], 4)}**`。按关单价见上表。",
        "- 这只是**剂量换算**，不是「该开 P3-A」或「该用哪个单价」的判决；A 臂还要看"
        "机制量是否真的移动、以及 win 地板。",
        "- `p95` 的 95% CI 用按局 bootstrap，可当作批内噪声的保守读数；"
        "跨批比较必须用同一关集/同一面板。",
        "",
        "## 4. 边界",
        "",
        f"- {res['boundary']}",
        "- 指标是训练期 rollout（含探索噪声），不是 eval 账本；不要把它当最终能力读数。",
        "- 未跑训练、未改 shard；本工具只读 `metrics.npy` + `manifest.json`。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = house_parser(description="C2: ΣthCost distribution from RL shards (read-only)")
    ap.add_argument("--shards", default="tmp/p0-c9/rollouts")
    ap.add_argument("--out", default="tmp/p0-thcost/thcost.json")
    ap.add_argument("--md", default="tmp/p0-thcost/thcost.md")
    ap.add_argument("--boot", type=int, default=DEFAULT_BOOT)
    ap.add_argument("--boot-seed", type=int, default=DEFAULT_BOOT_SEED)
    args = ap.parse_args()

    shard_dirs = find_shards(args.shards, SHARD_FILES)
    if not shard_dirs:
        print(f"✗ 没有 shard（需 metrics/manifest）: {args.shards}", file=sys.stderr)
        return 2
    games: list[CostGame] = []
    for d in shard_dirs:
        try:
            games.append(load_shard(d))
        except (OSError, ValueError, KeyError) as e:
            print(f"✗ {d}: {e}", file=sys.stderr)
            return 2
    keys = [(g.stage, g.seed) for g in games]
    if len(set(keys)) != len(keys):
        print("✗ 重复的 (stage, seed) shard", file=sys.stderr)
        return 2
    res = build_report(games, root=args.shards, n_boot=args.boot, boot_seed=args.boot_seed)
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    ps = res["pooled"]["summary"]
    print(f"✓ {out}")
    print(f"✓ {md}")
    print(
        f"  pooled: n={ps['n']} p50={_f(ps['p50'])} p95={_f(ps['p95'])} "
        f"p99={_f(ps['p99'])} w@18={_f(res['pooled']['dose']['implied_w'], 4)}"
    )
    return 0


if __name__ == "__main__":
    run_main(main)
