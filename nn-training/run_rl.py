"""
run_rl.py — RL on-policy 主循环（P1.5 蒸馏 → RL 阶段）。

流程：
  ① 权重初始化（幂等）：RL 权重不存在时，从 DAgger BC 检查点 warm-start 策略头
     （价值头随机初始化）；已存在则直接续跑。
  ② 迭代 N 次：bun TS rollout（subprocess，无需 torch）→ 进程内 clipped PPO 更新
     （复用 ppo.py 的 GAE/minibatch/更新函数，模型常驻内存）→ 原子写回权重文件，
     下一轮 rollout 即用新权重（标准 on-policy）。

取代 run_rl.sh：循环逻辑单点实现于 Python（跨平台），经统一启动器进入——
venv/torch 由启动器保证，bun 由本脚本在 PATH 上定位：

  bash nn-training/start-training.sh --script run_rl.py --iters 15
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 ^
      -Script run_rl.py --iters 15          # --xxx 参数原样透传

单步调试仍可用 ppo.py 的 --init-from / --resume CLI。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
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

REPO_ROOT = Path(__file__).resolve().parents[1]

# 分布式采样（plan/distributed-rollout.md v3.3）：runId 使 iterId={runId}.{it} 全局唯一，
# 杜绝 relaunch 后旧 run 未取结果与新 run 任务在 agent 侧混叠。
RUN_ID = secrets.token_hex(8)
MAX_TASK_ATTEMPTS = 3  # 单局失败回队重试上限；超限计入 missing 当轮放弃（rotate 新鲜种子自然补覆盖）

KL_WARN = 0.08        # calibrated to our setup: healthy steady state is 0.045-0.054
ENT_COLLAPSE_DROP = 0.10  # single-iteration entropy drop that warrants a warning

# F4 circuit breaker (2026-08-22): warnings had no teeth — the R3 long run kept
# going ~60 iterations past behavioral collapse because KL>0.15 never persisted
# two consecutive iterations before it100 (spikes at it65/73/79 were singles).
# Two trip rules, either fires:
#   KL:  kl >= KL_BREAK for KL_BREAK_CONSEC consecutive iterations (violent drift)
#   ENT: entropy <= ENT_BREAK for ENT_BREAK_CONSEC consecutive iterations AND
#        winRate < ENT_BREAK_MAX_WINRATE (degenerate determinism; the R3 collapse
#        sat at 0.42-0.55 for ~60 iterations). The winRate guard avoids stopping
#        a legitimately converged high-winning policy.
# Historical check against the R3 collapse: ENT rule trips ~it70 (11 consecutive
# it63-it73 below 0.60); KL rule alone would only fire at it100 — KL is a
# lagging indicator, entropy is the leading one.
KL_BREAK = 0.15
KL_BREAK_CONSEC = 3
ENT_BREAK = 0.60
ENT_BREAK_CONSEC = 8
ENT_BREAK_MAX_WINRATE = 0.5
CIRCUIT_EXIT_CODE = 3


def log(msg: str) -> None:
    """Timestamped stdout line — the training log must be analyzable over time."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_range(s: str) -> list[int]:
    """'0-3' / '0,2,5' / '0-1,4' → [0,1,2,3] / [0,2,5] / [0,1,4]."""
    out: list[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


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
    from concurrent.futures import ThreadPoolExecutor

    workers = max(1, min(args.workers, len(pairs)))

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        log_f = open(wdir / "rollout.log", "w", encoding="utf-8")
        cmd = [
            bun,
            "tools/sim/export-rl-rollout.ts",
            "--weights", rl_path,
            "--out", str(wdir),
            "--stages", str(si),
            "--seeds", str(seed),
            "--max-ticks", str(args.max_ticks),
            "--difficulty", args.difficulty,
        ]
        p = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
        rc = p.wait()
        log_f.close()
        report = None
        if rc == 0:
            report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run_one, i, si, sd) for i, (si, sd) in enumerate(pairs)]
        results = [f.result() for f in futures]

    failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
    if failed:
        tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
        raise SystemExit(f"[run_rl] rollout worker(s) {failed} failed:\n{tail}")

    reports = [r for _rc, r in results if r is not None]
    return combine_reports(reports)


