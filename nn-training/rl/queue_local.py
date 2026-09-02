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
from rl.cmd import build_rollout_cmd
from rl.log import log
from rl.reports import combine_reports

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 battle2（rl/ 上溯 3 层，修正 2026-09-02）

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
        cmd = build_rollout_cmd(
            bun,
            args,
            weights=rl_path,
            out_dir=str(wdir),
            stage=si,
            seed=seed,
            wver=wver,
            node_label="local",
        )
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


def run_local_rollout(
    bun: str, rl_path: str, traj_dir: Path, idx: int, task: tuple[int, int], args, wver: str
) -> dict:
    si, sd = task
    wdir = traj_dir / f"w{idx}"
    wdir.mkdir(parents=True, exist_ok=True)
    cmd = build_rollout_cmd(
        bun,
        args,
        weights=rl_path,
        out_dir=str(wdir),
        stage=si,
        seed=sd,
        wver=wver,
        node_label="local",
    )
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
    cfg,
    code_hash,
    upgrade_branch,
    dirty_files,
    local_bun,
    spawned_ids,
    alive,
    lock,
    weights_bytes,
    iter_id,
    wver,
    task_timeout,
    status_timeout,
    all_settled,
    deadline,
    rescan_sec,
    worker,
    extra_threads,
) -> None:
    """v3.9 动态节点发现线程主体（dispatch.run() spawn，18 参逐位对应）。

    2026-09-02 OO 拆分回归修复：旧签名带 log/dist_common/node_ping/
    request_upgrade_guarded/is_self_node/mm 六个参数——log/dist_common 本模块
    已顶层导入（拆出后不再需要注入），node_ping/request_upgrade_guarded/
    is_self_node 经 dist_common.xxx 调用（裸参数从未使用），mm 就地内联。
    修复前 dispatch 的 18 个实参会整体错位 → 线程启动即抛
    `missing 6 required positional arguments`（rollout-rescan 线程报废，
    运行中上线的节点永远不被发现）。
    """
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
                    log(f"[dist] rescan {nid}: codeHash stale — upgrade request failed ({reason})")
                continue
            remote_full = str(ping.get("bunVersion", "?"))
            # mm 就地内联（param 已删）：major.minor 一致性红线（确定性，M4）
            if ".".join(remote_full.split(".")[:2]) != ".".join(str(local_bun).split(".")[:2]):
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
                f"[dist] rescan: node {nid} online mid-run — weights {mode}, spawning {c_n} workers"
            )
            for _ in range(c_n):
                t = threading.Thread(target=worker, args=(nd,), daemon=True)
                t.start()
                extra_threads.append(t)


def pick_tail_race(inflight: dict[tuple[int, int], int], dup: int) -> tuple[int, int] | None:
    """v3.10 长尾竞速选择（纯函数，v3.10 单测覆盖）：排队队列已空时，空闲执行槽应复制
    哪个 in-flight 任务竞速——只要副本数 < tailFanoutDup 即选（**不看任务已耗时**，
    用户裁定"有空槽就派发"）。确定性：字典序最小者优先（避免多 worker 锁竞争抖动）。
    dup=1 或 inflight 为空 → None（无竞速副本名额）。"""
    cand: tuple[int, int] | None = None
    for t, c in inflight.items():
        if c < dup and (cand is None or t < cand):
            cand = t
    return cand


def race_tier_ok(speeds: dict[str, float], nid: str, top_n: int = 3) -> bool:
    """v3.11 竞速派档（纯函数，单测覆盖）：竞速副本只派给**快节点**，避免副本恰好落入
    慢节点、竞速形同虚设（用户 2026-08-31 观察："两个副本都分派到慢速节点，不还是要等"）。

    - 本机（local）：豁免（实测最快、无网络往返），永久参与竞速。
    - 无速度样本（首轮/全空）：乐观放行——没数据时不该设门槛。
    - 其余按 EWMA 耗时（speed 表，即各节点最近任务平均耗时）排序，取 top_n 快档；
      不在快档的节点不参与竞速（慢节点对竞速是负资产）。
    - 节点数 ≤ top_n：全员参与（退化回无门槛，正确）。"""
    if nid == "local":
        return True
    if not speeds:
        return True
    ranked = sorted((v, k) for k, v in speeds.items() if k != "local")
    if not ranked:
        return True
    tier = {k for _v, k in ranked[:top_n]}
    return nid in tier
