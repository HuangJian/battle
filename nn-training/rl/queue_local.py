"""queue_local —— 纯本地 rollout（单机线程池路径）。

从 rl/queue.py 拆出（2026-09-02）：run_rollout 只依赖本机并发（ThreadPoolExecutor
spawn bun 子进程），无分布式状态——独立成模块便于单测与职责分离。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log
from rl.reports import combine_reports

REPO_ROOT = Path(__file__).resolve().parent.parent

ROLLOUT_LOG_EVERY = 10  # 本地 rollout 每 N 局结算打一条进度行




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

    # 吞吐 T4：双缓冲复用前提——本地 shard 也必须写 wver（与 run_rollout_queue 的
    # local slot 对齐），否则主进程下一轮 completed_pairs 不命中、预采产物作废。
    wver = dist_common.weights_fingerprint(rl_path)
    workers = max(1, min(args.workers, len(pairs)))

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        log_f = open(wdir / "rollout.log", "w", encoding="utf-8")
        if getattr(args, "goal_rollout", False):
            # T7.2 goal RL：goal 承诺步采样器（export-goal-rollout.ts，心跳承诺期）。
            cmd = [
                bun,
                "tools/sim/export-goal-rollout.ts",
                "--weights",
                rl_path,
                "--out",
                str(wdir),
                "--stages",
                str(si),
                "--seeds",
                str(seed),
                "--max-ticks",
                str(args.max_ticks),
                "--difficulty",
                args.difficulty,
                "--heartbeat",
                str(getattr(args, "heartbeat", 240)),
            ]
            if getattr(args, "goal_coarse", False):
                cmd.append("--coarse")
        elif getattr(args, "intent_rollout", False):
            # M8 意图 RL：意图步半 MDP 采样器（export-intent-rollout.ts，replan cadence）。
            cmd = [
                bun,
                "tools/sim/export-intent-rollout.ts",
                "--weights",
                rl_path,
                "--out",
                str(wdir),
                "--stages",
                str(si),
                "--seeds",
                str(seed),
                "--max-ticks",
                str(args.max_ticks),
                "--difficulty",
                args.difficulty,
                "--replan",
                str(getattr(args, "replan", 30)),
            ]
        else:
            cmd = [
                bun,
                "tools/sim/export-rl-rollout.ts",
                "--weights",
                rl_path,
                "--out",
                str(wdir),
                "--stages",
                str(si),
                "--seeds",
                str(seed),
                "--max-ticks",
                str(args.max_ticks),
                "--difficulty",
                args.difficulty,
                "--wver",
                wver,
                "--node-label",
                "local",
            ]
            # goal-nn 卡 A2：玩具奖励臂覆盖（''=不传，导出器按 stage 解析默认）。
            if getattr(args, "reward", ""):
                cmd += ["--reward", args.reward]
            # goal-nn 卡 A3：dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。
            if getattr(args, "dodge", ""):
                cmd += ["--dodge", args.dodge]
        p = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT, **_POPEN_NO_WINDOW
        )
        rc = p.wait()
        log_f.close()
        report = None
        if rc == 0:
            report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    t0 = time.time()
    results: list[tuple[int, dict | None]] = [(1, None)] * len(pairs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, i, si, sd): i for i, (si, sd) in enumerate(pairs)}
        for done_n, fut in enumerate(as_completed(futures), 1):
            results[futures[fut]] = fut.result()
            if done_n % ROLLOUT_LOG_EVERY == 0 or done_n == len(pairs):
                log(
                    f"[rollout] local {done_n}/{len(pairs)} games settled ({time.time() - t0:.0f}s)"
                )

    failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
    if failed:
        tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
        raise SystemExit(f"[run_rl] rollout worker(s) {failed} failed:\n{tail}")

    reports = [r for _rc, r in results if r is not None]
    return combine_reports(reports)




def run_local_rollout(bun: str, rl_path: str, traj_dir: Path, idx: int, task: tuple[int, int], args, wver: str) -> dict:
    si, sd = task
    wdir = traj_dir / f"w{idx}"
    wdir.mkdir(parents=True, exist_ok=True)
    if getattr(args, "goal_rollout", False):
        cmd = [
            bun,
            "tools/sim/export-goal-rollout.ts",
            "--weights",
            rl_path,
            "--out",
            str(wdir),
            "--stages",
            str(si),
            "--seeds",
            str(sd),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--heartbeat",
            str(getattr(args, "heartbeat", 240)),
            "--wver",
            wver,
            "--node-label",
            "local",
        ]
        if getattr(args, "goal_coarse", False):
            cmd.append("--coarse")
    elif getattr(args, "intent_rollout", False):
        cmd = [
            bun,
            "tools/sim/export-intent-rollout.ts",
            "--weights",
            rl_path,
            "--out",
            str(wdir),
            "--stages",
            str(si),
            "--seeds",
            str(sd),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--replan",
            str(getattr(args, "replan", 30)),
            "--wver",
            wver,
            "--node-label",
            "local",
        ]
    else:
        cmd = [
            bun,
            "tools/sim/export-rl-rollout.ts",
            "--weights",
            rl_path,
            "--out",
            str(wdir),
            "--stages",
            str(si),
            "--seeds",
            str(sd),
            "--max-ticks",
            str(args.max_ticks),
            "--difficulty",
            args.difficulty,
            "--wver",
            wver,
            "--node-label",
            "local",
        ]
        # goal-nn 卡 A2：玩具奖励臂覆盖（''=不传，导出器按 stage 解析默认）。
        if getattr(args, "reward", ""):
            cmd += ["--reward", args.reward]
        # goal-nn 卡 A3：dodge 模式覆盖（''=不传，导出器按 stage 解析默认）。
        if getattr(args, "dodge", ""):
            cmd += ["--dodge", args.dodge]
    with open(wdir / "rollout.log", "w", encoding="utf-8") as log_f:
        # 整局墙钟计时，与远端 agent 写入 manifest 的 elapsedSec 同口径——
        # 此前 local 局无耗时数据，巡检「采样机健康」的局均耗时列对 local 恒为 '—'。
        t0 = time.time()
        p = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT, **_POPEN_NO_WINDOW
        )
        rc = p.wait()
        elapsed_sec = round(time.time() - t0, 3)
    if rc != 0:
        raise RuntimeError(f"local rollout rc={rc} (see {wdir}/rollout.log)")
    report: dict[str, Any] = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
    if report.get("wver") != wver:
        raise RuntimeError("local report wver mismatch")
    report["elapsedSec"] = elapsed_sec
    report["_dir"] = str(wdir)
    return report



def rescan_nodes(
    cfg, code_hash, upgrade_branch, dirty_files, local_bun,
    spawned_ids, alive, lock, weights_bytes, iter_id, wver,
    task_timeout, status_timeout, all_settled, deadline, rescan_sec,
    log, dist_common, node_ping, request_upgrade_guarded, is_self_node,
    mm, worker, extra_threads,
) -> None:
    configured = [n for n in cfg.get("nodes", []) if n.get("enabled", True)]
    while not all_settled.is_set() and time.time() < deadline:
        sleep_sec = min(rescan_sec, max(1.0, deadline - time.time()))
        if sleep_sec <= 0:
            return
        time.sleep(sleep_sec)
        if all_settled.is_set() or time.time() >= deadline:
            return
        for n in configured:
            nid = str(n.get("id") or n.get("url") or "?")
            with lock:
                if nid in spawned_ids:
                    continue
            ping = dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
            if ping is None:
                continue  # 仍未上线，下轮再试
            if ping.get("codeHash") != code_hash:
                # guarded 重启（跨代去重 + 脏树拒发，同 ping 门）；dedup 静默跳过
                # （rescan 周期 ~15s，重复刷屏无信息量）。
                if not upgrade_branch:
                    continue
                ok, reason = dist_common.request_upgrade_guarded(
                    nid,
                    n["url"],
                    n.get("authKey", ""),
                    upgrade_branch,
                    str(ping.get("codeHash")),
                    dirty=dirty_files,
                )
                if reason == "restart-requested":
                    log(
                        f"[dist] rescan {nid}: codeHash stale — requested upgrade "
                        f"to {upgrade_branch} (accepted)"
                    )
                elif reason.startswith("dirty-tree"):
                    log(
                        f"[dist] rescan {nid}: codeHash stale — remote restart "
                        f"suppressed ({reason}: uncommitted training-tree changes)"
                    )
                elif dist_common.is_self_node(n["url"], nid):
                    log(f"[dist] rescan {nid}: self node stale — restart-only ({reason})")
                elif reason != "dedup":
                    log(
                        f"[dist] rescan {nid}: codeHash stale — upgrade request "
                        f"failed ({reason})"
                    )
                continue
            remote_full = str(ping.get("bunVersion", "?"))
            if mm(remote_full) != mm(local_bun):
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            try:
                mode = dist_common.post_weights(
                    n["url"],
                    n.get("authKey", ""),
                    iter_id,
                    wver,
                    weights_bytes,
                    timeout=min(300.0, max(60.0, task_timeout)),
                )
            except dist_common.DistError as e:
                log(f"[dist] rescan {nid}: weights POST failed ({e}) — skip this round")
                continue
            nd = {"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n}
            with lock:
                spawned_ids.add(nid)
                alive.append(nd)
            log(
                f"[dist] rescan: node {nid} online mid-run — weights {mode}, "
                f"spawning {c_n} workers"
            )
            for _ in range(c_n):
                t = threading.Thread(target=worker, args=(nd,), daemon=True)
                t.start()
                extra_threads.append(t)

