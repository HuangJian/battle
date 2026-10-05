#!/usr/bin/env python
"""spatial-tower-parity.py — S0′-3：空间塔的 torch 侧规范式 golden 生成。

按 plan/policy-spatial-head.plan.md §3.3/§3.5 冻结口径构造塔：
  Conv2d(64→8, 1×1) + ReLU + adaptive_avg_pool2d(4) + Linear(128→112)
固定 seed 随机权重 + 随机 bufA，导出 JSON fixture（含 128 维分区均值与 112 维 FC 输出）。
TS 侧（`src/nn/spatial-tower.ts`）消费该 fixture 做逐值对账（容差 1e-4，与 goal-infer 同规）。

运行（写训练代码之前做，plan §4 Step 1）：
  bash tools/githook/nn-py-safe.sh nn-training/tools/spatial-tower-parity.py \
    --out tests/fixtures/spatial-tower-golden.json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # nn-training/

BOARD = 26
IN_CH = 64
C = 8
POOL = 4
FC_OUT = 112


def b64f32(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f4").tobytes()).decode("ascii")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/spatial-tower-golden.json")
    ap.add_argument("--seed", type=int, default=20261005)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    proj = nn.Conv2d(IN_CH, C, 1, bias=True)
    fc = nn.Linear(C * POOL * POOL, FC_OUT, bias=True)
    assert proj.weight is not None and proj.bias is not None and fc.weight is not None
    assert fc.bias is not None
    # 随机 bufA：量级贴近真实（主干 ReLU 输出，非负、均值 ~1）
    bufa = torch.randn(1, IN_CH, BOARD, BOARD) * 1.5
    bufa = F.relu(bufa)

    with torch.no_grad():
        z = F.relu(proj(bufa))  # (1,8,26,26)
        pooled = F.adaptive_avg_pool2d(z, POOL)  # (1,8,4,4)
        vec = pooled.flatten(1)  # (1,128) —— c*16 + row*4+col
        out = fc(vec)  # (1,112)

    fixture = {
        "format": "spatial-tower-golden",
        "version": 1,
        "seed": args.seed,
        "inCh": IN_CH,
        "board": BOARD,
        "c": C,
        "pool": POOL,
        "fcOut": FC_OUT,
        "projW": {"shape": [C, IN_CH], "data": b64f32(proj.weight.detach().numpy().reshape(C, IN_CH))},
        "projB": {"shape": [C], "data": b64f32(proj.bias.detach().numpy())},
        "fcW": {"shape": [FC_OUT, C * POOL * POOL], "data": b64f32(fc.weight.detach().numpy())},
        "fcB": {"shape": [FC_OUT], "data": b64f32(fc.bias.detach().numpy())},
        "bufA": {"shape": [IN_CH, BOARD, BOARD], "data": b64f32(bufa[0].numpy())},
        "regionVec": [float(v) for v in vec[0].tolist()],
        "expected": [float(v) for v in out[0].tolist()],
        "note": "Conv2d+ReLU+adaptive_avg_pool2d(4)+Linear(128,112)；TS 侧对账 ≤1e-4",
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parents[2] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    print(f"spatial-tower golden written: {out_path} (fc_out0={out[0,0]:.6f})")


if __name__ == "__main__":
    main()
