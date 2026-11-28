"""
option_probe.py — P′-4a 前置：option3（HUNT / INTERCEPT_BASE / HOLD）top-1 可学习性探针。

问题：P′-4a 要用 3 类 option 做选择层；先确认「从当前 obs 预测 tagger 的 option 标签」
在 held-out 局上 top-1 ≥ 65%（预注册 p4.top1Acc）。标签 = 机械 tagger 的 8 意图经
**预注册映射**（plan/hier-options-prereg.v1.json p4.option3Mapping）折叠：

  HUNT            ← HUNT / CLEAR / PICKUP / CRUISE
  INTERCEPT_BASE  ← INTERCEPT / RETURN_DEFENSE
  HOLD            ← HOLD_LANE / ESCAPE

与 M0b 意图探针同口径：StudentNet 主干 + linear head(3)，**无 prev-option 注入**（Q1）；
train/val 按局（shard）切分；报告 overall / 三桶（base/combat/cruise）acc vs majority、
per-class recall、3×3 混淆。数据来自 `tools/sim/export-intent-labels.ts` 的 shards。

用法（经启动器，勿裸跑 python）：
  bun dashboard/src/launch/cli.ts --script worker/train/option_probe.py \
      --data tmp/option-probe-hard/shards --out tmp/option-probe-hard/probe-report.json
"""

from __future__ import annotations

import json
import os

import numpy as np
import torch
import torch.nn as nn

from common.cli import house_parser, run_main
from common.log import log
from worker.models.arch_meta import NetSpec
from worker.models.student import DEFAULT_D, DEFAULT_H, StudentNet
from worker.train.intent_probe import _index_shards, _load_split

OPTION_IDS = ["HUNT", "INTERCEPT_BASE", "HOLD"]
OPTION_DIM = len(OPTION_IDS)
BUCKET_NAMES = ["base", "combat", "cruise"]
TOP1_GATE = 0.65

# 8 意图 → option3（顺序 = vocab.INTENT_IDS：INTERCEPT / RETURN_DEFENSE / HUNT /
# HOLD_LANE / CLEAR / PICKUP / CRUISE / ESCAPE）。与预注册 p4.option3Mapping 同表。
INTENT_IDS = [
    "INTERCEPT",
    "RETURN_DEFENSE",
    "HUNT",
    "HOLD_LANE",
    "CLEAR",
    "PICKUP",
    "CRUISE",
    "ESCAPE",
]
OPTION3_MAP = np.array([1, 1, 0, 2, 0, 0, 0, 2], dtype=np.int64)


