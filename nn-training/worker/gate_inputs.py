"""gate_inputs —— 课程结束门求值器的「输入读数面」（S5 第十五刀，2026-09-27）。

一家持有判决输入的**模型 + 全部显式读取入口**：`EvalRow`（settled `eval_summary` 行的
判决视图）/ `BudgetInfo`（预算门输入）· 行归一化 · trend 读取 · run_start / iteration
事件扫描 · G12 override 文件。判决面 `biz.gate_judges` 与引擎 `biz.gate_check` 只从这里
取输入——依赖单向（本模块是 **stdlib-only 叶子**，零仓内 import）。

读数侧红线（与 `biz/gate_check` 同源）：① 无隐式文件读取——`read_*` / `load_*` 是唯一
入口；② 确定性——时间不进模块状态，事件时间由账本行解析或调用方注入。
"""


from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: override 文件里可写的判决（G12 人工改向，记 DECISIONS）。
_OVERRIDE_VERDICTS: frozenset[str] = frozenset(
    {"ADVANCE", "REMEDIATE", "STOP", "PAUSE", "ABORT", "HOLD"}
)

#: run_start 事件时间格式（biz/events.py:66）。
_TS_FMT = "%Y-%m-%d %H:%M:%S"


class GateOverrideError(ValueError):
    """override 文件存在但非法（§4.6：响亮报错，CLI exit 2，绝不静默忽略）。"""


@dataclass(frozen=True)
class EvalRow:
    """一条 settled `eval_summary` 行的判决视图（缺字段 = None = unknown）。"""

    iter: int
    wver: str
    games: int
    wins: int
    win_rate: float | None
    kills_mean: float | None = None
    zero_kill_frac: float | None = None
    phits_mean: float | None = None
    pickup_mean: float | None = None
    timeout_frac: float | None = None
    seed_fp: str | None = None
    #: 评估缺口（plan/eval-gap-honesty D4，2026-10-18）：分母 = `games + dropped`
    #: （与控制台「缺N / 共N」同口径）。旧行缺键 ⇒ 0 / None ⇒ 判决逐位不变。
    dropped: int = 0
    close_reason: str | None = None

    #: metric 名 → 取值（plateau/hack 的窗口统计走它，避免重复 getattr）。
    def metric(self, name: str) -> float | None:
        if name == "win_rate":
            return self.win_rate
        if name == "timeout_frac":
            return self.timeout_frac
        return getattr(self, name, None)


@dataclass(frozen=True)
class BudgetInfo:
    """G5 预算门输入（§4.7：时间由 `now` 注入；基线 = 首条 run_start）。"""

    #: 首条 run_start 事件的 epoch 秒（None = 未知，预算门降级为 unknown）。
    started_at: float | None = None
    max_hours: float = 0.0
    iters: int = 0
    cur_iter: int = 0
    #: 累计**有效**训练秒（Σ ppo_cloud_sec，跨重启从 iteration 事件重算）。
    #: G13（duty 事故熔断）的分子（2026-09-11 评审新增）。
    train_sec: float = 0.0
    #: 累计**样本通过量** Σ(samples × epochs)：ADVANCE 的"证据充分性"判据
    #: （spec.min_train_samples，2026-09-11）。
    train_samples: float = 0.0
    #: G13 duty 的分母基线：**首个完成迭代的结束时刻**（2026-09-11 事故修复 §385）。
    #: 开腿到首个迭代完成之间的冷启动/换挡是常态起步成本，不算事故——否则 it1 的
    #: 债会终身稀释占空比、门自激停车（2026-09-11 c6b-margin：0.06→0.13 每个评估轮必停）。
    #: None → 回退 `started_at` 终身口径（无 duty 数据的旧调用方/测试，行为不变）。
    duty_baseline_ts: float | None = None
    #: 已完成 iteration 事件数（G13 最小样本守卫：不足不判，防开门即响）。
    duty_events: int = 0

    def wall_sec(self, now: float) -> float | None:
        """墙钟秒（自首条 run_start 起）；基线不可知 → None。"""
        if self.started_at is None:
            return None
        return max(0.0, float(now) - self.started_at)

    def exhaust_reason(self, now: float) -> str | None:
        """返回超预算的原因；None = 未超预算（或基线不可知）。"""
        if self.max_hours > 0 and self.started_at is not None:
            hours = (now - self.started_at) / 3600.0
            if hours >= self.max_hours:
                return f"已跑 {hours:.2f}h ≥ max_hours {self.max_hours}"
        if self.iters > 0 and self.cur_iter >= self.iters:
            return f"iter {self.cur_iter}/{self.iters} 达上限"
        return None


