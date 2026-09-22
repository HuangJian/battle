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
from typing import Any

import dist_common

# 同 queue.py：Windows 下隐藏本地评估子进程的控制台窗口（避免反复弹黑窗抢焦点）。
from rl.eval_local import (
    BASELINE_EVAL_ITER,
    EVAL_INFLIGHT_GRACE_SEC,
    EVAL_ITER_SUFFIX,
    EVAL_LOCAL_RELEASE_GRACE,
    EVAL_LOCAL_SLOTS_DEFAULT,
    EVAL_TASK_ATTEMPTS,
    a_eval_seed_list,
    eval_done_keys,
    eval_row,
    hold_for_local,
    release_local_gate_if_starved,
    report_winrate_safe,  # noqa: F401 — re-exported（旧模块成员，兼容外部引用）
    run_local_eval_game,
    settle_eval_summary,
    should_dual_track,
)
from rl.log import log
from rl.queue import _record_agent_meta, bun_version, mm
from rl.queue_local import pick_race_target, register_inflight

#: 干净评估的权重 kind（B6，2026-09-19）：节点按 (kind, wver) 分桶缓存权重
#: （sampler-agent `weightsByKindSha`）。旧实现与训练 rollout 共用 'rollout' 桶 ⇒ 训练每轮
#: 刷权重与 eval 那份在同桶内互相驱逐/清场，eval 局随即 409「wver not cached here」
#: （2026-09-19 审计 A1/B6；客户端只能靠 409 自愈重发兜底）。独立 kind 后两条腿互不驱逐，
#: 且日志与落盘文件（`weights-eval-<sha16>.json`）一眼可分。
#:
#: 协议侧 kind 是不透明字符串（POST x-kind / GET X-Kind / task ?kind= 三处同源）⇒
#: 旧节点无需任何改动，本改动不触 codeHash（nn-training/** 不在 SSOT 内）。
EVAL_WEIGHTS_KIND = "eval"


def select_delayed_eval_it(dispatch_it: int, is_eval_round) -> int | None:
    """延迟 eval 派发轮选择（P0 修复：in-loop eval 曾恒取 W(N-1) 却标 itN）。

    第 dispatch_it 轮采集收官后，可评估的最新已完成权重是 W(dispatch_it-1)
    （本轮 PPO 尚未跑）。返回应评估的权重轮 M，无则 None。
    M 从 1 起：W(0)=init 权重由 it0 基线流覆盖，A-eval 不重复。
    纯函数（可单测）；调用方（loop）负责实际派发与对账。
    """
    m = dispatch_it - 1
    if m < 1:
        return None
    return m if is_eval_round(m) else None


