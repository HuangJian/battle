"""run_rl_intent.py — M8 意图 RL on-policy 主循环（semi-MDP，plan/Intent-Policy-NN-Plan.md §7-M8）。

**训练机制与 run_rl.py 完全一致**（除网络与超参外，逐项复用 `rl/` 共享基础设施，
禁止复制第二份——机制约定见 nn-training/README.md「RL 训练机制」）：
  - 断点续跑：training_log.jsonl 锚点（--start-it / rotateSeed 继承）+ completed_pairs
    （wver 匹配 shard 秒回）+ PPO epoch checkpoint（traj_dir/ppo_ckpt）。
  - 流式迭代（--stream 1，默认）：rollout 采集与 PPO 波次重叠（rl/stream.py
    run_rollout_stream，backend=ppo_intent）；首个 PPO 波次 → local 槽位让位
    （local_suspend）；派发队列清空 → 派发干净评估（on_collect_done），PPO 收尾后
    eval join（eval_gate 语义）。
  - F4 熔断（rl/breaker.py）+ keep-iters 轮转 + 每轮 HTML 巡检（rl-hourly-inspect.ts
    --traj-dir）+ 失败重试（iter_error 事件 + 连续 5 次退出）。
  - 权重：B′ 意图 BC warm-start（value 头随机）→ 每轮 ppo_intent 更新 → 原子写回 +
    归档（前缀 intent-rl-weights）。
  - 评估：m1-eval --policy intent-exec 固定语料贪心局（35 关 × --eval-seeds/关），
    主指标 = Δ vs --baseline（M7② 72.3%，预注册 #27 iter15 350 局）；iter15 Δ≤0 →
    止损转 M9（P2-5）。

经统一启动器进入（venv/torch 由它保证）：
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl_intent.py --iters 15 --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import torch

import ppo_intent

REPO_ROOT = Path(__file__).resolve().parents[1]

# 共享基础设施（与 run_rl.py 同源，勿复制第二份实现——README「RL 训练机制」）。
from rl.log import log  # noqa: E402
from rl.course import build_pairs  # noqa: E402
from rl.queue import (REPO_ROOT as _RQ, RUN_ID,  # noqa: E402,F401
                      run_rollout, run_rollout_queue)
from rl.resume import (completed_pairs, last_completed_iter,  # noqa: E402
                       last_rotate_seed)
from rl.stream import run_rollout_stream  # noqa: E402
from rl.breaker import (CIRCUIT_EXIT_CODE, ENT_BREAK, ENT_BREAK_CONSEC,  # noqa: E402
                        ENT_BREAK_MAX_WINRATE, ENT_COLLAPSE_DROP, KL_BREAK,
                        KL_BREAK_CONSEC, KL_WARN, breaker_update)
from run_rl import backup_weights  # noqa: E402

# M7② 基线（m1-eval 35×10 hard，intent-exec B′，nn.progress.intent §25/§26）。
DEFAULT_BASELINE = 0.723
# 止损线：iter15 主指标 Δ（相对基线）≤ 0 → 停止并转 M9。
STOP_AT_ITER = 15
# 意图 RL 干净评估默认迭代（评估旁路不拖慢采集：只在这几个迭代跑）。
DEFAULT_EVAL_AT = "5,10,15"


def _run_inspect(bun: str, traj_root: Path, it: int) -> None:
    """每轮 PPO 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。非致命。"""
    try:
        subprocess.run(
            [bun, "tools/diag/rl-hourly-inspect.ts", "--up-to", str(it),
             "--traj-dir", str(traj_root)],
            cwd=str(REPO_ROOT), timeout=180, capture_output=True, text=True)
        log(f"inspection HTML regenerated (up to it{it})")
    except Exception as e:  # noqa: BLE001 — 巡检失败不中断训练
        log(f"WARN inspection failed (non-fatal): {e}")


def _log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）——与 run_rl.py 同语义。"""
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "iter_error", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(err)[:500],
            }) + "\n")
    except OSError:
        pass


