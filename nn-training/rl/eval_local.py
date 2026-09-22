"""eval_local —— 干净评估的纯函数/无状态工具（2026-09-02 从 eval_dispatch.py 拆出）。

干净评估（2026-08-24，用户指令）：用各节点已缓存的同权重跑固定语料贪心局。
两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料恒定 → 跨 checkpoint
配对可比（同 seed 胜负是确定事件）。本模块只含无状态工具与台账结算——
派发/对账状态机在 rl/eval_dispatch.py::EvalDispatcher（OO 化）。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW

# 单局看门狗口径：**一律通过模块属性读**（`game_watch.X`）——import 会把值抄成第二份绑定，
# 测试 patch 了 game_watch 那份、调用点还在读旧绑定就是静默的错口径。
from remote import game_watch
from rl.log import log

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 battle2（rl/ 上溯 3 层，修正 2026-09-03）

# 固定语料种子——前 2 个承载历史可比性（永不改动）；860003+ 为 goal-nn 扩展
# （arena 自评需要 20 seed/关的 trend 精度，纯增量、不影响旧口径）。
# 2026-09-13 扩到 200：c6-chip 起课程 eval_games_per_stage:200（快筛教训 §32——100 局
# SE≈±5 解析不了 −9% 量级的小效应），此前被 [:n_seeds] 静默截回 100（2026-09-06 同型坑：
# 100 曾被截成 20）。消费方一律 EVAL_SEEDS[:n_seeds] 前缀切片，前 100 不变 = 旧口径逐字节兼容。
EVAL_SEEDS = tuple(range(860001, 860201))
EVAL_ITER_SUFFIX = "ev"  # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离

# ---- 双轨日常评估（plan/dual-track-eval-seeds.plan.md）----
# 种子段注册表（P0；轮转只用池内 50-199，正式门用池外段，永不相交）：
#   860001-860050  锚点轨（固定；跨轮配对趋势，与历史逐点可比）
#   860051-860100  轮转段 0
#   860101-860150  轮转段 1
#   860151-860200  轮转段 2
# 正式门池外段：0-199 / 1000+ / 2000+ / 3000+（见 reports P0 注册表；不在此池）。
DUAL_TRACK_ANCHOR = 50
DUAL_TRACK_ROTOR = 50
# 过拟合报警：mean(锚点近3轮) − mean(轮转近3轮) ≥ 5pp 且持续 3 轮。
# 5pp 的取法（2026-09-15 订正 SE 口径，原注释把 100 局的 SE 当成了 50 局的）：
#   SE = sqrt(0.25/n)（p≈0.5 最坏情形）= 单侧 50 局 6.65pp / pooled 100 局 4.60pp /
#   pooled 200 局 3.32pp。报警量是**两轨各 3 轮均值**之差 ⇒ 有效样本各 ≈250 局、
#   SE ≈ 2.97pp，5pp ≈ 1.68σ。取整到 5pp 是防抖与灵敏度的折中，不是「50 局 SE 之上取整」。
OVERFIT_GAP_PP = 5.0
OVERFIT_PERSIST_ROUNDS = 3

# 段成员集（预计算；P2-7：用**下标集合**判定归属，不用数值区间猜）。
# 池子必须连续且严格递增——不满足就在这里响亮炸掉，而不是让门/台账静默算错段。
if tuple(sorted(set(EVAL_SEEDS))) != EVAL_SEEDS:
    raise ValueError(
        "EVAL_SEEDS 必须严格递增且无重复（双轨锚点/轮转段按**下标**切分，"
        "池子有洞或乱序会让段成员集错位）"
    )
_ANCHOR_SEED_SET = frozenset(EVAL_SEEDS[:DUAL_TRACK_ANCHOR])
_ROTOR_SEED_SET = frozenset(EVAL_SEEDS[DUAL_TRACK_ANCHOR:])


def rotor_offset(it: int) -> int:
    """轮转段在 EVAL_SEEDS 中的起始下标。it≥1，周期 3：it=1,2,3,4 → 50,100,150,50。"""
    return DUAL_TRACK_ANCHOR + ((it - 1) % 3) * DUAL_TRACK_ROTOR


def rotor_span(it: int) -> range:
    """第 it 轮轮转段的 EVAL_SEEDS 下标 range（长度恒 50）。"""
    off = rotor_offset(it)
    return range(off, off + DUAL_TRACK_ROTOR)


def dual_track_seeds(it: int) -> tuple[int, ...]:
    """第 it 轮日常双轨种子集 = 锚点 50 + 当轮轮转 50（无重叠，可无台账重构）。"""
    return EVAL_SEEDS[:DUAL_TRACK_ANCHOR] + tuple(EVAL_SEEDS[i] for i in rotor_span(it))


def a_eval_seed_list(it: int, n_seeds: int, *, baseline: bool = False) -> tuple[int, ...]:
    """A 层语料种子（in-loop 与 evalA 共用，防两侧口径漂移）。

    双轨课（n_seeds==50 且非 baseline）→ 锚点 50 + `dual_track_seeds(it)` 的当轮
    轮转 50；其余 → `EVAL_SEEDS[:n_seeds]` 前缀切片（it0 基线 / 大 n 正式前缀）。
    """
    if should_dual_track(n_seeds, baseline):
        return dual_track_seeds(it)
    return EVAL_SEEDS[:n_seeds]


def is_anchor_seed(seed: int) -> bool:
    """`seed` 是否落在锚点段（池内**下标**集合，不是数值区间猜的）。

    P2-7（2026-09-15）：原先写成数值区间 `EVAL_SEEDS[0] <= s <= EVAL_SEEDS[49]`——
    在**当前**连续种子池下与下标集合等价，但池子一旦出现空洞或非单调扩展（历史
    上扩过两次：2→100→200），数值区间会比真实成员集**更宽**，把不属于锚点的局
    悄悄算进锚点轨。改成显式成员集判断。
    """
    return int(seed) in _ANCHOR_SEED_SET


def is_rotor_seed(seed: int) -> bool:
    """`seed` 是否落在轮转段（池内下标 [50, END)；同 `is_anchor_seed` 的理由）。"""
    return int(seed) in _ROTOR_SEED_SET


def split_anchor_rotor(
    pairs: list[tuple[int, int]],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(stage,seed) 对拆成锚点轨 / 轮转轨（按 seed 落段，不看当轮 it）。"""
    anchor: list[tuple[int, int]] = []
    rotor: list[tuple[int, int]] = []
    for p in pairs:
        if is_anchor_seed(p[1]):
            anchor.append(p)
        elif is_rotor_seed(p[1]):
            rotor.append(p)
    return anchor, rotor


