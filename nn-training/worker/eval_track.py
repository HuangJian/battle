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
怎么算」，而不是「评估器怎么起」。只依赖 stdlib（`json` / `time` / `pathlib` / `collections.abc`）
+ `common.log`。

`biz/eval_local.py` 保留 `X as X` 门面，历史 import 一行不改（「名字是契约，位置不是」）。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

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
# 显式 `raise` 而不是 `assert`：这条不变量是「评估读数与训练局不相交」的地基，而 `python -O`
# 会把 assert 整段删掉（G7 禁止生产包用模块级 assert 做运行时校验）。两侧都是常量，
# import 期就炸，不会等到第一轮读数。
if EVAL_SEEDS_BASE <= ROLLOUT_SEED_MAX:
    raise RuntimeError(
        f"评估池必须在 rollout 抽签区间之外：EVAL_SEEDS_BASE={EVAL_SEEDS_BASE} "
        f"<= ROLLOUT_SEED_MAX={ROLLOUT_SEED_MAX}"
    )

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


def eval_round_ledger_state(
    eval_jsonl: Path, key16: str, it: int, pairs: list[tuple[int, int]]
) -> tuple[bool, dict[str, Any] | None]:
    """扫一遍账本：本轮语料是否已全部落在该 (iter, wver) 的逐局行里 + 该键最后一条 summary。

    过滤口径与 `settle_eval_summary` 的账本扫描**逐条一致**（同 event/wver/iter，
    排除带 `source` 的 B/C 行）——两处漂移会让「已全落」与 summary 的 `dropped`
    自相矛盾。续跑补账（2026-11-03 z6-porter.it10）靠它判「上一条 summary 的缺口
    是不是已被迟到行填平」：旧轮级闸 `preempted` 收工（2026-11-04 退役）时，在飞的本机
    局会在 summary 落盘后 1–2s 才写行；今天的同形现场是窗口到期收工后迟到的在飞局。
    两者都让 next 次续跑的 todo 为空 ⇒ 若不补对账，账本永远停在「缺 N 局」。
    """
    keys: set[tuple[int, int]] = set()
    last: dict[str, Any] | None = None
    try:
        with open(eval_jsonl, encoding="utf-8") as f:
            for ln in f:
                if '"eval' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("wver") != key16 or r.get("iter") != it:
                    continue
                ev = r.get("event")
                if ev == "eval_summary":
                    last = r
                elif ev == "eval" and "source" not in r:
                    try:
                        keys.add((int(r["stage"]), int(r["seed"])))
                    except (KeyError, TypeError, ValueError):
                        continue
    except FileNotFoundError:
        return False, None
    return all(p in keys for p in pairs), last


def reconcile_late_eval_rows(
    eval_jsonl: Path,
    key16: str,
    it: int,
    pairs: list[tuple[int, int]],
    append_row: Callable[[dict], None],
    rollout_winrate: float | None,
    course_fp: str | None,
    log_line: Callable[[str], None],
) -> int:
    """`todo` 空时的迟到行补账：补了返回补账前的缺口，没补返回 0。

    现场（2026-11-03 z6-porter.it10）：旧轮级闸 `preempted` 收工（2026-11-04 退役）时
    在飞的**本机**局会在 summary 落盘后 1–2s 才写行；今天的同形现场是窗口到期后迟到的
    在飞局。下次续跑 `todo` 已空 ⇒ 调用方直接 skip，账本永远停在「缺 N 局」。
    本轮语料已全部在该 (iter, wver) 的逐局行里、而最后一条 summary 仍记缺口
    ⇒ 补一条 `settled` 对账（数值全从账本重算，不猜）。幂等：补完后末条 summary 干净
    （`dropped=0`），再续跑不再补。

    `log_line` 由调用方注入（课程前缀取自派发侧；本模块只有全局 `log`）。
    """
    landed_all, prev = eval_round_ledger_state(eval_jsonl, key16, it, pairs)
    if not landed_all or prev is None:
        return 0
    gap = int(prev.get("dropped") or 0)
    if gap <= 0:
        return 0
    log_line(f"[eval] it{it}: 迟到局已全落账（缺 {gap} 局）— 补一条 settled 对账")
    settle_eval_summary(
        EvalSettleSpec(
            eval_jsonl=eval_jsonl,
            key16=key16,
            it=it,
            pairs=pairs,
            total=0,
            landed=set(),
            wins=[0],
            cleared_total=[0],
            outcomes={},
            node_games={},
            append_row=append_row,
            t_eval_start=time.time(),
            rollout_winrate=rollout_winrate,
            course_fp=course_fp,
            left_pending=0,
            left_pairs=set(),
            close_reason="settled",
        )
    )
    return gap


