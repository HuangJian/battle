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
import sys
import time
from pathlib import Path

import torch

import ppo as ppo_mod
from weights_io import load_state_into, save_weights_json

REPO_ROOT = Path(__file__).resolve().parents[1]

KL_WARN = 0.08        # calibrated to our setup: healthy steady state is 0.045-0.054
ENT_COLLAPSE_DROP = 0.10  # single-iteration entropy drop that warrants a warning

# F4 circuit breaker (2026-08-22): warnings had no teeth — the R3 long run kept
# going ~60 iterations past behavioral collapse because KL>0.15 never persisted
# two consecutive iterations before it100 (spikes at it65/73/79 were singles).
# Two trip rules, either fires:
#   KL:  kl >= KL_BREAK for KL_BREAK_CONSEC consecutive iterations (violent drift)
#   ENT: entropy <= ENT_BREAK for ENT_BREAK_CONSEC consecutive iterations AND
#        winRate < ENT_BREAK_MAX_WINRATE (degenerate determinism; the R3 collapse
#        sat at 0.42-0.55 for ~60 iterations). The winRate guard avoids stopping
#        a legitimately converged high-winning policy.
# Historical check against the R3 collapse: ENT rule trips ~it70 (11 consecutive
# it63-it73 below 0.60); KL rule alone would only fire at it100 — KL is a
# lagging indicator, entropy is the leading one.
KL_BREAK = 0.15
KL_BREAK_CONSEC = 3
ENT_BREAK = 0.60
ENT_BREAK_CONSEC = 8
ENT_BREAK_MAX_WINRATE = 0.5
CIRCUIT_EXIT_CODE = 3


