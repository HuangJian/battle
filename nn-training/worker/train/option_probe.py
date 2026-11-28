"""
option_probe.py — P′-4a 前置：option 槽位（3 / 6）top-1 可学习性探针 + PPO init 导出。

问题：P′-4a 要用 3 类 option（+ pathMode 修饰）做选择层；先确认「从当前 obs 预测
tagger 的 option 标签」在 held-out 局上 top-1 ≥ 65%（预注册 p4.top1Acc）。标签 =
机械 tagger 的 8 意图经**预注册映射**（plan/hier-options-prereg.v1.json
p4.option3Mapping）折叠：

  HUNT            ← HUNT / CLEAR / PICKUP / CRUISE
  INTERCEPT_BASE  ← INTERCEPT / RETURN_DEFENSE
  HOLD            ← HOLD_LANE / ESCAPE

`--slots 6` 再把 option3 展开成（option × pathMode）联合槽位（预注册 p4 的
pathMode 维度；CLEAR 是唯一 break 标签，HOLD_break 恒非法 ⇒ 掩码第 5 槽）：

  0 HUNT_direct        1 HUNT_break        2 INTERCEPT_BASE_direct
  3 INTERCEPT_BASE_break 4 HOLD_direct     5 HOLD_break（mask=0）

与 M0b 意图探针同口径：StudentNet 主干 + linear head(slots)，**无 prev-option 注入**
（Q1）；train/val 按局（shard）切分；报告 overall / 三桶（base/combat/cruise）acc vs
majority、per-class recall、混淆。数据来自 `tools/sim/export-intent-labels.ts` 的 shards。

`--save` 把训练好的头搬进 IntentNet 格式（intentDim=slots、injectDim=slots+1、
with_value=True，注入列零初始化）导出为 PPO init 权重——PPO 侧 `arch.intentDim`
即槽位数的单一来源。

用法（经启动器，勿裸跑 python）：
  bun dashboard/src/launch/cli.ts --script worker/train/option_probe.py \
      --data tmp/option-probe-hard/shards --out tmp/option-probe-hard/probe-report.json
  bun dashboard/src/launch/cli.ts --script worker/train/option_probe.py \
      --data tmp/option-probe-hard-700/shards --slots 6 \
      --out tmp/option-probe-hard-700/probe6.json \
      --save tmp/option-probe-hard-700/init-option6.json
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

# 6 槽 = option3 × pathMode（direct/break）。CLEAR 是唯一 break 标签源；HOLD_break
# 恒非法（L0：不为拆墙走向基地锚点，预注册 mask 第 5 槽 = 0）。
OPTION6_SLOT_IDS = [
    "HUNT_direct",
    "HUNT_break",
    "INTERCEPT_BASE_direct",
    "INTERCEPT_BASE_break",
    "HOLD_direct",
    "HOLD_break",
]
OPTION6_DIM = len(OPTION6_SLOT_IDS)
# INTENT_IDS 顺序 → slot：INTERCEPT 2 / RETURN_DEFENSE 2 / HUNT 0 / HOLD_LANE 4 /
# CLEAR 1 / PICKUP 0 / CRUISE 0 / ESCAPE 4。
OPTION6_MAP = np.array([2, 2, 0, 4, 1, 0, 0, 4], dtype=np.int64)
OPTION6_MASK = np.array([1, 1, 1, 1, 1, 0], dtype=np.int64)
# slot → option3（把 6 槽预测折叠后与预注册 top-1 口径对账）。
SLOT_TO_OPTION3 = np.array([0, 0, 1, 1, 2, 2], dtype=np.int64)


class OptionProbeNet(StudentNet):
    """StudentNet 主干 + option 线性头（无注入；槽位数 = `--slots`）。"""

    def __init__(self, spec: NetSpec, slots: int = OPTION_DIM):
        super().__init__(spec)
        self.slots = slots
        self.option_head = nn.Linear(self.head_hidden, slots)

    def option_logits(self, obs: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        # nn.Module.__call__ 存根返回 Any（多态 forward），显式收窄为本函数契约。
        logits: torch.Tensor = self.option_head(self.features(obs, scalars))
        return logits


def _option_parser():
    ap = house_parser()
    ap.add_argument("--data", nargs="+", required=True, help="shards 目录（可多个根合并）")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--slots",
        type=int,
        choices=[3, 6],
        default=OPTION_DIM,
        help="3 = option3（预注册 top-1 门）；6 = option×pathMode 联合槽（PPO init 用）",
    )
    ap.add_argument(
        "--save",
        default="",
        help="导出 PPO init 权重（IntentNet JSON：intentDim=slots、with_value=True）",
    )
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
        counts = np.bincount(y, minlength=model.slots).astype(np.float64)
        # 只对实际出现的类归一（HOLD_break 等恒空槽不参与训练）；3 槽时与旧
        # `len(y) / (3 * max(counts,1))` 逐字相同。
        present = max(1, int((counts > 0).sum()))
        w = len(y) / (present * np.maximum(counts, 1.0))
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


def _build_option_report(
    y,
    pred,
    bucket,
    args,
    shards,
    n_train,
    *,
    ids=OPTION_IDS,
    slots=OPTION_DIM,
) -> dict:
    cm = np.zeros((slots, slots), dtype=int)
    for t, p in zip(y, pred, strict=True):
        cm[t][p] += 1
    counts = np.bincount(y, minlength=slots)
    majority = float(counts.max()) / len(y)
    acc = float((pred == y).mean())
    vocab = "option3" if slots == OPTION_DIM else "option6"
    report: dict = {
        "vocab": vocab,
        "mapping": {
            "HUNT": ["HUNT", "CLEAR", "PICKUP", "CRUISE"],
            "INTERCEPT_BASE": ["INTERCEPT", "RETURN_DEFENSE"],
            "HOLD": ["HOLD_LANE", "ESCAPE"],
        },
        "gate": {"top1Acc": TOP1_GATE, "metric": "option3（6 槽时折叠后）"},
        "slots": ids,
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
        },
        "labelCounts": counts.tolist(),
        "buckets": {},
        "perClassRecall": {},
        "perClassShare": {},
    }
    for bk in range(len(BUCKET_NAMES)):
        m = bucket == bk
        if int(m.sum()) == 0:
            continue
        yy, pp = y[m], pred[m]
        cc = np.bincount(yy, minlength=slots)
        bmaj = float(cc.max()) / len(yy)
        bacc = float((yy == pp).mean())
        report["buckets"][BUCKET_NAMES[bk]] = {
            "frames": int(m.sum()),
            "acc": bacc,
            "majorityAcc": bmaj,
            "margin": bacc - bmaj,
            "majorityClass": ids[int(cc.argmax())],
        }
    per_class_recall = {}
    for c in range(slots):
        n_c = int((y == c).sum())
        per_class_recall[ids[c]] = float((pred[y == c] == c).sum() / n_c) if n_c else None
    report["perClassRecall"] = per_class_recall
    report["perClassShare"] = {ids[c]: float(counts[c]) / len(y) for c in range(slots)}
    recalls = [v for v in per_class_recall.values() if v is not None]
    report["balancedAcc"] = float(sum(recalls) / len(recalls)) if recalls else 0.0
    if slots == OPTION_DIM:
        report["confusion3x3"] = cm.tolist()
        report["overall"]["pass"] = bool(acc >= TOP1_GATE)
    else:
        report["confusion6x6"] = cm.tolist()
        # 预注册 top-1 门的分母是 option3 标签：6 槽预测折叠后与 option3 真值对账
        # （pathMode 错不影响 option 层正确性）。
        y3 = SLOT_TO_OPTION3[y]
        p3 = SLOT_TO_OPTION3[pred]
        report["option3Acc"] = float((y3 == p3).mean())
        report["overall"]["pass"] = bool(report["option3Acc"] >= TOP1_GATE)
    report["gatePass"] = bool(report["overall"]["pass"])
    return report


def _save_init(model: OptionProbeNet, args) -> None:
    """把 option 头搬进 IntentNet（PPO init）：主干逐键复制，注入列零初始化。

    - `arch.intentDim = slots` / `injectDim = slots + 1` ⇒ 训练/推理两侧的唯一维度源；
    - value / enemy / anchor 头随机初始化：PPO init 模式会保留 value 随机并由
      value_warmup 学基线（未用到的目标头不产梯度）。
    """
    from worker.models.intent_net import IntentNet, export_intent_weights

    net = IntentNet(
        NetSpec(h=args.h, d=args.d),
        inject=True,
        with_value=True,
        intent_dim=model.slots,
    )
    trunk = {k: v for k, v in model.state_dict().items() if not k.startswith("option_head.")}
    net.load_state_dict(trunk, strict=False)
    with torch.no_grad():
        net.intent_head.weight.zero_()
        net.intent_head.weight[:, : model.head_hidden] = model.option_head.weight
        net.intent_head.bias.copy_(model.option_head.bias)
    os.makedirs(os.path.dirname(args.save) or ".", exist_ok=True)
    export_intent_weights(net, args.save)
    log(
        f"[option-probe] init weights -> {args.save} "
        f"(intentDim={model.slots} injectDim={model.slots + 1})"
    )


def main() -> None:
    args = _option_parser().parse_args()
    torch.manual_seed(args.seed)
    rng = np.random.RandomState(args.seed)
    slots = int(args.slots)
    ids = OPTION_IDS if slots == OPTION_DIM else OPTION6_SLOT_IDS
    label_map = OPTION3_MAP if slots == OPTION_DIM else OPTION6_MAP

    shards, shard_roots, train_shards, val_shards = _index_shards(
        args.data, rng, args.val_shard_frac
    )
    train_split = _load_split(shards, train_shards, shard_roots, args.max_train)
    val_split = _load_split(shards, val_shards, shard_roots, args.max_train)

    # 8 → slots（只映射标签；obs/scalars/bucket 原样）。
    train_y = label_map[train_split.intent]
    val_y = label_map[val_split.intent]

    xo, xs, y = train_split.obs, train_split.scal, train_y
    if args.max_train > 0 and len(y) > args.max_train:
        sel = rng.choice(len(y), size=args.max_train, replace=False)
        xo, xs, y = xo[sel], xs[sel], y[sel]

    log(
        f"[option-probe] slots={slots} shards={len(shards)} "
        f"(train {len(train_shards)} / val {len(val_shards)}) "
        f"trainFrames={len(y)} valFrames={len(val_y)}"
    )
    log(
        f"[option-probe] label dist train={np.bincount(y, minlength=slots).tolist()} "
        f"val={np.bincount(val_y, minlength=slots).tolist()}"
    )

    model = OptionProbeNet(NetSpec(h=args.h, d=args.d), slots=slots).to(torch.device("cpu"))
    _fit_option(model, xo, xs, y, args)
    pred = _predict_option(model, val_split.obs, val_split.scal)

    report = _build_option_report(
        val_y,
        pred,
        val_split.bucket,
        args,
        shards,
        n_train=len(y),
        ids=ids,
        slots=slots,
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log(
        f"[option-probe] top1={report['overall']['acc']:.4f} "
        f"majority={report['overall']['majorityAcc']:.4f} "
        f"balancedAcc={report['balancedAcc']:.4f}"
    )
    if slots != OPTION_DIM:
        log(f"[option-probe] option3(collapsed)={report['option3Acc']:.4f}")
    log(
        f"[option-probe] gate {'PASS' if report['gatePass'] else 'FAIL'} -> {args.out}"
    )
    if args.save:
        _save_init(model, args)


if __name__ == "__main__":
    run_main(main)
