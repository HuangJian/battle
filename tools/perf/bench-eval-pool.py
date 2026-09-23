"""bench-eval-pool.py —— 云机离线 **eval** 腿的长驻池 A/B 台架（`remote/serve_pool.py`）。

口径：真 bun + 真导出器（`tools/sim/export-eval-game.ts`）+ 真权重，n 局并发与
`remote/offline_eval.run_cloud_eval` 同构（ThreadPoolExecutor + 同一个
`rl/eval_local.run_local_eval_game`），唯一变量 = 有没有池（`NN_SERVE_POOL=0` 关）。每档
**pool / spawn 交替跑两轮**（既有纪律：交错防频率漂移），并**逐字段对比**两臂的
`_eval_report.json`（`elapsedSec` 除外）+ `ticks` ⇒ 池只许更快、不许改产物。

用法（仓根）：
    bash tools/githook/nn-py-safe.sh tools/perf/bench-eval-pool.py                  # 3 关 × 8 种子
    EV_BENCH_STAGES=0,1,2,3 EV_BENCH_SEEDS=12 EV_BENCH_WORKERS=12 ...               # 其它口径

实测（win32-x64 / 5800H 16 线程 / workers=12 / max-ticks 12900）：
    24 局（2 局/lane）**1.19–1.27×**；48 局（4 局/lane）**≈1.4×** —— 与 rollout 腿同律
    （收益 ∝ 每 lane 局数；见 `docs/nn/runtime-opt.md` §22.2 / §22.7）。

产出落在 `tmp/bench-eval-pool/`（gitignore；每臂开跑前自清）：跑一臂 = 真跑一轮评估。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # tools/perf/ 往上是仓根
sys.path.insert(0, str(ROOT / "nn-training"))

import remote.serve_pool as serve_pool  # noqa: E402
from rl.eval_local import run_local_eval_game  # noqa: E402


def _ints(name: str, default: str) -> tuple[int, ...]:
    raw = (os.environ.get(name) or default).strip()
    return tuple(int(x) for x in raw.split(",") if x.strip())


STAGES = _ints("EV_BENCH_STAGES", "0,1,2")
SEEDS = _ints("EV_BENCH_SEEDS", ",".join(str(i) for i in range(8)))
WORKERS = int((os.environ.get("EV_BENCH_WORKERS") or "12").strip() or 12)
ROUNDS = int((os.environ.get("EV_BENCH_ROUNDS") or "2").strip() or 2)
MAX_TICKS = int((os.environ.get("EV_BENCH_MAX_TICKS") or "12900").strip() or 12900)

_WEIGHT_CANDIDATES = (
    "weights/bc-c4-v3/bc-c4-v3.it1.20260914-103830.json",
    "weights/bc-c4/bc-c4.it1.20260913-161311.json",
)


def _find_weights() -> Path:
    for rel in _WEIGHT_CANDIDATES:
        p = ROOT / "nn-training" / rel
        if p.exists():
            return p
    cands = sorted((ROOT / "nn-training" / "weights").glob("*/*.json"))
    if not cands:
        raise SystemExit("找不到权重（nn-training/weights/*/）——本台架需要真权重")
    return cands[0]


def arm(tag: str, with_pool: bool, base: Path, pairs: list[tuple[int, int]], weights: Path,
        bun: str, ready_hook=None) -> dict:
    root = base / tag
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    msgs: list[str] = []
    pool = None
    if with_pool:
        os.environ.pop(serve_pool.ENV_SWITCH, None)
        pool = serve_pool.make_pool(bun, serve_pool.EVAL_SCRIPT, ROOT, WORKERS, msgs.append)
        if pool is None:
            raise SystemExit("池没建起来（白名单/开关？）")
        ready = pool.start()
        print(f"  [{tag}] pool ready {ready}/{WORKERS} spawned={pool.spawned}", flush=True)
    else:
        os.environ[serve_pool.ENV_SWITCH] = "0"

    def one(task: tuple[int, int]) -> dict:
        stage, seed = task
        return run_local_eval_game(
            bun, str(weights), stage, seed, root / f"eval-s{stage}-d{seed}", MAX_TICKS,
            "hard", 600.0, "W" * 16, cwd=str(ROOT), log_fn=msgs.append, attempt=1, pool=pool,
        )

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            mans = list(ex.map(one, pairs))
    finally:
        if pool is not None:
            print(f"  [{tag}] {pool.summary()}", flush=True)
            pool.close()
    return {
        "tag": tag, "wall": round(time.time() - t0, 3), "mans": mans,
        "ticks": sum(int(m.get("ticks") or 0) for m in mans),
        "wins": sum(1 for m in mans if m.get("win")), "root": root,
        "pool": None if pool is None else {
            "served": pool.served, "spawned": pool.spawned, "fallback": pool.fallback,
            "reasons": dict(pool.fallback_reasons),
        },
    }


def reports(root: Path) -> dict[str, dict]:
    """逐局 `_eval_report.json`（去掉 `elapsedSec` —— 它是唯一允许不同的字段）。"""
    out: dict[str, dict] = {}
    for p in sorted(root.glob("eval-s*-d*/_eval_report.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        d.pop("elapsedSec", None)
        out[p.parent.name] = d
    return out


def main() -> int:
    weights = _find_weights()
    bun = shutil.which("bun")
    if not bun:
        raise SystemExit("找不到 bun")
    pairs = [(s, d) for s in STAGES for d in SEEDS]
    print(f"weights={weights.name} games={len(pairs)} workers={WORKERS} rounds={ROUNDS} "
          f"max_ticks={MAX_TICKS}", flush=True)
    base = ROOT / "tmp" / "bench-eval-pool"
    base.mkdir(parents=True, exist_ok=True)
    bad = 0
    for rnd in range(1, ROUNDS + 1):
        res: dict[str, dict] = {}
        for with_pool in (True, False):
            tag = f"{'pool' if with_pool else 'spawn'}-r{rnd}"
            res[tag] = arm(tag, with_pool, base, pairs, weights, bun)
            r = res[tag]
            print(f"{tag}: wall={r['wall']}s games={len(r['mans'])} ticks={r['ticks']} "
                  f"wins={r['wins']} pool={r['pool']}", flush=True)
        p, s = res[f"pool-r{rnd}"], res[f"spawn-r{rnd}"]
        same = reports(p["root"]) == reports(s["root"]) and [m.get("ticks") for m in p["mans"]] == [
            m.get("ticks") for m in s["mans"]
        ]
        bad += 0 if same else 1
        print(f"round {rnd}: spawn/pool = {s['wall'] / p['wall']:.3f}x "
              f"({s['wall']}s -> {p['wall']}s)  产物逐字段相同: {same}", flush=True)
    if bad:
        print(f"✗ {bad} 轮的评估报告不一致（池改了语义？）")
        return 1
    print("✓ 所有轮：评估报告逐字段相同（elapsedSec 除外）+ ticks 相同")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
