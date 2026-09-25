"""RolloutDispatcher —— 中央队列调度（2026-09-02 从 rl/queue.py 类化）。"""

from __future__ import annotations

import json
import random
import secrets
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import dist_common

# Windows：spawn 本地对局子进程（self.bun/node 跑游戏模拟）时使用 CREATE_NO_WINDOW，
# 否则每个本地槽位都会开一个黑色 cmd 控制台窗口，反复弹出抢占焦点。stdout/stderr
# 已重定向到文件，故隐藏窗口不影响日志落盘。（非 win32 平台此 dict 为空，无副作用）
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import rmtree_best_effort
from rl.log import log
from rl.queue_local import (
    pick_race_target,
    register_inflight,
    rescan_nodes,
    run_local_rollout,
    run_rollout,
)
from rl.reports import combine_reports, win_of
from rl.resume import completed_pairs, resumed_manifests, state_init_enabled

REPO_ROOT = Path(__file__).resolve().parents[2]

# 分布式采样（plan/distributed-rollout.md v3.3）：runId 使 iterId={runId}.{it} 全局唯一，
# 杜绝 relaunch 后旧 run 未取结果与新 run 任务在 agent 侧混叠。
RUN_ID = secrets.token_hex(8)

# F6（plan/dist-codehash-stale-fix.md）：dedup 静默排除的显式告警计数。RolloutDispatcher
# 每迭代新建实例，计数器必须挂模块级才跨轮累积；连续 ≥3 轮 dedup 且非 self ⇒ WARN。
# 非 dedup 结局（节点 rejoin / restart-requested / dirty-tree / restart-failed）清零。
DEDUP_STREAK: dict[str, int] = {}
MAX_TASK_ATTEMPTS = (
    3  # 单局失败回队重试上限；超限计入 missing 当轮放弃（rotate 新鲜种子自然补覆盖）
)
ROLLOUT_LOG_EVERY = 10  # 本地 rollout 每 N 局结算打一条进度行
RACE_LOG_SAMPLE = 2  # 竞速输家/dup settle 每类最多打前 N 条，其余进 round-done 汇总


def resolve_tail_join_sec(policy: dict, all_settled: bool, halted: bool) -> float:
    """收尾 join 的 grace 秒数（纯函数；2026-09-19 收紧）。

    all_settled / halt 后默认 **0**：计划对局已齐（或已熔断），在飞副本只剩竞速
    输家——结果注定被 dedup 丢弃，等待无数据价值（x20-rebirth it19：30s×3 波纯开销）。
    policy.tailGraceJoinSec 可覆写（e2e 用 2s 验有界）。
    窗口到期未齐 → tailGraceJoinSecDeadline（默认 5s）给在飞 worker 极短补结算窗；
    缺口由 volume 补波 / resume 兜底。绝不再用 queueWindow+taskTimeout（旧 2700s 洞）。
    """
    if all_settled or halted:
        return float(policy.get("tailGraceJoinSec", 0))
    return float(policy.get("tailGraceJoinSecDeadline", 5))


def _ensure_games(m: dict) -> dict:
    """将单局 agent manifest（无 games 键）转换为 combine_reports 可消费的格式。

    分布式 agent 返回的 manifest 没有 games/totalSamples/totalTicks 等聚合字段，
    而 _rl_report.json（本地 rollout）已含这些键。本函数为缺失格式补上，使
    results 与 resumed_manifests 输出同构，消除 KeyError('games')。
    """
    if "games" in m:
        return m
    score = m.get("score")
    return {
        "games": 1,
        "outcomes": {str(m.get("outcome", "unknown")): 1},
        "totalSamples": int(m.get("nSamples") or 0),
        "totalTicks": int(m.get("ticks") or 0),
        "scoreList": [score] if isinstance(score, (int, float)) else [],
        "dimLists": {},
    }


