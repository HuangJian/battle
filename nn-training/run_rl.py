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
ROLLOUT_LOG_EVERY = 10  # 本地 rollout 每 N 局结算打一条进度行

# 干净评估（2026-08-24，用户指令）：rollout 收官后的 PPO 空窗期，用各节点已缓存的
# 同权重跑固定语料贪心局。两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料
# 恒定 → 跨 checkpoint 配对可比（同 seed 胜负是确定事件）。时机即流水线设计：此刻
# 节点持有的权重正是"上一轮 PPO 产物"，与本轮 rollout winRate 同一策略、直接对照；
# 评估墙钟完全藏在 PPO 计算里，零额外成本。
EVAL_SEEDS = (860001, 860002)  # 固定语料种子——改动即失去与历史 checkpoint 的可比性
EVAL_ITER_SUFFIX = "ev"        # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离
EVAL_TASK_ATTEMPTS = 2         # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）

KL_WARN = 0.08        # calibrated to our setup: healthy steady state is 0.045-0.054
ENT_COLLAPSE_DROP = 0.10  # single-iteration entropy drop that warrants a warning

# Per-iteration weights archive (user request 2026-08-24): every completed PPO
# write-back is copied into nn-training/weights/ with an identifiable name.
WEIGHTS_BACKUP_DIR = REPO_ROOT / "nn-training" / "weights"
WEIGHTS_BACKUP_KEEP = 20  # bounded archive: prune oldest it-backups beyond this

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


def backup_weights(weights_path: str, it: int) -> str | None:
    """Archive the just-written RL weights into nn-training/weights/.

    Name: rl-weights.it<N>.<YYYYMMDD-HHMMSS>.json — iteration-first so the
    archive sorts by training progress at a glance; the timestamp disambiguates
    re-runs of the same iter. Deliberately NOT matching weights_io's strict
    `weights.<ts>_ep<N>_val<V>.json` BC auto-discovery regex (same reason the
    manual `rl-weights.*_post-it*ppo.json` backup avoided it): eval_bridge's
    latest_weights_path must never pick up RL archives. Oldest pruned beyond
    WEIGHTS_BACKUP_KEEP; non-fatal on any IO error."""
    try:
        WEIGHTS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = WEIGHTS_BACKUP_DIR / f"rl-weights.it{it}.{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(weights_path, dst)
        baks = sorted(WEIGHTS_BACKUP_DIR.glob("rl-weights.it*.json"))
        if len(baks) > WEIGHTS_BACKUP_KEEP:
            for old in baks[:len(baks) - WEIGHTS_BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        return str(dst)
    except OSError as e:
        log(f"[run_rl] WARN weights backup failed (non-fatal): {e}")
        return None


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
    from concurrent.futures import ThreadPoolExecutor, as_completed

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

    t0 = time.time()
    results: list[tuple[int, dict | None]] = [(1, None)] * len(pairs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, i, si, sd): i for i, (si, sd) in enumerate(pairs)}
        done_n = 0
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done_n += 1
            if done_n % ROLLOUT_LOG_EVERY == 0 or done_n == len(pairs):
                log(f"[rollout] local {done_n}/{len(pairs)} games settled "
                    f"({time.time() - t0:.0f}s)")

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


