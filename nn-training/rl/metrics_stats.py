"""metrics_stats —— 每 iter 指标统计量落盘（plan §4.7 / M1b）。

`<traj>/metrics_stats.jsonl`：21 维每维 {mean,min,max,p50,p05,p95,std} + 派生
episode_return / step_reward / term_contribution（顶层 `+−` 拆项占比）。

时机（评审 P1-4）：numpy 向量化算 21×9 万步精确分位 + 一次 reward 求值 ≈ 几十
ms，**inline 算完对 PPO 完全无感**；默认 inline，不预设线程/队列复杂度
（profiling >200ms 才升级后台 worker——当前不实现，留 TODO 注释）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from rl.reward_library import METRICS

_STAT_KEYS = ("mean", "min", "max", "p50", "p05", "p95", "std")


def row_stats(arr: np.ndarray) -> dict[str, float]:
    """单维统计量（跳过 NaN）。"""
    a = arr[np.isfinite(arr)]
    if a.size == 0:
        return {k: 0.0 for k in _STAT_KEYS}
    return {
        "mean": float(np.mean(a)),
        "min": float(np.min(a)),
        "max": float(np.max(a)),
        "p50": float(np.median(a)),
        "p05": float(np.percentile(a, 5)),
        "p95": float(np.percentile(a, 95)),
        "std": float(np.std(a)),
    }


def episode_returns(rewards: Sequence[np.ndarray]) -> list[float]:
    return [float(np.sum(r)) for r in rewards]


def metrics_stats(
    iter_dir: str,
    *,
    episode_rewards: Sequence[np.ndarray] | None = None,
    it: int = 0,
    identity: dict[str, Any] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """对 `iter_dir` 下全部 shard 的 metrics.npy 聚合统计。

    - 若 `episode_rewards` 为空，则自行加载（无 holder 也能统计——公式引擎
      不一定需要参与：统计口径是**指标向量**本身）。
    - `term_contribution`：terminal（末样本 reconcile）在总回报里的占比。
    """
    t0 = time.time()
    import glob

    m_list: list[np.ndarray] = []
    rewards = list(episode_rewards) if episode_rewards is not None else []
    n_shards = 0
    # 递归扫：实际布局有 it{N}/rl_s*_seed*（queue）与 it{N}/w*/rl_s*_seed*
    # 及 it{N}/dist/self/rl_s*_seed*（dist local slot）三种嵌套层级。
    for p in sorted(glob.glob(str(Path(iter_dir) / "**" / "metrics.npy"), recursive=True)):
        if "/rl_" not in p and "\\rl_" not in p:
            continue
        m = np.load(p)
        if m.ndim != 2 or m.shape[1] != len(METRICS):
            continue
        m_list.append(m)
        n_shards += 1
    if not m_list:
        return {"it": it, "shards": 0, "elapsed_ms": 0.0}
    stacked = np.concatenate([m[:-1] for m in m_list], axis=0)  # 决策步行（终局行除外）
    per_dim = {name: row_stats(stacked[:, i]) for i, name in enumerate(METRICS)}

    out: dict[str, Any] = {
        "it": it,
        "shards": n_shards,
        "decision_steps": int(stacked.shape[0]),
        "dim": per_dim,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }
    if identity:
        out["reward_identity"] = identity
    if rewards:
        rets = [float(np.sum(r)) for r in rewards]
        out["episode_return"] = row_stats(np.asarray(rets, dtype=np.float64))
        allr = np.concatenate([np.asarray(r, dtype=np.float64) for r in rewards])
        out["step_reward"] = row_stats(allr)
        # terminal 贡献：每个 episode 末样本之和 / 总回报（r[−1] 已含 reconcile）
        term_parts = [float(np.asarray(r, dtype=np.float64)[-1]) for r in rewards if len(r) > 0]
        total_ret = sum(rets)
        out["term_contribution"] = (
            round(sum(term_parts) / total_ret, 6) if abs(total_ret) > 1e-12 else 0.0
        )
    if write:
        path = Path(iter_dir) / "metrics_stats.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return out
