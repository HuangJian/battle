"""eval_rows —— 逐局 eval 行的 schema + `eval_log.jsonl` 账本 I/O（S5 第一刀，2026-09-27）。

从 `biz/eval_local.py` 整块搬出（连续行 287–602，**逐字节不动**）。这里是「一行评估读数长
什么样」+「两本账怎么并成一本」的**唯一**实现，与「怎么在本机跑一局评估」（`rl/eval_local`
的 `run_eval_runner_capture` / `run_local_eval_game`）是两件事：

* 行 schema：`eval_row`（唯一的逐局行构造点——in-loop 派发器、云机离线评估、batch 执行面
  都从它取，两条腿的读数逐字段可比）+ 三个字段抽取器（掉落三列 / Phase 0 七列 / metrics v8
  四列）；
* 账本 I/O：`read_eval_rows` / `append_eval_rows`（按 `eval_row_key` 去重）、
  `read_eval_summary_rows` / `append_eval_summaries`（按 `(iter, wver)` **单调**并）、
  `merge_eval_rows`（把云机产物账本并进课程账本）。

**为什么单独成家**（不是「多此一举的拆分」）：`remote/` 侧（`hub/queue_resume.py` 的补传合并、
`deliver_zip.py` 的产物导入、`offline_eval.py` 的行构造）本来要 `import biz.eval_local` 才拿得到
这些**纯行/账本**原语——那是「传输层伸手进本机评估运行器」的语义错位。独立之后，`remote`
依赖的是一个 stdlib-only 的纯数据模块（`json` / `time` / `pathlib`），依赖方向说实话。

本模块只依赖 stdlib（`json` / `time` / `pathlib`），无 torch、无 numpy、无传输层——可脱离
`biz.eval_local` 单测与复用。`biz/eval_local.py` 保留 `X as X` 门面，历史 import 一行不改
（「名字是契约，位置不是」）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: eval_log 掉落三列（x5⑧③：eval_dispatch/batch_eval/eval_a_once 此前未接线）。
#: 取数：manifest 顶层优先 → scorable.telemetry 回落 → None（旧 agent 缺键）。
#: - puGotOther：export-eval-game 报告顶层已有（metrics v7）。
#: - powerUpsSpawned / starsCollected：报告顶层未提（改 TS 会动 codehash 哈希集，
#:   本补齐刻意 Python-only）；已在 scorable.telemetry，新 agent 报告带 scorable。
EVAL_LOOT_KEYS = ("powerUpsSpawned", "puGotOther", "starsCollected")


def eval_loot_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出三列掉落字段（见 EVAL_LOOT_KEYS 注释）。"""
    tel: dict = {}
    if isinstance(manifest, dict):
        scorable = manifest.get("scorable")
        if isinstance(scorable, dict):
            t = scorable.get("telemetry")
            if isinstance(t, dict):
                tel = t
    out: dict = {}
    for k in EVAL_LOOT_KEYS:
        v = manifest.get(k) if isinstance(manifest, dict) else None
        if v is None:
            v = tel.get(k)
        out[k] = v
    return out


#: Phase 0 逐敌种画像七列（T5 分敌种信用；报告**顶层**，见 docs/evalboard-phase0-census.md）。
#: `export-eval-game.ts` 顶层直出（2026-09-19）；旧报告/未同步节点缺键 = None。
EVAL_CENSUS_KEYS = (
    "hitsByKind",
    "killsByKind",
    "exposureByKind",
    "firstHitKind",
    "firstKillKind",
    "killOrder",
    "killerKinds",
)


