"""dispatch ③ 队列的消费者：状态、认领、执行与结算（从 dispatch.py 拆出）。"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import common.distribution
import common.runtime
import common.task_client
import common.weights_client
from common.platform_utils import rmtree_best_effort
from trainer.queue_local import (
    LocalLane,
    QueueEdgeGate,
    abort_other_tags,
    drop_attempt,
    join_targets,
    next_attempt_tag,
    note_cancel_target,
    pick_race_target,
    prune_timeout_blocks,
    reap_alloc_timeouts,
    register_inflight,
    remember_task_key,
    rescan_nodes,
    resolve_tail_join_sec,
)
from worker.reports import win_of

MAX_TASK_ATTEMPTS = 3
ROLLOUT_LOG_EVERY = 10
RACE_LOG_SAMPLE = 2


@dataclass(frozen=True)
class QueuePlan:
    """③a 的计划产物：续跑过滤后的任务与保留段宽度。"""

    norm_pairs: list[tuple[int, int]]
    plan_set: set[tuple[int, int]]
    done: set[tuple[int, int]]
    done_all: set[tuple[int, int]]
    tasks: list[tuple[int, int]]
    local_slots: int


@dataclass
class QueueState:
    """一轮队列的共享状态：配置 + 容器 + 线程账本。"""

    bun: str
    rl_path: str
    traj_dir: Path
    args: Any
    cfg: dict
    iter_id: str
    on_result: Any
    local_suspend: threading.Event | None
    extra_wver: str | None
    course_fp: str | None
    corpus_fp: str | None
    policy: dict
    task_timeout: float
    status_timeout: float
    fail_streak_max: int
    upgrade_branch: str
    wver: str
    local_bun: str
    iter_no: int
    meta_path: Path
    code_hash: str
    dirty_files: list[str]
    wkind: str
    weights_bytes: bytes
    reuse: list[dict]
    need: list[dict]
    n_total_tasks: int
    norm_pairs: list[tuple[int, int]]
    remote_dead_sec: float
    task_fetch_timeout: float
    transient_backoff_sec: float
    soft_streak_max: int
    local_slots: int
    cap_full: int
    rescan_sec: float
    recover_first_sec: float
    rearm_cap: int
    on_shard: Any
    log: Callable[..., None]
    record_agent_meta: Callable[..., Any]
    run_local_rollout: Callable[..., Any]
    pending: deque[tuple[int, int]]
    head_tasks: deque[tuple[int, int]]
    lock: threading.Lock
    seen: set[tuple[int, int]]
    attempts: dict[tuple[int, int], int]
    streaks: dict[str, int]
    soft_streaks: dict[str, int]
    results: list[dict]
    stats: dict
    race_drops: dict
    missing_keys: set[tuple[int, int]]
    all_settled: threading.Event
    deadline: float
    next_idx: list[int]
    edges: QueueEdgeGate
    local_cap: list[int]
    local_active: list[int]
    sum_game_sec: list[float]
    sum_game_sec_by_node: dict[str, float]
    last_remote_ok: list[float]
    inflight: dict[tuple[int, int], int]
    inflight_ts: dict[tuple[int, int], float]
    inflight_nodes: dict[tuple[int, int], set[str]]
    task_timeout_blocks: dict[tuple[int, int], set[str]]
    inflight_tags: dict[tuple[int, int], set[str]]
    inflight_targets: dict[tuple[int, int], dict[str, dict]]
    tag_seq: list[int]
    last_settle_at: list[float | None]
    t_dist_start_box: list[float | None]
    t_dist_done_box: list[float | None]
    all_tasks: list[tuple[int, int]]
    plan_set: set[tuple[int, int]]
    done: set[tuple[int, int]]
    done_all: set[tuple[int, int]]
    local_pool: Any
    halt_event: threading.Event | None
    local_lane: LocalLane | None = None
    halted: bool = False
    threads: list[threading.Thread] = field(default_factory=list)
    extra_threads: list[threading.Thread] = field(default_factory=list)
    alive: list = field(default_factory=list)
    spawned_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Claim:
    task: tuple[int, int] | None
    attempt: int
    task_tag: str | None
    cancel_target: dict | None
    took_local: bool
    fanout_copy: bool
    t_task_start: float | None
    queue_open: bool
    drained: bool
    suspended: bool


@dataclass(frozen=True)
class _Exec:
    summary: dict | None
    err: str
    busy503: bool
    transient_err: bool
    task_lost: bool


def _claim(q: QueueState, nd: dict | None, local_slot: int | None) -> _Claim | None:
    """持锁认领一个任务（或竞速副本）；返回 None = 本线程收工。"""
    assert q.local_lane is not None
    suspended = False
    cancel_target: dict | None = None
    nd_id = nd["id"] if nd else "local"
    task = None
    attempt = 0
    took_local = fanout_copy = False
    t_task_start: float | None = None
    task_tag: str | None = None
    with q.lock:
        if nd is not None and (
            q.streaks.get(nd_id, 0) >= q.fail_streak_max
            or q.soft_streaks.get(nd_id, 0) >= q.soft_streak_max
        ):
            return None
        # B3：远端恢复后，失联期补孵的本机线程自行退场；远端再次失联时
        # watchdog 会按 cap_full 重新补孵。local_slots 以内的线程恒保留。
        if (
            nd is None
            and local_slot is not None
            and not q.local_lane.slot_allowed(local_slot)
        ):
            return None
        # v3.15 分配超时重入：扫描 in-flight 找到超时任务，重新进入 pending 队列。
        # 闪断节点（如 a96）抢走任务后实际挂起，其他节点为空闲但看不到这些任务。
        # 超时任务的持有节点加入冷却黑名单（task_timeout_blocks），冷却期内
        # 该节点不得再次抢到同一任务，防 a96 超时→回队→又被 a96 抢走的死循环。
        q.stats["alloc_timeout_requeued"] += reap_alloc_timeouts(
            inflight=q.inflight, inflight_ts=q.inflight_ts,
            inflight_nodes=q.inflight_nodes, inflight_tags=q.inflight_tags,
            inflight_targets=q.inflight_targets,
            task_timeout_blocks=q.task_timeout_blocks, pending=q.pending,
            timeout_sec=q.task_fetch_timeout, log=q.log,
        )
        prune_timeout_blocks(q.task_timeout_blocks, q.inflight, q.pending)
        if nd is None:
            # R6：PPO 波次启动后本机让位训练（local_suspend 置位）；
            # 集群停摆豁免——远端失联超阈值时让位自动失效，采集不饿死。
            suspended = (
                q.local_suspend is not None
                and q.local_suspend.is_set()
                and time.time() - q.last_remote_ok[0] <= q.remote_dead_sec
            )
            if not suspended and q.local_active[0] < q.local_cap[0]:
                took_local = True
            # 让位即交还保留段：任务并回主队列由远端消化（防饿死）
            if suspended and q.head_tasks:
                q.pending.extend(q.head_tasks)
                q.head_tasks.clear()
        src: deque | None = None
        if nd is not None:
            if q.pending:
                src = q.pending
        elif took_local:
            src = q.head_tasks or q.pending or None
        if src is not None:
            # pending 有活：直接接（不判快慢、无 fan-out）。
            # 冷却黑名单旋转：队首被本节点超时冷却则转到队尾。
            if src is q.pending and q.task_timeout_blocks:
                _rotated = 0
                while q.pending and _rotated < len(q.pending):
                    _front = q.pending[0]
                    _blk = q.task_timeout_blocks.get(_front, set())
                    if nd_id in _blk:
                        q.pending.rotate(-1)
                        _rotated += 1
                    else:
                        break
            task = src.popleft()
            q.attempts[task] = q.attempts.get(task, 0) + 1
            attempt = q.attempts[task]
            register_inflight(q.inflight, task)
            q.inflight_ts[task] = time.time()
            t_task_start = q.inflight_ts[task]
            if nd is not None:
                task_tag = next_attempt_tag(q.iter_id, q.tag_seq)
                q.inflight_tags.setdefault(task, set()).add(task_tag)
                cancel_target = note_cancel_target(
                    q.inflight_targets, task, task_tag, nd
                )
            q.inflight_nodes.setdefault(task, set()).add(nd_id)
            if nd is None:
                q.local_active[0] += 1
        else:
            # pending 已空：空槽 in-flight race（2026-09-16）。
            # 同节点不派回；无 dup 上限；先返回者结算。
            # 本机槽受 local_slots 闸门 + local_suspend 让位约束。
            _local_lane_ok = nd is not None or (
                not suspended and q.local_active[0] < q.local_cap[0]
            )
            if _local_lane_ok and q.inflight:
                tail_cand = pick_race_target(
                    q.inflight,
                    nd_id,
                    q.inflight_nodes,
                    q.task_timeout_blocks,
                )
                if tail_cand is not None:
                    task = tail_cand
                    q.inflight[task] += 1
                    # 不刷新 inflight_ts：它是**首次派发**的墙钟锚点，
                    # 竞速副本一刷新，挂住的主副本就能永远逃过 reaper。
                    t_task_start = time.time()
                    if nd is not None:
                        task_tag = next_attempt_tag(q.iter_id, q.tag_seq)
                        q.inflight_tags.setdefault(task, set()).add(task_tag)
                        cancel_target = note_cancel_target(
                            q.inflight_targets, task, task_tag, nd
                        )
                    q.inflight_nodes.setdefault(task, set()).add(nd_id)
                    fanout_copy = True
                    if nd is None:
                        q.local_active[0] += 1
                    attempt = q.attempts.get(task, 0) + 1
                    q.log(
                        f"[dist] tail-race s{task[0]}/seed{task[1]} "
                        f"node={nd_id} (inflight x{q.inflight[task]}) — race lane"
                    )
        queue_open = bool(q.pending or q.head_tasks)
    drained = task is not None and not fanout_copy and not queue_open
    return _Claim(
        task=task,
        attempt=attempt,
        task_tag=task_tag,
        cancel_target=cancel_target,
        took_local=took_local,
        fanout_copy=fanout_copy,
        t_task_start=t_task_start,
        queue_open=queue_open,
        drained=drained,
        suspended=suspended,
    )


def _execute(q: QueueState, claim: _Claim, nd: dict | None) -> _Exec:
    """执行一次认领：本机 rollout 或远端 fetch/validate/write。"""
    task = claim.task
    assert task is not None
    nd_id = nd["id"] if nd else "local"
    task_tag = claim.task_tag
    cancel_target = claim.cancel_target
    fanout_copy = claim.fanout_copy
    summary = None
    err = ""
    busy503 = False
    transient_err = False
    task_lost = False
    try:
        if nd is not None and task_tag:
            common.distribution.set_request_tag(task_tag)
        if nd is None:
            _idx = q.next_idx[0]
            q.next_idx[0] += 1
            summary = q.run_local_rollout(
                q.bun, q.rl_path, q.traj_dir, _idx, task, q.args, q.wver, q.local_pool
            )
        else:
            # M1d：课程自定义关 stageJson / 命数星级覆盖（本地 slot 分支经
            # cmd.py 透传；dist 分支在这里进查询参数）。
            from worker.cmd import course_fp_for_args
            from worker.config import args_rollout_overrides, stage_json_for_args

            _sj = stage_json_for_args(q.args, task[0])
            _ov = args_rollout_overrides(q.args)
            _cfp = course_fp_for_args(q.args)  # D14 语料血缘
            # 节点能力握手（4 个能力位，fail-closed）拆到传输层，
            # 见 common.task_client.check_node_capabilities。
            common.task_client.check_node_capabilities(
                q.args, nd_id, nd, stage_json=_sj
            )
            manifest, files = common.task_client.fetch_task(
                common.task_client.FetchTaskSpec(
                    url=nd["url"],
                    auth_key=nd["key"],
                    iter_id=q.iter_id,
                    wver=q.wver,
                    stage=task[0],
                    seed=task[1],
                    max_ticks=q.args.max_ticks,
                    difficulty=q.args.difficulty,
                    timeout=q.task_timeout,
                    kind=q.wkind,
                    replan=getattr(q.args, "replan", 0),
                    reward=getattr(q.args, "reward", ""),
                    dodge=getattr(q.args, "dodge", ""),
                    # R2 事件 rung：与课程 decision_events 同步（缺席 = 老行为）。
                    decision_events=bool(getattr(q.args, "decision_events", False)),
                    opening_connect=bool(getattr(q.args, "opening_connect", False)),
                    # 决策周期 K：与课程 decision_k 同步（缺席/10 = 老行为）。
                    decision_k=int(getattr(q.args, "decision_k", 10)),
                    on_key=partial(remember_task_key, cancel_target),
                    # greedy 分层采集：labelMode=greedy 进 URL → agent 拼
                    # --teacher-argmax；该键已进 taskKey 与轮询配方，缓存隔离。
                    label_mode="greedy"
                    if getattr(q.args, "rollout_argmax", False)
                    else "",
                    stage_json=_sj or "",
                    lives_override=int(_ov["lives_override"])
                    if "lives_override" in _ov
                    else None,
                    player_level=int(_ov["player_level"])
                    if "player_level" in _ov
                    else None,
                    course_fp=_cfp,
                    # P2：语料格式（仅 per-tick RL rollout；eval/BC 不走这条腿）。
                    corpus_fmt=str(getattr(q.args, "corpus_fmt", "full") or "full"),
                    # 竞速收尾洞修复（2026-09-06）：all_settled 后竞速副本必是
                    # 输家——fetch 内部轮询立即放弃，trainer 不再等慢节点把
                    # 注定丢弃的局跑完（实测拖住发布 4.5 分钟）。
                    # 2026-09-14：只对 fanout_copy 传 abandon_event。主副本
                    # 必须走 sync（不带 x-async）——此前恒传 all_settled 使
                    # fetch_task 的 use_async 恒真，sync 默认形同虚设（实测
                    # 重启后 rollout 仍 ~50s）。竞速输家仍可经 async 轮询放弃。
                    abandon_event=q.all_settled if fanout_copy else None,
                )
            )
            why = common.distribution.validate_result(
                manifest, files, q.wver, set(q.norm_pairs), q.seen
            )
            if why:
                raise common.distribution.DistError(0, why)
            out_dir = q.traj_dir / "dist" / nd_id / f"rl_s{task[0]}_seed{task[1]}"
            # ★ 零落盘（2026-10-28）：有段账 ⇒ `write_shard` 一个字节都不写
            #   （打包按名拼 `presegmented`）；没有段账 ⇒ 落盘，字节不能丢。
            common.distribution.write_shard(files, manifest, str(out_dir), q.on_shard)
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
        busy503 = isinstance(e, common.distribution.DistError) and e.status == 503
        transient_err = common.distribution.is_transient_error(e)
        if (
            nd is not None
            and isinstance(e, common.distribution.DistError)
            and e.status == 409
            and common.weights_client.refresh_weights(
                nd,
                iter_id=q.iter_id,
                wver=q.wver,
                weights_bytes=q.weights_bytes,
                timeout=min(300.0, max(60.0, q.task_timeout)),
                kind=q.wkind,
                err=err,
                log=q.log,
            )
        ):
            transient_err = True  # 已自愈：同样不计故障、不耗 attempt 配额
        # 404「task lost on node」= 节点重启/清场把它**内存里**的结果/在飞任务
        # 清掉了（resultCache/failedTasks/inflight 都是节点进程内状态）：既非
        # 背压也非节点故障 ⇒ 清该节点这条腿的 reuse 账本（重启后桶也可能空了，
        # 下次取活会重新握手）+ 立即回队、不耗 attempt 配额。
        # 2026-09-19 审计 F1：旧实现把它当确定性失败记 streak ⇒ 与 409 同族，
        # 3 条就把刚重启的节点熔断整轮。
        task_lost = common.task_client.is_task_lost_error(e)
        if task_lost:
            if nd is not None:
                common.distribution.forget_weights_node(nd_id, kind=q.wkind)
            transient_err = True
    return _Exec(
        summary=summary,
        err=err,
        busy503=busy503,
        transient_err=transient_err,
        task_lost=task_lost,
    )


def _settle_success(
    q: QueueState, claim: _Claim, res: _Exec, nd: dict | None
) -> bool:
    """结算成功/重复副本（调用方持锁）；返回 True = 本 attempt 直接收工。"""
    task = claim.task
    assert task is not None
    nd_id = nd["id"] if nd else "local"
    task_tag = claim.task_tag
    fanout_copy = claim.fanout_copy
    t_task_start = claim.t_task_start
    summary = res.summary
    assert summary is not None
    if nd is None and task is not None:
        # max(0, …)：任何未配对路径都不许把计数打成负数（负 = 闸门失效）。
        q.local_active[0] = max(0, q.local_active[0] - 1)
    if summary is not None:
        # v3.10 去重结算（对 main / 竞速(fan-out) 副本一律适用）：
        # v3.7 只在 fanout_copy 且 seen 时丢——漏网的后到者（主副本/竞速副本）
        # 会重复 append → 报告 ok=3/2、seen 触顶但 all_settled 滞后 → 整轮
        # 空等到 deadline（集成 I1 实测 0.2s → 120s）。先到者结算、后到者丢弃。
        if task in q.seen:
            if task in q.inflight:
                q.inflight[task] = q.inflight.get(task, 0) - 1
                if q.inflight[task] <= 0:
                    q.inflight.pop(task, None)
                    q.inflight_ts.pop(task, None)
                    q.inflight_nodes.pop(task, None)
            # 已结算的任务不再需要任何在飞副本的算力：关掉它们的请求。
            abort_other_tags(q.inflight_tags.pop(task, set()), task_tag)
            q.inflight_targets.pop(task, None)
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
            q.race_drops["dup_settle"] += 1
            q.stats["late_original_settled"] += int(not fanout_copy)
            if q.race_drops["dup_settle"] <= RACE_LOG_SAMPLE:
                q.log(
                    f"[dist] dup settle s{task[0]}/seed{task[1]} node={nd_id} — dropped"
                    + (f" (+retired {vdir})" if victim else "")
                )
            return True
        q.seen.add(task)
        if task in q.inflight:
            q.inflight.pop(task, None)
            q.inflight_ts.pop(task, None)
            q.inflight_nodes.pop(task, None)
        # 胜者已定：其余竞速副本的结果注定被丢弃，立即断连止损。
        abort_other_tags(q.inflight_tags.pop(task, set()), task_tag)
        q.inflight_targets.pop(task, None)
        q.last_settle_at[0] = time.time()
        q.streaks[nd_id] = 0
        q.soft_streaks[nd_id] = 0
        if nd is not None:
            q.last_remote_ok[0] = time.time()
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
        q.results.append(summary)
        q.record_agent_meta(
            q.meta_path,
            {
                "node": nd_id,
                "mode": "rollout",  # F5：console 贡献列按 mode 分桶
                "it": q.iter_no,
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
        if el is not None:
            try:
                _el = float(el)
                q.sum_game_sec[0] += _el
                q.sum_game_sec_by_node[nd_id] = (
                    q.sum_game_sec_by_node.get(nd_id, 0.0) + _el
                )
            except (TypeError, ValueError):
                # 上游口径变了（非数字）⇒ 不给 E 添一个假零头；
                # 分子少算会让 E 偏低，读数是「下界」，不会误导成「很好」。
                pass
        if q.on_result:
            try:
                q.on_result(summary)
            except Exception as cb_err:
                q.log(f"[dist] on_result callback error: {str(cb_err)[:120]}")
        n_settled = len(q.seen) + len(q.missing_keys)
        if n_settled % ROLLOUT_LOG_EVERY == 0 or n_settled == q.n_total_tasks:
            q.log(
                f"[dist] {n_settled}/{q.n_total_tasks} settled "
                f"node={nd_id} s{task[0]}/seed{task[1]} "
                f"elapsed={str(el) + 's' if el is not None else '-'}"
            )
        if len(q.seen) + len(q.missing_keys) >= q.n_total_tasks:
            q.all_settled.set()
        return True


def _settle_failure(
    q: QueueState, claim: _Claim, res: _Exec, nd: dict | None
) -> bool:
    """结算失败/竞速输家（调用方持锁）；返回 True = 本 attempt 直接收工。"""
    task = claim.task
    assert task is not None
    nd_id = nd["id"] if nd else "local"
    task_tag = claim.task_tag
    fanout_copy = claim.fanout_copy
    attempt = claim.attempt
    err = res.err
    busy503 = res.busy503
    transient_err = res.transient_err
    task_lost = res.task_lost
# v3.7 fan-out 副本失败：**永不回队**（防重复执行放大/死循环）。副本是竞速
    # 用的冗余执行——输了即静默；主副本失败自会走下方正常回队/记 missing。
    # 教训（实测 2026-08-27）：初版在「主副本已 settled → inflight key 被删」时
    # 副本落入正常失败分支 → pending.append 把已结算任务重新派发 → 无限循环。
    if fanout_copy:
        if task in q.inflight:
            q.inflight[task] -= 1
            if q.inflight[task] <= 0:
                q.inflight.pop(task, None)
                q.inflight_ts.pop(task, None)
                q.inflight_nodes.pop(task, None)
        drop_attempt(q.inflight_tags, q.inflight_targets, task, task_tag)
        if task in q.seen or task in q.missing_keys:
            q.race_drops["fanout_settled"] += 1
            if q.race_drops["fanout_settled"] <= RACE_LOG_SAMPLE:
                q.log(
                    f"[dist] fanout copy s{task[0]}/seed{task[1]} failed ({err}) — settled, dropped"
                )
        elif busy503:
            # 节点按 busy **直接拒收**：这一份副本一局都没跑、零槽秒。
            # 算进 `dup=` 会让「重复执行率」虚高（503 在竞速高峰是常态）⇒ 单列。
            q.race_drops["fanout_rejected"] += 1
            if q.race_drops["fanout_rejected"] <= RACE_LOG_SAMPLE:
                q.log(
                    f"[dist] fanout copy s{task[0]}/seed{task[1]} rejected (busy)"
                    " — never ran, not a dup"
                )
        else:
            q.race_drops["fanout_inflight"] += 1
            if q.race_drops["fanout_inflight"] <= RACE_LOG_SAMPLE:
                q.log(
                    f"[dist] fanout copy s{task[0]}/seed{task[1]} failed ({err}) — main in flight, dropped"
                )
        return True
    # v3.7 反向竞速：fan-out 副本抢先结算、主副本迟到被判 duplicate——
    # 主副本 fanout_copy=False，若不拦截会落入正常回队分支，把已结算任务
    # 重新派发（重复执行/潜在死循环）。任务已在 seen 即已结算，静默丢弃。
    if task in q.seen:
        if task in q.inflight:
            q.inflight[task] -= 1
            if q.inflight[task] <= 0:
                q.inflight.pop(task, None)
                q.inflight_ts.pop(task, None)
                q.inflight_nodes.pop(task, None)
        abort_other_tags(q.inflight_tags.pop(task, set()), task_tag)
        q.inflight_targets.pop(task, None)
        q.race_drops["main_by_fanout"] += 1
        if q.race_drops["main_by_fanout"] <= RACE_LOG_SAMPLE:
            q.log(
                f"[dist] main s{task[0]}/seed{task[1]} failed ({err}) — settled by fanout copy, dropped"
            )
        return True
    # 瞬时（503 busy / 502 / 10054 / 超时 / 409 已自愈）：不计熔断
    # 连击、不计重试上限（小批量突发提交与满负荷集群防误熔断）。
    hard_broke = False
    if nd is not None and not transient_err:
        q.streaks[nd_id] = q.streaks.get(nd_id, 0) + 1
        hard_broke = q.streaks[nd_id] == q.fail_streak_max
        broke = hard_broke
    elif nd is not None:
        # 连续软失败上界：单次瞬时错误不是故障，但“一直是瞬时错误”
        # 就是集群/隧道真挂了——停派该节点，不把整轮拖到窗口超时
        # （与真故障熔断区分：日志与计数都单独记）。
        q.soft_streaks[nd_id] = q.soft_streaks.get(nd_id, 0) + 1
        broke = q.soft_streaks[nd_id] >= q.soft_streak_max
        if broke:
            q.log(
                f"[dist] node {nd_id}: 连续 {q.soft_streaks[nd_id]} 次瞬时失败"
                f"（背压/瞬断，非节点故障）— 本轮停派"
            )
    else:
        broke = False
    if attempt < MAX_TASK_ATTEMPTS or transient_err:
        # v3.16：失败回队前清理 inflight 登记（避免 stale 条目）
        if task in q.inflight:
            q.inflight[task] -= 1
            if q.inflight[task] <= 0:
                q.inflight.pop(task, None)
                q.inflight_ts.pop(task, None)
                q.inflight_nodes.pop(task, None)
        # 只摘自己的 tag：同任务的竞速副本可能还在飞，不能一起取消。
        drop_attempt(q.inflight_tags, q.inflight_targets, task, task_tag)
        q.pending.append(task)
        q.stats["retried"] += 1
        q.log(
            f"[dist] s{task[0]}/seed{task[1]} node={nd_id or 'local'} failed ({err}) — requeued "
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
        q.missing_keys.add(task)
        q.inflight.pop(task, None)
        q.inflight_ts.pop(task, None)
        q.inflight_nodes.pop(task, None)
        q.inflight_tags.pop(task, None)
        q.inflight_targets.pop(task, None)
        q.record_agent_meta(
            q.meta_path,
            {
                "node": nd_id,
                "mode": "rollout",  # F5：console 贡献列按 mode 分桶
                "it": q.iter_no,
                "stage": task[0],
                "seed": task[1],
                "ok": False,
                "reason": err,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        q.log(
            f"[dist] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — missing this round "
            f"[{len(q.seen) + len(q.missing_keys)}/{q.n_total_tasks} settled]"
        )
    if hard_broke:
        q.log(
            f"[dist] node {nd_id}: {q.fail_streak_max} consecutive failures — "
            f"circuit-broken for this round"
        )
    if len(q.seen) + len(q.missing_keys) >= q.n_total_tasks:
        q.all_settled.set()
    return False


def _settle_attempt(q: QueueState, claim: _Claim, res: _Exec, nd: dict | None) -> bool:
    """结算入口：成功/失败两分支在同一把锁内。返回 True = 直接 continue。"""
    with q.lock:
        if nd is None and claim.task is not None:
            q.local_active[0] = max(0, q.local_active[0] - 1)
        if res.summary is not None:
            return _settle_success(q, claim, res, nd)
        return _settle_failure(q, claim, res, nd)


def queue_worker(q: QueueState, nd: dict | None, local_slot: int | None = None) -> None:
    """一个消费者线程：认领 → 执行 → 结算 → 背压退避。"""
    assert q.local_lane is not None
    while (
        not q.all_settled.is_set()
        and not (q.halt_event is not None and q.halt_event.is_set())
        and time.time() < q.deadline
    ):
        claim = _claim(q, nd, local_slot)
        if claim is None:
            return
        q.edges.seen(drained=claim.drained, queue_open=claim.queue_open)
        if claim.task is None:
            _idle_t0 = time.time()
            q.all_settled.wait(0.5)
            q.local_lane.note_idle(nd is None, claim.took_local, claim.suspended, _idle_t0)
            continue
        res = _execute(q, claim, nd)
        if claim.task_tag:
            common.distribution.clear_abort(claim.task_tag)
            common.distribution.set_request_tag("")
        if _settle_attempt(q, claim, res, nd):
            continue
        if res.transient_err:
            _cap = q.transient_backoff_sec
            time.sleep(min(_cap, max(min(0.5, _cap), q.deadline - time.time())))


def run_queue(q: QueueState) -> None:
    """③b 消费者编排：本机/远端/推送/rescan 线程孵化与收尾 join。"""
    worker = partial(queue_worker, q)
    q.threads = []
    q.extra_threads = []
    #: 本轮的采样/下发/rescan 线程都归它（名字统一 `queue-*`，`describe()` 里可读）。
    #: 建在 `run_queue` 里而不是模块级：一轮一份，收尾即散，不制造跨轮的单例。
    rt = common.runtime.Runtime(scope="queue")


    # alive / spawned_ids：权重就绪后才 append（边分发边开采；rescan 共享）。
    q.alive = []
    q.spawned_ids = set()
    alive_lock = threading.Lock()

    def _spawn_node_workers(nd: dict) -> None:
        """单节点权重就绪后立刻孵化其采样线程（POST 成功回调 / reuse / rescan）。

        **先 start 再入列表**：join 快照可能与 append 竞态，对未 start 的线程
        join 会 RuntimeError（stream smoke：local 秒结后 push/spawn 尚未启动）。
        """
        with alive_lock:
            if nd["id"] in q.spawned_ids:
                return
            q.spawned_ids.add(nd["id"])
            if nd not in q.alive:
                q.alive.append(nd)
        for _ in range(nd["c"]):
            t = rt.thread(worker, nd, name=f"worker:{nd.get('id')}", daemon=True)
            q.threads.append(t)
            q.extra_threads.append(t)

    # 本地线程先孵化：本地权重已在磁盘（rl_path），不依赖远端 POST。
    # B3：初始只开 local_slots 条（0 = 不开）；失联补孵由 LocalLane 守护。
    q.local_lane = LocalLane(
        worker,
        local_slots=q.local_slots,
        cap_full=q.cap_full,
        local_cap=q.local_cap,
        last_remote_ok=q.last_remote_ok,
        remote_dead_sec=q.remote_dead_sec,
        all_settled=q.all_settled,
        halt_event=q.halt_event,
        deadline=q.deadline,
        lock=q.lock,
        threads=q.threads,
    )
    q.t_dist_start_box[0] = time.time()
    q.local_lane.start()

    # reuse 节点：同 wver 已下发，立刻开采。
    for nd in q.reuse:
        _spawn_node_workers(nd)

    def _push_need_and_spawn() -> None:
        """need 节点并行 POST；每个成功节点立刻 spawn（边分发边开采）。"""
        try:
            if not q.need:
                return
            common.weights_client.post_weights_parallel(
                q.need,
                q.iter_id,
                q.wver,
                q.weights_bytes,
                timeout=min(300.0, max(60.0, q.task_timeout)),
                kind=q.wkind,
                hooks=common.weights_client.PushHooks(
                    log=q.log, on_alive=_spawn_node_workers
                ),
            )
        except Exception as e:
            # Fake/真节点在采集结束时被关掉会 URLError；不拖垮 push 线程。
            q.log(f"[dist] weights-push aborted ({e}) — 未成功 POST 的节点本轮不采样")
        finally:
            q.t_dist_done_box[0] = time.time()

    if q.need:
        push_t = rt.thread(_push_need_and_spawn, name="weights-push", daemon=True)
        q.threads.append(push_t)
    else:
        q.t_dist_done_box[0] = q.t_dist_start_box[0]

    # v3.9 动态节点发现：rescan 线程周期 ping 配置里未上线的节点，合格则
    # 权重下发 + 孵化新 worker 线程（与初始节点同等待遇，共享 pending 队列）。
    if q.rescan_sec > 0 and q.cfg.get("nodes"):
        scan_t = rt.thread(
            rescan_nodes,
            # **全部关键字传参**：本调用有 20+ 实参，历史上第 19 个位置参数错位
            # 过（线程启动即抛，运行中上线的节点永远不被发现——见函数 docstring）。
            kwargs=dict(
                cfg=q.cfg,
                code_hash=q.code_hash,
                upgrade_branch=q.upgrade_branch,
                dirty_files=q.dirty_files,
                local_bun=q.local_bun,
                spawned_ids=q.spawned_ids,
                alive=q.alive,
                lock=q.lock,
                weights_bytes=q.weights_bytes,
                iter_id=q.iter_id,
                wver=q.wver,
                task_timeout=q.task_timeout,
                status_timeout=q.status_timeout,
                all_settled=q.all_settled,
                deadline=q.deadline,
                rescan_sec=q.rescan_sec,
                worker=worker,
                extra_threads=q.extra_threads,
                # v3.14b：halt 感知——熔断后 rescan 立即退出，主 join 不再白等超时
                halt_event=q.halt_event,
                # A4：首个 pass 提前 / 权重 kind / 回场重置失败计数所需的状态。
                first_probe_sec=q.recover_first_sec,
                wkind=q.wkind,
                streaks=q.streaks,
                soft_streaks=q.soft_streaks,
                fail_streak_max=q.fail_streak_max,
                soft_streak_max=q.soft_streak_max,
                rearm_cap=q.rearm_cap,
            ),
            daemon=True,
            name="rollout-rescan",
        )
        q.threads.append(scan_t)

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
    while not q.all_settled.is_set() and time.time() < q.deadline:
        if q.halt_event is not None and q.halt_event.is_set():
            break
        # 步长**钳到 deadline**（与 `queue_local` 的 rescan 循环同源）：裸 `wait(0.5)`
        # 会让「窗口到期」实际晚到最多 0.5s（退出条件在下一轮才被检查）。生产窗
        # 1800s 无所谓，但窗口就是小窗用例的全部墙钟（实测 `test_soft_streak…`：
        # 窗 0.6s / 工时 0.17s，却花 1.04s = 窗 + 一个 0.5 步长的过冲）。
        # 2026-09-29（§43）：钳住后退出时刻 = deadline，与窗口语义逐字对齐。
        q.all_settled.wait(min(0.5, max(0.0, q.deadline - time.time())))
    halted = q.halt_event is not None and q.halt_event.is_set()
    # 结算口径要读它（`missing` 的「池语义只算派过的」分支、halt_kind 的 quota/external）：
    # 旧实现里 `halted` 是调度器循环的局部量，搬进 `QueueState` 后必须写回，否则恒 False。
    q.halted = q.halted or halted
    tail_join_sec = resolve_tail_join_sec(q.policy, q.all_settled.is_set(), halted)
    join_until = time.time() + max(0.0, tail_join_sec)
    for t in join_targets(q.threads, q.extra_threads):
        if t.ident is None and not t.is_alive():
            continue  # 尚未 start（或已从列表里被复用）——join 会 RuntimeError
        try:
            t.join(timeout=max(0.0, join_until - time.time()))
        except RuntimeError:
            pass
    # 两张表**有交集**（节点 worker 同时进两表、rescan 只进 extra_threads）：
    # 去重前相加会把在飞 worker 报成近 2×（现场 89，实际约一半）。
    stuck = [t.name for t in join_targets(q.threads, q.extra_threads) if t.is_alive()]
    if stuck:
        q.log(
            f"[dist] tail-join grace {tail_join_sec:.0f}s 到期：{len(stuck)} 个 worker "
            f"仍在收尾（本轮结果已齐，不再等待）: {stuck[:5]}"
        )



