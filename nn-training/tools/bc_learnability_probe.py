#!/usr/bin/env python3
"""bc_learnability_probe.py — 人类 BC 语料可学性探针（S4）。

回答的是**分流问题**：这批 obs/scalars 能不能学出人类的动作分布？
（不是“开 demo 腿会不会赚”——那是 plan §4 的短腿读数。）

纪律（plan/y22-phaseB-demo-data.plan.md §3-S4）：
  * **按局切 80/20**：≥2 shards 走 `plan_shard_split`（与 `make_loaders` 同一实现），
    不按样本切；
  * **masked CE**：与 PPO demo 项 `_demo_masked_ce` 同数学（非法类 -1e9、单合法类跳过），
    **不做 fire 正例加权**（S5 的 demo 项也不加权）；
  * 指标 = held-out 全局 top-1 + per-class recall + 预测分布 + 多数类基线；
  * 产出权重 JSON 供 `tools/sim/eval-course-ckpt.ts` 跑 24 局常量策略哨兵。

用法（仓根；训练入口走 launcher，别裸 python）：
    bun dashboard/src/launch/cli.ts --script tools/bc_learnability_probe.py -- \
        --data-dir nn-training/data/human-x20-corpus-v5 \
        --epochs 20 --val-split 0.2 --arch student \
        --out tmp/y22-bc-probe/weights.json --report tmp/y22-bc-probe/report.json
"""

from __future__ import annotations

import copy
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

NN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NN_ROOT))

from common.cli import house_parser, run_main
from common.schema import FIRE_DIM, MOVE_DIM
from worker.data.dataset import NNDataset, _AugWrapper
from worker.data.npyio import load_dataset, scan_shards
from worker.data.shard_split import plan_shard_split
from worker.data.weights_io import save_weights_json
from worker.models.core import NNPolicy
from worker.models.student import StudentNet
from worker.train.bc import _masked_ce


def _repo_path(raw: str) -> Path:
    """相对路径一律按**仓根**解析（launcher 的 cwd = nn-training/，不能靠 cwd 猜）。"""
    p = Path(raw)
    return p if p.is_absolute() else NN_ROOT.parent / p


def _split_indices(
    data: dict[str, np.ndarray], n_shards: int, val_split: float, seed: int
) -> tuple[list[int], list[int], str]:
    """按 shard 切（≥2 shards），单 shard 回退样本级（与 make_loaders 同判据）。"""
    n = int(data["obs"].shape[0])
    n_val = int(n * val_split)
    gen = torch.Generator().manual_seed(seed)
    if n_shards >= 2:
        shard_ids = data["shard_ids"]
        perm = torch.randperm(n_shards, generator=gen).tolist()
        tr_idx, va_idx = plan_shard_split(shard_ids, n_val, perm)
        return tr_idx.tolist(), va_idx.tolist(), "shard"
    sample_perm = torch.randperm(n, generator=gen)
    n_val = max(0, min(n - 1, n_val))
    return sample_perm[n_val:].tolist(), sample_perm[:n_val].tolist(), "sample"


def _confusion(
    logits: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
    n_classes: int,
    *,
    keep_all: bool = False,
) -> torch.Tensor:
    """合法类掩码混淆矩阵。

    keep_all=False：与 `_masked_acc` 同 keep 语义（单合法类样本跳过）。
    keep_all=True：**全部样本**都进矩阵（仍用 mask 做 argmax）——fire 头的可学习
    子集在冷却期只剩 release 一类，若只看 masked keep，hold 只占 1.84%，recall
    会被这个筛选口径误导（见 plan §3-S4 的 fire 口径说明）。
    """
    m = mask > 0
    keep = torch.ones_like(m[:, 0], dtype=torch.bool) if keep_all else m.sum(dim=-1) >= 2
    if not bool(keep.any()):
        return torch.zeros((n_classes, n_classes), dtype=torch.int64)
    z = logits.masked_fill(~m, -1e9)
    pred = z.argmax(dim=-1)
    idx = target[keep].to(torch.int64) * n_classes + pred[keep].to(torch.int64)
    return torch.bincount(idx, minlength=n_classes * n_classes).reshape(n_classes, n_classes)


def _metrics(cm: torch.Tensor) -> dict[str, Any]:
    total = int(cm.sum().item())
    diag = int(torch.diag(cm).sum().item())
    row = cm.sum(dim=1)
    recall = [
        (float(cm[i, i].item()) / float(row[i].item())) if int(row[i].item()) else None
        for i in range(int(cm.shape[0]))
    ]
    majority = (float(row.max().item()) / float(total)) if total else 0.0
    pred = cm.sum(dim=0)
    pred_share = [float(x.item()) / float(total) if total else 0.0 for x in pred]
    return {
        "n": total,
        "top1": (float(diag) / float(total)) if total else 0.0,
        "recall": recall,
        "majority": majority,
        "true_share": [float(x.item()) / float(total) if total else 0.0 for x in row],
        "pred_share": pred_share,
        "confusion": cm.tolist(),
    }


