"""干净评估分发：固定语料贪心局（旁路，绝不拖垮训练主循环）。

时机由调用方决定（流式=派发队列清空时经 on_queue_drained → dispatch_eval_bg；
串行=rollout 返回后藏进 PPO 空窗）。本模块只管单轮评估的派发与对账。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import dist_common

# 同 queue.py：Windows 下隐藏本地评估子进程的控制台窗口（避免反复弹黑窗抢焦点）。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log
from rl.queue import REPO_ROOT, _record_agent_meta, bun_version, mm

# 干净评估（2026-08-24，用户指令）：用各节点已缓存的同权重跑固定语料贪心局。
# 两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料恒定 → 跨 checkpoint
# 配对可比（同 seed 胜负是确定事件）。
# 固定语料种子——前 2 个承载历史可比性（永不改动）；860003+ 为 goal-nn 扩展
# （arena 自评需要 20 seed/关的 trend 精度，纯增量、不影响旧口径）。
EVAL_SEEDS = tuple([860001, 860002, *range(860003, 860021)])
EVAL_ITER_SUFFIX = "ev"  # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离
EVAL_TASK_ATTEMPTS = 2  # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）
EVAL_LOCAL_SLOTS_DEFAULT = 4  # 本地直跑槽位默认值（policy.evalLocalSlots 可覆写；0=禁用）
EVAL_LOCAL_RELEASE_GRACE = 300  # 距窗口截止剩这些秒时强制释放本地预留（本地失效也不空转到超时）


def hold_for_local(pending_len: int, reserved: int, gate_set: bool, past_release: bool) -> bool:
    """节点 worker 是否应暂缓取任务、把队列尾段留给本机直跑。

    R6 补丁：课程起步期每轮仅 ~12 局，派发即被远端线程抢空，gate 在 PPO 收尾才
    放行——届时队列恒空，本地永远零参与。预留 = 节点不取最后 reserved 局。
    释放条件（任一）：gate 已放行 / 距窗口截止进入宽限期 / reserved<=0。
    防挂死：仅当 pending 超出预留量时节点才被允许继续取之外的判断在此收口，
    全预留场景由宽限强制释放兜底。
    """
    if reserved <= 0 or gate_set or past_release:
        return False
    return 0 < pending_len <= reserved


def report_winrate_safe(wr: float) -> float | None:
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


def run_local_eval_game(
    bun: str,
    weights_snapshot: str,
    stage: int,
    seed: int,
    out_dir: Path,
    max_ticks: int,
    difficulty: str,
    timeout_sec: float,
    wver: str,
) -> dict:
    """本机直跑一局贪心评估（与节点 agent 同一 runner / 同一报告 schema）。

    权重必须传**派发时刻的冻结快照**而非 rl_path——主循环 PPO 写回会原地覆盖
    rl_path，本地局读错版本就是对账灾难。失败抛异常交 worker 回队/放弃；
    成功返回补齐 wver/mode 回显的 manifest（mode 戳在 agent 路径由 agent 盖，
    本地路径由这里盖——validate_eval_result 的对账项）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        bun,
        "tools/sim/export-eval-game.ts",
        "--weights",
        weights_snapshot,
        "--out",
        str(out_dir),
        "--stage",
        str(stage),
        "--seed",
        str(seed),
        "--difficulty",
        difficulty,
        "--max-ticks",
        str(max_ticks),
        "--wver",
        wver,
        "--node-label",
        "local",
    ]
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        **_POPEN_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rc={proc.returncode} ({(proc.stderr or proc.stdout or '')[-160:]})")
    manifest = json.loads((Path(out_dir) / "_eval_report.json").read_text(encoding="utf-8"))
    manifest.setdefault("wver", wver)
    manifest["mode"] = "eval"
    manifest["elapsedSec"] = round(time.time() - t0, 1)
    return manifest


