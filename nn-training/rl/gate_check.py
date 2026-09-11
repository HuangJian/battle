"""gate_check —— 课程结束门求值器（plan/course-exit-and-shutdown.md §3/§4，M1）。

职责边界：**只判不停、只算不写**。`evaluate()` 是纯函数（禁 torch/numpy，单测
断言），写盘与停机分别是 `loop_guards`（gate_verdict 事件）与执行面的事。

四条红线
--------
1. **纯函数**：无 torch/numpy；无隐式文件读取（`read_*` 是显式入口）。
2. **确定性**：时间一律由 `now` 注入（§4.7），预算门单测全程 frozen now。
3. **趋势单源 = settled `eval_summary` 行**（§3.3 v0.4）：逐局行不进判决，
   聚合均值由 `eval_local.settle_eval_summary` 落进 summary 行。
4. **sustain 去重按 (course_fp, wver)**（§4.4）：同 wver 重跑（崩溃恢复/手动
   重放）只保留最新一条，不虚增连续通过计数。

与计划的两处偏差（均经实现期实测确认，属严格增强）
--------------------------------------------------
* §4.4 原设计把 sustain 计数建在 gate_log 的历史 pass 行上（求值器要吃外部状态）。
  本实现改为**从 trend_rows 复算**：窗口 = 最近 `sustain` 个**不同 wver** 的
  summary 行（已去重），全部满足该门的逐行判据才放行。语义等价（同 wver 重跑
  不计数），但求值器保持纯函数、无隐藏状态、崩溃重放天然幂等。
* §4.4「ADVANCE 需窗口内 ≥2 个不同 seed 集」：仅当窗口行**携带** seed 标识
  （`seed_fp`）时才校验；全部缺失记 `seeds: unknown` **且放行**——否则历史语料
  （无该字段）会让 ADVANCE 永久不可达，等于把门焊死。

CLI（薄壳，§4.1）只做 dry-run 与崩溃恢复重放：
`python -m rl.gate_check --course c6b-margin --traj <dir> [--dry-run]`
exit 码见 `EXIT_CODES`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 运行时期望零 rl.config 依赖，见 `_lazy_config()`。
    from rl.config import GateRule, GatesSpec


def _lazy_config() -> tuple[Any, Any, Any]:
    """延迟导入 rl.config，返回 (GATE_SPLIT, GatesSpec, resolve_course)。

    **为什么延迟**：`rl.config` → `rl.reward_library` → **numpy**（课程公式求值确实
    需要它），而求值器的红线是「禁 torch/numpy」——它活在评估线程与监控脚本里，
    不该为一个判决付依赖加载。类型走 `TYPE_CHECKING`，运行时只在真正求值时导入
    （主进程里 rl.config 早已加载，开销为零）。
    """
    from rl.config import GATE_SPLIT, GatesSpec, resolve_course

    return GATE_SPLIT, GatesSpec, resolve_course


# --------------------------------------------------------------------------- 常量

#: §4.2 优先级 lattice（求值器内定死，单测锁死）：
#: override > ABORT > PAUSE > STOP > REMEDIATE > ADVANCE > HOLD。
VERDICT_PRIORITY: dict[str, int] = {
    "HOLD": 0,
    "ADVANCE": 1,
    "REMEDIATE": 2,
    "STOP": 3,
    "PAUSE": 4,
    "ABORT": 5,
}

#: CLI exit 码（§4.1，notebook cell switch 用；不是系统主协议）。
EXIT_CODES: dict[str, int] = {
    "HOLD": 0,
    "ADVANCE": 10,
    "REMEDIATE": 20,
    "ABORT": 30,
    "PAUSE": 40,
    "STOP": 50,
}

#: override 文件里可写的判决（G12 人工改向，记 DECISIONS）。
_OVERRIDE_VERDICTS: frozenset[str] = frozenset(
    {"ADVANCE", "REMEDIATE", "STOP", "PAUSE", "ABORT", "HOLD"}
)

#: run_start 事件时间格式（rl/events.py:66）。
_TS_FMT = "%Y-%m-%d %H:%M:%S"


class GateOverrideError(ValueError):
    """override 文件存在但非法（§4.6：响亮报错，CLI exit 2，绝不静默忽略）。"""


# --------------------------------------------------------------------------- 数据结构


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


@dataclass(frozen=True)
class RuleReading:
    """单门的读数（审计用：判决 + 完成度 + 原因，全保留）。"""

    rule_id: str
    kind: str
    #: 单值判决（分流声明折叠为 ADVANCE）。
    verdict: str
    #: 门条件成立（sustain 未满足也算 fired——fired 与 released 分离）。
    fired: bool
    #: 可放行（fired + sustain 满 + ADVANCE 附加条件）。
    released: bool
    #: 完成度 = 近 sustain 窗内达标轮数 / sustain（§3.4-4 全局唯一定义）。
    completion: float
    reason: str
    enabled: bool = True
    #: 休眠（enabled=false）或 kind 未实现（dependency，M3）。
    dormant: bool = False
    #: 数据不足/缺字段 → 不判，且**不触发任何反向判决**（§4.5）。
    unknown: bool = False
    #: G4/G5 分流结果（ADVANCE/REMEDIATE）；非分流门为 None。
    route: str | None = None
    #: verdict 本体是分流声明（G4）——**只有它**的 route 才是判决本身；
    #: G5 的 route 只是路由建议（§3.3：STOP 附带去向），判决仍是 STOP。
    split: bool = False

    @property
    def effective_verdict(self) -> str:
        """进 lattice 的判决：分流门取 `route`，其余取本体（route 仅作元信息）。"""
        return (self.route or self.verdict) if self.split else self.verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "kind": self.kind,
            "verdict": self.verdict,
            "fired": self.fired,
            "released": self.released,
            "completion": round(self.completion, 4),
            "reason": self.reason,
            "enabled": self.enabled,
            "dormant": self.dormant,
            "unknown": self.unknown,
            "route": self.route,
            "split": self.split,
        }


@dataclass(frozen=True)
class GateResult:
    """一次求值的结果。"""

    verdict: str
    reason: str
    readings: tuple[RuleReading, ...] = ()
    route: str | None = None
    override: Mapping[str, Any] | None = None
    #: "ok"（≥2 seed 集）/ "insufficient"（阻塞 ADVANCE）/ "unknown"（放行）。
    seeds: str = "unknown"
    now: float | None = None
    health: Mapping[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        """非 HOLD = 训练该停（§4.3 判决→动作映射）。"""
        return self.verdict != "HOLD"

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.verdict, 0)

    def to_event(self, it: int, decider: str = "loop") -> dict[str, Any]:
        """`gate_verdict` 事件体（进 training_log.jsonl）。"""
        return {
            "event": "gate_verdict",
            "iter": it,
            "time": time.strftime(_TS_FMT),
            "verdict": self.verdict,
            "route": self.route,
            "reason": self.reason,
            "seeds": self.seeds,
            "decider": decider,
            "readings": [r.to_dict() for r in self.readings],
            "override": dict(self.override) if self.override else None,
        }


@dataclass(frozen=True)
class _Partial:
    """kind 求值器的中间结果（fired/completion 语义统一后转 RuleReading）。"""

    fired: bool
    completion: float
    reason: str
    unknown: bool = False
    dormant: bool = False
    route: str | None = None


@dataclass(frozen=True)
class _Ctx:
    spec: GatesSpec
    rows: tuple[EvalRow, ...]
    teacher_win_rate: float
    now: float | None
    budget: BudgetInfo | None


# --------------------------------------------------------------------------- 行归一化


def _row_from_summary(r: Mapping[str, Any]) -> EvalRow | None:
    """summary 行 → EvalRow；非 eval_summary / 无 games 的行丢弃。"""
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
    )


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


def read_trend_rows(jsonl_path: Path, course_fp: str = "", limit: int = 0) -> tuple[dict, ...]:
    """读 `eval_log.jsonl`（per-tick 课程）里的 eval_summary 行；文件缺失返回 ()。

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


