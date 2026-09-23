"""bench-iter-pool.py —— 节点侧长驻池（`nn-training/remote/serve_pool.py`）的 A/B 台架。

口径（与 `nn-training/tests/test_remote_iter_real_bun.py` 同源）：**真 bun + 真导出器 + 真权重**，
同一份 `build_iter_spec` 出来的 spec，唯一变量 = `NN_SERVE_POOL`（0 = 逐局 spawn，旧行为）。
每档 **pool / spawn 交替跑两轮**（交错是防频率漂移的既有纪律），并断言两臂 `data_fp` 相同
（池只许更快、不许改变产物）。

用法（仓根）：
    bash tools/githook/nn-py-safe.sh tools/perf/bench-iter-pool.py            # 默认 8,16,32 局
    NN_BENCH_GAMES=328 bash tools/githook/nn-py-safe.sh tools/perf/bench-iter-pool.py
    NN_BENCH_WORKERS=8 NN_BENCH_MAX_TICKS=12900 …                            # 其它耦合旋钮

产出落在 `tmp/bench-iter-pool/`（gitignore；**每臂开跑前自清**，但 328 局一轮 ≈3GB ——
跑完自行 `rm -rf`）：跑一臂 = 真跑一整轮 rollout，shard 真的写盘。

实测（win32-x64 / 5800H 16 线程 / workers=8 / max-ticks 12900 / stage 0）：
    8 局 1.10–1.14× · 16 局 1.20–1.22× · 32 局 1.42–1.47× · **328 局（生产形态）1.45–1.47×**
    （每 lane 局数越多摊得越好；结论与复跑说明见 `docs/nn/runtime-opt.md` §22）。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent  # tools/perf/ 往上是仓根
sys.path.insert(0, str(ROOT / "nn-training"))

import remote.iter_rollout as iter_rollout  # noqa: E402
import remote.serve_pool as serve_pool  # noqa: E402
from remote.protocol import data_fp, validate_rollout_spec  # noqa: E402
from rl.iter_job import build_iter_spec  # noqa: E402


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


GAME_COUNTS = tuple(
    int(x) for x in (os.environ.get("NN_BENCH_GAMES") or "8,16,32").split(",") if x.strip()
)
WORKERS = _env_int("NN_BENCH_WORKERS", 8)
MAX_TICKS = _env_int("NN_BENCH_MAX_TICKS", 12900)
ROUNDS = _env_int("NN_BENCH_ROUNDS", 2)

#: 与 real-bun 验收测试同一批候选（找不到就退到任意一份 json —— 只要两臂用的是同一份）。
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


def _spec(args: SimpleNamespace, bun: str, n: int) -> dict:
    """n 局的 iter spec（hub 侧同一个拼装函数）——逐局 (stage, seed) = (0, 0..n-1)。"""
    return validate_rollout_spec(
        build_iter_spec(args, [(0, s) for s in range(n)], wver="W" * 64, workers=WORKERS,
                        hub_bun=bun)
    )


def run_arm(tag: str, enabled: bool, base: Path, spec: dict, weights: Path) -> dict:
    """跑一臂：`enabled=False` 时设 `NN_SERVE_POOL=0`（= 本批之前的逐局 spawn 行为）。"""
    if enabled:
        os.environ.pop(serve_pool.ENV_SWITCH, None)
    else:
        os.environ[serve_pool.ENV_SWITCH] = "0"
    job = base / tag
    if job.exists():
        shutil.rmtree(job)
    job.mkdir(parents=True)
    shutil.copyfile(weights, job / "init_weights.json")
    msgs: list[str] = []
    t0 = time.time()
    out = iter_rollout.run_iter_rollout(job, spec, ts_dir=ROOT, log=msgs.append)
    wall = time.time() - t0
    return {
        "tag": tag,
        "wall": round(wall, 3),
        "games": out["report"]["games"],
        "ticks": out["report"]["totalTicks"],
        "fp": data_fp([Path(p) for p in out["shard_dirs"]]),
        "pool": out["serve_pool"],
        "msgs": msgs,
    }


def main() -> int:
    weights = _find_weights()
    bun = shutil.which("bun")
    if not bun:
        raise SystemExit("找不到 bun")
    args = SimpleNamespace(
        goal_rollout=False, intent_rollout=False, max_ticks=MAX_TICKS, difficulty="hard",
        dodge="", course_obj=None, course_path="", course_frozen_bytes=None,
        lives_override=1, player_level=0,
    )
    print(f"weights={weights.name} workers={WORKERS} rounds={ROUNDS} "
          f"max_ticks={MAX_TICKS} 局数档={GAME_COUNTS}", flush=True)
    base = ROOT / "tmp" / "bench-iter-pool"
    base.mkdir(parents=True, exist_ok=True)
    bad = 0
    for n in GAME_COUNTS:
        spec = _spec(args, bun, n)
        res: dict[str, dict] = {}
        for rnd in range(1, ROUNDS + 1):
            for enabled in (True, False):
                arm = "pool" if enabled else "spawn"
                r = run_arm(f"{n}-{arm}-r{rnd}", enabled, base, spec, weights)
                res[r["tag"]] = r
                print(f"{r['tag']}: wall={r['wall']}s games={r['games']} "
                      f"ticks={r['ticks']} fp={r['fp'][:16]} pool={r['pool']}", flush=True)
        for rnd in range(1, ROUNDS + 1):
            p = res[f"{n}-pool-r{rnd}"]
            s = res[f"{n}-spawn-r{rnd}"]
            same = p["fp"] == s["fp"] and p["ticks"] == s["ticks"]
            bad += 0 if same else 1
            print(f"games={n} round {rnd}: spawn/pool = {s['wall'] / p['wall']:.3f}x "
                  f"({s['wall']}s -> {p['wall']}s)  产物相同: {same}", flush=True)
    last = res[sorted(res)[0]]
    print("pool 日志样本:", [m for m in last["msgs"] if "serve_pool" in m or "池" in m])
    if bad:
        print(f"✗ {bad} 个档的产物不一致（池改了语义？）")
        return 1
    print("✓ 所有档：产物逐位相同（data_fp + ticks）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
