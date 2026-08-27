"""run_rl.py — RL on-policy 主循环入口（P1.5 蒸馏 → RL 阶段）。

工程结构（2026-08-25 工程化重组）：编排逻辑抽取至 rl/ 包 ——
  rl/course.py        课程（build_pairs / parse_range）
  rl/queue.py         中央队列调度 + 纯本地回退（run_rollout_queue / run_rollout）
  rl/stream.py        流式迭代（run_rollout_stream / wave_params）
  rl/eval_dispatch.py 干净评估分发（dispatch_eval_bg 等）
  rl/resume.py        断点对账（completed_pairs / resumed_manifests / jsonl 锚点）
  rl/reports.py       报告聚合（combine_reports / win_of）
  rl/breaker.py       F4 熔断纯逻辑（阈值 + 连击判定）
本文件只保留：CLI、迭代主循环、权重初始化/归档、巡检与熔断停车。

流程：
  ① 权重初始化（幂等）：RL 权重不存在时，从 DAgger BC 检查点 warm-start 策略头
     （价值头随机初始化）；已存在则直接续跑。
  ② 迭代 N 次：bun TS rollout（subprocess，无需 torch）→ 进程内 clipped PPO 更新
     （复用 ppo.py 的 GAE/minibatch/更新函数，模型常驻内存）→ 原子写回权重文件，
     下一轮 rollout 即用新权重（标准 on-policy）。

经统一启动器进入（venv/torch 由它保证）：
  bash nn-training/start-training.sh --script run_rl.py --iters 15 --stream 1
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl.py --iters 15          # --xxx 参数原样透传

单步调试仍可用 ppo.py 的 --init-from / --resume CLI；回归测试见 test_run_rl.py。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import torch

import dist_common
import ppo as ppo_mod
from weights_io import load_state_into, save_weights_json

# rl/ 包：编排逻辑单点实现（本文件以下仅存 CLI + 主循环 + 权重归档/巡检）
from rl.log import log
from rl.course import build_pairs, parse_range, curriculum_active_count  # noqa: F401
from rl.reports import combine_reports, win_of as _win_of  # noqa: F401
from rl.resume import (completed_pairs, last_completed_iter,  # noqa: F401
                       last_rotate_seed, resumed_manifests)
from rl.queue import (MAX_TASK_ATTEMPTS, REPO_ROOT, RUN_ID,  # noqa: F401
                      ROLLOUT_LOG_EVERY, bun_version as _bun_version,
                      mm as _mm, run_rollout, run_rollout_queue)
from rl.stream import run_rollout_stream, wave_params  # noqa: F401
from rl.eval_dispatch import (EVAL_ITER_SUFFIX, EVAL_SEEDS,  # noqa: F401
                              EVAL_TASK_ATTEMPTS, dispatch_eval_bg,
                              dispatch_eval_round, eval_done_keys as _eval_done_keys,
                              report_winrate_safe)
from rl.breaker import (CIRCUIT_EXIT_CODE, ENT_BREAK, ENT_BREAK_CONSEC,
                        ENT_BREAK_MAX_WINRATE, ENT_COLLAPSE_DROP, KL_BREAK,
                        KL_BREAK_CONSEC, KL_WARN, breaker_update)

# Per-iteration weights archive (user request 2026-08-24): every completed PPO
# write-back is copied into nn-training/weights/ with an identifiable name.
WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # bounded archive: prune oldest it-backups beyond this


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


def backup_weights(weights_path: str, it: int, prefix: str = "rl-weights") -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    Name: <prefix>.it<N>.<YYYYMMDD-HHMMSS>.json — iteration-first so the
    archive sorts by training progress at a glance; the timestamp disambiguates
    re-runs of the same iter. Deliberately NOT matching weights_io's strict
    `weights.<ts>_ep<N>_val<V>.json` BC auto-discovery regex (same reason the
    manual `rl-weights.*_post-it*ppo.json` backup avoided it): eval_bridge's
    latest_weights_path must never pick up RL archives. Oldest pruned beyond
    WEIGHTS_BACKUP_KEEP; non-fatal on any IO error.
    prefix（工程化共享）：run_rl_intent 用 'intent-rl-weights' 独立前缀，与 per-tick
    RL 归档分桶（各自按前缀 prune，互不干扰）。"""
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = WEIGHTS_BACKUP_DIR / f"{prefix}.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(weights_path, dst)
        baks = sorted(WEIGHTS_BACKUP_DIR.glob(f"{prefix}.it*.json"))
        if len(baks) > WEIGHTS_BACKUP_KEEP:
            for old in baks[:len(baks) - WEIGHTS_BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        return str(dst)
    except OSError as e:
        log(f"[run_rl] WARN weights backup failed (non-fatal): {e}")
        return None


def _log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）。

    此前失败详情只进易失 stdout——detach 启动下不可见，it2/it3 连续跳轮时
    无任何可复盘痕迹。观测必须自带牙齿：last_completed_iter 只认 iteration
    事件，iter_error 不影响断点续跑定位。
    """
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "iter_error", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(err)[:500],
            }) + "\n")
    except OSError:
        pass


def _run_inspect(bun: str, it: int) -> None:
    """每轮 PPO 写回后自动生成巡检 HTML（rl-hourly-inspect.ts）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    仅当 traj 为默认 tmp/rl-traj 时生效（巡检脚本读固定 TRAJ_DIR）。
    """
    try:
        subprocess.run(
            [bun, "tools/diag/rl-hourly-inspect.ts", "--up-to", str(it)],
            cwd=str(REPO_ROOT), timeout=180, capture_output=True, text=True)
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:  # noqa: BLE001 — 巡检失败不中断训练
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")


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
    ap.add_argument("--start-it", type=int, default=None,
                    help="resume iteration index (default: auto — last completed iteration in "
                         "training_log.jsonl + 1, so restarts continue where they stopped)")
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
    ap.add_argument("--curriculum-stages", default="",
                    help="curriculum mode: easy→hard ordered stage list (e.g. "
                         "'13,1,16,8,21,4,15,31,0,29,33,...'). Non-empty enables it: each "
                         "iteration samples only the active window (first N stages), N grows "
                         "deterministically with it (see --curriculum-every). Recommended "
                         "ordering = per-stage eval win rate desc (2026-08-25 audit).")
    ap.add_argument("--curriculum-start", type=int, default=4,
                    help="curriculum initial active-stage count")
    ap.add_argument("--curriculum-every", type=int, default=8,
                    help="curriculum: expand every N iterations (0 = never expand)")
    ap.add_argument("--curriculum-grow", type=int, default=4,
                    help="curriculum: +G stages per expansion step")
    ap.add_argument("--difficulty", default="hard")
    ap.add_argument("--max-ticks", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 12),
                    help="concurrent bun rollout workers (games partitioned by seed)")
    ap.add_argument("--local-slots", type=int, default=0,
                    help="trainer direct-thread slots (stream mode). R6 schedule: "
                         "first-dispatched during collection; suspend once PPO waves "
                         "begin (auto-resume if the whole cluster stalls); join eval "
                         "remainder after PPO. 0 = auto (max(2, workers//4))")
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
    ap.add_argument("--stream", type=int, default=0,
                    help="1 = 流式迭代：采集与 PPO 重叠（权重整轮冻结，每积压一批局就跑一轮更新）；"
                         "0 = 串行（采集全部完成后再统一 PPO）")
    ap.add_argument("--eval-games-per-stage", type=int, default=2,
                    help="干净评估：每关固定种子贪心局数（0=关闭）。rollout 收官后的 PPO 空窗期 "
                         "分发到全部 ping.evalSupport 节点；结果追加 tmp/rl-traj/eval_log.jsonl")
    ap.add_argument("--eval-window-sec", type=int, default=1500,
                    help="干净评估线程的墙钟预算；超时未结算的局放弃（不阻塞 PPO 与下一轮）")
    args = ap.parse_args()

    import numpy as np

    np.random.seed(args.seed)

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

    # 续跑继承 rotateSeed：已有 run_start 历史 → 沿用其 rotateSeed（课程连续 → it 续跑时
    # 下轮 (stage,seed) 与已落盘局一致 → 断点续跑剔除生效，不重跑已完成局）。
    # 全新开始（无 jsonl 历史，例如用户清空重建）才用当前时刻抖动种子。
    prev_rs = last_rotate_seed(jsonl_path)
    if prev_rs is not None:
        rotate_seed = prev_rs
        log(f"[run_rl] resume: inherited rotateSeed={prev_rs} (course continuity preserved)")
    else:
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2 ** 32)
    # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
    # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()},
            "rotateSeed": rotate_seed,
        }) + "\n")

    log(f"[run_rl] iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (f"curriculum={args.curriculum_stages} start={args.curriculum_start} "
           f"every={args.curriculum_every} grow={args.curriculum_grow}"
           if args.curriculum_stages else
           f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
           f"of {args.total_stages} (full coverage every "
           f"{-(-args.total_stages // args.rotate_stages)} iters)" if args.rotate_stages > 0
           else f"stages={args.stages} seeds={args.seeds}")
        + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
        f"workers={args.workers} keepIters={args.keep_iters}")
    log(f"training_log: {jsonl_path}")
    log(f"[run_rl] runId={RUN_ID}")

    # 自动巡检仅对默认 traj 生效（巡检脚本读固定 tmp/rl-traj 的 TRAJ_DIR）
    auto_inspect = traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
    if auto_inspect:
        log("[run_rl] per-iteration auto-inspection ENABLED (HTML report after each PPO)")

    deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
    total = "∞" if args.iters <= 0 else str(args.iters)
    prev_entropy = None
    consec_fail = 0
    kl_streak = 0   # F4: consecutive iters with kl >= KL_BREAK
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < MAX_WINRATE
    tripped = None
    # it 断点续跑：--start-it 显式，否则自动 = 日志最后一个完成迭代 + 1
    start_it = args.start_it if args.start_it is not None else \
        (last_completed_iter(jsonl_path) + 1)
    if start_it > 1:
        log(f"[run_rl] resume: continuing from iteration {start_it} "
            f"(weights resume from {args.out})")
    it = start_it - 1
    while args.iters <= 0 or it < args.iters:
        it += 1
        if deadline is not None and time.time() >= deadline:
            log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
            break
        traj_dir = traj_root / f"it{it}"
        try:
            # rollout/PPO 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
            # 保留续跑（跳过已完成局 + 续 PPO checkpoint）；否则清空重建。
            wver = dist_common.weights_fingerprint(args.out)
            have_resume = bool(completed_pairs(traj_dir, wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(f"[run_rl] resume iteration {it}: keeping existing shards + PPO checkpoint")
            else:
                if traj_dir.exists():
                    shutil.rmtree(traj_dir)
                traj_dir.mkdir(parents=True)

            log(f"[run_rl] === iteration {it}/{total} ===")
            pairs = build_pairs(args, it, rotate_seed)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            t_rollout = time.time()
            stream_meta = None
            dist_iter_id: str | None = None
            eval_thread: threading.Thread | None = None
            eval_gate: threading.Event | None = None  # R6：PPO 收尾后放行本地 eval 参与
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                dist_iter_id = iter_id
                enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
                log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
                # 本地 eval 参与的门控事件：派发即创建，PPO/采集收尾时 set 放行
                eval_gate = threading.Event()
                if int(getattr(args, "stream", 0) or 0):
                    def _fire_eval():
                        # 触发点在中央派发队列清空瞬间（on_queue_drained →
                        # _fire_eval_once）：全部采集任务已派到节点、结果仍在途，
                        # 评估局顺势填补收尾空槽（2026-08-25 用户修订）。
                        # 线程句柄经报告回传主循环，jsonl 写回前 join——下轮新权重
                        # 分发前评估必已收官或到预算。positional args 创建即快照，
                        # 无闭包竞态。eval_gate 随闭包捕获：PPO 收尾时 set 放行本地。
                        return dispatch_eval_bg(bun, args.out, traj_dir, args, dist_cfg,
                                                iter_id, it, local_gate=eval_gate)
                    report = run_rollout_stream(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id,
                        model, opt, device, on_collect_done=_fire_eval)
                    stream_meta = report
                else:
                    report = run_rollout_queue(bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id)
                    # 串行：rollout 返回即 collector 收官；后台评估藏进随后的长 PPO 空窗
                    if int(getattr(args, "eval_games_per_stage", 0) or 0) > 0:
                        eval_thread = dispatch_eval_bg(bun, args.out, traj_dir, args, dist_cfg,
                                                       iter_id, it, report["winRate"],
                                                       local_gate=eval_gate)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)

            kl_cum = None
            halted_flag = False
            dropped_games = None
            load_sec = None
            tail_drain_sec = None
            waves_n = None
            if stream_meta is not None:
                # 流式评估线程句柄随报告回传（R4）：jsonl 写回前 join。
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
            log(f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}")
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}")
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            if stream_meta is None:
                t_ppo = time.time()
                episodes = ppo_mod.load_episodes(str(traj_dir))
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = ppo_mod.chunk_episodes(episodes, args.mb)
                # PPO epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
                agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, device,
                                         ckpt_path=str(traj_dir / "ppo_ckpt"))
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径
            save_weights_json(model, args.out)
            bak = backup_weights(args.out, it)
            log(f"[run_rl] ppo it{it}: steps={total_steps} chunks={chunks_n} "
                + (f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                   f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}"
                   if agg is not None else
                   "metrics n/a — PPO checkpoint completed by previous process"))
            if bak:
                log(f"[run_rl] weights archived -> {bak}")

            # 下轮权重分发前等评估收官（预算封顶），耗时计入 eval_join_sec——它是
            # 轮间气泡的直接观测。流式模式评估在派发队列清空时已并行触发，此处
            # 通常只等剩余尾巴；超时未完的局放弃（下轮异 sha 清场会杀掉它们，
            # eval_log 以 dropped 记账）。join 前置于 jsonl 写回：字段同轮入账；
            # 若此间崩溃，断点续跑走「语料秒回 + PPO checkpoint 完整」路径无损重放。
            # R6 补丁：训练侧梯度步已尽（流式=末波排水完，串行=PPO 完成）——
            # 本机 idle 算力此刻入列补评估尾局。若评估已收官，set 无害。
            if eval_gate is not None:
                eval_gate.set()
            eval_join_sec = 0.0
            if eval_thread is not None and eval_thread.is_alive():
                budget = float(getattr(args, "eval_window_sec", 900)) + 60.0
                log(f"[run_rl] waiting up to {budget:.0f}s for clean-eval round "
                    f"before next weight distribution")
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(json.dumps({
                    "event": "iteration", "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "winRate": report["winRate"], "outcomes": report["outcomes"],
                    "score_mean": report.get("scoreStats", {}).get("mean"),
                    "score_std": report.get("scoreStats", {}).get("std"),
                    "dim_means": report.get("dimMeans", {}),
                    "samples": report["totalSamples"], "ticks": report["totalTicks"],
                    "rollout_sec": rollout_sec, "ppo_sec": ppo_sec,
                    "steps": total_steps, "chunks": chunks_n,
                    "policy": agg["policy"] if agg else None,
                    "value": agg["value"] if agg else None,
                    "entropy": agg["entropy"] if agg else None,
                    "kl": agg["kl"] if agg else None,
                    "mean_ret": agg["mean_ret"] if agg else None, "lr": args.lr,
                    "mb": args.mb, "epochs": args.epochs,
                    # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
                    **({"missing": report["missing"], "expectedGames": report["expectedGames"],
                        "dist": report["dist"]} if "missing" in report else {}),
                    # 纯采集（用户定义）：末局结算 − 权重分发完毕；队列模式实测透传，
                    # 纯本地路径回退为 rollout 全长（无重叠即等价纯采集）。
                    "pure_collect_sec": report.get(
                        "pure_collect_sec", round(rollout_sec, 1)),
                    # R5 遥测补牙（2026-08-25）：流式的 kl 只是末 wave 单值，对轮内
                    # 累积漂移全盲——补 kl_cum/halted/dropped 与各阶段耗时拆分。
                    # F4 熔断仍读 kl（每梯度步均值，跨模式可比）；轮内漂移由
                    # streamKlCap 治理，kl_cum 供观测与事后分析。
                    "kl_cum": kl_cum,
                    "halted": halted_flag,
                    "dropped_games": dropped_games,
                    "waves": waves_n,
                    "load_sec": load_sec,
                    "tail_drain_sec": tail_drain_sec,
                    "dist_phase_sec": report.get("dist_phase_sec"),
                    "eval_join_sec": eval_join_sec,
                }) + "\n")

            # 每轮 PPO 写回后自动生成巡检 HTML（仅默认 traj；非致命，失败不断训练）
            if auto_inspect:
                _run_inspect(bun, it)

            # F4 circuit breaker（纯逻辑在 rl/breaker.py）。agg 为 None 的轮
            # （流式 checkpoint-complete，无任何梯度步）不计连击也不告警——
            # 本来就没有发生新的策略更新。break (not raise)：下方 except 会吞掉重试。
            if agg is not None:
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak, ent_streak, kl=agg["kl"], entropy=agg["entropy"],
                    win_rate=report["winRate"])
                if tripped_now is not None:
                    tripped = tripped_now
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
                    log(f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f}) — possible premature convergence")
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
            _log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(f"[run_rl] it{it} FAILED (SystemExit: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 原地重试同一迭代（resume 保留已完成 shard + PPO ckpt，不重跑已完局）
        except Exception as e:
            consec_fail += 1
            _log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration")
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 同上：失败迭代不前跳，杜绝静默跳轮丢语料

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