class _LedgerTally(NamedTuple):
    """`eval_log.jsonl` 里本 (iter,wver) 逐局行的聚合读数（自 `settle_eval_summary` 搬出）。"""

    keys: set[tuple[int, int]]
    wins: int
    clears: int
    outcomes: dict[str, int]
    nodes: dict[str, int]
    kills: list[float]
    phits: list[float]
    pu: list[float]
    zero_kill: int
    timeout: int
    # 环内拦截（2026-11-22，experiments §161）：[sum, count]，count = 有该字段的局数。
    ring_intr: list[float]
    ring_kills: list[float]
    ring_ttk: list[float]
    inner_frac: list[float]


def _scan_ledger(eval_jsonl: Path, key16: str, it: int) -> _LedgerTally:
    """扫账本聚合该 (iter,wver) 的逐局行（自 `settle_eval_summary` 原样搬出）。

    行过滤（event/wver/iter/source）、同 (stage,seed) 只认第一行、缺字段保守记 0 的
    口径全部不变；文件不存在返回空读数。
    """
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
    # 环内拦截：与技能子指标同规（缺字段的局不入分母，不把 unknown 读成 0）。
    led_ring_intr: list[float] = [0.0, 0.0]
    led_ring_kills: list[float] = [0.0, 0.0]
    led_ring_ttk: list[float] = [0.0, 0.0]
    led_inner_frac: list[float] = [0.0, 0.0]
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
                # 同 (stage, seed) 只认第一行（2026-11-04）：上一轮收工后仍在飞的僵尸写者会在
                # summary 落盘后补写一行，下一轮按 `done` 快照重派时又写一行 ⇒ 同轮行数超出
                # 语料（历史最多 +N）。去重放在累加之前，胜率/全歼/技能指标与 `n` 同分母。
                try:
                    st: int | None = int(r.get("stage") or 0)
                    sd: int | None = int(r.get("seed"))
                except (TypeError, ValueError):
                    st = sd = None
                if sd is not None and st is not None:
                    if (st, sd) in led_keys:
                        continue
                    led_keys.add((st, sd))
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
                # 环内拦截（旧行整键缺席 ⇒ 不入分母）。
                _acc(led_ring_intr, r.get("ringIntruders"))
                _acc(led_ring_kills, r.get("ringKills"))
                ttk = r.get("ringTtkMean")
                if isinstance(ttk, (int, float)) and float(ttk) >= 0:
                    _acc(led_ring_ttk, ttk)
                bit, tk = r.get("baseIntruderTicks"), r.get("ticks")
                if isinstance(bit, (int, float)) and isinstance(tk, (int, float)) and float(tk) > 0:
                    _acc(led_inner_frac, float(bit) / float(tk))
    except FileNotFoundError:
        pass
    return _LedgerTally(
        keys=led_keys,
        wins=led_wins,
        clears=led_clears,
        outcomes=led_outcomes,
        nodes=led_nodes,
        kills=led_kills,
        phits=led_phits,
        pu=led_pu,
        zero_kill=led_zero_kill,
        timeout=led_timeout,
        ring_intr=led_ring_intr,
        ring_kills=led_ring_kills,
        ring_ttk=led_ring_ttk,
        inner_frac=led_inner_frac,
    )


@dataclass(frozen=True, kw_only=True)
class EvalSettleSpec:
    """`settle_eval_summary` 的旋钮袋（P0-3：21 参 → 一个对象）。

    前 13 个是身份位（账本、本轮语料、现场计数与写者）；后 8 个是可选读数：课程血缘
    （`course_fp`）、收工余量（`left_pending` / `left_pairs` / `close_reason`）、观测
    （`eval_bundle` / `tail_race_grants` / `dup_games` / `frozen_sec`）。后 8 个旧版全带
    缺省值，而 `total` / `wins` / `node_games` 等又类型相近 —— 云机若按位置多传/少传一位，
    summary 的读数会静默错位。

    `eval_bundle` 仍标注 `Any`（**全仓既有口径**：调用方传 `common.log_bundle.LogBundle`，
    本模块刻意不 import 它——分层守卫见 `tests/worker/test_eval_track_split.py`）。
    """

    eval_jsonl: Path
    key16: str
    it: int
    pairs: list[tuple[int, int]]
    total: int
    landed: set[tuple[int, int]]
    wins: list[int]
    cleared_total: list[int]
    outcomes: dict[str, int]
    node_games: dict[str, int]
    append_row: Callable[[dict], None]
    t_eval_start: float
    rollout_winrate: float | None
    course_fp: str | None = None
    left_pending: int = 0
    left_pairs: set[tuple[int, int]] | None = None
    close_reason: str | None = None
    # duck-typed：调用方（remote/offline_eval）传入 `common.log_bundle.LogBundle`。
    eval_bundle: Any = None
    # P2-2 观测：竞速发牌 / 重复落盘 / 冻结累计秒。
    tail_race_grants: int = 0
    dup_games: int = 0
    frozen_sec: float = 0.0