def should_dual_track(n_seeds: int, baseline: bool) -> bool:
    """仅日常 A-eval（n_seeds==50）拼双轨；it0 基线与更大正式前缀保持旧切片。"""
    return (not baseline) and n_seeds == DUAL_TRACK_ANCHOR


def overfit_gap_pp(anchor_wr: float | None, rotor_wr: float | None) -> float | None:
    """单轮 gap（百分点）。任一侧缺读数 → None（不伪装成 0）。"""
    if anchor_wr is None or rotor_wr is None:
        return None
    return round((float(anchor_wr) - float(rotor_wr)) * 100.0, 2)


def overfit_fires(
    anchor_wrs: Sequence[float | None],
    rotor_wrs: Sequence[float | None],
    *,
    threshold_pp: float = OVERFIT_GAP_PP,
    persist: int = OVERFIT_PERSIST_ROUNDS,
) -> bool:
    """持续 persist 轮且 mean(锚)−mean(轮) ≥ threshold_pp 才响；缺读数或轮数不足不响。"""
    n = min(len(anchor_wrs), len(rotor_wrs), persist)
    if n < persist:
        return False
    a = anchor_wrs[-persist:]
    r = rotor_wrs[-persist:]
    if any(x is None or y is None for x, y in zip(a, r, strict=True)):
        return False
    ma = sum(float(x) for x in a if x is not None) / persist
    mr = sum(float(y) for y in r if y is not None) / persist
    return (ma - mr) * 100.0 >= threshold_pp
# it0 基线评估（2026-09-12 用户）：in-loop eval 的配对基准恒为课程 bc 权重的
# 干净评估，由主循环在 rollout 收官后派发、落账前每轮重试（baseline_summary_landed）。
# 它不是训练迭代（没有 iteration 事件）⇒ 课程结束门的趋势判据按 `iter <= 0` 把它
# 排除（gate_check.read_trend_rows），控制台/配对裁判仍用它当基线。账本双向隔离：
# 基线按 iter==0（且排除 B/C source 行）去重，A-eval 按 min_iter=1 去重。
BASELINE_EVAL_ITER = 0
EVAL_TASK_ATTEMPTS = 2  # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）
EVAL_LOCAL_SLOTS_DEFAULT = 4  # 本地直跑槽位默认值（policy.evalLocalSlots 可覆写；0=禁用）
EVAL_LOCAL_RELEASE_GRACE = 300  # 距窗口截止剩这些秒时强制释放本地预留（本地失效也不空转到超时）
# 窗口到期仍在飞的局：给它们的落账宽限上界（收工不是立刻砍在飞——那些局有价值，
# 但旧实现在此 join(window + taskTimeoutSec) 会空等 4–76s/轮，故改为「在飞清空即走 +
# 本上界兜底」，2026-09-19 审计 B1）。
EVAL_INFLIGHT_GRACE_SEC = 120

