"""ppo-loader-bench.py —— PPO 语料装载器（eager vs lazy）的同机 A/B 台架。

口径（plan/replay-corpus.plan.md §5.1 / P3 gate，B4）：**同一份语料、同一 seed**，唯一变量 =
`--ppo-loader`。分报：

  * `load`   —— scan + GAE + 归一（lazy 不 concatenate，峰值只多常驻小字段）；
  * `gather` —— lazy 的逐 minibatch fancy-index 吞吐（MB/s，obs 字节 / 秒）；
  * `batch`  —— 每 minibatch 转 torch 张量（H2D 前的本机部分；GPU 上 `.to(device)` 的读数
               只能在目标云机量，本台架只报 CPU）；
  * `rss`    —— 进程常驻内存（psutil 可用时；否则不编数字）。

另含一条**正确性自检**：lazy 的 `gather(rows)` 与 eager 的 `concatenate()[rows]` 逐字节相同
（否则台架数字无意义）。

用法（仓根，nn venv）：
    bash tools/githook/nn-py-safe.sh tools/perf/ppo-loader-bench.py
    NN_BENCH_SHARDS=40 NN_BENCH_SAMPLES=502 NN_BENCH_MB=1024 …  # 语料规模旋钮

合成语料落 `tmp/ppo-loader-bench/`（gitignore；**每轮开跑前自清**，40×502 样本 ≈ 190 MB）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "nn-training"))

from biz.reward_context import Scoped, reset  # noqa: E402
from biz.reward_library import METRICS_DIM  # noqa: E402
from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM  # noqa: E402
from worker.ppo import np_core  # noqa: E402
from worker.ppo.lazy_corpus import load_lazy_corpus  # noqa: E402


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


SHARDS = _env_int("NN_BENCH_SHARDS", 40)
SAMPLES = _env_int("NN_BENCH_SAMPLES", 502)
MB = _env_int("NN_BENCH_MB", 1024)
BATCHES = _env_int("NN_BENCH_BATCHES", 8)
WORK = ROOT / "tmp" / "ppo-loader-bench"


def _zero_reward(m: np.ndarray, _outcome: str, _score: float, _it: int) -> np.ndarray:
    """加载期奖励：全 0（台架只量装载，奖励公式不是变量）。"""
    return np.zeros(int(m.shape[0]) - 1, dtype=np.float32)


def _write_shard(root: Path, name: str, n: int, *, stage: int, seed: int) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed * 7919 + n)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.random((n, SCALAR_DIM), dtype=np.float32))
    np.save(d / "extra.npy", rng.random((n, POLICY_EXTRA_DIM), dtype=np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4, n, dtype=np.int64))
    np.save(d / "a_fire.npy", rng.integers(0, 3, n, dtype=np.int64))
    np.save(d / "a_hold.npy", rng.integers(0, 2, n, dtype=np.int64))
    np.save(d / "lp_move.npy", rng.random(n).astype(np.float32) - 2.0)
    np.save(d / "lp_fire.npy", rng.random(n).astype(np.float32) - 2.0)
    np.save(d / "lp_hold.npy", rng.random(n).astype(np.float32) - 2.0)
    np.save(d / "value.npy", rng.random(n).astype(np.float32) * 0.1)
    np.save(d / "metrics.npy", np.zeros((n + 1, METRICS_DIM), dtype=np.float64))
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 9), dtype=np.int64))
    (d / "manifest.json").write_text(
        json.dumps({"stage": stage, "nSamples": n, "outcome": "timeout", "score": 0.0}),
        encoding="utf-8",
    )


def _rss_mb() -> float | None:
    try:
        import psutil
    except Exception:
        return None
    return psutil.Process().memory_info().rss / (1 << 20)


def main() -> None:
    reset()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for i in range(SHARDS):
        _write_shard(WORK, f"rl_s{i}_seed1", SAMPLES, stage=i % 35, seed=i + 1)
    n = SHARDS * SAMPLES
    obs_bytes = n * OBS_CHANNELS * BOARD * BOARD
    print(
        f"[bench] 语料 {SHARDS} shards × {SAMPLES} = {n} 样本 · "
        f"obs {obs_bytes / (1 << 20):.0f} MB · mb={MB} · 合成 {time.time() - t0:.1f}s"
    )

    out: dict[str, object] = {"samples": n, "mb": MB, "obsMB": round(obs_bytes / (1 << 20), 1)}

    # ---- eager：concatenate 全量（旧路径）----
    np.random.seed(7)
    rss0 = _rss_mb()
    t = time.time()
    with Scoped(reward_fn=_zero_reward, it=1):
        eps_e = np_core.load_episodes(str(WORK))
    out["eagerLoadSec"] = round(time.time() - t, 2)
    rss_after_eager = _rss_mb()
    out["eagerRssMB"] = None if rss0 is None or rss_after_eager is None else round(rss_after_eager - rss0, 0)

    # ---- lazy：逐 shard mmap + 常驻小字段 ----
    np.random.seed(7)
    rss0 = _rss_mb()
    t = time.time()
    with Scoped(reward_fn=_zero_reward, it=1):
        corpus = load_lazy_corpus(str(WORK), mb=MB)
    out["lazyLoadSec"] = round(time.time() - t, 2)
    rss_after_lazy = _rss_mb()
    out["lazyRssMB"] = None if rss0 is None or rss_after_lazy is None else round(rss_after_lazy - rss0, 0)

    # ---- 正确性自检：同一批行号，lazy gather == eager concatenate ----
    rows_all = np.random.permutation(n)
    batch_rows = [rows_all[i * MB : (i + 1) * MB] for i in range(BATCHES)]
    gate = batch_rows[0]
    lazy_obs = corpus.gather(gate)["obs"]
    eager_obs = np.concatenate([e["obs"] for e in eps_e])[gate]
    out["sameObs"] = bool(np.array_equal(lazy_obs, eager_obs))

    # ---- lazy gather 吞吐（随机行，含跨 shard 分组）----
    t = time.time()
    for r in batch_rows:
        corpus.gather(r)
    sec = time.time() - t
    moved = sum(int(r.shape[0]) for r in batch_rows) * OBS_CHANNELS * BOARD * BOARD
    out["gatherSec"] = round(sec, 3)
    out["gatherMBs"] = round(moved / (1 << 20) / max(sec, 1e-9), 0)

    # ---- 每 minibatch 转张量（CPU；GPU 的 H2D 只能在目标云机量）----
    try:
        import torch

        from worker.ppo.trainer import tensored_batch

        dev = torch.device("cpu")
        gathered = [corpus.gather(r) for r in batch_rows]
        t = time.time()
        for b in gathered:  # 真实形态：`ppo_update` 每步转一批（tensored_batch 收单批）
            _ = tensored_batch(b, dev)
        out["batchSec"] = round(time.time() - t, 3)
    except Exception as e:  # torch 不在/版本差异：响亮记下，不编数字
        out["batchSec"] = f"skip: {type(e).__name__}: {e}"

    print(json.dumps(out, ensure_ascii=False))
    if out.get("sameObs") is not True:
        raise SystemExit("[bench] lazy 与 eager 的 obs 不一致——台架数字作废")
    reset()


if __name__ == "__main__":
    main()