def combine_reports(reports: list[dict]) -> dict:
    """跨 worker 精确重聚合（scoreList/dimLists 原始值列表）。

    本地 rollout 与远端单局摘要同构（远端 manifest 即单局 _rl_report.json 内容，
    另带 wver/node/elapsedSec 溯源字段，不影响聚合），两条采样路径共用本函数。
    """
    combined = {"games": 0, "winRate": 0.0, "outcomes": {}, "totalSamples": 0, "totalTicks": 0,
                "scoreList": [], "dimLists": {}}
    wins = 0
    for r in reports:
        combined["games"] += r["games"]
        combined["totalSamples"] += r["totalSamples"]
        combined["totalTicks"] += r["totalTicks"]
        for o, c in r.get("outcomes", {}).items():
            combined["outcomes"][o] = combined["outcomes"].get(o, 0) + c
            if o == "stage_clear":
                wins += c
        combined["scoreList"].extend(r.get("scoreList", []))
        for k, vs in r.get("dimLists", {}).items():
            combined["dimLists"].setdefault(k, []).extend(vs)
    combined["winRate"] = round(wins / combined["games"], 4) if combined["games"] else 0.0
    sl = combined["scoreList"]
    if sl:
        n = len(sl)
        mean = sum(sl) / n
        var = sum((x - mean) ** 2 for x in sl) / max(1, n - 1)
        combined["scoreStats"] = {"mean": round(mean, 4), "std": round(var ** 0.5, 4),
                                  "min": round(min(sl), 4), "max": round(max(sl), 4)}
    combined["dimMeans"] = {k: round(sum(v) / len(v), 4)
                            for k, v in combined["dimLists"].items() if v}
    return combined