def run_clean_eval(bun: str, rl_path: str, args) -> dict:
    """m1-eval intent-exec 固定语料贪心评估（本机并发；35 关 × eval_seeds/关）。

    seeds 固定为 1..N 与 M7② 基线同语料 → 配对可比（P1-1k3 / §245 协议）。
    """
    seeds = args.eval_seeds
    cmd = [bun, "tools/sim/m1-eval.ts",
           "--stages", "all", "--seeds", f"1-{seeds}",
           "--difficulty", args.difficulty,
           "--policy", "intent-exec", "--intent-weights", rl_path,
           "--workers", str(max(2, min(8, args.workers)))]
    log(f"clean eval: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"m1-eval rc={proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
    win = None
    for line in proc.stdout.splitlines():
        m = re.search(r"winRate[=:\s]+([\d.]+)%", line)
        if m:
            win = float(m.group(1)) / 100
            break
    return {"winRate": win, "games": 35 * seeds}


def dispatch_eval_bg_intent(bun: str, rl_path: str, args, it: int,
                            jsonl_path: Path, baseline: float) -> threading.Thread:
    """干净评估后台线程（流式 on_collect_done 触发）：跑 m1-eval → 结果写回
    training_log.jsonl 的 eval 事件 + 记录 iter15 止损判定。返回线程句柄，
    主循环在 jsonl 写回前 join（与 run_rl dispatch_eval_bg 同语义）。"""
    def _body() -> None:
        try:
            er = run_clean_eval(bun, rl_path, args)
            ev = er.get("winRate")
            delta = (ev - baseline) if ev is not None else None
            rec = {
                "event": "eval_summary", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "winRate": round(ev, 4) if ev is not None else None,
                "games": er["games"], "baseline": baseline,
                "delta": round(delta, 4) if delta is not None else None,
            }
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            log(f"eval it{it}: clean winRate={ev:.1%} ({er['games']} games) "
                f"Δ vs baseline={delta:+.1%}")
        except Exception as e:  # noqa: BLE001 — 评估旁路失败不中断训练
            log(f"WARN clean eval it{it} failed (ignored): {e}")
    t = threading.Thread(target=_body, daemon=True, name=f"eval-intent-it{it}")
    t.start()
    return t


def _read_eval_summary(jsonl_path: Path, it: int) -> dict | None:
    """回读该迭代最新 eval_summary（评估线程写入）——断点/线程竞态下仍可对上。"""
    out = None
    try:
        if jsonl_path.exists():
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("event") == "eval_summary" and r.get("iter") == it:
                    out = r
    except OSError:
        pass
    return out


def main() -> None:
    os.chdir(REPO_ROOT)
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", default="tmp/intent-weights-Bp.json", help="B′ 意图 BC 权重（首轮 init）")
    ap.add_argument("--out", default="tmp/intent-rl/weights.json", help="RL 意图权重路径（每轮写回；续跑源）")
    ap.add_argument("--traj", default="tmp/intent-rl", help="意图 rollout shard 根（默认 tmp/intent-rl）")
    ap.add_argument("--iters", type=int, default=15)
    ap.add_argument("--start-it", type=int, default=None)
    ap.add_argument("--rotate-stages", type=int, default=35, help="每轮轮转覆盖的关数（35=全量）")
    ap.add_argument("--seeds-per-stage", type=int, default=4, help="每关新鲜种子数（4 → 140 局/轮）")
    ap.add_argument("--total-stages", type=int, default=35)
    ap.add_argument("--difficulty", default="hard")
    ap.add_argument("--max-ticks", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 12))
    ap.add_argument("--local-slots", type=int, default=10,
                    help="本机直接 rollout 槽位（流式下首个 PPO 波次后让位训练；0=自动 max(2,workers//4)）")
    ap.add_argument("--stream", type=int, default=1,
                    help="1=流式（rollout 与 PPO 波次重叠，推荐）；0=串行")
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
    ap.add_argument("--eval-at", default=DEFAULT_EVAL_AT, help="干净评估的迭代集合（逗号分隔）")
    ap.add_argument("--eval-seeds", type=int, default=10, help="干净评估每关种子数（350 局/轮 @10）")
    ap.add_argument("--eval-window-sec", type=int, default=1800,
                    help="干净评估线程 join 预算；超时未结算放弃（不阻塞下一轮）")
    ap.add_argument("--baseline", type=float, default=DEFAULT_BASELINE,
                    help="M7② 基线胜率（干净评估 Δ 的参照）")
    args = ap.parse_args()
    # 意图 rollout 语义透传给 rl.queue / rl.stream（intent_rollout 分支 + kind/replan）。
    args.intent_rollout = True

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

    # 续跑继承 rotateSeed（与 run_rl.py 同语义）：build_pairs 是 (rotateSeed, it) 纯函数。
    prev_rs = last_rotate_seed(jsonl_path)
    if prev_rs is not None:
        rotate_seed = prev_rs
        log(f"resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
    else:
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)

    start_it = args.start_it if args.start_it is not None else last_completed_iter(jsonl_path) + 1
    eval_at = {int(x) for x in str(args.eval_at).split(",") if x.strip()}
    log(f"iters={args.iters} start={start_it} stream={args.stream} "
        f"rotate={args.rotate_stages}×{args.seeds_per_stage}/轮 difficulty={args.difficulty} "
        f"replan={args.replan} local_slots={args.local_slots} "
        f"eval_at={sorted(eval_at)} baseline={args.baseline:.1%}")
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()}, "rotateSeed": rotate_seed,
        }) + "\n")

    # 自动巡检仅对默认 traj 生效（巡检脚本读 --traj-dir 指定目录）。
    auto_inspect = True

    prev_entropy = None
    consec_fail = 0
    kl_streak = 0
    ent_streak = 0
    tripped = None
    it = start_it - 1
    stop_reason = None
    while args.iters <= 0 or it < start_it - 1 + args.iters:
        it += 1
        traj_dir = traj_root / f"it{it}"
        try:
            # 断点感知：该迭代已有 wver 匹配的完整 shard → 保留续跑；否则清空重建。
            wver = _weights_fingerprint(args.out)
            have_resume = bool(completed_pairs(traj_dir, wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(f"resume iteration {it}: keeping existing shards + PPO checkpoint")
            else:
                if traj_dir.exists():
                    shutil.rmtree(traj_dir)
                traj_dir.mkdir(parents=True)

            log(f"=== iteration {it}/{start_it - 1 + args.iters} ===")
            pairs = build_pairs(args, it, rotate_seed)
            dist_cfg = _load_dist_config()
            t_rollout = time.time()
            stream_meta = None
            eval_thread: threading.Thread | None = None
            eval_gate = threading.Event()
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                if args.stream:
                    def _fire_eval():
                        return dispatch_eval_bg_intent(bun, args.out, args, it,
                                                       jsonl_path, args.baseline)
                    report = run_rollout_stream(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id,
                        model, opt, device, on_collect_done=_fire_eval,
                        backend=ppo_intent, update_kwargs=_update_kwargs(args, it, start_it,
                                                                         ref_model))
                    stream_meta = report
                else:
                    report = run_rollout_queue(bun, args.out, traj_dir, pairs, args,
                                               dist_cfg, iter_id,
                                               local_slots_max=args.local_slots)
                    if it in eval_at:
                        eval_thread = dispatch_eval_bg_intent(bun, args.out, args, it,
                                                              jsonl_path, args.baseline)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)

            kl_cum = None
            halted_flag = False
            dropped_games = None
            load_sec = None
            tail_drain_sec = None
            waves_n = None
            if stream_meta is not None:
                eval_thread = report.pop("_eval_thread", None)
                _sm = report.pop("_stream")
                rollout_sec = _sm["rollout_sec"]
                ppo_sec = _sm["ppo_sec"]
                total_steps = _sm["steps"]
                chunks_n = _sm["chunks"]
                agg = _sm["agg"]
                tail_drain_sec = _sm.get("tail_drain_sec")
                kl_cum = _sm.get("kl_cum")
                halted_flag = bool(_sm.get("halted", False))
                dropped_games = _sm.get("dropped_games")
                load_sec = _sm.get("load_sec")
                waves_n = _sm.get("waves")
            else:
                rollout_sec = round(time.time() - t_rollout, 1)
            log(f"rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} samples={report['totalSamples']} "
                f"ticks={report['totalTicks']} kills={report['totalKills']}")

            if stream_meta is None:
                t_ppo = time.time()
                episodes = ppo_intent.load_episodes_intent(str(traj_dir))
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = ppo_intent.chunk_episodes(episodes, args.mb)
                agg = ppo_intent.ppo_update_intent(
                    model, opt, chunks, args.epochs, device, seed=args.seed,
                    ckpt_path=str(traj_dir / "ppo_ckpt"),
                    **_update_kwargs(args, it, start_it, ref_model))
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None

            ppo_intent.export_intent_weights(model, args.out)
            bak = backup_weights(args.out, it, prefix="intent-rl-weights")
            log(f"ppo it{it}: steps={total_steps} chunks={chunks_n} "
                f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f}"
                + (f" early_stopped={agg['early_stopped']}" if agg and agg.get('early_stopped')
                   else ""))
            if bak:
                log(f"weights archived -> {bak}")

            # 每轮 PPO 写回后自动生成巡检 HTML。
            if auto_inspect:
                _run_inspect(bun, traj_root, it)

            # 评估线程收尾（R6 eval_gate 语义）：PPO 已收尾 → 放行本机评估参与 → join。
            eval_gate.set()
            eval_join_sec = 0.0
            if eval_thread is not None and eval_thread.is_alive():
                budget = float(args.eval_window_sec) + 60.0
                log(f"waiting up to {budget:.0f}s for clean-eval round before next "
                    f"weight distribution")
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)
            eval_rec = _read_eval_summary(jsonl_path, it)

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
                    "steps": total_steps, "chunks": chunks_n,
                    "policy": agg["policy"] if agg else None,
                    "value": agg["value"] if agg else None,
                    "entropy": agg["entropy"] if agg else None,
                    "kl": agg["kl"] if agg else None,
                    "early_stopped": agg.get("early_stopped", False) if agg else False,
                    "kl_cum": kl_cum, "halted": halted_flag,
                    "dropped_games": dropped_games, "waves": waves_n,
                    "load_sec": load_sec, "tail_drain_sec": tail_drain_sec,
                    "eval_join_sec": eval_join_sec,
                    "eval": eval_rec,
                    "lr": args.lr, "mb": args.mb, "epochs": args.epochs,
                }
                f.write(json.dumps(rec) + "\n")

            # F4 熔断（纯逻辑 rl/breaker.py，与 run_rl.py 同阈值语义）。
            if agg is not None:
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak, ent_streak, kl=agg["kl"], entropy=agg["entropy"],
                    win_rate=report["winRate"])
                if tripped_now is not None:
                    tripped = tripped_now
                if tripped is not None:
                    with open(jsonl_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "event": "circuit_break", "iter": it,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "reason": tripped,
                            "kl": agg["kl"], "kl_streak": kl_streak,
                            "entropy": agg["entropy"], "ent_streak": ent_streak,
                            "winRate": report["winRate"], "weights": args.out,
                        }) + "\n")
                    log(f"CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                    break
                if agg["kl"] > KL_WARN:
                    log(f"WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast")
                if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                    log(f"WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f})")
                prev_entropy = agg["entropy"]

            # iter15 止损：eval_summary 的 Δ ≤ 0 → 转 M9（P2-5，不续命）。
            if it >= STOP_AT_ITER and eval_rec and eval_rec.get("delta") is not None \
                    and eval_rec["delta"] <= 0:
                stop_reason = (f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                               f"<= 0 — stop-loss to M9")
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
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(f"it{it} FAILED (SystemExit: {e}); consecutive={consec_fail}/5 — retry")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1
        except Exception as e:  # noqa: BLE001 — 失败原地重试（不静默跳轮丢语料）
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(f"it{it} FAILED ({type(e).__name__}: {e}); consecutive={consec_fail}/5 — retry")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl_intent] ALL DONE -> {args.out}")


def _weights_fingerprint(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_dist_config():
    import dist_common
    return dist_common.load_dist_config()


def _update_kwargs(args, it: int, start_it: int, ref_model):
    """ppo_intent 更新参数：value 预热（前 warmup-iters）+ kickstarting KL（系数衰减）。"""
    warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
    policy_iter = (it - start_it) - args.warmup_iters + 1
    kl_coef = args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1)) \
        if args.kickstart_kl > 0 and policy_iter >= 1 else 0.0
    return {"value_warmup_epochs": warmup_epochs, "ref_model": ref_model, "kl_coef": kl_coef,
            "seed": args.seed}


if __name__ == "__main__":
    main()
