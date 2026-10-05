#!/usr/bin/env python
"""spatial-probe-flatcv.py — S0′-1/1b 的 **5 折交叉验证**版平坦线性探针。

为什么补这一份（主探针 log 为证）：单折（33/7/8 局）下「早停选择」在 7 个 val 局上方差很大
（如 threat/front val 0.77 / test 0.55 的翻转）。本脚本用**局级 5 折 CV + 固定协议**
（标准化 + wd=1e-2 + 固定 20 epoch，无早停选择）得到更稳的样本外 AUC，并把逐折 spread
一并落盘（供 §7 读数判断置信度）。两套读数都保留（主探针单折版 + 本 CV 版）。

用法（必须走 nn-py-safe.sh）：
  bash tools/githook/nn-py-safe.sh nn-training/tools/spatial-probe-flatcv.py \
    --dump tmp/spatial-probe/hu150 --readings tmp/spatial-probe/hu150/readings.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
_NN = _ROOT / "nn-training"
sys.path.insert(0, str(_NN))

_spec = importlib.util.spec_from_file_location("spatial_probe_mod", _HERE.parent / "spatial-probe.py")
assert _spec is not None and _spec.loader is not None
_sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sp)

OBS_CH = 16


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def stats_f32(x: np.ndarray, is_tr: np.ndarray, chunk: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """train 折的 mean/std —— **按块 f32 累加**（x 可能是 f16 存储：整块 mean 会溢出）。"""
    idx = np.flatnonzero(is_tr)
    d = x.shape[1]
    s1 = np.zeros(d, dtype=np.float64)
    s2 = np.zeros(d, dtype=np.float64)
    cnt = 0
    for i in range(0, len(idx), chunk):
        xb = x[idx[i : i + chunk]].astype(np.float32).astype(np.float64)
        s1 += xb.sum(axis=0)
        s2 += (xb * xb).sum(axis=0)
        cnt += len(xb)
    mean = s1 / max(1, cnt)
    var = np.maximum(s2 / max(1, cnt) - mean * mean, 0.0)
    std = np.sqrt(var)
    return mean.astype(np.float32), (std + 1e-3).astype(np.float32)


def cv_probe(
    x: np.ndarray,
    labels: np.ndarray,
    game_ids: np.ndarray,
    n_folds: int,
    *,
    epochs: int = 20,
    batch: int = 1024,
    lr: float = 1e-3,
    wd: float = 1e-2,
    seed: int = 0,
) -> dict:
    """局级 CV：返回 {auc_pooled, fold_aucs}（labels 可多任务一次跑）。

    x 可为 f16 存储（bufA 全分辨率 43k 维）；统计量与标准化在 f32 下按块完成，
    训练batch 现取现转 —— 不物化整折 f32 矩阵。
    """
    rng = np.random.default_rng(seed)
    games = np.unique(game_ids)
    perm = rng.permutation(len(games))
    folds = np.array_split(perm, n_folds)
    n_tasks = labels.shape[1] if labels.ndim == 2 else 1
    lab2 = labels.reshape(len(labels), n_tasks)
    pooled_scores = np.zeros((len(x), n_tasks), dtype=np.float32)
    fold_aucs: list[list[float]] = [[] for _ in range(n_tasks)]
    for f in range(n_folds):
        te_games = set(games[folds[f]])
        is_te = np.array([g in te_games for g in game_ids])
        is_tr = ~is_te
        mean, std = stats_f32(x, is_tr)
        mean_t = torch.from_numpy(mean)
        std_t = torch.from_numpy(std)
        idx_tr = np.flatnonzero(is_tr)
        idx_te = np.flatnonzero(is_te)
        for t in range(n_tasks):
            torch.manual_seed(seed + f * 31 + t)
            model = _sp.LinearProbe(x.shape[1])
            opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
            yt = torch.from_numpy(lab2[is_tr, t].astype(np.float32))
            n = len(idx_tr)
            for _ep in range(epochs):
                p = torch.randperm(n)
                for i in range(0, n, batch):
                    sel = idx_tr[p[i : i + batch]]
                    xb = (torch.from_numpy(x[sel]).float() - mean_t) / std_t
                    loss = F.binary_cross_entropy_with_logits(model(xb), yt[p[i : i + batch]])
                    opt.zero_grad()
                    loss.backward()
                    opt.step()
            with torch.no_grad():
                xb = (torch.from_numpy(x[idx_te]).float() - mean_t) / std_t
                sc = model(xb).numpy()
            pooled_scores[is_te, t] = sc
            fold_aucs[t].append(_sp.auc_score(sc, lab2[is_te, t]))
    out: dict[str, Any] = {
        "fold_aucs": [
            dict(min=float(np.nanmin(a)), max=float(np.nanmax(a)), mean=float(np.nanmean(a)))
            for a in fold_aucs
        ]
    }
    out["auc_pooled"] = [float("nan")] * n_tasks
    for t in range(n_tasks):
        out["auc_pooled"][t] = _sp.auc_score(pooled_scores[:, t], lab2[:, t])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="tmp/spatial-probe/hu150")
    ap.add_argument("--readings", default="")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()

    t0 = time.time()
    dump = Path(args.dump)
    obs = np.load(dump / "obs.npy")
    scalars = np.load(dump / "scalars.npy")
    extra = np.load(dump / "extra.npy")
    pooled_ts = np.load(dump / "pooled.npy")
    index = np.load(dump / "index.npy")
    meta = json.loads((dump / "meta.json").read_text(encoding="utf-8"))
    n = len(obs)
    game_ids = np.array(
        [f"{int(a)}:{int(b)}" for a, b in zip(index[:, 0], index[:, 1], strict=True)]
    )

    model = _sp.load_model(str(_ROOT / meta["weights"]))
    bufa = np.empty((n, _sp.BUF_CH, _sp.BOARD, _sp.BOARD), dtype=np.float16)
    with torch.no_grad():
        for i in range(0, n, args.batch):
            ob = torch.from_numpy(obs[i : i + args.batch].astype(np.uint8))
            bufa[i : i + args.batch] = _sp.bufa_forward(model, ob).half().numpy()
    log(f"[flatcv] bufA 缓存完成 {time.time()-t0:.1f}s")

    threat = np.stack([extra[:, d] > 0 for d in range(4)], axis=1)
    hit = np.stack([np.abs(extra[:, 4 + d] - _sp.HIT_NONE) > 1e-6 for d in range(4)], axis=1)
    lab_names = ["front", "back", "left", "right"]

    x_flat = bufa.reshape(n, -1)  # f16 -> 每折内按需转（memory: 1.1GB f16）
    old_feat = np.concatenate([pooled_ts, scalars], axis=1)

    out: dict = {"protocol": f"game-level {args.folds}-fold CV, fixed 20ep wd1e-2, standardized"}
    for family, labels in (("threat", threat), ("hit", hit)):
        res = cv_probe(x_flat, labels, game_ids, args.folds, seed=args.seed)
        out[f"bufA_{family}"] = {
            lab_names[d]: {"auc_pooled": res["auc_pooled"][d], "folds": res["fold_aucs"][d]} for d in range(4)
        }
        log(f"    bufA {family}: " + " ".join(f"{lab_names[d]}={res['auc_pooled'][d]:.3f}" for d in range(4)))
    # old-input 对照（同一折，但需重算折）——直接跑一份
    for family, labels in (("threat", threat), ("hit", hit)):
        res = cv_probe(old_feat, labels, game_ids, args.folds, seed=args.seed)
        out[f"old_{family}"] = {
            lab_names[d]: {"auc_pooled": res["auc_pooled"][d], "folds": res["fold_aucs"][d]} for d in range(4)
        }
        log(f"    old  {family}: " + " ".join(f"{lab_names[d]}={res['auc_pooled'][d]:.3f}" for d in range(4)))

    out_path = Path(args.readings) if args.readings else dump / "readings-flatcv.json"
    if out_path.suffix != ".json":
        out_path = out_path / "readings-flatcv.json"
    payload = {}
    if out_path.exists():
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    payload["S0p1_flatcv5"] = out
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[flatcv] → {out_path} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
