"""中央队列调度：远端 agent 节点 + 本地槽位同队消费，含纯本地回退。

协议契约见 dist_common.py（plan/distributed-rollout.md）。本模块只做编排：
ping 门 → 权重下发 → 任务派发/回队/熔断 → 聚合报告。
"""

from __future__ import annotations

import json
import random
import secrets
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import dist_common

# Windows：spawn 本地对局子进程（bun/node 跑游戏模拟）时使用 CREATE_NO_WINDOW，
# 否则每个本地槽位都会开一个黑色 cmd 控制台窗口，反复弹出抢占焦点。stdout/stderr
# 已重定向到文件，故隐藏窗口不影响日志落盘。（非 win32 平台此 dict 为空，无副作用）
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log
from rl.reports import combine_reports, win_of
from rl.resume import completed_pairs, resumed_manifests

REPO_ROOT = Path(__file__).resolve().parents[2]

# 分布式采样（plan/distributed-rollout.md v3.3）：runId 使 iterId={runId}.{it} 全局唯一，
# 杜绝 relaunch 后旧 run 未取结果与新 run 任务在 agent 侧混叠。
RUN_ID = secrets.token_hex(8)
MAX_TASK_ATTEMPTS = (
    3  # 单局失败回队重试上限；超限计入 missing 当轮放弃（rotate 新鲜种子自然补覆盖）
)
ROLLOUT_LOG_EVERY = 10  # 本地 rollout 每 N 局结算打一条进度行


def bun_version(bun: str) -> str:
    try:
        return (
            subprocess.run(
                [bun, "--version"], capture_output=True, text=True, timeout=10, **_POPEN_NO_WINDOW
            ).stdout.strip()
            or "?"
        )
    except Exception:
        return "?"


