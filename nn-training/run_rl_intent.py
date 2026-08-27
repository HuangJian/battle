"""run_rl_intent.py — M8 意图 RL on-policy 主循环（semi-MDP，plan/Intent-Policy-NN-Plan.md §7-M8）。

与 run_rl.py（per-tick move/fire RL）相对，本入口跑意图 RL：
  - 权重初始化（幂等）：RL 权重不存在时从 B′ 意图 BC 权重 warm-start 主干+三头
    （value 头随机初始化，ppo_intent.py init 模式）；已存在则直接续跑。
  - 迭代 N 次：bun TS 意图 rollout（export-intent-rollout.ts，意图步 shard）→ 进程内
    clipped PPO（ppo_intent.py：意图步变步长 GAE + 意图头 + value 头）→ 原子写回权重。
  - 干净评估（--eval-at 指定迭代）：m1-eval --policy intent-exec 固定语料贪心局
    （35 关 × --eval-seeds/关），主指标 = Δ vs --baseline（M7② 72.3%，预注册 #27 用
    iter15 350 局重标）。iter15 Δ≤0 → 止损转 M9（P2-5，不续命）。
  - pace checkpoint：iter5 首现通关（rollout winRate>0）；iter15 Δ>0 且基地失守占比下行。

经统一启动器进入（venv/torch 由它保证）：
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl_intent.py --iters 15 --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import torch

import ppo_intent

REPO_ROOT = Path(__file__).resolve().parents[1]

WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20

# M7② 基线（m1-eval 35×10 hard，intent-exec B′，nn.progress.intent §25/§26）。
DEFAULT_BASELINE = 0.723
# 止损线：iter15 主指标 Δ（相对基线）≤ 0 → 停止并转 M9。
STOP_AT_ITER = 15


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl_intent] {msg}", flush=True)


def build_pairs(args, it: int, rotate_seed: int) -> list[tuple[int, int]]:
    """每轮覆盖 rotate_stages 个关（默认 35=全量），每关 sps 个新鲜种子（确定性）。
    部分覆盖（rotate_stages < total_stages）时按迭代轮转起始关，保证跨轮最终全覆盖。"""
    import random
    rng = random.Random(f"intent:{rotate_seed}:{it}")
    total = args.total_stages if args.total_stages > 0 else 35
    n_stages = args.rotate_stages if args.rotate_stages > 0 else total
    n_stages = min(n_stages, total)
    start = (it * n_stages) % total if n_stages < total else 0
    pairs: list[tuple[int, int]] = []
    for s in range(start, start + n_stages):
        si = s % total
        for _ in range(args.seeds_per_stage):
            seed = rng.randint(1, 2 ** 31)
            pairs.append((si, seed))
    return pairs


def run_rollout(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]], args) -> dict:
    """本地意图 rollout：export-intent-rollout.ts 每局一个 bun 子进程（单线程，全核并行）。"""
    workers = max(1, min(args.workers, len(pairs)))

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        with open(wdir / "rollout.log", "w", encoding="utf-8") as log_f:
            cmd = [
                bun, "tools/sim/export-intent-rollout.ts",
                "--weights", rl_path, "--out", str(wdir),
                "--stages", str(si), "--seeds", str(seed),
                "--max-ticks", str(args.max_ticks), "--difficulty", args.difficulty,
                "--replan", str(args.replan),
            ]
            p = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
            rc = p.wait()
        if rc != 0:
            return rc, None
        report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    t0 = time.time()
    results: list[tuple[int, dict | None]] = [(1, None)] * len(pairs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, i, si, sd): i for i, (si, sd) in enumerate(pairs)}
        done_n = 0
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done_n += 1
            if done_n % 10 == 0 or done_n == len(pairs):
                log(f"rollout local {done_n}/{len(pairs)} games settled ({time.time() - t0:.0f}s)")
    failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
    if failed:
        tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
        raise SystemExit(f"intent rollout worker(s) {failed} failed:\n{tail}")
    reports = [r for _rc, r in results if r is not None]
    if not reports:
        return {"games": len(pairs), "winRate": 0.0, "outcomes": {}, "totalSamples": 0,
                "totalTicks": 0, "intentCounts": [0] * 8, "totalKills": 0}
    games = sum(r["games"] for r in reports)
    wins = sum(int(round(r["winRate"] * r["games"])) for r in reports)
    outcomes: dict[str, int] = {}
    totalSamples = totalTicks = totalKills = 0
    intentCounts = [0] * 8
    for r in reports:
        for k, v in r.get("outcomes", {}).items():
            outcomes[k] = outcomes.get(k, 0) + v
        totalSamples += r["totalSamples"]
        totalTicks += r["totalTicks"]
        totalKills += r["totalKills"]
        ic = r.get("intentCounts", [])
        for i in range(min(8, len(ic))):
            intentCounts[i] += ic[i]
    return {"games": games, "winRate": round(wins / games, 4), "outcomes": outcomes,
            "totalSamples": totalSamples, "totalTicks": totalTicks,
            "intentCounts": intentCounts, "totalKills": totalKills}


def run_clean_eval(bun: str, rl_path: str, args) -> dict:
    """m1-eval intent-exec 固定语料贪心评估（本机并发；35 关 × eval_seeds/关）。"""
    seeds = args.eval_seeds
    cmd = [bun, "tools/sim/m1-eval.ts",
           "--stages", "all", "--seeds", f"1-{seeds}",
           "--difficulty", args.difficulty,
           "--policy", "intent-exec", "--intent-weights", rl_path,
           "--workers", str(max(2, args.workers))]
    log(f"clean eval: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"m1-eval rc={proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
    out = proc.stdout
    # m1-eval 摘要行：winRate X% (...)
    win = None
    for line in out.splitlines():
        if "winRate" in line:
            import re
            m = re.search(r"winRate[=:\s]+([\d.]+)%", line)
            if m:
                win = float(m.group(1)) / 100
            break
    return {"winRate": win, "games": 35 * seeds}


def last_completed_iter(jsonl_path: Path) -> int:
    it = 0
    try:
        if jsonl_path.exists():
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("event") == "iteration":
                    it = max(it, int(r.get("iter", 0)))
    except OSError:
        pass
    return it


def backup_weights(weights_path: str, it: int) -> str | None:
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = WEIGHTS_BACKUP_DIR / f"intent-rl-weights.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(weights_path, dst)
        baks = sorted(WEIGHTS_BACKUP_DIR.glob("intent-rl-weights.it*.json"))
        if len(baks) > WEIGHTS_BACKUP_KEEP:
            for old in baks[:len(baks) - WEIGHTS_BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        return str(dst)
    except OSError as e:
        log(f"WARN weights backup failed (non-fatal): {e}")
        return None


def main() -> None:
    os.chdir(REPO_ROOT)
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default="tmp/intent-weights-Bp.json", help="B′ 意图 BC 权重（首轮 init）")
    ap.add_argument("--out", default="tmp/intent-rl/weights.json", help="RL 意图权重路径（每轮写回；续跑源）")
    ap.add_argument("--traj", default="tmp/intent-rl-traj", help="意图 rollout shard 根")
    ap.add_argument("--iters", type=int, default=15)
    ap.add_argument("--start-it", type=int, default=None)
    ap.add_argument("--rotate-stages", type=int, default=35, help="每轮轮转覆盖的关数（35=全量）")
    ap.add_argument("--seeds-per-stage", type=int, default=4, help="每关新鲜种子数（4 → 140 局/轮）")
    ap.add_argument("--total-stages", type=int, default=35)
    ap.add_argument("--difficulty", default="hard")
    ap.add_argument("--max-ticks", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 12))
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--lr", type=float, default=ppo_intent.LR)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--replan", type=int, default=30, help="意图 replan cadence（M7① 定稿 30）")
    ap.add_argument("--warmup-iters", type=int, default=1,
                    help="前 N 迭代只训 value 头（B′ 冷启动 value 随机 → 先学回报基线再动策略）")
    ap.add_argument("--kickstart-kl", type=float, default=1.0,
                    help="kickstarting KL 惩罚基础系数（plan #5；0=关闭）")
    ap.add_argument("--kickstart-decay", type=float, default=0.5,
                    help="kickstarting 系数每策略迭代衰减因子（预注册 #5：0.5/iter）")
    ap.add_argument("--keep-iters", type=int, default=3)
    ap.add_argument("--eval-at", default="5,10,15", help="干净评估的迭代集合（逗号分隔）")
    ap.add_argument("--eval-seeds", type=int, default=10, help="干净评估每关种子数（350 局/轮 @10）")
    ap.add_argument("--baseline", type=float, default=DEFAULT_BASELINE,
                    help="M7② 基线胜率（干净评估 Δ 的参照）")
    args = ap.parse_args()

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl_intent] bun not found on PATH — rollout needs it")

    # 权重初始化（幂等）：RL 权重不存在时从 B′ warm-start（value 头随机）。
    if not os.path.exists(args.out):
        log(f"init RL weights from B′ ({args.bc}) -> {args.out}")
        subprocess.run([sys.executable, "nn-training/ppo_intent.py",
                        "--init-from", args.bc, "--out", args.out,
                        "--threads", str(max(1, min(8, args.workers)))],
                       cwd=str(REPO_ROOT), check=True)

    device = torch.device("cpu")
    model = ppo_intent.build_rl_net(args.out)
    ppo_intent.load_intent_weights(model, args.out)
    model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    log(f"model params={sum(int(p.numel()) for p in model.parameters())}")

    # kickstarting 参考策略：B′ 策略冻结快照（warmup 冻结主干+三头 → 策略与 B′ 一致）。
    # 与 model 同 arch，load 同一份权重后全量冻结——PPO 用它把策略钉在 B′ 附近，系数衰减。
    ref_model = None
    if args.kickstart_kl > 0:
        ref_model = ppo_intent.build_rl_net(args.out)
        ppo_intent.load_intent_weights(ref_model, args.out)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = traj_root / "training_log.jsonl"
    rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)

    start_it = args.start_it if args.start_it is not None else last_completed_iter(jsonl_path) + 1
    eval_at = {int(x) for x in str(args.eval_at).split(",") if x.strip()}
    log(f"iters={args.iters} start={start_it} rotate={args.rotate_stages}×{args.seeds_per_stage}"
        f"/轮 difficulty={args.difficulty} replan={args.replan} eval_at={sorted(eval_at)}"
        f" baseline={args.baseline:.1%}")
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()}, "rotateSeed": rotate_seed,
        }) + "\n")

    it = start_it - 1
    stop_reason = None
    while args.iters <= 0 or it < start_it - 1 + args.iters:
        it += 1
        traj_dir = traj_root / f"it{it}"
        if traj_dir.exists():
            shutil.rmtree(traj_dir)
        traj_dir.mkdir(parents=True)
        log(f"=== iteration {it}/{start_it - 1 + args.iters} ===")
        pairs = build_pairs(args, it, rotate_seed)

        t_roll = time.time()
        report = run_rollout(bun, args.out, traj_dir, pairs, args)
        rollout_sec = round(time.time() - t_roll, 1)
        log(f"rollout it{it}: games={report['games']} winRate={report['winRate']} "
            f"outcomes={json.dumps(report['outcomes'])} samples={report['totalSamples']} "
            f"ticks={report['totalTicks']} kills={report['totalKills']}")

        t_ppo = time.time()
        episodes = ppo_intent.load_episodes_intent(str(traj_dir))
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = ppo_intent.chunk_episodes(episodes, args.mb)
        # B′ 冷启动：前 warmup-iters 迭代只训 value 头（策略冻结防优势噪声塌缩）。
        warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
        # kickstarting：kl_coef = base · decay^(策略迭代序)（warmup 后从 0 计数，辅助项递减）。
        policy_iter = (it - start_it) - args.warmup_iters + 1
        kl_coef = args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1)) \
            if args.kickstart_kl > 0 and policy_iter >= 1 else 0.0
        agg = ppo_intent.ppo_update_intent(model, opt, chunks, args.epochs, device, seed=args.seed,
                                           value_warmup_epochs=warmup_epochs,
                                           ref_model=ref_model, kl_coef=kl_coef)
        ppo_sec = round(time.time() - t_ppo, 1)
        ppo_intent.export_intent_weights(model, args.out)
        bak = backup_weights(args.out, it)
        log(f"ppo it{it}: steps={total_steps} chunks={len(chunks)} "
            f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
            f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f}"
            + (f" early_stopped={agg['early_stopped']}" if agg.get('early_stopped') else ""))

        eval_rec = None
        if it in eval_at:
            t_ev = time.time()
            try:
                er = run_clean_eval(bun, args.out, args)
                ev = er.get("winRate")
                delta = (ev - args.baseline) if ev is not None else None
                eval_rec = {
                    "winRate": round(ev, 4) if ev is not None else None,
                    "games": er["games"], "delta": round(delta, 4) if delta is not None else None,
                    "baseline": args.baseline, "sec": round(time.time() - t_ev, 1),
                }
                log(f"eval it{it}: clean winRate={ev:.1%} ({er['games']} games) "
                    f"Δ vs baseline={delta:+.1%}")
                # iter15 止损：主指标 Δ ≤ 0 → 转 M9（P2-5，不续命）。
                if it >= STOP_AT_ITER and delta is not None and delta <= 0:
                    stop_reason = f"iter{it} clean-eval Δ={delta:+.4f} <= 0 — stop-loss to M9"
            except Exception as e:  # noqa: BLE001 — 评估旁路失败不中断训练
                log(f"WARN clean eval it{it} failed (ignored): {e}")
                eval_rec = {"error": str(e)[:200]}

        # pace checkpoint：iter5 首现通关。
        if it == 5 and report["winRate"] <= 0:
            log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")

        with open(jsonl_path, "a", encoding="utf-8") as f:
            rec = {
                "event": "iteration", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "winRate": report["winRate"], "outcomes": report["outcomes"],
                "samples": report["totalSamples"], "ticks": report["totalTicks"],
                "kills": report["totalKills"], "intentCounts": report["intentCounts"],
                "rollout_sec": rollout_sec, "ppo_sec": ppo_sec,
                "steps": total_steps, "chunks": len(chunks),
                "policy": agg["policy"], "value": agg["value"],
                "entropy": agg["entropy"], "kl": agg["kl"],
                "early_stopped": agg.get("early_stopped", False),
                "lr": args.lr, "mb": args.mb, "epochs": args.epochs,
                "eval": eval_rec,
            }
            f.write(json.dumps(rec) + "\n")

        if stop_reason:
            log(f"STOP-LOSS: {stop_reason}")
            break
        if args.keep_iters > 0:
            for old in traj_root.glob("it*"):
                try:
                    n_old = int(old.name[2:])
                except ValueError:
                    continue
                if n_old <= it - args.keep_iters:
                    shutil.rmtree(old, ignore_errors=True)

    print(f"[{time.strftime('%H:%M:%S')}] [run_rl_intent] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