# ---- eval 尾巴的收拢点与本机份额提前放行（2026-09-17 用户指令）-------------------
# 背景：in-loop eval 已藏在「下一轮 PPO」里（dispatch 排在 _serial_ppo 之前），但两处
# 仍把 eval 墙钟暴露在 PPO 之后：
#   ① `_join_eval` 在 PPO 收官后站着等尾巴（原硬编码 180s）；
#   ② 本机预留份额（policy.evalLocalSlots）的 gate 只在 `_join_eval` 置位——本机局
#      在 PPO 结束后才开跑，那段时间等于「PPO 后的第二次串行等待」。
# 处置：① **不站等任何固定秒数**——尾巴交给「下一轮 rollout 收官」这个自然边界收拢
#    （`_sweep_eval_tail`，非阻塞）：尾巴在整段采集期间自己跑完就自己落账，到边界只
#    做一次零成本观测/清账；跑不完的继续在后台（它自己的 `eval_window_sec` deadline
#    会结束它，且 summary 由该线程自己结算，不靠 join）。
#    `policy.evalJoinSoftSec` 保留为**应急旋钮**（缺省 0 = 不站等；>0 = 回到旧的
#    “PPO 后最多站等 N 秒”语义）。
# ② 本机份额按「本轮 PPO 是否占本机核心」分档放行：远端 PPO / 整轮上云 / stream
#    （PPO 已在轮内跑完）⇒ **立刻放行**；本机 PPO ⇒ 最后一个 epoch 开始即放行
#    （policy.evalLocalEarlyEpochs 控制，0 = 维持 R6 原语义：PPO 全收尾才放行）。
EVAL_JOIN_SOFT_SEC_DEFAULT = 0.0  # policy.evalJoinSoftSec 缺省值（0 = 不站等，尾巴交下一轮边界）
EVAL_LOCAL_EARLY_EPOCHS_DEFAULT = 1  # policy.evalLocalEarlyEpochs 缺省值（0=严格 R6）


def eval_join_soft_sec(policy_cfg: dict | None) -> float:
    """PPO 收官后站等上限（秒）：应急旋钮 policy.evalJoinSoftSec，**缺省 0 = 不站等**。

    正常路径不靠它：尾巴由下一轮 rollout 边界自然收拢（非阻塞）。非数值/NaN 回落默认、
    负值夹 0：配置写错不得让主链等一个荒谬的时长或直接崩。
    """
    raw = (policy_cfg or {}).get("evalJoinSoftSec", EVAL_JOIN_SOFT_SEC_DEFAULT)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return EVAL_JOIN_SOFT_SEC_DEFAULT
    if val != val:  # NaN
        return EVAL_JOIN_SOFT_SEC_DEFAULT
    return max(0.0, val)


def eval_tail_overran(t_start: float, window_sec: float, now: float) -> bool:
    """尾巴是否已跑过**它自己的** `eval_window_sec` 窗口（越过 = 异常，日志要打 WARN）。

    边界收拢不站等，但仍需要知道「这个尾巴是不是已经不正常了」：它自带的窗口是唯一的
    时间基准（不是新魔数），过期后线程会在自己的收尾路径里结算 summary 并退出。
    """
    if window_sec <= 0:
        return False
    return (float(now) - float(t_start)) > float(window_sec)


def eval_local_early_epochs(policy_cfg: dict | None) -> int:
    """本机 PPO 路径提前放行本机份额的 epoch 数（policy.evalLocalEarlyEpochs；0=不放行）。"""
    raw = (policy_cfg or {}).get("evalLocalEarlyEpochs", EVAL_LOCAL_EARLY_EPOCHS_DEFAULT)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return EVAL_LOCAL_EARLY_EPOCHS_DEFAULT
    return max(0, val)


def local_gate_release_plan(
    *,
    ppo_remote: bool,
    node_rollout: bool,
    stream_round: bool,
    early_epochs: int,
) -> str:
    """本机 eval 份额的放行档（纯函数）：'immediate' | 'last_epoch' | 'on_join'。

    - immediate：本轮本机**不跑 PPO**（远端 PPO / 整轮上云 rollout），或 stream 轮里
      PPO 已在轮内跑完 ⇒ 本机核心此刻空闲，预留尾段立即开跑（同时把节点从
      hold_for_local 的预留里放出来）。
    - last_epoch：本机 PPO ⇒ 最后一个 epoch 开始即放行（early_epochs>0）；
    - on_join：early_epochs==0 = 维持 R6 原语义（PPO 全部收尾、_join_eval 置位才放行）。
    """
    if node_rollout or ppo_remote or stream_round:
        return "immediate"
    return "last_epoch" if early_epochs > 0 else "on_join"


