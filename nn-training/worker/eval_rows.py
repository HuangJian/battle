"""eval_rows —— 逐局 eval 行的 schema + `eval_log.jsonl` 账本 I/O（S5 第一刀，2026-09-27）。

从 `biz/eval_local.py` 整块搬出（连续行 287–602，**逐字节不动**）。这里是「一行评估读数长
什么样」+「两本账怎么并成一本」的**唯一**实现，与「怎么在本机跑一局评估」（`biz/eval_local`
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

#: 分析列聚合行的 event（一轮一行；与 `remote.deliver_wire.EVAL_AGG_EVENT` 必须一致）。
EVAL_AGG_EVENT = "eval_agg"

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


#: metrics v10 + aim-dodge 20 列（plan/metrics-v10-gap-columns §1、plan/aim-dodge-levers
#: §4；`src/nn/danger-metrics.ts` 同名同义）。`export-eval-game.ts` 顶层直出；旧报告/未同步
#: 节点缺键 = None（与 v8/v9 同约）。下表不含 `idleTicks` ⇒ 19 键。
#: ⚠ `idleTicks` **不在本表**：它已有自己的通道（`EVAL_NEWERA_KEYS`，与
#: moveHist/decisions/stopRuns 同行）—— 两表同含一键会在行构造点重复赋值。
EVAL_V10_KEYS = (
    "stopTicks",
    "fireHeldTicks",
    "enemyDist",
    "nearEnemy4Ticks",
    "damageBursts",
    "maxDamage120",
    "damageWhileLow",
    "encl1Ticks",
    "encl2Ticks",
    "encl3pTicks",
    "enclMax",
    "aimHits",
    "aimHitDistSum",
    "aimBricks",
    "aimIgnited",
    "aimMisses",
    "hurtWeight",
    "enclWeightTicks",
    "cornerWeightTicks",
)


def eval_v10_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 v10 十九列（缺键 = 整键省略，不写 None）。

    与 `eval_v8_fields`/`eval_v9_fields` 逐字同形、同一个理由（0 是合法读数；
    下游 `eval-course-ckpt` 以键缺席判未知）与同一条纪律：`eval_row`、
    `rl/batch_eval.py::record`、`rl/batch_runner._UnitLanes` 三处行构造点必须
    **经本函数**取数（2026-09-24 v8 提交就是 Python 两处全漏 ⇒ 日常 eval 失明）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V10_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


#: metrics v11 热线族 2 列（plan/metrics-v11-hotlane.plan.md §2；`src/nn/danger-metrics.ts`
#: 同名同义）。`export-eval-game.ts` 顶层直出；旧报告/未同步节点缺键 = None（与 v8/v9/v10 同约）。
#: ⚠ 与前批同一条纪律：本表**只此一处**（同键两表赋值会在行构造点重复赋值，行宽对账抓不到）。
#: ⚠ `pickupProxMax`（原候选 idx76）**不在本表**：它被 §4bis.4 两条杀线拦下，本批未签入。
EVAL_V11_KEYS = (
    "nearSqSum",
    "postHitLaneTicks",
)


def eval_v11_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 v11 热线族两列（缺键 = 整键省略，不写 None）。

    与 `eval_v8/v9/v10_fields` 逐字同形、同一个理由（0 是合法读数；下游
    `eval-course-ckpt` 以键缺席判未知并走 **known 计数**分母）与同一条纪律：
    `worker/eval_rows.py::eval_row`、`trainer/batch_runner.py`（两处行构造点）
    必须**经本函数**取数（2026-09-24 v8 提交就是 Python 两处全漏 ⇒ 日常 eval 失明）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V11_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


#: metrics v12 窗口三列（plan/nn-capability-upgrade.plan.md §4-B5）。
#: 与 v8–v11 同约：旧报告/未同步节点缺键 = 整键省略；`eval_row` 与
#: `trainer/batch_runner.py` 两处行构造点必须经本函数取数。
EVAL_V12_KEYS = (
    "freezeShieldWindowTicks",
    "hitsInFreezeShieldWindow",
    "deathDerivedShieldTicks",
)


def eval_v12_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 v12 窗口三列（缺键 = 整键省略，不写 None）。"""
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V12_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


