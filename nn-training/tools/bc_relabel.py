"""bc_relabel.py — 用教师权重重写 v6 shard 的动作标签（P2-B 的跨教师标注）。

**为什么需要它**（2026-11-08 现场）：`w2-pick-collect` 用 `bc-v6-train` 在 classic35
4 关 20000 拍上采了 200 局，结果**全 loss、`pickupDist≤6` 只有 623 步（1.4%）** ——
pick 教师在这个面板上基本不跑动，采不到「pickup 状态下的捡动作」。
而 `z10.it233` 的轨迹有 **10,069 步（6.3%）** pickup 窗口，只是动作不是「捡」。

⇒ 本工具把 **z10 的状态分布**保留，把 `a_*`（训练标签，见 `npyio.py:129,182`）
换成 **pick 教师的掩码 argmax**；`lp_*` 同步重写以保持两套一致。
BC 只学动作、不看胜负，所以 z10 语料的 100% loss 不影响。

口径
  * 前向输入 = shard 的 `obs/scalars/extra`；掩码 = `mask[:, 0:4/4:7/7:9]`；
    与 `tools/p0_teacher_agreement.py` 同一实现（复用其 `load_policy_model` /
    `masked_argmax`，不复制算法）。
  * 写 `a_move/a_fire/a_hold`（uint8）与 `lp_*`；其余文件（obs/scalars/extra/mask/
    done/metrics/value）**原样复制**；`returns.npy` 不存在则不造。
  * `manifest.json` 追加 `relabel` 块（教师 sha16、源目录、时间），其余字段不动。
  * 只读源、只写目标；同源多份 shard 按目录名去重不需要（源目录本身就是唯一）。

用法（仓库根）：
    bash tools/githook/nn-py-safe.sh nn-training/tools/bc_relabel.py \
        --shards tmp/p0-c9/rollouts \
        --weights nn-training/weights/bc-v6-train/weights.json \
        --out tmp/w2-split/relabel-pick
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.cli import house_parser, run_main
from tools.p0_common import find_shards
from tools.p0_teacher_agreement import load_policy_model, masked_argmax

#: 原样复制的文件（不含 a_*/lp_*，那两个由前向重写）。
COPY_FILES = ("obs.npy", "scalars.npy", "extra.npy", "mask.npy", "done.npy", "metrics.npy")
OPTIONAL_COPY = ("value.npy", "returns.npy")
MANIFEST = "manifest.json"


def relabel_shard(src: str, dst: str, model, *, batch: int = 1024) -> int:
    """重写一个 shard 的动作标签；返回步数。"""
    import torch

    meta = json.load(open(os.path.join(src, MANIFEST), encoding="utf-8"))
    obs = np.load(os.path.join(src, "obs.npy"), mmap_mode="r")
    scalars = np.load(os.path.join(src, "scalars.npy"), mmap_mode="r")
    extra = np.load(os.path.join(src, "extra.npy"), mmap_mode="r")
    mask = np.load(os.path.join(src, "mask.npy"), mmap_mode="r")
    n = int(obs.shape[0])
    if n <= 0:
        raise ValueError(f"{src}: 空 shard")
    if obs.shape[1:] != (int(model.in_ch), int(model.board), int(model.board)):
        raise ValueError(f"{src}: obs {obs.shape} != 模型输入")
    if scalars.shape != (n, int(model.scalar_dim)):
        raise ValueError(f"{src}: scalars {scalars.shape} != ({n},{model.scalar_dim})")
    if mask.shape[0] != n or mask.shape[1] < 9:
        raise ValueError(f"{src}: mask {mask.shape} 与 N={n} 不符")

    move = np.empty(n, dtype=np.uint8)
    fire = np.empty(n, dtype=np.uint8)
    hold = np.empty(n, dtype=np.uint8)
    with torch.no_grad():
        for i in range(0, n, batch):
            j = min(i + batch, n)
            o = torch.from_numpy(np.array(obs[i:j], dtype=np.float32, copy=True))
            s = torch.from_numpy(np.array(scalars[i:j], dtype=np.float32, copy=True))
            e = torch.from_numpy(np.array(extra[i:j], dtype=np.float32, copy=True))
            out = model(o, s, e)
            if len(out) < 3:
                raise ValueError(f"{src}: 模型输出不足三个头")
            move[i:j] = masked_argmax(out[0].detach().cpu().numpy(), mask[i:j, 0:4])
            fire[i:j] = masked_argmax(out[1].detach().cpu().numpy(), mask[i:j, 4:7])
            hold[i:j] = masked_argmax(out[2].detach().cpu().numpy(), mask[i:j, 7:9])

    os.makedirs(dst, exist_ok=True)
    for name in COPY_FILES:
        p = os.path.join(src, name)
        if not os.path.exists(p):
            raise FileNotFoundError(f"{src} 缺 {name}（不是完整 v6 shard）")
        shutil.copy2(p, os.path.join(dst, name))
    for name in OPTIONAL_COPY:
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dst, name))

    for name, arr in (("a_move", move), ("a_fire", fire), ("a_hold", hold)):
        np.save(os.path.join(dst, f"{name}.npy"), arr)
        # lp_* 与 a_* 保持同值：argmax 模式下「执行 = 标签」，训练读 a_*（npyio.py:129）
        np.save(os.path.join(dst, f"lp_{name.split('_', 1)[1]}.npy"), arr)

    meta["relabel"] = {
        "teacher": str(getattr(model, "_relabel_teacher", "")),
        "from": os.path.basename(os.path.abspath(src)),
        "nSamples": n,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(dst, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return n


def main() -> int:
    ap = house_parser(description="用教师权重重写 v6 shard 的动作标签")
    ap.add_argument("--shards", required=True)
    ap.add_argument("--weights", required=True, help="教师 v6 权重（三头 argmax）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=1024)
    args = ap.parse_args()

    shards = find_shards(args.shards, ("obs.npy", "mask.npy", MANIFEST))
    if not shards:
        print(f"[bc_relabel] no v6 shards under {args.shards}", file=sys.stderr)
        return 2
    model = load_policy_model(args.weights)
    model._relabel_teacher = os.path.basename(args.weights)  # 仅用于 manifest 记录

    total = kept = 0
    for src in shards:
        dst = os.path.join(args.out, os.path.basename(src))
        total += relabel_shard(src, dst, model, batch=args.batch)
        kept += 1
    print(f"[bc_relabel] shards={kept} steps={total} teacher={args.weights}")
    print(f"[bc_relabel] out={args.out}")
    return 0


if __name__ == "__main__":
    run_main(main)