def early_epoch_reached(ep_done: int, epochs: int, early: int) -> bool:
    """第 ep_done 个 epoch（1 基，与 ppo_update 的 on_epoch_done 同口径）完成时，
    是否已到提前放行点。early=1、epochs=4 ⇒ 第 3 个 epoch 完成即放行（最后一个 epoch
    与本机 eval 份额并行）；与吞吐 T4 预采用的同一判据（`ep_done >= epochs - early`）。"""
    if early <= 0:
        return False
    return int(ep_done) >= max(1, int(epochs) - int(early))


def hold_for_local(pending_len: int, reserved: int, gate_set: bool, past_release: bool) -> bool:
    """节点 worker 是否应暂缓取任务、把队列尾段留给本机直跑。

    R6 补丁：课程起步期每轮仅 ~12 局，派发即被远端线程抢空，gate 在 PPO 收尾才
    放行——届时队列恒空，本地永远零参与。预留 = 节点不取最后 reserved 局。
    释放条件（任一）：gate 已放行 / 距窗口截止进入宽限期 / reserved<=0。
    防挂死：仅当 pending 超出预留量时节点才被允许继续取之外的判断在此收口，
    全预留场景由宽限强制释放兜底。
    """
    if reserved <= 0 or gate_set or past_release:
        return False
    return 0 < pending_len <= reserved


def release_local_gate_if_starved(local_gate, nodes_ok: list) -> bool:
    """无远端节点时立刻开闸本机 eval。

    2026-09-15 x3-power it30：engine_epoch 全员 mismatch → nodes_ok=[] →
    local_worker 空等 gate 到 deadline（600s 零局），drain 超时后才写 run_complete
    ——控制台「训练已完成」横幅被拖到 10 分钟后，且终轮 eval 缺失。
    """
    if local_gate is None or nodes_ok:
        return False
    local_gate.set()
    return True


def report_winrate_safe(wr: float | None) -> float | None:
    if wr is None:
        return None
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


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


def read_eval_rows(eval_jsonl: Path) -> list[dict]:
    """读账本里全部 `event:"eval"` 逐局行（文件缺失/坏行 = 跳过，绝不抛）。"""
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
            if isinstance(r, dict) and r.get("event") == "eval":
                rows.append(r)
    except OSError:
        pass
    return rows


def merge_eval_rows(src_jsonl: Path, dst_jsonl: Path) -> int:
    """把 `src_jsonl` 的逐局 eval 行并进 `dst_jsonl`（按 `eval_row_keys` 去重），返回新增行数。

    离线腿的读数回到课程账本**只有**这一条路：云机跑的局写在产物目录的 `eval_log.jsonl`
    里，随 artifacts zip 回来 → 导入时并进 `tmp/<课>/eval_log.jsonl`（板子读的就是它）。
    只并 `event:"eval"` 逐局行：`eval_summary` 由课程侧按合并后的台账重算更可信
    （云的 summary 也一起并会与本地 summary 打架——同一 iter 两行，板子按行画曲线）。

    刻意**不**重算 summary：`settle_eval_summary` 读的是同一本账本，本地下一轮
    （或控制台 evalA）自然会把合并后的分母算对。
    """
    return append_eval_rows(dst_jsonl, read_eval_rows(src_jsonl))


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


