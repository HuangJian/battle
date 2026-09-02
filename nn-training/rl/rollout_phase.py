"""rollout_phase —— 单轮采集派发（2026-09-02 从 rl/loop.py 拆出）。

run_training 主循环里与「采集子进程生命周期」相关的三块收敛于此：
  1) join_precollect_child —— 本轮开头 join 上一轮提前 spawn 的预采子进程
     （轮询 completed_pairs 而非 wait 退出，避免预采尾段拖慢主进程空等）；
  2) dispatch_rollout_phase —— 三路派发：dist 队列流式 / dist 队列串行 /
     纯本地（nodes=[] 字节一致回归基线），含 eval 派发时机接线；
  3) spawn_next_collect —— 本轮收尾 spawn 下一轮预采（双缓冲墙钟直降）。

控制流与日志逐字节沿用旧 rl/loop.py 内联实现（run() 局部别名 + 显式返回
双缓冲句柄，闭包内 nonlocal 改由返回值对外可见）。
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from rl.collect_only import precollect_snapshot_wver, spawn_collect_next
from rl.eval_dispatch import dispatch_eval_bg
from rl.eval_m1 import dispatch_eval_bg_m1
from rl.log import log
from rl.queue import RUN_ID, run_rollout, run_rollout_queue
from rl.resume import completed_pairs
from rl.stream import run_rollout_stream


def join_precollect_child(
    child: subprocess.Popen | None,
    traj_root: Path,
    it: int,
    args,
) -> subprocess.Popen | None:
    """吞吐 T4：本轮开头检查预采子进程产出。

    轮询 completed_pairs 直到足够开首波（而非 wait 子进程退出），避免预采尾段
    拖慢、主进程空等。子进程在后台继续产出剩余 shard，run_rollout_queue 的
    completed_pairs 会跳过已落盘局。返回始终为 None——句柄本轮已消费
    （超时 terminate / 正常退出），下一轮的新句柄由 spawn_next_collect 建立。
    """
    if child is None:
        return None
    import dist_common

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
        if child.poll() is not None:
            log(f"[double-buffer] precollect it{it} done rc={child.returncode}")
            _pre_ready = True
            break
        time.sleep(2)
    if not _pre_ready:
        log(f"[double-buffer] precollect it{it} timeout — fall back to own collect")
        child.terminate()
    return None


def dispatch_rollout_phase(
    args,
    bun: str,
    dist_cfg: dict | None,
    it: int,
    traj_dir: Path,
    pairs: list[tuple[int, int]],
    jsonl_path: Path,
    model,
    opt,
    device,
    ppo_backend,
    update_kwargs,
    start_it: int,
    ref_model,
    extra_wver: str | None,
    eval_on_round: bool,
) -> tuple[
    dict,
    dict | None,
    threading.Thread | None,
    threading.Event | None,
    subprocess.Popen | None,
    bool,
]:
    """单轮采集派发（dist 队列流式 / dist 队列串行 / 纯本地三路）。

    返回 (report, stream_meta, eval_thread, eval_gate, collect_child, spawned_early)。
      - stream 路径：eval_thread 经 report['_eval_thread'] 回传（on_collect_done
        回调返回值），collect_child/spawned_early 由 _on_epoch_done 回调写入、
        返回时对外可见（原 nonlocal 语义）。
      - 串行/纯本地路径：eval_thread 直接回传；eval_gate 由调用方随后 set 放行
        本地 eval 参与（R6）；collect_child/spawned_early 恒 None/False。
    """
    stream_meta: dict | None = None
    eval_thread: threading.Thread | None = None
    eval_gate: threading.Event | None = None
    collect_child: subprocess.Popen | None = None
    spawned_early = False
    if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
        iter_id = f"{RUN_ID}.{it}"
        enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
        log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
        # 本地 eval 参与的门控事件：派发即创建，ppo_backend/采集收尾时 set 放行
        eval_gate = threading.Event()
        if int(getattr(args, "stream", 0) or 0):
            # 吞吐 T3：非 eval 轮不派发（返回 None → 无 eval_thread → 调用方
            # join 跳过，集群尾段留给下一轮采集/双缓冲）。
            def _fire_eval():
                # 触发点在中央派发队列清空瞬间（on_queue_drained →
                # _fire_eval_once）：全部采集任务已派到节点、结果仍在途，
                # 评估局顺势填补收尾空槽（2026-08-25 用户修订）。
                # 线程句柄经报告回传主循环，jsonl 写回前 join——下轮新权重
                # 分发前评估必已收官或到预算。positional args 创建即快照，
                # 无闭包竞态。eval_gate 随闭包捕获：ppo_backend 收尾时 set 放行本地。
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
                return dispatch_eval_bg_m1(bun, args.out, args, it, jsonl_path, args.baseline)

            # 吞吐 T4 提前预采（--precollect-early>0）：ppo_backend 第 (epochs-early) 个
            # epoch 完成后，把当前 model（θ_{N,e3}）冻结为快照并 spawn 预采下一轮
            # 首波——预采墙钟藏进剩下的 epochs 里，而非 ppo_backend 全部结束后的串行等待。
            # 快照≈最终权重（差最后一段梯度），on-policy 带内；配合
            # --precollect-games 限制只采首波，剩余局由下轮以 θ_N 现场采。
            _early = int(getattr(args, "precollect_early", 0) or 0)
            spawned_early = False

            def _on_epoch_done(ep_done: int, _mdl):
                nonlocal collect_child
                nonlocal spawned_early
                if _early <= 0 or spawned_early:
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
                    from data.weights_io import save_weights_json as _save_json

                    _save_json(_mdl, _snap)
                    log(f"[double-buffer] early snapshot θ_{{N,e{ep_done}}} -> {_snap}")
                except Exception as _e:
                    log(f"[double-buffer] early snapshot fail: {_e} — skip")
                    return
                spawned_early = True
                collect_child = spawn_collect_next(args, it, snap_src=_snap)
                if collect_child is not None:
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
            report = run_rollout_queue(bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id)
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
    return report, stream_meta, eval_thread, eval_gate, collect_child, spawned_early


def spawn_next_collect(
    args,
    it: int,
    stream_meta,
    spawned_early: bool,
) -> subprocess.Popen | None:
    """吞吐 T4：双缓冲 spawn 下一轮预采（仅 stream + 双缓冲开启 + 非 collect-only）。

    提前预采（--precollect-early）已在 epoch3 spawn 过 → 尾部跳过，避免双 spawn。
    条件不满足返回 None（调用方把 _collect_child 归零——join 阶段已消费旧句柄）。
    """
    if (
        getattr(args, "double_buffer", 0)
        and stream_meta is not None
        and not getattr(args, "collect_only", 0)
        and (args.iters <= 0 or it < args.iters)
        and not spawned_early
    ):
        return spawn_collect_next(args, it)
    return None