def resumed_manifests(traj_dir: Path, wver: str,
                      exclude: set[tuple[int, int]] | None = None) -> list[dict]:
    """收集本轮未采样（不在 exclude）且已 done（wver 匹配）shard 的单局摘要，
    重启续跑时并入聚合，使报告 games/outcomes 仍覆盖完整一轮。

    shard manifest 是单局 schema（stage/seed/nSamples/ticks/outcome/score，无
    games/totalSamples 顶层键），必须转换为 combine_reports 消费的聚合 schema；
    exclude=本轮 results 已覆盖的 (stage,seed)。此前不排除也不转换：整轮完成后
    本轮 shard 被原样并入 → combine_reports KeyError('games') 秒崩 → 主循环吞掉
    后 it+=1 静默跳轮（2026-08-24 it2/it3 根因）。
    """
    out: list[dict] = []
    if not traj_dir.exists():
        return out
    skip = exclude or set()
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st, sd = mm.get("stage"), mm.get("seed")
        if mm.get("wver") != wver or not isinstance(st, int) or not isinstance(sd, int):
            continue
        if (int(st), int(sd)) in skip:
            continue
        if isinstance(mm.get("totalSamples"), int) and "outcomes" in mm:
            # 远端 agent 写回的 manifest 即单局聚合报告（games=1 + outcomes/
            # totalSamples/scoreList/dimLists），combine_reports 直接可消费，透传。
            out.append(mm)
            continue
        score = mm.get("score")
        out.append({
            "games": 1,
            "outcomes": {str(mm.get("outcome", "unknown")): 1},
            "totalSamples": int(mm.get("nSamples") or 0),
            "totalTicks": int(mm.get("ticks") or 0),
            "scoreList": [score] if isinstance(score, (int, float)) else [],
            # dims 细分维度暂不回填（单局 dims→dimLists 映射待统一 schema），只保
            # 证 games/outcomes/ticks/score 口径完整。
            "dimLists": {},
        })
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


