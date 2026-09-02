"""干净评估分发：固定语料贪心局（旁路，绝不拖垮训练主循环）。

时机由调用方决定（流式=派发队列清空时经 on_queue_drained → dispatch_eval_bg；
串行=rollout 返回后藏进 PPO 空窗）。本模块只管单轮评估的派发与对账——
OO 化（2026-09-02）：原 dispatch_eval_round 函数迁为 EvalDispatcher 类，
run() 内局部别名 + 闭包保持（行为逐字节不变）；纯函数工具在 rl/eval_local.py。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import dist_common

# 同 queue.py：Windows 下隐藏本地评估子进程的控制台窗口（避免反复弹黑窗抢焦点）。
from rl.eval_local import (
    EVAL_ITER_SUFFIX,
    EVAL_LOCAL_RELEASE_GRACE,
    EVAL_LOCAL_SLOTS_DEFAULT,
    EVAL_SEEDS,
    EVAL_TASK_ATTEMPTS,
    eval_done_keys,
    hold_for_local,
    report_winrate_safe,  # noqa: F401 — re-exported（旧模块成员，兼容外部引用）
    run_local_eval_game,
    settle_eval_summary,
)
from rl.log import log
from rl.queue import _record_agent_meta, bun_version, mm


class EvalDispatcher:
    """固定语料干净评估的单轮派发器（阻塞版，调用方放后台线程跑）。

    任何失败只记日志，绝不抛出（run() 顶层吞异常）。

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。

    本地参与（R6 补丁）：local_gate 由调用方在「训练侧已无梯度步可做」（PPO/采集
    收尾）时 set——此前本机算力让位训练，此后 idle CPU 经 run_local_eval_game 直跑
    剩余局（读派发时刻的冻结权重快照）。None = 不参与本地（旧路径行为不变）。
    """

    def __init__(
        self,
        bun: str,
        rl_path: str,
        traj_dir: Path,
        args,
        cfg: dict,
        iter_id: str,
        it: int,
        rollout_winrate: float | None = None,
        local_gate: threading.Event | None = None,
    ) -> None:
        self.bun = bun
        self.rl_path = rl_path
        self.traj_dir = traj_dir
        self.args = args
        self.cfg = cfg
        self.iter_id = iter_id
        self.it = it
        self.rollout_winrate = rollout_winrate
        self.local_gate = local_gate

    def run(self) -> None:
        """原 dispatch_eval_round 主体：run() 内局部别名，行为逐字节不变。"""
        bun = self.bun
        rl_path = self.rl_path
        traj_dir = self.traj_dir
        args = self.args
        cfg = self.cfg
        iter_id = self.iter_id
        it = self.it
        rollout_winrate = self.rollout_winrate
        local_gate = self.local_gate
        try:
            policy = cfg.get("policy", {})
            status_timeout = float(policy.get("statusTimeoutSec", 3))
            task_timeout = float(policy.get("taskTimeoutSec", 900))
            fail_streak_max = int(policy.get("nodeFailStreak", 3))
            window = float(getattr(args, "eval_window_sec", 1500) or 1500)
            deadline = time.time() + window
            wver = dist_common.weights_fingerprint(rl_path)
            key16 = wver[:16]
            eval_iter_id = f"{iter_id}{EVAL_ITER_SUFFIX}"
            eval_jsonl = traj_dir.parent / "eval_log.jsonl"
            # 采样机健康账本：eval 局与 rollout 同册入账（mode:"eval" 标记区分）——
            # 巡检「采样机健康」表按本文件聚合，eval 不入账则节点贡献被系统性低估。
            meta_path = traj_dir.parent / "dist-agent-meta.jsonl"
            n_seeds = max(0, int(getattr(args, "eval_games_per_stage", 0) or 0))
            # 干净评估语料（goal-nn）：--eval-stages 非空 = 按规格解析（如 arena
            # '1000-1002'，训练场自评）；空 = 真实关 0..total_stages-1（旧行为）。
            eval_stage_spec = str(getattr(args, "eval_stages", "") or "")
            if eval_stage_spec:
                from rl.course import parse_range

                eval_stages = parse_range(eval_stage_spec)
            else:
                eval_stages = list(range(args.total_stages))
            pairs = [(s, sd) for s in eval_stages for sd in EVAL_SEEDS[:n_seeds]]
            if not pairs:
                return
            todo = [p for p in pairs if p not in eval_done_keys(eval_jsonl, key16)]
            if not todo:
                log(f"[eval] it{it}: wver={key16[:12]}… already evaluated — skip")
                return
            t_eval_start = time.time()

            # 本地参与前提：冻结权重快照。主循环在 PPO 收尾后会原地覆盖 rl_path，
            # 本地局必须读派发时刻的 W(N)——赌时序读新权重 = 对账灾难。
            snapshot_path: str | None = None
            local_slots = max(0, int(policy.get("evalLocalSlots", EVAL_LOCAL_SLOTS_DEFAULT)))
            if local_gate is not None and local_slots > 0:
                try:
                    traj_dir.mkdir(parents=True, exist_ok=True)
                    snap = traj_dir / "_eval_frozen_weights.json"
                    shutil.copyfile(rl_path, snap)
                    snapshot_path = str(snap)
                except OSError as e:
                    log(f"[eval] WARN weights snapshot failed — local participation off: {e}")

            local_bun = bun_version(bun)
            alive = []
            for n in cfg.get("nodes", []):
                if not n.get("enabled", True):
                    continue
                nid = str(n.get("id") or n.get("url") or "?")
                ping = dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
                if ping is None:
                    continue
                if not ping.get("evalSupport"):
                    log(
                        f"[eval] node {nid}: agent lacks evalSupport — skipped "
                        f"(sync code + restart agent to enable)"
                    )
                    continue
                if mm(str(ping.get("bunVersion", "?"))) != mm(local_bun):
                    log(f"[eval] node {nid}: bun version mismatch — skipped")
                    continue
                c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
                alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
            if not alive and (local_gate is None or not snapshot_path):
                log("[eval] no eval-capable node — skipped this round")
                return
            if not alive:
                log("[eval] no eval-capable node — local-only eval this round")

            # 幂等下发（节点通常已持有 → kept；agent 重启过则补发）
            with open(rl_path, "rb") as f:
                weights_bytes = f.read()
            nodes_ok = []
            for nd in alive:
                try:
                    dist_common.post_weights(
                        nd["url"],
                        nd["key"],
                        iter_id,
                        wver,
                        weights_bytes,
                        timeout=min(300.0, max(60.0, task_timeout)),
                    )
                    nodes_ok.append(nd)
                except dist_common.DistError as e:
                    log(f"[eval] weights POST to {nd['id']} failed ({e}) — excluded")
            if not nodes_ok:
                if not alive and local_gate is not None and snapshot_path:
                    log("[eval] all weight POSTs failed — local-only eval this round")
                else:
                    log("[eval] all weight POSTs failed — skipped this round")
                    return

            total = len(todo)
            # 尾段预留量：gate 接线且本地可用时，节点不取最后 reserved 局（留给本机直跑）
            reserved = (
                min(local_slots, total)
                if (snapshot_path is not None and local_gate is not None and local_slots > 0)
                else 0
            )
            log(
                f"[eval] it{it}: dispatch {total} greedy games "
                f"(corpus={len(pairs)}, done={len(pairs) - total}) -> "
                f"{[(n['id'], n['c']) for n in nodes_ok]}"
                + (f" [local tail-reserved ×{reserved}]" if reserved else "")
            )

            pending: deque[tuple[int, int]] = deque(todo)
            lock = threading.Lock()
            seen: set[tuple[int, int]] = set()
            attempts: dict[tuple[int, int], int] = {}
            streaks = {nd["id"]: 0 for nd in nodes_ok}
            wins = [0]
            cleared_total = [0]
            outcomes: dict[str, int] = {}
            node_games: dict[str, int] = {}  # 每节点实际结算的评估局数（summary 用）
            jsonl_lock = threading.Lock()

            def record(manifest: dict, nd_id: str, task: tuple[int, int]) -> None:
                dims = manifest.get("dims") or {}
                dim_vals = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in dims.items()}
                win = 1 if manifest.get("win") else 0
                # 全歼率（方案 A 口径，§15/P0-1）：export-eval-game 已透传 cleared——
                # 敌人全灭即算歼灭，不受 BONUS TIME 窗口截断影响。门判定全歼必须读它，
                # 否则 S3/S4a 的 timeout 局被系统性少算（eval_win 偏低 10-15pp）。
                cleared = 1 if manifest.get("cleared") else 0
                row = {
                    "event": "eval",
                    "iter": it,
                    "wver": key16,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stage": task[0],
                    "seed": task[1],
                    "node": nd_id,
                    "outcome": manifest.get("outcome"),
                    "win": win,
                    "cleared": cleared,
                    "ticks": manifest.get("ticks"),
                    "score": manifest.get("score"),
                    "quality": manifest.get("quality"),
                    "dims": dim_vals,
                    "kills": manifest.get("kills"),
                    "enemyHits": manifest.get("enemyHits"),
                    "hitRate": manifest.get("hitRate"),
                    "powerUpsCollected": manifest.get("powerUpsCollected"),
                    "elapsedSec": manifest.get("elapsedSec"),
                }
                with jsonl_lock:
                    with open(eval_jsonl, "a", encoding="utf-8") as jf:
                        jf.write(json.dumps(row) + "\n")
                    # 采样机健康账本同册入账（mode:"eval"）——成功局才记，与 rollout 口径一致
                    _record_agent_meta(
                        meta_path,
                        {
                            "node": nd_id,
                            "mode": "eval",
                            "it": it,
                            "stage": task[0],
                            "seed": task[1],
                            "ok": True,
                            "win": win,
                            "elapsedSec": manifest.get("elapsedSec"),
                            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                with lock:
                    wins[0] += win
                    cleared_total[0] += cleared
                    node_games[nd_id] = node_games.get(nd_id, 0) + 1
                    oc = str(manifest.get("outcome"))
                    outcomes[oc] = outcomes.get(oc, 0) + 1

            def worker(nd: dict) -> None:
                while time.time() < deadline:
                    task = None
                    with lock:
                        if streaks.get(nd["id"], 0) >= fail_streak_max:
                            return
                        if pending:
                            # 尾段预留：gate 未放行且余量 ≤ reserved 时不取（留给本机直跑）；
                            # 宽限期强制释放防挂死。本地 worker 不受此约束。
                            if hold_for_local(
                                len(pending),
                                reserved,
                                local_gate is not None and local_gate.is_set(),
                                time.time() >= deadline - EVAL_LOCAL_RELEASE_GRACE,
                            ):
                                pass
                            else:
                                task = pending.popleft()
                                attempts[task] = attempts.get(task, 0) + 1
                                attempt = attempts[task]
                        else:
                            return
                    if task is None:
                        all_wait = min(5.0, max(0.1, deadline - time.time()))
                        time.sleep(all_wait)
                        continue
                    ok = False
                    err = ""
                    manifest: dict = {}
                    try:
                        manifest, _files = dist_common.fetch_task(
                            nd["url"],
                            nd["key"],
                            iter_id=eval_iter_id,
                            wver=wver,
                            stage=task[0],
                            seed=task[1],
                            max_ticks=args.max_ticks,
                            difficulty=args.difficulty,
                            timeout=task_timeout,
                            mode="eval",
                        )
                        why = dist_common.validate_eval_result(manifest, wver)
                        if why:
                            raise dist_common.DistError(0, why)
                        record(manifest, nd["id"], task)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                    with lock:
                        if ok:
                            seen.add(task)
                            streaks[nd["id"]] = 0
                            el = manifest.get("elapsedSec")
                            log(
                                f"[eval] {len(seen)}/{total} s{task[0]}/seed{task[1]} "
                                f"node={nd['id']} outcome={manifest.get('outcome')} "
                                f"ticks={manifest.get('ticks')} "
                                f"elapsed={str(el) + 's' if el is not None else '-'}"
                            )
                        else:
                            streaks[nd["id"]] = streaks.get(nd["id"], 0) + 1
                            if attempt < EVAL_TASK_ATTEMPTS and task not in seen:
                                pending.append(task)
                                log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                            elif task not in seen:
                                _record_agent_meta(
                                    meta_path,
                                    {
                                        "node": nd["id"],
                                        "mode": "eval",
                                        "it": it,
                                        "stage": task[0],
                                        "seed": task[1],
                                        "ok": False,
                                        "reason": err,
                                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    },
                                )
                                log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) "
                                    f"— dropped"
                                )

            def local_worker() -> None:
                """本机直跑 worker：gate 放行前让位训练（每 5s 醒来看一眼 deadline）。"""
                if snapshot_path is None or not pending:
                    return
                while time.time() < deadline:
                    if local_gate is not None and not local_gate.is_set():
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return
                        if not local_gate.wait(timeout=min(5.0, remaining)):
                            continue
                    task = None
                    with lock:
                        if pending:
                            task = pending.popleft()
                            attempts[task] = attempts.get(task, 0) + 1
                            attempt = attempts[task]
                        else:
                            return
                    ok = False
                    err = ""
                    manifest: dict = {}
                    try:
                        manifest = run_local_eval_game(
                            bun,
                            snapshot_path,
                            task[0],
                            task[1],
                            traj_dir / "local-eval" / f"rl_s{task[0]}_seed{task[1]}",
                            max_ticks=args.max_ticks,
                            difficulty=args.difficulty,
                            timeout_sec=task_timeout,
                            wver=wver,
                        )
                        why = dist_common.validate_eval_result(manifest, wver)
                        if why:
                            raise dist_common.DistError(0, why)
                        record(manifest, "local", task)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                    with lock:
                        if ok:
                            seen.add(task)
                            el = manifest.get("elapsedSec")
                            log(
                                f"[eval] {len(seen)}/{total} s{task[0]}/seed{task[1]} "
                                f"node=local outcome={manifest.get('outcome')} "
                                f"ticks={manifest.get('ticks')} "
                                f"elapsed={str(el) + 's' if el is not None else '-'}"
                            )
                        elif attempt < EVAL_TASK_ATTEMPTS and task not in seen:
                            pending.append(task)
                            log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                        elif task not in seen:
                            _record_agent_meta(
                                meta_path,
                                {
                                    "node": "local",
                                    "mode": "eval",
                                    "it": it,
                                    "stage": task[0],
                                    "seed": task[1],
                                    "ok": False,
                                    "reason": err,
                                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                },
                            )
                            log(f"[eval] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — dropped")

            threads = []
            for nd in nodes_ok:
                for _ in range(nd["c"]):
                    threads.append(
                        threading.Thread(
                            target=worker, args=(nd,), daemon=True, name=f"eval-{nd['id']}"
                        )
                    )
            if snapshot_path is not None and local_slots > 0:
                for _ in range(local_slots):
                    threads.append(
                        threading.Thread(target=local_worker, daemon=True, name="eval-local")
                    )
            for t_ in threads:
                t_.start()
            for t_ in threads:
                t_.join(timeout=max(30.0, window + task_timeout))

            settle_eval_summary(
                eval_jsonl=eval_jsonl,
                key16=key16,
                it=it,
                pairs=pairs,
                total=total,
                seen=seen,
                wins=wins,
                cleared_total=cleared_total,
                outcomes=outcomes,
                node_games=node_games,
                jsonl_lock=jsonl_lock,
                t_eval_start=t_eval_start,
                rollout_winrate=rollout_winrate,
            )
        except Exception as e:
            log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")


def dispatch_eval_round(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    args,
    cfg: dict,
    iter_id: str,
    it: int,
    rollout_winrate: float | None = None,
    local_gate: threading.Event | None = None,
) -> None:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    薄包装：OO 实现在 EvalDispatcher（rl/eval_dispatch.py 同模块——本地直跑
    runner 的 monkeypatch 需落在本模块全局名上，见 tests/test_run_rl.py）。
    """
    EvalDispatcher(bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate, local_gate).run()


def dispatch_eval_bg(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    args,
    cfg: dict,
    iter_id: str,
    it: int,
    rollout_winrate: float | None = None,
    local_gate: threading.Event | None = None,
) -> threading.Thread:
    t_ = threading.Thread(
        target=dispatch_eval_round,
        args=(bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate, local_gate),
        daemon=True,
        name=f"eval-it{it}",
    )
    t_.start()
    return t_
