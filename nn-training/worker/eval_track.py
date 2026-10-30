"""eval_track —— 评估账本的种子池与 summary 结算（S5 第三刀，2026-09-27）。

从 `biz/eval_local.py` 整块搬出（**逐字节不动**）。这里是「评估用哪些种子、summary 怎么结算」的
**唯一**实现，与「怎么在本机/云上跑一局评估」（`biz/eval_local` 的 `run_eval_runner_capture` /
`run_local_eval_game`）是两件事：

* **种子池**：`EVAL_SEEDS` = **池外固定池**（`EVAL_SEEDS_BASE` 起 200 个）。池基 > rollout 抽签
  上界 `ROLLOUT_SEED_MAX`（`2**30`，`biz/course.build_pairs` 的 `rng.integers(1, 2**30)`）且 < 2**31
  （32 位无符号 RNG 的安全带）⇒ **与训练种子空间永不相交**；`a_eval_seed_list` 是 in-loop /
  evalA / 云侧共用的唯一入口，**不含 it**（每轮同一批 (stage,seed)）⇒ 跨轮、跨课（同 level）
  的胜率/击杀/耗时逐点可比。2026-10-22 起取代「锚点 50 + 轮转 50」双轨（用户口径：池在训练
  空间之外，本来就没有记忆化可防）。
* **summary 结算**：`settle_eval_summary`（评估窗口结束后的对账与落账，含技能子指标）
  + `report_winrate_safe` / `_acc` / `_ratio`。

**为什么单独成家**：这些是**读账本算趋势**的纯逻辑（不跑进程、不开子进程），与「跑一局」的执行面
分离后，`worker/gate_inputs` / `trainer/eval_a_once` / `remote/offline_eval` 等读者依赖的是「口径
怎么算」，而不是「评估器怎么起」。只依赖 stdlib（`json` / `threading` / `time` / `pathlib`）+ `common.log`。

`biz/eval_local.py` 保留 `X as X` 门面，历史 import 一行不改（「名字是契约，位置不是」）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from common.log import log

# ---- 固定池外语料种子（2026-10-22，用户口径；DECISIONS §2026-10-22-fixed-offpool-eval-seeds）----
# 为什么必须「池外」：rollout 的种子是 `biz/course.build_pairs` 从 `rng.integers(1, 2**30)` 抽的
# ⇒ 落在 [1, 2**30) 的任何一个数都可能成为训练种子。评估种子若在这个区间里，就会和某条腿的
# 训练局撞上同一 (stage, seed)（读数不再独立），而且「训练空间之外」这件事无从证明。
# 取 `2_000_000_001` 起：> 2**30（1.07e9）⇒ 与 rollout 抽签区间不相交；< 2**31 ⇒ 不碰任何有符号
# 32 位假设（渲染/仿真侧 RNG 是 `seed >>> 0` 的 mulberry32，2**32 才是硬上界）。
# 池长 200：`eval_games_per_stage` 最多用到 200（c6-chip 起的精度口径），一律 `[:n_seeds]` 切片。
# **不含 it**：同一个 (stage, seed) 每轮都评同一局 ⇒ 跨轮、跨课（同 level/关卡）的胜率/击杀/耗时
# 直接可比——这是 2026-10-22 取代「锚点 50 + 轮转 50」双轨的唯一目的（轮换让每轮的局不同，
# 于是不同课程的 itN 之间根本不可比）。
ROLLOUT_SEED_MAX = 2**30
EVAL_SEEDS_BASE = 2_000_000_001
EVAL_SEEDS = tuple(range(EVAL_SEEDS_BASE, EVAL_SEEDS_BASE + 200))
assert EVAL_SEEDS_BASE > ROLLOUT_SEED_MAX, "评估池必须在 rollout 抽签区间之外"

#: 日常 A-eval 每关局数（用户口径 2026-10-22：「100，与今天双轨总量持平」）。
#: 课程写 `eval_games_per_stage: 50` 是双轨时代「锚点那一半」的配置；双轨退场后**成本与
#: 精度一字不变**——仍评 100 局/关（`EVAL_SEEDS[:100]`，固定不轮换）。其它值（120/200 /
#: 冒烟的个位数）一律照配置切片，不重映射：只有 50 这个「曾经的锚点半边」被翻译。
EVAL_DAILY_SEEDS = 100

#: 缺口原因清单（G6）：summary `missing` 字段的上限——缺口可能上百（节点全挂），
#: 账上只留前 20 条（按 seed 升序）；完整缺口看 `dropped` 计数与收工「三本账」行。
MISSING_MAX = 20


def course_logger(course: str, base_log):
    """把模块级 `log` 包成**本轮专属**的显式课程标签（plan/eval-gap-honesty §6-1）。

    为什么不能用 `_PREFIX`：eval 跑在后台线程里，活过 `prefix_scope` 的作用域 ⇒ 它每行
    读到的是别的课程线程最后置的前缀。`base_log` 由调用方传入（保持可 monkeypatch）。
    2026-10-26 从 `trainer/eval_dispatch.py` 迁来（LOC 预算）。
    """

    def _log(msg: str) -> None:
        if course:
            try:
                base_log(msg, course=course)
                return
            except TypeError:
                pass
        base_log(msg)

    return _log


def a_eval_seed_list(it: int, n_seeds: int, *, baseline: bool = False) -> tuple[int, ...]:
    """A 层语料种子（in-loop / evalA / 云侧共用的唯一入口，防几侧口径漂移）。

    **固定池前缀切片**：`EVAL_SEEDS[:n]`，与 `it` / `baseline` 无关（两个参数只为兼容调用面
    保留）。同一关每轮评同一批种子 ⇒ it 之间、同 level 的不同课程之间逐点可比；it0 基线也
    自然落在同一批局上（以前它走前缀切片、日常轮走双轨，两侧本来就不同语料）。

    `n` 的唯一翻译是 `50 → EVAL_DAILY_SEEDS`（双轨时代锚点那一半的配置 → 今天的日常档），
    其余值照配置切片。
    """
    n = int(n_seeds)
    if n == 50:
        n = EVAL_DAILY_SEEDS
    return EVAL_SEEDS[:n]


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


def lost_by_node(meta_path: Path, it: int) -> tuple[dict[str, int], dict[str, int]]:
    """丢局按节点与 errno 计数（`machine-fault` 判据的输入，plan/eval-gap-honesty D3）。

    只读 `dist-agent-meta.jsonl` 的 `ok is False` 行（有界：一轮几百行，收工时只跑一次）。
    没有 meta 行的丢局（线程直接死）不计入 ⇒ 集中度可能被低估（**偏保守**，不误报）。
    住这里而不是派发器：它和 `settle_eval_summary` 的缺口分额是同一张账。
    """
    per_node: dict[str, int] = {}
    per_errno: dict[str, int] = {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("ok") is not False:
                    continue
                if r.get("it") != it:
                    continue
                nd = str(r.get("node") or "?")
                per_node[nd] = per_node.get(nd, 0) + 1
                code = str(r.get("errno") or "")
                if code:
                    per_errno[code] = per_errno.get(code, 0) + 1
    except OSError:
        return ({}, {})
    return (per_node, per_errno)


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
    # duck-typed：调用方（remote/offline_eval）传入 `common.log_bundle.LogBundle`，本模块刻意不
    # import 它——`eval_track` 是纯逻辑底座，分层守卫不允许依赖 `common.log_bundle`（见
    # `tests/worker/test_eval_track_split.py::test_eval_track_imports_only_stdlib_and_the_log_primitive`）。
    # 标注用 `Any`（**全仓既有口径**：train_core / train_common / np_core 的 bundle 形参都这么写）
    # ——既不 import 那个类，也不给它再起一个名字（那会把「同名散落」账本顶过门槛）。
    eval_bundle: Any = None,
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

    种子（2026-10-22）：固定池外池 ⇒ 本 iter 与历史 iter 评的是**同一批局**，
    summary 的 wins/games/winRate 本身就是逐点可比的读数，不再拆锚点/轮转轨
    （`anchor_wr`/`rotor_wr`/`overfit_gap_pp` 三个字段已随双轨一起退场）。

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
    }
    with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(summary) + "\n")
    if n:
        winrate_part = (
            f"clean winRate={clean_wr:.1%} ({wins_v}/{n}, dropped={dropped})"
            + (
                f"（never_dispatched={never_dispatched} lost={lost} carried={carried}）"
                if dropped > 0
                else ""
            )
            + (f" clearRate={clear_rate:.1%} ({clears_v}/{n})" if clear_rate is not None else "")
            + (f" vs rollout(sampled)={rollout_winrate:.1%}" if rollout_winrate is not None else "")
            + f" outcomes={json.dumps(outcomes)}"
        )
        if eval_bundle is not None:
            # 并进取云机 eval 的同一行（Cat 3 日志节食）；in-loop 路径不传 bundle ⇒ 照旧单独打。
            eval_bundle.add("winRate", winrate_part)
        else:
            log(f"[eval] it{it} DONE wver={key16[:12]}… {winrate_part}")
    else:
        note = f"[eval] it{it}: no game settled within window — nothing recorded"
        if eval_bundle is not None:
            eval_bundle.note(note)
        else:
            log(note)
