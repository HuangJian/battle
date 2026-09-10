"""eval_ingest — EvalBench Python 侧入账（plan/rl-eval-system.md §9/§10.1 T0.3）。

定位：per-tick 链路（eval_dispatch.record）与 m1 链路（eval_m1 逐局行）共用的
行构造/读取 helpers。EvalStore 归档写入的唯一路径是 TS 侧 `ingestRows`
（tools/training/evalboard/ingest.ts，经 ingest-cli.ts 调用）——本模块只产
与 `RawEvalRow` 兼容的 dict，不自建第二条写入路径（附录 P1-1）。

m1 链路逐局行落盘（D5 取 a）：m1-eval stdout JSON 自带 `perGame` 数组；
`write_m1_game_rows` 把它们写进 eval_log.jsonl（与 per-tick 链路同文件、
同 `event="eval"` 口径，wver+iter 键对账）。m1 跑的是 sim-worker 而非
export-eval-game，天然缺 playerHits/enemyHits/playerDamageTaken/stuckTicks/
pu 分类型/score——写 None，ingest 侧进覆盖率豁免清单，不伪造。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

EVAL_SEED0 = 860001
SEGMENT_LEN = 100
SEGMENT_COUNT = 16


def segment_of_seed(seed: int) -> int:
    """seed → 所属段（eval860k 空间内；空间外 -1。与 store.ts 同式）。"""
    d = seed - EVAL_SEED0
    if d < 0 or d >= SEGMENT_LEN * SEGMENT_COUNT:
        return -1
    return d // SEGMENT_LEN


def seed_space_of(seed: int) -> str:
    """860001–860100 = eval860k 段 0；其余暂归 probe0（§2.2：跨界相减由断言拒绝）。"""
    if EVAL_SEED0 <= seed < EVAL_SEED0 + SEGMENT_LEN:
        return "eval860k"
    return "probe0"


def m1_game_row(
    g: dict,
    *,
    it: int,
    wver16: str,
    policy: str,
    node: str = "m1",
) -> dict:
    """m1-eval perGame 条目 → eval_log.jsonl 行（event=eval，键与 record() 对齐）。"""
    win = bool(g.get("win"))
    return {
        "event": "eval",
        "iter": it,
        "wver": wver16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": g.get("stage"),
        "seed": g.get("seed"),
        "node": node,
        "outcome": g.get("outcome"),
        "win": 1 if win else 0,
        "cleared": 1 if g.get("cleared") else 0,
        "ticks": g.get("ticks"),
        "score": None,
        "kills": g.get("kills"),
        "enemyHits": None,
        "hitRate": None,
        "powerUpsCollected": g.get("powerUpsCollected"),
        "playerDamageTaken": None,
        "playerHits": None,
        "policy": policy,
        "enemyTotal": g.get("enemyTotal"),
        "playerDeaths": g.get("playerDeaths"),
        "playerShots": g.get("playerShots"),
        "playerLevel": g.get("playerLevel"),
        "cellsVisited": g.get("cellsVisited"),
        "firstKillTick": g.get("firstKillTick"),
        "stuckTicks": None,
        "puSpawnBomb": None,
        "puSpawnTank": None,
        "puSpawnFreeze": None,
        "puSpawnShield": None,
        "puSpawnStar": None,
        "puGotBomb": None,
        "puGotTank": None,
        "puGotFreeze": None,
        "puGotShield": None,
        "elapsedSec": None,
    }


def write_m1_game_rows(
    eval_log: Path,
    games: list[dict],
    *,
    it: int,
    wver16: str,
    policy: str,
) -> int:
    """m1 逐局行落盘（D5-a）。已有同 (wver,iter,stage,seed) 行则跳过（幂等）。"""
    done: set[tuple] = set()
    try:
        if eval_log.exists():
            for line in eval_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(r, dict)
                    and r.get("event") == "eval"
                    and r.get("wver") == wver16
                    and r.get("iter") == it
                ):
                    try:
                        done.add((int(r["stage"]), int(r["seed"])))
                    except (KeyError, TypeError, ValueError):
                        continue
    except OSError:
        pass
    n = 0
    eval_log.parent.mkdir(parents=True, exist_ok=True)
    with open(eval_log, "a", encoding="utf-8") as f:
        for g in games:
            # mypy：dict.get() 是 Any | None，显式挡掉 None（行为与原先
            # int(None) 抛 TypeError 被下面 except 捕获完全一致）。
            stage_v, seed_v = g.get("stage"), g.get("seed")
            if stage_v is None or seed_v is None:
                continue
            try:
                key = (int(stage_v), int(seed_v))
            except (TypeError, ValueError):
                continue
            if key in done:
                continue
            done.add(key)
            f.write(json.dumps(m1_game_row(g, it=it, wver16=wver16, policy=policy)) + "\n")
            n += 1
    return n


def iter_eval_rows(eval_log: Path) -> list[dict]:
    """读 eval_log.jsonl 全部 event=eval 行（ingest-cli.ts 回填/钩子共用）。"""
    out: list[dict] = []
    try:
        if not eval_log.exists():
            return out
        for line in eval_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("event") == "eval":
                out.append(r)
    except OSError:
        pass
    return out