def log(msg: str) -> None:
    """Timestamped stdout line — the training log must be analyzable over time."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
        f"[{time.strftime('%H:%M:%S')}] [run_rl] "
        + ("resume" if resume else "init")
        + f" weights <- {src} "
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

    reports = [r for _rc, r in results if r is not None]
    combined = {"games": 0, "winRate": 0.0, "outcomes": {}, "totalSamples": 0, "totalTicks": 0,
                "scoreList": [], "dimLists": {}}
    wins = 0
    for r in reports:
        combined["games"] += r["games"]
        combined["totalSamples"] += r["totalSamples"]
        combined["totalTicks"] += r["totalTicks"]
        for o, c in r.get("outcomes", {}).items():
            combined["outcomes"][o] = combined["outcomes"].get(o, 0) + c
            if o == "stage_clear":
                wins += c
        combined["scoreList"].extend(r.get("scoreList", []))
        for k, vs in r.get("dimLists", {}).items():
            combined["dimLists"].setdefault(k, []).extend(vs)
    combined["winRate"] = round(wins / combined["games"], 4) if combined["games"] else 0.0
    sl = combined["scoreList"]
    if sl:
        n = len(sl)
        mean = sum(sl) / n
        var = sum((x - mean) ** 2 for x in sl) / max(1, n - 1)
        combined["scoreStats"] = {"mean": round(mean, 4), "std": round(var ** 0.5, 4),
                                  "min": round(min(sl), 4), "max": round(max(sl), 4)}
    combined["dimMeans"] = {k: round(sum(v) / len(v), 4)
                            for k, v in combined["dimLists"].items() if v}
    return combined


def build_pairs(args, it: int, rng, perm_state: dict) -> list[tuple[int, int]]:
    """Game pairs for iteration `it`. Rotate mode: shuffled batches — a fresh
    random permutation of all --total-stages stages is drawn once per epoch and
    sliced into ceil(total/N) consecutive batches, so full coverage holds every
    epoch while batch composition/order varies (immune to restart-position bias).
    Seeds are fresh random draws; reproducible via --seed within one launch."""
    if args.rotate_stages <= 0:
        return [(si, sd) for si in parse_range(args.stages) for sd in parse_range(args.seeds)]
    k = args.rotate_stages
    per_epoch = -(-args.total_stages // k)
    pos = (it - 1) % per_epoch
    if pos == 0 or "perm" not in perm_state:
        perm_state["perm"] = list(rng.permutation(args.total_stages))
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: new epoch — permutation "
              f"{perm_state['perm']} split into {per_epoch} batches")
    window = [int(s) for s in perm_state["perm"][pos * k:(pos + 1) * k]]
    draw = rng.integers(1, 2 ** 30, size=len(window) * args.seeds_per_stage)
    pairs = [
        (stage, int(draw[i * args.seeds_per_stage + j]))
        for i, stage in enumerate(window)
        for j in range(args.seeds_per_stage)
    ]
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: batch {pos + 1}/{per_epoch} "
          f"stages={window} (seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})")
    return pairs


def main() -> None:
    # Anchor cwd to the repo root (parent of nn-training/): all default paths
    # (tmp/student-weights-dagger, tmp/rl-weights, tmp/rl-traj) are repo-root
    # relative. Required for start-training.ps1 --detach, whose WorkingDirectory
    # is nn-training/ — same pattern as train_loop.py's REPO_ROOT.
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default="tmp/student-weights-dagger/weights.json",
                    help="BC checkpoint to warm-start from (first init only)")
    ap.add_argument("--out", default="tmp/rl-weights/weights.json",
                    help="RL weights path (written every iteration; also the resume source)")
    ap.add_argument("--traj", default="tmp/rl-traj", help="trajectory root dir")
    ap.add_argument("--iters", type=int, default=15,
                    help="iterations to run; 0 = infinite (stop via --max-hours or Ctrl-C)")
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
    ap.add_argument("--max-hours", type=float, default=0.0,
                    help="wall-clock budget in hours; checked between iterations; 0 = unlimited")
    ap.add_argument("--keep-iters", type=int, default=3,
                    help="keep only the last N trajectory dirs (disk bound); 0 = keep all")
    args = ap.parse_args()

    import numpy as np

    np.random.seed(args.seed)
    # 启动时刻抖动：每次 relaunch 得到不同的关卡置换序列，避免重启重放同一课程
    rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)
    rotate_rng = np.random.default_rng(rotate_seed)  # advanced per iteration below
    perm_state: dict = {}

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl] bun not found on PATH — rollout needs it")

    device = torch.device("cpu")
    model = build_model(args.bc, args.out)
    model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = traj_root / "training_log.jsonl"

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()},
            "rotateSeed": rotate_seed,
        }) + "\n")

    log(f"[run_rl] iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
           f"of {args.total_stages} (full coverage every "
           f"{-(-args.total_stages // args.rotate_stages)} iters)" if args.rotate_stages > 0
           else f"stages={args.stages} seeds={args.seeds}")
        + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
        f"workers={args.workers} keepIters={args.keep_iters}")
    log(f"training_log: {jsonl_path}")

    deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
    total = "∞" if args.iters <= 0 else str(args.iters)
    prev_entropy = None
    consec_fail = 0
    kl_streak = 0   # F4: consecutive iters with kl >= KL_BREAK
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < ENT_BREAK_MAX_WINRATE
    tripped = None
    it = 0
    while args.iters <= 0 or it < args.iters:
        it += 1
        if deadline is not None and time.time() >= deadline:
            log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
            break
        traj_dir = traj_root / f"it{it}"
        try:
            if traj_dir.exists():
                shutil.rmtree(traj_dir)
            traj_dir.mkdir(parents=True)

            log(f"[run_rl] === iteration {it}/{total} ===")
            pairs = build_pairs(args, it, rotate_rng, perm_state)
            report = run_rollout(bun, args.out, traj_dir, pairs, args)
            log(f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}")
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}")
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            episodes = ppo_mod.load_episodes(str(traj_dir))
            total_steps = sum(e["obs"].shape[0] for e in episodes)
            chunks = ppo_mod.chunk_episodes(episodes, args.mb)
            agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, device)
            save_weights_json(model, args.out)
            log(f"[run_rl] ppo it{it}: steps={total_steps} chunks={len(chunks)} "
                f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}")

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(json.dumps({
                    "event": "iteration", "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "winRate": report["winRate"], "outcomes": report["outcomes"],
                    "score_mean": report.get("scoreStats", {}).get("mean"),
                    "score_std": report.get("scoreStats", {}).get("std"),
                    "dim_means": report.get("dimMeans", {}),
                    "samples": report["totalSamples"], "ticks": report["totalTicks"],
                    "steps": total_steps, "chunks": len(chunks),
                    "policy": agg["policy"], "value": agg["value"],
                    "entropy": agg["entropy"], "kl": agg["kl"],
                    "mean_ret": agg["mean_ret"], "lr": args.lr,
                    "mb": args.mb, "epochs": args.epochs,
                }) + "\n")

            # F4 circuit breaker — the teeth behind KL_WARN/entropy warnings.
            # break (not raise): the except handlers below would swallow and retry.
            kl_streak = kl_streak + 1 if agg["kl"] >= KL_BREAK else 0
            ent_streak = (ent_streak + 1
                          if agg["entropy"] <= ENT_BREAK
                          and report["winRate"] < ENT_BREAK_MAX_WINRATE
                          else 0)
            if kl_streak >= KL_BREAK_CONSEC:
                tripped = f"kl>={KL_BREAK} for {kl_streak} consecutive iters (now {agg['kl']:.3f})"
            elif ent_streak >= ENT_BREAK_CONSEC:
                tripped = (f"entropy<={ENT_BREAK} for {ent_streak} consecutive iters "
                           f"(now {agg['entropy']:.3f}, winRate={report['winRate']})")
            if tripped is not None:
                with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                    jsonl_f.write(json.dumps({
                        "event": "circuit_break", "iter": it,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "reason": tripped,
                        "kl": agg["kl"], "kl_streak": kl_streak,
                        "entropy": agg["entropy"], "ent_streak": ent_streak,
                        "winRate": report["winRate"], "weights": args.out,
                    }) + "\n")
                log(f"[run_rl] CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                log(f"[run_rl] training PAUSED; weights kept at {args.out}; "
                    f"inspect policy behavior before relaunching")
                break

            if agg["kl"] > KL_WARN:
                log(f"[run_rl] WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast; "
                    f"consider lower lr/epochs")
            if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                log(f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} in one "
                    f"iteration (now {agg['entropy']:.3f}) — possible premature convergence")
            prev_entropy = agg["entropy"]

            if args.keep_iters > 0:
                for old in traj_root.glob("it*"):
                    try:
                        n_old = int(old.name[2:])
                    except ValueError:
                        continue
                    if n_old <= it - args.keep_iters:
                        shutil.rmtree(old, ignore_errors=True)
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            log(f"[run_rl] it{it} FAILED (SystemExit: {e}); consecutive={consec_fail}/5")
            if consec_fail >= 5:
                raise
            time.sleep(30)
        except Exception as e:
            consec_fail += 1
            log(f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); consecutive={consec_fail}/5")
            if consec_fail >= 5:
                raise
            time.sleep(30)

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
