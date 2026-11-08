"""bc_window_cut.py — 按 pickup 窗口裁 v6 shard 的决策步（P2-B）。

设计见 `plan/w2-window-split.plan.md` §2：从 `z10.it233` 起、只用 pick 教师语料里
`metrics[:,40] pickupDist ∈ [0, max_dist]` 的步做 BC 微调。本工具只做**离线裁剪**：
不碰导出器、不碰训练器，产出的目录可直接给 `corpus.source=fixed`。

口径
  * 决策数组（obs/scalars/extra/a_*/lp_*/mask/done/returns/value）按掩码裁行；
  * `metrics.npy` 是 `[N+1, D]`：裁前 N 行、**保留末行终局**，重排成 `[n+1, D]`；
  * `manifest.nSamples` 改为裁后步数；`ticks/outcome/score` 不动（局时长与结局不变）；
  * 裁后步数 < `--min-steps` 的局**不产出**（默认 1）。
  * `pickupDist` 哨兵 `-1`（不可达）被排除；v16 起该列是 BFS 可达距离。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/bc_window_cut.py \
        --shards tmp/p0-c9/rollouts --out tmp/w2-split/pickup-only --max-dist 6
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.cli import house_parser, run_main
from tools.p0_common import find_shards

#: 决策步对齐且**必须存在**（BC 采集与 RL 导出器都写）。
REQUIRED_FILES = (
    "obs.npy",
    "scalars.npy",
    "extra.npy",
    "a_move.npy",
    "a_fire.npy",
    "a_hold.npy",
    "lp_move.npy",
    "lp_fire.npy",
    "lp_hold.npy",
    "mask.npy",
    "done.npy",
)
#: 只有 RL 导出器会写；BC 采集 shard 没有 ⇒ 存在才裁，不强制。
OPTIONAL_FILES = ("returns.npy", "value.npy")
METRICS = "metrics.npy"
MANIFEST = "manifest.json"
PICKUP_DIST = 40


def cut_one(
    src: str, dst: str, max_dist: int, min_steps: int, dry_run: bool = False
) -> tuple[int, int]:
    """裁一个 shard；返回 (裁前步数, 裁后步数)。裁后不足 min_steps 返回 (n, 0) 且不产出。"""
    metrics = np.load(os.path.join(src, METRICS))
    n = metrics.shape[0] - 1
    dist = metrics[:n, PICKUP_DIST]
    keep = (dist >= 0) & (dist <= int(max_dist))
    n_keep = int(keep.sum())
    if n_keep < max(1, min_steps) or dry_run:
        return n, n_keep

    os.makedirs(dst, exist_ok=True)
    for name in REQUIRED_FILES:
        p = os.path.join(src, name)
        if not os.path.exists(p):
            raise FileNotFoundError(f"{src} 缺 {name}（不是完整 v6 shard）")
        a = np.load(p)
        if a.shape[0] != n:
            raise ValueError(f"{name} 行数 {a.shape[0]} != nSamples {n}（{src}）")
        np.save(os.path.join(dst, name), a[keep])
    for name in OPTIONAL_FILES:
        p = os.path.join(src, name)
        if not os.path.exists(p):
            continue
        a = np.load(p)
        if a.shape[0] != n:
            raise ValueError(f"{name} 行数 {a.shape[0]} != nSamples {n}（{src}）")
        np.save(os.path.join(dst, name), a[keep])

    m = np.vstack([metrics[:n][keep], metrics[n][None, :]])
    np.save(os.path.join(dst, METRICS), m)

    man = json.load(open(os.path.join(src, MANIFEST), encoding="utf-8"))
    man["nSamples"] = n_keep
    man["window_cut"] = {"by": "pickupDist", "max_dist": int(max_dist), "from": n, "to": n_keep}
    with open(os.path.join(dst, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return n, n_keep


def main() -> int:
    ap = house_parser(description="按 pickup 窗口裁 v6 BC 语料")
    ap.add_argument("--shards", required=True, help="源目录（含 rl_s*_seed* 子目录）")
    ap.add_argument("--out", required=True, help="输出目录（新目录；已存在会被复用）")
    ap.add_argument("--max-dist", type=int, default=6, help="pickupDist 上界（格，默认 6）")
    ap.add_argument("--min-steps", type=int, default=1, help="裁后至少保留多少步（默认 1）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    args = ap.parse_args()

    shards = find_shards(args.shards, ("obs.npy", METRICS, MANIFEST))
    if not shards:
        print(f"[bc_window_cut] no v6 shards under {args.shards}", file=sys.stderr)
        return 2

    tot_n = tot_keep = kept = dropped = 0
    for src in shards:
        name = os.path.basename(src)
        dst = os.path.join(args.out, name)
        n, k = cut_one(src, dst, args.max_dist, args.min_steps)
        tot_n += n
        tot_keep += k
        if k > 0:
            kept += 1
        else:
            dropped += 1

    pct = tot_keep / max(1, tot_n) * 100
    print(f"[bc_window_cut] shards={len(shards)} kept={kept} dropped={dropped}")
    print(f"[bc_window_cut] steps {tot_n} -> {tot_keep} ({pct:.1f}%), max_dist={args.max_dist}")
    print(f"[bc_window_cut] out={args.out}{' (dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    run_main(main)
