"""
gen_self_inj.py — 生成 self-feed 注入特征（scheduled sampling 数据源，P1-2）。

背景（M5 gate 四必报项）：teacher 60.3% vs self-feed 47.4% (gap 12.8pp)——训练用
teacher-forced 注入（prev/dur 从真值标签重建），运行时自喂模型预测 → 注入分布不匹配。
scheduled sampling 的**离线数据增强实现**：用给定权重模型对每个 shard 做 self-feed
滚动（逐帧以模型自身预测为 prev/dur 注入），产出 self_inj.npy（N×9：prev one-hot(8)+dur）。
训练时按 ε 概率混合 teacher（真值 prev）与 self（自喂 prev）注入，让模型见过真实
self-feed 输入分布，收敛 gap。成本 = 一次模型推理（CPU 秒级/千帧）。

用法（经启动器，勿裸跑 python）：
  powershell nn-training/start-training.ps1 -Script gen_self_inj.py \
    -ScriptArgs "--data tmp/intent-probe-hard/shards tmp/human-obs --weights tmp/intent-weights-Bp.json"
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import torch
from intent_net import IntentNet
from weights_io import load_weights_json

D_MAX = 300.0


def build_model(meta: dict, params: dict) -> IntentNet:
    arch = meta.get("arch", {})
    model = IntentNet(
        inject=True,
        h=arch.get("h", 64),
        d=arch.get("d", 8),
        head_hidden=arch.get("head_hidden", 128),
    )
    model.load_state_dict(params, strict=False)
    model.eval()
    return model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True, help="shards 根（多根合并）")
    ap.add_argument("--weights", required=True, help="已导出意图权重 JSON")
    args = ap.parse_args()

    meta, params = load_weights_json(args.weights)
    model = build_model(meta, params)

    shards = []
    for root in args.data:
        shards += [
            os.path.join(root, d)
            for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d))
            and os.path.exists(os.path.join(root, d, "intent.npy"))
        ]
    shards = sorted(shards)

    done = 0
    with torch.no_grad():
        for base in shards:
            out_p = os.path.join(base, "self_inj.npy")
            if os.path.exists(out_p):
                done += 1
                continue
            obs = np.load(os.path.join(base, "obs.npy"))
            scalars = np.load(os.path.join(base, "scalars.npy"))
            n = len(obs)
            if n == 0:
                np.save(out_p, np.zeros((0, 9), dtype=np.float32))
                continue

            h = model.features(torch.from_numpy(obs), torch.from_numpy(scalars))
            hc = torch.from_numpy(np.ascontiguousarray(h.numpy(), dtype=np.float32))

            # 逐帧 self-feed：预分配 torch 注入张量（避免每帧 from_numpy/cat/unsqueeze 的
            # torch dispatch 开销——原实现逐帧 head 前向 ~2-3ms，2204 shards 需 ~1.5h）。
            inj = torch.zeros(n, 9, dtype=torch.float32)
            inj[0, 8] = 1.0  # dur=1, prev 全零（首帧语义）
            prev_c, dur_c = 0, 1
            for t in range(n):
                if t > 0:
                    inj[t].zero_()
                    inj[t, prev_c] = 1.0
                    inj[t, 8] = min(dur_c, D_MAX)
                logit = model.intent_head(torch.cat([hc[t : t + 1], inj[t : t + 1]], dim=1))
                p = int(logit.argmax(1)[0])
                if p == prev_c:
                    dur_c += 1
                else:
                    dur_c = 1
                prev_c = p

            np.save(out_p, inj.numpy())
            done += 1

    print(f"[gen-self-inj] shards={len(shards)} done={done} -> self_inj.npy under each shard")


if __name__ == "__main__":
    main()