def run_rollout_queue(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]],
                      args, cfg: dict, iter_id: str,
                      on_result=None, local_slots_max: int | None = None) -> dict:
    """中央队列调度模式（plan/distributed-rollout.md v3.3 §5.2）。

    140 局组成全局队列（runId 种子确定性预洗牌），各节点 C_n 条工作线程 + 本机
    N_local 条线程同队消费；逐局 RPC（GET /v1/task）同步取结果。失败回队改派、
    超限当轮放弃不阻塞 PPO；节点连续失败熔断。本机与远端统一软失败语义。

    on_result: 非空则每局结算后回调 on_result(summary)；summary 注入 "_dir"
    （该局 shard 目录，供流式 PPO 增量装载 npz）。回调在锁内做 O(1) 入队，
    重活由调用方自行异步化。
    local_slots_max: 覆盖本机并发槽（流式模式下压低以给 torch 让核）。
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
        log("[dist] all pairs already done — aggregating report from disk shards "
            "(PPO will resume/replay)")
        # 从磁盘 shard 聚合而非返回空报告：空报告曾让 it1 的 winRate/samples
        # 全为零（指标盲区）；磁盘上的 shard 与本轮计划同权重同口径。
        return combine_reports(resumed_manifests(traj_dir, wver))
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
    if local_slots_max is not None:
        local_slots = max(0, min(int(local_slots_max), len(tasks)))
    else:
        local_slots = max(1, min(args.workers, len(tasks)))
    next_idx = [0]
    # 远端集体失联保护：本机线程按满额孵化、并发闸门初始为 local_slots
    # （流式模式下被压低以给 torch 让核）；若连续 remoteDeadSecs 无任何远端
    # 结算，则闸门放开到满额，保证 PPO 语料供应不被死掉的远端拖垮。
    cap_full = max(1, min(args.workers, len(tasks)))
    local_cap = [local_slots]
    local_active = [0]
    last_remote_ok = [time.time()]
    remote_dead_sec = float(policy.get("remoteDeadSecs", 150))

    # 收尾调度（tail dispatch）：按局均耗时的 EWMA 把节点分为快/慢两档；
    # 队列剩余量降到"快速集群一波容量"以下时，慢节点停止取任务，
    # 避免最后几局落在慢节点上拖长整轮（PPO 空等）。速度表用跨轮累积的
    # dist-agent-meta.jsonl 播种、本轮在线更新；无样本节点按快速处理（乐观）。
    # 分配依旧依赖实时负载（与既有语义一致），洗牌仍由 runId 种子确定。
    tail_factor = float(policy.get("tailFastFactor", 1.8))
    tail_grace = float(policy.get("tailGraceSec", 120.0))

    def _seed_speeds() -> dict[str, float]:
        hist: dict[str, list[float]] = {}
        try:
            if meta_path.exists():
                for line in meta_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("ok") and isinstance(r.get("elapsedSec"), (int, float)) \
                            and isinstance(r.get("node"), str):
                        hist.setdefault(r["node"], []).append(float(r["elapsedSec"]))
        except OSError:
            pass
        return {k: sum(v[-20:]) / len(v[-20:]) for k, v in hist.items() if v}

    speed = _seed_speeds()
    if speed:
        preview = ", ".join(f"{k}={v:.0f}s" for k, v in sorted(speed.items(), key=lambda x: x[1]))
        log(f"[dist] tail-dispatch speeds (seeded): {preview}")
    tail_notes: set[str] = set()
    last_progress = [time.time()]

    def _fast_enough(nid: str, pending_len: int) -> bool:
        if not speed or pending_len <= 0:
            return True
        my = speed.get(nid)
        best = min(speed.values())
        if my is None or my <= best * tail_factor:
            return True
        if time.time() - last_progress[0] > tail_grace:
            key = f"{nid}:grace"
            if key not in tail_notes:
                tail_notes.add(key)
                log(f"[dist] tail-grace: no settle for {tail_grace:.0f}s — {nid} re-admitted")
            return True
        fast_slots = 0
        for a in alive:
            s = speed.get(a["id"])
            if s is not None and s <= best * tail_factor:
                fast_slots += a["c"]
        if speed.get("local", 1e18) <= best * tail_factor:
            fast_slots += local_slots
        return pending_len > fast_slots

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
        report["_dir"] = str(wdir)
        return report

    def worker(nd: dict | None) -> None:
        while not all_settled.is_set() and time.time() < deadline:
            nd_id = nd["id"] if nd else "local"
            task = None
            attempt = 0
            took_local = False
            with lock:
                if nd is not None and streaks.get(nd_id, 0) >= fail_streak_max:
                    return
                if nd is None:
                    # 远端失联 → 本机并发恢复满额（防流式 PPO 被饿死）
                    if (time.time() - last_remote_ok[0] > remote_dead_sec
                            and local_cap[0] != cap_full):
                        local_cap[0] = cap_full
                        log(f"[dist] no remote settle for {remote_dead_sec:.0f}s — "
                            f"local slots {local_slots} -> {cap_full}")
                    if local_active[0] < local_cap[0]:
                        took_local = True
                if pending and (nd is not None or took_local):
                    if _fast_enough(nd_id, len(pending)):
                        task = pending.popleft()
                        attempts[task] = attempts.get(task, 0) + 1
                        attempt = attempts[task]
                        if nd is None:
                            local_active[0] += 1
                    elif f"{nd_id}:hold" not in tail_notes:
                        tail_notes.add(f"{nd_id}:hold")
                        log(f"[dist] tail-mode: holding {nd_id} "
                            f"(ewma={speed.get(nd_id, -1):.0f}s, pending={len(pending)})")
                        task = None
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
                    manifest["_dir"] = str(out_dir)
                    summary = manifest
            except Exception as e:  # noqa: BLE001 — 单局任何失败都只回队/记 missing
                err = str(e)[:200]
            with lock:
                if nd is None and task is not None:
                    local_active[0] -= 1
                if summary is not None:
                    seen.add(task)
                    streaks[nd_id] = 0
                    if nd is not None:
                        last_remote_ok[0] = time.time()
                    results.append(summary)
                    _record_agent_meta(meta_path, {
                        "node": nd_id, "it": iter_no, "stage": task[0], "seed": task[1],
                        "ok": True, "win": _win_of(summary),
                        "elapsedSec": summary.get("elapsedSec"),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    el = summary.get("elapsedSec")
                    if isinstance(el, (int, float)) and el > 0:
                        prev = speed.get(nd_id)
                        speed[nd_id] = 0.3 * float(el) + 0.7 * prev if prev else float(el)
                    last_progress[0] = time.time()
                    if on_result:
                        try:
                            on_result(summary)
                        except Exception as cb_err:  # noqa: BLE001 — 回调异常不拖垮采集
                            log(f"[dist] on_result callback error: {str(cb_err)[:120]}")
                    log(f"[dist] {len(seen) + len(missing_keys)}/{len(tasks)} settled "
                        f"node={nd_id} s{task[0]}/seed{task[1]} "
                        f"elapsed={str(el) + 's' if el is not None else '-'}")
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
                    log(f"[dist] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — missing this round "
                        f"[{len(seen) + len(missing_keys)}/{len(tasks)} settled]")
                if broke:
                    log(f"[dist] node {nd_id}: {fail_streak_max} consecutive failures — "
                        f"circuit-broken for this round")
                if len(seen) + len(missing_keys) >= len(tasks):
                    all_settled.set()

    threads: list[threading.Thread] = []
    for nd in alive:
        for _ in range(nd["c"]):
            threads.append(threading.Thread(target=worker, args=(nd,), daemon=True))
    for _ in range(max(local_slots, cap_full)):
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

    combined = combine_reports(results + resumed_manifests(traj_dir, wver, exclude=seen))
    combined["missing"] = [list(k) for k in missing]
    combined["expectedGames"] = len(pairs)
    combined["dist"] = {"iterId": iter_id, "nodes": by_node, "retried": stats["retried"], "resumed": len(done)}
    return combined


# ---------------- 干净评估（PPO 空窗期分布式贪心局） ----------------

def _eval_done_keys(eval_jsonl: Path, wver16: str) -> set[tuple[int, int]]:
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
                        iter_id: str, it: int, rollout_winrate: float) -> None:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    节点门：enabled ∧ ping ∧ ping.evalSupport ∧ bun major.minor 一致——旧 agent 无
    能力声明即跳过（它会静默忽略 mode 参数把评估局跑成采样局），逐节点灰度点亮。
    """
    from collections import deque

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
        todo = [p for p in pairs if p not in _eval_done_keys(eval_jsonl, key16)]
        if not todo:
            log(f"[eval] it{it}: wver={key16[:12]}… already evaluated — skip")
            return

        local_bun = _bun_version(bun)
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
            if _mm(str(ping.get("bunVersion", "?"))) != _mm(local_bun):
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
        n = len(seen)
        clean_wr = (wins[0] / n) if n else None
        summary = {
            "event": "eval_summary", "iter": it, "wver": key16,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "games": n, "wins": wins[0],
            "winRate": round(clean_wr, 4) if clean_wr is not None else None,
            "outcomes": outcomes, "dropped": dropped,
            "rolloutWinRate": report_winrate_safe(rollout_winrate),
            "nodes": {nd["id"]: nd["c"] for nd in nodes_ok},
        }
        with jsonl_lock:
            with open(eval_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(summary) + "\n")
        if n:
            log(f"[eval] it{it} DONE wver={key16[:12]}… clean winRate={clean_wr:.1%} "
                f"({wins[0]}/{n}, dropped={dropped}) vs rollout(sampled)="
                f"{rollout_winrate:.1%} outcomes={json.dumps(outcomes)}")
        else:
            log(f"[eval] it{it}: no game settled within window — nothing recorded")
    except Exception as e:  # noqa: BLE001 — 评估是旁路，绝不允许拖垮训练主循环
        log(f"[eval] round error (ignored): {type(e).__name__}: {str(e)[:200]}")