def run_eval_runner_capture(
    cmd: list[str],
    timeout_sec: float,
    cwd: str | None = None,
    *,
    label: str = "",
    log_fn: Any = None,
    kind: str = "eval",
    attempt: int = 1,
) -> subprocess.CompletedProcess[str]:
    """跑一次导出器并**捕获文本输出**（显式 UTF-8 + errors=replace）。

    必须显式给 encoding：win32 中文机的 locale 是 gbk（cp936），而 bun 的输出里带
    UTF-8 字节 ⇒ 默认解码在 `subprocess` 的 **reader 线程**里抛 UnicodeDecodeError，
    后果两条（2026-09-22 evalA 实测，65 次/100 局）：
      ① 父进程日志被整片 `Exception in thread ... _readerthread` traceback 刷屏；
      ② **captured stdout/stderr 直接丢成 None**（异常死在读线程，`communicate`
         不重抛）⇒ 下面那句失败 RuntimeError 只剩 rc、诊断信息全没（响亮错误变哑巴）。
    同 `rl/queue.bun_version` 的处置（那里早就写对了，这里是漏网的一个）。

    `cwd=None` 缺省 = 仓库根（本机/控制台路径，历史行为逐字节不变）；云机离线评估显式给
    TS 运行时树根（云上没有仓库，见 `run_local_eval_game` 的 `cwd` 形参）。

    **停滞看门狗**（2026-09-22，与 rollout 同一套口径 `remote/game_watch.py`）：用 Popen +
    轮询代替一次 `subprocess.run(timeout=)`——后者在局卡住期间什么都看不见，只能等超时；
    现在单局超过 `SLOW_GAME_WARN_SEC`（5s，正常亚秒~几秒级）就**点名**打一行 WARN（带 `s3/d7`），
    到 `timeout_sec` 才 kill 并按 `TimeoutExpired` 上抛（语义与 `subprocess.run` 逐字一致，
    包括异常体里的 captured output——诊断不能被超时吃掉）。

    软告警与硬顶同值（默认 5s = 5s）时不重复打 WARN：超时行的抛出体自己带着局身份与现场。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_POPEN_NO_WINDOW,
    )
    t0 = time.time()
    warned = False
    while True:
        try:
            out, err = proc.communicate(timeout=game_watch.GAME_POLL_SEC)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            if (
                not warned
                and not game_watch.warn_is_redundant(timeout_sec)
                and elapsed >= game_watch.SLOW_GAME_WARN_SEC
            ):
                warned = True
                (log_fn or log)(
                    game_watch.slow_warn_line(
                        kind, label or "?", elapsed, timeout_sec, attempt, " ".join(cmd[:3])
                    )
                )
            if elapsed >= timeout_sec:
                proc.kill()
                out, err = proc.communicate()  # kill 后把尾巴收干净（诊断就在这里面）
                raise subprocess.TimeoutExpired(
                    cmd, timeout_sec, output=out, stderr=err
                ) from None
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), out, err)


def run_local_eval_game(
    bun: str,
    weights_snapshot: str,
    stage: int,
    seed: int,
    out_dir: Path,
    max_ticks: int,
    difficulty: str,
    timeout_sec: float,
    wver: str,
    stage_json: str = "",
    lives_override: int | None = None,
    player_level: int | None = None,
    # T1.2 policy 透传（EvalBench C 层 God 基线；'god' → export-eval-game 真 God-AI，
    # 权重快照不需存在但调用方仍传——本地直跑与节点同报告 schema）。
    policy: str = "nn",
    # 控制台「导出 replay」（rl/eval_replays_once.py）：非空 = 整局输入录成 .replay
    # 写入该目录（export-eval-game --replay；评估语义零变化）。
    replay_dir: str = "",
    # 导出器的执行根（缺省 = 仓库根，即本机/控制台的路径）。云机离线评估传 TS 运行时树
    # 的根（`ts_code_cache/<sha>/`）——云上没有「仓库」，`tools/sim/export-eval-game.ts`
    # 只在随包下发的 TS 树里。`tools/...` 相对路径与 bun 在 PATH 上都因此成立。
    cwd: str | None = None,
    # 慢局告警的落点（缺省 = `rl.log` 的 log）。云机离线评估传 run_loop 自己的 log，
    # 让「哪一局慢」与那段训练的日志同册（否则告警在另一条流里）。
    log_fn: Any = None,
    # 这是第几次尝试（重试由调用方负责，见 `remote/offline_eval`）——只进告警行。
    attempt: int = 1,
) -> dict:
    """本机直跑一局贪心评估（与节点 agent 同一 runner / 同一报告 schema）。

    权重必须传**派发时刻的冻结快照**而非 rl_path——主循环 PPO 写回会原地覆盖
    rl_path，本地局读错版本就是对账灾难。失败抛异常交 worker 回队/放弃；
    成功返回补齐 wver/mode 回显的 manifest（mode 戳在 agent 路径由 agent 盖，
    本地路径由这里盖——validate_eval_result 的对账项）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        bun,
        "tools/sim/export-eval-game.ts",
        "--weights",
        weights_snapshot,
        "--out",
        str(out_dir),
        "--stage",
        str(stage),
        "--seed",
        str(seed),
        "--difficulty",
        difficulty,
        "--max-ticks",
        str(max_ticks),
        "--wver",
        wver,
        "--node-label",
        "local",
    ]
    # M1d 双侧同规：课程自定义关 stageJson + lives/level 覆盖（plan §6/§5.2）
    if stage_json:
        cmd += ["--stage-json", stage_json]
    if lives_override is not None:
        cmd += ["--lives-override", str(lives_override)]
    if player_level is not None:
        cmd += ["--player-level", str(player_level)]
    # T1.2：非 nn 策略透传（god 局权重文件不需要，export 侧忽略 --weights）。
    if policy and policy != "nn":
        cmd += ["--policy", policy]
    if replay_dir:
        cmd += ["--replay", replay_dir]
    t0 = time.time()
    proc = run_eval_runner_capture(
        cmd,
        timeout_sec,
        cwd=cwd,
        label=game_watch.game_label(stage, seed),
        log_fn=log_fn,
        kind="eval",
        attempt=attempt,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rc={proc.returncode} ({(proc.stderr or proc.stdout or '')[-160:]})")
    # json.loads 返回 Any；_eval_report.json 契约固定为 dict。
    manifest: dict[str, Any] = json.loads(
        (Path(out_dir) / "_eval_report.json").read_text(encoding="utf-8")
    )
    manifest.setdefault("wver", wver)
    manifest["mode"] = "eval"
    manifest["elapsedSec"] = round(time.time() - t0, 1)
    return manifest


def eval_done_keys(
    eval_jsonl: Path,
    wver16: str,
    iter_filter: int | None = None,
    *,
    min_iter: int | None = None,
) -> set[tuple[int, int]]:
    """已评估账本：eval_log.jsonl 中同 wver 的 (stage,seed) 集（断点/重启不重评）。

    `iter_filter` 非 None 时只认该 iter 的逐局行——it0 基线（`BASELINE_EVAL_ITER`）
    用，并同时排除带 `source` 字段的行（B/C evalboard 行与 EvalDispatcher 行同册
    `event:"eval"`，若 B/C 批恰好评过 bc 权重，其行不得被当成基线已评估）。
    `min_iter` 非 None 时排除 int iter < min_iter 的行——A-eval 账本传 1：把 it0
    基线行挡在 A-eval 去重之外（账本互吞只防单方向 = 另一半漏洞）；缺 `iter`
    字段的旧行与同 wver 跨 iter 复用（零梯度轮 args.out 未变 → 下轮免重评）照旧保留。
    """
    out: set[tuple[int, int]] = set()
    try:
        if eval_jsonl.exists():
            for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("event") != "eval":
                    continue
                if r.get("wver") != wver16:
                    continue
                if iter_filter is not None:
                    if r.get("iter") != iter_filter:
                        continue
                    if "source" in r:
                        continue
                if min_iter is not None:
                    it_v = r.get("iter")
                    if isinstance(it_v, int) and it_v < min_iter:
                        continue
                try:
                    out.add((int(r["stage"]), int(r["seed"])))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return out


def baseline_summary_landed(traj_dir: Path, wver16: str) -> bool:
    """it0 基线是否已落账：eval_log.jsonl 里存在**同权重指纹**的 `iter=0` summary 行。

    主循环据此决定是否（重）派基线——落账即停，未落账（进程首派、节点瞬时全挂
    被跳过、bc 换文件）每轮重试，账本去重让重试天然只补缺口。按 wver 匹配：bc
    换文件后旧基线不算数，控制台合成行取的也是最后一条 it0 summary。
    """
    eval_jsonl = traj_dir.parent / "eval_log.jsonl"
    try:
        if eval_jsonl.exists():
            for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(r, dict)
                    and r.get("event") == "eval_summary"
                    and r.get("iter") == BASELINE_EVAL_ITER
                    and r.get("wver") == wver16
                ):
                    return True
    except OSError:
        pass
    return False