# --------------------------------------------------------------------------- 行归一化


#: 「缺口大到不算读数」的阈值（与 `worker/eval_local.py::BASELINE_DROP_FRAC_MAX` 同值同分母：
#: 分母 = `games + dropped`）。为什么是 2%：单局节点失败（1/400 = 0.25%）不应让整轮读数作废；
#: 而 400 局缺 8 局以上（20.7% 的 y22-lr it5 就是极端例）胜率的 SE 已翻过门边。
#: `gate_inputs` 是 **stdlib-only 叶子**（头注的零仓内 import 契约）⇒ 不得 import worker/，
#: 只许与那个常量保持同值（改了这里就要同步改那里，两处都有注释指着对方）。
GATE_DROP_FRAC_MAX = 0.02


def _reject_reason(row: EvalRow) -> str | None:
    """这条读数能不能进数值判决（D4）：None = 能。

    两个拒收面：
    · `close_reason == "machine-fault"` —— 缺口**配比不可信**（集中在一台机器），单列硬拒；
    · 缺口占 `games + dropped` 超 `GATE_DROP_FRAC_MAX` —— 样本量不足到能判 3pp 级差异。
    为什么不再按「`close_reason != settled` 一刀切」（评审 F3）：云腿 summary 从不传
    `close_reason`（`remote/offline_eval.py`），本地 `window` 收工也是「结构完整、只缺尾巴」
    —— 按状态拒会把缺 1 局的健康读数整行拒掉，趋势窗口凭空断档。**量的诚实**才是判据。
    """
    if row.close_reason == "machine-fault":
        return "机器级故障收工（缺口配比不可信）"
    if row.dropped > 0 and row.dropped / (row.games + row.dropped) > GATE_DROP_FRAC_MAX:
        return f"缺口 {row.dropped}/{row.games + row.dropped} 超阈值"
    return None


def _build_row(r: Mapping[str, Any]) -> EvalRow | None:
    """summary 行 → EvalRow（**纯映射**，不做准入；准入在 `_row_from_summary`）。

    非 eval_summary / 无 games 的行丢弃。

    胜率口径（2026-10-22，DECISIONS §2026-10-22-fixed-offpool-eval-seeds）：门读
    `winRate`。评估语料是**固定池外池**（每轮/每门同 level 的课同 (stage,seed)）⇒
    这个读数本身就跨轮配对可比，不需要（也不再存在）「锚点轨」特例；旧行的
    `anchor_wr` 只留在历史行里，门不再按它分流（口径切换的一次性台阶记在决定里）。
    这里是门内**唯一**的胜率入口 ⇒ G1 / G10 / plateau / pool 全部同源。
    """
    if r.get("event") != "eval_summary":
        return None
    games = r.get("games")
    wins = r.get("wins")
    wr = r.get("winRate")
    if not isinstance(games, int) or games <= 0:
        return None
    if not isinstance(wins, int):
        wins = 0
    if isinstance(wr, (int, float)):
        win_rate: float | None = float(wr)
    else:
        win_rate = wins / games
    timeout_frac = r.get("timeout_frac")
    if timeout_frac is None:
        outcomes = r.get("outcomes")
        if isinstance(outcomes, dict):
            t = outcomes.get("timeout")
            if isinstance(t, (int, float)) and games:
                timeout_frac = float(t) / games
    seed_fp = r.get("seed_fp")
    if seed_fp is None:
        for k in ("rotateSeed", "seed0"):
            if r.get(k) is not None:
                seed_fp = str(r[k])
                break
    return EvalRow(
        iter=int(r.get("iter") or 0),
        wver=str(r.get("wver") or ""),
        games=games,
        wins=wins,
        win_rate=win_rate,
        kills_mean=_num(r.get("kills_mean")),
        zero_kill_frac=_num(r.get("zero_kill_frac")),
        phits_mean=_num(r.get("phits_mean")),
        pickup_mean=_num(r.get("pickup_mean")),
        timeout_frac=_num(timeout_frac),
        seed_fp=str(seed_fp) if seed_fp is not None else None,
        dropped=int(r.get("dropped") or 0),
        close_reason=str(r["close_reason"]) if r.get("close_reason") else None,
    )