def report_winrate_safe(wr: float) -> float | None:
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


def dispatch_eval_bg(bun: str, rl_path: str, traj_dir: Path, args, cfg: dict,
                     iter_id: str, it: int, rollout_winrate: float) -> threading.Thread:
    t_ = threading.Thread(target=dispatch_eval_round,
                          args=(bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate),
                          daemon=True, name=f"eval-it{it}")
    t_.start()
    return t_


def run_rollout_stream(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]],
                       args, cfg: dict, iter_id: str, model, opt, device) -> dict:
    """流式迭代（--stream 1）：采集与 PPO 重叠。

    正确性依据：整轮权重冻结为 W(N)（分发只发生在迭代边界），故任意时刻到达的
    语料都出自同一策略版本，on-policy 比率数学不受到达顺序影响；GAE 用采样时
    存储的 value 计算，与装载时机无关。

    机制：collector 线程跑 run_rollout_queue（本机槽压到 max(2, workers//4)
    给 torch 让核），每局结算回调注入待处理队列；主线程每当积压 ≥
    policy.streamWaveGames（默认 12）局就把这批 shard 装载（load_shard + GAE，
    wave 内 advantage 归一化）、chunkify 后按 --epochs 遍更新——每局总更新遍数
    与串行模式一致。轮内累计 KL 超 policy.streamKlCap（默认 0.12）则停止后续
    更新、只采不训（权重以当前状态收尾）。

    与串行模式的语义差异（已记录 nn.progress.md §6.7）：①adv 归一化从"全轮"
    变为"每 wave"；②早期 wave 的更新发生在 θ 漂移更早处（PPO clip 容忍范围）；
    ③PPO epoch checkpoint 流式期间不落盘（崩溃重启该轮重训，语料靠
    completed_pairs 秒回）；④断点续跑轮里此前已落盘的旧局不参与本轮更新。
    """
    import collections
    import threading as _th

    import numpy as np

    ppo = ppo_mod
    pend: collections.deque = collections.deque()
    lock = _th.Lock()
    box: dict = {}

    def _on_result(summary):
        with lock:
            pend.append(dict(summary))

    local_slots = max(2, int(args.workers) // 4)
    policy = cfg.get("policy", {})
    kl_cap = float(policy.get("streamKlCap", 0.12))
    wave_games = max(4, int(policy.get("streamWaveGames", 12)))

    def _collector():
        try:
            box["report"] = run_rollout_queue(
                bun, rl_path, traj_dir, pairs, args, cfg, iter_id,
                on_result=_on_result, local_slots_max=local_slots)
        except Exception as e:  # noqa: BLE001 — 主线程统一上报
            box["err"] = str(e)

    state = {"cum_kl": 0.0, "steps": 0, "chunks": 0, "ppo_sec": 0.0,
             "halted": False, "last_agg": None}

    def _shard_dir(entry: str) -> str | None:
        """本地局 _dir 指向 rollout 工作目录（w9/rl_s30_seed619823394/*.npy 多一层
        子目录），远程局 _dir 直接就是 shard 目录（obs.npy 平铺）。探测含 obs.npy 的一层。"""
        if os.path.exists(os.path.join(entry, "obs.npy")):
            return entry
        try:
            for sub in os.listdir(entry):
                cand = os.path.join(entry, sub)
                if os.path.isfile(os.path.join(cand, "obs.npy")):
                    return cand
        except OSError:
            pass
        return None

    def _load_wave(summaries: list[dict]) -> list[dict]:
        eps = []
        for s in summaries:
            d = s.get("_dir")
            if not d:
                continue
            shard = _shard_dir(d)
            if shard is None:
                continue
            try:
                dd = ppo.load_shard(shard)
            except Exception as e:  # noqa: BLE001 — 单局坏 shard 跳过
                log(f"[stream] skip bad shard {shard}: {str(e)[:100]}")
                continue
            if dd["obs"].shape[0] == 0:
                continue
            adv, ret = ppo.compute_gae(dd["reward"], dd["value"], dd["done"],
                                       ppo.GAMMA, ppo.LAM)
            eps.append({
                "obs": dd["obs"], "scalars": dd["scalars"],
                "a_move": dd["a_move"], "a_fire": dd["a_fire"], "a_item": dd["a_item"],
                "lp_move": dd["lp_move"], "lp_fire": dd["lp_fire"], "lp_item": dd["lp_item"],
                "value": dd["value"], "adv": adv.astype(np.float32),
                "ret": ret.astype(np.float32), "mask": dd["mask"],
            })
        if eps:
            all_adv = np.concatenate([e["adv"] for e in eps])
            mean, std = all_adv.mean(), all_adv.std() + 1e-8
            for e in eps:
                e["adv"] = ((e["adv"] - mean) / std).astype(np.float32)
        return eps

    def _drain(final: bool, cap: int | None = None) -> None:
        took: list[dict] = []
        with lock:
            # cap 限制单波规模：it15 教训——无上限 drain 曾一口吞 90 局，
            # 单波 376 步算了 20 分钟，流水线碎度全毁、KL 曲线也变粗。
            while pend and (cap is None or len(took) < cap):
                took.append(pend.popleft())
        if not took:
            return
        if state["halted"]:
            log(f"[stream] KL cap reached — dropped {len(took)} settled games from training")
            return
        eps = _load_wave(took)
        if not eps:
            return
        chs = ppo.chunk_episodes(eps, args.mb)
        t_p = time.time()
        agg_w = ppo.ppo_update(model, opt, chs, args.epochs, device)
        state["ppo_sec"] += time.time() - t_p
        state["cum_kl"] += float(agg_w["kl"])
        state["steps"] += len(chs) * args.epochs
        state["chunks"] += len(chs)
        state["last_agg"] = agg_w
        log(f"[stream] wave: {len(took)} games -> {len(chs)} chunks x{args.epochs}ep "
            f"kl={agg_w['kl']:.4f} cum_kl={state['cum_kl']:.4f} "
            f"ent={agg_w['entropy']:.3f}")
        if state["cum_kl"] > kl_cap:
            state["halted"] = True
            log(f"[stream] cumulative KL {state['cum_kl']:.4f} > cap {kl_cap} — "
                f"switching to collect-only for the rest of this round")

    th = _th.Thread(target=_collector, daemon=True)
    t0 = time.time()
    log(f"[stream] collector started (local_slots={local_slots}, "
        f"wave={wave_games} games, kl_cap={kl_cap})")
    wave_cap = max(wave_games * 2, 24)
    th.start()
    while True:
        with lock:
            n_pending = len(pend)
        if n_pending >= wave_games:
            _drain(False, cap=wave_cap)
            continue
        if not th.is_alive():
            break
        th.join(timeout=3.0)
    collect_done = time.time()
    th.join(timeout=5.0)
    while True:
        with lock:
            n_pending = len(pend)
        if n_pending == 0:
            break
        _drain(True, cap=wave_cap)
    if "err" in box:
        raise RuntimeError(f"stream collector failed: {box['err']}")
    report = box.get("report")
    if report is None:
        raise RuntimeError("stream collector produced no report")
    # 断点续跑轮可能零结算（全部秒回）
    if state["chunks"] == 0 and int(getattr(args, "epochs", 0)) > 0:
        eps_done = ppo._ppo_load(str(traj_dir / "ppo_ckpt"), model, opt)
        if eps_done >= args.epochs:
            # 该轮 PPO 已在先前进程中完整跑完：权重以当前状态收尾即可，
            # 重复调用 ppo_update 会走"剩余 0 epoch"路径（空聚合）。
            log(f"[stream] no fresh settles + PPO checkpoint already complete "
                f"({eps_done}/{args.epochs} epochs) — weights final, skipping update")
        else:
            log("[stream] no fresh settles this round — falling back to full-disk update")
            episodes = ppo.load_episodes(str(traj_dir))
            chunks = ppo.chunk_episodes(episodes, args.mb)
            t_p = time.time()
            state["last_agg"] = ppo.ppo_update(model, opt, chunks, args.epochs, device,
                                               ckpt_path=str(traj_dir / "ppo_ckpt"))
            state["ppo_sec"] += time.time() - t_p
            state["steps"] = sum(e["obs"].shape[0] for e in episodes)
            state["chunks"] = len(chunks)
    rollout_sec = round(collect_done - t0, 1)  # 纯采集窗口（不含收尾训练尾巴）
    tail_sec = round(time.time() - collect_done, 1)
    log(f"[stream] done: games={report['games']} waves_kl_cum={state['cum_kl']:.4f} "
        f"steps={state['steps']} chunks={state['chunks']} "
        f"collect_wall={rollout_sec}s tail_update={tail_sec}s ppo_cpu={state['ppo_sec']:.0f}s")
    # agg=None 表示本轮没有发生任何梯度步（checkpoint 已在先前进程完整跑完）。
    # 指标不伪造为 0——jsonl 写 null，报告显示 '—'，健康判定自动忽略该轮。
    last = state["last_agg"]
    report["_stream"] = {"rollout_sec": rollout_sec, "ppo_sec": round(state["ppo_sec"], 1),
                         "steps": state["steps"], "chunks": state["chunks"],
                         "kl_cum": state["cum_kl"], "agg": last}
    return report


def build_pairs(args, it: int, rotate_seed: int) -> list[tuple[int, int]]:
    """Game pairs for iteration `it` — 纯函数 of (rotateSeed, it)，与调用顺序无关。

    2026-08-24 it5 重跑事故根因：旧实现从单一连续流按调用顺序抽签，重启后 it5
    复用了流头（= 旧 it3 的签）→ 与已落盘 shard 完全不相交 → 断点续跑剔除失效、
    整轮重跑 + 语料膨胀。现改为按 (rotateSeed, it) 派生独立流：permutation 按
    epoch 键控（同 epoch 内窗口平铺一个公共排列），seeds 按 it 键控（同一 it 跨
    重启逐字节一致）。
    """
    if args.rotate_stages <= 0:
        return [(si, sd) for si in parse_range(args.stages) for sd in parse_range(args.seeds)]
    import numpy as np

    k = args.rotate_stages
    per_epoch = -(-args.total_stages // k)
    pos = (it - 1) % per_epoch
    epoch_idx = (it - 1) // per_epoch
    rng_perm = np.random.default_rng([rotate_seed, 0xA11CE, epoch_idx])
    perm = [int(s) for s in rng_perm.permutation(args.total_stages)]
    if pos == 0:
        print(f"[{time.strftime('%H:%M:%S')}] [run_rl] rotate: new epoch — permutation "
              f"{perm} split into {per_epoch} batches")
    window = perm[pos * k:(pos + 1) * k]
    rng_draw = np.random.default_rng([rotate_seed, 0xB0B, it])
    draw = rng_draw.integers(1, 2 ** 30, size=len(window) * args.seeds_per_stage)
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
            pairs = build_pairs(args, it, rotate_seed)
            # 动态读取节点配置（每轮一次）：有 enabled 节点 → 队列调度模式；
            # nodes=[] / 文件缺失 → 现有纯本地路径零改动（字节一致回归基线）。
            dist_cfg = dist_common.load_dist_config()
            t_rollout = time.time()
            stream_meta = None
            dist_iter_id: str | None = None
            if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
                iter_id = f"{RUN_ID}.{it}"
                dist_iter_id = iter_id
                enabled = [n for n in dist_cfg["nodes"] if n.get("enabled", True)]
                log(f"[dist] queue mode iterId={iter_id} nodes={[n.get('id') for n in enabled]}")
                if int(getattr(args, "stream", 0) or 0):
                    report = run_rollout_stream(
                        bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id,
                        model, opt, device)
                    stream_meta = report
                else:
                    report = run_rollout_queue(bun, args.out, traj_dir, pairs, args, dist_cfg, iter_id)
            else:
                report = run_rollout(bun, args.out, traj_dir, pairs, args)

            if stream_meta is not None:
                _sm = report.pop("_stream")
                rollout_sec = _sm["rollout_sec"]
                ppo_sec = _sm["ppo_sec"]
                total_steps = _sm["steps"]
                chunks_n = _sm["chunks"]
                agg = _sm["agg"]
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

            # 干净评估：rollout 收官、PPO 空窗期，用各节点已缓存的同权重（= 上一轮 PPO
            # 产物，与本轮 rollout winRate 同一策略）跑固定语料贪心局。后台线程不阻塞
            # PPO；下轮权重分发时未完成的评估局自然作废（账本只收已结算的）。
            if dist_iter_id is not None and dist_cfg is not None \
                    and int(getattr(args, "eval_games_per_stage", 0) or 0) > 0:
                dispatch_eval_bg(bun, args.out, traj_dir, args, dist_cfg, dist_iter_id, it,
                                 report["winRate"])

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
            save_weights_json(model, args.out)
            bak = backup_weights(args.out, it)
            log(f"[run_rl] ppo it{it}: steps={total_steps} chunks={chunks_n} "
                + (f"policy={agg['policy']:.4f} value={agg['value']:.4f} "
                   f"entropy={agg['entropy']:.4f} kl={agg['kl']:.5f} -> {args.out}"
                   if agg is not None else
                   "metrics n/a — PPO checkpoint completed by previous process"))
            if bak:
                log(f"[run_rl] weights archived -> {bak}")

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
