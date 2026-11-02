#!/usr/bin/env python
"""e2a_readout.py — E2-A it150 接线读数：`--ablate-ch` 批量聚合 + stem 新列 L2。

为什么单独成器（plan/r5-next.plan.md §3.4 的接线闸）：
`export-eval-game` 的 `--ablate-ch` 是**逐局**探针；闸要的是「同批权重下 ch16 消融的
翻转率 ≥ 地形通道 ch0 的 1/3」。本工具按课程真实关卡与 `EVAL_SEEDS` 前缀批量跑局、
聚合 `ablate.groups`，并读 `stem.weight` 第 16 列（v7 新通道）的 L2。

用法（必须走 nn-py-safe.sh；每局 spawn 一个 `export-eval-game`）：
  bash tools/githook/nn-py-safe.sh nn-training/tools/e2a_readout.py \
    --weights <it150 weights.json> \
    --course nn-training/curricula/z4-nav.jsonc \
    --games-per-stage 50 --parallel 4
默认 50 局/关（200 局）；判决点建议 200 局/关（800 局，按 4 并发约 20–40 min）。
产物：`tmp/e2a-readout/<weights stem>/`（逐局 `_eval_report.json` + `readout.json`）。
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nn-training"))

from biz.course_resolve import load_course
from common.cli import house_parser, run_main
from common.proc import run_capture
from worker.eval_track import EVAL_SEEDS

#: 闸的分母 / 阳性对照（plan §3.4）：ch16 翻转率 ≥ ch0 的 1/3。
CONTROL_CH = 0
TARGET_CH = 16
GATE_RATIO = 1.0 / 3.0


def stem_new_col_l2(meta: dict[str, Any]) -> dict[str, Any]:
    """读 `params["stem.weight"]`，返回 v7 新通道列（`c-3`，v7 的 ch16）的 L2。

    v7 stem = `[h, 19, 3, 3]`（17 obs + 2 coord）⇒ 新 obs 通道列 = `c-3 = 16`。
    旧 18ch 档（`c=18`）也能算，但 `new_col_l2` 只是列 15 的读数，不用于 v7 判决。
    """
    entry = (meta.get("params") or {}).get("stem.weight")
    if not entry:
        raise ValueError("weights 缺 params['stem.weight']（不是学生模型权重档）")
    shape = list(entry["shape"])
    if len(shape) != 4:
        raise ValueError(f"stem.weight 形状 {shape} 不是 [h, c, k, k]")
    h, c, k1, k2 = shape
    if c < 3:
        raise ValueError(f"stem.weight 通道数 {c} 太小")
    arr = np.frombuffer(base64.b64decode(entry["data"]), dtype="<f4").reshape(shape)
    new_idx = c - 3
    col = arr[:, new_idx, :, :]
    l2 = float(np.sqrt(np.sum(col.astype(np.float64) ** 2)))
    # 旧 obs 列 = 0..c-4（v7: 0..15）；new 列 = c-3；最后两列 coord。
    old_mean = float(
        np.mean(
            [
                np.sqrt(np.sum(arr[:, i, :, :].astype(np.float64) ** 2))
                for i in range(c - 3)
            ]
        )
    )
    return {
        "shape": shape,
        "new_col_index": new_idx,
        "new_col_l2": l2,
        "old_obs_cols_mean_l2": old_mean,
    }


def aggregate_ablate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐局 `ablate` 段聚合成逐通道读数与闸判定（缺 `ablate` 的局不入账）。"""
    per: dict[str, dict[str, float]] = {}
    decisions = 0
    games = 0
    for r in reports:
        ab = r.get("ablate")
        if not ab:
            continue
        games += 1
        decisions += int(ab.get("decisions") or 0)
        for name, g in (ab.get("groups") or {}).items():
            a = per.setdefault(
                name,
                {"flipsMove": 0, "flipsFire": 0, "flipsHold": 0, "dMove": 0.0, "dFire": 0.0, "dValue": 0.0},
            )
            for k in ("flipsMove", "flipsFire", "flipsHold"):
                a[k] += int(g.get(k) or 0)
            for k in ("dMove", "dFire", "dValue"):
                a[k] += float(g.get(k) or 0.0)
    denom = max(1, decisions)
    groups = {}
    for name, a in sorted(per.items(), key=lambda kv: kv[0]):
        groups[name] = {
            **{k: a[k] for k in ("flipsMove", "flipsFire", "flipsHold")},
            "moveFlipRate": a["flipsMove"] / denom,
            "fireFlipRate": a["flipsFire"] / denom,
            "holdFlipRate": a["flipsHold"] / denom,
            "dMovePerDecision": a["dMove"] / denom,
            "dFirePerDecision": a["dFire"] / denom,
            "dValuePerDecision": a["dValue"] / denom,
        }
    control = groups.get(f"ch{CONTROL_CH}")
    target = groups.get(f"ch{TARGET_CH}")
    positive = control["moveFlipRate"] if control else 0.0
    mine = target["moveFlipRate"] if target else 0.0
    ratio = (mine / positive) if positive > 0 else None
    if positive <= 0 or target is None:
        verdict = "invalid"
        why = "阳性对照 ch0 的 move 翻转率为 0（同批权重下消融不生效，闸无意义）"
    elif ratio is not None and ratio >= GATE_RATIO:
        verdict = "pass"
        why = f"ch16/ch0 = {ratio:.3f} ≥ {GATE_RATIO:.3f}"
    else:
        verdict = "fail"
        why = f"ch16/ch0 = {ratio:.3f} < {GATE_RATIO:.3f}"
    return {
        "games_with_ablate": games,
        "decisions": decisions,
        "groups": groups,
        "verdict": {"result": verdict, "reason": why, "gate_ratio": GATE_RATIO},
    }


