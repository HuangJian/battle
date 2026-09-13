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

    def slot_worker(node: dict) -> None:
        url = node["url"]
        auth = node.get("authKey", "")
        nid = str(node.get("id") or url)
        while True:
            try:
                stage, seed = work.get_nowait()
            except queue.Empty:
                return
            ok = False
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
                    ok = True
                    break
                except dist_common.DistError as e:
                    log(
                        f"[bc-dispatch] {nid} s{stage} seed{seed} attempt {attempt} "
                        f"failed: {e.reason[:200]}"
                    )
                except Exception as e:  # 未知异常同样重试一次后放弃
                    log(
                        f"[bc-dispatch] {nid} s{stage} seed{seed} attempt {attempt} "
                        f"error: {type(e).__name__}: {e}"
                    )
            if not ok:
                with lock:
                    stats["failed"] += 1

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
    log(
        f"[bc-dispatch] round it{it} done in {stats['elapsed_sec']}s: games={stats['games']} "
        f"kept={stats['kept']} loss_skipped={stats['loss_skipped']} failed={stats['failed']} "
        f"nodes={stats['nodes']}"
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