def bun_version(bun: str) -> str:
    try:
        return (
            subprocess.run(
                [bun, "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                **_POPEN_NO_WINDOW,
            ).stdout.strip()
            or "?"
        )
    except Exception:
        return "?"


def mm(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def _record_agent_meta(meta_path: Path, rec: dict) -> None:
    """追加一条节点采样元数据到 dist-agent-meta.jsonl（巡检读它聚合进 HTML）。

    rec: {node, it, stage, seed, ok, [win, elapsedSec, wallSec | reason], ts}。
    elapsedSec = 节点侧服务时长；wallSec = 训练机派发→结算墙钟（含网络）。
    放锁内调用保证顺序；单局一次 IO，成本可忽略。
    """
    try:
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


class RolloutDispatcher:
    """中央队列调度（原 run_rollout_queue 函数类化）。

    OO 封装：配置/状态收为实例属性；run() = 原主体（ping 门 → 权重下发 →
    队列/线程 → 聚合）。worker 等闭包保留在 run() 内（方法化作为后续）。
    """

    def __init__(
        self,
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
        course_fp: str | None = None,
        corpus_fp: str | None = None,
    ):
        self.bun = bun
        self.rl_path = rl_path
        self.traj_dir = traj_dir
        self.pairs = pairs
        self.args = args
        self.cfg = cfg
        self.iter_id = iter_id
        self.on_result = on_result
        self.local_slots_max = local_slots_max
        self.tail_dispatch = tail_dispatch
        self.halt_event = halt_event
        self.on_queue_drained = on_queue_drained
        self.local_suspend = local_suspend
        self.extra_wver = extra_wver
        self.course_fp = course_fp
        self.corpus_fp = corpus_fp

    def run(self) -> dict:
        # 参数局部别名（OO 化：run 主体保持原函数体裸名，指向 self 状态）
        bun = self.bun
        rl_path = self.rl_path
        traj_dir = self.traj_dir
        pairs = self.pairs
        args = self.args
        cfg = self.cfg
        iter_id = self.iter_id
        on_result = self.on_result
        local_slots_max = self.local_slots_max
        tail_dispatch = self.tail_dispatch  # noqa: F841 — API 兼容保留；2026-09-16 起不再用于 hold
        halt_event = self.halt_event
        on_queue_drained = self.on_queue_drained
        local_suspend = self.local_suspend
        extra_wver = self.extra_wver
        course_fp = self.course_fp
        corpus_fp = self.corpus_fp
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
        # F6：跨轮 dedup 计数（模块级 dict 的局部别名）
        dedup_streak = DEDUP_STREAK

        def _probe(n: dict):
            nid = str(n.get("id") or n.get("url") or "?")
            return nid, dist_common.node_ping(
                n["url"], n.get("authKey", ""), timeout=status_timeout
            )

        probe_results: list = []
        if cfg_nodes:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(8, len(cfg_nodes))) as _ex:
                probe_results = list(_ex.map(_probe, cfg_nodes))

        nodes = []
        for (nid, ping), n in zip(probe_results, cfg_nodes, strict=False):
            if ping is None:
                log(f"[dist] node {nid}: ping failed — excluded this round")
                dist_common.forget_weights_node(nid)
                continue
            if ping.get("codeHash") != code_hash:
                # codeHash 不符 ⇒ 节点可能 pull/restart 丢权重——缓存必须失效
                dist_common.forget_weights_node(nid)
                # 主动升级（guarded，2026-09-01 重启循环修复②）：分支 = 训练机当前分支
                # （dist_common.UPGRADE_BRANCH 锁存）。护栏见 dist_common.request_upgrade_
                # guarded——跨代去重（同节点同 (agent codeHash, 期望 hash) 只杀一次，
                # F1：键含期望 hash）+ 脏工作区拒发（远端 pull 永不收敛，手动更新重启
                # 的进程不再被远控杀掉）。
                # F2（plan/dist-codehash-stale-fix.md）：mismatch 分支所有日志携带两侧
                # hash 前缀——运维一眼看出差异在哪一侧（mac 事故 root-cause 前提）。
                local_short = code_hash[:8]
                remote_short = str(ping.get("codeHash") or "")[:8] or "none"
                if not upgrade_branch:
                    log(
                        f"[dist] node {nid}: codeHash mismatch "
                        f"(local={local_short} remote={remote_short}) — excluded (red)"
                    )
                    dedup_streak.pop(nid, None)
                    continue
                ok, reason = dist_common.request_upgrade_guarded(
                    nid,
                    n["url"],
                    n.get("authKey", ""),
                    upgrade_branch,
                    str(ping.get("codeHash")),
                    dirty=dirty_files,
                    expected_hash=code_hash,
                )
                if dist_common.is_self_node(n["url"], nid):
                    log(
                        f"[dist] node {nid}: self node stale "
                        f"(local={local_short} remote={remote_short}) — restart-only "
                        f"upgrade ({reason}), no git pull; excluded this round"
                    )
                elif reason == "restart-requested":
                    log(
                        f"[dist] node {nid}: requested upgrade to {upgrade_branch} "
                        f"(accepted) — will rejoin after restart"
                    )
                elif reason == "dedup":
                    # F6：连续 ≥3 轮 dedup 且非 self ⇒ 显式告警——dedup 是静默排除，
                    # mac 事故 40+ 轮无任何提示；指引用 codehash-report 双侧 diff 定位。
                    dedup_streak[nid] = dedup_streak.get(nid, 0) + 1
                    log(
                        f"[dist] node {nid}: codeHash mismatch "
                        f"(local={local_short} remote={remote_short}) — restart already "
                        f"sent for this agent codeHash (dedup) — excluded this round"
                    )
                    if not dist_common.is_self_node(n["url"], nid) and dedup_streak[nid] >= 3:
                        log(
                            f"[dist] WARN node {nid}: stale for {dedup_streak[nid]} "
                            f"rounds, upgrade suppressed by dedup （去重有"
                            f"{int(dist_common.restart_dedup_cooldown_sec())}s 冷却窗，"
                            f"窗过后会自动重发一次）— 在节点执行 "
                            f"'bun tools/agent/codehash-report.ts' 与本机 diff"
                            f"（见 plan/dist-codehash-stale-fix.md §4）"
                        )
                elif reason.startswith("dirty-tree"):
                    log(
                        f"[dist] node {nid}: codeHash mismatch "
                        f"(local={local_short} remote={remote_short}) — remote pull "
                        f"cannot converge (uncommitted training-tree changes), restart "
                        f"suppressed ({reason}) — excluded this round"
                    )
                else:
                    log(
                        f"[dist] node {nid}: codeHash mismatch "
                        f"(local={local_short} remote={remote_short}) — upgrade request "
                        f"failed ({reason}) — excluded this round"
                    )
                if reason != "dedup":
                    dedup_streak.pop(nid, None)
                continue
            dedup_streak.pop(nid, None)
            remote_full = str(ping.get("bunVersion", "?"))
            if mm(remote_full) != mm(local_bun):
                log(
                    f"[dist] node {nid}: bun {remote_full} vs local {local_bun} "
                    f"(major.minor differs) — excluded (red)"
                )
                dist_common.forget_weights_node(nid)
                continue
            if remote_full != local_bun:
                log(
                    f"[dist] node {nid}: bun patch differs ({remote_full} vs {local_bun}) — allowed (yellow)"
                )
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            log(f"[dist] node {nid}: online, concurrency={c_n}")
            nodes.append(
                {
                    "id": nid,
                    "url": n["url"],
                    "key": n.get("authKey", ""),
                    "c": c_n,
                    "ping": ping,  # M1d：能力位查询（stageJsonSupport）
                }
            )
        if not nodes:
            log("[dist] no eligible node — falling back to local-only rollout")
            return run_rollout(bun, rl_path, traj_dir, pairs, args)

        # ② 权重下发准备（边分发边开采，2026-09-19 用户口径）：
        #    rollout 耗时 = 权重就绪开始分发 → 样本齐可交 PPO。
        #    同 wver 进程缓存 reuse 跳过 POST；need 节点 POST 成功瞬间孵化采样线程。
        #    pure_collect_sec = last_settle − t_dist_start（含与采集重叠的分发墙钟）。
        if getattr(args, "goal_rollout", False):
            wkind = "goal"
        elif getattr(args, "intent_rollout", False):
            wkind = "intent"
        else:
            wkind = "rollout"
        with open(rl_path, "rb") as f:
            weights_bytes = f.read()
        reuse, need = dist_common.partition_weights_nodes(nodes, wver, kind=wkind)
        if reuse:
            log(
                f"[dist] weights[{wkind}] reuse wver={wver[:12]}… skip POST for "
                f"{[nd['id'] for nd in reuse]}"
            )
        # ping 失败/codeHash/bun 不符已在门内 forget；本列表在 worker 定义后用于
        # 边分发边开采的即时 spawn。
        # 最后一局成功结算的时刻（worker 内更新）。
        last_settle_at: list[float | None] = [None]
        # 权重分发起止（诊断 + pure_collect 新口径）。
        t_dist_start_box: list[float | None] = [None]
        t_dist_done_box: list[float | None] = [None]

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
        done_all = completed_pairs(
            traj_dir,
            wver,
            extra_wver=extra_wver,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
            state_init=state_init_enabled(args),
        )
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
                resumed_manifests(
                    traj_dir,
                    wver,
                    only=plan_set,
                    extra_wver=extra_wver,
                    course_fp=course_fp,
                    corpus_fp=corpus_fp,
                    state_init=state_init_enabled(args),
                )
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
        streaks = {nd["id"]: 0 for nd in nodes}
        # 瞬时（背压/瞬断）连续计数：与真故障分开。瞬时错误**不计**节点击败
        # streak（502 隧道/10054/超时/503 busy 都是可恢复的），但连续软失败仍有
        # 上界——否则「整个集群或隧道挂了」时会无限重排把整轮拖到窗口超时。
        # 一局成功即清零。`policy.nodeSoftFailStreak` 可覆盖（缺省 3×）。
        soft_streak_max = int(policy.get("nodeSoftFailStreak", fail_streak_max * 3))
        soft_streaks: dict[str, int] = {}
        results: list[dict] = []
        stats = {"retried": 0}
        # 竞速输家/dup settle 是 fan-out 的正常结局，逐条打会刷爆 training-loop.log
        # （实测单轮数百行）。每类只留前 RACE_LOG_SAMPLE 条作证据，其余进 round-done 汇总。
        race_drops = {
            "dup_settle": 0,
            "fanout_settled": 0,
            "fanout_inflight": 0,
            "main_by_fanout": 0,
        }
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
        # 2026-09-09 诊断：local_slots 闸门实测失效（配置 1、实测并发≈8）时的一手证据。
        # 线程孵化数 = max(local_slots, cap_full) ⇒ 闸门若失效，并发就等于这里的
        # thread_slots；与实测并发对比即可判定闸门是否真的在起作用。
        log(
            f"[dist] slots: local_slots={local_slots} (max={local_slots_max}) "
            f"cap_full={cap_full} workers={args.workers} "
            f"thread_slots={max(local_slots, cap_full)} "
            f"nodes={[(n['id'], n['c']) for n in nodes]}"
        )
        remote_dead_sec = float(policy.get("remoteDeadSecs", 150))

        # 2026-09-16 用户裁定：去掉 EWMA 快慢 hold / tail fan-out / dup 上限。
        # 分派不判节点快慢；pending 有活谁空谁接；pending 清空后空槽对**其它节点
        # 正在跑的局**做 in-flight race（同节点不派回；先返回者结算、败者丢弃）。
        # 副本数天然上界 = 节点数。it24 实锤：hold 使 self/mac 空转 16s 等 a95。
        # v3.15 分配超时重入（taskFetchTimeoutSec）与冷却黑名单保留。
        task_fetch_timeout = float(policy.get("taskFetchTimeoutSec", 30))
        _cooldown_sec = task_fetch_timeout * 4
        # v3.9 动态节点发现 + A4 轮内回场（2026-09-19 审计）：跑批中途上线的 agent、
        # 以及**被熔断/软停后恢复的节点**都能回场（0 = 关闭）。
        #  cadence：`recoverPingSec`（缺省 20s，与 B/C 层同口径）> `agentRescanSec`（旧名，
        #  显式设 0 即关闭）> 20s。首个 pass 由 `nodeRecoverFirstSec`（缺省 5s）决定——
        #  旧实现首个 pass 要等满 120s，而 volume 短波 234 轮全部 <120s ⇒ 一次都没跑过。
        rescan_sec = float(policy.get("recoverPingSec", policy.get("agentRescanSec", 20)))
        recover_first_sec = float(policy.get("nodeRecoverFirstSec", 5.0))
        rearm_cap = int(policy.get("nodeRearmLimit", 3))
        # 瞬断/背压退避上限（秒，2026-09-20 提为旋钮）：worker 撞上可刷新条件
        # （502/503/504/超时/409）后退避再领下一任务。生产缺省 5s 不变；测试把它
        # 调到 ms 级（与 nodeRecoverFirstSec/recoverPingSec 同款「小节奏」用法）——
        # 否则每个瞬断错误白等满窗：test_rollout_dispatch_resilience 的 502/soft-streak
        # 用例实测 3~5 次瞬断 ⇒ 15~26.5s/用例，是 nn 门禁墙钟的头号来源（纯 sleep，
        # 不占 CPU——即「CPU 占用低、墙钟却很长」的直接成因）。
        transient_backoff_sec = float(policy.get("transientBackoffSec", 5.0))

        # 任务 → 在跑副本数；任务 → 持有副本的节点集合（防竞速派回同节点）；
        # 任务 → 派发墙钟（超时 requeue）；任务 → 超时冷却节点集合。
        inflight: dict[tuple[int, int], int] = {}
        inflight_ts: dict[tuple[int, int], float] = {}
        inflight_nodes: dict[tuple[int, int], set[str]] = {}
        task_timeout_blocks: dict[tuple[int, int], set[str]] = {}

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
                # 本 worker 本次 attempt 的派发墙钟起点（勿用 inflight_ts[task]——竞速副本会覆盖）。
                t_task_start: float | None = None
                with lock:
                    if nd is not None and (
                        streaks.get(nd_id, 0) >= fail_streak_max
                        or soft_streaks.get(nd_id, 0) >= soft_streak_max
                    ):
                        return
                    # v3.15 分配超时重入：扫描 in-flight 找到超时任务，重新进入 pending 队列。
                    # 闪断节点（如 a96）抢走任务后实际挂起，其他节点为空闲但看不到这些任务。
                    # 超时任务的持有节点加入冷却黑名单（task_timeout_blocks），冷却期内
                    # 该节点不得再次抢到同一任务，防 a96 超时→回队→又被 a96 抢走的死循环。
                    _now = time.time()
                    _reaped = []
                    for _t, _ts in list(inflight_ts.items()):
                        if _now - _ts > task_fetch_timeout:
                            _reaped.append(_t)
                    if _reaped:
                        for _t in _reaped:
                            _blocked_nodes = inflight_nodes.pop(_t, set())
                            inflight.pop(_t, None)
                            inflight_ts.pop(_t, None)
                            pending.append(_t)
                            if _blocked_nodes:
                                task_timeout_blocks.setdefault(_t, set()).update(_blocked_nodes)
                                log(
                                    f"[dist] alloc-timeout s{_t[0]}/seed{_t[1]} "
                                    f"({task_fetch_timeout:.0f}s) — re-queued, "
                                    f"cooling nodes: {_blocked_nodes}"
                                )
                            else:
                                log(
                                    f"[dist] alloc-timeout s{_t[0]}/seed{_t[1]} "
                                    f"({task_fetch_timeout:.0f}s) — re-queued"
                                )
                    # 清理过期的冷却黑名单条目
                    _now2 = time.time()
                    _expired = []
                    for _t, _nodes in list(task_timeout_blocks.items()):
                        if _t in inflight or _t in pending:
                            continue  # 任务仍在活跃，保留冷却
                        _expired.append(_t)
                    for _t in _expired:
                        task_timeout_blocks.pop(_t, None)
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
                        # pending 有活：直接接（不判快慢、无 fan-out）。
                        # 冷却黑名单旋转：队首被本节点超时冷却则转到队尾。
                        if src is pending and task_timeout_blocks:
                            _rotated = 0
                            while pending and _rotated < len(pending):
                                _front = pending[0]
                                _blk = task_timeout_blocks.get(_front, set())
                                if nd_id in _blk:
                                    pending.rotate(-1)
                                    _rotated += 1
                                else:
                                    break
                        task = src.popleft()
                        attempts[task] = attempts.get(task, 0) + 1
                        attempt = attempts[task]
                        register_inflight(inflight, task)
                        inflight_ts[task] = time.time()
                        t_task_start = inflight_ts[task]
                        inflight_nodes.setdefault(task, set()).add(nd_id)
                        if nd is None:
                            local_active[0] += 1
                        if not head_tasks and not pending:
                            drained = True
                    else:
                        # pending 已空：空槽 in-flight race（2026-09-16）。
                        # 同节点不派回；无 dup 上限；先返回者结算。
                        # 本机槽受 local_slots 闸门 + local_suspend 让位约束。
                        _local_lane_ok = nd is not None or (
                            not suspended and local_active[0] < local_cap[0]
                        )
                        if _local_lane_ok and inflight:
                            tail_cand = pick_race_target(
                                inflight,
                                nd_id,
                                inflight_nodes,
                                task_timeout_blocks,
                            )
                            if tail_cand is not None:
                                task = tail_cand
                                inflight[task] += 1
                                inflight_ts[task] = time.time()
                                t_task_start = inflight_ts[task]
                                inflight_nodes.setdefault(task, set()).add(nd_id)
                                fanout_copy = True
                                if nd is None:
                                    local_active[0] += 1
                                attempt = attempts.get(task, 0) + 1
                                log(
                                    f"[dist] tail-race s{task[0]}/seed{task[1]} "
                                    f"node={nd_id} (inflight x{inflight[task]}) — race lane"
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
                busy503 = False  # HTTP 503(busy) 瞬时负载（重排 + 背压退避）
                transient_err = False  # 背压/瞬断（单一判据：dist_common.is_transient_error）
                task_lost = False  # 取包丢失（节点重启/清场；判据：dist_common.is_task_lost_error）
                try:
                    if nd is None:
                        _idx = next_idx[0]
                        next_idx[0] += 1
                        summary = run_local_rollout(bun, rl_path, traj_dir, _idx, task, args, wver)
                    else:
                        # M1d：课程自定义关 stageJson / 命数星级覆盖（本地 slot 分支经
                        # cmd.py 透传；dist 分支在这里进查询参数）。
                        from rl.cmd import course_fp_for_args
                        from rl.config import args_rollout_overrides, stage_json_for_args

                        _sj = stage_json_for_args(args, task[0])
                        _ov = args_rollout_overrides(args)
                        _cfp = course_fp_for_args(args)  # D14 语料血缘
                        # 能力握手（plan §5.2 / LC §4.6）：stageJson 任务只派给
                        # ping.stageJsonSupport 节点；不支持 → 响亮失败，绝不降级。
                        if _sj and not (nd.get("ping") or {}).get("stageJsonSupport"):
                            raise dist_common.DistError(
                                0,
                                f"node {nd_id} 缺 stageJsonSupport 能力位（旧 agent）——"
                                "stageJson 任务被拒；不降级（plan §5.2 能力握手）",
                            )
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
                            stage_json=_sj or "",
                            lives_override=int(_ov["lives_override"])
                            if "lives_override" in _ov
                            else None,
                            player_level=int(_ov["player_level"])
                            if "player_level" in _ov
                            else None,
                            course_fp=_cfp,
                            # 竞速收尾洞修复（2026-09-06）：all_settled 后竞速副本必是
                            # 输家——fetch 内部轮询立即放弃，trainer 不再等慢节点把
                            # 注定丢弃的局跑完（实测拖住发布 4.5 分钟）。
                            # 2026-09-14：只对 fanout_copy 传 abandon_event。主副本
                            # 必须走 sync（不带 x-async）——此前恒传 all_settled 使
                            # fetch_task 的 use_async 恒真，sync 默认形同虚设（实测
                            # 重启后 rollout 仍 ~50s）。竞速输家仍可经 async 轮询放弃。
                            abandon_event=all_settled if fanout_copy else None,
                        )
                        why = dist_common.validate_result(
                            manifest, files, wver, set(norm_pairs), seen
                        )
                        if why:
                            raise dist_common.DistError(0, why)
                        out_dir = traj_dir / "dist" / nd_id / f"rl_s{task[0]}_seed{task[1]}"
                        dist_common.write_shard(files, manifest, str(out_dir))
                        manifest["_dir"] = str(out_dir)
                        summary = manifest
                except Exception as e:
                    err = str(e)[:200]
                    # 逐层分类（单一判据，与 B/C 层共用）：
                    # · 背压/瞬断（503 busy / 502 隧道 / 10054 / 超时）⇒ 不计节点故障；
                    # · 409 wver-not-cached ⇒ “可刷新”条件：节点侧权重文件没了（桶轮换/
                    #   agent 重启/别的客户端 churn），进程内 reuse 缓存却仍说在
                    #   ⇒ 就地重发权重 + 清缓存，同一节点继续用（2026-09-19 实测教训：
                    #   5 条 409 把 a97 当作故障熔断，7 槽位整轮闲置；同一 wver 44s
                    #   前刚收过）。两者都**不得**记节点失败 streak。
                    busy503 = isinstance(e, dist_common.DistError) and e.status == 503
                    transient_err = dist_common.is_transient_error(e)
                    if (
                        nd is not None
                        and isinstance(e, dist_common.DistError)
                        and e.status == 409
                        and dist_common.refresh_weights(
                            nd,
                            iter_id=iter_id,
                            wver=wver,
                            weights_bytes=weights_bytes,
                            timeout=min(300.0, max(60.0, task_timeout)),
                            kind=wkind,
                            err=err,
                            log=log,
                        )
                    ):
                        transient_err = True  # 已自愈：同样不计故障、不耗 attempt 配额
                    # 404「task lost on node」= 节点重启/清场把它**内存里**的结果/在飞任务
                    # 清掉了（resultCache/failedTasks/inflight 都是节点进程内状态）：既非
                    # 背压也非节点故障 ⇒ 清该节点这条腿的 reuse 账本（重启后桶也可能空了，
                    # 下次取活会重新握手）+ 立即回队、不耗 attempt 配额。
                    # 2026-09-19 审计 F1：旧实现把它当确定性失败记 streak ⇒ 与 409 同族，
                    # 3 条就把刚重启的节点熔断整轮。
                    task_lost = dist_common.is_task_lost_error(e)
                    if task_lost:
                        if nd is not None:
                            dist_common.forget_weights_node(nd_id, kind=wkind)
                        transient_err = True
                with lock:
                    if nd is None and task is not None:
                        # max(0, …)：任何未配对路径都不许把计数打成负数（负 = 闸门失效）。
                        local_active[0] = max(0, local_active[0] - 1)
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
                                    inflight_ts.pop(task, None)
                                    inflight_nodes.pop(task, None)
                            # 输家副本退场：竞速双方都在锁外写盘、锁内结算——后到者
                            # 的 shard 目录若不删，发布端 iter_shard_dirs 会把同一
                            # seed 的两份数据一起打进 payload.zip（重复 arcname，
                            # 训练吃哪份由解包顺序偶然决定，2026-09-06 it3-it8 实测）。
                            # 本地 _dir = wave 目录（shard 是其子目录），远程
                            # _dir = shard 目录本身——按目录名归一化到 shard 层。
                            victim = str(summary.get("_dir") or "")
                            if victim:
                                vdir = Path(victim)
                                shard_name = f"rl_s{task[0]}_seed{task[1]}"
                                if vdir.name != shard_name:
                                    vdir = vdir / shard_name
                                rmtree_best_effort(vdir, ignore_errors=True)
                            race_drops["dup_settle"] += 1
                            if race_drops["dup_settle"] <= RACE_LOG_SAMPLE:
                                log(
                                    f"[dist] dup settle s{task[0]}/seed{task[1]} node={nd_id} — dropped"
                                    + (f" (+retired {vdir})" if victim else "")
                                )
                            continue
                        seen.add(task)
                        if task in inflight:
                            inflight.pop(task, None)
                            inflight_ts.pop(task, None)
                            inflight_nodes.pop(task, None)
                        last_settle_at[0] = time.time()
                        streaks[nd_id] = 0
                        soft_streaks[nd_id] = 0
                        if nd is not None:
                            last_remote_ok[0] = time.time()
                        # byNode / dist.nodes 按**配置节点 id**（mac/self/local）记账：
                        # summary["node"] 是 agent 自报的 worker 名（bun-71535 /
                        # node-30332 之类），直接用它汇总会看不到真实节点（2026-09-09）。
                        summary["nodeId"] = nd_id
                        # 训练机侧墙钟：本 attempt 派发→结算（含网络/轮询/本地 Popen）。
                        # 不覆盖 elapsedSec（节点服务时长仍用于算力横向比）。
                        wall_sec = (
                            round(time.time() - t_task_start, 3)
                            if t_task_start is not None
                            else None
                        )
                        summary["wallSec"] = wall_sec
                        results.append(summary)
                        _record_agent_meta(
                            meta_path,
                            {
                                "node": nd_id,
                                "mode": "rollout",  # F5：console 贡献列按 mode 分桶
                                "it": iter_no,
                                "stage": task[0],
                                "seed": task[1],
                                "ok": True,
                                "win": win_of(summary),
                                "elapsedSec": summary.get("elapsedSec"),
                                "wallSec": wall_sec,
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            },
                        )
                        el = summary.get("elapsedSec")
                        if on_result:
                            try:
                                on_result(summary)
                            except Exception as cb_err:
                                log(f"[dist] on_result callback error: {str(cb_err)[:120]}")
                        n_settled = len(seen) + len(missing_keys)
                        if n_settled % ROLLOUT_LOG_EVERY == 0 or n_settled == n_total_tasks:
                            log(
                                f"[dist] {n_settled}/{n_total_tasks} settled "
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
                                inflight_ts.pop(task, None)
                                inflight_nodes.pop(task, None)
                        if task in seen or task in missing_keys:
                            race_drops["fanout_settled"] += 1
                            if race_drops["fanout_settled"] <= RACE_LOG_SAMPLE:
                                log(
                                    f"[dist] fanout copy s{task[0]}/seed{task[1]} failed ({err}) — settled, dropped"
                                )
                        else:
                            race_drops["fanout_inflight"] += 1
                            if race_drops["fanout_inflight"] <= RACE_LOG_SAMPLE:
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
                                inflight_ts.pop(task, None)
                                inflight_nodes.pop(task, None)
                        race_drops["main_by_fanout"] += 1
                        if race_drops["main_by_fanout"] <= RACE_LOG_SAMPLE:
                            log(
                                f"[dist] main s{task[0]}/seed{task[1]} failed ({err}) — settled by fanout copy, dropped"
                            )
                        continue
                    # 瞬时（503 busy / 502 / 10054 / 超时 / 409 已自愈）：不计熔断
                    # 连击、不计重试上限（小批量突发提交与满负荷集群防误熔断）。
                    hard_broke = False
                    if nd is not None and not transient_err:
                        streaks[nd_id] = streaks.get(nd_id, 0) + 1
                        hard_broke = streaks[nd_id] == fail_streak_max
                        broke = hard_broke
                    elif nd is not None:
                        # 连续软失败上界：单次瞬时错误不是故障，但“一直是瞬时错误”
                        # 就是集群/隧道真挂了——停派该节点，不把整轮拖到窗口超时
                        # （与真故障熔断区分：日志与计数都单独记）。
                        soft_streaks[nd_id] = soft_streaks.get(nd_id, 0) + 1
                        broke = soft_streaks[nd_id] >= soft_streak_max
                        if broke:
                            log(
                                f"[dist] node {nd_id}: 连续 {soft_streaks[nd_id]} 次瞬时失败"
                                f"（背压/瞬断，非节点故障）— 本轮停派"
                            )
                    else:
                        broke = False
                    if attempt < MAX_TASK_ATTEMPTS or transient_err:
                        # v3.16：失败回队前清理 inflight 登记（避免 stale 条目）
                        if task in inflight:
                            inflight[task] -= 1
                            if inflight[task] <= 0:
                                inflight.pop(task, None)
                                inflight_ts.pop(task, None)
                                inflight_nodes.pop(task, None)
                        pending.append(task)
                        stats["retried"] += 1
                        log(
                            f"[dist] s{task[0]}/seed{task[1]} failed ({err}) — requeued "
                            f"(attempt {attempt}/{MAX_TASK_ATTEMPTS}"
                            + (
                                ", busy"
                                if busy503
                                else ", 任务丢失（节点重启/清场，已回队、不计故障）"
                                if task_lost
                                else ", 瞬断/背压（不计节点故障）"
                                if transient_err
                                else ""
                            )
                            + ")"
                        )
                    else:
                        missing_keys.add(task)
                        _record_agent_meta(
                            meta_path,
                            {
                                "node": nd_id,
                                "mode": "rollout",  # F5：console 贡献列按 mode 分桶
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
                    if hard_broke:
                        log(
                            f"[dist] node {nd_id}: {fail_streak_max} consecutive failures — "
                            f"circuit-broken for this round"
                        )
                    if len(seen) + len(missing_keys) >= n_total_tasks:
                        all_settled.set()
                # 背压退避：agent 满负荷 / 隧道抖 / 瞬断 → 本 worker 退避再领下一任务，
                # 防提交洪峰（无限重排会把 attempt 刷到上百、日志洪水——实测 503 洪峰教训）。
                if transient_err:
                    _cap = transient_backoff_sec
                    # 地板 0.5s 与上限同源：旋钮配到 ms 级时地板必须跟着缩，否则
                    # 「配置小节奏、实际仍睡 0.5s」，旋钮说谎。
                    time.sleep(min(_cap, max(min(0.5, _cap), deadline - time.time())))

        threads: list[threading.Thread] = []
        extra_threads: list[threading.Thread] = []
        # alive / spawned_ids：权重就绪后才 append（边分发边开采；rescan 共享）。
        alive: list = []
        spawned_ids: set[str] = set()
        alive_lock = threading.Lock()

        def _spawn_node_workers(nd: dict) -> None:
            """单节点权重就绪后立刻孵化其采样线程（POST 成功回调 / reuse / rescan）。

            **先 start 再入列表**：join 快照可能与 append 竞态，对未 start 的线程
            join 会 RuntimeError（stream smoke：local 秒结后 push/spawn 尚未启动）。
            """
            with alive_lock:
                if nd["id"] in spawned_ids:
                    return
                spawned_ids.add(nd["id"])
                if nd not in alive:
                    alive.append(nd)
            for _ in range(nd["c"]):
                t = threading.Thread(target=worker, args=(nd,), daemon=True)
                t.start()
                threads.append(t)
                extra_threads.append(t)

        # 本地线程先孵化：本地权重已在磁盘（rl_path），不依赖远端 POST。
        t_dist_start_box[0] = time.time()
        for _ in range(max(local_slots, cap_full)):
            t = threading.Thread(
                target=worker, args=(None,), daemon=True, name="rollout-local"
            )
            t.start()
            threads.append(t)
        # reuse 节点：同 wver 已下发，立刻开采。
        for nd in reuse:
            _spawn_node_workers(nd)

        def _push_need_and_spawn() -> None:
            """need 节点并行 POST；每个成功节点立刻 spawn（边分发边开采）。"""
            try:
                if not need:
                    return
                dist_common.post_weights_parallel(
                    need,
                    iter_id,
                    wver,
                    weights_bytes,
                    timeout=min(300.0, max(60.0, task_timeout)),
                    kind=wkind,
                    log=log,
                    on_alive=_spawn_node_workers,
                )
            except Exception as e:
                # Fake/真节点在采集结束时被关掉会 URLError；不拖垮 push 线程。
                log(f"[dist] weights-push aborted ({e}) — 未成功 POST 的节点本轮不采样")
            finally:
                t_dist_done_box[0] = time.time()

        if need:
            push_t = threading.Thread(
                target=_push_need_and_spawn, daemon=True, name="weights-push"
            )
            push_t.start()
            threads.append(push_t)
        else:
            t_dist_done_box[0] = t_dist_start_box[0]

        # v3.9 动态节点发现：rescan 线程周期 ping 配置里未上线的节点，合格则
        # 权重下发 + 孵化新 worker 线程（与初始节点同等待遇，共享 pending 队列）。
        if rescan_sec > 0 and cfg.get("nodes"):
            scan_t = threading.Thread(
                target=rescan_nodes,
                # **全部关键字传参**：本调用有 20+ 实参，历史上第 19 个位置参数错位
                # 过（线程启动即抛，运行中上线的节点永远不被发现——见函数 docstring）。
                kwargs=dict(
                    cfg=cfg,
                    code_hash=code_hash,
                    upgrade_branch=upgrade_branch,
                    dirty_files=dirty_files,
                    local_bun=local_bun,
                    spawned_ids=spawned_ids,
                    alive=alive,
                    lock=lock,
                    weights_bytes=weights_bytes,
                    iter_id=iter_id,
                    wver=wver,
                    task_timeout=task_timeout,
                    status_timeout=status_timeout,
                    all_settled=all_settled,
                    deadline=deadline,
                    rescan_sec=rescan_sec,
                    worker=worker,
                    extra_threads=extra_threads,
                    # v3.14b：halt 感知——熔断后 rescan 立即退出，主 join 不再白等超时
                    halt_event=halt_event,
                    # A4：首个 pass 提前 / 权重 kind / 回场重置失败计数所需的状态。
                    first_probe_sec=recover_first_sec,
                    wkind=wkind,
                    streaks=streaks,
                    soft_streaks=soft_streaks,
                    fail_streak_max=fail_streak_max,
                    soft_streak_max=soft_streak_max,
                    rearm_cap=rearm_cap,
                ),
                daemon=True,
                name="rollout-rescan",
            )
            threads.append(scan_t)
            scan_t.start()

        # v3.17 收尾兜底（2026-09-06，竞速收尾洞②）：旧实现对每个线程
        # join(window + task_timeout) = 2700s —— 只要有一个 worker 卡在**不可中断**
        # 的 HTTP 调用（同步 agent 的 200 分支、提交阶段挂起），整轮就空等到满超时，
        # 哪怕 150 局早已结算完毕（p4-horizon it2 273s / it3 258s，洞内日志静默）。
        # v3.16 的 abandon_event 只覆盖 x-async 轮询阶段，盖不住这条路径，故在此兜底。
        # 语义（2026-09-19 收紧，x20-rebirth it19 复盘）：
        #   ① 先等「本轮结算完成 / halt / 窗口到期」——正常收官时立即通过；
        #   ② **all_settled / halt 后默认 0s 不再等**：计划对局已齐（或已熔断），
        #      在飞副本只剩竞速输家——返回值注定被 dedup 丢弃，等待无数据价值；
        #      30s×volume 多波是纯墙钟开销（it19 实测 3×30s）。policy 可覆写。
        #   ③ 窗口到期未齐：给在飞 worker 极短 grace（默认 5s）尝试补结算；
        #      缺口由 volume 补波 / 下轮 resume 兜底，不再用 window+taskTimeout。
        #   ④ 仍存活的线程一律放弃等待（daemon 线程随进程退出，不影响报告/落盘）。
        while not all_settled.is_set() and time.time() < deadline:
            if halt_event is not None and halt_event.is_set():
                break
            all_settled.wait(0.5)
        halted = halt_event is not None and halt_event.is_set()
        tail_join_sec = resolve_tail_join_sec(policy, all_settled.is_set(), halted)
        join_until = time.time() + max(0.0, tail_join_sec)
        seen_join: set[int] = set()
        for t in list(threads) + list(extra_threads):
            tid = id(t)
            if tid in seen_join:
                continue
            seen_join.add(tid)
            if t.ident is None and not t.is_alive():
                continue  # 尚未 start（或已从列表里被复用）——join 会 RuntimeError
            try:
                t.join(timeout=max(0.0, join_until - time.time()))
            except RuntimeError:
                pass
        stuck = [t.name for t in list(threads) + list(extra_threads) if t.is_alive()]
        if stuck:
            log(
                f"[dist] tail-join grace {tail_join_sec:.0f}s 到期：{len(stuck)} 个 worker "
                f"仍在收尾（本轮结果已齐，不再等待）: {stuck[:5]}"
            )

        missing = sorted(k for k in all_tasks if k not in seen)
        by_node: dict[str, int] = {}
        for s in results:
            # nodeId = 下发时的配置 id；缺失时回退 agent 自报名（旧结果/本地直跑）。
            nid = str(s.get("nodeId") or s.get("node") or "?")
            by_node[nid] = by_node.get(nid, 0) + 1
        log(
            f"[dist] round done: ok={len(results)}/{n_total_tasks} missing={len(missing)} "
            f"retried={stats['retried']} byNode={json.dumps(by_node)}"
        )
        race_total = sum(race_drops.values())
        if race_total:
            log(
                f"[dist] race drops (expected, sampled≤{RACE_LOG_SAMPLE}/kind): "
                + " ".join(f"{k}={v}" for k, v in race_drops.items() if v)
            )
        if missing:
            log(f"[dist] missing pairs: {[list(k) for k in missing]}")

        combined = combine_reports(
            [_ensure_games(r) for r in results]
            + resumed_manifests(
                traj_dir,
                wver,
                exclude=seen,
                only=plan_set,
                extra_wver=extra_wver,
                course_fp=course_fp,
                corpus_fp=corpus_fp,
                state_init=state_init_enabled(args),
            )
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
        combined["dist_phase_sec"] = round(
            (t_dist_done_box[0] or time.time()) - t_queue_enter, 1
        )
        if halt_event is not None and halt_event.is_set():
            combined["halt_aborted"] = True
            log(
                f"[dist] KL halt active — dispatch stopped early "
                f"({len(missing)} task(s) left undispatched/unsettled)"
            )
        # rollout 采集耗时（用户口径 2026-09-19）：权重就绪开始分发 → 样本齐可交 PPO。
        # 边分发边开采下含与采集重叠的分发墙钟（端到端，不是「纯仿真」）。
        collect_sec = dist_common.rollout_collect_sec(t_dist_start_box[0], last_settle_at[0])
        if collect_sec is not None:
            combined["pure_collect_sec"] = collect_sec
        # 数值锚点：多波 volume 由 combine_reports 做 it 级 min→max 聚合。
        if t_dist_start_box[0] is not None:
            combined["weights_dist_start_ts"] = float(t_dist_start_box[0])
            combined["weights_dist_start_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(t_dist_start_box[0])
            )
        end_ts = last_settle_at[0]
        if end_ts is None:
            end_ts = t_dist_done_box[0] or time.time()
        combined["collect_end_ts"] = float(end_ts)
        if t_dist_done_box[0] is not None:
            combined["weights_dist_done_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(t_dist_done_box[0])
            )
        return combined