def _run_one(
    bun: str,
    weights: Path,
    course: Any,
    stage: Any,
    stage_idx: int,
    seed: int,
    ablate: str,
    out_dir: Path,
) -> dict[str, Any]:
    """跑一局（`export-eval-game`），读回 `_eval_report.json`；失败返回 error 行。"""
    stage_json = json.dumps(stage.model_dump(), ensure_ascii=False, separators=(",", ":"))
    cmd = [
        bun,
        "tools/sim/export-eval-game.ts",
        "--stage",
        str(2000 + stage_idx),
        "--seed",
        str(seed),
        "--max-ticks",
        str(int(course.max_ticks or 20000)),
        "--policy",
        "nn",
        "--weights",
        str(weights),
        "--stage-json",
        stage_json,
        "--difficulty",
        str(course.difficulty or "hard"),
        "--lives-override",
        str(int(course.player.lives)),
        "--player-level",
        str(int(course.player.level)),
        "--ablate-ch",
        ablate,
        "--out",
        str(out_dir),
    ]
    p = run_capture(cmd, cwd=ROOT, timeout=180)
    report = out_dir / "_eval_report.json"
    if p.returncode != 0 or not report.is_file():
        return {
            "stage": 2000 + stage_idx,
            "seed": seed,
            "error": (p.stderr or p.stdout or "no report").strip()[-400:],
        }
    try:
        d: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"stage": 2000 + stage_idx, "seed": seed, "error": f"report 解析失败: {e}"}
    d["stage"] = 2000 + stage_idx
    d["seed"] = seed
    return d


