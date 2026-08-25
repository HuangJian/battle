"""课程采样：每轮迭代采哪些 (stage, seed)。"""
from __future__ import annotations

import time


def parse_range(s: str) -> list[int]:
    """'0-3' / '0,2,5' / '0-1,4' → [0,1,2,3] / [0,2,5] / [0,1,4]."""
    out: list[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def build_pairs(args, it: int, rotate_seed: int) -> list[tuple[int, int]]:
    """Game pairs for iteration `it` — 纯函数 of (rotateSeed, it)，与调用顺序无关。

    2026-08-24 it5 重跑事故根因：旧实现从单一连续流按调用顺序抽签，重启后 it5
    复用了流头（= 旧 it3 的签）→ 与已落盘 shard 完全不相交 → 断点续跑剔除失效、
    整轮重跑 + 语料膨胀。现改为按 (rotateSeed, it) 派生独立流：permutation 按
    epoch 键控（同 epoch 内窗口平铺一个公共排列），seeds 按 it 键控（同一 it 跨
    重启逐字节一致）。
    """
    if args.rotate_stages <= 0:
        return [(si, sd) for si in parse_range(args.stages) for sd in parse_range(args.seeds)]
    import numpy as np

    k = args.rotate_stages
    per_epoch = -(-args.total_stages // k)
    pos = (it - 1) % per_epoch
    epoch_idx = (it - 1) // per_epoch
    rng_perm = np.random.default_rng([rotate_seed, 0xA11CE, epoch_idx])
    perm = [int(s) for s in rng_perm.permutation(args.total_stages)]
    if pos == 0:
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: new epoch — permutation "
              f"{perm} split into {per_epoch} batches")
    window = perm[pos * k:(pos + 1) * k]
    rng_draw = np.random.default_rng([rotate_seed, 0xB0B, it])
    draw = rng_draw.integers(1, 2 ** 30, size=len(window) * args.seeds_per_stage)
    pairs = [
        (stage, int(draw[i * args.seeds_per_stage + j]))
        for i, stage in enumerate(window)
        for j in range(args.seeds_per_stage)
    ]
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: batch {pos + 1}/{per_epoch} "
          f"stages={window} (seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})")
    return pairs
