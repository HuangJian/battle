"""eval_track —— 双轨日常评估：锚点/轮转种子分类 + 过拟合报警 + summary 结算（S5 第三刀，2026-09-27）。

从 `biz/eval_local.py` 整块搬出（**逐字节不动**）。这里是「双轨（anchor/rotor）怎么分段、怎么判
过拟合、summary 怎么结算」的**唯一**实现，与「怎么在本机/云上跑一局评估」（`biz/eval_local` 的
`run_eval_runner_capture` / `run_local_eval_game`）是两件事：

* **种子分段**：`EVAL_SEEDS` 池 + 锚点段（固定，跨轮配对趋势）+ 轮转段（it 轮换，防记忆化）；
  `rotor_offset` / `rotor_span` / `dual_track_seeds` / `a_eval_seed_list`（in-loop 与 evalA 共用的
  A 层语料种子，防两侧口径漂移）/ `is_anchor_seed` / `is_rotor_seed` / `split_anchor_rotor` /
  `should_dual_track`；
* **过拟合判决**：`overfit_gap_pp`（单轮 gap）+ `overfit_fires`（持续 persist 轮且 mean(锚)−mean(轮)
  ≥ 阈值）/ `_read_recent_track_wrs`（近 N 轮双轨读数）/ `_maybe_warn_overfit`（响亮 WARN）；
* **summary 结算**：`settle_eval_summary`（评估窗口结束后的对账与落账，含双轨分轨读数与技能子指标）
  + `report_winrate_safe` / `_acc` / `_ratio`。

**为什么单独成家**：这些是**读账本算趋势**的纯逻辑（不跑进程、不开子进程），与「跑一局」的执行面
分离后，`biz/gate_check` / `trainer/eval_a_once` / `remote/offline_eval` 等读者依赖的是「双轨怎么算」，
而不是「评估器怎么起」。只依赖 stdlib（`json` / `threading` / `time` / `pathlib`）+ `biz.log`。

`biz/eval_local.py` 保留 `X as X` 门面，历史 import 一行不改（「名字是契约，位置不是」）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from common.log import log

# 固定语料种子——前 2 个承载历史可比性（永不改动）；860003+ 为 goal-nn 扩展
# （arena 自评需要 20 seed/关的 trend 精度，纯增量、不影响旧口径）。
# 2026-09-13 扩到 200：c6-chip 起课程 eval_games_per_stage:200（快筛教训 §32——100 局
# SE≈±5 解析不了 −9% 量级的小效应），此前被 [:n_seeds] 静默截回 100（2026-09-06 同型坑：
# 100 曾被截成 20）。消费方一律 EVAL_SEEDS[:n_seeds] 前缀切片，前 100 不变 = 旧口径逐字节兼容。
EVAL_SEEDS = tuple(range(860001, 860201))

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

#: 缺口原因清单（G6）：summary `missing` 字段的上限——缺口可能上百（节点全挂），
#: 账上只留前 20 条（按 seed 升序）；完整缺口看 `dropped` 计数与收工「三本账」行。
MISSING_MAX = 20

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


def report_winrate_safe(wr: float | None) -> float | None:
    if wr is None:
        return None
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


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


def _missing_reasons(meta_path: Path, it: int) -> dict[tuple[int, int], str]:
    """缺口局的原因分类（G6）：扫 `dist-agent-meta.jsonl` 里同 it 的失败行。

    ① 有 `ok=False` 行 ⇒ reason 以 `record-failed` 开头记 `record-failed`，否则
    `node-failed`（attempt 打光/bootId 更换等派发面失败）；② 无失败行 ⇒ `undispatched`
    （从未派出，或成功 meta 却没落盘行的罕见怪态——保守归此）。只在缺口非空时调用
    （常态零开销）。
    """
    out: dict[tuple[int, int], str] = {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            for ln in f:
                if '"eval"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if r.get("mode") != "eval" or r.get("it") != it or r.get("ok") is not False:
                    continue
                st = r.get("stage")
                sd = r.get("seed")
                if not isinstance(st, int) or not isinstance(sd, int):
                    continue
                reason = str(r.get("reason") or "")
                if out.get((st, sd)) == "record-failed":
                    continue  # 已有更具体的原因
                out[(st, sd)] = (
                    "record-failed" if reason.startswith("record-failed") else "node-failed"
                )
    except OSError:
        pass
    return out


def settle_eval_summary(
    eval_jsonl: Path,
    key16: str,
    it: int,
    pairs: list[tuple[int, int]],
    total: int,
    landed: set[tuple[int, int]],
    wins: list[int],
    cleared_total: list[int],
    outcomes: dict[str, int],
    node_games: dict[str, int],
    jsonl_lock: threading.Lock,
    t_eval_start: float,
    rollout_winrate: float | None,
    course_fp: str | None = None,
    left_pending: int = 0,
    left_pairs: set[tuple[int, int]] | None = None,
    close_reason: str | None = None,
) -> None:
    """评估窗口结束后的对账与 summary 落账（原 dispatch_eval_round 尾部，纯函数化）。

    断点续跑口径：summary 必须聚合台账中该 (iter,wver) 的全部逐局行——只统计本次
    补跑会低估分母（it29 实测教训：补跑 20 局写出 2/20）。ledger 无行时退回本次
    现场计数（wins/cleared_total/outcomes/node_games 由 record 闭包累积）。

    门控读数（plan/course-exit-and-shutdown.md §8）：M1 顺手把**技能子指标均值**
    累加进 summary 行（kills/zero_kill_frac/playerHits/powerUps/timeout），
    与 wins 同分母、dropped 自洽——`biz/gate_check.py` 的趋势单源只读 summary 行，
    不重扫逐局行（§3.3 v0.4）。缺字段（旧 agent/旧行）一律 None = unknown，
    门侧按 unknown 处理而不是当成 0。

    双轨（plan/dual-track-eval-seeds）：summary 保持单行 wins/games 口径不变，
    另加 anchor_wr / rotor_wr / overfit_gap_pp（按 seed 落段拆分本 iter 台账行；
    非双轨轮 rotor 无局 → rotor_wr/gap 为 None）。过拟合报警看近 3 轮均值差，
    单轮 gap 只落账不报警。

    `landed` 是**落盘集**（2026-10-02，plan/eval-final-round-and-dropped S2/S3）：只有
    eval 行 append 成功才计入——本参数曾名 `seen`（认领集），改名正是为了让下一个调用方
    不再喂认领集（`dropped` 公式一字未动，φ 语义从「结算」变「落盘」）。缺口原因清单
    同时进 summary 的 `missing` 字段（G6）。

    缺口细分（2026-10-08，plan/eval-baseline-undispatched §2 P0-2/P0-3）：`dropped` 的**数值
    一字未动**（控制台「缺N」/门控趋势行的历史可比性靠它），另加三个纯新增读数——
    `never_dispatched`（本轮**从未离开 `pending`** 的局数 = `len(left_pairs)`）、
    `lost`（派出去了但没落盘 = `max(0, total - 落盘 - never_dispatched)`）、
    `carried`（`dropped` 的**第二项**残留：语料全集 − 本轮台账 —— 别轮已评/断点续跑复用；
    只为不让它混进 `lost`，不参与门判）。`left_pending` = 收工时的待办余量（含重投回队
    的局，人读用）；`close_reason` ∈ `settled`/`window`/`workers-gone`（判定单一实现：
    `worker/eval_yield.py::eval_close_reason`）。`missing` 的 reason 同步细分：`left_pairs`
    命中 ⇒ `never-dispatched`（真·从未派出），否则走 `_missing_reasons`。

    三个新参数均带缺省值（`left_pending=0` / `left_pairs=None` / `close_reason=None`）⇒
    云机调用点（`remote/offline_eval.py`，位置参数调用）**零改动**：它的
    `never_dispatched=0`、`lost` = 失败局数、`carried` = 全集口径残留 —— 云机侧没有
    「本机待办队列」这个概念，不传是**有意**的，不是遗漏。
    """
    dropped = total - len(landed)
    #: 账本里已有的 (stage, seed)（与 n 同口径的行）——`missing` 的判据（G6）。
    led_keys: set[tuple[int, int]] = set()
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
                # 只认本调度器落的局：B/C evalboard 行（`trainer/batch_runner.py` 的
                # `BatchEvalRunner._run.record` —— 引符号名不写行号，行号跨刀会漂）同为
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
                try:
                    led_keys.add((int(r.get("stage") or 0), sd))
                except (TypeError, ValueError):
                    pass
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
        n = len(landed)
        wins_v = wins[0]
        clears_v = cleared_total[0]
    dropped = max(dropped, len(pairs) - n)
    # 缺口细分（2026-10-08，plan/eval-baseline-undispatched §2 P0-2，评审 F1/F2）：
    # `dropped` 数值不动，只解释它由什么组成。`lost` 从**第一项**推，不是 `dropped - never`
    # ——后者会让第二项（别轮已评/零梯度复用）整块灌进 `lost` 变成幻数（续跑轮会报出
    # 「一局没丢却丢了 180 局」）。`left_pairs` 只收 attempts==0 的局（派过的重投回队
    # 不算「从未派出」——那是 `lost`）。
    never_set = set(left_pairs) if left_pairs else set()
    never_dispatched = len(never_set)
    not_landed = max(0, total - len(landed))
    lost = max(0, not_landed - never_dispatched)
    # 第二项残留（≥0：`pending ∩ landed = ∅` 且 `pending ⊆ todo` ⇒ never ≤ not_landed）。
    carried = max(0, dropped - never_dispatched - lost)
    # 缺口原因（G6）：只在真有缺口时扫 meta；有界 MISSING_MAX、按 seed 升序。
    missing: list[dict[str, object]] = []
    if dropped > 0:
        meta_reasons = _missing_reasons(eval_jsonl.parent / "dist-agent-meta.jsonl", it)
        gap_pairs = sorted((p for p in pairs if p not in led_keys), key=lambda p: (p[1], p[0]))
        missing = [
            {
                "stage": s,
                "seed": sd,
                # 真·从未派出（收工时的 pending 快照，attempts==0）优先于 meta 扫描；
                # `undispatched` 从此只表示「meta 有 ok=True 行却没落盘行」的罕怪态
                # （P0-3；评审 F9：字段名与 reason 不再同义）。
                "reason": "never-dispatched"
                if (s, sd) in never_set
                else meta_reasons.get((s, sd), "undispatched"),
            }
            for (s, sd) in gap_pairs[:MISSING_MAX]
        ]
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
        # 缺口细分（plan/eval-baseline-undispatched §2 P0-2）：dropped 数值不动，
        # 三个新键解释它由什么组成（恒有 dropped == never_dispatched + lost + carried）。
        "never_dispatched": never_dispatched,
        "lost": lost,
        "carried": carried,
        # 收工时的待办余量（含重投回队的局）与收工原因（新行才有；旧行缺字段 = unknown）。
        "left_pending": int(left_pending),
        "close_reason": close_reason,
        # 缺口局 + 原因（G6；上限 MISSING_MAX）。旧行缺本字段 = unknown（不是 0）。
        "missing": missing,
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
            + (
                f"（never_dispatched={never_dispatched} lost={lost} carried={carried}）"
                if dropped > 0
                else ""
            )
            + (f" clearRate={clear_rate:.1%} ({clears_v}/{n})" if clear_rate is not None else "")
            + (f" vs rollout(sampled)={rollout_winrate:.1%}" if rollout_winrate is not None else "")
            + f" outcomes={json.dumps(outcomes)}"
        )
        log(done_msg)
    else:
        log(f"[eval] it{it}: no game settled within window — nothing recorded")
