"""
run_rl.py — RL on-policy 主循环（P1.5 蒸馏 → RL 阶段）。

流程：
  ① 权重初始化（幂等）：RL 权重不存在时，从 DAgger BC 检查点 warm-start 策略头
     （价值头随机初始化）；已存在则直接续跑。
  ② 迭代 N 次：bun TS rollout（subprocess，无需 torch）→ 进程内 clipped PPO 更新
     （复用 ppo.py 的 GAE/minibatch/更新函数，模型常驻内存）→ 原子写回权重文件，
     下一轮 rollout 即用新权重（标准 on-policy）。

取代 run_rl.sh：循环逻辑单点实现于 Python（跨平台），经统一启动器进入——
venv/torch 由启动器保证，bun 由本脚本在 PATH 上定位：

  bash nn-training/start-training.sh --script run_rl.py --iters 15
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl.py --iters 15          # --xxx 参数原样透传

单步调试仍可用 ppo.py 的 --init-from / --resume CLI。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import torch

import ppo as ppo_mod
from weights_io import load_state_into, save_weights_json

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_range(s: str) -> list[int]:
    """'0-3' / '0,2,5' / '0-1,4' → [0,1,2,3] / [0,2,5] / [0,1,4]."""
    out: list[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def build_model(bc_path: str, rl_path: str) -> torch.nn.Module:
    """Init once: warm-start policy heads from BC when no RL weights exist yet;
    otherwise resume from the existing RL weights (policy + trained value).
    The init path SAVES the merged weights to rl_path before returning — the
    TS rollout reads that file, so it must exist before iteration 1."""
    resume = os.path.exists(rl_path)
    src = rl_path if resume else bc_path
    model = ppo_mod.build_ppo(src)
    load_state_into(model, src)
    if not resume:
        save_weights_json(model, rl_path)
    print(
        f"[run_rl] {'resume' if resume else 'init'} weights "
        f"<- {src} "
        f"(params={sum(int(p.numel()) for p in model.parameters())})"
        + ("" if resume else f" -> {rl_path}")
    )
    return model


def run_rollout(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]], args) -> dict:
    """Run one rollout generation into traj_dir with up to W concurrent bun
    processes over the given (stage, seed) game pairs.

    Each game is one single-threaded bun process writing shards under
    traj_dir/w{i}/ — disjoint by construction, and discover_rl_shards() scans
    recursively, so the PPO side needs no knowledge of the layout. Per-game
    granularity saturates all cores regardless of how few stages/seeds the
    sweep has (bun startup ~300ms is noise vs a 12000-tick game).
    Returns the aggregated report dict.
    """
    from concurrent.futures import ThreadPoolExecutor

    workers = max(1, min(args.workers, len(pairs)))

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        log_f = open(wdir / "rollout.log", "w", encoding="utf-8")
        cmd = [
            bun,
            "tools/sim/export-rl-rollout.ts",
            "--weights", rl_path,
            "--out", str(wdir),
            "--stages", str(si),
            "--seeds", str(seed),
            "--max-ticks", str(args.max_ticks),
            "--difficulty", args.difficulty,
        ]
        p = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
        rc = p.wait()
        log_f.close()
        report = None
        if rc == 0:
            report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run_one, i, si, sd) for i, (si, sd) in enumerate(pairs)]
        results = [f.result() for f in futures]

    failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
    if failed:
        tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
        raise SystemExit(f"[run_rl] rollout worker(s) {failed} failed:\n{tail}")

    combined = {"games": 0, "winRate": 0.0, "outcomes": {}, "totalSamples": 0, "totalTicks": 0}
    wins = 0
    for _rc, r in results:
        combined["games"] += r["games"]
        combined["totalSamples"] += r["totalSamples"]
        combined["totalTicks"] += r["totalTicks"]
        for o, c in r["outcomes"].items():
            combined["outcomes"][o] = combined["outcomes"].get(o, 0) + c
        wins += r["outcomes"].get("stage_clear", 0)
    combined["winRate"] = round(wins / combined["games"], 4) if combined["games"] else 0.0
    return combined


def build_pairs(args, it: int, rng) -> list[tuple[int, int]]:
    """Game pairs for iteration `it`. Rotate mode: a deterministic window of
    --rotate-stages stages (full coverage every ceil(35/N) iterations) x fresh
    random seeds drawn from a (seed, iter)-derived Generator — reproducible via
    --seed, yet never replaying a previous iteration's configurations."""
    if args.rotate_stages <= 0:
        return [(si, sd) for si in parse_range(args.stages) for sd in parse_range(args.seeds)]
    start = ((it - 1) * args.rotate_stages) % args.total_stages
    window = [(start + j) % args.total_stages for j in range(args.rotate_stages)]
    draw = rng.integers(1, 2 ** 30, size=args.rotate_stages * args.seeds_per_stage)
    pairs = [
        (stage, int(draw[k * args.seeds_per_stage + j]))
        for k, stage in enumerate(window)
        for j in range(args.seeds_per_stage)
    ]
    print(f"[run_rl] rotate: stages {window[0]}-{window[-1]} "
          f"(seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})")
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default="tmp/student-weights-dagger/weights.json",
                    help="BC checkpoint to warm-start from (first init only)")
    ap.add_argument("--out", default="tmp/rl-weights/weights.json",
                    help="RL weights path (written every iteration; also the resume source)")
    ap.add_argument("--traj", default="tmp/rl-traj", help="trajectory root dir")
    ap.add_argument("--iters", type=int, default=15)
    ap.add_argument("--stages", default="0-3", help="explicit stage range (ignored in rotate mode)")
    ap.add_argument("--seeds", default="0-3", help="explicit seed range (ignored in rotate mode)")
    ap.add_argument("--rotate-stages", type=int, default=0,
                    help=">0: rotate through ALL stages this many per iteration "
                         "(iteration i uses stages [(i-1)*N %% 35 ...]); seeds are drawn "
                         "fresh every iteration from a (seed, iter)-derived RNG")
    ap.add_argument("--seeds-per-stage", type=int, default=10,
                    help="random seeds per stage in rotate mode")
    ap.add_argument("--total-stages", type=int, default=35,
                    help="stage count for rotate mode (repo has 35)")
    ap.add_argument("--difficulty", default="hard")
    ap.add_argument("--max-ticks", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 12),
                    help="concurrent bun rollout workers (games partitioned by seed)")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=512,
                    help="minibatch size — 512 halves gradient steps vs 256 "
                         "(faster PPO, smaller per-iteration KL drift)")
    ap.add_argument("--lr", type=float, default=ppo_mod.LR)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import numpy as np

    np.random.seed(args.seed)
    rotate_rng = np.random.default_rng(args.seed * 1009 + 1)  # advanced per iteration below

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl] bun not found on PATH — rollout needs it")

    device = torch.device("cpu")
    model = build_model(args.bc, args.out)
    model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)

    print(f"[run_rl] iters={args.iters} "
          + (f"rotate={args.rotate_stages}stages x{args.seeds_per_stage}seeds "
             f"of {args.total_stages}" if args.rotate_stages > 0
             else f"stages={args.stages} seeds={args.seeds}")
          + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
          f"workers={args.workers}")

    for it in range(1, args.iters + 1):
        traj_dir = traj_root / f"it{it}"
        if traj_dir.exists():
            shutil.rmtree(traj_dir)
        traj_dir.mkdir(parents=True)

        print(f"[run_rl] === iteration {it}/{args.iters} ===")
        pairs = build_pairs(args, it, rotate_rng)
        report = run_rollout(bun, args.out, traj_dir, pairs, args)
        print(f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
              f"outcomes={json.dumps(report['outcomes'])} "
              f"samples={report['totalSamples']} ticks={report['totalTicks']}")

        episodes = ppo_mod.load_episodes(str(traj_dir))
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = ppo_mod.chunk_episodes(episodes, args.mb)
        agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, device)
        save_weights_json(model, args.out)
        print(f"[run_rl] ppo it{it}: steps={total_steps} chunks={len(chunks)} "
              f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
              f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}")

    print(f"[run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