def _evaluate(
    model: torch.nn.Module, val_dl: DataLoader[Any], dev: torch.device
) -> dict[str, Any]:
    model.eval()
    move_cm = torch.zeros((MOVE_DIM, MOVE_DIM), dtype=torch.int64)
    fire_cm = torch.zeros((FIRE_DIM, FIRE_DIM), dtype=torch.int64)
    fire_all_cm = torch.zeros((FIRE_DIM, FIRE_DIM), dtype=torch.int64)
    with torch.no_grad():
        for obs, sc, ex, mv, fr, mm, mf, _ret in val_dl:
            obs = obs.to(dev)
            sc = sc.to(dev)
            ex = ex.to(dev)
            mv = mv.to(dev)
            fr = fr.to(dev)
            if isinstance(model, StudentNet):
                lm, lf = model(obs, sc, ex)
            elif isinstance(model, NNPolicy):
                lm, lf = model(obs, sc)
            else:
                raise TypeError(f"unsupported model: {type(model).__name__}")
            move_cm += _confusion(lm, mv, mm.to(dev), MOVE_DIM)
            fire_cm += _confusion(lf, fr, mf.to(dev), FIRE_DIM)
            fire_all_cm += _confusion(lf, fr, mf.to(dev), FIRE_DIM, keep_all=True)
    return {
        "move": _metrics(move_cm),
        "fire": _metrics(fire_cm),
        "fire_all": _metrics(fire_all_cm),
    }


def main() -> None:
    ap = house_parser(description="人类 BC 语料可学性探针（S4）")
    ap.add_argument("--data-dir", required=True, help="v5 npy shard 目录")
    ap.add_argument("--arch", choices=["student", "bc"], default="student")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--val-split", type=float, default=0.2)
    ap.add_argument("--mirror-p", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out", default="tmp/y22-bc-probe/weights.json")
    ap.add_argument("--report", default="tmp/y22-bc-probe/report.json")
    args = ap.parse_args()

    t0 = time.time()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    dev = torch.device(args.device)

    data_dir = str(_repo_path(args.data_dir))
    shards = scan_shards(data_dir)
    if not shards:
        raise SystemExit(f"no shards under {data_dir}")
    names = [Path(s).name for s in shards]
    data = load_dataset(data_dir)
    tr_idx, va_idx, split_kind = _split_indices(data, len(shards), args.val_split, args.seed)
    if not tr_idx or not va_idx:
        raise SystemExit(f"split 为空（train={len(tr_idx)} val={len(va_idx)}）")

    full = NNDataset(data, augment=False)
    train_ds = _AugWrapper(data, tr_idx, args.mirror_p, args.seed)
    val_ds = Subset(full, va_idx)
    train_dl = DataLoader(
        train_ds,
        batch_size=args.batch,
        shuffle=True,
        num_workers=0,
        generator=torch.Generator().manual_seed(args.seed + 1),
    )
    val_dl = DataLoader(val_ds, batch_size=args.batch, shuffle=False, num_workers=0)
    val_shards = sorted({names[int(x)] for x in np.unique(data["shard_ids"][va_idx])})
    print(
        f"[probe] shards={len(shards)} split={split_kind} "
        f"train={len(tr_idx)} val={len(va_idx)} val_shards={len(val_shards)}",
        flush=True,
    )

    model: torch.nn.Module = StudentNet() if args.arch == "student" else NNPolicy()
    model.to(dev)
    # 与 worker/train/bc.py 同优化器纪律（AdamW + cosine + grad clip 1.0）：
    # 裸 Adam 在 raw-uint8 输入上会一步炸 logits（实测单 shard loss 36）。
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, args.epochs))
    best_score = -1.0
    best_state: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []

    for ep in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        nb = 0
        for obs, sc, ex, mv, fr, mm, mf, _ret in train_dl:
            obs = obs.to(dev)
            sc = sc.to(dev)
            ex = ex.to(dev)
            mv = mv.to(dev)
            fr = fr.to(dev)
            mm = mm.to(dev)
            mf = mf.to(dev)
            if isinstance(model, StudentNet):
                lm, lf = model(obs, sc, ex)
            elif isinstance(model, NNPolicy):
                lm, lf = model(obs, sc)
            else:
                raise TypeError(f"unsupported model: {type(model).__name__}")
            loss = _masked_ce(lm, mv, mm) + _masked_ce(lf, fr, mf)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss.item())
            nb += 1
        scheduler.step()
        met = _evaluate(model, val_dl, dev)
        score = met["move"]["top1"] + met["fire"]["top1"]
        if score > best_score:
            best_score = score
            best_state = copy.deepcopy(model.state_dict())
        history.append(
            {
                "epoch": ep,
                "train_loss": total / max(1, nb),
                "move_top1": met["move"]["top1"],
                "fire_top1": met["fire"]["top1"],
            }
        )
        print(
            f"[probe] ep{ep}/{args.epochs} loss={total / max(1, nb):.4f} "
            f"move={met['move']['top1']:.4f} fire={met['fire']['top1']:.4f}",
            flush=True,
        )

    assert best_state is not None
    model.load_state_dict(best_state)
    final = _evaluate(model, val_dl, dev)
    out = _repo_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_weights_json(model, str(out), extra_meta={"probe": "bc-learnability", "epochs": args.epochs})
    report = {
        "data_dir": data_dir,
        "arch": args.arch,
        "seed": args.seed,
        "epochs": args.epochs,
        "batch": args.batch,
        "lr": args.lr,
        "val_split": args.val_split,
        "mirror_p": args.mirror_p,
        "split": split_kind,
        "shards": len(shards),
        "train_samples": len(tr_idx),
        "val_samples": len(va_idx),
        "val_shards": val_shards,
        "move": final["move"],
        "fire": final["fire"],
        "fire_all": final["fire_all"],
        "history": history,
        "weights": str(out),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    rp = _repo_path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"[probe] final move top1={final['move']['top1']:.4f} "
        f"(majority {final['move']['majority']:.4f}) "
        f"fire(masked) top1={final['fire']['top1']:.4f} "
        f"fire(all) top1={final['fire_all']['top1']:.4f} "
        f"hold recall(all)={final['fire_all']['recall'][1]}",
        flush=True,
    )
    print(f"[probe] weights -> {out}\n[probe] report -> {rp}", flush=True)


if __name__ == "__main__":
    run_main(main)
