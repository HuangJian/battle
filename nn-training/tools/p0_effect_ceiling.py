"""C1 — z 价格腿的效应量上限（`plan/classic35-path.plan.md` §5 C1）。

**问题**：z 系的胜率判决已做了，但没有一张表回答「这个价格项就算全量程移动，
最多能改多少 return？」。若上限小于一个可测阈值，后续再抬同一项就是白烧轮数。

**口径**
* 只审 **价格侧** 的 `w*`（终点价 / 机制价）；训练侧 (`lr`/`kl`/`ent`)、换起点、
  BC 不在本表。
* `最大差额 = |新权重 − 旧权重| × 项内最大量`。项内最大量优先取公式自带的 cap
  （`pickupCap` / `brickCap` / `thCostCap` / `approachCap` / `nearRadius`）；
  `wBase` 的 `baseWallIntact` 上界 = 8；`wDmg` 的 `playerDamageTaken` 无公式 cap，
  用同一批 on-policy shard 的**观测最大值**，并在报告里标明是经验上界。
* 阈值 = **30 分**（`classic35-path.ds.md` P0.1 的「<30 分 = 不可测」），不是
  win-rate 的 8pp MDE；两者单位不同，报告只把 8pp 作参考列，不拿它重判。
* 这是**固定轨迹/固定 metrics** 的代数上限，不是行为响应预测；上限大不等于
  该腿会有效（z11-cost 就是反例：上限大、行为没动）。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/p0_effect_ceiling.py \\
        --shards tmp/p0-c9/rollouts \\
        --out tmp/p0-return/effect-ceiling.json --md tmp/p0-return/effect-ceiling.md
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

from biz.course_resolve import load_course
from biz.reward_library import METRIC_INDEX, METRICS_DIM, METRICS_VERSION
from common.cli import house_parser, run_main
from tools.p0_common import find_shards, read_manifest
from tools.p0_common import fmt_float as _f

NN_ROOT = Path(__file__).resolve().parents[1]
CURRICULA = NN_ROOT / "curricula"
THRESHOLD = 30.0
WIN_MDE_PP = 8.0
BASE_WALL_CELLS = 8.0
DEFAULT_SHARDS = "tmp/p0-c9/rollouts"
SHARD_FILES = ("metrics.npy", "manifest.json")


@dataclass(frozen=True)
class Lever:
    """一个价格腿的一行：历史臂 + 当前课程里的项内上界。"""

    arm: str
    param: str
    old: float
    new: float
    cap: float | None
    cap_source: str
    note: str


def _params(name: str) -> dict[str, float]:
    course = load_course(CURRICULA / f"{name}.jsonc")
    return {k: float(v) for k, v in course.reward.params.items()}


def observed_max_metric(root: str, name: str) -> tuple[float | None, int]:
    """同一批 shard 的末行指标最大值；无 shard ⇒ (None, 0)。"""
    dirs = find_shards(root, SHARD_FILES)
    if not dirs:
        return None, 0
    idx = METRIC_INDEX[name]
    best: float | None = None
    used = 0
    for d in dirs:
        p = os.path.join(d, "metrics.npy")
        try:
            manifest = read_manifest(d)
            if int(manifest.get("metrics_version", METRICS_VERSION)) != METRICS_VERSION:
                continue
            m = np.asarray(np.load(p), dtype=np.float64)
        except (OSError, ValueError, TypeError):
            continue
        if m.ndim != 2 or m.shape[1] < METRICS_DIM or m.shape[0] < 1:
            continue
        v = float(m[-1, idx])
        if not np.isfinite(v):
            continue
        best = v if best is None else max(best, v)
        used += 1
    return best, used


def build_levers(shards: str = DEFAULT_SHARDS) -> tuple[list[Lever], dict[str, Any]]:
    """从当前课程取 cap；历史臂的 old/new 来自 plan/§2 的价目表。"""
    p5 = _params("z5-pick")
    p9 = _params("z9-r4")
    p9b = _params("z9-r4b")
    p10 = _params("z10-maze")
    p10b = _params("z10-maze-brick")
    p10t = _params("z10-maze-t")
    p11 = _params("z11-null")
    p11c = _params("z11-cost")
    pickup_cap = p5["pickupCap"]
    brick_cap = p10b["brickCap"]
    approach_cap = p9["approachCap"]
    near_cap = p9["nearRadius"]
    thcost_cap = p10t["thCostCap"]
    dmg_max, n_shards = observed_max_metric(shards, "playerDamageTaken")
    dmg_cap_source = (
        f"{shards} 末行 playerDamageTaken 最大 {dmg_max:.0f}（{n_shards} shard）"
        if dmg_max is not None
        else "无 shard，无法取经验上界"
    )
    levers = [
        Lever(
            "z5-pick",
            "wPickup",
            4.0,
            7.0,
            pickup_cap,
            f"z5-pick.pickupCap={pickup_cap:g}",
            "历史第一档；拾取项 min(powerUpsCollected,pickupCap)",
        ),
        Lever(
            "z5-pick (max)",
            "wPickup",
            4.0,
            20.0,
            pickup_cap,
            f"z5-pick.pickupCap={pickup_cap:g}",
            "历史最高档；盘上无 20 的课程文件，按 plan §2 记录",
        ),
        Lever(
            "z9-r4",
            "wPickup",
            4.0,
            8.0,
            pickup_cap,
            f"z5-pick.pickupCap={pickup_cap:g}",
            "拾取终点价；与 z10 的 4 对照",
        ),
        Lever(
            "z9-r4",
            "wNear",
            0.0,
            float(p9["wNear"]),
            near_cap,
            f"z9-r4.nearRadius={near_cap:g}",
            "新增近距离项；与 wApproach 同距离族，实际净额需逐状态对账",
        ),
        Lever(
            "z9-r4",
            "wApproach",
            float(p5["wApproach"]),
            float(p9["wApproach"]),
            approach_cap,
            f"z9-r4.approachCap={approach_cap:g}",
            "距离塑形；与 wNear 同族，单看本项只是上界",
        ),
        Lever(
            "z9-r4b",
            "wBase",
            3.0,
            float(p9b["wBase"]),
            BASE_WALL_CELLS,
            "classic35 基环 8 格（baseWallIntact）",
            "静态墙价；z9-r4b 的 wBase 7 对照 z9-r4 的 3",
        ),
        Lever(
            "z10-maze-t",
            "wBase",
            float(p10["wBase"]),
            3.0,
            BASE_WALL_CELLS,
            "classic35 基环 8 格（baseWallIntact）",
            "基地价从静态高价降到 3，为流价让位",
        ),
        Lever(
            "z10-maze-t",
            "wThCost",
            0.0,
            float(p10t["wThCost"]),
            thcost_cap,
            f"z10-maze-t.thCostCap={thcost_cap:g}",
            "基地威胁四轴累计流价；cap 是课程自带上限",
        ),
        Lever(
            "z10-maze-brick",
            "wBrick",
            float(p10["wBrick"]),
            float(p10b["wBrick"]),
            brick_cap,
            f"z10-maze-brick.brickCap={brick_cap:g}",
            "破墙价；项为 min(aimBricks,brickCap)",
        ),
        Lever(
            "z11-cost",
            "wDmg",
            float(p11["wDmg"]),
            float(p11c["wDmg"]),
            dmg_max,
            dmg_cap_source,
            "承伤价下调；无公式 cap，用同批 on-policy 经验上界",
        ),
    ]
    meta = {
        "shards": shards,
        "n_shards": n_shards,
        "metrics_version": METRICS_VERSION,
        "w_dmg_max": dmg_max,
        "course_caps": {
            "pickupCap": pickup_cap,
            "brickCap": brick_cap,
            "approachCap": approach_cap,
            "nearRadius": near_cap,
            "thCostCap": thcost_cap,
        },
    }
    return levers, meta


def compute_row(lever: Lever) -> dict[str, Any]:
    """`|Δw| × cap`；无 cap 时不给假数。"""
    delta = abs(float(lever.new) - float(lever.old))
    if lever.cap is None:
        return {
            "arm": lever.arm,
            "param": lever.param,
            "old": float(lever.old),
            "new": float(lever.new),
            "delta_w": delta,
            "cap": None,
            "cap_source": lever.cap_source,
            "ceiling": None,
            "ratio_to_30": None,
            "ratio_to_win_mde": None,
            "verdict": "n/a",
            "note": lever.note,
        }
    ceiling = delta * float(lever.cap)
    return {
        "arm": lever.arm,
        "param": lever.param,
        "old": float(lever.old),
        "new": float(lever.new),
        "delta_w": delta,
        "cap": float(lever.cap),
        "cap_source": lever.cap_source,
        "ceiling": float(ceiling),
        "ratio_to_30": float(ceiling / THRESHOLD),
        "ratio_to_win_mde": float(ceiling / WIN_MDE_PP),
        "verdict": "不可测" if ceiling < THRESHOLD else "≥30",
        "note": lever.note,
    }


def build_report(levers: list[Lever], meta: dict[str, Any]) -> dict[str, Any]:
    rows = [compute_row(x) for x in levers]
    below = [r for r in rows if r["ceiling"] is not None and r["ceiling"] < THRESHOLD]
    return {
        "schema": "c1-effect-ceiling-v1",
        "threshold": THRESHOLD,
        "win_mde_pp_reference": WIN_MDE_PP,
        "rule": "ceiling = |new-old| * term cap; <30 => 不可测",
        "meta": meta,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "below_30": len(below),
            "at_or_above_30": sum(
                1 for r in rows if r["ceiling"] is not None and r["ceiling"] >= THRESHOLD
            ),
            "n_a": sum(1 for r in rows if r["ceiling"] is None),
        },
        "boundary": (
            "固定轨迹/metrics 的代数上限，不是行为响应预测；"
            "训练侧/换起点/BC 不在表内。"
        ),
    }


def render_markdown(res: dict[str, Any]) -> str:
    meta = res["meta"]
    out = [
        "# C1 — z 价格腿效应量上限",
        "",
        f"- 数据：`{meta['shards']}` · {meta['n_shards']} shard · metrics v{meta['metrics_version']}",
        "- 公式：`最大差额 = |Δw| × 项内最大量` · 阈值 **30 分**（<30 = 不可测）",
        f"- 经验上界：`playerDamageTaken` max **{_f(meta['w_dmg_max'], 0)}**（同一批 on-policy 末行）",
        "- 参考：win-rate 的 8pp MDE 只列比值，**不**与 30 分同单位比较",
        "",
        "## 1. 表",
        "",
        "| 腿 | 参数 | 旧 → 新 | 项内最大量 | 最大差额 | /30 | ÷8pp | 判决 | 备注 |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in res["rows"]:
        out.append(
            f"| {r['arm']} | `{r['param']}` | {_f(r['old'], 3)} → {_f(r['new'], 3)} | "
            f"{_f(r['cap'], 0)} | {_f(r['ceiling'])} | {_f(r['ratio_to_30'], 2)} | "
            f"{_f(r['ratio_to_win_mde'], 2)} | {r['verdict']} | {r['note']} |"
        )
    out += [
        "",
        "## 2. 判读",
        "",
        f"- **{res['summary']['below_30']} / {res['summary']['rows']} 行**低于 30 分："
        "wPickup 4→7、wPickup 4→8、wNear、wApproach、wThCost、wBrick。"
        "这些项即使在满量程移动，也不足以单独解释一个胜率台阶。",
        "- **wBase 3→7 / 7→3 = 32 分**，刚过阈值；但 z9-r4b 的实际读数仍是负向，"
        "说明「上限够大」不等于「行为会被买到」。",
        "- **wDmg 下调的上限 111.34 分**（经验 max 1172）看似很大，但 z11-cost 的"
        " `dmg/局` 没降、配对 win −2.84pp（C8）⇒ 上限只是**可能性**，不是效应量。",
        "- `wNear`/`wApproach` 是同距离族的两个项，单行上界不能相加；"
        "实际净额要按状态逐点对账。",
        "",
        "## 3. 边界",
        "",
        f"- {res['boundary']}",
        "- `wDmg` 的 cap 是这 200 局的经验最大值，不是理论/校验上界；换关集/换策略会变。",
        "- `wPickup 4→7/20` 是历史臂，盘上当前 `z5-pick` 只有 4；"
        "上界按当前 `pickupCap=6` 计算。",
        "- 未跑训练、未改课程；本工具只读课程公式与 shard metrics。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = house_parser(description="C1: reward effect-size ceiling (read-only)")
    ap.add_argument("--shards", default=DEFAULT_SHARDS)
    ap.add_argument("--out", default="tmp/p0-return/effect-ceiling.json")
    ap.add_argument("--md", default="tmp/p0-return/effect-ceiling.md")
    args = ap.parse_args()

    levers, meta = build_levers(args.shards)
    res = build_report(levers, meta)
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    s = res["summary"]
    print(f"✓ {out}")
    print(f"✓ {md}")
    print(
        f"  rows={s['rows']} below30={s['below_30']} "
        f"at_or_above30={s['at_or_above_30']} n/a={s['n_a']}"
    )
    return 0


if __name__ == "__main__":
    run_main(main)
