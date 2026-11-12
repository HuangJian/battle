"""biz/bc_eval.py — BC 每 N epoch 的多地图干净评估派发（2026-09-13）。

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

import common.distribution
from common.distribution import fetch_task, node_ping, post_weights_cached, validate_eval_result

#: 与 biz/config.GATE_PLATEAU_METRICS 同词表（RL eval 展示口径）
EVAL_METRIC_KEYS = ("win_rate", "kills_mean", "phits_mean", "pickup_mean", "timeout_frac")


class BcEvalError(RuntimeError):
    """BC eval 派发失败（无可用节点 / 权重推送失败）。"""


def eval_capable_nodes(cfg: dict | None, log=lambda msg: None) -> list[dict]:
    """enabled + evalSupport + codeHash 与训练机一致的节点。"""
    expected = common.distribution.compute_code_hash()
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


def build_eval_panel(
    *,
    level_name: str,
    max_ticks: int,
    difficulty: str,
    lives: int | None,
    games_per_stage: int,
    stages: int,
    course_max_ticks: int | None = None,
    source: str = "levels",
    stage_ids: list[int] | None = None,
    enemies: list[int] | None = None,
) -> dict[str, Any]:
    """eval 行/面板的**面板口径**（B4，2026-11-05）：max_ticks / lives / games / seed0。

    课程顶层 `max_ticks` 不进这条链（每图用自己关卡文件的值），所以读数**禁止跨面板相减**；
    两个值都写进去，漂移在面板上可见，而不是靠人记。`seed0` 固定 1（每 stage 的 seed 从 1 起）。

    2026-11-12：加 `source`（`course` = 继承课程环境 / `levels` = 关卡文件）、
    `stage_ids`（实际被评的关；eval 的 2000+i 与训练的 2000+i 可能同号不同关）、
    `enemies`（课程 stages 的 count 集合）。跨面板分析先看这三个键。
    """
    panel = {
        "level": level_name,
        "source": str(source),
        "max_ticks": int(max_ticks),
        "course_max_ticks": None if course_max_ticks is None else int(course_max_ticks),
        "difficulty": str(difficulty),
        "lives": None if lives is None else int(lives),
        "games_per_stage": int(games_per_stage),
        "seed0": 1,
        "stages": int(stages),
    }
    if stage_ids is not None:
        panel["stage_ids"] = [int(s) for s in stage_ids]
    if enemies is not None:
        panel["enemies"] = [int(n) for n in enemies]
    return panel


def _ratio(num: float, den: float) -> float | None:
    """有分母才有率；0 分母 = 无读数（None），不编 0。"""
    return round(num / den, 4) if den > 0 else None


def _behaviour_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """行为门需要的三个率/量（B5，2026-11-05）：周期 eval 以前只有 `pickup_mean`（拾取
    **个数**），而课程门判据是 `powerUpsCollected/powerUpsSpawned` 的率 ≥65%；
    `cellsVisited` / `moveHist` 根本没进产物 ⇒ 五项判据一项也测不到。

    - `pickup_rate` = Σ收集 / Σ生成（分母 0 ⇒ None）；
    - `pickup_spawn_mean` = 每局生成数（解释 pickup_rate 的分母口径）；
    - `cells_mean` = 每局访问格数的均值（探索塌陷哨兵）；
    - `move_frac` = 方向移动占全部决策的比例（`moveHist[0]` 是停/无输入）。
    """
    if not rows:
        return {
            "pickup_rate": None,
            "pickup_spawn_mean": None,
            "cells_mean": None,
            "move_frac": None,
        }
    spawned = sum(float(r.get("powerUpsSpawned") or 0) for r in rows)
    collected = sum(float(r.get("powerUpsCollected") or 0) for r in rows)
    cells = [
        float(r["cellsVisited"])
        for r in rows
        if r.get("cellsVisited") is not None
    ]
    move_num = 0.0
    move_den = 0.0
    for h in (r.get("moveHist") for r in rows):
        if not isinstance(h, (list, tuple)) or len(h) < 2:
            continue
        vals = [float(x or 0) for x in h]
        move_den += sum(vals)
        move_num += sum(vals[1:])  # 槽 0 = 停/无输入
    n = len(rows)
    return {
        "pickup_rate": _ratio(collected, spawned),
        "pickup_spawn_mean": round(spawned / n, 3),
        "cells_mean": round(sum(cells) / len(cells), 1) if cells else None,
        "move_frac": _ratio(move_num, move_den),
    }


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
            # B5：行为门口径（拾取率 / 探索 / 移动占比）——零局时全是 None，
            # 不编 0（0 会被误读成「测到了，真没捡」）。
            **_behaviour_fields([]),
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
        **_behaviour_fields(rows),
    }


#: busy（节点并发槽满 503）背压上限与退避步长（2026-09-14，与 biz/bc_dispatch 同语义）。
#: 评估单局很短（~6s），退避不必大；上限 4 次足够等到槽位释放，超过才认失败。
BUSY_RETRY_LIMIT = 4
BUSY_BACKOFF_SEC = 0.5


def dispatch_bc_eval(
    *,
    weights_bytes: bytes,
    eval_levels: list[str],
    games_per_stage: int,
    it: int,
    epoch: int,
    course_max_ticks: int | None = None,
    cfg: dict | None = None,
    nodes: list[dict] | None = None,
    course: Any | None = None,
    course_env: bool = False,
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """把一份 BC 权重在 eval 图上跑干净评估并聚合。

    两种图源（2026-11-12）：
      - `course_env=False`（默认）：`eval_levels` 的关卡文件，各图用自己关卡文件的
        stages/difficulty/max_ticks/player（旧行为，逐字不变）。
      - `course_env=True`：直接继承**课程**解析后的 stage 集与环境覆盖
        （`course.stage_ids` / `course.max_ticks` / `course.difficulty` /
        `course.player.lives|level`），与训练语料同源；`eval_levels` 被忽略。
        仅支持 `course.stages` 为 list 的课程（`BcEvalBlock` 加载时已拒 range 型）。

    返回 {"epoch", "wver", "levels": [{level, n, wins, win_rate, kills_mean,
    phits_mean, pickup_mean, timeout_frac, score_mean, mean_ticks, failed,
    pickup_rate, pickup_spawn_mean, cells_mean, move_frac, panel}],
    "sentinel_only": True, "ts"}。

    B4/B5（2026-11-05）：每行带 `panel`（max_ticks / difficulty / lives / games / seed0）——
    课程顶层的 `max_ticks` 不进这条链（各图用自己关卡文件的值），所以面板读数**禁止跨
    面板相减**；`sentinel_only=True` 写死「周期 eval 只作哨兵，判决只认 200 局行为门」。
    无可用节点 / 权重推送全败 → BcEvalError（调用方决定降级或中止）。
    """
    from worker.config import load_course, resolve_level

    if nodes is None:
        from common.distribution import load_dist_config

        nodes = eval_capable_nodes(cfg if cfg is not None else load_dist_config(), log=log)
    if not nodes:
        raise BcEvalError("无可用 eval 节点（enabled + evalSupport + codeHash 一致）")

    wver = hashlib.sha256(weights_bytes).hexdigest()
    iter_id = f"bc-eval-it{it}-ep{epoch}-{wver[:8]}"
    pushed = 0
    for n in nodes:
        try:
            post_weights_cached(
                n["url"], n.get("authKey", ""), iter_id, wver, weights_bytes, kind="rollout"
            )
            pushed += 1
        except common.distribution.DistError as e:
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
    sources: list[dict[str, Any]] = []
    if course_env:
        if course is None:
            raise BcEvalError("course_env=true 但未传 course（调用点漏透传）")
        sources.append(
            {
                "name": str(getattr(course, "name", "") or "course"),
                "stage_ids": list(course.stage_ids),
                "stage_json": course.custom_stage_payload,
                "max_ticks": int(course.max_ticks),
                "difficulty": str(course.difficulty),
                "lives": course.player.lives,
                "level": course.player.level,
                "enemies": sorted(
                    {int(s.get("count") or 0) for s in course.stages if isinstance(s, dict)}
                ),
                "kind": "course",
            }
        )
    else:
        for level_name in eval_levels:
            lvl = load_course(str(resolve_level(level_name)))
            sources.append(
                {
                    "name": level_name,
                    "stage_ids": list(lvl.stage_ids),
                    "stage_json": lvl.stage_json,
                    "max_ticks": int(lvl.max_ticks),
                    "difficulty": str(lvl.difficulty),
                    "lives": getattr(lvl.player, "lives", None),
                    "level": getattr(lvl.player, "level", 0),
                    "enemies": None,
                    "kind": "levels",
                }
            )
    for src in sources:
        level_name = str(src["name"])
        tasks: list[tuple[int, int]] = [
            (stage, seed)
            for stage in src["stage_ids"]
            for seed in range(1, int(games_per_stage) + 1)
        ]
        rows: list[dict[str, Any]] = []
        failed = 0

        def _run_one(node: dict, stage: int, seed: int) -> dict[str, Any] | None:
            """单局评估；**busy 走退避重排**（2026-09-14，与 biz/bc_dispatch 同语义）。

            为什么加：节点并发槽满时回 503 busy，旧实现把它当普通失败「立刻重试一次」，
            两次都撞 busy 就丢弃该局 —— 实测 10:38 那轮 mac 的 busy 让 arena6 只跑成
            n=14/30（评估样本不足 ⇒ 均值噪声大，误判能力变化）。busy 是**限流信号**，
            不是节点故障：退避等槽位释放，且**不消耗** attempt 配额。
            """
            attempts = 0
            busy_tries = 0
            while attempts < 2:
                attempts += 1
                try:
                    manifest, _files = fetch_task(
                        node["url"],
                        node.get("authKey", ""),
                        iter_id=iter_id,
                        wver=wver,
                        stage=stage,
                        seed=seed,
                        max_ticks=int(src["max_ticks"]),
                        difficulty=str(src["difficulty"]),
                        timeout=max(120.0, float(src["max_ticks"]) * 0.5 + 120.0),
                        mode="eval",
                        policy="nn",
                        stage_json=src["stage_json"](stage) or "",
                        lives_override=src["lives"],
                        player_level=src["level"],
                    )
                    reason = validate_eval_result(manifest, wver)
                    if reason:
                        raise common.distribution.DistError(0, f"validate: {reason}")
                    return manifest
                except common.distribution.DistError as e:
                    reason = str(e.reason)
                    if "busy" in reason[:64].lower() and busy_tries < BUSY_RETRY_LIMIT:
                        busy_tries += 1
                        attempts -= 1  # busy 不消耗 attempt 配额
                        time.sleep(BUSY_BACKOFF_SEC * busy_tries)
                        continue
                    log(
                        f"[bc-eval] {node.get('id')} {level_name} s{stage} seed{seed} "
                        f"attempt {attempts} failed: {reason[:140]}"
                    )
                except Exception as e:
                    log(
                        f"[bc-eval] {node.get('id')} {level_name} s{stage} seed{seed} "
                        f"attempt {attempts} error: {type(e).__name__}: {e}"
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
        if course_max_ticks is not None and int(src["max_ticks"]) != int(course_max_ticks):
            log(
                f"[bc-eval] {level_name}: 面板 max_ticks={int(src['max_ticks'])} ≠ 课程声明 "
                f"{int(course_max_ticks)}——行内 panel 已记两个值，禁止与别的面板相减"
            )
        agg = aggregate_eval_rows(ok_rows)
        agg.update(
            {
                "level": level_name,
                "failed": failed,
                "panel": build_eval_panel(
                    level_name=level_name,
                    max_ticks=int(src["max_ticks"]),
                    difficulty=str(src["difficulty"]),
                    lives=src["lives"],
                    games_per_stage=int(games_per_stage),
                    stages=len(src["stage_ids"]),
                    course_max_ticks=course_max_ticks,
                    source=str(src["kind"]),
                    stage_ids=list(src["stage_ids"]),
                    enemies=src.get("enemies"),
                ),
            }
        )
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
        # B5：周期 eval 不是判决（判决 = 200 局行为门）；面板据此标注。
        "sentinel_only": True,
        "ts": time.time(),
    }
