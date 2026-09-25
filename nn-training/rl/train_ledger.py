"""train_ledger —— `training_log.jsonl` 的单一只读视图（R2a，plan/r2-loop-task-queue §5）。

**要解决的问题**：门禁与续跑的输入**本来就在账本里**，但今天散在五个扫描器里
（`rl/resume.py` 的 `last_completed_iter`/`last_rotate_seed`/`peak_entropy`、
`rl/gate_check.py` 的 `sum_train_sec`/`sum_train_samples`/`count_iteration_events`/
`first_run_start_ts`/`first_iter_end_ts`），每个都**独立读一遍全文件**；而另有一批门禁
计数（连击 / 止损 / 提示类 REMEDIATE 次数）**只在进程内存里** ⇒ 重启即洗白。
R2 把它们收敛成**一次扫描**得到的 `LedgerView`：每课开课/续跑读一遍，之后由写事件处
增量追加（`apply_event`，永不重扫）。这正是用户 2026-09-18 裁决「门禁语义从内存计数
改成扫账本；门禁指标按课程分别缓存，只在每门课开启/续跑时读一遍」的落地。

**三条硬口径**

1. **账本是 SSOT**：`LedgerView` 必须能重建内存计数——`tests/test_train_ledger.py`
   与既有五个扫描器做**奇偶断言**（旧扫描器即参考语义）。`loop-state.json` 只是加速器，
   冲突时以账本为准。
2. **向前兼容**：未知事件名直接忽略（hub 追加的 `job_completed`/`job_failed` 对旧读者
   透明），半截/坏行跳过（崩溃可能留下截断行），两者都**不得**改变任何计数。
3. **判定逻辑复用生产纯函数**（`rl.breaker.breaker_update`），绝不在这里写第二份
   ——否则「扫账本」会与「内存计数」悄悄分叉。

**已知不可重建项（刻意不假装能算）**：`zero_shard_streak` 的口径在内存里还依赖
`_node_rollout`（节点轮本地本来就零 shard，必须排除），而账本 `iteration` 行今天
不带 `rollout_src` ⇒ 从账本重算会对节点轮报假事故。因此它只作为**观测量**留在 view 里，
不作为启动继承值（R2b 给事件加 `rollout_src` 后再接）。`consec_fail`（重试连击）同理：
它是**单腿内**的进程级护栏（5 连击即抛），跨重启继承会让「重启即秒死」，故也只观测。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rl.breaker import (
    ENT_BREAK,
    ENT_BREAK_CONSEC,
    ENT_BREAK_MAX_WINRATE,
    KL_BREAK,
    KL_BREAK_CONSEC,
    breaker_update,
)

#: 事件 `time` 字段的 wire 格式（与 `gate_check._TS_FMT` 同口径，不 import 私有名）。
_TS_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class LedgerSpec:
    """重算连击所需的阈值（必须与 `loop_guards_trip._breaker` 用的那次判定同源）。

    `from_args` 逐条复制 `loop_guards_trip` 的选择语义：KL 阈值只在 intent/goal 模式取
    args 覆盖（per-tick 恒用常量），ENT 三个阈值恒取 args 覆盖（缺省回落常量）。
    阈值变了 ⇒ 新阈值下重算的连击是「新纪元」的口径，这是刻意的：换阈值就是换判据。
    """

    kl_break: float = KL_BREAK
    kl_consec: int = KL_BREAK_CONSEC
    ent_break: float = ENT_BREAK
    ent_consec: int = ENT_BREAK_CONSEC
    ent_max_winrate: float = ENT_BREAK_MAX_WINRATE

    @classmethod
    def from_args(cls, args: Any) -> LedgerSpec:
        relaxed = str(getattr(args, "mode", "")) in ("intent", "goal")
        if relaxed:
            kl_break = float(getattr(args, "kl_break", KL_BREAK))
            kl_consec = int(getattr(args, "kl_break_consec", KL_BREAK_CONSEC))
        else:
            kl_break, kl_consec = KL_BREAK, KL_BREAK_CONSEC
        return cls(
            kl_break=kl_break,
            kl_consec=kl_consec,
            ent_break=float(getattr(args, "ent_break", ENT_BREAK)),
            ent_consec=int(getattr(args, "ent_break_consec", ENT_BREAK_CONSEC)),
            ent_max_winrate=float(getattr(args, "ent_break_max_winrate", ENT_BREAK_MAX_WINRATE)),
        )


@dataclass(frozen=True)
class IterRow:
    """一条 `iteration` 事件的可重算切片（门禁窗口只消费这些字段）。"""

    it: int
    samples: float
    epochs: float
    kl: float | None
    entropy: float | None
    win_rate: float | None
    train_sec: float
    ts: float | None


@dataclass(frozen=True)
class VerdictRow:
    """一条 `gate_verdict` 事件的判决切片。

    `released_kinds` = 该判决里 `released=True` 的门种类（读明细时的唯一依据，
    与 `loop_guards._is_soft_verdict` 同口径：空 = 读不到明细 ⇒ **不**算提示类）。
    """

    it: int
    verdict: str
    decider: str
    released_kinds: tuple[str, ...]


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _ts_to_epoch(ts: Any) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return time.mktime(time.strptime(ts, _TS_FMT))
    except ValueError:
        return None


def _released_kinds(readings: Any) -> tuple[str, ...]:
    """判决明细里 `released=True` 的门种类（兼容 dict 与对象两种形态）。

    账本里的 `readings` 是 `[r.to_dict() for r in res.readings]`；调用方也可能
    直接喂对象。两形态都认，缺字段按未 released 处理（保守）。
    """

    def _get(r: Any, key: str, default: Any) -> Any:
        if isinstance(r, dict):
            return r.get(key, default)
        return getattr(r, key, default)

    out: list[str] = []
    if not isinstance(readings, (list, tuple)):
        return ()
    for r in readings:
        if _get(r, "released", False):
            out.append(str(_get(r, "kind", "?")))
    return tuple(sorted(set(out)))


@dataclass
class LedgerView:
    """账本的单次扫描 + 增量视图（字段全部可由事件序列重放得到）。

    读取面分两类：**启动继承**（`next_it`/`rotate_seed`/`ent_peak`/`train_*_total`/
    `kl_streak`/`ent_streak`/`stop_loss_streak`/`soft_remediate_count`）与**只观测**
    （`zero_shard_streak`/`consec_fail`，见模块 docstring）。
    """

    path: Path
    spec: LedgerSpec = field(default_factory=LedgerSpec)
    # ---- 启动继承 ----
    next_it: int = 1
    rotate_seed: int | None = None
    ent_peak: float | None = None
    train_sec_total: float = 0.0
    train_samples_total: float = 0.0
    kl_streak: int = 0
    ent_streak: int = 0
    stop_loss_streak: int = 0
    # ---- 只观测 ----
    zero_shard_streak: int = 0
    consec_fail: int = 0
    # ---- 参考面 ----
    first_run_ts: float | None = None
    first_iter_ts: float | None = None
    iterations_n: int = 0
    rows: list[IterRow] = field(default_factory=list)
    verdicts: list[VerdictRow] = field(default_factory=list)
    ignored_events: int = 0
    bad_lines: int = 0

    # --------------------------------------------------------------- 读取面

    @property
    def last_verdict(self) -> str | None:
        return self.verdicts[-1].verdict if self.verdicts else None

    def soft_remediate_count(self, kinds: frozenset[str]) -> int:
        """提示类（只含 `kinds`）REMEDIATE 的累计次数——I2 停腿判据（§2026-09-13）。

        与内存语义的差别正是修掉的 bug：内存计数随进程重启归零，于是「边际收益枯竭
        的 N 次确认」可以从头再来。口径 = **整条账本**（= 这门课在这条 traj 里的一辈子）；
        换新 traj 重新开课 = 新纪元，计数从 0 起——与「重启不是语义重置」并不矛盾。
        """
        n = 0
        for v in self.verdicts:
            if v.verdict != "REMEDIATE" or not v.released_kinds:
                continue
            if all(k in kinds for k in v.released_kinds):
                n += 1
        return n

    # --------------------------------------------------------------- 增量

    def apply_event(self, event: dict) -> None:
        """把一条刚写进账本的事件并入视图（**唯一**增量入口）。

        与 `load_ledger` 走同一段逻辑 ⇒ 二者对同一事件序列必然一致
        （`tests/test_train_ledger.py::test_incremental_equals_load` 钉住）。
        """
        name = event.get("event")
        if name == "iteration":
            self._on_iteration(event)
        elif name == "run_start":
            self._on_run_start(event)
        elif name == "iter_error":
            self.consec_fail += 1
        elif name == "gate_verdict":
            self._on_verdict(event)
        elif name == "stop_loss":
            self.stop_loss_streak = int(event.get("streak") or 0)
        else:
            # 未知/无关键（job_completed、job_failed、circuit_break、run_complete…）
            self.ignored_events += 1

    def _on_run_start(self, event: dict) -> None:
        rs = event.get("rotateSeed")
        if isinstance(rs, int) and not isinstance(rs, bool):
            self.rotate_seed = rs
        if self.first_run_ts is None:
            self.first_run_ts = _ts_to_epoch(event.get("time"))

    def _on_iteration(self, event: dict) -> None:
        it = event.get("iter")
        if not isinstance(it, int) or isinstance(it, bool):
            self.bad_lines += 1
            return
        samples = _num(event.get("samples")) or 0.0
        epochs = _num(event.get("epochs"))
        epochs = 1.0 if epochs is None else epochs
        # 真训练秒：优先云端自报（ppo_cloud_sec），旧账本无此键回落 ppo_sec
        # （与 gate_check.sum_train_sec 逐字同口径）。
        train_sec = _num(event.get("ppo_cloud_sec"))
        if train_sec is None:
            train_sec = _num(event.get("ppo_sec")) or 0.0
        kl = _num(event.get("kl"))
        entropy = _num(event.get("entropy"))
        win_rate = _num(event.get("winRate"))
        row = IterRow(
            it=it,
            samples=samples,
            epochs=epochs,
            kl=kl,
            entropy=entropy,
            win_rate=win_rate,
            train_sec=train_sec,
            ts=_ts_to_epoch(event.get("time")),
        )
        self.rows.append(row)
        self.iterations_n += 1
        self.next_it = max(self.next_it, it + 1)
        self.train_sec_total += row.train_sec
        self.train_samples_total += samples * epochs
        if self.first_iter_ts is None:
            self.first_iter_ts = row.ts
        self.consec_fail = 0
        # 配额事故计数：连续零样本轮（观测量，见模块 docstring）。
        self.zero_shard_streak = 0 if samples > 0 else self.zero_shard_streak + 1
        # 连击：agg 缺失的轮（流式 checkpoint-complete，无梯度步）不计连击也不更新
        # 峰值——与 `_breaker` 的调用门控一致。判定复用生产纯函数，不重写第二份。
        if kl is None or entropy is None:
            return
        self.kl_streak, self.ent_streak, _tripped = breaker_update(
            self.kl_streak,
            self.ent_streak,
            kl=kl,
            entropy=entropy,
            win_rate=0.0 if win_rate is None else win_rate,
            kl_break=self.spec.kl_break,
            kl_consec=self.spec.kl_consec,
            ent_break=self.spec.ent_break,
            ent_consec=self.spec.ent_consec,
            ent_max_winrate=self.spec.ent_max_winrate,
            ent_peak=self.ent_peak,
        )
        self.ent_peak = entropy if self.ent_peak is None else max(self.ent_peak, entropy)

    def _on_verdict(self, event: dict) -> None:
        verdict = str(event.get("verdict") or "")
        if not verdict:
            self.bad_lines += 1
            return
        it = event.get("iter")
        self.verdicts.append(
            VerdictRow(
                it=it if isinstance(it, int) and not isinstance(it, bool) else -1,
                verdict=verdict,
                decider=str(event.get("decider") or "loop"),
                released_kinds=_released_kinds(event.get("readings")),
            )
        )


def load_ledger(
    jsonl_path: Path, spec: LedgerSpec | None = None, *, args: Any | None = None
) -> LedgerView:
    """一次扫描得到账本视图（每课开课/续跑读一遍；之后走 `apply_event`）。

    `spec`/`args` 二选一：给 `args` 时按 `LedgerSpec.from_args` 推阈值（生产调用面），
    给 `spec` 时直接用（测试/多课程调度器按课注入）。
    """
    if spec is None:
        spec = LedgerSpec.from_args(args) if args is not None else LedgerSpec()
    view = LedgerView(path=jsonl_path, spec=spec)
    if not jsonl_path.exists():
        return view
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    view.bad_lines += 1
                    continue
                if not isinstance(event, dict):
                    view.bad_lines += 1
                    continue
                view.apply_event(event)
    except OSError:
        # 读不到账本 = 首启（正常）或权限问题；返回空视图，由调用方决定是否响亮告警。
        return view
    return view