def _row_from_summary(r: Mapping[str, Any]) -> EvalRow | None:
    """summary 行 → **准入后**的 EvalRow（单一漏斗：G1/G10/plateau/pool/跨课全走这里）。"""
    row = _build_row(r)
    if row is None or _reject_reason(row) is not None:
        return None
    return row


def rejected_summaries(
    trend_rows: Sequence[Mapping[str, Any]], course_fp: str = ""
) -> tuple[dict[str, Any], ...]:
    """被准入拒收的 summary 行（给「响亮一行」用：`gate_inputs` 保持零 logging）。

    判据与 `_row_from_summary` 同一个（`_reject_reason`）——不会两边漂。
    """
    out: list[dict[str, Any]] = []
    for raw in trend_rows:
        row = _build_row(raw)
        if row is None:
            continue
        fp = str(raw.get("course_fp") or "")
        if course_fp and fp and fp != course_fp:
            continue
        why = _reject_reason(row)
        if why is not None:
            out.append(
                {
                    "iter": row.iter,
                    "wver": row.wver,
                    "dropped": row.dropped,
                    "games": row.games,
                    "close_reason": row.close_reason,
                    "reason": why,
                }
            )
    return tuple(out)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def normalize_rows(
    trend_rows: Sequence[Mapping[str, Any]],
    course_fp: str = "",
) -> tuple[EvalRow, ...]:
    """summary 行 → 去重后的时间序（升序）EvalRow。

    去重键 `(course_fp, wver)`（§4.4）：同 wver 重复出现只保留**后**一条（文件
    序即时间序），崩溃重放不会虚增连续通过计数。旧行缺 `course_fp` → 按"全匹配"
    兼容（与 §8 的 eval_done_keys 口径一致）。
    """
    kept: dict[tuple[str, str], EvalRow] = {}
    for raw in trend_rows:
        row = _row_from_summary(raw)
        if row is None:
            continue
        fp = str(raw.get("course_fp") or "")
        if course_fp and fp and fp != course_fp:
            continue
        kept[(fp, row.wver)] = row
    rows = sorted(kept.values(), key=lambda r: (r.iter, r.wver))
    return tuple(rows)


def read_trend_rows(
    jsonl_path: Path, course_fp: str = "", limit: int = 0, include_baseline: bool = False
) -> tuple[dict, ...]:
    """读 `eval_log.jsonl`（per-tick 课程）里的 eval_summary 行；文件缺失返回 ()。

    `include_baseline=True` 时**连 it0 基线行一起返回**（它们默认被滤掉，见下）——
    §5 的干烧熔断需要「本腿起点」（it0 = 课程 bc 权重的干净评估）当基线，而判据必须
    与训练进展同源：所以在这里开一扇门，而不是在别处再写一个读文件的循环（同源判据、
    唯一入口）。

    **it0 基线行（`iter <= 0`）不进趋势**：那一行是课程 bc 权重的干净评估（主循环在
    首次 rollout 收官后补派，见 `loop_core._maybe_dispatch_baseline_eval`），它是监控/
    配对基准而不是训练进展——让它进判据会虚增 sustain 的"连续通过"计数、也会把 plateau
    的上升趋势起点拉回 PPO 前。控制台（`console/iters.ts`）不过滤，仍按它当配对基准。

    `limit > 0` 时只读末尾 `limit` 条 summary 行（大文件防护）。
    """
    out: list[dict] = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("event") != "eval_summary":
                    continue
                it = r.get("iter")
                if isinstance(it, int) and it <= 0 and not include_baseline:
                    continue  # it0 基线（bc 权重）：只作监控/配对参照，不进判据
                fp = str(r.get("course_fp") or "")
                if course_fp and fp and fp != course_fp:
                    continue
                out.append(r)
    except OSError:
        return ()
    if limit > 0:
        out = out[-limit:]
    return tuple(out)


