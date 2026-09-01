"""课程采样：每轮迭代采哪些 (stage, seed)。"""

from __future__ import annotations

import time
from typing import Any


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


def curriculum_active_count(order_len: int, it: int, start: int, every: int, grow: int) -> int:
    """课程模式第 `it` 轮激活的关卡数——(order_len, it) 的纯函数，断点续跑安全。

    确定性时间驱动扩展：每 `every` 轮 +`grow` 关，从 `start` 起步，封顶 `order_len`。
    `every <= 0` 表示不扩展（永远只用前 `start` 关）。
    """
    if every <= 0:
        return min(order_len, start)
    n = start + grow * ((it - 1) // every)
    return min(order_len, n)


def build_pairs(args: Any, it: int, rotate_seed: int) -> list[tuple[int, int]]:
    """Game pairs for iteration `it` — 纯函数 of (rotateSeed, it)，与调用顺序无关。

    2026-08-24 it5 重跑事故根因：旧实现从单一连续流按调用顺序抽签，重启后 it5
    复用了流头（= 旧 it3 的签）→ 与已落盘 shard 完全不相交 → 断点续跑剔除失效、
    整轮重跑 + 语料膨胀。现改为按 (rotateSeed, it) 派生独立流：permutation 按
    epoch 键控（同 epoch 内窗口平铺一个公共排列），seeds 按 it 键控（同一 it 跨
    重启逐字节一致）。

    课程模式（R6，2026-08-25）：--curriculum-stages 给出易→难有序关卡列表时启用，
    每轮只在激活窗口（前 N 关）内采样，N 随 it 确定性扩展（curriculum_active_count）。
    与 rotate 模式同款 (rotateSeed, it) 键控种子流，逐字节可复现、断点续跑剔除生效。
    """
    order = getattr(args, "curriculum_stages", "")
    if order:
        order_list = parse_range(order)
        n_active = curriculum_active_count(
            len(order_list),
            it,
            getattr(args, "curriculum_start", 4),
            getattr(args, "curriculum_every", 8),
            getattr(args, "curriculum_grow", 4),
        )
        active = order_list[:n_active]
        import numpy as np

        rng_draw = np.random.default_rng([rotate_seed, 0xC0E, it])
        draw = rng_draw.integers(1, 2**30, size=len(active) * args.seeds_per_stage)
        pairs = [
            (stage, int(draw[i * args.seeds_per_stage + j]))
            for i, stage in enumerate(active)
            for j in range(args.seeds_per_stage)
        ]
        print(
            f"[{time.strftime('%H:%M:%S')}] [run_rl] curriculum: it{it} active "
            f"{n_active}/{len(order_list)} stages={active} "
            f"(seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})"
        )
        return pairs
    if args.rotate_stages <= 0:
        base = parse_range(args.stages)
        # goal-nn（2026-08-30，用户指令）：显式模式 seed 轮转——--seed-rotate N 时
        # 每迭代对 --stages 的每个关卡抽 N 个全新 seed（(rotateSeed, it) 键控、
        # 断点复现）。固定 seeds 反复刷 = 记忆化过拟合（微课最差形态）；轮转后
        # 训练胜率自带泛化语义。N <= 0 退回固定 seeds 旧口径（逐字节不变）。
        rotate = int(getattr(args, "seed_rotate", 0) or 0)
        if rotate > 0:
            import numpy as np

            rng = np.random.default_rng([rotate_seed, 0x5EED, it])
            rotate_pairs: list[tuple[int, int]] = []
            for si in base:
                draws = rng.integers(1, 2**30, size=rotate)
                rotate_pairs.extend((si, int(d)) for d in draws)
            return rotate_pairs
        return [(si, sd) for si in base for sd in parse_range(args.seeds)]
    import numpy as np

    k = args.rotate_stages
    per_epoch = -(-args.total_stages // k)
    pos = (it - 1) % per_epoch
    epoch_idx = (it - 1) // per_epoch
    rng_perm = np.random.default_rng([rotate_seed, 0xA11CE, epoch_idx])
    perm = [int(s) for s in rng_perm.permutation(args.total_stages)]
    if pos == 0:
        print(
            f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: new epoch — permutation "
            f"{perm} split into {per_epoch} batches"
        )
    window = perm[pos * k : (pos + 1) * k]
    rng_draw = np.random.default_rng([rotate_seed, 0xB0B, it])
    draw = rng_draw.integers(1, 2**30, size=len(window) * args.seeds_per_stage)
    pairs = [
        (stage, int(draw[i * args.seeds_per_stage + j]))
        for i, stage in enumerate(window)
        for j in range(args.seeds_per_stage)
    ]
    print(
        f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: batch {pos + 1}/{per_epoch} "
        f"stages={window} (seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})"
    )
    return pairs
