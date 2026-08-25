"""干净评估分发：固定语料贪心局（旁路，绝不拖垮训练主循环）。

时机由调用方决定（流式=派发队列清空时经 on_queue_drained → dispatch_eval_bg；
串行=rollout 返回后藏进 PPO 空窗）。本模块只管单轮评估的派发与对账。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

import dist_common
from rl.log import log
from rl.queue import bun_version, mm

# 干净评估（2026-08-24，用户指令）：用各节点已缓存的同权重跑固定语料贪心局。
# 两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料恒定 → 跨 checkpoint
# 配对可比（同 seed 胜负是确定事件）。
EVAL_SEEDS = (860001, 860002)  # 固定语料种子——改动即失去与历史 checkpoint 的可比性
EVAL_ITER_SUFFIX = "ev"        # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离
EVAL_TASK_ATTEMPTS = 2         # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）


def report_winrate_safe(wr: float) -> float | None:
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


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


def dispatch_eval_round(bun: str, rl_path: str, traj_dir: Path, args, cfg: dict,
                        iter_id: str, it: int,
                        rollout_winrate: float | None = None) -> None:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。
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
        n_seeds = max(0, int(getattr(args, "eval_games_per_stage", 0) or 0))
        pairs = [(s, sd) for s in range(args.total_stages) for sd in EVAL_SEEDS[:n_seeds]]
        if not pairs:
            return
        todo = [p for p in pairs if p not in eval_done_keys(eval_jsonl, key16)]
        if not todo:
            log(f"[eval] it{it}: wver={key16[:12]}… already evaluated — skip")
            return
        t_eval_start = time.time()

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
                log(f"[eval] node {nid}: agent lacks evalSupport — skipped "
                    f"(sync code + restart agent to enable)")
                continue
            if mm(str(ping.get("bunVersion", "?"))) != mm(local_bun):
                log(f"[eval] node {nid}: bun version mismatch — skipped")
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
        if not alive:
            log("[eval] no eval-capable node — skipped this round")
            return

        # 幂等下发（节点通常已持有 → kept；agent 重启过则补发）
        with open(rl_path, "rb") as f:
            weights_bytes = f.read()
        nodes_ok = []
        for nd in alive:
            try:
                dist_common.post_weights(nd["url"], nd["key"], iter_id, wver, weights_bytes,
                                         timeout=min(300.0, max(60.0, task_timeout)))
                nodes_ok.append(nd)
            except dist_common.DistError as e:
                log(f"[eval] weights POST to {nd['id']} failed ({e}) — excluded")
        if not nodes_ok:
            log("[eval] all weight POSTs failed — skipped this round")
            return

        total = len(todo)
        log(f"[eval] it{it}: dispatch {total} greedy games "
            f"(corpus={len(pairs)}, done={len(pairs) - total}) -> "
            f"{[(n['id'], n['c']) for n in nodes_ok]}")

        pending: deque[tuple[int, int]] = deque(todo)
        lock = threading.Lock()
        seen: set[tuple[int, int]] = set()
        attempts: dict[tuple[int, int], int] = {}
        streaks = {nd["id"]: 0 for nd in nodes_ok}
        wins = [0]
        outcomes: dict[str, int] = {}
        node_games: dict[str, int] = {}  # 每节点实际结算的评估局数（summary 用）
        jsonl_lock = threading.Lock()

        def record(manifest: dict, nd_id: str, task: tuple[int, int]) -> None:
            dims = manifest.get("dims") or {}
            dim_vals = {k: (v.get("value") if isinstance(v, dict) else v)
                        for k, v in dims.items()}
            win = 1 if manifest.get("win") else 0
            row = {
                "event": "eval", "iter": it, "wver": key16,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": task[0], "seed": task[1], "node": nd_id,
                "outcome": manifest.get("outcome"), "win": win,
                "ticks": manifest.get("ticks"), "score": manifest.get("score"),
                "quality": manifest.get("quality"), "dims": dim_vals,
                "elapsedSec": manifest.get("elapsedSec"),
            }
            with jsonl_lock:
                with open(eval_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            with lock:
                wins[0] += win
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
                        task = pending.popleft()
                        attempts[task] = attempts.get(task, 0) + 1
                        attempt = attempts[task]
                    else:
                        return
                ok = False
                err = ""
                manifest: dict = {}
                try:
                    manifest, _files = dist_common.fetch_task(
                        nd["url"], nd["key"], iter_id=eval_iter_id, wver=wver,
                        stage=task[0], seed=task[1], max_ticks=args.max_ticks,
                        difficulty=args.difficulty, timeout=task_timeout, mode="eval")
                    why = dist_common.validate_eval_result(manifest, wver)
                    if why:
                        raise dist_common.DistError(0, why)
                    record(manifest, nd["id"], task)
                    ok = True
                except Exception as e:  # noqa: BLE001 — 单局失败只回队/放弃
                    err = str(e)[:200]
                with lock:
                    if ok:
                        seen.add(task)
                        streaks[nd["id"]] = 0
                        el = manifest.get("elapsedSec")
                        log(f"[eval] {len(seen)}/{total} s{task[0]}/seed{task[1]} "
                            f"node={nd['id']} outcome={manifest.get('outcome')} "
                            f"ticks={manifest.get('ticks')} "
                            f"elapsed={str(el) + 's' if el is not None else '-'}")
                    else:
                        streaks[nd["id"]] = streaks.get(nd["id"], 0) + 1
                        if attempt < EVAL_TASK_ATTEMPTS and task not in seen:
                            pending.append(task)
                            log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                        elif task not in seen:
                            log(f"[eval] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) "
                                f"— dropped")

        threads = []
        for nd in nodes_ok:
            for _ in range(nd["c"]):
                threads.append(threading.Thread(target=worker, args=(nd,), daemon=True,
                                                name=f"eval-{nd['id']}"))
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join(timeout=max(30.0, window + task_timeout))

        dropped = total - len(seen)
        # 断点续跑口径：summary 必须聚合台账中该 (iter,wver) 的全部逐局行——
        # 只统计本次补跑会低估分母（it29 实测教训：补跑 20 局写出 2/20）。
        led_wins = 0
        led_outcomes: dict[str, int] = {}
        led_nodes: dict[str, int] = {}
        try:
            with open(eval_jsonl, "r", encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if (r.get("event") != "eval" or r.get("wver") != key16
                            or r.get("iter") != it):
                        continue
                    led_wins += 1 if r.get("win") else 0
                    oc = str(r.get("outcome") or "?")
                    led_outcomes[oc] = led_outcomes.get(oc, 0) + 1
                    ndm = str(r.get("node") or "?")
                    led_nodes[ndm] = led_nodes.get(ndm, 0) + 1
        except FileNotFoundError:
            pass
        if led_outcomes:
            n = sum(led_outcomes.values())
            wins_v = led_wins
            outcomes = led_outcomes
            node_games = led_nodes
        else:
            n = len(seen)
            wins_v = wins[0]
        dropped = max(dropped, len(pairs) - n)
        clean_wr = (wins_v / n) if n else None
        summary = {
            "event": "eval_summary", "iter": it, "wver": key16,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sec": round(time.time() - t_eval_start, 1),
            "games": n, "wins": wins_v,
            "winRate": round(clean_wr, 4) if clean_wr is not None else None,
            "outcomes": outcomes, "dropped": dropped,
            "rolloutWinRate": report_winrate_safe(rollout_winrate),
            # 每节点实际结算的评估局数（勿与并发槽位混淆——首版曾误写 nd["c"]）
            "nodes": dict(sorted(node_games.items())),
        }
        with jsonl_lock:
            with open(eval_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(summary) + "\n")
        if n:
            done_msg = (f"[eval] it{it} DONE wver={key16[:12]}… clean winRate="
                        f"{clean_wr:.1%} ({wins_v}/{n}, dropped={dropped})"
                        + (f" vs rollout(sampled)={rollout_winrate:.1%}"
                           if rollout_winrate is not None else "")
                        + f" outcomes={json.dumps(outcomes)}")
            log(done_msg)
        else:
            log(f"[eval] it{it}: no game settled within window — nothing recorded")
    except Exception as e:  # noqa: BLE001 — 评估是旁路，绝不允许拖垮训练主循环
        log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")


def dispatch_eval_bg(bun: str, rl_path: str, traj_dir: Path, args, cfg: dict,
                     iter_id: str, it: int,
                     rollout_winrate: float | None = None) -> threading.Thread:
    t_ = threading.Thread(target=dispatch_eval_round,
                          args=(bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate),
                          daemon=True, name=f"eval-it{it}")
    t_.start()
    return t_