def first_run_start_ts(jsonl_path: Path) -> float | None:
    """首条 run_start 事件的 epoch 秒（§7：预算基线跨重启累计，读第一条而非最后）。

    最后一条 = 每次重启都续命（ds-P1-3 教训）；第一条才是课程真正的开跑时刻。
    """
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "run_start":
                    ts = r.get("time")
                    if isinstance(ts, str):
                        try:
                            return time.mktime(time.strptime(ts, _TS_FMT))
                        except ValueError:
                            return None
                    return None
    except OSError:
        return None
    return None


def first_iter_end_ts(jsonl_path: Path) -> float | None:
    """首个完成迭代（iteration 事件）的结束时刻（epoch 秒）；无 → None。

    G13 duty 的分母基线（§385）：开腿到首个迭代完成之间的冷启动/换挡算「起步
    热身」，不计入事故分母——2026-09-11 c6b-margin 事故里 it1 的 47min 冷启动
    不该让整条腿的「占空比」终身被稀释、每评估轮必停。
    """
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "iteration":
                    ts = r.get("time")
                    if isinstance(ts, str):
                        try:
                            return time.mktime(time.strptime(ts, _TS_FMT))
                        except ValueError:
                            continue
    except OSError:
        return None
    return None


def count_iteration_events(jsonl_path: Path) -> int:
    """已完成 iteration 事件数（G13 最小样本守卫的分子）。文件缺失 → 0。"""
    n = 0
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "iteration":
                    n += 1
    except OSError:
        return 0
    return n


def sum_train_samples(jsonl_path: Path) -> float:
    """累计**样本通过量** Σ(samples × epochs)（min_train_samples 的分子，跨重启重算）。

    为什么用样本而不是秒（2026-09-11）：时间口径在远端模式是"往返墙钟"，含打包上传、
    排队领活、结果下载——排队越久越"达标"，而本地采样期间云端空转（照烧 Kaggle 配额）
    它又完全看不到。样本通过量 = 优化器真正吃进去的样本数，与硬件/网络/事故无关，
    且能在开腿前由 seed_rotate × 样本/局 × epochs × iters 预先算出。
    """
    total = 0.0
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (isinstance(r, dict) and r.get("event") == "iteration"):
                    continue
                s = r.get("samples")
                if not isinstance(s, (int, float)):
                    continue
                ep = r.get("epochs")
                total += float(s) * (float(ep) if isinstance(ep, (int, float)) else 1.0)
    except OSError:
        return 0.0
    return total


def sum_train_sec(jsonl_path: Path) -> float:
    """累计有效训练秒（Σ iteration 事件的真训练秒；G13 duty 的分子）。

    跨重启口径：分子从**账本**重算而非进程内存——否则每次重启占空比被低估，
    G13 会在重启后误报"在烧事故"。文件缺失 → 0.0（首启正常）。
    """
    total = 0.0
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "iteration":
                    # 2026-09-11：优先**云端自报的真训练秒**（ppo_cloud_sec）；
                    # 旧账本无此键 → 回落 ppo_sec（远端模式那是往返墙钟，历史口径不变）。
                    v = r.get("ppo_cloud_sec")
                    if not isinstance(v, (int, float)):
                        v = r.get("ppo_sec")
                    if isinstance(v, (int, float)):
                        total += float(v)
    except OSError:
        return 0.0
    return total


def load_override(path: Path) -> dict[str, Any] | None:
    """读 G12 人工改向文件（§4.6）。不存在 → None；存在但非法 → 抛。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateOverrideError(f"override 文件 {path} 解析失败：{e}") from e
    if not isinstance(data, dict):
        raise GateOverrideError(f"override 文件 {path} 顶层必须是 object")
    verdict = data.get("verdict")
    if verdict not in _OVERRIDE_VERDICTS:
        raise GateOverrideError(
            f"override 文件 {path}: verdict={verdict!r} 非法（{sorted(_OVERRIDE_VERDICTS)}）"
        )
    return data