def find_archive_weights(backup_dir: str, backup_prefix: str, it: int) -> str | None:
    """归档目录里定位 itN 权重（`{prefix}.it{N}.*.json`，取最新）。

    P0 修复：延迟派发读不可变归档而非活指针——wver 与离线复跑同源，
    且天然免疫 PPO 落盘竞态与断点续跑后的指针前移。缺席返回 None
    （归档失败是非致命的；调用方回落活指针 + 响亮日志）。
    """
    if not backup_dir or not backup_prefix:
        return None
    _repo_root: Any = None
    try:
        from rl.archive import REPO_ROOT

        _repo_root = REPO_ROOT
    except Exception:
        pass
    import os

    bdir = backup_dir
    if _repo_root is not None and not os.path.isabs(bdir):
        bdir = str(_repo_root / bdir)
    try:
        cands = sorted(
            Path(bdir).glob(f"{backup_prefix}.it{it}.*.json"),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return None
    return str(cands[-1]) if cands else None


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
        baseline: bool = False,
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
        #: it0 基线模式：`rl_path` 是课程 bc 权重、`it` 是 0；已评估账本按 iter 去重
        #: （见 eval_done_keys 的 iter_filter），不与 A-eval 共用 wver 键发生互吞。
        self.baseline = baseline

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
        baseline = self.baseline
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
            # 双轨日常评估（plan/dual-track-eval-seeds）：A-eval 且 n_seeds==50 时
            # 锚点 50 + 轮转 50（总量翻倍）；it0 基线与更大正式前缀（100/200）保持
            # EVAL_SEEDS[:n_seeds] 逐字节兼容。小 n_seeds（冒烟/单测）仍走前缀切片。
            seed_list = a_eval_seed_list(it, n_seeds, baseline=baseline)
            pairs = [(s, sd) for s in eval_stages for sd in seed_list]
            if not pairs:
                return
            # it0 基线：账本按 iter 隔离（baseline 行的 iter 恒 0），否则同指纹的
            # A-eval 行会把基线局算成"已评估"——it0 行永远不落盘、控制台退回旧基准。
            # A-eval 反向同理：min_iter=1 把 it0 基线行挡在 A-eval 去重外（bc 与
            # it1 的 args.out 指纹偶同时，it1 的 A-eval 不得被 it0 行整轮跳过）。
            done = (
                eval_done_keys(eval_jsonl, key16, BASELINE_EVAL_ITER)
                if baseline
                else eval_done_keys(eval_jsonl, key16, min_iter=1)
            )
            todo = [p for p in pairs if p not in done]
            if not todo:
                log(
                    f"[eval] it{it}: wver={key16[:12]}… already evaluated — skip"
                    + ("（it0 基线）" if baseline else "")
                )
                return
            t_eval_start = time.time()

            # 本地参与前提：冻结权重快照。主循环在 PPO 收尾后会原地覆盖 rl_path，
            # 本地局必须读派发时刻的 W(N)——赌时序读新权重 = 对账灾难。
            # 快照文件按评估流分流：it0 基线与 A-eval 可并发（基线落账前的重试轮
            # 撞上 eval 轮），同盘同名会互相覆写、后启动的本地局读到错权重。
            snapshot_path: str | None = None
            local_slots = max(0, int(policy.get("evalLocalSlots", EVAL_LOCAL_SLOTS_DEFAULT)))
            if local_gate is not None and local_slots > 0:
                try:
                    traj_dir.mkdir(parents=True, exist_ok=True)
                    snap = traj_dir / (
                        "_eval_frozen_weights-baseline.json"
                        if baseline
                        else "_eval_frozen_weights.json"
                    )
                    shutil.copyfile(rl_path, snap)
                    snapshot_path = str(snap)
                except OSError as e:
                    log(f"[eval] WARN weights snapshot failed — local participation off: {e}")

            total = len(todo)
            # 尾段预留量：gate 接线且本地可用时，节点不取最后 reserved 局（留给本机直跑）
            reserved = (
                min(local_slots, total)
                if (snapshot_path is not None and local_gate is not None and local_slots > 0)
                else 0
            )
            pending: deque[tuple[int, int]] = deque(todo)
            lock = threading.Lock()
            seen: set[tuple[int, int]] = set()
            attempts: dict[tuple[int, int], int] = {}
            # 按需建键（节点集在本块之后才定向——并行 ping 的门在闭包之后，见下）；
            # 读取一律 .get(nid, 0)，写入才建键。
            streaks: dict[str, int] = {}
            # 瞬时（背压/瞬断）连续计数：与真故障分开（与 A 层 dispatch.py 同款）。
            # 瞬时错误不计节点失败 streak，但连续软失败有上界——否则隧道/集群
            # 整体脉搏时会一直空转到窗口到期。任一一局结算即清零。
            soft_streak_max = int(policy.get("nodeSoftFailStreak", fail_streak_max * 3))
            soft_streaks: dict[str, int] = {}
            wins = [0]
            cleared_total = [0]
            outcomes: dict[str, int] = {}
            node_games: dict[str, int] = {}  # 每节点实际结算的评估局数（summary 用）
            jsonl_lock = threading.Lock()
            # 2026-09-16：eval 与 rollout 同款 in-flight race——pending 清空后空槽
            # 复制其它节点在跑的尾局，先返回者记账、败者丢弃（不双计）。
            inflight: dict[tuple[int, int], int] = {}
            inflight_nodes: dict[tuple[int, int], set[str]] = {}
            all_done = threading.Event()
            # 收工即断连的作用域（线程 tag）：settled 满即置位 + 关在飞连接——尾部
            # 竞速副本/慢节点不再等（同 A/B 层）。同进程 rollout 的标签不同，不误伤。
            req_scope = f"evalscope:{eval_iter_id}"
            dist_common.clear_abort()
            #: 正在写行的赢家数（record 期间 +1）：收工只等它们落盘。
            writers = [0]
            #: 消费线程数（孵化即 +1，线程退出 -1）。全退 = 再等也不会结算 ⇒ 收工
            #: （否则「任务全被 drop + 无在飞」时只能空等整个窗口，2026-09-19）。
            live_workers = [0]

            def _settle_complete() -> None:
                """settled 满：置收工位 + 断连（调用方持 lock；实现非阻塞）。"""
                all_done.set()
                dist_common.abort_active_requests(req_scope)

            def _pop_inflight(task: tuple[int, int], nd_id: str) -> None:
                if task in inflight:
                    inflight[task] -= 1
                    if inflight[task] <= 0:
                        inflight.pop(task, None)
                        inflight_nodes.pop(task, None)
                    else:
                        inflight_nodes.get(task, set()).discard(nd_id)

            def _clear_inflight(task: tuple[int, int]) -> None:
                inflight.pop(task, None)
                inflight_nodes.pop(task, None)

            def record(
                manifest: dict,
                nd_id: str,
                task: tuple[int, int],
                wall_sec: float | None = None,
            ) -> None:
                # 行构造的唯一实现点在 rl/eval_local.eval_row（云机离线评估用同一份）
                row = eval_row(
                    manifest, it=it, key16=key16, task=task, node=nd_id, wall_sec=wall_sec
                )
                # 累加器要的两个量从行里读回（它们本来就是同一份 manifest 的派生）
                win = int(row["win"])
                cleared = int(row["cleared"])
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
                            "wallSec": wall_sec,
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
                # 线程本地标签（不继承主线程）：收工断连的作用域键。
                dist_common.set_request_tag(req_scope)
                while time.time() < deadline and not all_done.is_set():
                    task = None
                    fanout_copy = False
                    t_task_start: float | None = None
                    with lock:
                        if (
                            streaks.get(nd["id"], 0) >= fail_streak_max
                            or soft_streaks.get(nd["id"], 0) >= soft_streak_max
                        ):
                            return
                        if pending:
                            # 尾段预留：gate 未放行且余量 ≤ reserved 时不取（留给本机直跑）；
                            # 宽限期强制释放防挂死。hold 中仍可对 in-flight race。
                            if not hold_for_local(
                                len(pending),
                                reserved,
                                local_gate is not None and local_gate.is_set(),
                                time.time() >= deadline - EVAL_LOCAL_RELEASE_GRACE,
                            ):
                                task = pending.popleft()
                                attempts[task] = attempts.get(task, 0) + 1
                                attempt = attempts[task]
                                register_inflight(inflight, task)
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add(nd["id"])
                        elif not inflight:
                            return
                        if task is None and inflight:
                            cand = pick_race_target(
                                inflight, nd["id"], inflight_nodes, {}
                            )
                            if cand is not None:
                                task = cand
                                inflight[task] += 1
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add(nd["id"])
                                fanout_copy = True
                                attempt = attempts.get(task, 0) + 1
                                log(
                                    f"[eval] tail-race s{task[0]}/seed{task[1]} "
                                    f"node={nd['id']} — race lane"
                                )
                    if task is None:
                        all_wait = min(5.0, max(0.1, deadline - time.time()))
                        time.sleep(all_wait)
                        continue
                    ok = False
                    err = ""
                    transient = False  # 背压/瞬断（判据单一实现：dist_common.is_transient_error）
                    task_lost = False  # 取包丢失（节点重启/清场；dist_common.is_task_lost_error）
                    manifest: dict = {}
                    try:
                        from rl.config import args_rollout_overrides, stage_json_for_args

                        _ov = args_rollout_overrides(args)
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
                            kind=EVAL_WEIGHTS_KIND,
                            stage_json=stage_json_for_args(args, task[0]) or "",
                            lives_override=int(_ov["lives_override"])
                            if "lives_override" in _ov
                            else None,
                            player_level=int(_ov["player_level"])
                            if "player_level" in _ov
                            else None,
                        )
                        why = dist_common.validate_eval_result(manifest, wver)
                        if why:
                            raise dist_common.DistError(0, why)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                        # 503 busy / 502 隧道 / 10054 / 超时 = 可恢复，**不计节点故障**
                        # （判据单一实现：dist_common.is_transient_error；旧实现对 153 次
                        # 503 也记 streak、3 次即把该节点熔断整轮）。
                        transient = dist_common.is_transient_error(e)
                        # 409 wver-not-cached = 可刷新条件：节点侧那份（归档）权重没了，
                        # 就地重发 + 清 reuse 缓存，同一节点继续用（不是节点故障）。
                        if (
                            isinstance(e, dist_common.DistError)
                            and e.status == 409
                            and dist_common.refresh_weights(
                                nd,
                                iter_id=eval_iter_id,
                                wver=wver,
                                weights_bytes=weights_bytes,
                                timeout=min(300.0, max(60.0, task_timeout)),
                                kind=EVAL_WEIGHTS_KIND,
                                err=err,
                                log=log,
                            )
                        ):
                            transient = True
                        # 404「task lost on node」= 节点重启/清场把它进程内的结果/在飞任务清掉了
                        # （2026-09-19 审计 F1，与 A 层同判据）：不计故障、不耗 attempt 配额，
                        # 并清该节点这条腿的 reuse 账本（重启后桶也可能空了）。
                        task_lost = dist_common.is_task_lost_error(e)
                        if task_lost:
                            dist_common.forget_weights_node(nd["id"], kind=EVAL_WEIGHTS_KIND)
                            transient = True
                    with lock:
                        if ok:
                            if task in seen:
                                _pop_inflight(task, nd["id"])
                                log(
                                    f"[eval] dup settle s{task[0]}/seed{task[1]} "
                                    f"node={nd['id']} — dropped"
                                )
                                continue
                            seen.add(task)
                            writers[0] += 1  # 收工前必须等它落盘
                            _clear_inflight(task)
                            streaks[nd["id"]] = 0
                            soft_streaks[nd["id"]] = 0
                            if len(seen) >= total:
                                _settle_complete()
                        elif fanout_copy:
                            _pop_inflight(task, nd["id"])
                        else:
                            # 瞬时错误不计节点击败（与 A 层 / 一次性评估同判据），但连续
                            # 软失败仍有上界——否则整体脉停（隧道挂了）时会一直空转到窗口到期。
                            if transient:
                                soft_streaks[nd["id"]] = soft_streaks.get(nd["id"], 0) + 1
                                if soft_streaks[nd["id"]] == soft_streak_max:
                                    log(
                                        f"[eval] node {nd['id']}: 连续 {soft_streak_max} 次瞬时失败"
                                        f"（背压/瞬断，非节点故障）— 本轮停派"
                                    )
                            else:
                                streaks[nd["id"]] = streaks.get(nd["id"], 0) + 1
                            _pop_inflight(task, nd["id"])
                            if (attempt < EVAL_TASK_ATTEMPTS or transient) and task not in seen:
                                pending.append(task)
                                log(
                                    f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued"
                                    + (
                                        "（任务丢失·节点重启，已回队、不计故障）"
                                        if task_lost
                                        else "（瞬断/背压，不计节点故障）"
                                        if transient
                                        else ""
                                    )
                                )
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
                    if ok:
                        wall_sec = (
                            round(time.time() - t_task_start, 3)
                            if t_task_start is not None
                            else None
                        )
                        try:
                            record(manifest, nd["id"], task, wall_sec)
                        finally:
                            with lock:
                                writers[0] = max(0, writers[0] - 1)
                        el = manifest.get("elapsedSec")
                        log(
                            f"[eval] {len(seen)}/{total} s{task[0]}/seed{task[1]} "
                            f"node={nd['id']} outcome={manifest.get('outcome')} "
                            f"ticks={manifest.get('ticks')} "
                            f"elapsed={str(el) + 's' if el is not None else '-'}"
                        )

            def local_worker() -> None:
                """本机直跑 worker：gate 放行前让位训练（每 5s 醒来看一眼 deadline）。"""
                if snapshot_path is None:
                    return
                while time.time() < deadline and not all_done.is_set():
                    t_task_start = None
                    if local_gate is not None and not local_gate.is_set():
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return
                        if not local_gate.wait(timeout=min(5.0, remaining)):
                            continue
                    task = None
                    fanout_copy = False
                    with lock:
                        if pending:
                            task = pending.popleft()
                            attempts[task] = attempts.get(task, 0) + 1
                            attempt = attempts[task]
                            register_inflight(inflight, task)
                            t_task_start = time.time()
                            inflight_nodes.setdefault(task, set()).add("local")
                        elif not inflight:
                            return
                        if task is None and inflight:
                            cand = pick_race_target(inflight, "local", inflight_nodes, {})
                            if cand is not None:
                                task = cand
                                inflight[task] += 1
                                t_task_start = time.time()
                                inflight_nodes.setdefault(task, set()).add("local")
                                fanout_copy = True
                                attempt = attempts.get(task, 0) + 1
                                log(
                                    f"[eval] tail-race s{task[0]}/seed{task[1]} "
                                    f"node=local — race lane"
                                )
                    if task is None:
                        time.sleep(min(1.0, max(0.1, deadline - time.time())))
                        continue
                    ok = False
                    err = ""
                    manifest: dict = {}
                    try:
                        from rl.config import args_rollout_overrides, stage_json_for_args

                        _ov = args_rollout_overrides(args)
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
                            stage_json=stage_json_for_args(args, task[0]) or "",
                            lives_override=int(_ov["lives_override"])
                            if "lives_override" in _ov
                            else None,
                            player_level=int(_ov["player_level"])
                            if "player_level" in _ov
                            else None,
                        )
                        why = dist_common.validate_eval_result(manifest, wver)
                        if why:
                            raise dist_common.DistError(0, why)
                        ok = True
                    except Exception as e:
                        err = str(e)[:200]
                    with lock:
                        if ok:
                            if task in seen:
                                _pop_inflight(task, "local")
                                log(
                                    f"[eval] dup settle s{task[0]}/seed{task[1]} "
                                    f"node=local — dropped"
                                )
                                continue
                            seen.add(task)
                            writers[0] += 1  # 收工前必须等它落盘
                            _clear_inflight(task)
                            if len(seen) >= total:
                                _settle_complete()
                        elif fanout_copy:
                            _pop_inflight(task, "local")
                        elif attempt < EVAL_TASK_ATTEMPTS and task not in seen:
                            _pop_inflight(task, "local")
                            pending.append(task)
                            log(f"[eval] s{task[0]}/seed{task[1]} failed ({err}) — requeued")
                        elif task not in seen:
                            _pop_inflight(task, "local")
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
                            log(
                                f"[eval] s{task[0]}/seed{task[1]} failed {attempt}x ({err}) — dropped"
                            )
                    if ok:
                        wall_sec = (
                            round(time.time() - t_task_start, 3)
                            if t_task_start is not None
                            else None
                        )
                        try:
                            record(manifest, "local", task, wall_sec)
                        finally:
                            with lock:
                                writers[0] = max(0, writers[0] - 1)
                        el = manifest.get("elapsedSec")
                        log(
                            f"[eval] {len(seen)}/{total} s{task[0]}/seed{task[1]} "
                            f"node=local outcome={manifest.get('outcome')} "
                            f"ticks={manifest.get('ticks')} "
                            f"elapsed={str(el) + 's' if el is not None else '-'}"
                        )

            # —— 本机槽位先开工（不等节点门/权重门，审计 B3）——
            # 本地权重就是本机冻结快照，无需下发；节点门（并行 ping）与权重 POST 都是
            # 秒级开销，让本机槽位干等纯属白丢吞吐。
            def _spawn_tracked(fn, *a: Any, name: str = "") -> threading.Thread:
                """孵化消费线程并计入 live_workers（线程必须先计数再启动：主线程在
                spawn 完才看计数，晚计数会让收工判断误以为「消费线程全退」）。"""
                live_workers[0] += 1

                def _body() -> None:
                    try:
                        fn(*a)
                    finally:
                        with lock:
                            live_workers[0] -= 1

                t = threading.Thread(target=_body, daemon=True, name=name)
                t.start()
                return t

            threads: list[threading.Thread] = []
            if snapshot_path is not None and local_slots > 0:
                for _ in range(local_slots):
                    threads.append(_spawn_tracked(local_worker, name="eval-local"))

            local_bun = bun_version(bun)
            # 2026-09-03 修正（mac 实测 stage.tiles null）：eval_stages 含自定义关
            # （>=2000）时，节点必须有能力位 stageJsonSupport——旧 agent（无该位）
            # 收到 stage=2000 会走 arena/真实关解析 → stage null → 崩溃。无能力节点
            # 一律跳过，任务自然落回本机 local（已支持 stage-json 透传）。
            need_sj = bool(todo) and any(t[0] >= 2000 for t in todo)
            # 节点门（2026-09-17 统一）：与 rollout 同一判据 = codeHash——唯一事实来源
            # tools/agent/codehash-files.txt（引擎 src/game、config、RNG、God AI 已并入
            # 该清单）。不再比 ping.engineEpoch：该字段已从 /v1/ping 移除（engine_epoch
            # 退为账本记录值），也不再需要「旧 agent 无字段→过渡期放行」的分支——
            # codeHash 是 rollout 门一直都在用的字段。
            code_hash_local = dist_common.compute_code_hash()
            alive = []
            # 并行 ping（保序）：串行墙钟 = Σ 每台延迟（两台超时即 ~7s），而节点门每轮
            # 重跑一次；并行 == 最慢一台（同 batch_eval，2026-09-19 审计 B2）。
            enabled_nodes = [n for n in cfg.get("nodes", []) if n.get("enabled", True)]
            pings = dist_common.ping_nodes_parallel(enabled_nodes, timeout=status_timeout)
            for n, ping in zip(enabled_nodes, pings, strict=True):
                nid = str(n.get("id") or n.get("url") or "?")
                if ping is None:
                    # 留痕：旧实现静默 continue——节点被丢时日志里既看不出是谁、也看不出
                    # 为什么（2026-09-19 审计 B4）。
                    log(
                        f"[eval] node {nid}: ping 失败/超时（并行探测，预算 {status_timeout}s）"
                        f" — 本轮不参与"
                    )
                    continue
                if not ping.get("evalSupport"):
                    log(
                        f"[eval] node {nid}: agent lacks evalSupport — skipped "
                        f"(sync code + restart agent to enable)"
                    )
                    continue
                if need_sj and not ping.get("stageJsonSupport"):
                    log(
                        f"[eval] node {nid}: 自定义关 eval 需 stageJsonSupport 能力位"
                        f"（旧 agent）—— skipped，任务落本机 local"
                    )
                    continue
                if mm(str(ping.get("bunVersion", "?"))) != mm(local_bun):
                    log(f"[eval] node {nid}: bun version mismatch — skipped")
                    continue
                ch_why = dist_common.check_code_hash(ping, code_hash_local)
                if ch_why:
                    log(f"[eval] node {nid}: {ch_why} — skipped")
                    continue
                c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
                alive.append({"id": nid, "url": n["url"], "key": n.get("authKey", ""), "c": c_n})
            # 本机槽位可用 ⇔ snapshot_path 已建（见上方快照段）⇒ 门失败不再整轮空转。
            if not alive:
                if snapshot_path is None:
                    log("[eval] no eval-capable node — skipped this round")
                    return
                log("[eval] no eval-capable node — local-only eval this round")

            # 幂等下发（节点通常已持有 → kept 短路径；agent 重启过则补发）——并行
            with open(rl_path, "rb") as f:
                weights_bytes = f.read()
            nodes_ok = (
                dist_common.post_weights_parallel(
                    alive,
                    iter_id,
                    wver,
                    weights_bytes,
                    timeout=min(300.0, max(60.0, task_timeout)),
                    kind=EVAL_WEIGHTS_KIND,
                    log=log,
                )
                if alive
                else []
            )
            if alive and not nodes_ok:
                # 旧实现此处无条件 return：本机槽位明明可用却整轮 0 局（审计 B5）。
                if snapshot_path is None:
                    log("[eval] all weight POSTs failed — skipped this round")
                    return
                log("[eval] all weight POSTs failed — local-only eval this round")

            log(
                f"[eval] it{it}: dispatch {total} greedy games "
                f"(corpus={len(pairs)}, done={len(pairs) - total})"
                + (" [it0 基线 · bc 权重]" if baseline else "")
                + (
                    " [dual-track anchor+rotor]"
                    if should_dual_track(n_seeds, baseline)
                    else ""
                )
                + f" -> {[(n['id'], n['c']) for n in nodes_ok]}"
                + (f" [local tail-reserved ×{reserved}]" if reserved else "")
            )

            # 收官 drain / 远端全员 mismatch 时：gate 若仍关着，local_worker 会空等到
            # deadline 才放行——终轮 eval 等 600s 却 0 局。没有节点可派时立刻开闸。
            if release_local_gate_if_starved(local_gate, nodes_ok):
                log(
                    f"[eval] it{it}: no remote nodes — local_gate released immediately"
                    f"（local_slots={local_slots} snapshot={'yes' if snapshot_path else 'no'}）"
                )
            for nd in nodes_ok:
                for _ in range(nd["c"]):
                    threads.append(_spawn_tracked(worker, nd, name=f"eval-{nd['id']}"))
            # —— 等收口（**显式**：旧实现靠 join(window + task_timeout) 顺带完成）——
            # ① settled 满 → 立即收工（req 4：断连 + 拒发新请求，慢节点/竞速副本不再等）；
            # ② 墙钟到期 → **有界**等窗口内的在飞局落账（在飞清空即走，兜底
            #    EVAL_INFLIGHT_GRACE_SEC），那些局有价值，但不再像旧实现那样空等 4–76s。
            while not all_done.is_set() and time.time() < deadline:
                all_done.wait(0.2)
                with lock:
                    if live_workers[0] <= 0:
                        break
            grace_end = time.time() + float(min(task_timeout, EVAL_INFLIGHT_GRACE_SEC))
            while not all_done.is_set() and time.time() < grace_end:
                with lock:
                    if not inflight or live_workers[0] <= 0:
                        break
                all_done.wait(0.2)
            with lock:
                no_consumers = live_workers[0] <= 0
            # settled 满 = 断连（收工只等「正在写行」的赢家落盘）。
            closing = dist_common.abort_active_requests(req_scope)
            if all_done.is_set():
                log(
                    f"[eval] it{it}: settled 满（{len(seen)}/{total}）— 断连 {closing} 条"
                    f"在飞连接 + 拒发新请求，立即收工（慢节点/竞速副本不再等）"
                )
            else:
                log(
                    f"[eval] it{it}: 收工（未全结算 {len(seen)}/{total}）— 断连 {closing} 条"
                    f"在飞连接（在途局丢弃，下次续跑）"
                    + ("【消费线程已全退】" if no_consumers else "")
                )
            all_done.set()
            quiet_deadline = time.monotonic() + 1.0
            while time.monotonic() < quiet_deadline:
                with lock:
                    if writers[0] <= 0:
                        break
                time.sleep(0.002)
            with lock:
                pending_writers = writers[0]
            if pending_writers > 0:
                log(
                    f"[eval] it{it}: 收工仍有 {pending_writers} 个写行者未落盘"
                    f"（1s 预算用尽，行可能晚于本行 summary 落账）"
                )
            # 线程只做 best-effort 收拢（0.25s 总预算）：daemon 线程本就随进程退出。
            join_deadline = time.monotonic() + 0.25
            for t_ in threads:
                t_.join(timeout=max(0.01, join_deadline - time.monotonic()))

            # 课程血缘进 summary 行（门控趋势过滤；延迟导入避免 rl.cmd ↔ 本模块环）。
            from rl.cmd import course_fp_for_args

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
                # D14 课程血缘（门按 course_fp 过滤趋势行；无课程 = ""=不过滤）。
                course_fp=course_fp_for_args(args),
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
    baseline: bool = False,
) -> None:
    """固定语料干净评估（阻塞版，调用方放后台线程跑）。任何失败只记日志，绝不抛出。

    薄包装：OO 实现在 EvalDispatcher（rl/eval_dispatch.py 同模块——本地直跑
    runner 的 monkeypatch 需落在本模块全局名上，见 tests/test_run_rl.py）。
    """
    EvalDispatcher(
        bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate, local_gate, baseline
    ).run()


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
    baseline: bool = False,
) -> threading.Thread:
    t_ = threading.Thread(
        target=dispatch_eval_round,
        args=(bun, rl_path, traj_dir, args, cfg, iter_id, it, rollout_winrate, local_gate, baseline),
        daemon=True,
        name=f"eval-it{it}" + ("-baseline" if baseline else ""),
    )
    t_.start()
    return t_
