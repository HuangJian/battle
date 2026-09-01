"""
eval_intent_m5.py — M5 gate 四必报项评估（plan/Intent-Policy-NN-Plan.md §7 M5 gate / §3.6 P2-4）。

在训练后对【已导出权重】在验证集上计算四项诊断（acc 是诊断量，放行仍看 WIN gate）：
1. teacher vs self-feed acc gap（Q2/P2-4）：逐 shard 时序序列重建注入——teacher = 真值 prev，
   self = 模型自预测 prev 滚动自举。gap 大 ⇒ 训练/推理注入分布不匹配（E2E 自举风险）。
   self-feed 技巧：注入只作用 FC 后头层，主干 features 先整批缓存 h(128)，再逐帧线性头前向
   （86K 帧 ≈ 秒级，而非 86K 次全前向）。
2. prev ±3 tick 扰动鲁棒（Q5/预注册 #17）：teacher prev 错位 ±1/±2/±3 tick 的 acc 坍塌量。
3. 守家桶误差方向矩阵（Q5）：base 桶 8×8 混淆 + 安全级误判率（守家类
   INTERCEPT/RETURN_DEFENSE/HOLD_LANE ↔ 进攻/巡游类 HUNT/CLEAR/PICKUP/CRUISE 互错）>5% 报警。
4. 路由错配率（P2-4）：预测≠真值 且 ACTIVATION_MATRIX 激活头集合不同（src/ai/intent/vocab.ts
   镜像，下方 ACTIVATION 常量）的帧占比——错配 ⇒ 执行器走错误白名单。

用法（经启动器，勿裸跑 python）：
  powershell nn-training/start-training.ps1 -Script eval_intent_m5.py \
    -ScriptArgs "--data tmp/intent-probe-hard/shards --weights tmp/intent-weights-A.json --out tmp/probe-M5-A-gate.json"
  # B 臂（多根合并，与训练同 --data）：
  -ScriptArgs "--data tmp/intent-probe-hard/shards tmp/human-obs --weights tmp/intent-weights-B.json --out tmp/probe-M5-B-gate.json"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch

from data.weights_io import load_weights_json
from models.intent_net import IntentNet

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
BUCKET_NAMES = ["base", "combat", "cruise"]
SAFETY_GATE = 0.05  # 守家桶安全级误判阈值（Q5）
D_MAX = 300.0  # 时长归一化上限（预注册 #11，与训练一致的口径）

# ACTIVATION_MATRIX 镜像（src/ai/intent/vocab.ts）：(enemy, anchor) 头激活集合。
ACTIVATION: dict[str, tuple[int, int]] = {
    "INTERCEPT": (1, 0),
    "RETURN_DEFENSE": (0, 1),
    "HUNT": (1, 0),
    "HOLD_LANE": (0, 1),
    "CLEAR": (0, 0),
    "PICKUP": (0, 0),
    "CRUISE": (1, 1),
    "ESCAPE": (0, 0),
}
DEFENSIVE = {"INTERCEPT", "RETURN_DEFENSE", "HOLD_LANE"}
AGGRESSIVE = {"HUNT", "CLEAR", "PICKUP", "CRUISE"}


def seq_features(seq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(prev one-hot (n,8), duration 标量 (n,1))，逐 shard 时序重建（frame 0 = zero prev + dur 1）。"""
    n = len(seq)
    prev = np.zeros(n, dtype=np.int64)
    dur = np.ones(n, dtype=np.float32)
    for i in range(1, n):
        prev[i] = seq[i - 1]
        dur[i] = dur[i - 1] + 1 if seq[i] == seq[i - 1] else 1
    onehot = np.zeros((n, 8), dtype=np.float32)
    onehot[np.arange(n), prev] = 1.0
    return onehot, dur


def shift_prev(seq: np.ndarray, k: int) -> np.ndarray:
    """prev 错位：prev[t] = seq[t-1+k]（越界帧置 0 语义 = 无信息 prev）。"""
    n = len(seq)
    prev = np.zeros(n, dtype=np.int64)
    for i in range(n):
        j = i - 1 + k
        if 0 <= j < n:
            prev[i] = seq[j]
    return prev