def mm(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


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


def run_rollout_queue(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    pairs: list[tuple[int, int]],
    args,
    cfg: dict,
    iter_id: str,
    on_result=None,
    local_slots_max: int | None = None,
    tail_dispatch: bool = True,
    halt_event: threading.Event | None = None,
    on_queue_drained=None,
    local_suspend: threading.Event | None = None,
    extra_wver: str | None = None,
) -> dict:
    """中央队列调度模式（plan/distributed-rollout.md v3.3 §5.2）。

    140 局组成全局队列（runId 种子确定性预洗牌），各节点 C_n 条工作线程 + 本机
    N_local 条线程同队消费；逐局 RPC（GET /v1/task）同步取结果。失败回队改派、
    超限当轮放弃不阻塞 PPO；节点连续失败熔断。本机与远端统一软失败语义。

    on_result: 非空则每局结算后回调 on_result(summary)；summary 注入 "_dir"
    （该局 shard 目录，供流式 PPO 增量装载 npz）。回调在锁内做 O(1) 入队，
    重活由调用方自行异步化。
    local_slots_max: 覆盖本机并发槽（流式模式下压低以给 torch 让核）。
    halt_event: 非空且被置位时 worker 不再领取新任务（在途局自然收尾）——
    流式 KL 熔断止损用：更新预算耗尽后，继续采集是无人消费的纯浪费。
    on_queue_drained: 中央派发队列清空（全部采集任务已交到节点/本地线程手上、
    结果仍在途）即刻回调——干净评估据此进场填收尾空槽（2026-08-25 用户修订：
    取代初版「权重分发完即派」，那会与采集全程抢节点）。
    local_suspend: 非空且被置位时本机直跑线程停止领取新任务（在途局自然收尾）
    ——R6 语义：「--local-slots 的 N 个 worker 在 dist 阶段最先被分派，PPO 启动后
    暂时不参与 dist」。集群停摆豁免：远端失联超 remoteDeadSec 时让位自动失效，
    保底采样不被挂死。
    """
    t_queue_enter = time.time()  # ping+权重下发阶段计时起点（→ dist_phase_sec）

    policy = cfg.get("policy", {})
    task_timeout = float(policy.get("taskTimeoutSec", 900))
    window = float(policy.get("queueWindowSec", 1800))
    status_timeout = float(policy.get("statusTimeoutSec", 3))
    fail_streak_max = int(policy.get("nodeFailStreak", 3))
    # 主动升级机制（M8）：policy.upgradeBranch 非空时，ping 发现 codeHash stale 的
    # 节点 → POST /v1/restart 指示它 git pull + 重启（本轮不参与，重启后 rescan 纳入）。
    # 节点远控升级分支：永远以训练机当前分支为准（run_rl 启动时锁存到
    # dist_common.UPGRADE_BRANCH）。config 的 upgradeBranch 只在该锁存缺失时兜底
    # （2026-08-30 事故：残留 'intent-ai' 把全部节点 reset 回旧代码）。
    upgrade_branch = dist_common.upgrade_branch_or(str(policy.get("upgradeBranch") or ""))
    wver = dist_common.weights_fingerprint(rl_path)
    local_bun = bun_version(bun)
    iter_no = int(iter_id.rsplit(".", 1)[-1])
    meta_path = (
        traj_dir.parent / "dist-agent-meta.jsonl"
    )  # traj 根，跨轮累积（不被 keep-iters 清理）

    # ① ping 门：codeHash 一致 ∧ bunVersion major.minor 一致（确定性红线，M4）
    # v4.0 ping-first 并行化（用户指令 2026-08-29，移植 m1-eval 激活模式）：死节点的
    # ping 超时（statusTimeoutSec=3s）全部并行——7 死节点从串行 ~21s/轮 压到 ~3s；
    # 判定与日志按配置顺序串行回放（保序、保线程安全）。节点中途上线的接管仍靠
    # 下一轮迭代的 ping 门（与 rollout 既有语义一致）。
    code_hash = dist_common.compute_code_hash()
    # 脏工作区护栏（2026-09-01 重启循环修复①）：期望 codeHash 由训练机工作区算出，
    # 含未提交改动时远端 git pull 永不收敛 ⇒ 对远端节点下发 pull+restart 是无效扰动
    # （实测：节点拉到最新提交后 hash 仍不等 → 每轮 rescan 再杀一次，无限重启循环）。
    # 每轮检测一次；self/回环节点不受限（代码同源，纯重启即可拾取工作区代码）。
    dirty_files = dist_common.dirty_hash_files()
    if dirty_files:
        log(
            f"[dist] WARN: {len(dirty_files)} uncommitted file(s) in codeHash set "
            f"({', '.join(dirty_files[:5])}{', …' if len(dirty_files) > 5 else ''}) — "
            f"remote restart suppressed until committed+pushed"
        )
    cfg_nodes = [n for n in cfg.get("nodes", []) if n.get("enabled", True)]

    def _probe(n: dict):
        nid = str(n.get("id") or n.get("url") or "?")
        return nid, dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)

    probe_results: list = []
    if cfg_nodes:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(cfg_nodes))) as _ex:
            probe_results = list(_ex.map(_probe, cfg_nodes))

    nodes = []
    for (nid, ping), n in zip(probe_results, cfg_nodes, strict=False):
        if ping is None:
            log(f"[dist] node {nid}: ping failed — excluded this round")
            continue
        if ping.get("codeHash") != code_hash:
            # 主动升级（guarded，2026-09-01 重启循环修复②）：分支 = 训练机当前分支
            # （dist_common.UPGRADE_BRANCH 锁存）。护栏见 dist_common.request_upgrade_
            # guarded——跨代去重（同节点同 agent codeHash 只杀一次）+ 脏工作区拒发
            # （远端 pull 永不收敛，手动更新重启的进程不再被远控杀掉）。
            if not upgrade_branch:
                log(f"[dist] node {nid}: codeHash mismatch — excluded (red)")
                continue
            ok, reason = dist_common.request_upgrade_guarded(
                nid,
                n["url"],
                n.get("authKey", ""),
                upgrade_branch,
                str(ping.get("codeHash")),
                dirty=dirty_files,
            )
            if dist_common.is_self_node(n["url"], nid):
                log(
                    f"[dist] node {nid}: self node stale — restart-only upgrade "
                    f"({reason}), no git pull; excluded this round"
                )
            elif reason == "restart-requested":
                log(
                    f"[dist] node {nid}: requested upgrade to {upgrade_branch} "
                    f"(accepted) — will rejoin after restart"
                )
            elif reason == "dedup":
                log(
                    f"[dist] node {nid}: codeHash mismatch — restart already sent "
                    f"for this agent codeHash (dedup) — excluded this round"
                )
            elif reason.startswith("dirty-tree"):
                log(
                    f"[dist] node {nid}: codeHash mismatch — remote pull cannot "
                    f"converge (uncommitted training-tree changes), restart "
                    f"suppressed ({reason}) — excluded this round"
                )
            else:
                log(
                    f"[dist] node {nid}: codeHash mismatch — upgrade request failed "
                    f"({reason}) — excluded this round"
                )
            continue
        remote_full = str(ping.get("bunVersion", "?"))
        if mm(remote_full) != mm(local_bun):
            log(
                f"[dist] node {nid}: bun {remote_full} vs local {local_bun} "
                f"(major.minor differs) — excluded (red)"
            )
            continue
        if remote_full != local_bun:
            log(
                f"[dist] node {nid}: bun patch differs ({remote_full} vs {local_bun}) — allowed (yellow)"
            )
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
    if getattr(args, "goal_rollout", False):
        wkind = "goal"
    elif getattr(args, "intent_rollout", False):
        wkind = "intent"
    else:
        wkind = "rollout"
    for nd in nodes:
        try:
            mode = dist_common.post_weights(
                nd["url"],
                nd["key"],
                iter_id,
                wver,
                weights_bytes,
                timeout=min(300.0, max(60.0, task_timeout)),
                kind=wkind,
            )
            log(f"[dist] weights[{wkind}] -> {nd['id']} ({mode})")
            alive.append(nd)
        except dist_common.DistError as e:
            log(f"[dist] weights POST to {nd['id']} failed ({e}) — excluded")
    if not alive:
        log("[dist] all weights POST failed — falling back to local-only rollout")
        return run_rollout(bun, rl_path, traj_dir, pairs, args)
    # 纯采集起点（用户定义 2026-08-24）：新权重分发完毕的时刻。
    # 纯采集耗时 = 最后一局结算时刻 − 本时刻；与 PPO 重叠与否无关，就是两个事件锚点。
    t_dist_done = time.time()
    last_settle_at = [None]  # 最后一局成功结算的时刻（worker 内更新）

    # ③ 中央队列 + 消费者（远端 C_n 线程 + 本机 workers 线程）
    # 断点续跑：剔除已完整落盘且 wver 匹配的局（本轮重启/重试不重跑已完成任务）。
    # pairs 元素强制 int 化：build_pairs 产生的元组可能是 numpy 标量，其 hash/相等 与
    # completed_pairs 返回的 Python int 元组不一致 → `in done` 过滤失效 → 重跑已完成局（浪费）。
    # norm_pairs 统一为 (int,int)，与 done 集合可比。
    norm_pairs = [(int(a), int(b)) for a, b in pairs]
    # 断点续跑按「本轮计划」口径：目录里可能有跨配置残留的同权重 shard（sps 变更后
    # 重启同一迭代，it60 实测目录 235 局/计划 105 局），它们不在新计划里——既不重跑
    # 也不并入报告。done 一词自此恒指计划内已完成。
    plan_set = set(norm_pairs)
    done_all = completed_pairs(traj_dir, wver, extra_wver=extra_wver)
    done = done_all & plan_set
    tasks = [p for p in norm_pairs if p not in done]
    if done_all:
        log(
            f"[dist] resume: {len(done)}/{len(norm_pairs)} planned pairs already on disk — "
            f"run {len(tasks)} remaining"
            + (
                f" (ignoring {len(done_all) - len(done)} off-plan shards)"
                if len(done_all) != len(done)
                else ""
            )
        )
    if not tasks:
        log(
            "[dist] all planned pairs already on disk — aggregating report from shards "
            "(PPO will resume/replay)"
        )
        # 从磁盘 shard 聚合而非返回空报告：空报告曾让 it1 的 winRate/samples
        # 全为零（指标盲区）；only=plan_set 保证跨配置残留下聚合口径仍等于本轮计划。
        # 补齐 missing/expectedGames/dist：与全流程路径同 schema，下游免分支。
        combined = combine_reports(
            resumed_manifests(traj_dir, wver, only=plan_set, extra_wver=extra_wver)
        )
        combined["missing"] = []
        combined["expectedGames"] = len(pairs)
        combined["dist"] = {
            "iterId": iter_id,
            "nodes": {},
            "retried": 0,
            "resumed": len(done),
            "offPlanShards": max(0, len(done_all) - len(done)),
        }
        return combined
    random.Random(f"queue:{RUN_ID}:{iter_id}").shuffle(tasks)  # 队列可复现；分配依实时负载
    if local_slots_max is not None:
        local_slots = max(0, min(int(local_slots_max), len(tasks)))
    else:
        local_slots = max(1, min(args.workers, len(tasks)))
    n_total_tasks = len(tasks)
    # R6：--local-slots 头部分配——洗牌后的前 local_slots 个任务划入本机专用队列，
    # 节点线程不可触及（确定性「dist 阶段最先被分派」）；本机让位（local_suspend）
    # 时一次性并回主队列交远端消化，防挂死。
    head_tasks: deque[tuple[int, int]] = deque()
    if local_slots > 0 and tasks:
        k = min(local_slots, len(tasks))
        head_tasks = deque(tasks[:k])
        tasks = tasks[k:]
    all_tasks = list(tasks)  # 全量任务清单（含已划入本机保留段的）——完成判定/missing 口径
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
    next_idx = [0]
    # 远端集体失联保护：本机线程按满额孵化、并发闸门初始为 local_slots
    # （流式模式下被压低以给 torch 让核）；若连续 remoteDeadSecs 无任何远端
    # 结算，则闸门放开到满额，保证 PPO 语料供应不被死掉的远端拖垮。
    cap_full = max(1, min(args.workers, n_total_tasks))
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
    # v3.7 尾部 fan-out（用户需求 2026-08-27）：pending 剩 ≤ tail_fanout_n 时，空闲执行槽
    # 优先复制一个在跑的尾部任务（重复派发），与主副本竞速取先返回——即使有 EWMA 分档，
    # 末尾任务仍可能落在低速 agent（EWMA 是预期、单局有方差），fan-out 用重复执行兜底。
    tail_fanout_n = int(policy.get("tailFanoutN", 4))
    tail_fanout_dup = int(policy.get("tailFanoutDup", 2))
    # v3.10 长尾竞速（用户需求 2026-08-31）：v3.7 的 fan-out 只在「pending 还有排队任务」
    # 时复制。末尾任务一旦被某 worker pop 出队、独占 in-flight（长 RPC 挂起），其他空闲
    # 执行槽因 src=None 退化为干等 → 整轮被一个慢副本拖住。本机制：排队队列已空时，
    # **只要空槽**就复制一个 in-flight 任务竞速（每任务副本数上限 = tailFanoutDup，
    # 不按时间阈值等待——用户裁定"有空槽就派发"）。
    # v3.9 动态节点发现（用户需求 2026-08-27）：跑批中途上线的 agent 也能贡献算力。
    # rescan 线程周期 ping 配置里未在跑的节点，合格即权重下发 + 孵化新 worker 线程
    # （共享 pending 队列），无需重启整轮。0 = 关闭。
    rescan_sec = float(policy.get("agentRescanSec", 120))

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
                    if (
                        r.get("ok")
                        and isinstance(r.get("elapsedSec"), (int, float))
                        and isinstance(r.get("node"), str)
                    ):
                        hist.setdefault(r["node"], []).append(float(r["elapsedSec"]))
        except OSError:
            pass
        return {k: sum(v[-20:]) / len(v[-20:]) for k, v in hist.items() if v}

    speed = _seed_speeds() if tail_dispatch else {}
    if speed:
        preview = ", ".join(f"{k}={v:.0f}s" for k, v in sorted(speed.items(), key=lambda x: x[1]))
        log(f"[dist] tail-dispatch speeds (seeded): {preview}")
    tail_notes: set[str] = set()
    last_progress = [time.time()]
    # v3.7 fan-out：尾部任务 -> 当前在跑副本数（>1 = 已被重复派发竞速）。
    inflight: dict[tuple[int, int], int] = {}

    def _fast_enough(nid: str, pending_len: int) -> bool:
        # R6：本机直跑槽位豁免速度持留——它由 local_slots 上限 + local_suspend
        # 让位语义自治理；再叠加 EWMA 持留会把 local 彻底饿死（实测 local=0）。
        if nid == "local":
            return True
        if not tail_dispatch or not speed or pending_len <= 0:
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

    def worker(nd: dict | None) -> None:
        suspended = False  # 本迭代持锁前先置初值（nd 非 None 时不赋值，v3.10 else 分支引用）
        while (
            not all_settled.is_set()
            and not (halt_event is not None and halt_event.is_set())
            and time.time() < deadline
        ):
            nd_id = nd["id"] if nd else "local"
            task = None
            attempt = 0
            took_local = False
            fanout_copy = False
            drained = False  # 本次取任务后派发队列是否清空（回调在锁外做，避免持锁派 eval）
            with lock:
                if nd is not None and streaks.get(nd_id, 0) >= fail_streak_max:
                    return
                if nd is None:
                    # 远端失联 → 本机并发恢复满额（防流式 PPO 被饿死）
                    if (
                        time.time() - last_remote_ok[0] > remote_dead_sec
                        and local_cap[0] != cap_full
                    ):
                        local_cap[0] = cap_full
                        log(
                            f"[dist] no remote settle for {remote_dead_sec:.0f}s — "
                            f"local slots {local_slots} -> {cap_full}"
                        )
                    # R6：PPO 波次启动后本机让位训练（local_suspend 置位）；
                    # 集群停摆豁免——远端失联超阈值时让位自动失效，采集不饿死。
                    suspended = (
                        local_suspend is not None
                        and local_suspend.is_set()
                        and time.time() - last_remote_ok[0] <= remote_dead_sec
                    )
                    if not suspended and local_active[0] < local_cap[0]:
                        took_local = True
                    # 让位即交还保留段：任务并回主队列由远端消化（防饿死）
                    if suspended and head_tasks:
                        pending.extend(head_tasks)
                        head_tasks.clear()
                src: deque | None = None
                if nd is not None:
                    if pending:
                        src = pending
                elif took_local:
                    src = head_tasks or pending or None
                if src is not None:
                    probe = len(head_tasks) if src is head_tasks else len(pending)
                    if _fast_enough(nd_id, probe):
                        fanout_copy = False
                        # v3.7 尾部 fan-out：pending 剩 ≤ tail_fanout_n 且存在在跑的尾部任务时，
                        # 空闲执行槽复制一个在跑任务（重复派发），与主副本竞速取先返回。
                        # 复制不 pop pending（主副本完成前任务保持"未完成"状态）。
                        if src is pending and len(pending) <= tail_fanout_n and inflight:
                            cand = next(
                                (t for t, c in inflight.items() if c < tail_fanout_dup), None
                            )
                            if cand is not None:
                                task = cand
                                inflight[task] += 1
                                fanout_copy = True
                                attempt = attempts.get(task, 0) + 1
                        if not fanout_copy:
                            task = src.popleft()
                            attempts[task] = attempts.get(task, 0) + 1
                            attempt = attempts[task]
                            if src is pending and len(pending) <= tail_fanout_n:
                                inflight[task] = inflight.get(task, 0) + 1
                        if nd is None:
                            local_active[0] += 1
                        if not head_tasks and not pending:
                            drained = True
                    elif f"{nd_id}:hold" not in tail_notes:
                        tail_notes.add(f"{nd_id}:hold")
                        log(
                            f"[dist] tail-mode: holding {nd_id} "
                            f"(ewma={speed.get(nd_id, -1):.0f}s, pending={probe})"
                        )
                        task = None
                else:
                    # v3.10 长尾竞速（用户需求 2026-08-31）：排队队列已空，**只要空槽**就
                    # 复制一个 in-flight 任务竞速（不看任务已耗时；用户裁定"有空槽就派发"）。
                    # 每任务副本数上限 tailFanoutDup 防无限复制；副本失败静默、成功到 seen
                    # 则丢弃（v3.7 既有 fan-out 语义）。本机槽被 local_suspend 让位时不竞速
                    # （让位语义 = 给 PPO 腾核，不抢尾流）。
                    # v3.11 竞速派档（用户 2026-08-31 观察"副本落到慢节点=白等"）：竞速名额
                    # 只给 top-3 快节点（EWMA 耗时，speed 表）——慢节点不浪费竞速副本。
                    if (
                        not (nd is None and suspended)
                        and inflight
                        and race_tier_ok(speed, nd_id, 3)
                    ):
                        tail_cand = pick_tail_race(inflight, tail_fanout_dup)
                        if tail_cand is not None:
                            task = tail_cand
                            inflight[task] += 1
                            fanout_copy = True
                            attempt = attempts.get(task, 0) + 1
                            log(
                                f"[dist] tail-race s{task[0]}/seed{task[1]} "
                                f"(inflight x{inflight[task]}) — race lane"
                            )
            if drained and on_queue_drained is not None:
                # 派发队列清空：全部采集任务已交到节点/本地线程手上、结果仍在途。
                # 干净评估此刻进场填收尾空槽（2026-08-25 用户修订，取代「权重分发完
                # 即派」——那会与采集全程抢节点）。失败回队会让队列再次非空乃至二次
                # 清空；重复触发由调用方的护栏去重。
                try:
                    on_queue_drained()
                except Exception as cb_err:
                    log(f"[dist] on_queue_drained error: {str(cb_err)[:120]}")
            if task is None:
                all_settled.wait(0.5)
                continue
            summary = None
            err = ""
            busy503 = False  # HTTP 503(busy) 瞬时负载标记——重排后背压退避
            try:
                if nd is None:
                    summary = run_local(task)
                else:
                    manifest, files = dist_common.fetch_task(
                        nd["url"],
                        nd["key"],
                        iter_id=iter_id,
                        wver=wver,
                        stage=task[0],
                        seed=task[1],
                        max_ticks=args.max_ticks,
                        difficulty=args.difficulty,
                        timeout=task_timeout,
                        kind=wkind,
                        replan=getattr(args, "replan", 0),
                        reward=getattr(args, "reward", ""),
                        dodge=getattr(args, "dodge", ""),
                    )
                    why = dist_common.validate_result(manifest, files, wver, set(norm_pairs), seen)
                    if why:
                        raise dist_common.DistError(0, why)
                    out_dir = traj_dir / "dist" / nd_id / f"rl_s{task[0]}_seed{task[1]}"
                    dist_common.write_shard(files, manifest, str(out_dir))
                    manifest["_dir"] = str(out_dir)
                    summary = manifest
            except Exception as e:
                err = str(e)[:200]
                # HTTP 503（busy）是瞬时负载不是节点故障——except 内捕获（Python 3
                # 在 except 块后删除 e，必须在块内读出标记）。
                busy503 = isinstance(e, dist_common.DistError) and e.status == 503
            with lock:
                if nd is None and task is not None:
                    local_active[0] -= 1
                if summary is not None:
                    # v3.10 去重结算（对 main / 竞速(fan-out) 副本一律适用）：
                    # v3.7 只在 fanout_copy 且 seen 时丢——漏网的后到者（主副本/竞速副本）
                    # 会重复 append → 报告 ok=3/2、seen 触顶但 all_settled 滞后 → 整轮
                    # 空等到 deadline（集成 I1 实测 0.2s → 120s）。先到者结算、后到者丢弃。
                    if task in seen:
                        if task in inflight:
                            inflight[task] = inflight.get(task, 0) - 1
                            if inflight[task] <= 0:
                                inflight.pop(task, None)
                        log(f"[dist] dup settle s{task[0]}/seed{task[1]} node={nd_id} — dropped")
                        continue
                    seen.add(task)
                    if task in inflight:
                        inflight.pop(task, None)
                    last_settle_at[0] = time.time()
                    streaks[nd_id] = 0
                    if nd is not None:
                        last_remote_ok[0] = time.time()
                    results.append(summary)
                    _record_agent_meta(
                        meta_path,
                        {
                            "node": nd_id,
                            "it": iter_no,
                            "stage": task[0],
                            "seed": task[1],
                            "ok": True,
                            "win": win_of(summary),
                            "elapsedSec": summary.get("elapsedSec"),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                    )
                    el = summary.get("elapsedSec")
                    if isinstance(el, (int, float)) and el > 0:
                        prev = speed.get(nd_id)
                        speed[nd_id] = 0.3 * float(el) + 0.7 * prev if prev else float(el)
                    last_progress[0] = time.time()
                    if on_result:
                        try:
                            on_result(summary)
                        except Exception as cb_err:
                            log(f"[dist] on_result callback error: {str(cb_err)[:120]}")
                    log(
                        f"[dist] {len(seen) + len(missing_keys)}/{n_total_tasks} settled "
                        f"node={nd_id} s{task[0]}/seed{task[1]} "
                        f"elapsed={str(el) + 's' if el is not None else '-'}"
                    )
                    if len(seen) + len(missing_keys) >= n_total_tasks:
                        all_settled.set()
                    continue
                # v3.7 fan-out 副本失败：**永不回队**（防重复执行放大/死循环）。副本是竞速
                # 用的冗余执行——输了即静默；主副本失败自会走下方正常回队/记 missing。
                # 教训（实测 2026-08-27）：初版在「主副本已 settled → inflight key 被删」时
                # 副本落入正常失败分支 → pending.append 把已结算任务重新派发 → 无限循环。
                if fanout_copy:
                    if task in inflight:
                        inflight[task] -= 1
                        if inflight[task] <= 0:
                            inflight.pop(task, None)
                    if task in seen or task in missing_keys:
                        log(
                            f"[dist] fanout copy s{task[0]}/seed{task[1]} failed ({err}) — settled, dropped"
                        )
                    else:
                        log(
                            f"[dist] fanout copy s{task[0]}/seed{task[1]} failed ({err}) — main in flight, dropped"
                        )
                    continue
                # v3.7 反向竞速：fan-out 副本抢先结算、主副本迟到被判 duplicate——
                # 主副本 fanout_copy=False，若不拦截会落入正常回队分支，把已结算任务
                # 重新派发（重复执行/潜在死循环）。任务已在 seen 即已结算，静默丢弃。
                if task in seen:
                    if task in inflight:
                        inflight[task] -= 1
                        if inflight[task] <= 0:
                            inflight.pop(task, None)
                    log(
                        f"[dist] main s{task[0]}/seed{task[1]} failed ({err}) — settled by fanout copy, dropped"
                    )
                    continue
                # 503（busy）不计熔断连击、不计重试上限（小批量突发提交防误熔断）。
                if nd is not None and not busy503:
                    streaks[nd_id] = streaks.get(nd_id, 0) + 1
                    broke = streaks[nd_id] == fail_streak_max
                else:
                    broke = False
                if attempt < MAX_TASK_ATTEMPTS or busy503:
                    pending.append(task)
                    stats["retried"] += 1
                    log(
                        f"[dist] s{task[0]}/seed{task[1]} failed ({err}) — requeued "
                        f"(attempt {attempt}/{MAX_TASK_ATTEMPTS}"
                        + (", busy" if busy503 else "")
                        + ")"
                    )
                else:
                    missing_keys.add(task)
                    _record_agent_meta(
                        meta_path,
                        {
                            "node": nd_id,
                            "it": iter_no,
                            "stage": task[0],
                            "seed": task[1],
                            "ok": False,
                            "reason": err,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                    )
                    log(
                        f"[dist] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — missing this round "
                        f"[{len(seen) + len(missing_keys)}/{n_total_tasks} settled]"
                    )
                if broke:
                    log(
                        f"[dist] node {nd_id}: {fail_streak_max} consecutive failures — "
                        f"circuit-broken for this round"
                    )
                if len(seen) + len(missing_keys) >= n_total_tasks:
                    all_settled.set()
            # 503(busy) 背压：agent 满负荷 → 本 worker 退避再领下一任务，防提交洪峰
            # （无限重排会把 attempt 刷到上百、日志洪水——实测 503 洪峰教训）。
            if busy503:
                time.sleep(min(5.0, max(0.5, deadline - time.time())))

    threads: list[threading.Thread] = []
    # 本地线程先孵化：任务队列在启动瞬间是满的，谁先起跑谁抢到——agent 线程在
    # 前的历史顺序曾让课程小轮（12 局）被远端瞬间清空、local 全程零参与。
    for _ in range(max(local_slots, cap_full)):
        threads.append(
            threading.Thread(target=worker, args=(None,), daemon=True, name="rollout-local")
        )
    for nd in alive:
        for _ in range(nd["c"]):
            threads.append(threading.Thread(target=worker, args=(nd,), daemon=True))

    # v3.9 动态节点发现：rescan 线程周期 ping 配置里未上线的节点，合格则
    # 权重下发 + 孵化新 worker 线程（与初始节点同等待遇，共享 pending 队列）。
    # strategies: 初始 alive 已是共享可变列表（后续 append），spawned_ids 防重复孵化。
    spawned_ids: set[str] = {nd["id"] for nd in alive}
    extra_threads: list[threading.Thread] = []

    def rescan_body() -> None:
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

    if rescan_sec > 0 and cfg.get("nodes"):
        scan_t = threading.Thread(target=rescan_body, daemon=True, name="rollout-rescan")
        threads.append(scan_t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(30.0, window + task_timeout))
    # rescan 中途孵化的 worker 已由 all_settled/deadline 自然收尾，这里兜底 join。
    for t in extra_threads:
        t.join(timeout=max(30.0, window + task_timeout))

    missing = sorted(k for k in all_tasks if k not in seen)
    by_node: dict[str, int] = {}
    for s in results:
        nid = str(s.get("node", "?"))
        by_node[nid] = by_node.get(nid, 0) + 1
    log(
        f"[dist] round done: ok={len(results)}/{n_total_tasks} missing={len(missing)} "
        f"retried={stats['retried']} byNode={json.dumps(by_node)}"
    )
    if missing:
        log(f"[dist] missing pairs: {[list(k) for k in missing]}")

    combined = combine_reports(
        results
        + resumed_manifests(traj_dir, wver, exclude=seen, only=plan_set, extra_wver=extra_wver)
    )
    combined["missing"] = [list(k) for k in missing]
    combined["expectedGames"] = len(pairs)
    combined["dist"] = {
        "iterId": iter_id,
        "nodes": by_node,
        "retried": stats["retried"],
        "resumed": len(done),
        # 跨配置断点轮的目录残留量（不在本轮计划、已忽略）——一次性观测
        "offPlanShards": max(0, len(done_all) - len(done)),
    }
    combined["dist_phase_sec"] = round(t_dist_done - t_queue_enter, 1)
    if halt_event is not None and halt_event.is_set():
        combined["halt_aborted"] = True
        log(
            f"[dist] KL halt active — dispatch stopped early "
            f"({len(missing)} task(s) left undispatched/unsettled)"
        )
    # 纯采集（用户定义）：最后一局结算时刻 − 权重分发完毕时刻。与 PPO 重叠无关。
    if last_settle_at[0] is not None:
        combined["pure_collect_sec"] = round(last_settle_at[0] - t_dist_done, 1)
        combined["weights_dist_done_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(t_dist_done)
        )
    return combined
