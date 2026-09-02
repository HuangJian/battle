"""run_training —— RL 迭代主循环（2026-09-02 从 run_rl.py main() 拆出）。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.archive import backup_weights
from rl.breaker import (
    CIRCUIT_EXIT_CODE,
    ENT_COLLAPSE_DROP,
    KL_BREAK,
    KL_BREAK_CONSEC,
    KL_WARN,
    breaker_update,
)
from rl.collect_only import precollect_snapshot_wver, spawn_collect_next
from rl.course import build_pairs
from rl.eval_dispatch import dispatch_eval_bg
from rl.eval_m1 import dispatch_eval_bg_m1, read_eval_summary
from rl.log import log
from rl.modes import _MODE_BACKUP_PREFIX, get_backend
from rl.queue import REPO_ROOT, RUN_ID, run_rollout, run_rollout_queue
from rl.resume import (
    completed_pairs,
    last_completed_iter,
    last_rotate_seed,
)
from rl.stop_loss import eval_sigma, stop_loss_hit
from rl.stream import run_rollout_stream


def log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）。

    此前失败详情只进易失 stdout——detach 启动下不可见，it2/it3 连续跳轮时
    无任何可复盘痕迹。观测必须自带牙齿：last_completed_iter 只认 iteration
    事件，iter_error 不影响断点续跑定位。
    """
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "iter_error",
                        "iter": it,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "error": str(err)[:500],
                    }
                )
                + "\n"
            )
    except OSError:
        pass





def run_inspect(bun: str, it: int, traj_dir: Path) -> None:
    """每轮 ppo_backend 写回后自动生成巡检 HTML（rl-hourly-inspect.ts --traj-dir）。

    非致命：巡检失败仅记录 warning，绝不中断训练主线（AGENTS §14 / 训练可用性优先）。
    显式传 --traj-dir（intent/goal 的非默认 traj 也要能出巡检 HTML）。"""
    try:
        subprocess.run(
            [
                bun,
                "tools/diag/rl-hourly-inspect.ts",
                "--up-to",
                str(it),
                "--traj-dir",
                str(traj_dir),
            ],
            cwd=str(REPO_ROOT),
            timeout=180,
            capture_output=True,
            text=True,
            **_POPEN_NO_WINDOW,
        )
        log(f"[run_rl] inspection HTML regenerated (up to it{it})")
    except Exception as e:
        log(f"[run_rl] WARN inspection failed (non-fatal): {e}")