class _SettleCounts(NamedTuple):
    """settle 的计数面：账本聚合优先，现场计数兜底。"""

    n: int
    wins: int
    clears: int
    outcomes: dict[str, int]
    node_games: dict[str, int]
    tally: _LedgerTally


class _SettleGaps(NamedTuple):
    """`dropped` 的三项细分（恒有 dropped == never + lost + carried）与缺口原因。"""

    dropped: int
    never_dispatched: int
    lost: int
    carried: int
    missing: list[dict[str, object]]


def _settle_counts(spec: EvalSettleSpec) -> _SettleCounts:
    """账本里有本 (it,wver) 的行 ⇒ 用它（断点续跑口径）；否则用现场计数。"""
    tally = _scan_ledger(spec.eval_jsonl, spec.key16, spec.it)
    if tally.outcomes:
        return _SettleCounts(
            n=sum(tally.outcomes.values()),
            wins=tally.wins,
            clears=tally.clears,
            outcomes=tally.outcomes,
            node_games=tally.nodes,
            tally=tally,
        )
    return _SettleCounts(
        n=len(spec.landed),
        wins=spec.wins[0],
        clears=spec.cleared_total[0],
        outcomes=spec.outcomes,
        node_games=spec.node_games,
        tally=tally,
    )


def _settle_gaps(spec: EvalSettleSpec, counts: _SettleCounts) -> _SettleGaps:
    """`dropped` 的数值不动，只解释它由什么组成（G6 的缺口三分 + reason 清单）。"""
    dropped = max(spec.total - len(spec.landed), len(spec.pairs) - counts.n)
    # 缺口细分（2026-10-08，plan/eval-baseline-undispatched §2 P0-2，评审 F1/F2）：
    # `dropped` 数值不动，只解释它由什么组成。`lost` 从**第一项**推，不是 `dropped - never`
    # ——后者会让第二项（别轮已评/零梯度复用）整块灌进 `lost` 变成幻数（续跑轮会报出
    # 「一局没丢却丢了 180 局」）。`left_pairs` 只收 attempts==0 的局（派过的重投回队
    # 不算「从未派出」——那是 `lost`）。
    never_set = set(spec.left_pairs) if spec.left_pairs else set()
    never_dispatched = len(never_set)
    not_landed = max(0, spec.total - len(spec.landed))
    lost = max(0, not_landed - never_dispatched)
    # 第二项残留（≥0：`pending ∩ landed = ∅` 且 `pending ⊆ todo` ⇒ never ≤ not_landed）。
    carried = max(0, dropped - never_dispatched - lost)
    # 缺口原因（G6）：只在真有缺口时扫 meta；有界 MISSING_MAX、按 seed 升序。
    missing: list[dict[str, object]] = []
    if dropped > 0:
        meta_reasons = _missing_reasons(spec.eval_jsonl.parent / "dist-agent-meta.jsonl", spec.it)
        gap_pairs = sorted(
            (p for p in spec.pairs if p not in counts.tally.keys), key=lambda p: (p[1], p[0])
        )
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
    return _SettleGaps(dropped, never_dispatched, lost, carried, missing)