def build_model(meta: dict, params: dict) -> IntentNet:
    arch = meta.get("arch", {})
    model = IntentNet(
        inject=True,
        h=arch.get("h", 64),
        d=arch.get("d", 8),
        head_hidden=arch.get("head_hidden", 128),
    )
    # strict=False：容忍 value/enemy/anchor 头缺失/形状差异（intent 评估只需主干 + intent_head）。
    model.load_state_dict(params, strict=False)
    model.eval()
    return model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", required=True, help="shards 根（与训练同，多根合并）")
    ap.add_argument("--weights", required=True, help="已导出的意图权重 JSON（intent_net 导出格式）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--val-shard-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7, help="与训练同 seed 复刻 val 切分（A 臂默认 7）")
    args = ap.parse_args()

    # ---- 与 train_intent_probe 一致的 shard 索引 + val 切分 ----
    shards = []
    for root in args.data:
        shards += [
            os.path.join(root, d)
            for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d))
            and os.path.exists(os.path.join(root, d, "intent.npy"))
        ]
    shards = sorted(shards)
    rng = np.random.RandomState(args.seed)
    perm = rng.permutation(len(shards))
    n_val = max(1, int(len(shards) * args.val_shard_frac))
    val_idx = set(perm[:n_val].tolist())
    val_shards = [i for i in range(len(shards)) if i in val_idx]
    print(
        f"[m5gate] shards={len(shards)} val={len(val_shards)} weights={args.weights}",
        file=sys.stderr,
    )

    meta, params = load_weights_json(args.weights)
    model = build_model(meta, params)

    # 累加器
    conf_teach = np.zeros((8, 8), dtype=np.int64)  # teacher-feed 全量 8×8
    conf_self = np.zeros((8, 8), dtype=np.int64)
    conf_base = np.zeros((8, 8), dtype=np.int64)  # base 桶 8×8
    n_base = 0
    n_route_mismatch = 0
    n_route_preddiff = 0
    n_total = 0
    n_self_correct = 0
    n_teach_correct = 0
    # 扰动 acc（k=-3..3，0 = teacher 无扰动）
    pert = {k: [0, 0] for k in range(-3, 4)}  # k -> [correct, total]

    with torch.no_grad():
        for si in val_shards:
            base = shards[si]
            obs = np.load(os.path.join(base, "obs.npy"))
            scalars = np.load(os.path.join(base, "scalars.npy"))
            intent = np.load(os.path.join(base, "intent.npy")).astype(np.int64)
            bucket = np.load(os.path.join(base, "bucket.npy")).astype(np.int64)
            n = len(intent)
            if n == 0:
                continue
            n_total += n

            # 主干整批缓存 h（注入无关）
            h = model.features(torch.from_numpy(obs), torch.from_numpy(scalars))  # (n, 128)
            hc = torch.from_numpy(np.ascontiguousarray(h.numpy(), dtype=np.float32))

            # ---- teacher-feed ----
            prev, dur = seq_features(intent)
            inj = np.concatenate([prev, np.minimum(dur, D_MAX)[:, None]], axis=1).astype(np.float32)
            logits = model.intent_head(torch.cat([hc, torch.from_numpy(inj)], dim=1))
            pred_t = logits.argmax(1).numpy()
            conf_teach += np.bincount(intent * 8 + pred_t, minlength=64).reshape(8, 8)
            n_teach_correct += int((pred_t == intent).sum())

            # ---- self-feed（逐帧线性头，注入 = 自预测序列）----
            pred_s = np.zeros(n, dtype=np.int64)
            prev_c, dur_c = 0, 1
            inj0 = np.zeros(9, dtype=np.float32)
            inj0[8] = 1.0  # dur=1, prev 全零（frame 0 语义）
            for t in range(n):
                if t == 0:
                    inj_t = inj0
                else:
                    inj_t = np.zeros(9, dtype=np.float32)
                    inj_t[prev_c] = 1.0
                    inj_t[8] = min(dur_c, D_MAX)
                logit = model.intent_head(torch.cat([hc[t], torch.from_numpy(inj_t)]).unsqueeze(0))
                p = int(logit.argmax(1)[0])
                pred_s[t] = p
                if p == prev_c:
                    dur_c += 1
                else:
                    dur_c = 1
                prev_c = p
            conf_self += np.bincount(intent * 8 + pred_s, minlength=64).reshape(8, 8)
            n_self_correct += int((pred_s == intent).sum())

            # ---- 路由错配（teacher-feed 预测）----
            for t in range(n):
                if pred_t[t] != intent[t]:
                    n_route_preddiff += 1
                    if (
                        ACTIVATION[INTENT_IDS[int(pred_t[t])]]
                        != ACTIVATION[INTENT_IDS[int(intent[t])]]
                    ):
                        n_route_mismatch += 1

            # ---- base 桶混淆 + 安全级误判 ----
            mb = bucket == 0
            if mb.sum() > 0:
                b_true = intent[mb]
                b_pred = pred_t[mb]
                conf_base += np.bincount(b_true * 8 + b_pred, minlength=64).reshape(8, 8)
                n_base += int(mb.sum())

            # ---- prev ±3 tick 扰动 ----
            for k in range(-3, 4):
                p_k = shift_prev(intent, k)
                oh = np.zeros((n, 8), dtype=np.float32)
                oh[np.arange(n), p_k] = 1.0
                inj_k = np.concatenate([oh, np.minimum(dur, D_MAX)[:, None]], axis=1).astype(
                    np.float32
                )
                lg = model.intent_head(torch.cat([hc, torch.from_numpy(inj_k)], dim=1))
                pert[k][0] += int((lg.argmax(1).numpy() == intent).sum())
                pert[k][1] += n

    teach_acc = n_teach_correct / n_total if n_total else 0.0
    self_acc = n_self_correct / n_total if n_total else 0.0
    _ = conf_base.sum()  # diagnostic: total predictions
    safety_cross = 0
    for r in range(8):
        for c in range(8):
            rn, cn = INTENT_IDS[r], INTENT_IDS[c]
            if (rn in DEFENSIVE and cn in AGGRESSIVE) or (rn in AGGRESSIVE and cn in DEFENSIVE):
                safety_cross += int(conf_base[r][c])
    safety_rate = safety_cross / n_base if n_base else 0.0

    report = {
        "weights": args.weights,
        "shards": len(shards),
        "valShards": len(val_shards),
        "valFrames": int(n_total),
        "seed": args.seed,
        "safetyGate": SAFETY_GATE,
        "item1": {
            "teacherAcc": round(float(teach_acc), 4),
            "selfFeedAcc": round(float(self_acc), 4),
            "gap": round(float(teach_acc - self_acc), 4),
        },
        "item2": {
            "perturbedPrevAcc": {
                k: round(v[0] / v[1], 4) if v[1] else None for k, v in sorted(pert.items())
            },
            "maxDropAt3": round(
                max(
                    (pert[0][0] / pert[0][1] - pert[k][0] / pert[k][1])
                    for k in (-3, 3)
                    if pert[k][1]
                ),
                4,
            ),
        },
        "item3": {
            "baseBucketFrames": int(n_base),
            "confusion8x8": conf_base.tolist(),
            "safetyMisclassRate": round(float(safety_rate), 4),
            "safetyPass": bool(safety_rate <= SAFETY_GATE),
        },
        "item4": {
            "routeMismatchFrames": int(n_route_mismatch),
            "routePredDiffFrames": int(n_route_preddiff),
            "routeMismatchRate": round(n_route_mismatch / n_total, 4) if n_total else None,
        },
        "confusionTeacher8x8": conf_teach.tolist(),
        "confusionSelf8x8": conf_self.tolist(),
        "perClassRecall": {},
    }
    # 类级 recall（teacher-feed，与训练报告同口径：diag/真值类总数）
    diag = np.diag(conf_teach)
    colsum = conf_teach.sum(axis=1)
    for c in range(8):
        report["perClassRecall"][INTENT_IDS[c]] = (
            round(float(diag[c] / colsum[c]), 4) if colsum[c] else None
        )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("confusion8x8", "confusionTeacher8x8", "confusionSelf8x8")
            },
            indent=2,
        )
    )
    print(f"[m5gate] done -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
