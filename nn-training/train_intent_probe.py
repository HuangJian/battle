"""
train_intent_probe.py — M0b 意图可学习性前置探针（plan/Intent-Policy-NN-Plan.md §3.6）。

训【纯 intent-8 head 分类器】（复用 StudentNet 主干，无 prev-intent 注入），按
divergence-probe 三桶（base/combat/cruise）报告 acc vs majority 基线余量。
Q1 预注册门槛：任何桶余量 < 0.1 → 表达/时序缺口嫌疑升级，先改分段/词表/obs，
不许进入执行器投入；探针重试上限 3 轮。

数据：tools/sim/export-intent-labels.ts 产出的 shards/*/（obs/scalars/intent/bucket npy）。
train/val 按【局（shard）】切分——同局相邻帧不跨集，防时序泄漏高估。

用法（经启动器，勿裸跑 python）：
  powershell nn-training/start-training.ps1 -Script train_intent_probe.py \
      -ScriptArgs "--data tmp/intent-probe-hard/shards --out tmp/intent-probe-hard/probe-report.json"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch
import torch.nn as nn

from student_model import StudentNet, DEFAULT_H, DEFAULT_D
from intent_net import IntentNet, export_intent_weights

INTENT_IDS = ["INTERCEPT", "RETURN_DEFENSE", "HUNT", "HOLD_LANE", "CLEAR", "PICKUP", "CRUISE", "ESCAPE"]
BUCKET_NAMES = ["base", "combat", "cruise"]
MARGIN_GATE = 0.1  # 预注册 #16


class IntentProbeNet(IntentNet):
    """M5：IntentNet 全尺寸三头 + 注入；训练仅对 intent 头算 CE（enemy/anchor 头
    随机初始化保留导出——M6 之后由 M8/enemy-anchor 监督接管）。探针瘦身 h/d 亦支持。"""

    def intent_forward(self, obs: torch.Tensor, scalars: torch.Tensor, inject_vec=None) -> torch.Tensor:
        h = self.features(obs, scalars)
        if self.inject:
            if inject_vec is None:
                inject_vec = torch.zeros(h.shape[0], 9, device=h.device)
            h = torch.cat([h, inject_vec], dim=1)
        return self.intent_head(h)


def build_injection(seq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """从逐采样帧 intent 序列重建 (prev one-hot(8), duration 标量)。
    prev[i] = seq[i-1]（帧 0 用 zero）；duration[i] = 与 prev 同类的连续计数。"""
    n = len(seq)
    prev = np.zeros(n, dtype=np.int64)
    dur = np.ones(n, dtype=np.float32)
    for i in range(1, n):
        prev[i] = seq[i - 1]
        dur[i] = dur[i - 1] + 1 if seq[i] == seq[i - 1] else 1
    onehot = np.zeros((n, 8), dtype=np.float32)
    onehot[np.arange(n), prev] = 1.0
    return onehot, dur


def quota_sample(xi: np.ndarray, xo: np.ndarray, xs: np.ndarray, xb: np.ndarray,
                 quota: int, rng: np.random.RandomState):
    """P2-2 每类配额采样：每类至多 quota 帧（超类下采样、稀有类全留）。"""
    idx = []
    for c in range(8):
        ci = np.where(xi == c)[0]
        if len(ci) > quota:
            ci = rng.choice(ci, size=quota, replace=False)
        idx.append(ci)
    sel = np.concatenate(idx)
    rng.shuffle(sel)
    return xo[sel], xs[sel], xi[sel], xb[sel]


def quota_sample_priority(xi: np.ndarray, xo: np.ndarray, xs: np.ndarray, xb: np.ndarray,
                          xr: np.ndarray, quota: int, rng: np.random.RandomState,
                          priority_root: int):
    """B 臂配额采样（预注册 #20）：priority_root 帧优先保留，God-AI 帧补足每类配额。

    目的：人像黄金样本（人类守家分布）不因"每类配额随机采样"被按比例稀释——比例采样下
    人类帧只占各类的自然份额（合并语料人类仅 4.4%，quota 后 ~13%），达不到 #20 的
    ≥30% 训练混合比。本采样：每类先取全部 priority 帧（>quota 则随机下采样），
    God-AI 帧补足至 quota（不足则全取）→ 人类混合比显著抬升、人像分布保真。"""
    idx = []
    for c in range(8):
        ci = np.where(xi == c)[0]
        if len(ci) == 0:
            continue
        pri = ci[xr[ci] == priority_root]
        god = ci[xr[ci] != priority_root]
        if len(pri) >= quota:
            sel = rng.choice(pri, size=quota, replace=False)
        else:
            sel = list(pri)
            need = quota - len(sel)
            if len(god) > need:
                sel += list(rng.choice(god, size=need, replace=False))
            else:
                sel += list(god)
        idx.append(np.array(sel, dtype=np.int64))
    sel = np.concatenate(idx)
    rng.shuffle(sel)
    return xo[sel], xs[sel], xi[sel], xb[sel]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True, help="shards 目录（可多个根合并训练，M5-B 用：God-AI + 人像）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-train", type=int, default=60_000, help="训练帧上限（内存预算；60K ≈ 1 轮 ~15-20min CPU）")
    ap.add_argument("--val-shard-frac", type=float, default=0.2)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--inject", action="store_true", help="①.5 注入版（prev-intent+duration teacher-forced）")
    ap.add_argument("--quota", type=int, default=0, help="P2-2 每类配额采样帧数（0=不限）")
    ap.add_argument("--priority-root", type=int, default=-1,
                    help="B 臂（预注册 #20）：该 data 根索引的帧优先保留（人类黄金样本），"
                         "God-AI 帧补足每类配额；-1 = 关闭（A 臂比例采样）")
    ap.add_argument("--save", metavar="OUT", default="", help="M5：训练后保存全尺寸权重 JSON（intent_net 导出格式）")
    ap.add_argument("--h", type=int, default=DEFAULT_H, help="主干宽度（探针可瘦身；M5 全尺寸 64）")
    ap.add_argument("--d", type=int, default=DEFAULT_D)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = np.random.RandomState(args.seed)

    # ---- 索引 shards，按局切分 train/val（多数据根合并训练，shards[i] 为完整路径）----
    shards = []
    root_of: dict[str, int] = {}
    for ri, root in enumerate(args.data):
        root_of[root] = ri
        shards += [
            os.path.join(root, d)
            for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d)) and os.path.exists(os.path.join(root, d, "intent.npy"))
        ]
    shards = sorted(shards)
    if not shards:
        print(f"[probe] no shards under {args.data}", file=sys.stderr)
        sys.exit(1)
    # 每个 shard 的根索引（B 臂 priority-root 需要按根识别帧来源）。
    shard_roots = [root_of[os.path.dirname(d)] for d in shards]
    perm = rng.permutation(len(shards))
    n_val = max(1, int(len(shards) * args.val_shard_frac))
    val_idx = set(perm[:n_val].tolist())

    def load_split(indices) -> tuple[np.ndarray, ...]:
        obs_l, sc_l, in_l, bk_l, rt_l = [], [], [], [], []
        total = 0
        cap = args.max_train * 1.5 if args.max_train > 0 else 0
        for i in indices:
            base = shards[i]
            o = np.load(os.path.join(base, "obs.npy"))
            total += o.shape[0]
            if cap > 0 and total > cap:
                break
            obs_l.append(o)
            sc_l.append(np.load(os.path.join(base, "scalars.npy")))
            in_l.append(np.load(os.path.join(base, "intent.npy")))
            bk_l.append(np.load(os.path.join(base, "bucket.npy")))
            rt_l.append(np.full(o.shape[0], shard_roots[i], dtype=np.int8))
        return (
            np.concatenate(obs_l),
            np.concatenate(sc_l),
            np.concatenate(in_l).astype(np.int64),
            np.concatenate(bk_l).astype(np.int64),
            np.concatenate(rt_l).astype(np.int64),
        )

    val_shards = [i for i in range(len(shards)) if i in val_idx]
    train_shards = [i for i in range(len(shards)) if i not in val_idx]
    xo, xs, xi, xb, xr = load_split(train_shards)

    # 先按混合上限子采样、再按类配额采样（P2-2，配额 ≪ 上限时以配额为准）。
    if args.max_train > 0 and len(xi) > args.max_train:
        sel = rng.choice(len(xi), size=args.max_train, replace=False)
        xo, xs, xi, xb, xr = xo[sel], xs[sel], xi[sel], xb[sel], xr[sel]
    if args.quota > 0:
        if args.priority_root >= 0:
            xo, xs, xi, xb = quota_sample_priority(xi, xo, xs, xb, xr, args.quota, rng, args.priority_root)
        else:
            xo, xs, xi, xb = quota_sample(xi, xo, xs, xb, args.quota, rng)

    vo, vs_, vi, vb, _vr = load_split(val_shards)

    # 注入特征（teacher-forced）：per-shard 序列重建在做子采样之前不可行，
    # 这里用全局重建近似——探针仅测"加上时序特征是否显著提升"。
    train_inj = None
    val_inj = None
    if args.inject:
        t_inj_oh, t_inj_dur = build_injection(xi)
        v_inj_oh, v_inj_dur = build_injection(vi)
        train_inj = np.concatenate([t_inj_oh, t_inj_dur[:, None]], axis=1).astype(np.float32)
        val_inj = np.concatenate([v_inj_oh, v_inj_dur[:, None]], axis=1).astype(np.float32)

    print(
        f"[probe] shards={len(shards)} (train {len(train_shards)} / val {len(val_shards)}) "
        f"trainFrames={len(xi)} valFrames={len(vi)} inject={args.inject} quota={args.quota}",
        file=sys.stderr,
    )
    print(f"[probe] label dist train={np.bincount(xi, minlength=8).tolist()} "
          f"val={np.bincount(vi, minlength=8).tolist()}", file=sys.stderr)

    dev = torch.device("cpu")
    model = IntentProbeNet(inject=args.inject, h=args.h, d=args.d).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    ce = nn.CrossEntropyLoss()

    to = torch.from_numpy(xo)
    ts = torch.from_numpy(xs)
    ti = torch.from_numpy(xi)
    t_inj_t = torch.from_numpy(train_inj) if train_inj is not None else None
    v_inj_t = torch.from_numpy(val_inj) if val_inj is not None else None
    n = len(xi)

    for ep in range(args.epochs):
        model.train()
        order = torch.randperm(n)
        tot_loss, correct = 0.0, 0
        for b in range(0, n, args.batch):
            idx = order[b : b + args.batch]
            opt.zero_grad()
            logits = model.intent_forward(to[idx], ts[idx], t_inj_t[idx] if t_inj_t is not None else None)
            loss = ce(logits, ti[idx])
            loss.backward()
            opt.step()
            tot_loss += float(loss) * len(idx)
            correct += int((logits.argmax(1) == ti[idx]).sum())
        print(f"[probe] epoch {ep + 1}/{args.epochs} loss={tot_loss / n:.4f} trainAcc={correct / n:.4f}",
              file=sys.stderr)

    # M5：保存全尺寸权重（intent_net 导出格式，含主干 shape 断言 + 三头）。
    if args.save:
        export_intent_weights(model, args.save)
        print(f"[probe] weights saved -> {args.save}", file=sys.stderr)

    # ---- 评估：overall + 三桶 acc vs majority 余量 ----
    model.eval()
    preds = []
    with torch.no_grad():
        for b in range(0, len(vi), 1024):
            logits = model.intent_forward(
                torch.from_numpy(vo[b : b + 1024]),
                torch.from_numpy(vs_[b : b + 1024]),
                v_inj_t[b : b + 1024] if v_inj_t is not None else None,
            )
            preds.append(logits.argmax(1).numpy())
    pred = np.concatenate(preds)

    report: dict = {
        "gate": "any-bucket margin < 0.1 -> fail (prereg #16)",
        "marginGate": MARGIN_GATE,
        "shards": len(shards),
        "trainFrames": int(len(xi)),
        "valFrames": int(len(vi)),
        "epochs": args.epochs,
        "inject": args.inject,
        "quota": args.quota,
        "overall": {"acc": float((pred == vi).mean())},
        "buckets": {},
        "confusion8x8": np.zeros((8, 8), dtype=int).tolist(),
    }
    cm = np.zeros((8, 8), dtype=int)
    for t, p in zip(vi, pred):
        cm[t][p] += 1
    report["confusion8x8"] = cm.tolist()

    for bk in range(3):
        m = vb == bk
        if m.sum() == 0:
            continue
        y, yh = vi[m], pred[m]
        counts = np.bincount(y, minlength=8)
        majority = float(counts.max()) / len(y)
        acc = float((y == yh).mean())
        report["buckets"][BUCKET_NAMES[bk]] = {
            "frames": int(m.sum()),
            "acc": acc,
            "majorityAcc": majority,
            "margin": acc - majority,
            "pass": bool(acc - majority >= MARGIN_GATE),
            "majorityClass": INTENT_IDS[int(counts.argmax())],
            "perClassTrue": counts.tolist(),
        }

    report["gatePass"] = all(b["pass"] for b in report["buckets"].values())

    # M5 gate：类级 recall vs majority（§18 修订口径）——非多数类 recall 必须显著 >0。
    perClassRecall = {}
    perClassMajority = {}
    total = len(vi)
    for c in range(8):
        n_c = int((vi == c).sum())
        perClassRecall[INTENT_IDS[c]] = float((pred[vi == c] == c).sum() / n_c) if n_c else None
        perClassMajority[INTENT_IDS[c]] = n_c / total
    report["perClassRecall"] = perClassRecall
    report["perClassShare"] = perClassMajority

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps({k: v for k, v in report.items() if k != "confusion8x8"}, indent=2))
    print(f"[probe] gate {'PASS' if report['gatePass'] else 'FAIL'} -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