# --------------------------------------------------------------------------- 通用统计


def _mean(rows: Sequence[EvalRow], metric: str) -> float | None:
    vals: list[float] = [v for v in (r.metric(metric) for r in rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _slope(vals: Sequence[float]) -> float:
    """最小二乘斜率（等距 x = 0..n-1）；n<2 返回 0.0。"""
    n = len(vals)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(vals) / n
    den = sum((i - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return sum((i - mx) * (v - my) for i, v in enumerate(vals)) / den


def _sustain(ctx: _Ctx, judge: Callable[[EvalRow], str | None]) -> tuple[float, str, bool]:
    """sustain 窗口判定（§4.4 复算口径）：judge 返回 None = 该行达标。

    返回 (completion, reason, unknown)。窗口不足 sustain → completion 按实际达标
    数计，reason 点名"数据不足"，永不满 1.0（门不放行）。

    `unknown`：窗口里的未达标**全部**源于缺数据（judge 返回 "缺 …"/"分母为零"）
    → 判不了，而不是"策略不行"——缺数据只阻塞依赖它的 ADVANCE，不构成反向判决
    （§4.5）。窗口为空同样算 unknown（一条 summary 都没有）。
    """
    sustain = max(1, int(ctx.spec.sustain))
    window = list(ctx.rows[-sustain:])
    fails = [j for j in (judge(r) for r in window) if j]
    n_pass = len(window) - len(fails)
    completion = n_pass / sustain
    if len(window) < sustain:
        reason = f"{n_pass}/{sustain} 轮达标（数据不足：窗口仅 {len(window)} 轮）"
    else:
        reason = f"{n_pass}/{sustain} 轮达标"
    if fails:
        reason += f"；最近未达标：{fails[-1]}"
    unknown = (not window) or bool(
        fails and all(f.startswith("缺 ") or "分母为零" in f for f in fails)
    )
    return completion, reason, unknown


def _window(rule: GateRule, ctx: _Ctx, n: int) -> list[EvalRow] | None:
    """取最近 n 个去重后的 summary 行；不足 n → None（数据不足，不判）。"""
    if n <= 0:
        return None
    rows = list(ctx.rows[-n:])
    return rows if len(rows) >= n else None


def _halves(rule: GateRule, ctx: _Ctx, n: int) -> tuple[list[EvalRow], list[EvalRow]] | None:
    """窗口前后半（plateau/hack 口径）：n 必须 ≥2，返回 (前半, 后半)。"""
    rows = _window(rule, ctx, n)
    if rows is None or len(rows) < 2:
        return None
    k = len(rows) // 2
    return rows[:k], rows[k:]


# --------------------------------------------------------------------------- kind 求值器


def _eval_wins_mastery(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G1：窗口内胜率 ≥ rel_teacher × 教师胜率，连续 sustain 轮。

    2026-09-11 评审新增两个可选附加条件（§12.4 effect size）：
      * `min_gain_pp`：还须 ≥ `spec.baseline_win_rate + min_gain_pp/100`——
        防"400 局把 1pp 变成显著但无意义"；
      * `require_rising`：sustain 窗口内胜率最小二乘斜率须 ≥ 0（同向）。
    """
    rel = rule.rel_teacher if rule.rel_teacher is not None else 1.0
    thr = rel * ctx.teacher_win_rate
    baseline = getattr(ctx.spec, "baseline_win_rate", None)
    floor = (
        (baseline + rule.min_gain_pp / 100.0)
        if baseline is not None and rule.min_gain_pp is not None
        else None
    )

    def judge(r: EvalRow) -> str | None:
        if r.win_rate is None:
            return "本轮无 winRate"
        if r.win_rate < thr:
            return f"winRate {r.win_rate:.3f} < {thr:.3f}"
        if floor is not None and r.win_rate < floor:
            return f"winRate {r.win_rate:.3f} < 起点{baseline:.2f}+{rule.min_gain_pp}pp={floor:.3f}"
        return None

    if rule.pool_window is not None:
        return _wins_mastery_pooled(rule, ctx, thr=thr, floor=floor, baseline=baseline)

    completion, reason, unknown = _sustain(ctx, judge)
    if rule.require_rising:
        wrs = [
            r.win_rate for r in ctx.rows[-max(1, int(ctx.spec.sustain)) :] if r.win_rate is not None
        ]
        if len(wrs) >= 2:
            s = _slope(wrs)
            if s < 0:
                completion = min(completion, 1.0 - 1.0 / max(1, int(ctx.spec.sustain)))
                reason += f"；窗口胜率斜率 {s:+.4f} < 0（非同向，§12.4）"
        else:
            reason += "；同向性待第二个数据点"
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"胜率 ≥ {thr:.3f}（{rel}×教师 {ctx.teacher_win_rate:.3f}）：{reason}",
        unknown=unknown,
    )


def _wins_mastery_pooled(
    rule: GateRule,
    ctx: _Ctx,
    *,
    thr: float,
    floor: float | None,
    baseline: float | None,
) -> _Partial:
    """G1 池化判据（2026-09-11 判决力修复）：合并最近 `pool_window` 个点的局数再判。

    为什么必须池化：100 局/点的 SE≈4.3pp，而门要求的效果量只有 5pp —— 逐点判等于
    用噪声判噪声（真 +7pp 也仅约 12% 概率三连过）。池化不花一分额外评估时间：
    3×100 局 ⇒ SE≈2.6pp。sustain 的"复现"语义靠**窗口证据 + 斜率同向**保留。
    """
    k = max(1, int(rule.pool_window or 1))
    rows = list(ctx.rows[-k:])
    if len(rows) < k:
        return _Partial(
            False, 0.0, f"池化窗口仅 {len(rows)}/{k} 轮（数据不足，不判）", unknown=True
        )
    games = sum(int(r.games or 0) for r in rows)
    if games <= 0:
        return _Partial(False, 0.0, "池化窗口缺 games/wins（不判）", unknown=True)
    wins = sum(int(r.wins or 0) for r in rows)
    p = wins / games
    se = math.sqrt(max(0.0, p * (1.0 - p)) / games)
    head = (
        f"池化 {k} 轮 {games} 局 {wins} 胜 = {p:.3f}"
        f"（SE {100 * se:.1f}pp，95%CI ±{196 * se:.1f}pp）"
    )
    fails: list[str] = []
    if p < thr:
        fails.append(f"{p:.3f} < 教师线 {thr:.3f}")
    target: float | None = None
    if floor is not None:
        target = floor
        if p < floor:
            fails.append(f"{p:.3f} < 起点 {float(baseline or 0.0):.2f}+{rule.min_gain_pp}pp={floor:.3f}")
    z = float(rule.conf_z or 0.0)
    if z > 0 and target is not None and p - z * se < target:
        fails.append(
            f"效应未高于噪声：{p:.3f} − {z:g}×{se:.3f} = {p - z * se:.3f} < {target:.3f}"
        )
    if rule.require_rising:
        wrs = [r.win_rate for r in rows if r.win_rate is not None]
        if len(wrs) >= 2:
            s = _slope(wrs)
            if s < 0:
                fails.append(f"窗口胜率斜率 {s:+.4f} < 0（非同向，§12.4）")
        else:
            fails.append("同向性待第二个数据点")
    if fails:
        return _Partial(False, 0.0, f"{head}；未达标：{'；'.join(fails)}")
    return _Partial(True, 1.0, f"{head}；达标（教师线 {thr:.3f}）")


def _eval_skill_floor(rule: GateRule, ctx: _Ctx, teacher: Any) -> _Partial:
    """G2：0 杀占比 / 场均杀（教师相对）/ 被击中（教师相对）三项全过。"""
    max_zkf = rule.max_zero_kill_frac
    min_kills = (rule.min_kills_rel or 0.0) * float(teacher.kills)
    max_phits = (
        (rule.max_phits_rel * float(teacher.phits)) if rule.max_phits_rel is not None else None
    )

    def judge(r: EvalRow) -> str | None:
        if max_zkf is not None:
            if r.zero_kill_frac is None:
                return "缺 zero_kill_frac"
            if r.zero_kill_frac > max_zkf:
                return f"0 杀占比 {r.zero_kill_frac:.3f} > {max_zkf:.3f}"
        if rule.min_kills_rel is not None:
            if float(teacher.kills) <= 0:
                return "教师 kills=0（分母为零，子项跳过）"
            if r.kills_mean is None:
                return "缺 kills_mean"
            if r.kills_mean < min_kills:
                return f"场均杀 {r.kills_mean:.2f} < {min_kills:.2f}"
        if max_phits is not None:
            if float(teacher.phits) <= 0:
                return "教师 phits=0（分母为零，子项跳过）"
            if r.phits_mean is None:
                return "缺 phits_mean"
            if r.phits_mean > max_phits:
                return f"被击中 {r.phits_mean:.2f} > {max_phits:.2f}"
        return None

    completion, reason, unknown = _sustain(ctx, judge)
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"技能地板（0杀≤{max_zkf} / 杀≥{min_kills:.2f} / 被击中≤{max_phits}）：{reason}",
        unknown=unknown,
    )


def _eval_teacher_parity(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G10：|胜率 − 教师胜率| ≤ tol_pp（pp 口径），连续 sustain 轮。"""
    tol = (rule.tol_pp if rule.tol_pp is not None else 5.0) / 100.0

    def judge(r: EvalRow) -> str | None:
        if r.win_rate is None:
            return "本轮无 winRate"
        if abs(r.win_rate - ctx.teacher_win_rate) > tol:
            return f"|{r.win_rate:.3f} − {ctx.teacher_win_rate:.3f}| > {tol:.3f}"
        return None

    completion, reason, unknown = _sustain(ctx, judge)
    return _Partial(
        fired=completion >= 1.0,
        completion=completion,
        reason=f"教师触顶：{reason}",
        unknown=unknown,
    )


def _eval_plateau(rule: GateRule, ctx: _Ctx, completions: Mapping[str, float]) -> _Partial:
    """G4：窗口前后半均值差 ≤ 容差（各 metric 全满足）= 边际收益枯竭 → 分流。"""
    n = int(rule.window_rounds or 0)
    halves = _halves(rule, ctx, n)
    if halves is None:
        return _Partial(
            fired=False,
            completion=0.0,
            unknown=True,
            reason=f"窗口 {n} 轮数据不足（现有 {len(ctx.rows)} 轮）",
        )
    first, second = halves
    tol_pp = rule.tol_pp if rule.tol_pp is not None else 8.0
    tol_kills = rule.tol_kills if rule.tol_kills is not None else 0.5
    # 缺失的 metric 跳过（只判有的）——要求至少判了一项。理由：kills_mean 这类
    # 技能子指标要等 summary 富化后才在盘（旧语料一律缺），若因它缺就把整个平台
    # 门变 unknown，门在旧腿上等于焊死（c6 回溯实测：只看 win_rate 时 G4 会在
    # it30 判枯竭 → REMEDIATE，而"缺 kills 就 unknown"让它全程 HOLD）。
    checked: list[str] = []
    for m in rule.metrics or ["win_rate"]:
        a = _mean(first, m)
        b = _mean(second, m)
        if a is None or b is None:
            continue
        tol = tol_pp / 100.0 if m in ("win_rate", "timeout_frac") else tol_kills
        if abs(b - a) > tol:
            return _Partial(
                fired=False,
                completion=0.0,
                reason=f"{m} 前后半 {a:.4f}→{b:.4f}（Δ{b - a:+.4f}）仍超容差 {tol:.4f}——尚未枯竭",
            )
        checked.append(m)
    if not checked:
        return _Partial(
            fired=False, completion=0.0, unknown=True, reason="窗口内所有 metric 均无数据（不判）"
        )
    route = _route_by_completion(rule, completions)
    return _Partial(
        fired=True,
        completion=1.0,
        reason=f"窗口 {n} 轮 {','.join(checked)} 变化均在容差内（边际收益枯竭）→ {route}",
        route=route,
    )


def _eval_budget(rule: GateRule, ctx: _Ctx, completions: Mapping[str, float]) -> _Partial:
    """G5：预算到顶（max_hours / iters）→ STOP，route 按 advance_if 完成度填。"""
    if ctx.now is None or ctx.budget is None:
        return _Partial(
            fired=False, completion=0.0, unknown=True, reason="缺 now 或 budget（预算门不判）"
        )
    why = ctx.budget.exhaust_reason(ctx.now)
    if why is None:
        return _Partial(fired=False, completion=0.0, reason="预算未到顶")
    route = _route_by_completion(rule, completions)
    return _Partial(fired=True, completion=1.0, reason=f"预算到顶：{why} → {route}", route=route)


def _eval_course_valid(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G7：超时占比超限**且**窗口斜率 > 0（严格单调改斜率，ds-P2-1）→ REMEDIATE。"""
    max_tf = rule.max_timeout_frac
    rows = ctx.rows
    if not rows:
        return _Partial(False, 0.0, unknown=True, reason="无 summary 行")
    latest = rows[-1]
    if latest.timeout_frac is None:
        return _Partial(False, 0.0, unknown=True, reason="缺 timeout_frac（不判）")
    if max_tf is not None and latest.timeout_frac <= max_tf:
        return _Partial(
            False,
            0.0,
            reason=f"超时占比 {latest.timeout_frac:.3f} ≤ {max_tf:.3f}（课程未失效）",
        )
    n = max(2, int(rule.rising_rounds or 3))
    win = list(rows[-n:])
    vals = [r.timeout_frac for r in win if r.timeout_frac is not None]
    if len(vals) < 2:
        return _Partial(False, 0.0, unknown=True, reason="超时序列不足 2 点（斜率不可算）")
    s = _slope(vals)
    if s <= 0:
        return _Partial(
            False,
            0.0,
            reason=f"超时占比 {latest.timeout_frac:.3f} 超限但斜率 {s:+.4f} ≤ 0（未恶化）",
        )
    return _Partial(
        True,
        1.0,
        reason=f"超时占比 {latest.timeout_frac:.3f} 超限且近 {len(vals)} 轮斜率 {s:+.4f} > 0",
    )


def _eval_hack(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G9：拾取↑ 且 击杀↓（双向条件，P0-3）→ PAUSE（ABORT 留给人工 override）。"""
    n = int(rule.window_rounds or 0)
    halves = _halves(rule, ctx, n)
    if halves is None:
        return _Partial(
            False,
            0.0,
            unknown=True,
            reason=f"窗口 {n} 轮数据不足（现有 {len(ctx.rows)} 轮）",
        )
    first, second = halves
    pu_a, pu_b = _mean(first, "pickup_mean"), _mean(second, "pickup_mean")
    k_a, k_b = _mean(first, "kills_mean"), _mean(second, "kills_mean")
    if None in (pu_a, pu_b, k_a, k_b):
        return _Partial(False, 0.0, unknown=True, reason="缺 pickup_mean/kills_mean（不判）")
    assert pu_a is not None and pu_b is not None and k_a is not None and k_b is not None
    up_rel = rule.pickup_up_rel if rule.pickup_up_rel is not None else 0.5
    down_rel = rule.kills_down_rel if rule.kills_down_rel is not None else 0.2
    pu_up = pu_b >= pu_a * (1.0 + up_rel)
    k_down = k_b <= k_a * (1.0 - down_rel) if k_a > 0 else k_b <= k_a
    if not (pu_up and k_down):
        return _Partial(
            False,
            0.0,
            reason=f"单向：拾取 {pu_a:.2f}→{pu_b:.2f}（需 ≥+{up_rel:.0%}）"
            f"/ 击杀 {k_a:.2f}→{k_b:.2f}（需 ≤−{down_rel:.0%}）",
        )
    return _Partial(
        True,
        1.0,
        reason=f"拾取 {pu_a:.2f}→{pu_b:.2f} 且 击杀 {k_a:.2f}→{k_b:.2f}（疑似 hack）",
    )


def _eval_cross_course(
    rule: GateRule, ctx: _Ctx, all_rows: Sequence[Mapping[str, Any]]
) -> _Partial:
    """G3/G8（跨课）：按 `course` 名匹配 trend_rows 里的异课行。

    首期两门默认休眠（§3.3）。启用时：无该课行 = 值班缺勤 → unknown，**只阻塞
    依赖它的 ADVANCE，不触发任何反向判决**（§4.5）。
    """
    target = str(rule.course or "")
    if not target:
        return _Partial(False, 0.0, unknown=True, reason="未配 course")
    sub = [
        r
        for r in (
            _row_from_summary(x)
            for x in all_rows
            if str(x.get("course") or x.get("course_name") or "") == target
        )
        if r is not None
    ]
    if not sub:
        return _Partial(
            False, 0.0, unknown=True, reason=f"无课程 '{target}' 的 summary 行（值班缺勤）"
        )
    latest = sub[-1]
    if rule.kind == "transfer":
        need_wins = rule.min_wins
        need_kills = rule.min_kills
        if need_wins is not None and latest.wins < int(need_wins):
            return _Partial(
                False, 0.0, reason=f"'{target}' 胜 {latest.wins} < {need_wins}（迁移未达标）"
            )
        if need_kills is not None and (latest.kills_mean or 0.0) < float(need_kills):
            return _Partial(
                False, 0.0, reason=f"'{target}' 场均杀 {latest.kills_mean} < {need_kills}"
            )
        return _Partial(True, 1.0, reason=f"'{target}' 迁移达标（单次，不需 sustain）")
    # retention
    min_wr = rule.min_win_rate if rule.min_win_rate is not None else 0.85
    if latest.win_rate is not None and latest.win_rate < min_wr:
        return _Partial(
            True,
            1.0,
            reason=f"'{target}' 胜率 {latest.win_rate:.3f} < {min_wr:.3f}（回退）",
        )
    return _Partial(False, 0.0, reason=f"'{target}' 胜率 {latest.win_rate} ≥ {min_wr:.3f}（保持）")


def _eval_dependency(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G11：kind 保留，M3 实现（§3.3）——恒为 dormant，永不触发判决。"""
    return _Partial(False, 0.0, dormant=True, reason="dependency 未实现（M3）")


def _eval_duty(rule: GateRule, ctx: _Ctx) -> _Partial:
    """G13 事故熔断（2026-09-11 评审新增）：有效训练占空比过低 → REMEDIATE。

    c6 的病灶形态：6.5h 墙钟里只有 ~1h 训练（事故/排队吃掉 5.5h）。墙钟预算门
    （G5）看不出来——"3h 全是事故、训练 40min"在 G5 眼里只是"还没到顶"。
    duty = train_sec / wall_sec（分子跨重启从 iteration 事件重算，分母自首条
    run_start 起）。占空比 < min_train_frac → 课程在烧事故，停车复诊。
    """
    frac = rule.min_train_frac if rule.min_train_frac is not None else 0.3
    if ctx.budget is None or ctx.now is None:
        return _Partial(False, 0.0, unknown=True, reason="缺 budget/now（duty 门不判）")
    wall = ctx.budget.wall_sec(ctx.now)
    if wall is None or wall <= 0:
        return _Partial(False, 0.0, unknown=True, reason="无墙钟基线（duty 门不判）")
    duty = ctx.budget.train_sec / wall
    if duty >= frac:
        return _Partial(False, 0.0, reason=f"有效训练占空比 {duty:.2f} ≥ {frac:.2f}（健康）")
    hours = ctx.budget.train_sec / 3600.0
    return _Partial(
        True,
        1.0,
        reason=(
            f"有效训练占空比 {duty:.2f} < {frac:.2f}"
            f"（{hours:.2f}h 训练 / {wall / 3600.0:.2f}h 墙钟）——腿在烧事故，复诊"
        ),
    )


def _route_by_completion(rule: GateRule, completions: Mapping[str, float]) -> str:
    """G4/G5 分流：advance_if 里**最弱一环**的完成度 ≥ advance_frac → ADVANCE。

    取 min 而非均值：任一前置门没站稳就不放行（保守口径）。目标门本身是分流门
    （理论上会成环）时按 0.0 计并在 reason 里点名。
    """
    if not rule.advance_if:
        # 没写 advance_if = 无人替 ADVANCE 背书 → 保守走 REMEDIATE。
        return "REMEDIATE"
    frac = rule.advance_frac if rule.advance_frac is not None else 1.0
    weakest = min(completions.get(gid, 0.0) for gid in rule.advance_if)
    return "ADVANCE" if weakest >= frac else "REMEDIATE"


# --------------------------------------------------------------------------- 主入口

_JUDGES_NO_TEACHER: dict[str, Callable[[GateRule, _Ctx], _Partial]] = {
    "wins_mastery": _eval_wins_mastery,
    "teacher_parity": _eval_teacher_parity,
    "course_valid": _eval_course_valid,
    "hack": _eval_hack,
    "duty": _eval_duty,
    "dependency": _eval_dependency,
}


def evaluate(
    course: Any,
    trend_rows: Sequence[Mapping[str, Any]],
    health: Mapping[str, Any] | None = None,
    budget: BudgetInfo | None = None,
    now: float | None = None,
    *,
    override: Mapping[str, Any] | None = None,
    course_fp: str = "",
    decider: str = "loop",
) -> GateResult:
    """课程结束门求值（纯函数，§4.1）。

    Args:
        course: `CourseConfig`（读 `.gates`）或直接传 `GatesSpec`；无 gates → HOLD。
        trend_rows: settled `eval_summary` 行（原始 dict，时间序）。
        health: 预留（§3.3 末：健康门不经求值器，由 loop_guards 直接判）。
        budget: G5 输入；None → 预算门 unknown。
        now: epoch 秒；None → `time.time()`（单测注入 frozen now）。
        override: G12 人工改向（最高优先级）；非法由 `load_override` 抛。
        course_fp: 本课课程指纹；用于行过滤与 sustain 去重键。
        decider: "loop" | "notebook" | "cli"（记入事件，血缘可归因）。

    Returns:
        GateResult：verdict ∈ {HOLD, ADVANCE, REMEDIATE, STOP, PAUSE, ABORT}。
    """
    now_v = time.time() if now is None else float(now)
    GATE_SPLIT, GatesSpec, _ = _lazy_config()
    spec = getattr(course, "gates", None)
    if spec is None and isinstance(course, GatesSpec):
        spec = course
    if spec is None:
        return GateResult(
            verdict="HOLD",
            reason="课程无 gates 块（门关闭，老课程行为不变）",
            now=now_v,
            health=health,
        )

    rows = normalize_rows(trend_rows, course_fp)
    teacher_wr = spec.teacher.wins / spec.teacher.games if spec.teacher.games else 0.0
    ctx = _Ctx(spec=spec, rows=rows, teacher_win_rate=teacher_wr, now=now_v, budget=budget)

    # ---- pass 1：非分流门（完成度不依赖 advance_if）----
    completions: dict[str, float] = {}
    partials: dict[str, _Partial] = {}
    for rule in spec.rules:
        if rule.kind in ("plateau", "budget"):
            continue
        partials[rule.id] = _eval_one(rule, ctx, trend_rows, spec)
        completions[rule.id] = partials[rule.id].completion

    # ---- pass 2：分流门（route 读 pass 1 的完成度）----
    for rule in spec.rules:
        if rule.kind not in ("plateau", "budget"):
            continue
        p = _eval_one(rule, ctx, trend_rows, spec, completions)
        partials[rule.id] = p
        completions[rule.id] = p.completion

    # ---- ADVANCE 附加条件：advance_requires 全绿 + 窗口 ≥2 seed 集 ----
    by_rule = {r.id: r for r in spec.rules}
    advance_ready = all(
        completions.get(gid, 0.0) >= 1.0
        for gid in spec.advance_requires
        if by_rule.get(gid) is not None and by_rule[gid].enabled
    )
    window = list(rows[-max(1, int(spec.sustain)) :])
    seed_fps = {r.seed_fp for r in window if r.seed_fp}
    if not seed_fps:
        seeds_state = "unknown"
    elif len(seed_fps) >= 2:
        seeds_state = "ok"
    else:
        seeds_state = "insufficient"
    seeds_ok = seeds_state != "insufficient"

    # ---- ADVANCE 附加条件之四：样本通过量（§12.4：数据不够下结论 ≠ 事故）----
    min_samples = getattr(spec, "min_train_samples", None)
    train_ok = True
    train_note = ""
    if budget is not None and min_samples is not None:
        got = budget.train_samples
        if got < float(min_samples):
            train_ok = False
            train_note = f"（样本通过量 {got / 1e6:.2f}M < {float(min_samples) / 1e6:.2f}M）"

    readings: list[RuleReading] = []
    #: 达标但被 ADVANCE 附加条件挡住的门（观测必须自带牙齿：HOLD 也要说清差在哪）。
    blocked_advance: list[str] = []
    for rule in spec.rules:
        p = partials[rule.id]
        verdict = "ADVANCE" if rule.verdict == GATE_SPLIT else rule.verdict
        released = p.fired and p.completion >= 1.0
        if rule.verdict == GATE_SPLIT:
            # 分流门：fired 即放行，实际判决取 route
            released = p.fired
        if not rule.enabled:
            released = False
        elif released and (p.route if rule.verdict == GATE_SPLIT else verdict) == "ADVANCE":
            # ADVANCE 附加条件（§4.4 + §12.4）：advance_requires 全绿 + 窗口 ≥2 seed 集
            # + 样本通过量 ≥ min_train_samples。
            # 分流门按**实际 route** 判（route=REMEDIATE 不需 advance_requires 背书，
            # 否则平台期永远放不出 REMEDIATE——c6 回溯实测正是卡在这里）。
            gates_ok = advance_ready and seeds_ok and train_ok
            if not gates_ok:
                blocked_advance.append(
                    f"{rule.id}"
                    + ("" if advance_ready else "(advance_requires 未全绿)")
                    + ("" if seeds_ok else f"(seed 集 {seeds_state})")
                    + (train_note if not train_ok else "")
                )
            released = gates_ok
        readings.append(
            RuleReading(
                rule_id=rule.id,
                kind=rule.kind,
                verdict=verdict,
                fired=p.fired,
                released=released,
                completion=p.completion,
                reason=p.reason,
                enabled=rule.enabled,
                dormant=p.dormant or not rule.enabled,
                unknown=p.unknown,
                route=p.route,
                split=rule.verdict == GATE_SPLIT,
            )
        )

    # ---- lattice：同轮多门取最高优先级 ----
    best: RuleReading | None = None
    for rd in readings:
        if not rd.released:
            continue
        if best is None:
            best = rd
            continue
        # 同优先级时取**带 route 的那条**（分流门信息更全：既给判决又给去向）。
        key_new = (VERDICT_PRIORITY.get(rd.effective_verdict, 0), rd.route is not None)
        key_old = (VERDICT_PRIORITY.get(best.effective_verdict, 0), best.route is not None)
        if key_new > key_old:
            best = rd
    verdict = "HOLD"
    route: str | None = None
    if best is not None:
        verdict = best.effective_verdict
        route = best.route
        reason = f"{best.rule_id}({best.kind}): {best.reason}"
        if not advance_ready and verdict == "ADVANCE":
            verdict = "HOLD"
            reason += "（advance_requires 未全绿 → HOLD）"
        if not seeds_ok and verdict == "ADVANCE":
            verdict = "HOLD"
            reason += "（窗口 seed 集 <2 → HOLD）"
    elif blocked_advance:
        # 门已达标、只差附加条件——HOLD 也要说清差在哪一环（否则屏幕上只有沉默）。
        reason = f"ADVANCE 待放行（{'，'.join(blocked_advance)}）→ HOLD"
    else:
        reason = "无门放行（HOLD）"

    ov = dict(override) if override else None
    if ov is not None:
        ov_verdict = str(ov.get("verdict") or "HOLD")
        verdict = ov_verdict
        reason = f"G12 override → {ov_verdict}：{ov.get('reason', '')}".strip()
        route = ov.get("route")

    return GateResult(
        verdict=verdict,
        reason=reason,
        readings=tuple(readings),
        route=route,
        override=ov,
        seeds=seeds_state,
        now=now_v,
        health=health,
    )


def _eval_one(
    rule: GateRule,
    ctx: _Ctx,
    all_rows: Sequence[Mapping[str, Any]],
    spec: GatesSpec,
    completions: Mapping[str, float] | None = None,
) -> _Partial:
    """单门分发（未知 kind 已在解析期拦掉；这里兜底为 dormant，不崩训练）。"""
    comp = completions or {}
    if not rule.enabled:
        return _Partial(False, 0.0, dormant=True, reason="休眠（enabled=false）")
    if rule.kind in _JUDGES_NO_TEACHER:
        return _JUDGES_NO_TEACHER[rule.kind](rule, ctx)
    if rule.kind == "skill_floor":
        return _eval_skill_floor(rule, ctx, spec.teacher)
    if rule.kind == "plateau":
        return _eval_plateau(rule, ctx, comp)
    if rule.kind == "budget":
        return _eval_budget(rule, ctx, comp)
    if rule.kind in ("transfer", "retention"):
        return _eval_cross_course(rule, ctx, all_rows)
    return _Partial(False, 0.0, dormant=True, reason=f"kind '{rule.kind}' 未实现")


# --------------------------------------------------------------------------- CLI（薄壳）


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rl.gate_check",
        description="课程结束门 dry-run / 崩溃恢复重放（判决只写 stdout，零写盘）",
    )
    p.add_argument("--course", required=True, help="课程名或 jsonc 路径")
    p.add_argument("--traj", required=True, help="traj 根目录（读 eval_log.jsonl）")
    p.add_argument(
        "--log",
        default="training_log.jsonl",
        help="预算基线来源（相对 traj；默认 training_log.jsonl）",
    )
    p.add_argument("--decider", default="cli", choices=["cli", "loop", "notebook"])
    p.add_argument("--json", action="store_true", help="输出完整 GateResult JSON")
    p.add_argument("--dry-run", action="store_true", help="显式声明零写盘（默认就是）")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """薄壳 CLI（§4.1）。exit 码 = `EXIT_CODES`；override 非法 = 2。"""
    from rl.config import load_course  # 延迟导入：CLI 路径才需要

    args = build_cli().parse_args(argv)
    _, _, resolve_course = _lazy_config()
    course_path = resolve_course(args.course)
    course = load_course(course_path)
    traj = Path(args.traj)
    # 与 `course_fp_for_args` 同算法（D14），否则读不到带 fp 的 summary 行。
    try:
        fp = hashlib.sha256(Path(course_path).read_bytes()).hexdigest()
    except OSError:
        fp = ""
    rows = read_trend_rows(traj / "eval_log.jsonl", course_fp=fp)
    started = first_run_start_ts(traj / args.log)
    budget = BudgetInfo(
        started_at=started,
        max_hours=float(getattr(course, "max_hours", 0.0) or 0.0),
        iters=int(getattr(course, "iters", 0) or 0),
        cur_iter=int(getattr(course, "_cur_iter", 0) or 0),
    )
    override = None
    if course.gates is not None:
        ov_path = traj.parent / str(course.gates.override_file)
        override = load_override(ov_path)
    res = evaluate(
        course, rows, budget=budget, override=override, course_fp=fp, decider=args.decider
    )
    if args.json:
        print(
            json.dumps(
                {
                    "verdict": res.verdict,
                    "route": res.route,
                    "reason": res.reason,
                    "seeds": res.seeds,
                    "readings": [r.to_dict() for r in res.readings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"verdict={res.verdict} route={res.route} seeds={res.seeds}")
        print(f"reason: {res.reason}")
        for r in res.readings:
            print(
                f"  - {r.rule_id:<4} {r.kind:<14} fired={r.fired!s:<5} "
                f"released={r.released!s:<5} completion={r.completion:.2f} {r.reason}"
            )
        for note in _notes(course, rows):
            print(f"  ! {note}")
    return res.exit_code


def _notes(course: Any, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """§3.4-8 预算可行性 + §12.4 判决力（两条 warn-only，调用方负责打印）。

    `budget_warnings` 自 2026-09-11 前**从未接线**（只有单测在调）——这里一并接上。
    """
    spec = getattr(course, "gates", None)
    if spec is None:
        return []
    games_per_point = int(rows[-1].get("games") or 0) if rows else 0  # 原始行 dict
    notes = list(
        spec.budget_warnings(
            float(getattr(course, "max_hours", 0.0) or 0.0),
            int(getattr(course, "eval_every", 0) or 0),
        )
    )
    notes += list(spec.power_notes(games_per_point))
    return notes


if __name__ == "__main__":
    raise SystemExit(main())