def run_training(args, ppo_backend, bun, update_kwargs) -> None:

    # ===== 训练路径延迟导入（B7，2026-09-02）：collect-only 子进程已提前 return，
    # 此刻起才允许拉起 torch / ppo.* / models.*（CPU 上 ~3-8s 的 torch 加载不再
    # 出现在每轮的双缓冲预采子进程里）。=====
    import numpy as np
    import torch

    import ppo.engine as ppo_mod
    import ppo.goal as ppo_goal
    import ppo.intent as ppo_intent
    from data.weights_io import save_weights_json
    from models.goal_net import GoalNet
    from models.intent_net import IntentNet
    from rl.model_build import build_model

    np.random.seed(args.seed)
    ppo_backend = get_backend(args.mode)

    device = torch.device("cpu")
    model = build_model(args.bc, args.out, mode=args.mode, workers=args.workers)
    model.to(device)
    ref_model = None
    if args.mode in ("intent", "goal") and args.kickstart_kl > 0:
        # kickstarting 参考策略：B′ 冻结快照（须在 build_model 完成 init-from 落盘
        # args.out 之后构建）。warmup 冻结主干+三头 → 策略与 B′ 一致。
        ref_model = ppo_backend.build_rl_net(args.out)
        if args.mode == "goal":
            ppo_goal.load_goal_weights(ref_model, args.out)
        else:
            ppo_intent.load_intent_weights(ref_model, args.out)
        for p in ref_model.parameters():
            p.requires_grad = False
        ref_model.eval()
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
        rotate_seed = (args.seed * 1009 + 1 + int(time.time())) % (2**32)
    # build_pairs 是 (rotateSeed, it) 的纯函数：不持有任何跨迭代的随机流状态，
    # 同一 it 在任意时刻重启都得到完全相同的一批局（断点续跑剔除的前提）。

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(
            json.dumps(
                {
                    "event": "run_start",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "args": {k: v for k, v in vars(args).items()},
                    "rotateSeed": rotate_seed,
                }
            )
            + "\n"
        )

    log(
        f"[run_rl] mode={args.mode} "
        f"iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (
            f"curriculum={args.curriculum_stages} start={args.curriculum_start} "
            f"every={args.curriculum_every} grow={args.curriculum_grow}"
            if args.curriculum_stages
            else f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
            f"of {args.total_stages} (full coverage every "
            f"{-(-args.total_stages // args.rotate_stages)} iters)"
            if args.rotate_stages > 0
            else f"stages={args.stages} seeds={args.seeds}"
        )
        + f" maxTicks={args.max_ticks} epochs={args.epochs} mb={args.mb} lr={args.lr} "
        f"workers={args.workers} keepIters={args.keep_iters}"
    )
    log(f"training_log: {jsonl_path}")
    log(f"[run_rl] runId={RUN_ID}")

    # 自动巡检：per-tick 仅对默认 traj 生效（巡检脚本默认读 tmp/rl-traj）；
    # intent/goal 总是生成巡检 HTML（_run_inspect 显式传 --traj-dir）。
    auto_inspect = (
        True
        if args.mode in ("intent", "goal")
        else traj_root.resolve() == (REPO_ROOT / "tmp" / "rl-traj").resolve()
    )
    if auto_inspect:
        log("[run_rl] per-iteration auto-inspection ENABLED (HTML report after each ppo_backend)")

    deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
    total = "∞" if args.iters <= 0 else str(args.iters)
    prev_entropy = None
    consec_fail = 0
    kl_streak = 0  # F4: consecutive iters with kl >= KL_BREAK
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < MAX_WINRATE
    stop_loss_streak = 0  # P1-9: 统计显著止损（Δ≤−2σ）的连续轮数，≥2 才停车
    tripped = None
    # it 断点续跑：--start-it 显式，否则自动 = 日志最后一个完成迭代 + 1
    start_it = args.start_it if args.start_it is not None else (last_completed_iter(jsonl_path) + 1)
    if start_it > 1:
        log(
            f"[run_rl] resume: continuing from iteration {start_it} "
            f"(weights resume from {args.out})"
        )
    it = start_it - 1
    # 吞吐 T3：eval 稀疏化周期（默认 1 = 每轮，字节一致；>1 = 每 N 轮一次）。
    eval_every = int(getattr(args, "eval_every", 1) or 1)
    # 吞吐 T3：eval 绝对迭代点集（复用 run_rl_intent 的 eval_at 语义；空 = 不启用该维）。
    eval_at_set = {int(x) for x in str(getattr(args, "eval_at", "") or "").split(",") if x.strip()}
    _collect_child: subprocess.Popen | None = None  # 吞吐 T4：预采子进程句柄（下一轮开头 join）
    _spawned_early = False  # 吞吐 T4：本轮是否已在 ppo_backend 中途（epoch 提前量处）spawn 过预采
    while args.iters <= 0 or it < args.iters:
        it += 1
        # 吞吐 T4：本轮开头检查预采子进程产出——轮询 completed_pairs 直到足够开首波
        # （而非 wait 子进程退出），避免预采尾段拖慢、主进程空等。
        # 子进程在后台继续产出剩余 shard，run_rollout_queue 的 completed_pairs 会跳过已落盘局。
        if _collect_child is not None:
            _pre_traj_dir = traj_root / f"it{it}"
            # wver/extra_wver 需在子进程退出前算出才能匹配预采 shard
            _pre_wver = dist_common.weights_fingerprint(args.out)
            _pre_extra_wver = precollect_snapshot_wver(args.out, it)
            _pre_cfg = dist_common.load_dist_config() or {}
            _pre_policy = _pre_cfg.get("policy", {})
            _pre_wave = max(4, int(_pre_policy.get("streamWaveGames", 12)))
            _pre_min_wave = max(4, _pre_wave // 2)  # 半波即可开训
            _pre_deadline = time.time() + 3600
            _pre_ready = False
            while time.time() < _pre_deadline:
                _pre_done = completed_pairs(_pre_traj_dir, _pre_wver, extra_wver=_pre_extra_wver)
                if len(_pre_done) >= _pre_min_wave:
                    log(
                        f"[double-buffer] precollect it{it}: {len(_pre_done)} shards ready "
                        f"(≥{_pre_min_wave}), proceeding before subprocess exit"
                    )
                    _pre_ready = True
                    break
                if _collect_child.poll() is not None:
                    log(f"[double-buffer] precollect it{it} done rc={_collect_child.returncode}")
                    _pre_ready = True
                    break
                time.sleep(2)
            if not _pre_ready:
                log(f"[double-buffer] precollect it{it} timeout — fall back to own collect")
                _collect_child.terminate()
            _collect_child = None
        if deadline is not None and time.time() >= deadline:
            log(f"[run_rl] max-hours={args.max_hours} reached — stopping before it{it}")
            break
        traj_dir = traj_root / f"it{it}"
        try:
            # rollout/ppo_backend 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
            # 保留续跑（跳过已完成局 + 续 ppo_backend checkpoint）；否则清空重建。
            wver = dist_common.weights_fingerprint(args.out)
            # 吞吐 T4 提前预采：上一轮若在 epoch3 已 spawn，本轮对账还需接受快照 wver
            # （θ_{N,e3} ≈ θ_N 于最后 1 个 epoch 前）——否则预采首波被当"未完成"清场。
            extra_wver = precollect_snapshot_wver(args.out, it)
            have_resume = bool(completed_pairs(traj_dir, wver, extra_wver=extra_wver))
            if have_resume:
                traj_dir.mkdir(parents=True, exist_ok=True)
                log(
                    f"[run_rl] resume iteration {it}: keeping existing shards + ppo_backend checkpoint"
                    + (f" (precollect snapshot wver {extra_wver[:12]}…)" if extra_wver else "")
                )
            else:
                if traj_dir.exists():
                    try:
                        shutil.rmtree(traj_dir)  # 沙箱删除保护拦截时跳过（保留旧目录，训练照常）
                    except BaseException:
                        pass
                traj_dir.mkdir(parents=True)

            log(f"[run_rl] === iteration {it}/{total} ===")
            pairs = build_pairs(args, it, rotate_seed)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            # 吞吐 T3：本轮是否派发干净评估。per-tick 按 eval-games/eval-every/eval-at
            # 三条件；intent/goal 按 eval_at（默认 '5,10,15'）——别的模式不派发不 join。
            if args.mode == "per-tick":
                eval_on_round = (
                    int(getattr(args, "eval_games_per_stage", 0) or 0) > 0
                    and (eval_every <= 1 or it % eval_every == 0)
                    and (not eval_at_set or it in eval_at_set)
                )
            else:
                eval_on_round = it in eval_at_set
            t_rollout = time.time()
            stream_meta = None
            eval_thread: threading.Thread | None = None
            eval_gate: threading.Event | None = None  # R6：ppo_backend 收尾后放行本地 eval 参与
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
                log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
                # 本地 eval 参与的门控事件：派发即创建，ppo_backend/采集收尾时 set 放行
                eval_gate = threading.Event()
                if int(getattr(args, "stream", 0) or 0):

                    def _fire_eval():
                        # 触发点在中央派发队列清空瞬间（on_queue_drained →
                        # _fire_eval_once）：全部采集任务已派到节点、结果仍在途，
                        # 评估局顺势填补收尾空槽（2026-08-25 用户修订）。
                        # 线程句柄经报告回传主循环，jsonl 写回前 join——下轮新权重
                        # 分发前评估必已收官或到预算。positional args 创建即快照，
                        # 无闭包竞态。eval_gate 随闭包捕获：ppo_backend 收尾时 set 放行本地。
                        # 吞吐 T3：非 eval 轮不派发（返回 None → 无 eval_thread →
                        # 下方 join 跳过，集群尾段留给下一轮采集/双缓冲）。
                        if not eval_on_round:
                            return None
                        if args.mode == "per-tick":
                            return dispatch_eval_bg(
                                bun,
                                args.out,
                                traj_dir,
                                args,
                                dist_cfg,
                                iter_id,
                                it,
                                local_gate=eval_gate,
                            )
                        # intent/goal：m1-eval 固定语料整批路线（rl/eval_m1.py）。
                        return dispatch_eval_bg_m1(
                            bun, args.out, args, it, jsonl_path, args.baseline
                        )

                    # 吞吐 T4 提前预采（--precollect-early>0）：ppo_backend 第 (epochs-early) 个
                    # epoch 完成后，把当前 model（θ_{N,e3}）冻结为快照并 spawn 预采下一轮
                    # 首波——预采墙钟藏进剩下的 epochs 里，而非 ppo_backend 全部结束后的串行等待。
                    # 快照≈最终权重（差最后一段梯度），on-policy 带内；配合
                    # --precollect-games 限制只采首波，剩余局由下轮以 θ_N 现场采。
                    _early = int(getattr(args, "precollect_early", 0) or 0)
                    _spawned_early = False

                    def _on_epoch_done(ep_done: int, _mdl):
                        nonlocal _collect_child  # 提前 spawn 的句柄存入主循环变量（下轮开头 join）
                        nonlocal _spawned_early
                        if _early <= 0 or _spawned_early:
                            return
                        if ep_done < args.epochs - _early:
                            return
                        if not (
                            getattr(args, "double_buffer", 0)
                            and not getattr(args, "collect_only", 0)
                            and (args.iters <= 0 or it < args.iters)
                        ):
                            return
                        # 冻结当前权重快照（θ_{N,e3}）→ spawn；ppo_backend 尾段自然完成临时文件。
                        _snap = str(Path(args.out).with_name(f"weights-collect-{it + 1}.json"))
                        try:
                            save_weights_json(_mdl, _snap)
                            log(f"[double-buffer] early snapshot θ_{{N,e{ep_done}}} -> {_snap}")
                        except Exception as _e:
                            log(f"[double-buffer] early snapshot fail: {_e} — skip")
                            return
                        _spawned_early = True
                        _collect_child = spawn_collect_next(args, it, snap_src=_snap)
                        if _collect_child is not None:
                            log(
                                f"[double-buffer] precollect EARLY at epoch {ep_done}/{args.epochs} "
                                f"(rest of ppo_backend hides collection)"
                            )

                    _stream_kwargs = {}
                    if args.mode in ("intent", "goal"):
                        # backend/update_kwargs 注入（intent 的 value warmup + kickstarting）。
                        _stream_kwargs = {
                            "backend": ppo_backend,
                            "update_kwargs": update_kwargs(args, it, start_it, ref_model),
                        }
                    report = run_rollout_stream(
                        bun,
                        args.out,
                        traj_dir,
                        pairs,
                        args,
                        dist_cfg,
                        iter_id,
                        model,
                        opt,
                        device,
                        on_collect_done=_fire_eval,
                        on_epoch_done=_on_epoch_done,
                        extra_wver=extra_wver,
                        **_stream_kwargs,
                    )
                    stream_meta = report
                else:
                    report = run_rollout_queue(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id
                    )
                    # 串行：rollout 返回即 collector 收官；后台评估藏进随后的长 ppo_backend 空窗
                    # 吞吐 T3：非 eval 轮不派发（eval_on_round 循环级统一门控）。
                    if eval_on_round:
                        if args.mode == "per-tick":
                            eval_thread = dispatch_eval_bg(
                                bun,
                                args.out,
                                traj_dir,
                                args,
                                dist_cfg,
                                iter_id,
                                it,
                                report["winRate"],
                                local_gate=eval_gate,
                            )
                        else:
                            eval_thread = dispatch_eval_bg_m1(
                                bun, args.out, args, it, jsonl_path, args.baseline
                            )
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
            log(
                f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}"
            )
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(
                    f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}"
                )
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            if stream_meta is None:
                t_ppo = time.time()
                # P1-7：--adv-norm none 时串行路径跳过 global 归一（对照实验）
                episodes = ppo_backend.load_episodes(
                    str(traj_dir), normalize_adv=getattr(args, "adv_norm", "auto") != "none"
                )
                total_steps = sum(e["obs"].shape[0] for e in episodes)
                chunks = ppo_backend.chunk_episodes(episodes, args.mb)
                # ppo_backend epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
                if args.mode in ("intent", "goal"):
                    agg = ppo_backend.update(
                        model,
                        opt,
                        chunks,
                        args.epochs,
                        device,
                        ckpt_path=str(traj_dir / "ppo_ckpt"),
                        **update_kwargs(args, it, start_it, ref_model),
                    )
                else:
                    agg = ppo_mod.ppo_update(
                        model,
                        opt,
                        chunks,
                        args.epochs,
                        device,
                        ckpt_path=str(traj_dir / "ppo_ckpt"),
                    )
                ppo_sec = round(time.time() - t_ppo, 1)
                chunks_n = len(chunks)
                kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径
            if args.mode == "goal":
                ppo_goal.export_goal_weights(cast(GoalNet, model), args.out)
            elif args.mode == "intent":
                ppo_intent.export_intent_weights(cast(IntentNet, model), args.out)
            else:
                save_weights_json(model, args.out)
            bak = backup_weights(args.out, it, prefix=_MODE_BACKUP_PREFIX[args.mode])
            log(
                f"[run_rl] ppo it{it}: steps={total_steps} chunks={chunks_n} "
                + (
                    f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                    f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}"
                    if agg is not None
                    else "metrics n/a — ppo_backend checkpoint completed by previous process"
                )
            )
            if bak:
                log(f"[run_rl] weights archived -> {bak}")

            # v3.12 eval 延迟化（用户 2026-08-31）：eval **不阻塞训练主链**。eval 冻结本轮
            # 权重、由后台线程独立写 eval_log（dispatch_eval_bg 返回 daemon 线程），账按
            # wver 晚入（eval_done_keys 按 wver16 去重，晚到不重跑）。此处只做短软等待
            # （吃已收官尾巴 + 给在途 eval 局一段缓存缓冲，防止下轮新权重 POST purge 掐
            # 掉），长尾 eval 项留在 it+1..N 的采集/ppo_backend 空档消化——节点任务队列天然仲裁
            # （采集忙则 eval 排队，采集 done 则 eval 补做）。
            # 门判定读 eval_log 的 eval_summary（iter 字段保留原轮号 + wver），晚入账只
            # 让判定窗口顺延，判据不变。溢出预算未收官的在途局由下轮异 sha 清场 + 阈值
            # 熔断兜底（与 v3.10 前语义一致）。
            if eval_gate is not None:
                eval_gate.set()
            eval_join_sec = 0.0
            if eval_thread is not None and eval_thread.is_alive():
                budget = float(args.eval_window_sec) + 60.0
                if args.mode in ("intent", "goal"):
                    # intent/goal：eval_summary 须在 jsonl 写回前结算（止损判门依赖）。
                    log(
                        f"waiting up to {budget:.0f}s for clean-eval round before next "
                        f"weight distribution"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=budget)
                    eval_join_sec = round(time.time() - _t_join, 1)
                else:
                    soft = min(budget, 180.0)  # per-tick 软等待上限：吃尾巴 + 缓存缓冲
                    log(
                        f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                        f"(remaining eval finishes in background, wver-keyed)"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=soft)
                    eval_join_sec = round(time.time() - _t_join, 1)
            # intent/goal：回读该迭代 eval_summary（评估线程写入；止损判门的数据源）。
            eval_rec = (
                read_eval_summary(jsonl_path, it) if args.mode in ("intent", "goal") else None
            )
            # pace checkpoint（intent/goal 护栏）：iter5 首现通关。
            if args.mode in ("intent", "goal") and it == 5 and report["winRate"] <= 0:
                log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(
                    json.dumps(
                        {
                            "event": "iteration",
                            "iter": it,
                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "winRate": report["winRate"],
                            "outcomes": report["outcomes"],
                            "score_mean": report.get("scoreStats", {}).get("mean"),
                            "score_std": report.get("scoreStats", {}).get("std"),
                            "dim_means": report.get("dimMeans", {}),
                            # 意图 RL 字段（per-tick 报告无此行 → None，不破兼容）。
                            "intentCounts": report.get(
                                "intentCounts" if args.mode != "goal" else "actionCounts"
                            ),
                            "baseIntegrity": report.get("dimMeans", {}).get("baseIntegrity"),
                            "samples": report["totalSamples"],
                            "ticks": report["totalTicks"],
                            "rollout_sec": rollout_sec,
                            "ppo_sec": ppo_sec,
                            "steps": total_steps,
                            "chunks": chunks_n,
                            "policy": agg["policy"] if agg else None,
                            "value": agg["value"] if agg else None,
                            "entropy": agg["entropy"] if agg else None,
                            "kl": agg["kl"] if agg else None,
                            "mean_ret": agg["mean_ret"] if agg else None,
                            "lr": args.lr,
                            # P1-12：reward/dodge 臂版本落盘（历史实验可归因——
                            # 此前奖励规格无记录，复盘无法区分 v7/toy 臂）
                            "reward": getattr(args, "reward", ""),
                            "dodge": getattr(args, "dodge", ""),
                            "mb": args.mb,
                            "epochs": args.epochs,
                            # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
                            **(
                                {
                                    "missing": report["missing"],
                                    "expectedGames": report["expectedGames"],
                                    "dist": report["dist"],
                                }
                                if "missing" in report
                                else {}
                            ),
                            # 纯采集（用户定义）：末局结算 − 权重分发完毕；队列模式实测透传，
                            # 纯本地路径回退为 rollout 全长（无重叠即等价纯采集）。
                            "pure_collect_sec": report.get(
                                "pure_collect_sec", round(rollout_sec, 1)
                            ),
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
                        }
                    )
                    + "\n"
                )

            # 每轮 ppo_backend 写回后自动生成巡检 HTML（intent/goal 总是生成；per-tick 仅默认 traj）
            if auto_inspect:
                run_inspect(bun, it, traj_dir=traj_root)

            # F4 circuit breaker（纯逻辑在 rl/breaker.py）。agg 为 None 的轮
            # （流式 checkpoint-complete，无任何梯度步）不计连击也不告警——
            # 本来就没有发生新的策略更新。break (not raise)：下方 except 会吞掉重试。
            if agg is not None:
                # intent/goal 用放宽的 KL 熔断阈值（原 intent_rl 专属 --kl-break
                # 0.6 / --kl-break-consec 3，避免误熔断 Bug D；per-tick 用默认 0.15/3）。
                _kl_break = args.kl_break if args.mode in ("intent", "goal") else KL_BREAK
                _kl_consec = (
                    args.kl_break_consec if args.mode in ("intent", "goal") else KL_BREAK_CONSEC
                )
                kl_streak, ent_streak, tripped_now = breaker_update(
                    kl_streak,
                    ent_streak,
                    kl=agg["kl"],
                    entropy=agg["entropy"],
                    win_rate=report["winRate"],
                    kl_break=_kl_break,
                    kl_consec=_kl_consec,
                )
                if tripped_now is not None:
                    tripped = tripped_now
                if tripped is not None:
                    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                        jsonl_f.write(
                            json.dumps(
                                {
                                    "event": "circuit_break",
                                    "iter": it,
                                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "reason": tripped,
                                    "kl": agg["kl"],
                                    "kl_streak": kl_streak,
                                    "entropy": agg["entropy"],
                                    "ent_streak": ent_streak,
                                    "winRate": report["winRate"],
                                    "weights": args.out,
                                }
                            )
                            + "\n"
                        )
                    log(f"[run_rl] CRITICAL CIRCUIT-BREAK it{it}: {tripped}")
                    log(
                        f"[run_rl] training PAUSED; weights kept at {args.out}; "
                        f"inspect policy behavior before relaunching"
                    )
                    break

                if agg["kl"] > KL_WARN:
                    log(
                        f"[run_rl] WARNING kl={agg['kl']:.3f} > {KL_WARN} — policy drifting fast; "
                        f"consider lower lr/epochs"
                    )
                if prev_entropy is not None and prev_entropy - agg["entropy"] > ENT_COLLAPSE_DROP:
                    log(
                        f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} "
                        f"in one iteration (now {agg['entropy']:.3f}) — possible premature convergence"
                    )
                prev_entropy = agg["entropy"]

            # 止损判定（D4 泛化，仅 intent/goal 生效）：eval_summary 的 Δ（相对
            # baseline）在 stop-loss-at 迭代 ≤ stop-loss-delta → 停车（原
            # run_rl_intent iter15 Δ≤0 转 M9 语义由 --stop-loss-at 15 --stop-loss-delta 0 复现）。
            # P1-9（2026-09-02）：单次评估噪声 σ≈0.024~0.04，旧"Δ≤0 即停"是抛硬币——
            # 现在要求 Δ 统计显著（≤ −2σ，_eval_sigma 推 σ）且**连续 2 轮**才停车。
            if stop_loss_hit(args.mode, args.stop_loss_at, args.stop_loss_delta, it, eval_rec):
                assert eval_rec is not None  # stop_loss_hit 已保证非 None（delta 可读）
                stop_loss_streak += 1
                sigma = eval_sigma(eval_rec)
                log(
                    f"STOP-LOSS: iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                    f"(σ={'--' if sigma is None else f'{sigma:.4f}'}, "
                    f"z·σ={'--' if sigma is None else f'{2.0 * sigma:.4f}'}) "
                    f"streak={stop_loss_streak}/2 — waiting for confirmation"
                )
            else:
                stop_loss_streak = 0
            if stop_loss_streak >= 2:
                assert eval_rec is not None
                stop_reason = (
                    f"iter{it} clean-eval Δ={eval_rec['delta']:+.4f} "
                    f"<= {args.stop_loss_delta:+.4f} (显著: Δ≤−2σ) × 2 轮 — stop-loss"
                )
                log(f"STOP-LOSS CONFIRMED: {stop_reason}")
                break

            if args.keep_iters > 0:
                for old in traj_root.glob("it*"):
                    try:
                        n_old = int(old.name[2:])
                    except ValueError:
                        continue
                    if n_old <= it - args.keep_iters:
                        try:
                            shutil.rmtree(old, ignore_errors=True)  # 沙箱删除保护拦截时跳过（磁盘轮转降级）
                        except BaseException:
                            pass

            # 吞吐 T4：双缓冲 spawn 下一轮预采（仅 stream + 双缓冲开启 + 非 collect-only）。
            # 下一轮开头 join（上方）：采集藏进本轮 ppo_backend 尾段 + 非 eval 轮集群空档，墙钟直降。
            # 提前预采（--precollect-early）已在 epoch3 spawn 过 → 尾部跳过，避免双 spawn。
            if (
                getattr(args, "double_buffer", 0)
                and stream_meta is not None
                and not getattr(args, "collect_only", 0)
                and (args.iters <= 0 or it < args.iters)
                and not _spawned_early
            ):
                _collect_child = spawn_collect_next(args, it)
            consec_fail = 0
        except SystemExit as e:
            consec_fail += 1
            log_iter_error(jsonl_path, it, f"SystemExit: {e}")
            log(
                f"[run_rl] it{it} FAILED (SystemExit: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration"
            )
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 原地重试同一迭代（resume 保留已完成 shard + ppo_backend ckpt，不重跑已完局）
        except Exception as e:
            consec_fail += 1
            log_iter_error(jsonl_path, it, f"{type(e).__name__}: {e}")
            log(
                f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); "
                f"consecutive={consec_fail}/5 — retry same iteration"
            )
            if consec_fail >= 5:
                raise
            time.sleep(30)
            it -= 1  # 同上：失败迭代不前跳，杜绝静默跳轮丢语料

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")

