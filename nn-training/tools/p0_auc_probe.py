"""C4 — 离线 AUC 探针：基地将毁信号能否从冻结 `pf` 线性解码。

**问题**（`plan/classic35-path.plan.md` §5 C4）：z10 的 critic 冷启动且标定差，
但“critic 没用好”只有在输入里**确实存在**可解码信号时才是训练问题。本探针冻结
z10.it233 的 `pf`（policy_features，不是 critic 的 `V(s)`），只训练 logistic probe：
标签 `outcome=base_destroyed ∧ end_tick−t ≤ W`，W = 50/100/200 拍（200 拍在
GAE 193 拍窗外），并对 `POLICY_EXTRA[25:37]`（pf 尾部 12 维基地威胁）做置零消融。

**口径**
* 特征只用 t 时刻的 `obs/scalars/extra`；标签用该局 terminal tick 与 outcome。
* 按 `(stage, seed)` 分组的 5 折交叉验证：同一局绝不跨训练/评估。
* 训练抽样 = **全部正步 + 3% 负步**（池化约 3.3%–4.3%，落在 plan 的 2–5%）；
  每局内按实际入样概率 `1/p` 加权并归一，使每局总权重相同（episode 加权）。
* 评估用**留出局的全部决策步**，报告 AUC + PR-AUC（average precision）；
  95% CI = 按局 cluster bootstrap。PR-AUC 与 prevalence 同报，不跨 W 直接比。
* `V(s)` 基线读 shard 的 `value.npy`，同一标签、同一评估行；AUC 高只证明
  “可行”，不证明“必要”，是否开辅助头仍看与 critic 的对照。

用法（仓库根）：

    bash tools/githook/nn-py-safe.sh nn-training/tools/p0_auc_probe.py \
        --shards tmp/p0-c9/rollouts \
        --out tmp/p0-c4/auc.json --md tmp/p0-c4/auc.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from biz.reward_library import METRIC_INDEX, METRICS_DIM, METRICS_VERSION
from common.cli import house_parser, run_main
from common.schema import POLICY_EXTRA_DIM
from tools.p0_common import find_shards, rankdata, read_manifest, select_balanced, sha16

DEFAULT_WEIGHTS = "nn-training/weights/z10-maze/z10-maze.it233.20261106-211711.json"
DEFAULT_SHARDS = "tmp/p0-c9/rollouts"
SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "extra.npy",
    "metrics.npy",
    "done.npy",
    "value.npy",
    "manifest.json",
)
WINDOWS = (50, 100, 200)
THREAT_LO = 25
THREAT_HI = 37
NEG_SAMPLE_FRAC = 0.03
N_FOLDS = 5
HIGH_AUC = 0.70
LOW_AUC = 0.60
MATERIAL_DROP = 0.05


def auc_score(scores: np.ndarray, labels: np.ndarray) -> float:
    """秩统计 ROC AUC；单类 ⇒ nan。"""
    labels = np.asarray(labels, dtype=bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def average_precision(scores: np.ndarray, labels: np.ndarray) -> float:
    """PR-AUC（average precision），按分数降序、tie 以稳定排序处理。"""
    labels = np.asarray(labels, dtype=bool)
    n_pos = int(labels.sum())
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-np.asarray(scores, dtype=np.float64), kind="mergesort")
    y = labels[order]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float(precision[y].sum() / n_pos)


def threat_slice(pf_dim: int) -> slice:
    """`POLICY_EXTRA[25:37]` 在冻结 pf 中的位置（extra 在尾部）。"""
    start = int(pf_dim) - POLICY_EXTRA_DIM + THREAT_LO
    stop = int(pf_dim) - POLICY_EXTRA_DIM + THREAT_HI
    if start < 0 or stop > int(pf_dim) or stop - start != 12:
        raise ValueError(f"pf_dim={pf_dim} 放不下 POLICY_EXTRA[25:37]")
    return slice(start, stop)


def labels_for(
    outcome: str, ticks: np.ndarray, end_tick: float, windows: tuple[int, ...] = WINDOWS
) -> dict[int, np.ndarray]:
    """`base_destroyed ∧ end_tick−t≤W`；非基地毁局全负。"""
    t = np.asarray(ticks, dtype=np.float64)
    base = outcome == "base_destroyed"
    return {
        int(w): (base & ((float(end_tick) - t) <= float(w))).astype(bool) for w in windows
    }


@dataclass
class GameRows:
    stage: int
    seed: int
    x: np.ndarray
    value: np.ndarray
    labels: dict[int, np.ndarray]

    @property
    def key(self) -> tuple[int, int]:
        return (self.stage, self.seed)


def load_policy_model(path: str) -> Any:
    """按权重 arch 建 PPOStudent 并严格装载。"""
    from worker.data.weights_io import load_state_into
    from worker.models.arch_meta import build_ppo
    from worker.models.student import PPOStudent

    model = build_ppo(PPOStudent, path)
    missing = load_state_into(model, path)
    if missing:
        raise ValueError(
            f"{path}: 未装载参数 {missing[:8]}（共 {len(missing)}）——不是同架构 PPO checkpoint"
        )
    if not bool(getattr(model, "action_v6", False)):
        raise ValueError(f"{path}: arch.actionV6=false，C4 需要 v6 语料")
    if not bool(getattr(model, "policy_extra", False)):
        raise ValueError(f"{path}: policyExtra=false，无法取 pf 尾部 12 维做消融")
    model.eval()
    return model


def extract_pf(dirpath: str, model: Any, batch: int = 512) -> np.ndarray:
    """冻结模型前向，取 policy_features（不读 value_head）。"""
    import torch

    obs = np.load(os.path.join(dirpath, "obs.npy"), mmap_mode="r")
    scalars = np.load(os.path.join(dirpath, "scalars.npy"), mmap_mode="r")
    extra = np.load(os.path.join(dirpath, "extra.npy"), mmap_mode="r")
    n = int(obs.shape[0])
    if n <= 0:
        raise ValueError(f"{dirpath}: 空 shard")
    if obs.shape[1:] != (int(model.in_ch), int(model.board), int(model.board)):
        raise ValueError(f"{dirpath}: obs {obs.shape} != 模型输入")
    if scalars.shape != (n, int(model.scalar_dim)):
        raise ValueError(f"{dirpath}: scalars {scalars.shape} != ({n},{model.scalar_dim})")
    if extra.shape != (n, POLICY_EXTRA_DIM):
        raise ValueError(f"{dirpath}: extra {extra.shape} != ({n},{POLICY_EXTRA_DIM})")
    out = np.empty((n, int(model.policy_input_dim)), dtype=np.float32)
    with torch.no_grad():
        for i in range(0, n, batch):
            j = min(i + batch, n)
            o = torch.from_numpy(np.array(obs[i:j], dtype=np.float32, copy=True))
            s = torch.from_numpy(np.array(scalars[i:j], dtype=np.float32, copy=True))
            e = torch.from_numpy(np.array(extra[i:j], dtype=np.float32, copy=True))
            if bool(getattr(model, "spatial_tower", False)):
                pf = model.policy_features(o, s, e)
            else:
                hidden = model.features(o, s)
                pf = model.policy_features(o, s, e, hidden=hidden)
            arr = pf.detach().cpu().numpy()
            if arr.shape != (j - i, out.shape[1]):
                raise ValueError(f"{dirpath}: pf {arr.shape} != ({j - i},{out.shape[1]})")
            out[i:j] = arr
    return out


def load_game(dirpath: str, model: Any, *, batch: int = 512) -> GameRows:
    meta = read_manifest(dirpath)
    stage = meta.get("stage")
    seed = meta.get("seed")
    outcome = meta.get("outcome")
    if not isinstance(stage, int) or not isinstance(seed, int):
        raise ValueError(f"{dirpath}: manifest 缺 int stage/seed")
    if not isinstance(outcome, str):
        raise ValueError(f"{dirpath}: manifest 缺 outcome")
    mver = meta.get("metrics_version")
    if mver is not None and int(mver) != METRICS_VERSION:
        raise ValueError(
            f"{dirpath}: metrics_version={mver} != {METRICS_VERSION}——语料与当前列宽不同源"
        )

    obs = np.load(os.path.join(dirpath, "obs.npy"), mmap_mode="r")
    settings = np.load(os.path.join(dirpath, "metrics.npy"), mmap_mode="r")
    done = np.load(os.path.join(dirpath, "done.npy"), mmap_mode="r")
    values = np.load(os.path.join(dirpath, "value.npy"), mmap_mode="r")
    n = int(obs.shape[0])
    if settings.ndim != 2 or settings.shape[0] < n or settings.shape[1] < METRICS_DIM:
        raise ValueError(f"{dirpath}: metrics {settings.shape} 短于 N={n}")
    if done.shape != (n,):
        raise ValueError(f"{dirpath}: done {done.shape} != ({n},)")
    if values.shape != (n,):
        raise ValueError(f"{dirpath}: value {values.shape} != ({n},)")
    if int(done[-1]) != 1:
        raise ValueError(f"{dirpath}: 末步 done=0，无法确定 terminal tick")

    ticks = np.asarray(settings[:n, METRIC_INDEX["ticks"]], dtype=np.float64)
    if not np.isfinite(ticks).all():
        raise ValueError(f"{dirpath}: ticks 有非有限值")
    end_tick = (
        float(settings[n, METRIC_INDEX["ticks"]])
        if settings.shape[0] > n
        else float(meta.get("ticks", ticks[-1]))
    )
    if end_tick < ticks[-1]:
        raise ValueError(f"{dirpath}: end_tick={end_tick} < 最后决策 tick={ticks[-1]}")
    return GameRows(
        stage=stage,
        seed=seed,
        x=extract_pf(dirpath, model, batch=batch),
        value=np.asarray(values, dtype=np.float32),
        labels=labels_for(outcome, ticks, end_tick),
    )


def make_folds(games: list[GameRows], n_folds: int, seed: int) -> np.ndarray:
    """按“局内是否出现过基地毁样本”分层，保证每折都有正负局。"""
    if n_folds < 2:
        raise ValueError("至少 2 折")
    has_base = np.array(
        [any(bool(g.labels[w].any()) for w in WINDOWS) for g in games], dtype=bool
    )
    fold = np.full(len(games), -1, dtype=np.int64)
    rng = np.random.default_rng(seed)
    for mask in (has_base, ~has_base):
        idx = np.flatnonzero(mask)
        rng.shuffle(idx)
        for j, game_idx in enumerate(idx):
            fold[int(game_idx)] = j % int(n_folds)
    if bool((fold < 0).any()):
        raise ValueError("有局未分到 fold")
    return fold


def sample_episode(
    game: GameRows,
    window: int,
    neg_frac: float,
    rng: np.random.Generator,
    *,
    ablate: slice | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """一局内抽全部正步 + `neg_frac` 负步，返回 x/y/每局归一权重。"""
    y = game.labels[window]
    pos = np.flatnonzero(y)
    neg = np.flatnonzero(~y)
    takes: list[np.ndarray] = []
    probs: list[np.ndarray] = []
    if pos.size:
        takes.append(pos)
        probs.append(np.ones(pos.size, dtype=np.float64))
    if neg.size:
        n_neg = max(1, round(float(neg.size) * float(neg_frac)))
        n_neg = min(n_neg, int(neg.size))
        take_neg = rng.choice(neg, size=n_neg, replace=False)
        takes.append(np.asarray(take_neg, dtype=np.int64))
        probs.append(np.full(n_neg, float(n_neg) / float(neg.size), dtype=np.float64))
    if not takes:
        raise ValueError(f"stage={game.stage} seed={game.seed}: 窗口 {window} 无样本")
    idx = np.concatenate(takes)
    p = np.concatenate(probs)
    w = 1.0 / p
    w /= w.sum()
    x = np.array(game.x[idx], copy=True)
    if ablate is not None:
        x[:, ablate] = 0.0
    return x, y[idx], w.astype(np.float32)


@dataclass
class LogisticProbe:
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    bias: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        z = (np.asarray(x, dtype=np.float64) - self.mean) / self.std
        return np.asarray(z @ self.coef + self.bias, dtype=np.float64)


def fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    lam: float = 1e-3,
    max_iter: int = 100,
    seed: int = 0,
) -> LogisticProbe:
    """加权 L2 logistic（LBFGS）；输入标准化统计只用训练折。"""
    import torch
    import torch.nn.functional as F

    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float32)
    w = np.asarray(w, dtype=np.float64)
    wsum = float(w.sum())
    if wsum <= 0:
        raise ValueError("样本权重全为 0")
    mean = (x * w[:, None]).sum(axis=0) / wsum
    var = ((x - mean) ** 2 * w[:, None]).sum(axis=0) / wsum
    std = np.sqrt(np.maximum(var, 0.0))
    std[std < 1e-6] = 1.0
    z = (x - mean) / std

    torch.manual_seed(int(seed))
    xt = torch.from_numpy(z.astype(np.float32))
    yt = torch.from_numpy(y)
    wt = torch.from_numpy(w.astype(np.float32))
    coef = torch.zeros(z.shape[1], dtype=torch.float32, requires_grad=True)
    bias = torch.zeros(1, dtype=torch.float32, requires_grad=True)
    opt = torch.optim.LBFGS(
        [coef, bias],
        max_iter=int(max_iter),
        history_size=10,
        line_search_fn="strong_wolfe",
    )

    def closure() -> Any:
        opt.zero_grad()
        logits = xt @ coef + bias
        loss = (
            F.binary_cross_entropy_with_logits(logits, yt, reduction="none") * wt
        ).sum() / wt.sum()
        loss = loss + 0.5 * float(lam) * (coef @ coef)
        loss.backward()
        return loss

    opt.step(closure)
    return LogisticProbe(
        mean=mean,
        std=std,
        coef=coef.detach().cpu().numpy().astype(np.float64),
        bias=float(bias.detach().cpu().item()),
    )


def run_probe(
    games: list[GameRows],
    folds: np.ndarray,
    window: int,
    *,
    neg_frac: float,
    n_folds: int,
    seed: int,
    ablate: slice | None,
) -> dict[str, Any]:
    """按 fold 训练/评估，返回留出局的 OOF 分数与标签。"""
    scores: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    game_ids: list[np.ndarray] = []
    train_rows: list[int] = []
    train_pos: list[int] = []
    for k in range(int(n_folds)):
        train_ids = np.flatnonzero(folds != k)
        eval_ids = np.flatnonzero(folds == k)
        if train_ids.size == 0 or eval_ids.size == 0:
            raise ValueError(f"fold {k} 为空")
        rng = np.random.default_rng(int(seed) + 1009 * int(window) + 17 * k)
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        ws: list[np.ndarray] = []
        for game_idx in train_ids:
            x, y, w = sample_episode(
                games[int(game_idx)], window, neg_frac, rng, ablate=ablate
            )
            xs.append(x)
            ys.append(y)
            ws.append(w)
        xt = np.concatenate(xs)
        yt = np.concatenate(ys)
        wt = np.concatenate(ws)
        if not bool(yt.any()) or bool(yt.all()):
            raise ValueError(f"window={window} fold={k} 训练标签只有一类")
        probe = fit_logistic(xt, yt, wt, seed=int(seed) + k + int(window))
        train_rows.append(len(yt))
        train_pos.append(int(yt.sum()))
        for game_idx in eval_ids:
            g = games[int(game_idx)]
            x = g.x
            if ablate is not None:
                x = np.array(x, copy=True)
                x[:, ablate] = 0.0
            s = probe.predict(x)
            scores.append(s)
            labels.append(g.labels[window])
            game_ids.append(np.full(len(s), int(game_idx), dtype=np.int64))
    return {
        "scores": np.concatenate(scores),
        "labels": np.concatenate(labels),
        "game_ids": np.concatenate(game_ids),
        "mean_train_rows": float(np.mean(train_rows)) if train_rows else 0.0,
        "mean_train_pos": float(np.mean(train_pos)) if train_pos else 0.0,
    }


def _bootstrap_ci(
    scores: np.ndarray,
    labels: np.ndarray,
    game_ids: np.ndarray,
    *,
    reps: int,
    seed: int,
) -> dict[str, list[float] | None]:
    if reps <= 0:
        return {"auc": None, "pr_auc": None}
    unique = np.unique(game_ids)
    if unique.size < 2:
        return {"auc": None, "pr_auc": None}
    groups = [(scores[game_ids == g], labels[game_ids == g]) for g in unique]
    rng = np.random.default_rng(seed)
    aucs: list[float] = []
    aps: list[float] = []
    for _ in range(int(reps)):
        pick = rng.integers(0, len(groups), size=len(groups))
        s = np.concatenate([groups[int(i)][0] for i in pick])
        y = np.concatenate([groups[int(i)][1] for i in pick])
        a = auc_score(s, y)
        if np.isfinite(a):
            aucs.append(float(a))
            aps.append(float(average_precision(s, y)))
    if not aucs:
        return {"auc": None, "pr_auc": None}
    return {
        "auc": [float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))],
        "pr_auc": [float(np.percentile(aps, 2.5)), float(np.percentile(aps, 97.5))],
    }


def metric_block(
    scores: np.ndarray,
    labels: np.ndarray,
    game_ids: np.ndarray,
    *,
    boot: int,
    seed: int,
) -> dict[str, Any]:
    ci = _bootstrap_ci(scores, labels, game_ids, reps=boot, seed=seed)
    return {
        "auc": auc_score(scores, labels),
        "auc_ci95": ci["auc"],
        "pr_auc": average_precision(scores, labels),
        "pr_auc_ci95": ci["pr_auc"],
        "n_steps": len(labels),
        "n_pos": int(np.asarray(labels, dtype=bool).sum()),
        "n_games": int(np.unique(game_ids).size),
        "prevalence": float(np.asarray(labels, dtype=bool).mean()),
    }


def probe_verdict(full_auc: float, ablated_auc: float) -> str:
    """三行读法的机械映射；不是开腿门。"""
    if not np.isfinite(full_auc):
        return "no_data"
    if full_auc < LOW_AUC:
        return "no_linear_signal"
    if np.isfinite(ablated_auc) and (full_auc - ablated_auc) >= MATERIAL_DROP:
        return "signal_in_base_12d"
    if full_auc >= HIGH_AUC and (
        not np.isfinite(ablated_auc) or ablated_auc >= HIGH_AUC
    ):
        return "redundant_signal"
    return "partial_signal"


def critic_verdict(full_auc: float, critic_auc: float) -> str:
    if not np.isfinite(full_auc) or not np.isfinite(critic_auc):
        return "no_data"
    if critic_auc >= full_auc - 0.05:
        return "critic_ranks_near_probe"
    if full_auc >= HIGH_AUC and critic_auc < 0.65:
        return "critic_underuses_signal"
    return "inconclusive"


def build_report(
    games: list[GameRows],
    folds: np.ndarray,
    *,
    shards_root: str,
    n_shards_total: int,
    weights_path: str,
    pf_dim: int,
    ablate: slice,
    neg_frac: float,
    n_folds: int,
    boot: int,
    seed: int,
) -> dict[str, Any]:
    if not games:
        raise ValueError("没有可用 shard")
    keys = [g.key for g in games]
    if len(set(keys)) != len(keys):
        raise ValueError("重复 (stage, seed) shard")
    windows: dict[str, Any] = {}
    for offset, w in enumerate(WINDOWS):
        full = run_probe(
            games,
            folds,
            w,
            neg_frac=neg_frac,
            n_folds=n_folds,
            seed=seed,
            ablate=None,
        )
        abl = run_probe(
            games,
            folds,
            w,
            neg_frac=neg_frac,
            n_folds=n_folds,
            seed=seed,
            ablate=ablate,
        )
        full_m = metric_block(
            full["scores"], full["labels"], full["game_ids"], boot=boot, seed=seed + 10 + offset
        )
        abl_m = metric_block(
            abl["scores"], abl["labels"], abl["game_ids"], boot=boot, seed=seed + 20 + offset
        )
        critic_raw = np.concatenate([g.value for g in games])
        critic_labels = np.concatenate([g.labels[w] for g in games])
        critic_ids = np.concatenate(
            [np.full(len(g.value), i, dtype=np.int64) for i, g in enumerate(games)]
        )
        # V 是回报预测：低 V = 高风险，所以风险分数取 −V；原始 AUC 一并留档。
        critic_m = metric_block(
            -critic_raw, critic_labels, critic_ids, boot=boot, seed=seed + 30 + offset
        )
        critic_m["raw_value_auc"] = auc_score(critic_raw, critic_labels)
        critic_m["orientation"] = "risk = -V(s)"
        d_auc = full_m["auc"] - abl_m["auc"]
        windows[str(w)] = {
            "window_ticks": int(w),
            "window_decisions": float(w) / 10.0,
            "full": {
                **full_m,
                "mean_train_rows": full["mean_train_rows"],
                "mean_train_pos": full["mean_train_pos"],
            },
            "ablated_base_12d": {
                **abl_m,
                "mean_train_rows": abl["mean_train_rows"],
                "mean_train_pos": abl["mean_train_pos"],
            },
            "delta_auc": float(d_auc),
            "probe_verdict": probe_verdict(full_m["auc"], abl_m["auc"]),
            "critic_value": critic_m,
            "critic_vs_probe": critic_verdict(full_m["auc"], critic_m["auc"]),
        }
    return {
        "schema": "c4-offline-auc-v1",
        "shards": {
            "root": shards_root,
            "used": len(games),
            "available": n_shards_total,
            "steps": int(sum(len(g.x) for g in games)),
            "metrics_version": METRICS_VERSION,
            "stages": sorted({g.stage for g in games}),
        },
        "feature": {
            "source": weights_path,
            "sha16": sha16(weights_path),
            "pf_dim": int(pf_dim),
            "ablation": "POLICY_EXTRA[25:37] zeroed",
            "ablation_slice": [int(ablate.start), int(ablate.stop)],
        },
        "labels": {
            "rule": "outcome=base_destroyed and end_tick - t <= W",
            "windows_ticks": list(WINDOWS),
            "decision_k": 10,
            "non_base_outcomes": "all negative",
        },
        "sampling": {
            "all_positive_steps": True,
            "negative_fraction": float(neg_frac),
            "episode_weighted": True,
            "evaluation": "all steps of held-out episodes",
            "folds": int(n_folds),
            "group_key": "(stage, seed)",
        },
        "thresholds": {
            "high_auc": HIGH_AUC,
            "low_auc": LOW_AUC,
            "material_ablation_drop": MATERIAL_DROP,
        },
        "windows": windows,
        "bootstrap": {"reps": int(boot), "seed": int(seed), "cluster": "game"},
        "elapsed_sec": 0.0,
    }


def _f(x: float | int | None, digits: int = 3) -> str:
    if x is None or not np.isfinite(float(x)):
        return "n/a"
    return f"{float(x):.{digits}f}"


def _ci(ci: list[float] | None, digits: int = 3) -> str:
    if ci is None:
        return ""
    return f" [{ci[0]:.{digits}f}, {ci[1]:.{digits}f}]"


def render_markdown(res: dict[str, Any]) -> str:
    sh = res["shards"]
    out = [
        "# C4 — 离线 AUC 探针：冻结 pf 对基地将毁的线性可解码性",
        "",
        f"- 数据：`{sh['root']}` · {sh['used']}/{sh['available']} shard · "
        f"{sh['steps']} 决策步 · metrics v{sh['metrics_version']} · stages {sh['stages']}",
        f"- 特征：`{res['feature']['source']}`（sha16 `{res['feature']['sha16']}`）· "
        f"pf_dim **{res['feature']['pf_dim']}** · 消融 `{res['feature']['ablation']}` "
        f"= `pf[{res['feature']['ablation_slice'][0]}:{res['feature']['ablation_slice'][1]}]`",
        f"- 标签：`outcome=base_destroyed ∧ end_tick−t≤W`；W = "
        f"{' / '.join(str(w) for w in res['labels']['windows_ticks'])} 拍（"
        f"{' / '.join(str(int(w / res['labels']['decision_k'])) for w in res['labels']['windows_ticks'])}"
        " 决策步）",
        f"- 抽样：**全部正步 + {res['sampling']['negative_fraction']:.0%} 负步**，按局归一权重；"
        f"{res['sampling']['folds']} 折按 `{res['sampling']['group_key']}` 分组；"
        f"评估 = 留出局全部步；95% CI = 按局 cluster bootstrap {res['bootstrap']['reps']} 次",
        "",
        "## 1. 总表",
        "",
        "| W（拍） | 正步 / 步数 | prevalence | **full AUC [95% CI]** | "
        "**去 12 维 AUC [95% CI]** | Δ AUC | full AP | 去 12 维 AP | critic −V AUC [95% CI] |",
        "|---:|---:|---:|---|---|---:|---:|---:|---|",
    ]
    for w in res["labels"]["windows_ticks"]:
        r = res["windows"][str(w)]
        f = r["full"]
        a = r["ablated_base_12d"]
        c = r["critic_value"]
        out.append(
            f"| {w} | {f['n_pos']} / {f['n_steps']} | {f['prevalence']:.2%} | "
            f"**{_f(f['auc'])}**{_ci(f['auc_ci95'])} | "
            f"**{_f(a['auc'])}**{_ci(a['auc_ci95'])} | {_f(r['delta_auc'])} | "
            f"{_f(f['pr_auc'])} | {_f(a['pr_auc'])} | "
            f"{_f(c['auc'])}{_ci(c['auc_ci95'])} |"
        )
    out += [
        "",
        "## 2. 读法",
        "",
        "- **full AUC 高 = 信息在同一份输入里线性可解码**，不是“critic 已经在用”。"
        "AUC 高只证明辅助头/预热**可行**，不证明**必要**；必要性看 critic V 对照。",
        "- 消融列是 `POLICY_EXTRA[25:37]`（基地上/左/右威胁、环墙、TTI、相对向量、"
        "各侧最近敌距），在 pf 中位于尾部 `[pf_dim−57+25, pf_dim−57+37)`。",
        "- `PR-AUC` 与 prevalence 同报：三个 W 的正类率不同，**不跨 W 直接比 AP**；"
        "full / 去 12 维 / critic 三者同 W、同评估行才可比。",
        "- 评估只用留出局全部步；训练抽样只影响拟合，不改变评估 prevalence。",
        "",
        "### 2.1 机械判决（描述性，不是开腿门）",
        "",
        "| W | probe 读法 | critic 对照 |",
        "|---:|---|---|",
    ]
    for w in res["labels"]["windows_ticks"]:
        r = res["windows"][str(w)]
        out.append(f"| {w} | `{r['probe_verdict']}` | `{r['critic_vs_probe']}` |")
    out += [
        "",
        "- `signal_in_base_12d`：full 高、去 12 维掉 ≥0.05 ⇒ 辅助头/预热最对症，"
        "但先确认 critic 是否已接近。",
        "- `redundant_signal`：full 与去 12 维都高 ⇒ 信息在 obs 各处，辅助头边际低；"
        "先做 P3-A（把事件搬到当拍）。",
        "- `no_linear_signal`：full AUC < 0.60 ⇒ 表示层缺失，才谈 ch17；辅助头无信号可学。",
        "- critic 列用 **−V(s)**（低 V = 高风险）；JSON 的 `raw_value_auc` 保留原始 V 方向。"
        "`critic_underuses_signal`：probe 高而 critic 风险 AUC < 0.65 ⇒ critic 是可信的失败因子候选；"
        "`critic_ranks_near_probe` 则辅助头/预热的边际更小。",
        "",
        "## 3. 边界",
        "",
        "- 单 checkpoint（z10.it233）与单批 4 关 on-policy shard；不是跨关/跨难度结论。",
        "- 标签是**基地毁前 W 拍**，不是“守家动作是否正确”；AUC 不回答反事实。",
        "- logistic 只给线性可解码性；AUC 低不等于非线性模型也低。",
        "- 训练抽样保留全部正步会改变训练类先验；权重按局内入样概率回补，评估仍是自然 prevalence。",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = house_parser(description="C4: frozen-pf logistic AUC probe (read-only)")
    ap.add_argument("--shards", default=DEFAULT_SHARDS)
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--folds", type=int, default=N_FOLDS)
    ap.add_argument("--neg-frac", type=float, default=NEG_SAMPLE_FRAC)
    ap.add_argument("--boot", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument(
        "--max-games", type=int, default=0, help=">0 按 stage 轮转取 N 局（smoke/抽样）"
    )
    ap.add_argument("--out", default="tmp/p0-c4/auc.json")
    ap.add_argument("--md", default="tmp/p0-c4/auc.md")
    args = ap.parse_args()

    if not os.path.exists(args.weights):
        print(f"✗ 权重不存在: {args.weights}", file=sys.stderr)
        return 2
    shards = find_shards(args.shards, SHARD_FILES)
    if not shards:
        print(f"✗ 没有 shard: {args.shards}", file=sys.stderr)
        return 2
    used = select_balanced(shards, int(args.max_games)) if args.max_games > 0 else shards
    if len(used) < 2:
        print("✗ 至少需要 2 个 shard", file=sys.stderr)
        return 2

    import torch

    torch.set_num_threads(max(1, int(args.threads)))
    try:
        model = load_policy_model(args.weights)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"✗ 装载权重: {e}", file=sys.stderr)
        return 2
    pf_dim = int(model.policy_input_dim)
    try:
        ablate = threat_slice(pf_dim)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    games: list[GameRows] = []
    t0 = time.time()
    for k, d in enumerate(used, 1):
        try:
            games.append(load_game(d, model, batch=args.batch))
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            print(f"✗ {d}: {e}", file=sys.stderr)
            return 2
        if k % 10 == 0 or k == len(used):
            print(f"  [{k}/{len(used)}] {time.time() - t0:.1f}s", file=sys.stderr)
    n_folds = min(int(args.folds), max(2, len(games) // 2))
    if n_folds != int(args.folds):
        print(f"  folds {args.folds} -> {n_folds}（局数不足）", file=sys.stderr)
    try:
        folds = make_folds(games, n_folds, int(args.seed))
        res = build_report(
            games,
            folds,
            shards_root=args.shards,
            n_shards_total=len(shards),
            weights_path=args.weights,
            pf_dim=pf_dim,
            ablate=ablate,
            neg_frac=float(args.neg_frac),
            n_folds=n_folds,
            boot=int(args.boot),
            seed=int(args.seed),
        )
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        print(f"✗ 运行探针: {e}", file=sys.stderr)
        return 2
    res["elapsed_sec"] = round(time.time() - t0, 1)
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_markdown(res), encoding="utf-8")
    print(f"✓ {out}")
    print(f"✓ {md}")
    for w in WINDOWS:
        r = res["windows"][str(w)]
        print(
            f"  W={w}: full AUC={_f(r['full']['auc'])} "
            f"ablated={_f(r['ablated_base_12d']['auc'])} "
            f"critic={_f(r['critic_value']['auc'])} "
            f"probe={r['probe_verdict']} critic={r['critic_vs_probe']}"
        )
    return 0


if __name__ == "__main__":
    run_main(main)