def eval_done_keys(eval_jsonl: Path, wver16: str) -> set[tuple[int, int]]:
    """已评估账本：eval_log.jsonl 中同 wver 的 (stage,seed) 集（断点/重启不重评）。"""
    out: set[tuple[int, int]] = set()
    try:
        if eval_jsonl.exists():
            for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "eval" and r.get("wver") == wver16:
                    try:
                        out.add((int(r["stage"]), int(r["seed"])))
                    except (KeyError, TypeError, ValueError):
                        continue
    except OSError:
        pass
    return out


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

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。

    本地参与（R6 补丁）：local_gate 由调用方在「训练侧已无梯度步可做」（PPO/采集
    收尾）时 set——此前本机算力让位训练，此后 idle CPU 经 run_local_eval_game 直跑
    剩余局（读派发时刻的冻结权重快照）。None = 不参与本地（旧路径行为不变）。
    """
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
                with open(eval_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
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

        dropped = total - len(seen)
        # 断点续跑口径：summary 必须聚合台账中该 (iter,wver) 的全部逐局行——
        # 只统计本次补跑会低估分母（it29 实测教训：补跑 20 局写出 2/20）。
        led_wins = 0
        led_clears = 0
        led_outcomes: dict[str, int] = {}
        led_nodes: dict[str, int] = {}
        try:
            with open(eval_jsonl, encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if r.get("event") != "eval" or r.get("wver") != key16 or r.get("iter") != it:
                        continue
                    led_wins += 1 if r.get("win") else 0
                    # 全歼率（方案 A 口径）：旧行（cleared 缺省）视为未全歼——新 schema
                    # 上线前的行只有本地局有 cleared，此处保守记 0，聚合以新行口径为准。
                    led_clears += 1 if r.get("cleared") else 0
                    oc = str(r.get("outcome") or "?")
                    led_outcomes[oc] = led_outcomes.get(oc, 0) + 1
                    ndm = str(r.get("node") or "?")
                    led_nodes[ndm] = led_nodes.get(ndm, 0) + 1
        except FileNotFoundError:
            pass
        if led_outcomes:
            n = sum(led_outcomes.values())
            wins_v = led_wins
            clears_v = led_clears
            outcomes = led_outcomes
            node_games = led_nodes
        else:
            n = len(seen)
            wins_v = wins[0]
            clears_v = cleared_total[0]
        dropped = max(dropped, len(pairs) - n)
        clean_wr = (wins_v / n) if n else None
        clear_rate = (clears_v / n) if n else None
        summary = {
            "event": "eval_summary",
            "iter": it,
            "wver": key16,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sec": round(time.time() - t_eval_start, 1),
            "games": n,
            "wins": wins_v,
            "winRate": round(clean_wr, 4) if clean_wr is not None else None,
            # 全歼率（敌人全灭即算，不受 BONUS TIME 截断影响）——S3/S4a 门判定读它。
            "clears": clears_v,
            "clearRate": round(clear_rate, 4) if clear_rate is not None else None,
            "outcomes": outcomes,
            "dropped": dropped,
            "rolloutWinRate": report_winrate_safe(rollout_winrate),
            # 每节点实际结算的评估局数（勿与并发槽位混淆——首版曾误写 nd["c"]）
            "nodes": dict(sorted(node_games.items())),
        }
        with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
        if n:
            done_msg = (
                f"[eval] it{it} DONE wver={key16[:12]}… clean winRate="
                f"{clean_wr:.1%} ({wins_v}/{n}, dropped={dropped})"
                + (
                    f" clearRate={clear_rate:.1%} ({clears_v}/{n})"
                    if clear_rate is not None
                    else ""
                )
                + (
                    f" vs rollout(sampled)={rollout_winrate:.1%}"
                    if rollout_winrate is not None
                    else ""
                )
                + f" outcomes={json.dumps(outcomes)}"
            )
            log(done_msg)
        else:
            log(f"[eval] it{it}: no game settled within window — nothing recorded")
    except Exception as e:
        log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")


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
