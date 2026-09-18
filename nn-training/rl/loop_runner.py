"""loop_runner —— 任务体 ↔ 真 `TrainingLoop` 的唯一桥（R2c-2，plan/r2-loop-task-queue §3/§4）。

`Supervisor`（`rl/loop_scheduler.py`）只认 `Task`/`TaskResult`，不认识训练；本模块是它
与训练引擎之间**唯一**的翻译层，回答两件事：

1. **「下一步是什么」**（`planner`）：读该课账本 → `next_it`；账本比队列旧就跟上账本
   （账本是 SSOT，队列只是本进程的加速器）；课已收官 ⇒ 空表（队列进 `done`）。
2. **「这一步做了什么」**（`run_round`）：调 `TrainingLoop.run_one_round(it)`，把它的
   `RoundOutcome` 映射成四态。

**粒度（诚实记账）**：今天一个任务 = **一轮**。轮内那 13 步需要的轮内局部量
（`pairs`/`dist_cfg`/`t_rollout`/`seg`）还锁在 `run_one_round` 里，提成 `RoundContext`
之后即可细化为 13 步（R2c-3）。粒度只决定**让位点的密度**，不改变本模块或调度器的契约
——所以先把「单进程服务多课 + 让位 + 池闸门」用轮粒度跑通，再把轮体切开。

**`WAIT` 的判据必须来自事实**：本模块只认训练引擎显式提供的 `remote_job_ready(it)` 钩子
（云端 PPO 回传是否已落位）。钩子**不存在**（今天的 `TrainingLoop`）⇒ 不猜、不睡、不轮询，
按「跑完即 DONE」处理——即行为与改造前完全一致；钩子存在（R2c-3 的轮询化实现，或测试里的
假件）⇒ 未就绪就 `WAIT` 让位，把执行权交给别的课。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rl.loop_core import (
    ROUND_BUNDLE_EXIT,
    ROUND_NEXT,
    ROUND_RETRY,
    ROUND_SMOKE_STOP,
    ROUND_STOP,
)
from rl.loop_scheduler import CourseQueue
from rl.loop_tasks import Task, TaskResult, abort, done, retry, waiting

#: `WAIT` 后的再问间隔（秒）——远小于本机轮询密度需求，且不烧 CPU（调度器是事件驱动）。
POLL_INTERVAL_SEC = 15.0

#: 轮粒度任务名（R2c-3 会展开成 `pool`/`ppo`/… 13 个 kind）。
ROUND_KIND = "round"


@dataclass
class LoopRunner:
    """把一门课的 `TrainingLoop` 包成调度器要的 `planner` + `executor`。"""

    loop: Any
    course: str
    iters: int = 0  # 0 = 不限（与 CLI 同语义）
    poll_interval: float = POLL_INTERVAL_SEC
    now: Callable[[], float] = time.time
    finished: bool = False
    finish_reason: str = ""
    #: 执行痕迹（诊断/测试用）：(`it`, outcome_status)
    trace: list[tuple[int, str]] = field(default_factory=list)

    # ---------------------------------------------------------------- planner

    def planner(self, course: str, it: int, queue: CourseQueue) -> list[Task]:
        if self.finished:
            return []
        want = self._ledger_next_it(it)
        if self.iters and want > self.iters:
            self.finished = True
            self.finish_reason = f"iters={self.iters} 跑满"
            return []
        return [Task(course, want, ROUND_KIND, params={})]

    def _ledger_next_it(self, fallback: int) -> int:
        """账本里的下一轮（SSOT）。读不到（首启/IO 故障）⇒ 用队列给的指针。"""
        try:
            from rl.train_ledger import load_ledger

            traj = Path(self.loop.args.traj)
            view = load_ledger(traj / "training_log.jsonl")
            return max(int(view.next_it), int(fallback))
        except Exception:  # 观测/读盘失败不得卡住调度（保守：用队列指针）
            return int(fallback)

    # --------------------------------------------------------------- executor

    def run_round(self, task: Task, queue: CourseQueue) -> TaskResult:
        it = task.it
        # ① 事实判据：远端 PPO 回传是否已到（钩子不存在 = 今天的行为，跑完即 DONE）
        ready = getattr(self.loop, "remote_job_ready", None)
        if callable(ready) and not ready(it):
            jid = getattr(self.loop, "inflight_job_id", lambda _it: None)(it)
            return waiting(
                self.now() + self.poll_interval,
                f"等远端 PPO 回传（it{it}）",
                hold=False,
                jid=jid,
                round=str(it),
            )
        # ② 真跑一轮（fakes 之下不碰 rollout/ppo/eval 的真运算）
        try:
            out = self.loop.run_one_round(it)
        except Exception as e:  # 引擎异常 = 一次失败（重试语义与主循环一致）
            return retry(f"{type(e).__name__}: {e}")
        self.trace.append((it, out.status))
        if out.status == ROUND_NEXT:
            return done(it=out.it)
        if out.status == ROUND_RETRY:
            # 主循环里这是「it 原地重试」：不判 5 连击（那由引擎内部 _consec_fail 负责）
            return retry("本轮作废，it 原地重试", same_iter=True)
        if out.status == ROUND_SMOKE_STOP:
            self.finished = True
            self.finish_reason = "smoke-void"
            return done(it=out.it, final=True)
        if out.status == ROUND_BUNDLE_EXIT:
            self.finished = True
            self.finish_reason = "全离线任务包已导出"
            return done(it=out.it, final=True)
        if out.status == ROUND_STOP:
            # 硬边界（门 / 熔断 / 止损 / 预算 / 停腿）：整条腿正常收工，**不是**调度层失败。
            self.finished = True
            self.finish_reason = "硬边界停车（门/熔断/止损/预算）"
            return done(it=out.it, final=True)
        return abort(f"未知轮终态 {out.status!r}")
