"""loop_runner —— 任务体 ↔ 真 `TrainingLoop` 的唯一桥（R2c-2/R2c-3，plan/r2-loop-task-queue §3/§4）。

`Supervisor`（`rl/loop_scheduler.py`）只认 `Task`/`TaskResult`，不认识训练；本模块是它
与训练引擎之间**唯一**的翻译层，回答三件事：

1. **「下一步是什么」**（`planner`）：读该课账本 → `next_it`；账本比队列旧就跟上账本
   （账本是 SSOT，队列只是本进程的加速器）；课已收官 ⇒ 空表（队列进 `done`）。
2. **「这一步做了什么」**（`executor`）：`Task.kind` 决定走哪条路——
   · `round`（`ROUND_KIND`，轮粒度）：`run_round` → `TrainingLoop.run_one_round`；
   · 13 个步骤 kind（R2c-3，**细粒度**）：`run_step` → 引擎的 `step_*`（表见
     `rl.loop_round.STEP_METHOD`）。
3. **「这一步算完/要等什么」**（四态映射）：`RoundOutcome` / `StepResult` → `DONE` / `WAIT` /
   `RETRY` / `ABORT`。

**粒度只决定让位点的密度**，不改变契约：轮粒度下一个任务 = 一轮；细粒度下一个任务 = 一步
（`planner` 用 `pending_tasks(round_tasks(...), RoundFacts)` 出表，顺序即 `ROUND_TASKS`）。
两条路都通过同一张步骤表落到同一份引擎实现上，所以**不可能漂移**。

**`WAIT` 的判据必须来自事实**：本模块只认训练引擎显式提供的 `*_ready(it)` 钩子，按
`WAIT_HOOKS`（kind → 钩子名）查表。钩子**不存在**（未改造的引擎路径）⇒ 不猜、不睡、
不轮询，按「跑完即 DONE」处理——行为与改造前完全一致；钩子存在（R2c-3 的轮询化实现，
或测试里的假件）⇒ 未就绪就让位（`WAIT`，不占执行权、不占资源票），把执行权交给别的课。

表里今天只有一处（让位点按需增加），其余两处**刻意不在**：

- ✅ `precollect_join` → `TrainingLoop.precollect_ready`：预采子进程的 shard 还没到半波时
  步骤内部会**轮询到 1 小时**（`join_precollect_child`）——正是该让位的形态；
- ✅ `ppo`（**不在表里**，因为让位点已经移进步骤）——R2c-3 把 `_remote_ppo` 拆成
  「发布 / 等结果 / 落位」三相后，`step_ppo` 自己就会在「已发布、还没回」时说 `wait_for`。
  表里的闸门跑在**步骤之前**，对 ppo 来说那等于「还没发布」，挡住了就永远等不到回传——
  这正是本模块早先不敢装它的原因，所以现在也**不要**把它加回来（`tests/test_loop_runner.py`
  有用例钉住这个缺席）。
- ❌ `eval_join`：本机 eval 局的墙钟是「藏在下一轮 rollout 里」的（尾巴交棒 + 下轮收拢）。
  给它让位 = 把那条尾巴重新串回轮边界，正好抵消掉当初压掉的软等窗口（文档里写明，防再犯）。

**轮内上下文**：细粒度路径每课持一份 `RoundContext`（`pairs`/`dist_cfg`/`t_rollout`/`seg`
以及会被半离线整段推进的 `it`）——它**只活在内存**，且只在同一轮内有效（`it` 变了就换新的）。
指针的权威始终是账本；进程重开时盘上判据（`RoundFacts`）决定从哪一步接着做。
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
    RoundOutcome,
)
from rl.loop_round import STEP_METHOD, RoundContext, StepResult
from rl.loop_scheduler import CourseQueue
from rl.loop_tasks import (
    ROUND_TASKS,
    RoundFacts,
    Task,
    TaskResult,
    abort,
    done,
    pending_tasks,
    retry,
    round_tasks,
    waiting,
)

#: `WAIT` 后的再问间隔（秒）——远小于本机轮询密度需求，且不烧 CPU（调度器是事件驱动）。
POLL_INTERVAL_SEC = 15.0

#: 轮粒度任务名（细粒度模式用 `ROUND_TASKS` 里的 13 个 kind）。
ROUND_KIND = "round"

#: 步骤 kind → 引擎钩子名（回答「现在能进这一步吗」）。钩子不存在 ⇒ 不当场让位，
#: 行为与改造前逐字节一致（见模块 docstring）——所以这张表可以只先装能吃的两处。
#:
#: ★ `eval_join` **故意不在表里**：本机 eval 局的墙钟是「藏在下一轮 rollout 里」的
#: （`_eval_tail` 交棒、`_dispatch_delayed_eval` 入口收拢，2026-09-17 用户口径）。
#: 给它加让位 = 把那条尾巴重新串回轮边界上，正好抵消掉当初压掉的软等窗口。
WAIT_HOOKS: dict[str, str] = {
    # 预采子进程：shard 还没到半波 ⇒ 让位，别让整条腿等着（步骤内部循环会等 1 小时）。
    "precollect_join": "precollect_ready",
}

#: 让位原因（日志/读面用；缺省按 kind 生成）。
WAIT_REASONS: dict[str, str] = {
    "precollect_join": "等上一轮预采子进程的 shard 就绪",
    "ppo": "等远端 PPO 回传",
}


@dataclass
class LoopRunner:
    """把一门课的 `TrainingLoop` 包成调度器要的 `planner` + `executor`。

    `step_mode=True` 走细粒度（13 步）；默认轮粒度（一个任务 = 一轮），既有调用点不变。
    `facts_fn` 注入「盘上事实」的来源（生产实现 = `rl.loop_plan`；测试直接喂 `RoundFacts`）
    ——注入而非内置 IO，本模块保持可单测。
    """

    loop: Any
    course: str
    iters: int = 0  # 0 = 不限（与 CLI 同语义）
    poll_interval: float = POLL_INTERVAL_SEC
    now: Callable[[], float] = time.time
    finished: bool = False
    finish_reason: str = ""
    #: 执行痕迹（诊断/测试用）：(`it`, outcome_status 或 `step:<kind>`)
    trace: list[tuple[int, str]] = field(default_factory=list)
    #: 细粒度模式（13 步任务表）与盘上事实来源。
    step_mode: bool = False
    facts_fn: Callable[[str, int], RoundFacts] | None = None
    #: 每课一份轮内上下文（仅内存、仅本轮；`it` 变了即换新）。
    contexts: dict[str, RoundContext] = field(default_factory=dict)

    # ---------------------------------------------------------------- planner

    def planner(self, course: str, it: int, queue: CourseQueue) -> list[Task]:
        if self.finished:
            return []
        want = self._ledger_next_it(it)
        if self.iters and want > self.iters:
            self.finished = True
            self.finish_reason = f"iters={self.iters} 跑满"
            return []
        if self.step_mode:
            tasks = self._pending_steps(course, want)
            if not tasks:
                # 盘上判据说本轮已经结算（`iteration_recorded`）⇒ 本课无活可干。
                # 账本是 SSOT：这里**不**自作主张加一轮，新的一轮由账本指针（`want`）决定。
                self.finished = True
                self.finish_reason = f"it{want} 已按账本结算"
                return []
            return tasks
        return [Task(course, want, ROUND_KIND, params={})]

    def _pending_steps(self, course: str, it: int) -> list[Task]:
        return pending_tasks(round_tasks(course, it), self.facts(course, it))

    def facts(self, course: str, it: int) -> RoundFacts:
        """本轮盘上事实（`facts_fn` 未注入 ⇒ 全 unknown：判据不可用则宁可重做）。"""
        if self.facts_fn is None:
            return RoundFacts(it=it)
        return self.facts_fn(course, it)

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

    def executor(self, task: Task, queue: CourseQueue) -> TaskResult:
        """调度器入口：按 `Task.kind` 分派（轮粒度 / 13 步细粒度）。"""
        if task.kind == ROUND_KIND:
            return self.run_round(task, queue)
        if task.kind not in STEP_METHOD:
            return abort(f"未知任务 kind {task.kind!r}（既不是 {ROUND_KIND} 也不在步骤表里）")
        return self.run_step(task, queue)

    # ---- 轮粒度 ----------------------------------------------------------

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
        return self._map_outcome(out)

    # ---- 细粒度（13 步） --------------------------------------------------

    def context(self, course: str, it: int) -> RoundContext:
        """取该课**本轮**的上下文（`it` 变了 ⇒ 换新的；轮内上下文不得跨轮复用）。

        `resumable=True`：**这条路径有让位点**（调度器会接手）——步骤据此选择「未就绪就
        让位」而不是就地阻塞（`run_one_round` 造的 ctx 保持默认 False，于是组合路径
        行为不变）。
        """
        ctx = self.contexts.get(course)
        if ctx is None or ctx.it != it:
            ctx = RoundContext(it=it, resumable=True)
            self.contexts[course] = ctx
        return ctx

    def run_step(self, task: Task, queue: CourseQueue) -> TaskResult:
        """跑一个步骤（`Task.kind` ∈ `ROUND_TASKS`）。"""
        ctx = self.context(task.course, task.it)
        method = STEP_METHOD[task.kind]
        fn = getattr(self.loop, method, None)
        if fn is None:  # 引擎缺这一步的实现：响亮失败（不静默跳过一步）
            return abort(f"引擎缺少步骤实现 {method}（loop_round.STEP_METHOD 与引擎不同步）")
        # ① 事实判据：这一步是否需要等外部（钩子不存在 = 不等）
        gate = self._wait_gate(task.kind, ctx)
        if gate is not None:
            return gate
        # ② 跑这一步
        try:
            res = fn(ctx)
        except Exception as e:
            # 与组合路径**共用一份判决**（连击 / iter_error 账 / 死腿）；退避交给调度器。
            try:
                out = self.loop.round_failure(e, ctx.it, backoff=False)
            except BaseException as raised:  # 5 连击 / 死腿：上抛给调度器（它会收敛到 ABORT）
                return retry(f"{type(raised).__name__}: {raised}")
            return self._map_outcome(out)
        # ③ 结局：终态 → 四态；否则这一步算完（WAIT 由上面的判据产生）
        if res is not None and isinstance(res, StepResult) and res.is_wait:
            # `jid`：这一轮在等谁（远端 PPO 会话刚发布的那份 job）。让位是**每步**都可能
            # 发生的事，而「等什么、等谁」分开记才够定位（读面/事故考古都靠它）。
            jid = ctx.remote.jid if ctx.remote is not None else None
            return waiting(
                self.now() + self.poll_interval,
                res.reason,
                hold=False,
                jid=jid,
                round=str(ctx.it),
            )
        if res is not None and res.is_final:
            return self._map_outcome(RoundOutcome(res.outcome or ROUND_NEXT, ctx.it))
        ctx.mark(task.kind)
        self.trace.append((ctx.it, f"step:{task.kind}"))
        return done(it=ctx.it)

    def _wait_gate(self, kind: str, ctx: RoundContext) -> TaskResult | None:
        """这一步是否该让位（等外部事实）。``None`` = 可以往下走。"""
        hook = WAIT_HOOKS.get(kind)
        if hook is None:
            return None
        ready = getattr(self.loop, hook, None)
        if not callable(ready) or ready(ctx.it):
            return None
        # `hold=False`：本机没在替这一步干活（预采是子进程、PPO 在云机）⇒ 票还掉，
        # 让别的课用机器。「后台仍在跑」那一类（本机 eval 局）才需要 `hold=True`。
        jid = getattr(self.loop, "inflight_job_id", lambda _it: None)(ctx.it)
        reason = WAIT_REASONS.get(kind, f"等外部事实（{kind}）")
        return waiting(
            self.now() + self.poll_interval,
            f"{reason}（it{ctx.it}）",
            hold=False,
            jid=jid,
            round=str(ctx.it),
        )

    # ---- 四态映射（两条路共用） -------------------------------------------

    def _map_outcome(self, out: RoundOutcome) -> TaskResult:
        self.trace.append((out.it, out.status))
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


#: 13 步顺序（供读面/测试断言）：与 `ROUND_TASKS` 同源。
STEP_KINDS: tuple[str, ...] = ROUND_TASKS