def eval_census_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 Phase 0 七列（缺键 = None，不伪造）。

    只认**顶层**：这七列与本模块 `eval_loot_fields` 的三列形态不同——它们不在
    `scorable.telemetry` 里（那是 basePressure/powerUps 一类标量），所以没有
    telemetry 回退可走；节点未同步/旧报告就是没有，交付给 ingest 计入覆盖率
    豁免清单（`PHASE0_FIELDS`）。
    """
    out: dict = {}
    for k in EVAL_CENSUS_KEYS:
        out[k] = manifest.get(k) if isinstance(manifest, dict) else None
    return out


#: metrics v8 危险暴露四列（plan/x20-dodge-avoidance §2；`src/nn/danger-metrics.ts`
#: 同名同义）。`export-eval-game.ts` 顶层直出（2026-09-24）；旧报告/未同步节点缺键 = None。
EVAL_V8_KEYS = (
    "playerHpRatio",
    "dangerTicks",
    "threatTicks",
    "dmgFirst600",
)


def eval_v8_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 v8 四列（缺键 = 整键省略，不写 None）。

    与 `eval_census_fields` 同形、但缺省语义**故意不同**：下游汇总
    （`tools/sim/eval-course-ckpt.ts`）以“键缺席”判未知（`!== undefined` 才计入
    `dmg600Known` 分母）；若写显式 None，JSON 落盘为 null，会被误计入分母、
    稀释 `clean600%`。合法的 0 值（前 600t 零承伤的干净局）必须保留，
    故只过滤 None、保留 0。
    `trainer/batch_eval.py::record`（in-loop 日常评估的行构造点）必须经本函数取数，
    与 `eval_row` 同源——两处行构造点不得各自手写字段表（2026-09-24：v8 提交只改了
    TS 侧，Python 两处全漏，日常 eval 失明）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V8_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


#: metrics v9 命中方位 + 穿越税观测族（plan/geo-threat-instrumentation §1.1/§1.3；
#: `src/nn/hit-geometry.ts` / `danger-metrics.ts` 同名同义）。`export-eval-game.ts` 顶层
#: 直出；旧报告/未同步节点缺键 = None（与 v8 同约）。
EVAL_V9_KEYS = (
    "backHits",
    "sideHits",
    "frontHitsExempt",
    "farHits",
    "geoFallback",
    "onLaneTicks",
    "onLaneExemptTicks",
    "onLaneMoveTicks",
    "onLaneHoldFireTicks",
)


def eval_v9_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 v9 九列（缺键 = 整键省略，不写 None）。

    与 `eval_v8_fields` 逐字同形、同一个理由（0 是合法读数，不能伪造缺失值；
    下游 `eval-course-ckpt` 以键缺席判未知）与同一条纪律：`eval_row` 与
    `trainer/batch_eval.py::record` 两处行构造点必须**经本函数**取数，不得各自手写
    字段表（2026-09-24 v8 提交就是 Python 两处全漏 ⇒ 日常 eval 失明）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V9_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


def eval_row(
    manifest: dict,
    *,
    it: int,
    key16: str,
    task: tuple[int, int],
    node: str,
    wall_sec: float | None = None,
) -> dict:
    """一条逐局评估账本行（`eval_log.jsonl` 的 `event:"eval"` schema）。

    这是**唯一**的行构造点：in-loop 派发器（`rl/eval_dispatch.EvalDispatcher.record`）与
    云机离线评估（`remote/offline_eval.py`）都从这里取——两条腿的读数必须逐字段可比，
    否则「离线跑的 eval 与 in-loop eval 是不是同一档」就只能靠人肉比对了。
    键的取舍与理由（掉落三列 / Phase 0 七列 / wallSec 与 elapsedSec 之别）见各字段注释。
    """
    dims = manifest.get("dims") or {}
    dim_vals = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in dims.items()}
    win = 1 if manifest.get("win") else 0
    # 全歼率（方案 A 口径，§15/P0-1）：export-eval-game 已透传 cleared——
    # 敌人全灭即算歼灭，不受 BONUS TIME 窗口截断影响。门判定全歼必须读它，
    # 否则 S3/S4a 的 timeout 局被系统性少算（eval_win 偏低 10-15pp）。
    cleared = 1 if manifest.get("cleared") else 0
    # x5⑧③：掉落三列（供给/构成可从 eval 直读，不再用 spawn 分项反推）。
    loot = eval_loot_fields(manifest)
    # Phase 0 逐敌种画像七列（T5 分敌种信用；旧报告缺键 = None）。
    census = eval_census_fields(manifest)
    return {
        "event": "eval",
        "iter": it,
        "wver": key16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": task[0],
        "seed": task[1],
        "node": node,
        "outcome": manifest.get("outcome"),
        "win": win,
        "cleared": cleared,
        "ticks": manifest.get("ticks"),
        "score": manifest.get("score"),
        "quality": manifest.get("quality"),
        "dims": dim_vals,
        "kills": manifest.get("kills"),
        "enemyHits": manifest.get("enemyHits"),
        "hitRate": manifest.get("hitRate"),
        "powerUpsCollected": manifest.get("powerUpsCollected"),
        "powerUpsSpawned": loot["powerUpsSpawned"],
        "starsCollected": loot["starsCollected"],
        "playerDamageTaken": manifest.get("playerDamageTaken"),
        # T0.4 贯通（EvalBench §3.3 🟡🟠🔴）：export-eval-game 顶层直转，
        # 缺键（旧 agent/旧报告）= None，ingest 侧进覆盖率豁免清单。
        "playerHits": manifest.get("playerHits"),
        "policy": manifest.get("policy", "nn"),
        "enemyTotal": manifest.get("enemyTotal"),
        "playerDeaths": manifest.get("playerDeaths"),
        "playerShots": manifest.get("playerShots"),
        "playerLevel": manifest.get("playerLevel"),
        "cellsVisited": manifest.get("cellsVisited"),
        "firstKillTick": manifest.get("firstKillTick"),
        "stuckTicks": manifest.get("stuckTicks"),
        # metrics v8 危险暴露四列（与 batch_eval.record 同源，见 eval_v8_fields）。
        **eval_v8_fields(manifest),
        # metrics v9 命中方位 + 穿越税观测族（与 batch_eval.record 同源，见 eval_v9_fields）。
        **eval_v9_fields(manifest),
        "puSpawnBomb": manifest.get("puSpawnBomb"),
        "puSpawnTank": manifest.get("puSpawnTank"),
        "puSpawnFreeze": manifest.get("puSpawnFreeze"),
        "puSpawnShield": manifest.get("puSpawnShield"),
        "puSpawnStar": manifest.get("puSpawnStar"),
        "puGotBomb": manifest.get("puGotBomb"),
        "puGotTank": manifest.get("puGotTank"),
        "puGotFreeze": manifest.get("puGotFreeze"),
        "puGotShield": manifest.get("puGotShield"),
        "puGotOther": loot["puGotOther"],
        "elapsedSec": manifest.get("elapsedSec"),
        "wallSec": wall_sec,
        "hitsByKind": census["hitsByKind"],
        "killsByKind": census["killsByKind"],
        "exposureByKind": census["exposureByKind"],
        "firstHitKind": census["firstHitKind"],
        "firstKillKind": census["firstKillKind"],
        "killOrder": census["killOrder"],
        "killerKinds": census["killerKinds"],
        # 新纪元死刑通道（plan §2 #9/P1-2 方案 a）：裸透传；旧报告缺键 = None，
        # 读数方按无信号处理（与 EvalCourseRow 可选字段同约）。
        "moveHist": manifest.get("moveHist"),
        "decisions": manifest.get("decisions"),
        "idleTicks": manifest.get("idleTicks"),
        "stopRuns": manifest.get("stopRuns"),
    }


def eval_row_key(r: dict) -> tuple:
    """一条逐局 eval 行的去重键 `(iter, wver, stage, seed)`（畸形行 → 哨兵键，永不被去重命中）。

    `node` **不**进键：同一局在云机与节点各跑一次是同一份读数（同权重同 seed 是确定事件），
    重复导入该被吃掉，而不是在趋势里出现两个点。
    """
    if not isinstance(r, dict) or r.get("event") != "eval":
        return (None, "", -1, -1)
    try:
        return (r.get("iter"), str(r.get("wver", "")), int(r["stage"]), int(r["seed"]))
    except (KeyError, TypeError, ValueError):
        return (None, "", -1, -1)


def eval_row_keys(rows: list[dict]) -> set[tuple]:
    """账本去重键集（`eval_row_key` 的批量形式）——合并两条腿的 eval 行时用。"""
    return {eval_row_key(r) for r in rows if isinstance(r, dict) and r.get("event") == "eval"}


def _read_ledger_rows(eval_jsonl: Path, event: str) -> list[dict]:
    """读账本里全部 `event == <event>` 行（文件缺失/坏行 = 跳过，绝不抛）。

    两类调用方（逐局行 / summary 行）共用这一份行解析——两条腿的合并都靠它，
    别再写第二个读循环（同源判据、唯一入口）。
    """
    rows: list[dict] = []
    try:
        if not eval_jsonl.exists():
            return rows
        for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("event") == event:
                rows.append(r)
    except OSError:
        pass
    return rows


def read_eval_rows(eval_jsonl: Path) -> list[dict]:
    """读账本里全部 `event:"eval"` 逐局行（文件缺失/坏行 = 跳过，绝不抛）。"""
    return _read_ledger_rows(eval_jsonl, "eval")


def read_eval_summary_rows(eval_jsonl: Path) -> list[dict]:
    """读账本里全部 `event:"eval_summary"` 行（文件缺失/坏行 = 跳过，绝不抛）。"""
    return _read_ledger_rows(eval_jsonl, "eval_summary")


def eval_summary_key(r: dict) -> tuple[int, str] | None:
    """summary 行的身份 `(iter, wver)`；形状不合法（事件不对/缺键/负 iter）⇒ None。

    与逐局行的 `eval_row_key` **分开**：summary 一个 `(iter, wver)` 只该有一行，没有
    stage/seed 可进键。`iter <= 0` 的 it0 基线行是合法 summary（控制台的配对基准），
    所以这里只拒负 iter。
    """
    if not isinstance(r, dict) or r.get("event") != "eval_summary":
        return None
    it = r.get("iter")
    wver = str(r.get("wver") or "")
    if not isinstance(it, int) or it < 0 or not wver:
        return None
    return (int(it), wver)


def _summary_games(r: dict) -> float:
    """summary 行的 `games`（缺/非数 = 0）——单调比较用，不猜内容。"""
    g = r.get("games")
    return float(g) if isinstance(g, (int, float)) and not isinstance(g, bool) else 0.0


def append_eval_summaries(dst_jsonl: Path, rows: list[dict]) -> int:
    """把 summary 行并进 `dst_jsonl`，**单调**：只在该 `(iter, wver)` 尚无 summary、
    或新来的 `games` 更多（断点续跑「先部分、后补齐」的升级）时追加；返回追加行数。

    为什么必须并（2026-09-23 用户实测：`x20-demo-mix` it50–110、每 5 轮 400 局、
    `node=cloud` 的读数全在账本里，控制台却看不见）：纯云腿（云机评估 → 回传/导入）
    **没有本地循环**，于是「summary 由课程侧按合并后的台账重算」这条退路根本不存在
    ——只并逐局行 ⇒ 指标表 eval 列 / eval 弹窗 / 开课回执 / 门判据（都只读 summary 行）
    对整段读数一律瞎眼。云机自己那份 summary 是在它同一本账本上结算出来的，口径与
    in-loop 同源（`settle_eval_summary`），并过来正是「同一份读数」。

    单调规则防的是「后到的旧 summary 覆盖先到的新 summary」：重投/重复导入天然会重发。
    已有 summary 且 `games` 不少于新来的 ⇒ 一行不写（两次调用结果相同 = 幂等）。
    """
    have: dict[tuple[int, str], float] = {}
    for r in read_eval_summary_rows(dst_jsonl):
        k = eval_summary_key(r)
        if k is not None:
            have[k] = max(have.get(k, 0.0), _summary_games(r))
    fresh: list[dict] = []
    for r in rows:
        k = eval_summary_key(r)
        if k is None:
            continue
        g = _summary_games(r)
        prev = have.get(k)
        if prev is not None and g <= prev:
            continue
        have[k] = g
        fresh.append(r)
    if not fresh:
        return 0
    dst_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_jsonl, "a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(fresh)


def merge_eval_rows(src_jsonl: Path, dst_jsonl: Path) -> tuple[int, int]:
    """把 `src_jsonl` 的云机 A 层评估（**逐局行 + summary 行**）并进 `dst_jsonl`。

    返回 `(新增逐局行数, 新增 summary 行数)`。

    读数回到课程账本只有这一条路：云机跑的局写在产物目录的 `eval_log.jsonl` 里，
    随 artifacts zip 回来（导入）或随补传体到达（回传）→ 并进 `tmp/<课>/eval_log.jsonl`
    ——**控制台与门判据只读这一份**。

    逐局行按 `eval_row_key` 去重（`(iter,wver,stage,seed)`；`node` 不进键：同一局在云机与
    节点各跑一次是同一份读数）；summary 按 `append_eval_summaries` 的单调规则并。
    """
    games = append_eval_rows(dst_jsonl, read_eval_rows(src_jsonl))
    summaries = append_eval_summaries(dst_jsonl, read_eval_summary_rows(src_jsonl))
    return (games, summaries)


def append_eval_rows(dst_jsonl: Path, rows: list[dict]) -> int:
    """把若干逐局 eval 行并进 `dst_jsonl`（按键去重），返回新增行数；文件缺失即建。"""
    src_rows = [r for r in rows if isinstance(r, dict) and r.get("event") == "eval"]
    if not src_rows:
        return 0
    have = eval_row_keys(read_eval_rows(dst_jsonl))
    fresh = [r for r in src_rows if eval_row_key(r) not in have]
    if not fresh:
        return 0
    dst_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_jsonl, "a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(fresh)