def _summary_row(
    spec: EvalSettleSpec, counts: _SettleCounts, gaps: _SettleGaps
) -> dict[str, object]:
    """summary 行（字段序即落盘序；各字段口径见 `settle_eval_summary` 的 docstring）。"""
    n = counts.n
    wins_v = counts.wins
    clears_v = counts.clears
    outcomes = counts.outcomes
    node_games = counts.node_games
    tally = counts.tally
    dropped = gaps.dropped
    never_dispatched = gaps.never_dispatched
    lost = gaps.lost
    carried = gaps.carried
    missing = gaps.missing
    clean_wr = (wins_v / n) if n else None
    clear_rate = (clears_v / n) if n else None
    return {
        "event": "eval_summary",
        "iter": spec.it,
        "wver": spec.key16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec": round(time.time() - spec.t_eval_start, 1),
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
        "left_pending": int(spec.left_pending),
        "close_reason": spec.close_reason,
        "eval_tail_race_grants": int(spec.tail_race_grants),
        "eval_dup_games": int(spec.dup_games),
        "frozenSec": round(float(spec.frozen_sec), 1),
        # 缺口局 + 原因（G6；上限 MISSING_MAX）。旧行缺本字段 = unknown（不是 0）。
        "missing": missing,
        "rolloutWinRate": report_winrate_safe(spec.rollout_winrate),
        # 每节点实际结算的评估局数（勿与并发槽位混淆——首版曾误写 nd["c"]）
        "nodes": dict(sorted(node_games.items())),
        # ---- 门控技能子指标（§8；缺数据 None = unknown，门侧不当事）----
        # 分母 = 该指标有值的局数（旧 agent 缺 playerHits 时 phits_mean 为 None，
        # 不与 kills 混用一个分母——混用会把"没数据"伪装成"0 次被击中"）。
        # ---- 环内拦截（2026-11-22，experiments §161）----
        # 四个读数一组：进环次数/局、拦截率（**总和比**，不是均值比）、进环→死亡拍数、
        # 环内有敌的 tick 占比（与训练侧奖励列 `baseIntruderTicks/ticks` 同口径）。
        # 旧行整键缺席 ⇒ None（unknown），门侧按 unknown 处理。
        "ring_intr_mean": _ratio(tally.ring_intr),
        "ring_kill_rate": (
            round(tally.ring_kills[0] / tally.ring_intr[0], 4)
            if tally.ring_intr[0] > 0
            else None
        ),
        "ring_ttk_mean": _ratio(tally.ring_ttk),
        "inner_press_frac": _ratio(tally.inner_frac),
        "kills_mean": _ratio(tally.kills),
        "zero_kill_frac": (
            round(tally.zero_kill / tally.kills[1], 4) if tally.kills[1] else None
        ),
        "phits_mean": _ratio(tally.phits),
        "pickup_mean": _ratio(tally.pu),
        "timeout_frac": (round(tally.timeout / n, 4) if n else None),
        # D14 课程血缘：门按 course_fp 过滤趋势行（改课程 = 新实验，不与旧课混读）。
        "course_fp": spec.course_fp or "",
    }


def _log_settle(spec: EvalSettleSpec, counts: _SettleCounts, gaps: _SettleGaps) -> None:
    """summary 落账后的那一行（有 `eval_bundle` 时并进云机 eval 的同一行）。"""
    n = counts.n
    wins_v = counts.wins
    clears_v = counts.clears
    outcomes = counts.outcomes
    dropped = gaps.dropped
    never_dispatched = gaps.never_dispatched
    lost = gaps.lost
    carried = gaps.carried
    it, key16 = spec.it, spec.key16
    rollout_winrate = spec.rollout_winrate
    clean_wr = (wins_v / n) if n else None
    clear_rate = (clears_v / n) if n else None
    eval_bundle = spec.eval_bundle
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

def settle_eval_summary(spec: EvalSettleSpec) -> None:
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

    三个新读数（`left_pending` / `left_pairs` / `close_reason`）在 `EvalSettleSpec` 里
    带缺省值 ⇒ 云机调用点（`remote/offline_eval.py`）不传它们是**有意**的，不是遗漏：
    它的 `never_dispatched=0`、`lost` = 失败局数、`carried` = 全集口径残留 —— 云机侧没有
    「本机待办队列」这个概念。

    `append_row` 由调用方注入（`common.fs.append_jsonl` 的每路径单写者）：本模块是纯逻辑
    底座，不能 import `common.fs`（分层守卫）。两轮评估可以重叠，summary 必须与逐局行走
    **同一个写者**——否则 summary 仍可能与别轮的行互相覆盖（2026-11-02 z4-polish.it60）。
    """

    counts = _settle_counts(spec)
    gaps = _settle_gaps(spec, counts)
    spec.append_row(_summary_row(spec, counts, gaps))
    _log_settle(spec, counts, gaps)