def _acc(acc: list[float], v: object) -> None:
    """累加器 (sum, count)：非数值（None/旧行缺字段）整条跳过，count 不涨。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        acc[0] += float(v)
        acc[1] += 1


def _ratio(acc: list[float]) -> float | None:
    """(sum, count) → 均值；count=0 → None（unknown，不伪装成 0）。"""
    return round(acc[0] / acc[1], 4) if acc[1] else None


def _read_recent_track_wrs(
    eval_jsonl: Path,
    *,
    window: int = OVERFIT_PERSIST_ROUNDS,
    course_fp: str | None = None,
) -> tuple[list[float | None], list[float | None]]:
    """最近 window 条双轨 summary 的 (anchor_wr, rotor_wr)（旧→新）。

    不按 wver 过滤：过拟合是**跨迭代**现象（每轮权重不同、wver 不同）。
    可选按 course_fp 收窄，避免混读并行课程。
    """
    a: list[float | None] = []
    r: list[float | None] = []
    try:
        if not eval_jsonl.exists():
            return a, r
        rows: list[dict] = []
        for ln in eval_jsonl.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("event") != "eval_summary":
                continue
            if course_fp is not None and row.get("course_fp", "") != course_fp:
                continue
            # 只认已写双轨字段的行（非双轨轮 rotor_wr 为 None，不进滑动窗）。
            if row.get("rotor_wr") is None:
                continue
            rows.append(row)
        for row in rows[-window:]:
            av = row.get("anchor_wr")
            rv = row.get("rotor_wr")
            a.append(float(av) if isinstance(av, (int, float)) else None)
            r.append(float(rv) if isinstance(rv, (int, float)) else None)
    except OSError:
        pass
    return a, r


def _maybe_warn_overfit(
    eval_jsonl: Path,
    key16: str,
    a_wr: float | None,
    r_wr: float | None,
    course_fp: str | None = None,
) -> None:
    """双轨过拟合报警：近 3 轮 mean(锚)−mean(轮) ≥5pp ⇒ WARN（单轮/缺读数不报）。"""
    if a_wr is None or r_wr is None:
        return
    hist_a, hist_r = _read_recent_track_wrs(eval_jsonl, course_fp=course_fp)
    if not hist_a:
        hist_a, hist_r = [a_wr], [r_wr]
    if not overfit_fires(hist_a, hist_r):
        return
    ma = sum(x for x in hist_a[-OVERFIT_PERSIST_ROUNDS:] if x is not None) / OVERFIT_PERSIST_ROUNDS
    mr = sum(x for x in hist_r[-OVERFIT_PERSIST_ROUNDS:] if x is not None) / OVERFIT_PERSIST_ROUNDS
    log(
        f"[eval] WARN overfit gap {((ma - mr) * 100):.1f}pp ≥ {OVERFIT_GAP_PP:.0f}pp "
        f"sustained {OVERFIT_PERSIST_ROUNDS} rounds (wver={key16[:12]}…) — "
        f"prefer rotor_wr for checkpoint picks"
    )


def settle_eval_summary(
    eval_jsonl: Path,
    key16: str,
    it: int,
    pairs: list[tuple[int, int]],
    total: int,
    seen: set[tuple[int, int]],
    wins: list[int],
    cleared_total: list[int],
    outcomes: dict[str, int],
    node_games: dict[str, int],
    jsonl_lock: threading.Lock,
    t_eval_start: float,
    rollout_winrate: float | None,
    course_fp: str | None = None,
) -> None:
    """评估窗口结束后的对账与 summary 落账（原 dispatch_eval_round 尾部，纯函数化）。

    断点续跑口径：summary 必须聚合台账中该 (iter,wver) 的全部逐局行——只统计本次
    补跑会低估分母（it29 实测教训：补跑 20 局写出 2/20）。ledger 无行时退回本次
    现场计数（wins/cleared_total/outcomes/node_games 由 record 闭包累积）。

    门控读数（plan/course-exit-and-shutdown.md §8）：M1 顺手把**技能子指标均值**
    累加进 summary 行（kills/zero_kill_frac/playerHits/powerUps/timeout），
    与 wins 同分母、dropped 自洽——`rl/gate_check.py` 的趋势单源只读 summary 行，
    不重扫逐局行（§3.3 v0.4）。缺字段（旧 agent/旧行）一律 None = unknown，
    门侧按 unknown 处理而不是当成 0。

    双轨（plan/dual-track-eval-seeds）：summary 保持单行 wins/games 口径不变，
    另加 anchor_wr / rotor_wr / overfit_gap_pp（按 seed 落段拆分本 iter 台账行；
    非双轨轮 rotor 无局 → rotor_wr/gap 为 None）。过拟合报警看近 3 轮均值差，
    单轮 gap 只落账不报警。
    """
    dropped = total - len(seen)
    led_wins = 0
    led_clears = 0
    led_outcomes: dict[str, int] = {}
    led_nodes: dict[str, int] = {}
    # 技能子指标累加器（sum, count）——count 与分母 n 分离：旧行缺字段时该指标 None。
    led_kills: list[float] = [0.0, 0.0]
    led_phits: list[float] = [0.0, 0.0]
    led_pu: list[float] = [0.0, 0.0]
    led_zero_kill = 0
    led_timeout = 0
    # 双轨拆分（锚点 50 / 轮转 50-199）：只统计本 (iter,wver) 台账行。
    led_anchor_wins = 0
    led_anchor_n = 0
    led_rotor_wins = 0
    led_rotor_n = 0
    try:
        with open(eval_jsonl, encoding="utf-8") as jf:
            for ln in jf:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                # 只认本调度器落的局：B/C evalboard 行（`batch_eval.py:703`）同为
                # `event:"eval"`，且 iter=0 畸形批能撞上同 (iter,wver) —— 不滤 source
                # 会把 B/C 的局混进本臂胜率与双轨拆段（P2-7，2026-09-15）。
                if r.get("event") != "eval" or r.get("wver") != key16 or r.get("iter") != it:
                    continue
                if "source" in r:
                    continue
                led_wins += 1 if r.get("win") else 0
                # 全歼率（方案 A 口径）：旧行（cleared 缺省）视为未全歼——新 schema
                # 上线前的行只有本地局有 cleared，此处保守记 0，聚合以新行口径为准。
                led_clears += 1 if r.get("cleared") else 0
                oc = str(r.get("outcome") or "?")
                led_outcomes[oc] = led_outcomes.get(oc, 0) + 1
                ndm = str(r.get("node") or "?")
                led_nodes[ndm] = led_nodes.get(ndm, 0) + 1
                _acc(led_kills, r.get("kills"))
                if isinstance(r.get("kills"), (int, float)) and float(r["kills"]) <= 0:
                    led_zero_kill += 1
                _acc(led_phits, r.get("playerHits"))
                _acc(led_pu, r.get("powerUpsCollected"))
                # 2026-09-13 P0 止血：rollout/探针落盘的 outcome 值是 **`max_ticks`**，
                # 不是 `timeout`（`timeout` 只存在于 reward 引擎的 `is_timeout` 虚拟符号里，
                # 它才把两者都当超时）。旧判据 `oc == "timeout"` 恒 False ⇒ `timeout_frac`
                # 永远是 0.0 ⇒ G7（course_valid）形同虚设（c6-bonus 实测真超时 11/100，
                # summary 仍报 0.0）。口径同 `evalboard/stats.ts`：已清场的局不算超时。
                if oc in ("timeout", "max_ticks") and not r.get("cleared"):
                    led_timeout += 1
                try:
                    sd = int(r.get("seed"))
                except (TypeError, ValueError):
                    continue
                w = 1 if r.get("win") else 0
                if is_anchor_seed(sd):
                    led_anchor_n += 1
                    led_anchor_wins += w
                elif is_rotor_seed(sd):
                    led_rotor_n += 1
                    led_rotor_wins += w
    except FileNotFoundError:
        pass
    if led_outcomes:
        n = sum(led_outcomes.values())
        wins_v = led_wins
        clears_v = led_clears
        outcomes = led_outcomes
        node_games = led_nodes
    else:
        n = len(seen)
        wins_v = wins[0]
        clears_v = cleared_total[0]
    dropped = max(dropped, len(pairs) - n)
    clean_wr = (wins_v / n) if n else None
    clear_rate = (clears_v / n) if n else None
    # 双轨读数：无台账行时退回现场计数不可拆段 → 保持 None（调用方不写台账路径下
    # 不假装有分轨）；有台账行才按 seed 落段。
    a_wr = (led_anchor_wins / led_anchor_n) if led_anchor_n else None
    r_wr = (led_rotor_wins / led_rotor_n) if led_rotor_n else None
    gap = overfit_gap_pp(a_wr, r_wr)
    summary = {
        "event": "eval_summary",
        "iter": it,
        "wver": key16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec": round(time.time() - t_eval_start, 1),
        "games": n,
        "wins": wins_v,
        "winRate": round(clean_wr, 4) if clean_wr is not None else None,
        # 全歼率（敌人全灭即算，不受 BONUS TIME 截断影响）——S3/S4a 门判定读它。
        "clears": clears_v,
        "clearRate": round(clear_rate, 4) if clear_rate is not None else None,
        "outcomes": outcomes,
        "dropped": dropped,
        "rolloutWinRate": report_winrate_safe(rollout_winrate),
        # 每节点实际结算的评估局数（勿与并发槽位混淆——首版曾误写 nd["c"]）
        "nodes": dict(sorted(node_games.items())),
        # ---- 门控技能子指标（§8；缺数据 None = unknown，门侧不当事）----
        # 分母 = 该指标有值的局数（旧 agent 缺 playerHits 时 phits_mean 为 None，
        # 不与 kills 混用一个分母——混用会把"没数据"伪装成"0 次被击中"）。
        "kills_mean": _ratio(led_kills),
        "zero_kill_frac": (round(led_zero_kill / led_kills[1], 4) if led_kills[1] else None),
        "phits_mean": _ratio(led_phits),
        "pickup_mean": _ratio(led_pu),
        "timeout_frac": (round(led_timeout / n, 4) if n else None),
        # D14 课程血缘：门按 course_fp 过滤趋势行（改课程 = 新实验，不与旧课混读）。
        "course_fp": course_fp or "",
        # ---- 双轨日常评估（wins/games 总口径不变；分轨只读，门控兼容）----
        "anchor_wr": round(a_wr, 4) if a_wr is not None else None,
        "rotor_wr": round(r_wr, 4) if r_wr is not None else None,
        "overfit_gap_pp": gap,
    }
    with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(summary) + "\n")
    _maybe_warn_overfit(eval_jsonl, key16, a_wr, r_wr, course_fp=course_fp or "")
    if n:
        done_msg = (
            f"[eval] it{it} DONE wver={key16[:12]}… clean winRate="
            f"{clean_wr:.1%} ({wins_v}/{n}, dropped={dropped})"
            + (f" clearRate={clear_rate:.1%} ({clears_v}/{n})" if clear_rate is not None else "")
            + (f" vs rollout(sampled)={rollout_winrate:.1%}" if rollout_winrate is not None else "")
            + f" outcomes={json.dumps(outcomes)}"
        )
        log(done_msg)
    else:
        log(f"[eval] it{it}: no game settled within window — nothing recorded")