class OptionProbeNet(StudentNet):
    """StudentNet 主干 + option3 线性头（无注入）。"""

    def __init__(self, spec: NetSpec):
        super().__init__(spec)
        self.option_head = nn.Linear(self.head_hidden, OPTION_DIM)

    def option_logits(self, obs: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        # nn.Module.__call__ 存根返回 Any（多态 forward），显式收窄为本函数契约。
        logits: torch.Tensor = self.option_head(self.features(obs, scalars))
        return logits


def _option_parser():
    ap = house_parser()
    ap.add_argument("--data", nargs="+", required=True, help="shards 目录（可多个根合并）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-train", type=int, default=60_000)
    ap.add_argument("--val-shard-frac", type=float, default=0.2)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument(
        "--class-weight",
        choices=["none", "balanced"],
        default="balanced",
        help="balanced = 逆类频 CE 权重（HUNT 占 98%，不加权必坍缩到全 HUNT）",
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--h", type=int, default=DEFAULT_H)
    ap.add_argument("--d", type=int, default=DEFAULT_D)
    return ap


def _fit_option(model: OptionProbeNet, obs, scal, y, args) -> None:
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    if args.class_weight == "balanced":
        counts = np.bincount(y, minlength=OPTION_DIM).astype(np.float64)
        w = len(y) / (OPTION_DIM * np.maximum(counts, 1.0))
        weight = torch.tensor(w, dtype=torch.float32)
    else:
        weight = None
    ce = nn.CrossEntropyLoss(weight=weight)
    to = torch.from_numpy(obs)
    ts = torch.from_numpy(scal)
    ty = torch.from_numpy(y)
    n = len(y)
    for ep in range(args.epochs):
        model.train()
        order = torch.randperm(n)
        tot_loss, correct = 0.0, 0
        for b in range(0, n, args.batch):
            idx = order[b : b + args.batch]
            opt.zero_grad()
            logits = model.option_logits(to[idx], ts[idx])
            loss = ce(logits, ty[idx])
            loss.backward()
            opt.step()
            tot_loss += float(loss) * len(idx)
            correct += int((logits.argmax(1) == ty[idx]).sum())
        log(
            f"[option-probe] epoch {ep + 1}/{args.epochs} "
            f"loss={tot_loss / n:.4f} trainAcc={correct / n:.4f}"
        )


def _predict_option(model: OptionProbeNet, obs, scal) -> np.ndarray:
    model.eval()
    preds = []
    with torch.no_grad():
        for b in range(0, len(obs), 1024):
            logits = model.option_logits(
                torch.from_numpy(obs[b : b + 1024]),
                torch.from_numpy(scal[b : b + 1024]),
            )
            preds.append(logits.argmax(1).numpy())
    return np.concatenate(preds)


def _build_option_report(y, pred, bucket, args, shards, n_train) -> dict:
    cm = np.zeros((OPTION_DIM, OPTION_DIM), dtype=int)
    for t, p in zip(y, pred, strict=True):
        cm[t][p] += 1
    counts = np.bincount(y, minlength=OPTION_DIM)
    majority = float(counts.max()) / len(y)
    acc = float((pred == y).mean())
    report: dict = {
        "vocab": "option3",
        "mapping": {
            "HUNT": ["HUNT", "CLEAR", "PICKUP", "CRUISE"],
            "INTERCEPT_BASE": ["INTERCEPT", "RETURN_DEFENSE"],
            "HOLD": ["HOLD_LANE", "ESCAPE"],
        },
        "gate": {"top1Acc": TOP1_GATE},
        "shards": len(shards),
        "trainFrames": n_train,
        "valFrames": len(y),
        "epochs": args.epochs,
        "classWeight": args.class_weight,
        "h": args.h,
        "d": args.d,
        "overall": {
            "acc": acc,
            "majorityAcc": majority,
            "margin": acc - majority,
            "pass": bool(acc >= TOP1_GATE),
        },
        "labelCounts": counts.tolist(),
        "buckets": {},
        "confusion3x3": cm.tolist(),
    }
    for bk in range(len(BUCKET_NAMES)):
        m = bucket == bk
        if int(m.sum()) == 0:
            continue
        yy, pp = y[m], pred[m]
        cc = np.bincount(yy, minlength=OPTION_DIM)
        bmaj = float(cc.max()) / len(yy)
        bacc = float((yy == pp).mean())
        report["buckets"][BUCKET_NAMES[bk]] = {
            "frames": int(m.sum()),
            "acc": bacc,
            "majorityAcc": bmaj,
            "margin": bacc - bmaj,
            "majorityClass": OPTION_IDS[int(cc.argmax())],
        }
    per_class_recall = {}
    for c in range(OPTION_DIM):
        n_c = int((y == c).sum())
        per_class_recall[OPTION_IDS[c]] = (
            float((pred[y == c] == c).sum() / n_c) if n_c else None
        )
    report["perClassRecall"] = per_class_recall
    report["perClassShare"] = {
        OPTION_IDS[c]: float(counts[c]) / len(y) for c in range(OPTION_DIM)
    }
    recalls = [v for v in per_class_recall.values() if v is not None]
    report["balancedAcc"] = float(sum(recalls) / len(recalls)) if recalls else 0.0
    report["gatePass"] = bool(report["overall"]["pass"])
    return report


def main() -> None:
    args = _option_parser().parse_args()
    torch.manual_seed(args.seed)
    rng = np.random.RandomState(args.seed)

    shards, shard_roots, train_shards, val_shards = _index_shards(
        args.data, rng, args.val_shard_frac
    )
    train_split = _load_split(shards, train_shards, shard_roots, args.max_train)
    val_split = _load_split(shards, val_shards, shard_roots, args.max_train)

    # 8 → 3（只映射标签；obs/scalars/bucket 原样）。
    train_y = OPTION3_MAP[train_split.intent]
    val_y = OPTION3_MAP[val_split.intent]

    xo, xs, y = train_split.obs, train_split.scal, train_y
    if args.max_train > 0 and len(y) > args.max_train:
        sel = rng.choice(len(y), size=args.max_train, replace=False)
        xo, xs, y = xo[sel], xs[sel], y[sel]

    log(
        f"[option-probe] shards={len(shards)} (train {len(train_shards)} / val {len(val_shards)}) "
        f"trainFrames={len(y)} valFrames={len(val_y)}"
    )
    log(
        f"[option-probe] label dist train={np.bincount(y, minlength=OPTION_DIM).tolist()} "
        f"val={np.bincount(val_y, minlength=OPTION_DIM).tolist()}"
    )

    model = OptionProbeNet(NetSpec(h=args.h, d=args.d)).to(torch.device("cpu"))
    _fit_option(model, xo, xs, y, args)
    pred = _predict_option(model, val_split.obs, val_split.scal)

    report = _build_option_report(
        val_y, pred, val_split.bucket, args, shards, n_train=len(y)
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log(
        f"[option-probe] top1={report['overall']['acc']:.4f} "
        f"majority={report['overall']['majorityAcc']:.4f} "
        f"balancedAcc={report['balancedAcc']:.4f}"
    )
    log(
        f"[option-probe] gate {'PASS' if report['gatePass'] else 'FAIL'} -> {args.out}"
    )


if __name__ == "__main__":
    run_main(main)
