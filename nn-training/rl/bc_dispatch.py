"""rl/bc_dispatch.py — BC 语料 LAN 集群派发（plan/bc-cloud-integration.plan.md §5）。

把 (stage, seed) 语料任务分派给启用的 LAN 采样节点（`/v1/task ?mode=bc`），节点侧
跑 God-AI 教师单局（tools/sim/export-godai-bc.ts）并回传 BCV2 容器；训练机校验后
落盘 `<data_dir>/it{it}/bc_s{stage}_seed{seed}/`（npy + manifest，含 course_fp/
corpus_fp 血缘）。

与 RL dispatch.py 的差异（刻意精简）：
  - 无竞速：每个 (stage, seed) 任务归一个节点槽位独占（语料不需要最快者胜）；
  - 无 wver 权重切换（教师自对弈不依赖策略权重，wver 恒 BC_WVER）；
  - wins-only 败局（manifest.kept=false 空容器）是**合法结果**，计 loss_skipped；
  - 局失败（DistError）重试一次（可能换节点），再败计 failed 由调用方决断。

torch-free / stdlib-only。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

import dist_common
from dist_common import (
    BC_MODE,
    BC_WVER,
    fetch_task,
    load_dist_config,
    node_ping,
    validate_result,
    write_shard,
)


class BcDispatchError(RuntimeError):
    """BC 语料派发失败（无可用节点 / 任务彻底失败）。"""


def bc_capable_nodes(cfg: dict | None, log=lambda msg: None) -> list[dict]:
    """启用且 ping 报告 bcSupport 的节点（旧 agent 缺能力位 → 不派——fail-closed，
    与 stageJsonSupport 同规：旧 agent 会把不认识的 mode 静默跑成默认 rollout，
    数据污染绝不容忍）。"""
    out: list[dict] = []
    for n in (cfg or {}).get("nodes", []):
        if not n.get("enabled", True):
            continue
        ping = node_ping(n["url"], n.get("authKey", ""))
        if ping is None:
            log(f"[bc-dispatch] node {n.get('id') or n.get('url')} unreachable — skip")
            continue
        if not ping.get("bcSupport"):
            log(
                f"[bc-dispatch] node {n.get('id') or n.get('url')} 无 bcSupport 能力位"
                "（旧 agent）—— skip（/v1/update 升级后自动纳入）"
            )
            continue
        out.append(n)
    return out


def dispatch_bc_corpus(
    *,
    tasks: list[tuple[int, int]],
    out_dir: str | Path,
    it: int,
    corpus_fp: str,
    course_fp: str,
    difficulty: str,
    max_ticks: int,
    stage_json_for=lambda stage: None,
    lives_override: int | None = None,
    player_level: int | None = None,
    wins_only: bool = True,
    near_miss_times: int = 3,
    cfg: dict | None = None,
    nodes: list[dict] | None = None,
    max_slots: int = 64,
    task_timeout_sec: float = 600.0,
    iter_suffix: str = "",
    #: 节点熔断阈值（同一节点连续 N 次「真失败」后本轮摘掉它，任务改派其它节点）。
    #: 0 = 关闭。2026-09-14 事故：mac 节点胜局一律崩（17 局全废），训练直接
    #: BcDispatchError 挂掉——有熔断则 3 次就摘掉它，剩余任务重排给健康节点。
    node_fail_limit: int = 3,
    #: busy（节点并发槽满，503）背压重排上限与退避步长（2026-09-14：40 局瞬推时
    #: 溢出任务两次撞 busy 即被判失败，整轮训练退出）。busy 不是故障：不计 streak、
    #: 不消耗 attempt 配额，只放回队列 + 线性退避；超上限才认失败。
    busy_retry_limit: int = 6,
    busy_backoff_sec: float = 0.25,
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """并发派发 BC 语料任务到节点，落盘 shard 目录。返回统计 dict。

    tasks：本轮还缺的 (stage, seed) 对（调用方已做断点续跑过滤）。
    nodes：显式节点表（测试注入）；None = 从 rl-config 取 bc_capable_nodes 过滤结果。
    iter_suffix：iterId 命名空间后缀（如 "-smoke"）——agent 结果缓存键含 iterId，
    smoke 覆盖（wins_only 等）不改 corpus_fp，必须换命名空间才不会命中上一轮缓存
    （2026-09-13 实测：smoke 两次跑同 iterId，第二次直接回放上一次的 loss-skipped）。
    """
    if not tasks:
        return {"games": 0, "kept": 0, "loss_skipped": 0, "failed": 0, "nodes": 0}
    if nodes is None:
        nodes = bc_capable_nodes(cfg if cfg is not None else load_dist_config(), log=log)
    if not nodes:
        raise BcDispatchError(
            "无可用 bc 语料节点（enabled + bcSupport）——检查 rl-config nodes 与节点"
            " agent 版本（/v1/ping 需报 bcSupport:true）"
        )
    slots: list[tuple[dict, int]] = []
    for n in nodes:
        for i in range(max(1, int(n.get("concurrency", 1) or 1))):
            slots.append((n, i))
    if len(slots) > max_slots:
        slots = slots[:max_slots]

    iter_id = f"bc-it{it}-{str(corpus_fp)[:12]}{iter_suffix}"
    work: queue.Queue = queue.Queue()
    for task in tasks:
        work.put(task)
    seen_keys: set[tuple[int, int]] = set()
    # Any：games/kept 计数与 elapsed_sec（float）同表——mypy 拒绝异构 dict 字面量推断
    stats: dict[str, Any] = {
        "games": 0,
        "kept": 0,
        "loss_skipped": 0,
        "failed": 0,
        "nodes": len(nodes),
    }
    lock = threading.Lock()
    # 节点健康（本轮内）：连续真失败计数 / 已熔断节点 / 已放回队列一次的 (stage,seed)。
    # 放回只做一次 —— 否则全节点熔断后任务会在队列里被反复放回，静默漏采。
    node_fail_streak: dict[str, int] = {str(n.get("id") or n["url"]): 0 for n in nodes}
    tripped: set[str] = set()
    requeued: set[tuple[int, int]] = set()
    #: 每任务的 busy 重排次数（跨 worker 共享——否则任务在节点间来回被推会无限重排）。
    busy_tries: dict[tuple[int, int], int] = {}

    def is_busy_hint(reason: str) -> bool:
        """节点回 503 busy（并发槽满）不算节点故障——不能据此熔断。"""
        return "busy" in reason[:64].lower()

    def slot_worker(node: dict) -> None:
        url = node["url"]
        auth = node.get("authKey", "")
        nid = str(node.get("id") or url)
        empty_waits = 0
        while True:
            try:
                stage, seed = work.get_nowait()
            except queue.Empty:
                # 熔断发生后，被放回的任务要等健康节点接手：队列暂空时多等一会儿再退
                # （最多 2s）。无熔断的正常轮次零开销——直接退出。
                if tripped and empty_waits < 40:
                    empty_waits += 1
                    time.sleep(0.05)
                    continue
                return
            # 熔断：本节点已摘掉 → 手上这个任务**无条件放回**队列，然后本 worker 退出。
            # 不能 continue（否则放回的任务会被自己立刻重新拾起，等于没熔断）；放回是安全的，
            # 因为本 worker 立即 return，不存在"放回→自取"死循环。队列若最终无人消费，
            # 主线程末尾对账会把差额计 failed（不静默漏采）。
            if node_fail_limit > 0 and nid in tripped:
                with lock:
                    others = [k for k in node_fail_streak if k not in tripped]
                    first = (stage, seed) not in requeued
                    requeued.add((stage, seed))
                work.put((stage, seed))
                if first:
                    log(
                        f"[bc-dispatch] {nid} 已熔断 → s{stage} seed{seed} 改派其它节点"
                        f"（健康节点：{'、'.join(others) if others else '无'}）"
                    )
                return
            ok = False
            busy_hint = False
            requeue_busy = False
            for attempt in (1, 2):  # 局失败重试一次（可能换节点）
                try:
                    manifest, files = fetch_task(
                        url,
                        auth,
                        iter_id=iter_id,
                        wver=BC_WVER,
                        stage=stage,
                        seed=seed,
                        max_ticks=max_ticks,
                        difficulty=difficulty,
                        timeout=task_timeout_sec,
                        mode=BC_MODE,
                        stage_json=stage_json_for(stage) or "",
                        lives_override=lives_override,
                        player_level=player_level,
                        course_fp=course_fp,
                        wins=(1 if wins_only else 0),
                        near_miss_times=near_miss_times,
                    )
                    reason = validate_result(
                        manifest, files, BC_WVER, {(stage, seed)}, seen_keys
                    )
                    if reason:
                        raise dist_common.DistError(0, f"validate: {reason}")
                    with lock:
                        stats["games"] += 1
                    if manifest.get("kept") is False:
                        with lock:
                            stats["loss_skipped"] += 1
                        log(
                            f"[bc-dispatch] {nid} s{stage} seed{seed}: loss-skipped"
                            "（wins-only）"
                        )
                    else:
                        shard_dir = Path(out_dir) / f"bc_s{stage}_seed{seed}"
                        manifest = dict(manifest)
                        manifest["course_fp"] = course_fp
                        manifest["corpus_fp"] = corpus_fp
                        manifest["it"] = int(it)
                        write_shard(files, manifest, str(shard_dir))
                        with lock:
                            stats["kept"] += 1
                        log(
                            f"[bc-dispatch] {nid} s{stage} seed{seed}: kept "
                            f"nSamples={manifest.get('nSamples')} outcome={manifest.get('outcome')}"
                        )
                    with lock:
                        seen_keys.add((stage, seed))
                        busy_tries.pop((stage, seed), None)
                    node_fail_streak[nid] = 0  # 一次成功即清零连续失败
                    ok = True
                    break
                except dist_common.DistError as e:
                    reason = str(e.reason)
                    busy_hint = is_busy_hint(reason)
                    if busy_hint:
                        # 背压（2026-09-14 事故）：节点并发槽占满时回 503 busy。原实现把它
                        # 当普通失败「立刻重试一次」——同一瞬的 40 局里溢出的那批两次都撞
                        # busy，直接计 failed 并把整轮训练打死（实测 7 局）。busy 是限流信号
                        # 而非故障：任务放回队列（让空闲节点接手）+ 退避后再抢，且**不消耗**
                        # attempt 配额、**不计**节点失败 streak。退避上限后仍 busy 才认失败。
                        with lock:
                            n = busy_tries.get((stage, seed), 0)
                            if n < busy_retry_limit:
                                busy_tries[(stage, seed)] = n + 1
                                work.put((stage, seed))
                                requeue_busy = True
                        if requeue_busy:
                            if n == 0:
                                log(
                                    f"[bc-dispatch] {nid} 并发槽满（busy）→ 退避重排"
                                    f"（背压，不计失败；上限 {busy_retry_limit} 次）"
                                )
                            time.sleep(busy_backoff_sec * (n + 1))
                            break
                    # 截断放宽到 600：节点回传的 reason 已做「错误类型行 + 栈顶帧」摘要
                    # （sampler-agent.summarizeChildFailure），原文截断会把诊断信息切掉。
                    log(
                        f"[bc-dispatch] {nid} s{stage} seed{seed} attempt {attempt} "
                        f"failed: {reason[:600]}"
                    )
                except Exception as e:  # 未知异常同样重试一次后放弃
                    busy_hint = False
                    log(
                        f"[bc-dispatch] {nid} s{stage} seed{seed} attempt {attempt} "
                        f"error: {type(e).__name__}: {str(e)[:600]}"
                    )
            if requeue_busy:
                continue  # 已被背压重排，本任务不计失败、本 worker 去取下一个
            if not ok:
                with lock:
                    stats["failed"] += 1
                    if node_fail_limit > 0 and not busy_hint:
                        node_fail_streak[nid] = node_fail_streak.get(nid, 0) + 1
                        if node_fail_streak[nid] >= node_fail_limit and nid not in tripped:
                            tripped.add(nid)
                            log(
                                f"[bc-dispatch] ⚠ 节点 {nid} 连续 {node_fail_limit} 次任务失败"
                                " → 本轮熔断（剩余任务只派其它节点；请 /v1/ping 看 codeHash"
                                " 与节点日志定位）"
                            )

    threads = [
        threading.Thread(target=slot_worker, args=(node,), daemon=True, name=f"bc-{idx}")
        for idx, (node, _slot_i) in enumerate(slots)
    ]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats["elapsed_sec"] = round(time.time() - t0, 1)
    stats["failed_nodes"] = sorted(tripped)
    # 对账：熔断节点退出后可能留下无人消费的任务 —— 计 failed，绝不静默漏采
    # （run_bc 侧按 landed_pairs 判定缺口，差额必须出现在 failed 里否则会“少采却以为齐”）。
    consumed = stats["games"] + stats["failed"]
    if consumed < len(tasks):
        missing = len(tasks) - consumed
        stats["failed"] += missing
        log(
            f"[bc-dispatch] ⚠ {missing} 个任务未被任何节点消费（熔断后队列无人接管）"
            "——已计 failed，重跑本课程即可断点续补"
        )
    log(
        f"[bc-dispatch] round it{it} done in {stats['elapsed_sec']}s: games={stats['games']} "
        f"kept={stats['kept']} loss_skipped={stats['loss_skipped']} failed={stats['failed']} "
        f"nodes={stats['nodes']}"
        + (f" tripped={'、'.join(stats['failed_nodes'])}" if tripped else "")
    )
    return stats


def landed_pairs(data_round_dir: str | Path) -> set[tuple[int, int]]:
    """已落盘 (stage, seed) 集（断点续跑过滤；目录名 bc_s{stage}_seed{seed}）。"""
    out: set[tuple[int, int]] = set()
    root = Path(data_round_dir)
    if not root.exists():
        return out
    for p in root.glob("bc_s*_seed*"):
        if not (p / "manifest.json").is_file():
            continue
        try:
            mm = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
            out.add((int(mm.get("stage")), int(mm.get("seed"))))
        except (OSError, ValueError, TypeError):
            continue
    return out
