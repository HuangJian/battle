"""init_scratch_weights.py — 纯从零 RL 的合理随机初始化（goal-nn 卡 A2/A5）。

背景（2026-08-30 校准实测，docs/goal-nn.progress.md §7）：StudentNet 的
kaiming-uniform init 在 8 层残差 ConvMixer 上复合放大（输入 0..255 无归一，
trunk 隐层激活 ~1000）——两个后果：
  ① 策略头 logits 随机初始化即 ±2000 ⇒ 采样分布 one-hot、熵 ≈ 0，无探索；
  ② 更新不稳：即使 grad-norm clip=1.0、lr=3e-4，value 梯度经 trunk 扰动被
     放大成 logits 百纳特级漂移（实测 it1 kl=11930 ⇒ 策略一步坍缩成确定性，
     之后 kl=0 完全僵死——熵 0 的策略没有策略梯度信号，自我锁定）。
历史 68-iter 训练从未踩到这颗地雷——它从 BC 权重热启动（训练过的权重激活
量级正常）。本脚本 = **工作流级** scratch init（不改共享 student_model.py，
BC / 历史路径零影响）。

缩放依据（正齐次性：Conv/Linear 对 (w,x)→(αw,αx) 的输出恰好 α 倍，ReLU 保持，
故"测一次、按比例缩"是精确的，不是近似）：
  trunk (stem/blocks/fc 的 weight+bias) ×0.1  ⇒ 隐层激活 ~O(10)
  move/fire head ×0.01                        ⇒ logits ~O(0.1)，近均匀（可探索）
  value head ×0.1                             ⇒ V(s) ~O(1)，与回报同量级
  ⇒ 每 grad step 的 |Δlogp| ≈ clip×lr×|h|×√fan ≈ 0.03 纳特 ⇒ 更新温和。

用法（经统一启动器跑）：
  ./nn-training/start-training.sh --script init_scratch_weights.py --out <path>.json
然后 run_rl.py --bc <path>.json（--bc 是"首个 init 来源"通道；权重已存在则
run_rl 原样续跑，语义不变）。
"""

from __future__ import annotations

import argparse
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.weights_io import save_weights_json
from ppo.engine import build_ppo

TRUNK_SCALE = 0.1
HEAD_SCALE = 0.01
VALUE_SCALE = 0.1

TRUNK_PREFIXES = ("stem.", "blocks.", "fc.")
HEADS = ("move_head.weight", "fire_head.weight")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tmp/scratch-init/weights.json")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    model = build_ppo(None)

    with torch.no_grad():
        for n, p in model.named_parameters():
            if n.startswith(TRUNK_PREFIXES):
                p.mul_(TRUNK_SCALE)
            elif n in HEADS:
                p.mul_(HEAD_SCALE)
            elif n.startswith("value_head."):
                p.mul_(VALUE_SCALE)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    save_weights_json(model, args.out)

    # 自检（零 obs 前向）：logits 必须近均匀、value 必须与回报同量级。
    obs = torch.zeros(1, 14, 26, 26, dtype=torch.uint8)
    sc = torch.zeros(1, 19)
    with torch.no_grad():
        mv, fr, val = model(obs, sc)
    mmax = float(mv.abs().max())
    fmax = float(fr.abs().max())
    vmax = float(val.abs().max())
    print(
        f"[init_scratch] saved {args.out} (trunk×{TRUNK_SCALE} heads×{HEAD_SCALE} value×{VALUE_SCALE})"
    )
    print(
        f"[init_scratch] |move_logits|max={mmax:.4f} |fire_logits|max={fmax:.4f} |value|={vmax:.4f}"
    )
    # logits ±0.5 ⇒ softmax p_max ≈ 0.30（5 动作近均匀）；value ≈ 回报量级。
    assert mmax < 0.5 and fmax < 0.5, "policy logits too large — near-deterministic start"
    assert vmax < 10.0, "value head far from return scale — GAE bootstrap will explode"


if __name__ == "__main__":
    main()
