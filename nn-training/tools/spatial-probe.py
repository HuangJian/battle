#!/usr/bin/env python
"""spatial-probe.py — S0′ 六探针（plan/policy-spatial-head.plan.md §4 Step 1）。

输入 = `tools/sim/spatial-probe-dump.ts` 的存档（obs/scalars/extra/pooled/index）。
本脚本负责：

  · 前置：torch 主干批处理重算 bufA（float16 缓存）+ 与 TS pooled 对账；
  · S0′-1  ：线性探针（bufA）预测「被瞄/能打中」两谓词 × 4 相对方向，报 AUC + 方向混淆矩阵；
  · S0′-1b ：同一探针改喂旧输入（pooled 64 + scalars 30）；
  · S0′-2  ：结构同冻结设计的小塔（1×1 64→8 + ReLU + 4×4 分区均值 + FC 128→1）端到端拟合，
             报样本外 AUC + ReLU 后激活非零率；
  · S0′-2c ：冻结 S0′-2 拟合出的 W，测「投影+ReLU 后」16 区块向量的线性可分性（准确率 vs 基线）；
  · S0′-2b ：原始区块 16 类线性分类（诊断，不设否决权）；
  · 输出读数 JSON（供 plan §7 读数表与后续归档）。

无 sklearn 依赖（venv 只有 numpy+torch）：AUC 用秩统计，模型用 torch。

用法（必须走 nn-py-safe.sh）：
  bash tools/githook/nn-py-safe.sh nn-training/tools/spatial-probe.py \
    --dump tmp/spatial-probe/hu150 --out tmp/spatial-probe/hu150/readings.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]  # repo root
_NN = _ROOT / "nn-training"
if str(_NN) not in sys.path:
    sys.path.insert(0, str(_NN))

from common.schema import BOARD
from worker.data.weights_io import load_weights_json
from worker.models.student import PPOStudent

OBS_CH = 16
BUF_CH = 64
POOL = 4
REGION_BOUNDS = [(0, 7), (6, 13), (13, 20), (19, 26)]  # plan §3.5 冻结边界
HIT_NONE = 1.5


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ---------------- 基础设施 ----------------

def rankdata(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    sorter = np.argsort(a, kind="mergesort")
    inv = np.empty(len(a), dtype=np.int64)
    inv[sorter] = np.arange(len(a))
    a_sorted = a[sorter]
    obs = np.r_[True, a_sorted[1:] != a_sorted[:-1]]
    dense = obs.cumsum()[inv]
    count = np.r_[np.nonzero(obs)[0], len(a)]
    res: np.ndarray = 0.5 * (count[dense] + count[dense - 1] + 1)
    return res


def auc_score(scores: np.ndarray, labels: np.ndarray) -> float:
    """秩统计 ROC AUC（tie-aware）；单类 ⇒ nan。"""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def load_model(weights_path: str) -> PPOStudent:
    meta, params = load_weights_json(weights_path)
    arch = meta.get("arch", meta) if isinstance(meta, dict) else {}
    model = PPOStudent(
        h=int(arch.get("h", 64)),
        d=int(arch.get("d", 8)),
        head_hidden=int(arch.get("head_hidden", 128)),
    )
    missing, unexpected = model.load_state_dict(params, strict=False)
    if missing:
        raise RuntimeError(f"权重缺键: {missing[:5]}...")
    model.eval()
    return model


def make_coords(device: torch.device) -> torch.Tensor:
    r = torch.arange(BOARD, dtype=torch.float32, device=device) / (BOARD - 1)
    x = r.repeat(BOARD, 1)
    y = x.t()
    return (torch.stack([x, y]) * 255).round()  # (2,B,B) 0..255


@torch.no_grad()
def bufa_forward(model: PPOStudent, obs_u8: torch.Tensor) -> torch.Tensor:
    """trunk → bufA (B,64,26,26)（与 student.py features() 前段逐字同构）。"""
    b = obs_u8.shape[0]
    coords = make_coords(obs_u8.device).unsqueeze(0).expand(b, -1, -1, -1)
    x = torch.cat([obs_u8.float(), coords], dim=1)
    x = F.relu(model.stem(x))
    for block in model.blocks:
        x = block(x)
    return x


class LinearProbe(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.w = nn.Parameter(torch.zeros(dim))
        self.b = nn.Parameter(torch.zeros(1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out: torch.Tensor = x @ self.w + self.b
        return out


def train_linear(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    *,
    epochs: int = 30,
    batch: int = 512,
    lr: float = 1e-3,
    wd: float = 1e-2,
    seed: int = 0,
) -> tuple[LinearProbe, float]:
    """训练线性探针；返回 (最优 val AUC 时的模型, val AUC)。"""
    torch.manual_seed(seed)
    model = LinearProbe(x_train.shape[1])
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    yt = torch.from_numpy(y_train.astype(np.float32))
    xt = torch.from_numpy(x_train)
    xv = torch.from_numpy(x_val)
    best: tuple[float, dict[str, Any] | None] = (-1.0, None)
    n = len(x_train)
    for _ep in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch):
            idx = perm[i : i + batch]
            logits = model(xt[idx])
            loss = F.binary_cross_entropy_with_logits(logits, yt[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        with torch.no_grad():
            vauc = auc_score(model(xv).numpy(), y_val)
        if not np.isnan(vauc) and vauc > best[0]:
            best = (vauc, {k: v.detach().clone() for k, v in model.state_dict().items()})
    if best[1] is not None:
        model.load_state_dict(best[1])
    return model, best[0]


class TowerProbe(nn.Module):
    """结构同冻结设计的小塔（S0′-2）：1×1 64→8 + ReLU + 4×4 分区均值 + FC(128→1)。"""

    def __init__(self):
        super().__init__()
        self.proj = nn.Conv2d(BUF_CH, 8, 1, bias=True)
        self.fc = nn.Linear(8 * POOL * POOL, 1, bias=True)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        z = F.relu(self.proj(x))
        p = F.adaptive_avg_pool2d(z, POOL).flatten(1)
        return self.fc(p).squeeze(-1), z


def train_tower(
    bufa_train: np.ndarray,
    y_train: np.ndarray,
    bufa_val: np.ndarray,
    y_val: np.ndarray,
    *,
    epochs: int = 30,
    batch: int = 128,
    lr: float = 1e-3,
    wd: float = 1e-3,
    seed: int = 0,
) -> tuple[TowerProbe, float, float]:
    """bufA 传 f16 数组（每 batch 现转 f32，控内存）。"""
    torch.manual_seed(seed)
    model = TowerProbe()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    yt = torch.from_numpy(y_train.astype(np.float32))
    xv = torch.from_numpy(bufa_val.astype(np.float32))
    yv = y_val
    best: tuple[float, dict[str, Any] | None] = (-1.0, None)
    n = len(bufa_train)
    for _ep in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch):
            idx = perm[i : i + batch]
            xb = torch.from_numpy(bufa_train[idx]).float()
            logits, _ = model(xb)
            loss = F.binary_cross_entropy_with_logits(logits, yt[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        with torch.no_grad():
            out, z = model(xv)
            vauc = auc_score(out.numpy(), yv)
            nz = float((z > 0).float().mean().item())
        if not np.isnan(vauc) and vauc > best[0]:
            best = (vauc, {k: v.detach().clone() for k, v in model.state_dict().items()})
    assert best[1] is not None
    model.load_state_dict(best[1])
    with torch.no_grad():
        out, z = model(xv)
        nz = float((z > 0).float().mean().item())
    return model, best[0], nz


@torch.no_grad()
def tower_features(model: TowerProbe, bufa: np.ndarray, batch: int = 128) -> np.ndarray:
    """投影+ReLU+分区均值 → (N,128)（S0′-2c 的冻结特征；bufA 传 f16）。"""
    out = []
    for i in range(0, len(bufa), batch):
        xb = torch.from_numpy(bufa[i : i + batch]).float()
        z = F.relu(model.proj(xb))
        p = F.adaptive_avg_pool2d(z, POOL).flatten(1)
        out.append(p.numpy())
    return np.concatenate(out, axis=0)


# ---------------- 主流程 ----------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="tmp/spatial-probe/hu150")
    ap.add_argument("--out", default="")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--batch", type=int, default=256)
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
    log(f"[probe] N={n} games={meta['games']} weightsSha={meta['weightsSha16']}")

    # ---- 前置：trunk 批处理 → bufA（f16 缓存）+ pooled 对账 ----
    model = load_model(str(_ROOT / meta["weights"]) if not Path(meta["weights"]).is_absolute() else meta["weights"])
    bufa = np.empty((n, BUF_CH, BOARD, BOARD), dtype=np.float16)
    pooled_t = np.empty((n, BUF_CH), dtype=np.float32)
    with torch.no_grad():
        for i in range(0, n, args.batch):
            ob = torch.from_numpy(obs[i : i + args.batch].astype(np.uint8))
            feats = bufa_forward(model, ob)
            pooled_t[i : i + args.batch] = feats.mean(dim=(2, 3)).numpy()
            bufa[i : i + args.batch] = feats.half().numpy()
            if i == 0:
                log(f"[probe] trunk batch {args.batch}: {time.time()-t0:.1f}s (含权重加载)")
    pooled_delta = float(np.abs(pooled_t - pooled_ts).max())
    log(f"[probe] pooled TS↔torch max|Δ| = {pooled_delta:.3e}")

    # ---- 标签 ----
    threat = np.stack([extra[:, d] > 0 for d in range(4)], axis=1)  # 前/后/左/右
    hit = np.stack([np.abs(extra[:, 4 + d] - HIT_NONE) > 1e-6 for d in range(4)], axis=1)
    pincer = extra[:, 8] > 0
    lab_names = ["front", "back", "left", "right"]

    # ---- 局级切分（按 (stage,seed) 分组；70/15/15 train/val/test）----
    games = sorted({(int(a), int(b)) for a, b in zip(index[:, 0], index[:, 1], strict=True)})
    rng = np.random.default_rng(args.seed)
    perm = rng.permutation(len(games))
    n_train = int(len(games) * 0.7)
    n_val = int(len(games) * 0.15)
    g_train = {games[i] for i in perm[:n_train]}
    g_val = {games[i] for i in perm[n_train : n_train + n_val]}
    g_test = {games[i] for i in perm[n_train + n_val :]}
    is_train = np.array(
        [(int(a), int(b)) in g_train for a, b in zip(index[:, 0], index[:, 1], strict=True)]
    )
    is_val = np.array(
        [(int(a), int(b)) in g_val for a, b in zip(index[:, 0], index[:, 1], strict=True)]
    )
    is_test = np.array(
        [(int(a), int(b)) in g_test for a, b in zip(index[:, 0], index[:, 1], strict=True)]
    )
    log(f"[probe] games train/val/test = {len(g_train)}/{len(g_val)}/{len(g_test)}")

    readings: dict = {
        "dump": str(dump),
        "N": n,
        "games": meta["games"],
        "weightsSha16": meta["weightsSha16"],
        "pooled_ts_torch_max_abs_delta": pooled_delta,
        "label_prevalence": {
            "threat": {lab_names[d]: float(threat[:, d].mean()) for d in range(4)},
            "hit": {lab_names[d]: float(hit[:, d].mean()) for d in range(4)},
            "pincer": float(pincer.mean()),
        },
        "split": {"train_games": len(g_train), "val_games": len(g_val), "test_games": len(g_test)},
    }

    # ================= S0′-1：线性探针 on bufA =================
    log("[probe] S0′-1 线性探针（bufA 43k 维）...")
    x_flat_train = bufa[is_train].reshape(int(is_train.sum()), -1).astype(np.float32)
    x_flat_val = bufa[is_val].reshape(int(is_val.sum()), -1).astype(np.float32)
    x_flat_test = bufa[is_test].reshape(int(is_test.sum()), -1).astype(np.float32)
    mean = x_flat_train.mean(axis=0, keepdims=True)
    x_flat_train -= mean
    x_flat_val -= mean
    x_flat_test -= mean
    s1: dict = {"threat_auc": {}, "hit_auc": {}, "confusion": {}}
    clfs: dict = {}
    for family, labels, feat_tr, feat_va, feat_te in (
        ("threat", threat, x_flat_train, x_flat_val, x_flat_test),
        ("hit", hit, x_flat_train, x_flat_val, x_flat_test),
    ):
        for d in range(4):
            clf, vauc = train_linear(
                feat_tr,
                labels[is_train, d],
                feat_va,
                labels[is_val, d],
                seed=args.seed + d,
            )
            te = auc_score(clf(torch.from_numpy(feat_te)).detach().numpy(), labels[is_test, d])
            s1[f"{family}_auc"][lab_names[d]] = {"test_auc": te, "val_auc": vauc}
            clfs[(family, d)] = clf
            log(f"    {family}/{lab_names[d]}: testAUC={te:.3f} valAUC={vauc:.3f}")
        # 方向混淆矩阵：clf_d 评 label_s
        mat = np.full((4, 4), np.nan)
        for d in range(4):
            sc = clfs[(family, d)](torch.from_numpy(feat_te)).detach().numpy()
            for s in range(4):
                mat[d, s] = auc_score(sc, labels[is_test, s])
        s1["confusion"][family] = mat.tolist()
    # pincer（诊断附加）
    clf_p, _ = train_linear(
        x_flat_train,
        pincer[is_train],
        x_flat_val,
        pincer[is_val],
        seed=args.seed + 17,
    )
    s1["pincer_auc"] = auc_score(clf_p(torch.from_numpy(x_flat_test)).detach().numpy(), pincer[is_test])
    del x_flat_train, x_flat_val, x_flat_test
    readings["S0p1_linear_bufA"] = s1
    log(f"    pincer: testAUC={s1['pincer_auc']:.3f}")

    # ================= S0′-1b：旧输入对照（pooled+scalars）=================
    log("[probe] S0′-1b 线性探针（pooled 64 + scalars 30）...")
    old_feat = np.concatenate([pooled_ts, scalars], axis=1)
    om = old_feat[is_train].mean(axis=0, keepdims=True)
    old_feat = old_feat - om
    s1b: dict = {"threat_auc": {}, "hit_auc": {}}
    for family, labels in (("threat", threat), ("hit", hit)):
        for d in range(4):
            clf, vauc = train_linear(
                old_feat[is_train],
                labels[is_train, d],
                old_feat[is_val],
                labels[is_val, d],
                seed=args.seed + d,
            )
            te = auc_score(clf(torch.from_numpy(old_feat[is_test])).detach().numpy(), labels[is_test, d])
            s1b[f"{family}_auc"][lab_names[d]] = {"test_auc": te, "val_auc": vauc}
            log(f"    {family}/{lab_names[d]}: testAUC={te:.3f}")
    readings["S0p1b_linear_old_input"] = s1b

    # ================= S0′-2 / S0′-2c：C=8 小塔 =================
    log("[probe] S0′-2 / S0′-2c 小塔（1×1 64→8 + ReLU + 4×4 + FC）...")
    bufa_train = bufa[is_train]  # f16（train_tower / tower_features 每 batch 转 f32）
    bufa_val = bufa[is_val]
    bufa_test = bufa[is_test]
    s2: dict = {"threat_auc": {}, "hit_auc": {}, "relu_nonzero_rate": {}, "S0p2c_linear_auc": {}, "S0p2c_acc": {}}
    for family, labels in (("threat", threat), ("hit", hit)):
        for d in range(4):
            tp, vauc, nz = train_tower(
                bufa_train,
                labels[is_train, d],
                bufa_val,
                labels[is_val, d],
                seed=args.seed + d,
            )
            with torch.no_grad():
                out, _ = tp(torch.from_numpy(bufa_test).float())
            te = auc_score(out.numpy(), labels[is_test, d])
            s2[f"{family}_auc"][lab_names[d]] = {"test_auc": te, "val_auc": vauc}
            s2["relu_nonzero_rate"][f"{family}/{lab_names[d]}"] = nz
            # S0′-2c：冻结 W → 128 维特征 → 新鲜线性分类器
            f_tr = tower_features(tp, bufa_train)
            f_va = tower_features(tp, bufa_val)
            f_te = tower_features(tp, bufa_test)
            clf2, _ = train_linear(
                f_tr,
                labels[is_train, d],
                f_va,
                labels[is_val, d],
                epochs=60,
                seed=args.seed + d,
            )
            sc = clf2(torch.from_numpy(f_te)).detach().numpy()
            auc2 = auc_score(sc, labels[is_test, d])
            pred = sc > 0
            acc = float((pred == labels[is_test, d]).mean())
            base = max(float(labels[is_test, d].mean()), 1 - float(labels[is_test, d].mean()))
            s2["S0p2c_linear_auc"][f"{family}/{lab_names[d]}"] = auc2
            s2["S0p2c_acc"][f"{family}/{lab_names[d]}"] = {"acc": acc, "majority_base": base}
            log(
                f"    {family}/{lab_names[d]}: towerAUC={te:.3f} nzRate={nz:.3f} "
                f"2c-AUC={auc2:.3f} 2c-acc={acc:.3f} (base {base:.3f})"
            )
    readings["S0p2_tower"] = s2

    # ================= S0′-2b：原始区块 16 类（诊断）=================
    log("[probe] S0′-2b 原始区块 16 类线性分类...")
    n_b = min(2048, int(is_test.sum()) + int(is_train.sum()))
    pool_b = min(int(is_train.sum()), n_b // 2)
    pool_te = min(int(is_test.sum()), n_b - pool_b)
    idx_tr = np.flatnonzero(is_train)[:pool_b]
    idx_te = np.flatnonzero(is_test)[:pool_te]
    blocks, base_lab = [], []
    for idx_list in (idx_tr, idx_te):
        for j, si in enumerate(idx_list):
            for k in range(4):
                r = (j * 4 + k) % 16
                rb, cb = REGION_BOUNDS[r // 4], REGION_BOUNDS[r % 4]
                b = bufa[si, :, rb[0] : rb[1], cb[0] : cb[1]]
                # 2×2 均值把 7×7 收到 4×4（探针规模控制；线性受限，只作诊断）
                tb_t = torch.from_numpy(b.astype(np.float32)).unsqueeze(0)
                tb_np = F.avg_pool2d(tb_t, 2).squeeze(0).numpy()
                blocks.append(tb_np.reshape(-1))
                base_lab.append(r)
    xb = np.stack(blocks).astype(np.float32)
    yb = np.array(base_lab, dtype=np.int64)
    ntr = pool_b * 4
    torch.manual_seed(args.seed)
    clf3 = nn.Linear(xb.shape[1], 16)
    opt3 = torch.optim.AdamW(clf3.parameters(), lr=1e-3, weight_decay=1e-4)
    xt3 = torch.from_numpy(xb[:ntr])
    yt3 = torch.from_numpy(yb[:ntr])
    for _ep in range(30):
        perm3 = torch.randperm(ntr)
        for i in range(0, ntr, 256):
            sel = perm3[i : i + 256]
            loss = F.cross_entropy(clf3(xt3[sel]), yt3[sel])
            opt3.zero_grad()
            loss.backward()
            opt3.step()
    with torch.no_grad():
        pred = clf3(torch.from_numpy(xb[ntr:])).argmax(dim=1).numpy()
    acc3 = float((pred == yb[ntr:]).mean())
    readings["S0p2b_region16"] = {"test_acc": acc3, "baseline_1of16": 0.0625, "n": len(xb)}
    log(f"    16-class acc={acc3:.3f} (baseline 0.0625, n={len(xb)})")

    # ---- 落盘 ----
    out = Path(args.out) if args.out else dump / "readings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(readings, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[probe] readings → {out}  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