def main() -> None:
    ap = house_parser(description="E2-A it150 接线读数（ablate-ch 批量 + stem 新列 L2）")
    ap.add_argument("--weights", required=True, help="it150 nn-weights JSON（schema 7）")
    ap.add_argument("--course", default="nn-training/curricula/z4-nav.jsonc")
    ap.add_argument("--games-per-stage", type=int, default=50, help="每关局数（EVAL_SEEDS 前缀）")
    ap.add_argument("--ablate-ch", default=f"{CONTROL_CH},{TARGET_CH}", help="逗号分隔的通道号")
    ap.add_argument("--parallel", type=int, default=4, help="并发局数（本机 CPU 核 ≤）")
    ap.add_argument("--out", default="", help="产物目录；缺省 tmp/e2a-readout/<weights stem>")
    args = ap.parse_args()
    if args.games_per_stage < 1 or args.games_per_stage > len(EVAL_SEEDS):
        raise SystemExit(f"拒绝：--games-per-stage 必须在 1..{len(EVAL_SEEDS)}")
    if args.parallel < 1:
        raise SystemExit("拒绝：--parallel 必须 ≥1")
    weights = Path(args.weights)
    if not weights.is_absolute():
        weights = ROOT / weights
    if not weights.is_file():
        raise SystemExit(f"拒绝：weights 文件不存在 {weights}")
    meta = json.loads(weights.read_text(encoding="utf-8"))
    arch_in = (meta.get("arch") or {}).get("in_ch")
    if arch_in is not None and int(arch_in) != 17:
        raise SystemExit(f"拒绝：weights arch.in_ch={arch_in}，不是 v7（17 obs 通道）")
    course = load_course(ROOT / args.course if not Path(args.course).is_absolute() else args.course)
    if not course.stages:
        raise SystemExit("拒绝：课程没有解析出 stages")
    out_dir = (
        Path(args.out)
        if args.out
        else ROOT / "tmp" / "e2a-readout" / weights.stem
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    bun = shutil.which("bun") or "bun"
    jobs = [
        (si, seed, out_dir / f"{2000 + si}-{seed}")
        for si in range(len(course.stages))
        for seed in EVAL_SEEDS[: args.games_per_stage]
    ]
    for _, _, d in jobs:
        d.mkdir(parents=True, exist_ok=True)
    print(
        f"[e2a-readout] weights={weights.name} course={course.name} "
        f"stages={len(course.stages)} games={len(jobs)} ablate={args.ablate_ch} parallel={args.parallel}"
    )
    reports: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futs = {
            ex.submit(
                _run_one,
                bun,
                weights,
                course,
                course.stages[si],
                si,
                seed,
                args.ablate_ch,
                d,
            ): (si, seed)
            for si, seed, d in jobs
        }
        for f in as_completed(futs):
            r = f.result()
            reports.append(r)
            done += 1
            if done % 20 == 0 or done == len(jobs):
                print(f"[e2a-readout] {done}/{len(jobs)}")
    errors = [r for r in reports if r.get("error")]
    good = [r for r in reports if not r.get("error")]
    agg = aggregate_ablate(good)
    stem = stem_new_col_l2(meta)
    result = {
        "weights": str(weights),
        "course": str(ROOT / args.course) if not Path(args.course).is_absolute() else args.course,
        "ablate_ch": args.ablate_ch,
        "games_per_stage": args.games_per_stage,
        "stage_ids": [2000 + i for i in range(len(course.stages))],
        "games_run": len(reports),
        "games_ok": len(good),
        "errors": errors,
        "ablate": agg,
        "stem": stem,
    }
    readout = out_dir / "readout.json"
    readout.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[e2a-readout] games={len(good)}/{len(reports)} decisions={agg['decisions']} "
        f"ch{CONTROL_CH}.moveFlip={agg['groups'].get(f'ch{CONTROL_CH}', {}).get('moveFlipRate', 0):.4f} "
        f"ch{TARGET_CH}.moveFlip={agg['groups'].get(f'ch{TARGET_CH}', {}).get('moveFlipRate', 0):.4f} "
        f"ge={agg['verdict']['result']} ({agg['verdict']['reason']})"
    )
    print(
        f"[e2a-readout] stem col {stem['new_col_index']} L2={stem['new_col_l2']:.6g} "
        f"(old obs cols mean L2={stem['old_obs_cols_mean_l2']:.6g})"
    )
    print(f"[e2a-readout] → {readout}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    run_main(main)