def _bun_version(bun: str) -> str:
    try:
        return subprocess.run([bun, "--version"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or "?"
    except Exception:
        return "?"


def _mm(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def completed_pairs(traj_dir: Path, wver: str) -> set[tuple[int, int]]:
    """扫描 traj_dir 已完整落盘且 manifest.wver==当前权重的 (stage,seed)——rollout 断点。

    完整 shard 判定：write_shard 先写 12 npy 后写 manifest；存在 manifest.json ⇒ 目录完整。
    仅在 manifest 显式回显 stage/seed（agent 打包时回填）后才算数，否则不计入 done。
    """
    done: set[tuple[int, int]] = set()
    if not traj_dir.exists():
        return done
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st, sd = mm.get("stage"), mm.get("seed")
        if mm.get("wver") != wver or not isinstance(st, int) or not isinstance(sd, int):
            continue
        done.add((int(st), int(sd)))
    return done


def last_completed_iter(jsonl_path: Path) -> int:
    """回读日志最后一个 iteration 事件的迭代号（it 断点续跑），无则 0。"""
    last = 0
    if not jsonl_path.exists():
        return 0
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "iteration" and isinstance(e.get("iter"), int):
            last = max(last, e["iter"])
    return last


def last_rotate_seed(jsonl_path: Path) -> int | None:
    """回读日志最后一个 run_start 的 rotateSeed（课程连续性）。

    rotateSeed 决定 build_pairs 的 (stage,seed) 序列。若跨 relaunch 每次 re-roll（含时间戳），
    重启后下轮课程 seed 与已落盘局不交 ⇒ 断点续跑剔除失效 ⇒ 重跑已完成局（浪费）。续跑继承
    上一个 run_start 的 rotateSeed，使同一 traj 的训练流课程连续，断点续跑跨 relaunch 真正生效。
    """
    if not jsonl_path.exists():
        return None
    seed = None
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "run_start" and isinstance(e.get("rotateSeed"), int):
            seed = e["rotateSeed"]
    return seed


def resumed_manifests(traj_dir: Path, wver: str) -> list[dict]:
    """收集本轮已 done（wver 匹配）shard 的单局摘要，重启续跑时并入聚合，
    使报告 games/outcomes 仍覆盖完整一轮。"""
    out: list[dict] = []
    if not traj_dir.exists():
        return out
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if mm.get("wver") == wver and isinstance(mm.get("stage"), int):
            out.append(mm)
    return out


def _record_agent_meta(meta_path: Path, rec: dict) -> None:
    """追加一条节点采样元数据到 dist-agent-meta.jsonl（巡检读它聚合进 HTML）。

    rec: {node, it, stage, seed, ok, [win, elapsedSec | reason], ts}。
    放锁内调用保证顺序；单局一次 IO，成本可忽略。
    """
    try:
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _win_of(summary: dict) -> int:
    return 1 if summary.get("outcomes", {}).get("stage_clear", 0) > 0 else 0


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


def run_rollout_queue(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]],
                      args, cfg: dict, iter_id: str) -> dict:
    """中央队列调度模式（plan/distributed-rollout.md v3.3 §5.2）。

    140 局组成全局队列（runId 种子确定性预洗牌），各节点 C_n 条工作线程 + 本机
    N_local 条线程同队消费；逐局 RPC（GET /v1/task）同步取结果。失败回队改派、
    超限当轮放弃不阻塞 PPO；节点连续失败熔断。本机与远端统一软失败语义。
    """
    from collections import deque

    policy = cfg.get("policy", {})
    task_timeout = float(policy.get("taskTimeoutSec", 900))
    window = float(policy.get("queueWindowSec", 1800))
    status_timeout = float(policy.get("statusTimeoutSec", 3))
    fail_streak_max = int(policy.get("nodeFailStreak", 3))
    wver = dist_common.weights_fingerprint(rl_path)
    local_bun = _bun_version(bun)
    iter_no = int(iter_id.rsplit(".", 1)[-1])
    meta_path = traj_dir.parent / "dist-agent-meta.jsonl"  # traj 根，跨轮累积（不被 keep-iters 清理）

    # ① ping 门：codeHash 一致 ∧ bunVersion major.minor 一致（确定性红线，M4）
    nodes = []
    for n in cfg.get("nodes", []):
        if not n.get("enabled", True):
            continue
        nid = str(n.get("id") or n.get("url") or "?")
        ping = dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
        if ping is None:
            log(f"[dist] node {nid}: ping failed — excluded this round")
            continue
        if ping.get("codeHash") != dist_common.compute_code_hash():
            log(f"[dist] node {nid}: codeHash mismatch — excluded (red)")
            continue
        remote_full = str(ping.get("bunVersion", "?"))
        if _mm(remote_full) != _mm(local_bun):
            log(f"[dist] node {nid}: bun {remote_full} vs local {local_bun} "
                f"(major.minor differs) — excluded (red)")
            continue
        if remote_full != local_bun:
            log(f"[dist] node {nid}: bun patch differs ({remote_full} vs {local_bun}) — allowed (yellow)")
        c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
        log(f"[dist] node {nid}: online, concurrency={c_n}")
        nodes.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
    if not nodes:
        log("[dist] no eligible node — falling back to local-only rollout")
        return run_rollout(bun, rl_path, traj_dir, pairs, args)

    # ② 权重一次下发；异 sha 由 agent 原子清场，同 sha 幂等不动
    with open(rl_path, "rb") as f:
        weights_bytes = f.read()
    alive = []
    for nd in nodes:
        try:
            mode = dist_common.post_weights(nd["url"], nd["key"], iter_id, wver,
                                            weights_bytes,
                                            timeout=min(300.0, max(60.0, task_timeout)))
            log(f"[dist] weights -> {nd['id']} ({mode})")
            alive.append(nd)
        except dist_common.DistError as e:
            log(f"[dist] weights POST to {nd['id']} failed ({e}) — excluded")
    if not alive:
        log("[dist] all weights POST failed — falling back to local-only rollout")
        return run_rollout(bun, rl_path, traj_dir, pairs, args)

    # ③ 中央队列 + 消费者（远端 C_n 线程 + 本机 workers 线程）
    # 断点续跑：剔除已完整落盘且 wver 匹配的局（本轮重启/重试不重跑已完成任务）。
    # pairs 元素强制 int 化：build_pairs 产生的元组可能是 numpy 标量，其 hash/相等 与
    # completed_pairs 返回的 Python int 元组不一致 → `in done` 过滤失效 → 重跑已完成局（浪费）。
    # norm_pairs 统一为 (int,int)，与 done 集合可比。
    norm_pairs = [(int(a), int(b)) for a, b in pairs]
    done = completed_pairs(traj_dir, wver)
    tasks = [p for p in norm_pairs if p not in done]
    if done:
        log(f"[dist] resume: {len(done)}/{len(norm_pairs)} pairs already done this iter — run "
            f"{len(tasks)} remaining")
    if not tasks:
        log("[dist] all pairs already done — returning empty report (PPO will resume/replay)")
        return combine_reports([])
    random.Random(f"queue:{RUN_ID}:{iter_id}").shuffle(tasks)  # 队列可复现；分配依实时负载
    pending: deque[tuple[int, int]] = deque(tasks)
    lock = threading.Lock()
    seen: set[tuple[int, int]] = set()
    attempts: dict[tuple[int, int], int] = {}
    streaks = {nd["id"]: 0 for nd in alive}
    results: list[dict] = []
    stats = {"retried": 0}
    missing_keys: set[tuple[int, int]] = set()
    all_settled = threading.Event()  # 成功+永久缺失 == 总局数 时置位，worker 立即收工
    deadline = time.time() + window
    local_slots = max(1, min(args.workers, len(tasks)))
    next_idx = [0]

    def run_local(task: tuple[int, int]) -> dict:
        si, sd = task
        idx = next_idx[0]
        next_idx[0] += 1
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        with open(wdir / "rollout.log", "w", encoding="utf-8") as log_f:
            cmd = [bun, "tools/sim/export-rl-rollout.ts",
                   "--weights", rl_path, "--out", str(wdir),
                   "--stages", str(si), "--seeds", str(sd),
                   "--max-ticks", str(args.max_ticks), "--difficulty", args.difficulty,
                   "--wver", wver, "--node-label", "local"]
            p = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
            rc = p.wait()
        if rc != 0:
            raise RuntimeError(f"local rollout rc={rc} (see {wdir}/rollout.log)")
        report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        if report.get("wver") != wver:
            raise RuntimeError("local report wver mismatch")
        return report

    def worker(nd: dict | None) -> None:
        while not all_settled.is_set() and time.time() < deadline:
            nd_id = nd["id"] if nd else "local"
            task = None
            attempt = 0
            with lock:
                if nd is not None and streaks.get(nd_id, 0) >= fail_streak_max:
                    return
                if pending:
                    task = pending.popleft()
                    attempts[task] = attempts.get(task, 0) + 1
                    attempt = attempts[task]
            if task is None:
                all_settled.wait(0.5)
                continue
            summary = None
            err = ""
            try:
                if nd is None:
                    summary = run_local(task)
                else:
                    manifest, files = dist_common.fetch_task(
                        nd["url"], nd["key"], iter_id=iter_id, wver=wver,
                        stage=task[0], seed=task[1], max_ticks=args.max_ticks,
                        difficulty=args.difficulty, timeout=task_timeout)
                    why = dist_common.validate_result(manifest, files, wver,
                                                      set(norm_pairs), seen)
                    if why:
                        raise dist_common.DistError(0, why)
                    out_dir = traj_dir / "dist" / nd_id / f"rl_s{task[0]}_seed{task[1]}"
                    dist_common.write_shard(files, manifest, str(out_dir))
                    summary = manifest
            except Exception as e:  # noqa: BLE001 — 单局任何失败都只回队/记 missing
                err = str(e)[:200]
            with lock:
                if summary is not None:
                    seen.add(task)
                    streaks[nd_id] = 0
                    results.append(summary)
                    _record_agent_meta(meta_path, {
                        "node": nd_id, "it": iter_no, "stage": task[0], "seed": task[1],
                        "ok": True, "win": _win_of(summary),
                        "elapsedSec": summary.get("elapsedSec"),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    if len(seen) + len(missing_keys) >= len(tasks):
                        all_settled.set()
                    continue
                if nd is not None:
                    streaks[nd_id] = streaks.get(nd_id, 0) + 1
                    broke = streaks[nd_id] == fail_streak_max
                else:
                    broke = False
                if attempt < MAX_TASK_ATTEMPTS:
                    pending.append(task)
                    stats["retried"] += 1
                    log(f"[dist] s{task[0]}/seed{task[1]} failed ({err}) — requeued "
                        f"(attempt {attempt}/{MAX_TASK_ATTEMPTS})")
                else:
                    missing_keys.add(task)
                    _record_agent_meta(meta_path, {
                        "node": nd_id, "it": iter_no, "stage": task[0], "seed": task[1],
                        "ok": False, "reason": err,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    log(f"[dist] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — missing this round")
                if broke:
                    log(f"[dist] node {nd_id}: {fail_streak_max} consecutive failures — "
                        f"circuit-broken for this round")
                if len(seen) + len(missing_keys) >= len(tasks):
                    all_settled.set()

    threads: list[threading.Thread] = []
    for nd in alive:
        for _ in range(nd["c"]):
            threads.append(threading.Thread(target=worker, args=(nd,), daemon=True))
    for _ in range(local_slots):
        threads.append(threading.Thread(target=worker, args=(None,), daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(30.0, window + task_timeout))

    missing = sorted(k for k in tasks if k not in seen)
    by_node: dict[str, int] = {}
    for s in results:
        nid = str(s.get("node", "?"))
        by_node[nid] = by_node.get(nid, 0) + 1
    log(f"[dist] round done: ok={len(results)}/{len(tasks)} missing={len(missing)} "
        f"retried={stats['retried']} byNode={json.dumps(by_node)}")
    if missing:
        log(f"[dist] missing pairs: {[list(k) for k in missing]}")

    combined = combine_reports(results + resumed_manifests(traj_dir, wver))
    combined["missing"] = [list(k) for k in missing]
    combined["expectedGames"] = len(pairs)
    combined["dist"] = {"iterId": iter_id, "nodes": by_node, "retried": stats["retried"], "resumed": len(done)}
    return combined


def build_pairs(args, it: int, rng, perm_state: dict) -> list[tuple[int, int]]:
    """Game pairs for iteration `it`. Rotate mode: shuffled batches — a fresh
    random permutation of all --total-stages stages is drawn once per epoch and
    sliced into ceil(total/N) consecutive batches, so full coverage holds every
    epoch while batch composition/order varies (immune to restart-position bias).
    Seeds are fresh random draws; reproducible via --seed within one launch."""
    if args.rotate_stages <= 0:
        return [(si, sd) for si in parse_range(args.stages) for sd in parse_range(args.seeds)]
    k = args.rotate_stages
    per_epoch = -(-args.total_stages // k)
    pos = (it - 1) % per_epoch
    if pos == 0 or "perm" not in perm_state:
        perm_state["perm"] = list(rng.permutation(args.total_stages))
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: new epoch — permutation "
              f"{perm_state['perm']} split into {per_epoch} batches")
    window = [int(s) for s in perm_state["perm"][pos * k:(pos + 1) * k]]
    draw = rng.integers(1, 2 ** 30, size=len(window) * args.seeds_per_stage)
    pairs = [
        (stage, int(draw[i * args.seeds_per_stage + j]))
        for i, stage in enumerate(window)
        for j in range(args.seeds_per_stage)
    ]
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: batch {pos + 1}/{per_epoch} "
          f"stages={window} (seeds {min(p[1] for p in pairs)}..{max(p[1] for p in pairs)})")
    return pairs


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
    ap.add_argument("--difficulty", default="hard")
    ap.add_argument("--max-ticks", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 12),
                    help="concurrent bun rollout workers (games partitioned by seed)")
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
    rotate_rng = np.random.default_rng(rotate_seed)  # advanced per iteration below
    perm_state: dict = {}

    with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
        jsonl_f.write(json.dumps({
            "event": "run_start", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()},
            "rotateSeed": rotate_seed,
        }) + "\n")

    log(f"[run_rl] iters={'infinite' if args.iters <= 0 else args.iters}"
        + (f" (max-hours={args.max_hours})" if args.max_hours > 0 else "")
        + " "
        + (f"rotate=shuffled {args.rotate_stages}-stage batches x{args.seeds_per_stage}seeds "
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
    ent_streak = 0  # F4: consecutive iters with entropy <= ENT_BREAK and winRate < ENT_BREAK_MAX_WINRATE
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
            pairs = build_pairs(args, it, rotate_rng, perm_state)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
                log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
                report = run_rollout_queue(bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)
            log(f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
                f"outcomes={json.dumps(report['outcomes'])} "
                f"samples={report['totalSamples']} ticks={report['totalTicks']}")
            if "scoreStats" in report:
                ss = report["scoreStats"]
                log(f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                    f"min={ss['min']:.4f} max={ss['max']:.4f}")
            if "dimMeans" in report:
                log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

            episodes = ppo_mod.load_episodes(str(traj_dir))
            total_steps = sum(e["obs"].shape[0] for e in episodes)
            chunks = ppo_mod.chunk_episodes(episodes, args.mb)
            # PPO epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
            agg = ppo_mod.ppo_update(model, opt, chunks, args.epochs, device,
                                     ckpt_path=str(traj_dir / "ppo_ckpt"))
            save_weights_json(model, args.out)
            log(f"[run_rl] ppo it{it}: steps={total_steps} chunks={len(chunks)} "
                f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}")

            with open(jsonl_path, "a", encoding="utf-8") as jsonl_f:
                jsonl_f.write(json.dumps({
                    "event": "iteration", "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "winRate": report["winRate"], "outcomes": report["outcomes"],
                    "score_mean": report.get("scoreStats", {}).get("mean"),
                    "score_std": report.get("scoreStats", {}).get("std"),
                    "dim_means": report.get("dimMeans", {}),
                    "samples": report["totalSamples"], "ticks": report["totalTicks"],
                    "steps": total_steps, "chunks": len(chunks),
                    "policy": agg["policy"], "value": agg["value"],
                    "entropy": agg["entropy"], "kl": agg["kl"],
                    "mean_ret": agg["mean_ret"], "lr": args.lr,
                    "mb": args.mb, "epochs": args.epochs,
                    # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
                    **({"missing": report["missing"], "expectedGames": report["expectedGames"],
                        "dist": report["dist"]} if "missing" in report else {}),
                }) + "\n")

            # 每轮 PPO 写回后自动生成巡检 HTML（仅默认 traj；非致命，失败不断训练）
            if auto_inspect:
                _run_inspect(bun, it)

            # F4 circuit breaker — the teeth behind KL_WARN/entropy warnings.
            # break (not raise): the except handlers below would swallow and retry.
            kl_streak = kl_streak + 1 if agg["kl"] >= KL_BREAK else 0
            ent_streak = (ent_streak + 1
                          if agg["entropy"] <= ENT_BREAK
                          and report["winRate"] < ENT_BREAK_MAX_WINRATE
                          else 0)
            if kl_streak >= KL_BREAK_CONSEC:
                tripped = f"kl>={KL_BREAK} for {kl_streak} consecutive iters (now {agg['kl']:.3f})"
            elif ent_streak >= ENT_BREAK_CONSEC:
                tripped = (f"entropy<={ENT_BREAK} for {ent_streak} consecutive iters "
                           f"(now {agg['entropy']:.3f}, winRate={report['winRate']})")
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
                log(f"[run_rl] WARNING entropy dropped {prev_entropy - agg['entropy']:.3f} in one "
                    f"iteration (now {agg['entropy']:.3f}) — possible premature convergence")
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
            log(f"[run_rl] it{it} FAILED (SystemExit: {e}); consecutive={consec_fail}/5")
            if consec_fail >= 5:
                raise
            time.sleep(30)
        except Exception as e:
            consec_fail += 1
            log(f"[run_rl] it{it} FAILED ({type(e).__name__}: {e}); consecutive={consec_fail}/5")
            if consec_fail >= 5:
                raise
            time.sleep(30)

    if tripped is not None:
        sys.exit(CIRCUIT_EXIT_CODE)
    print(f"[{time.strftime('%H:%M:%S')}] [run_rl] ALL DONE -> {args.out}")


if __name__ == "__main__":
    main()