#: B4 哨兵（Bf9 / 评审 D4）解冻后 60 拍内承伤份额 —— **逐局标量**，不是 per-tick
#: metrics 列，故走本账本行而非 `biz.reward_library.METRICS` 那个 79 列向量。
#: 落点 `tools/sim/export-eval-game.ts:518`（`postThawDamageRate`），顶层直出
#: `_eval_report.json`（:1406）。此前它**只进逐局报告、不进账本行** ⇒ 无法按腿聚合
#: ⇒ 评审 D4 要求的「把 B4 哨兵接成门」缺前置。基线 = 新 schema null 腿同期值。
#: ⚠ 与 v8–v12 同约：缺键 = 整键省略（0 是合法读数）；`eval_row` 与
#: `trainer/batch_runner.py` 两处行构造点必须经本函数取数。
EVAL_V13_KEYS = ("postThawDamageRate",)


def eval_v13_fields(manifest: dict | None) -> dict:
    """从 eval 报告 manifest 抽出 B4 哨兵列（缺键 = 整键省略，不写 None）。"""
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_V13_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


EVAL_MECHANISM_KEYS = ("wallPressTicks", "aliveTicks")


def eval_mechanism_fields(manifest: dict | None) -> dict:
    """机制闸两列（`wallPress/千 = wallPressTicks / aliveTicks × 1000` 的分子/分母）。

    与 v8–v13 的「缺键 = 整键省略」同约：旧 agent/旧报告没有这两列 ⇒ 不进行，
    读数方按 unknown 处理，不伪造 0（2026-11-01：E2 的机制闸必须能读到分子/分母）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in EVAL_MECHANISM_KEYS:
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


#: 与 rollout `metrics.npy` 同名、但 eval 报告**顶层已有**的 27 列
#: （plan/o1-eval-metrics-remediation §6 S1）。切片 1 先补 16 列；切片 5 让
#: `export-eval-game.ts` 直出剩余 `pickupDist` + `th*` 10 列后，本表覆盖全部
#: 27 列缺口。本表是**补齐点**，不是 METRICS 的副本。
EVAL_EXTRA_KEYS = (
    "lives",
    "baseAlive",
    "baseWallTotal",
    "baseWallIntact",
    "basePressureSum",
    "basePressureSamples",
    "clearTick",
    "enemyDist",
    "killsBasic",
    "killsFast",
    "killsPower",
    "killsArmor",
    "hitsBasic",
    "hitsFast",
    "hitsPower",
    "hitsArmor",
    # 切片 5（2026-11-08）：`pickupDist` + 基地威胁四轴/成本流/发射累计。
    "pickupDist",
    "thBreach",
    "thBreachAimed",
    "thDirect",
    "thDirectAimed",
    "thShotsAtBaseCum",
    "thBulletsAtBase",
    "thCostBreach",
    "thCostBreachAimed",
    "thCostDirect",
    "thCostDirectAimed",
)

#: 切片 5（2026-11-08）后上游已补 `pickupDist` + `th*` 10 列 ⇒ 本表清空。
#: 保留登记处（不是删机制）：将来 METRICS 新增列而上游尚未构造时，先登记在这里，
#: 列集合守卫按 `METRICS = 行键 ∪ 本表` 判定；**空表 = 90 列全在行里**。
EVAL_METRIC_EXEMPT_KEYS: tuple[str, ...] = ()


def _telemetry(manifest: dict | None) -> dict:
    """`scorable.telemetry`（缺/形状不对 = 空 dict）——与 `eval_loot_fields` 同一取法。"""
    if not isinstance(manifest, dict):
        return {}
    scorable = manifest.get("scorable")
    if not isinstance(scorable, dict):
        return {}
    t = scorable.get("telemetry")
    return t if isinstance(t, dict) else {}


def eval_extra_fields(manifest: dict | None) -> dict:
    """补齐 L2 相对 METRICS 缺的 27 列（缺键 = 整键省略，0 是合法读数）。

    与 v8–v13 同一条纪律：**只此一处**，`eval_row` 与 `trainer/batch_runner.record`
    都经 `eval_metric_fields` 取数（2026-11-08 评审 P0-2：只改 `_to_tool_row` 不改
    行构造点 ⇒ 汇总拿 None 再 `?? 0`，键存在性测试绿而假零照旧）。

    `clearTick` 是**值转换**不是复制：eval 报告用 `null` 表示「未清场」，账本统一成
    rollout 的 `-1` 哨兵。⚠ 但值仍是 `stage_clear` 的**终局** tick（含 BONUS TIME），
    不是 rollout 的 first-clear tick；只能判 presence（`clearTick >= 0`），不得当
    first-clear 用（plan §8 口径速查）。
    """
    out: dict = {}
    if not isinstance(manifest, dict):
        return out
    for k in (
        "lives",
        "baseWallTotal",
        "baseWallIntact",
        "basePressureSamples",
        "enemyDist",
    ):
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    # `baseAlive` 在报告里是 bool，metrics 列是 1/0（与 rollout `buildMetricsRow` 同型）。
    # `null` = 无读数（整键省略）；只有明确 `False` 才写 0（不得把未知读成「基地已失」）。
    alive = manifest.get("baseAlive")
    if alive is not None:
        out["baseAlive"] = 1 if alive else 0
    # 报告顶层只有 mean/samples（R11）；sum 优先取 telemetry 的原值，再退回乘积。
    total = _telemetry(manifest).get("basePressureSum")
    samples = manifest.get("basePressureSamples")
    if total is not None:
        out["basePressureSum"] = total
    else:
        mean = manifest.get("basePressureMean")
        if (
            isinstance(mean, (int, float))
            and not isinstance(mean, bool)
            and isinstance(samples, (int, float))
            and not isinstance(samples, bool)
        ):
            out["basePressureSum"] = mean * samples
    if "clearTick" in manifest:
        tick = manifest["clearTick"]
        out["clearTick"] = -1 if tick is None else tick
    # 分敌种四列：索引序 = ENEMY_KIND_ORDER = [basic, fast, power, armor]（与 METRICS 同序）。
    for prefix, key in (("kills", "killsByKind"), ("hits", "hitsByKind")):
        kinds = manifest.get(key)
        if not isinstance(kinds, list):
            continue
        for idx, name in enumerate(("Basic", "Fast", "Power", "Armor")):
            if idx < len(kinds) and kinds[idx] is not None:
                out[f"{prefix}{name}"] = kinds[idx]
    # 切片 5：`export-eval-game.ts` 顶层已直出（终局快照 / 决策步累计）。
    # `pickupDist` 的 -1 是合法哨兵（无存活拾取/玩家不在场），不是「缺键」。
    for k in (
        "pickupDist",
        "thBreach",
        "thBreachAimed",
        "thDirect",
        "thDirectAimed",
        "thShotsAtBaseCum",
        "thBulletsAtBase",
        "thCostBreach",
        "thCostBreachAimed",
        "thCostDirect",
        "thCostDirectAimed",
    ):
        v = manifest.get(k)
        if v is not None:
            out[k] = v
    return out


def eval_metric_fields(manifest: dict | None) -> dict:
    """**metrics 族的唯一聚合入口** = extra ∪ v8 ∪ v9 ∪ v10 ∪ v11 ∪ v12 ∪ v13 ∪ mechanism。

    为什么要有它（2026-10-19）：v8–v13 六个族在**两处**行构造点逐族展开
    （`eval_rows.eval_row` 与 `trainer/batch_runner.py`），加一族就要改两处、且容易漏一处
    （2026-09-24 v8 提交就是 Python 两处全漏 ⇒ 日常 eval 失明）。聚合后：
      · 新族只需在**本文件**登记一次，两个行构造点自动跟上；
      · `trainer/batch_runner.py` 从 6 行压到 1 行 —— 该文件已顶到
        `tests/test_python_loc_budget.py` 的 1000 LOC 天花板，这是**腾预算**而非新增预算。
    语义：与逐族调用**逐键等价**（同键不会被重复赋值，因为各族键集互斥）。
    """
    out: dict = {}
    for fn in (
        eval_extra_fields,
        eval_v8_fields,
        eval_v9_fields,
        eval_v10_fields,
        eval_v11_fields,
        eval_v12_fields,
        eval_v13_fields,
        eval_mechanism_fields,
    ):
        out.update(fn(manifest))
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

    这是**唯一**的行构造点：in-loop 派发器（`trainer/eval_dispatch.EvalDispatcher.record`）与
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
        # 三档拆分的唯一来源：base_destroyed / lives_exhausted（stage_clear 时为 None）。
        # 缺它则"基毁率"读不出 —— 2026-11-01 补评实测：报告里有、行里没有 ⇒ 主判据判不了。
        "lossDetail": manifest.get("lossDetail"),
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
        # 机制闸（plan/r5-next §5.3）：wallPress/千 的分子/分母；缺键 = 整键省略。
        **eval_mechanism_fields(manifest),
        # metrics v14（plan/o1-eval-metrics-remediation §6 S1）：27 列缺口全量补齐
        # （切片 1 的 16 列 + 切片 5 让 exporter 直出的 11 列；EXEMPT 已清空）。
        **eval_extra_fields(manifest),
        # metrics v8 危险暴露四列（与 batch_eval.record 同源，见 eval_v8_fields）。
        **eval_v8_fields(manifest),
        # metrics v9 命中方位 + 穿越税观测族（与 batch_eval.record 同源，见 eval_v9_fields）。
        **eval_v9_fields(manifest),
        # metrics v10 差距四族（与 batch_runner 同源，见 eval_v10_fields）。
        **eval_v10_fields(manifest),
        # metrics v11 热线族 2 列（与 batch_runner 同源，见 eval_v11_fields）。
        **eval_v11_fields(manifest),
        **eval_v12_fields(manifest),
        # B4 哨兵：解冻后承伤份额（评审 D4 接门的前置；与 batch_runner 同源）。
        **eval_v13_fields(manifest),
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


def rows_by_summary_key(rows: list[dict]) -> dict[tuple[int, str], int]:
    """逐局行按 `(iter, wver)` 计数 —— 与 summary 行**同一身份**（`eval_summary_key`）。

    "账本里这一轮到底有几行"只能从这里数：summary 的 `games` 是**结算方**的口径，
    两者在转移链上会分开（2026-11-01：云机自己写满 800 行，课程账本只到 400 行）。
    """
    out: dict[tuple[int, str], int] = {}
    for r in rows:
        if not isinstance(r, dict) or r.get("event") != "eval":
            continue
        it = r.get("iter")
        wver = str(r.get("wver") or "")
        if not isinstance(it, int) or not wver:
            continue
        out[(int(it), wver)] = out.get((int(it), wver), 0) + 1
    return out


def stamp_rows_completeness(summary: dict, n_rows: int) -> dict:
    """给 summary 行盖「逐局行是否齐」的章（plan/eval-bias §3.1 Step 3，2026-11-01）。

    `games` 是结算方的口径，`n_rows` 是**当前账本里真数得到**的行数。前者更大 ⇒ 这一点的
    分关/结局读数不可用（池化 clear 仍可信）。只加字段、不改既有键：旧读方忽略未知键。

    **为什么不放在 `settle_eval_summary`**：那里 `games` 就是同一本账数的行数，恒等；
    缺口只可能在「云机账本 → 课程账本」的转移/合并之后出现（本函数在本侧被
    `append_eval_summaries` 调用，正是那一侧的唯一入口）。
    """
    games = summary.get("games")
    want = int(games) if isinstance(games, (int, float)) and not isinstance(games, bool) else 0
    have = int(n_rows)
    summary["rows_incomplete"] = have < want
    summary["rows_have"] = have
    return summary


def incomplete_summaries(summary_rows: list[dict], rows: list[dict]) -> list[dict]:
    """声明局数 > 账本行数的 summary（分析工具据此**拒答**，不是警告）。

    判据**重算**、不读 `rows_incomplete` 标记：标记只在合并那一刻成立，之后补行/重评会变；
    读方若信旧标记，就会把已补齐的点当成坏的（或反过来）。返回按 (iter, wver) 排序，
    元素 `{"iter", "wver", "games", "rows"}`。
    """
    counts = rows_by_summary_key(rows)
    out: list[dict] = []
    for s in summary_rows:
        k = eval_summary_key(s)
        if k is None:
            continue
        games = int(_summary_games(s))
        have = counts.get(k, 0)
        if have < games:
            out.append({"iter": k[0], "wver": k[1], "games": games, "rows": have})
    return sorted(out, key=lambda g: (g["iter"], g["wver"]))


def _append_monotone(dst_jsonl: Path, rows: list[dict], *, read_rows, key_fn, stamp=None) -> int:
    """按 `(iter, wver)` **单调**并一类行：已有且 `games` 不少于新来的 ⇒ 不写。

    summary 与 `eval_agg` 共用这一套（两处各写一遍必然漂）：`read_rows` 是同类目的读法，
    `key_fn` 给身份，`stamp` 可选（summary 用它盖 `rows_incomplete`）。返回追加行数。
    """
    have: dict[tuple[int, str], float] = {}
    for r in read_rows(dst_jsonl):
        k = key_fn(r)
        if k is not None:
            have[k] = max(have.get(k, 0.0), _summary_games(r))
    fresh: list[dict] = []
    for r in rows:
        k = key_fn(r)
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
    counts = rows_by_summary_key(read_eval_rows(dst_jsonl)) if stamp is not None else {}
    dst_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_jsonl, "a", encoding="utf-8") as f:
        for r in fresh:
            k = key_fn(r)
            out = stamp(dict(r), counts.get(k, 0) if k else 0) if stamp is not None else r
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return len(fresh)


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
    return _append_monotone(
        dst_jsonl,
        rows,
        read_rows=read_eval_summary_rows,
        key_fn=eval_summary_key,
        stamp=stamp_rows_completeness,
    )


def read_eval_agg_rows(eval_jsonl: Path) -> list[dict]:
    """读账本里全部 `event:"eval_agg"` 分析列聚合行（缺失/坏行 = 跳过）。"""
    return _read_ledger_rows(eval_jsonl, EVAL_AGG_EVENT)


def eval_agg_key(r: dict) -> tuple[int, str] | None:
    """聚合行的身份 `(iter, wver)`（一行一轮；形状不合法 ⇒ None）。"""
    if not isinstance(r, dict) or r.get("event") != EVAL_AGG_EVENT:
        return None
    it = r.get("iter")
    wver = str(r.get("wver") or "")
    if not isinstance(it, int) or it < 0 or not wver:
        return None
    return (int(it), wver)


def append_eval_aggs(dst_jsonl: Path, rows: list[dict]) -> int:
    """把分析列聚合行并进账本（与 summary 同一套 `(iter, wver)` + `games` 单调规则）。"""
    return _append_monotone(dst_jsonl, rows, read_rows=read_eval_agg_rows, key_fn=eval_agg_key)


def merge_eval_rows(src_jsonl: Path, dst_jsonl: Path, log=None) -> tuple[int, int]:
    """把 `src_jsonl` 的云机 A 层评估（**逐局行 + summary 行**）并进 `dst_jsonl`。

    返回 `(新增逐局行数, 新增 summary 行数)`。

    读数回到课程账本只有这一条路：云机跑的局写在产物目录的 `eval_log.jsonl` 里，
    随 artifacts zip 回来（导入）或随补传体到达（回传）→ 并进 `tmp/<课>/eval_log.jsonl`
    ——**控制台与门判据只读这一份**。

    逐局行按 `eval_row_key` 去重（`(iter,wver,stage,seed)`；`node` 不进键：同一局在云机与
    节点各跑一次是同一份读数）；summary 按 `append_eval_summaries` 的单调规则并。

    `log`（可选，duck-typed 单参函数）打三件事：**收到 / fresh / 落盘后总计**——本模块
    stdlib-only（分层守卫钉着 import 面），所以日志口由调用方给，不在这里 import 日志层。
    这三行就是「400 的坎」的判据：收到 800 / fresh 400 ⇒ 写在写盘；收到 400 ⇒ 丢在回传。
    """
    src_rows = read_eval_rows(src_jsonl)
    games = append_eval_rows(dst_jsonl, src_rows)
    summaries = append_eval_summaries(dst_jsonl, read_eval_summary_rows(src_jsonl))
    aggs = append_eval_aggs(dst_jsonl, read_eval_agg_rows(src_jsonl))
    if log is not None:
        log(
            f"[eval-rows] 收到 {len(src_rows)} 行 / fresh {games} 行 / 聚合 {aggs} 行 / "
            f"落盘后总计 {len(read_eval_rows(dst_jsonl))} 行"
        )
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


