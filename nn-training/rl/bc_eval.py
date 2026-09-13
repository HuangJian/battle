"""rl/bc_eval.py — BC 每 N epoch 的多地图干净评估派发（2026-09-13）。

被评对象 = 云端/本地每 epoch 回传的 BC 权重本身（mode=eval、policy=nn——节点侧
贪心 runner，与 RL 干净评估同一通道）。地图 = 课程 `eval.levels` 引用的关卡文件
（arena4/arena6/…），每张图按其 stages/difficulty/max_ticks/player 逐 stage ×
games_per_stage 派局，**按 RL 门指标口径聚合**（GATE_PLATEAU_METRICS 同词表）：
win_rate / kills_mean / phits_mean（被命中）/ pickup_mean / timeout_frac，
外加 mean_ticks / score_mean 供参考。

节点门：enabled + evalSupport + codeHash == 训练机口径（stale 节点跑旧引擎的
评估是污染数据，fail-closed 排除——self 节点与训练机同工作区恒通过）。
单局失败重试一次换节点；聚合行进训练账本（bc_eval 事件），控制台分图展示。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import dist_common
from dist_common import fetch_task, node_ping, post_weights, validate_eval_result

#: 与 rl/config.GATE_PLATEAU_METRICS 同词表（RL eval 展示口径）
EVAL_METRIC_KEYS = ("win_rate", "kills_mean", "phits_mean", "pickup_mean", "timeout_frac")


class BcEvalError(RuntimeError):
    """BC eval 派发失败（无可用节点 / 权重推送失败）。"""


def eval_capable_nodes(cfg: dict | None, log=lambda msg: None) -> list[dict]:
    """enabled + evalSupport + codeHash 与训练机一致的节点。"""
    expected = dist_common.compute_code_hash()
    out: list[dict] = []
    for n in (cfg or {}).get("nodes", []):
        if not n.get("enabled", True):
            continue
        ping = node_ping(n["url"], n.get("authKey", ""))
        if ping is None:
            log(f"[bc-eval] node {n.get('id') or n.get('url')} unreachable — skip")
            continue
        if not ping.get("evalSupport"):
            log(f"[bc-eval] node {n.get('id') or n.get('url')} 无 evalSupport —— skip")
            continue
        if str(ping.get("codeHash")) != expected:
            log(
                f"[bc-eval] node {n.get('id') or n.get('url')} codeHash stale "
                "—— skip（升级后自动纳入）"
            )
            continue
        out.append(n)
    return out


def aggregate_eval_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """单地图局结果 → RL 门指标口径聚合（纯函数，单测覆盖）。

    行字段（eval manifest）：win/cleared/outcome/ticks/score/kills/playerHits/
    powerUpsCollected——缺键按 0 计（旧 agent 兼容）。
    """
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "win_rate": 0.0,
            "kills_mean": 0.0,
            "phits_mean": 0.0,
            "pickup_mean": 0.0,
            "timeout_frac": 0.0,
            "score_mean": 0.0,
            "mean_ticks": 0.0,
        }
    wins = sum(1 for r in rows if r.get("win"))
    timeouts = sum(
        1
        for r in rows
        if str(r.get("outcome")) in ("timeout", "max_ticks") and not r.get("cleared")
    )
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4),
        "kills_mean": round(sum(float(r.get("kills") or 0) for r in rows) / n, 3),
        "phits_mean": round(sum(float(r.get("playerHits") or 0) for r in rows) / n, 3),
        "pickup_mean": round(sum(float(r.get("powerUpsCollected") or 0) for r in rows) / n, 3),
        "timeout_frac": round(timeouts / n, 4),
        "score_mean": round(sum(float(r.get("score") or 0) for r in rows) / n, 3),
        "mean_ticks": round(sum(float(r.get("ticks") or 0) for r in rows) / n, 1),
    }


def dispatch_bc_eval(
    *,
    weights_bytes: bytes,
    eval_levels: list[str],
    games_per_stage: int,
    it: int,
    epoch: int,
    cfg: dict | None = None,
    nodes: list[dict] | None = None,
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """把一份 BC 权重在 eval.levels 各图上跑干净评估并聚合。

    返回 {"epoch", "wver", "levels": [{level, n, wins, win_rate, kills_mean,
    phits_mean, pickup_mean, timeout_frac, score_mean, mean_ticks, failed}], "ts"}。
    无可用节点 / 权重推送全败 → BcEvalError（调用方决定降级或中止）。
    """
    from rl.config import load_course, resolve_level

    if nodes is None:
        from dist_common import load_dist_config

        nodes = eval_capable_nodes(cfg if cfg is not None else load_dist_config(), log=log)
    if not nodes:
        raise BcEvalError("无可用 eval 节点（enabled + evalSupport + codeHash 一致）")

    wver = hashlib.sha256(weights_bytes).hexdigest()
    iter_id = f"bc-eval-it{it}-ep{epoch}-{wver[:8]}"
    pushed = 0
    for n in nodes:
        try:
            post_weights(
                n["url"], n.get("authKey", ""), iter_id, wver, weights_bytes, kind="rollout"
            )
            pushed += 1
        except dist_common.DistError as e:
            log(f"[bc-eval] weights 推送失败 {n.get('id')}: {e.reason[:120]}")
    if pushed == 0:
        raise BcEvalError("weights 推送全部失败——本 epoch 评估放弃")

    # 任务槽位 = 节点并发（评估局短，槽位即并发）
    slots: list[dict] = []
    for n in nodes:
        for _ in range(max(1, int(n.get("concurrency", 1) or 1))):
            slots.append(n)

    level_rows: list[dict[str, Any]] = []
    t0 = time.time()
    for level_name in eval_levels:
        lvl = load_course(str(resolve_level(level_name)))
        tasks: list[tuple[int, int]] = [
            (stage, seed)
            for stage in lvl.stage_ids
            for seed in range(1, int(games_per_stage) + 1)
        ]
        rows: list[dict[str, Any]] = []
        failed = 0

        def _run_one(node: dict, stage: int, seed: int) -> dict[str, Any] | None:
            for attempt in (1, 2):
                try:
                    manifest, _files = fetch_task(
                        node["url"],
                        node.get("authKey", ""),
                        iter_id=iter_id,
                        wver=wver,
                        stage=stage,
                        seed=seed,
                        max_ticks=int(lvl.max_ticks),
                        difficulty=str(lvl.difficulty),
                        timeout=max(120.0, float(lvl.max_ticks) * 0.5 + 120.0),
                        mode="eval",
                        policy="nn",
                        stage_json=lvl.stage_json(stage) or "",
                        lives_override=lvl.player.lives,
                        player_level=lvl.player.level,
                    )
                    reason = validate_eval_result(manifest, wver)
                    if reason:
                        raise dist_common.DistError(0, f"validate: {reason}")
                    return manifest
                except dist_common.DistError as e:
                    log(
                        f"[bc-eval] {node.get('id')} {level_name} s{stage} seed{seed} "
                        f"attempt {attempt} failed: {e.reason[:140]}"
                    )
                except Exception as e:
                    log(
                        f"[bc-eval] {node.get('id')} {level_name} s{stage} seed{seed} "
                        f"attempt {attempt} error: {type(e).__name__}: {e}"
                    )
            return None

        import queue as _q
        import threading as _th

        work: _q.Queue = _q.Queue()
        for task in tasks:
            work.put(task)
        lock = _th.Lock()

        def slot_worker(node: dict) -> None:
            while True:
                try:
                    stage, seed = work.get_nowait()
                except _q.Empty:
                    return
                m = _run_one(node, stage, seed)
                with lock:
                    if m is None:
                        rows.append({"failed": True})
                    else:
                        rows.append(m)

        threads = [
            _th.Thread(target=slot_worker, args=(n,), daemon=True, name=f"bc-eval-{i}")
            for i, n in enumerate(slots)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok_rows = [r for r in rows if not r.get("failed")]
        failed = len(rows) - len(ok_rows)
        agg = aggregate_eval_rows(ok_rows)
        agg.update({"level": level_name, "failed": failed})
        level_rows.append(agg)
        log(
            f"[bc-eval] {level_name}: n={agg['n']} win_rate={agg['win_rate']} "
            f"kills_mean={agg['kills_mean']} phits_mean={agg['phits_mean']} "
            f"pickup_mean={agg['pickup_mean']} timeout_frac={agg['timeout_frac']} "
            f"failed={failed} ({round(time.time() - t0, 1)}s)"
        )

    return {
        "epoch": int(epoch),
        "wver": wver,
        "levels": level_rows,
        "ts": time.time(),
    }
