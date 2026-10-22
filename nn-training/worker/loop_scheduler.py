"""loop_scheduler —— 单进程多课程的任务调度器（R2c-1，plan/r2-loop-task-queue §4）。

**要解决什么**：`trainingLoop` 现在「一门课一个进程」，而它绝大部分墙钟花在**等**上
（等远端 PPO、等 rollout 子进程、等 eval 尾巴）。用户 2026-09-18 定案：改成**一个进程 +
每课一条任务队列 + 细粒度步骤 + `WAIT` 让位**，资源竞争靠队列串行化
（本机重资源池容量 1，跨课排队）。

**三条不可交易的性质**（本模块的测试逐条钉住）：

1. **同一时刻只跑一个任务**（单线程执行器）——没有锁竞争、没有 GIL 内斗，顺序确定；
2. **`WAIT` 不占执行权也不占资源票**——这才是一门课等云机时另一门课能推进的原因；
3. **故障域按课隔离**：一门课 `ABORT`（门禁停腿 / 5 连击）只脏它自己的队列，
   其它课照常（与 §3.13「停机达令按课程」同口径）。

**排程策略（2026-10-22 用户口径，取代「步级公平轮转」）**：**按课分批** + **采集优先**。

* **按课分批**：一旦选中某门课，就把它的**本 it** 任务连续跑完再换课（让位点仍只有
  `WAIT` / 资源票被挡 / 本轮收尾）——A 课的 rollout 与 B 课的 eval 不再交错；
* **采集恒高于 eval**：没在分批时挑课（首拍 / 本批收尾 / 本课在等外部）的族序 =
  `rollout`/`volume_topup`（`FAMILY_COLLECT`）→ 其余 → `eval_dispatch`/`eval_join`
  （`FAMILY_EVAL`）；同一门课排在评估步骤、而**别的课有采集可跑** ⇒ 本课让位。理由与被否决
  备选见 DECISIONS `§2026-10-22-course-batched-rollout-eval`。

**本模块是调度**，不含训练逻辑：执行体由调用方注入（`executor(task, ctx)`），
因此可以完全脱离 torch / 网络做单测；R2c-2 把它接到真的 `TrainingLoop` 任务体上。

**幂等与重放**（与 `biz/loop_tasks.py` 的分工）：调度器只负责「下一步该谁跑、它能不能跑」；
「这件事在盘上是不是已经做完了」由 `pending_tasks`/`already_done` 依 `RoundFacts` 判定。
两者合起来才是「重启后不重写账本、不重发 job」。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from worker.loop_tasks import ABORT as ABORT_STATUS
from worker.loop_tasks import (
    DONE,
    FAMILY_COLLECT,
    FAMILY_EVAL,
    FAMILY_STEP,
    RETRY,
    WAIT,
    Task,
    TaskResult,
    resolve_failure,
    task_family,
)

#: 重试退避（与现主循环的 `time.sleep(30)` 同值；`now()` 可注入 ⇒ 测试不真睡）。
RETRY_BACKOFF_SEC = 30.0

#: 课程队列状态（控制台/CLI 的读面就是它）。
#: 注意与**任务终态** `biz.loop_tasks.DONE`（"done"）区分：那个是「一个任务做完了」，
#: 这里是「这门课的队列没有可做的事了」；两者数值碰巧同形，但语义与生命周期不同。
READY = "ready"
RUNNING = "running"
WAITING = "waiting"
PAUSED = "paused"
ABORTED = "aborted"
QUEUE_DONE = "done"


class PoolError(RuntimeError):
    """资源池用法错误（未知池名/超发）——配置错必须响亮，不静默降级。"""


@dataclass
class PoolSet:
    """本机重资源的**唯一**容量闸门（R2c 定案：跨课排队、容量 1）。

    「容量」是**票据**数而不是进程数：rollout 池可以 >1（子进程并行），而本机 PPO 与本机
    eval 定案为 1 ⇒ 任一时刻只有一门课在跑它们，其它课要么排在队列里、要么用云机。
    """

    capacities: dict[str, int]

    def __post_init__(self) -> None:
        self._held: dict[str, int] = {name: 0 for name in self.capacities}

    def capacity(self, name: str) -> int:
        if name not in self.capacities:
            raise PoolError(f"未知资源池 {name!r}（已声明：{sorted(self.capacities)}）")
        return int(self.capacities[name])

    def free(self, name: str) -> int:
        return self.capacity(name) - self._held[name]

    def try_acquire(self, name: str) -> bool:
        if self.free(name) <= 0:
            return False
        self._held[name] += 1
        return True

    def release(self, name: str) -> None:
        if self._held.get(name, 0) <= 0:
            raise PoolError(f"资源池 {name!r} 释放了未持有的票（容量记账已失衡）")
        self._held[name] -= 1

    def held(self, name: str) -> int:
        return self._held.get(name, 0)

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            name: {"held": self._held[name], "capacity": int(cap)}
            for name, cap in self.capacities.items()
        }


@dataclass
class CourseQueue:
    """一门课的队列：指针 + 本轮剩余任务 + 在飞集 + 状态。"""

    course: str
    next_it: int
    tasks: list[Task] = field(default_factory=list)
    state: str = READY
    resume_at: float | None = None
    reason: str = ""
    #: task_id → 在飞事实（jid / dispatch / ts）；WAIT 时由执行体从 payload 带出来。
    inflight: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: task_id → 仍被该任务持有的资源池名（`TaskResult.hold` 的账；见 _run_one）。
    holds: dict[str, str] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    rounds_done: int = 0
    #: **本轮已经动过手**（第一个步返回后置 True，`_finish_round`/`_abort` 清 False）。
    #  为什么不能拿 `tasks` 非空当判据：`_finish_round` 会**立刻**为下一轮重新规划
    #  （tasks 又非空），于是「还没开跑的下一轮」与「跑了一半的本轮」形状相同。
    #  消费者 = `EnginePool.pinned`（serve 传「轮内不许抽走引擎」，2026-10-02 事故）。
    mid_round: bool = False

    @property
    def current(self) -> Task | None:
        return self.tasks[0] if self.tasks else None

    def runnable(self, now: float) -> bool:
        if self.state in (PAUSED, ABORTED, QUEUE_DONE):
            return False
        if self.resume_at is not None and now < self.resume_at:
            return False
        return self.current is not None


@dataclass(frozen=True)
class StepTrace:
    """一步调度的可读结论（诊断/测试断言的唯一入口）。"""

    course: str
    action: str  # ran|blocked_pool|idle|round_done|aborted
    task_id: str = ""
    kind: str = ""
    status: str = ""
    detail: str = ""


class Supervisor:
    """单进程调度器：按课分批（采集优先）→ 资源闸门 → 单线程执行 → 按四态收敛。

    `planner(course, it, queue) -> list[Task]`：为某课的某一轮准备任务表（生产实现 =
    `pending_tasks(round_tasks(...), RoundFacts(...))`）；返回空表 = 这门课没有可做事
    （队列进入 `done`）。
    """

    def __init__(
        self,
        executor: Callable[[Task, CourseQueue], TaskResult],
        planner: Callable[[str, int, CourseQueue], list[Task]],
        *,
        capacities: dict[str, int] | None = None,
        pools: PoolSet | None = None,
        now: Callable[[], float] = time.time,
        max_attempts: int = 5,
    ) -> None:
        self.executor = executor
        self.planner = planner
        self.pools = pools or PoolSet(dict(capacities or {}))
        self.now = now
        self.max_attempts = max_attempts
        self.courses: dict[str, CourseQueue] = {}
        self._cursor = 0
        #: 「本批」课程（2026-10-22 按课分批）：上一步跑的课，下一步优先还是它，直到本轮收尾。
        self._last_course: str | None = None
        self.traces: list[StepTrace] = []
        #: 本圈已被资源池挡住的课（全被挡住 = 真没事可做 ⇒ step() 返回 None，不空转）。
        self.blocked_courses: set[str] = set()

    # ------------------------------------------------------------ 课程管理

    def add_course(self, course: str, next_it: int, tasks: list[Task] | None = None) -> CourseQueue:
        q = CourseQueue(course=course, next_it=int(next_it))
        q.tasks = list(tasks) if tasks is not None else list(self.planner(course, q.next_it, q))
        q.state = READY if q.tasks else QUEUE_DONE
        self.courses[course] = q
        return q

    def pause(self, course: str, reason: str = "") -> None:
        q = self.courses[course]
        q.state = PAUSED
        q.reason = reason

    def resume(self, course: str) -> None:
        q = self.courses[course]
        if q.state == PAUSED:
            q.state = READY if q.tasks else QUEUE_DONE
            q.reason = ""

    # ------------------------------------------------------------ 调度

    def _candidates(self, now: float) -> list[CourseQueue]:
        """可跑的课（**按轮转序**，即从上次选中的下一门开始绕一圈）。

        被资源池挡住 / 等外部 / 暂停 / 停腿的课不进候选：按课分批后上一门课会一直是首选，
        「选中它再由 `step` 返回 None」等于两拍才给别的课一次机会——那是饿死，不是排队。
        """
        names = list(self.courses)
        n = len(names)
        out: list[CourseQueue] = []
        for off in range(n):
            q = self.courses[names[(self._cursor + off) % n]]
            if q.course in self.blocked_courses or not q.runnable(now):
                continue
            out.append(q)
        return out

    def _take(self, q: CourseQueue) -> CourseQueue:
        """选中一门课：推进轮转游标，并记下「本批」是哪门课（下一步继续优先它）。"""
        names = list(self.courses)
        self._cursor = (names.index(q.course) + 1) % len(names)
        self._last_course = q.course
        return q

    def _pick(self, now: float) -> CourseQueue | None:
        """挑下一步：**按课分批 + 采集优先**（2026-10-22 用户口径，见模块 docstring）。

        ① **黏住上一门课**：同一门课的某个 it 的任务连续跑完再换课——A 的 rollout 与 B 的
           eval 不再交错（**非评估**步骤不会因为别课有采集而被打断：那会把一门课的准备/PPO/
           落账无限推后，语料并没有更快到账）；
        ② 唯一例外 =「本课排在评估上、别的课有采集可跑」⇒ 让位（**采集恒高于评估**）；
        ③ 没有可黏的课（首拍 / 上一轮已收尾 / 上一门课在等外部）⇒ 采集 → 其余 → 评估，
           同族内仍按轮转序（末位课程不会因为上游一直有采集而永远饿着）。
        """
        cands = self._candidates(now)
        if not cands:
            return None
        sticky: CourseQueue | None = None
        for q in cands:
            if q.course == self._last_course:
                sticky = q
                break
        if sticky is not None:
            cur = sticky.current
            assert cur is not None  # 候选只收 runnable（tasks 非空）
            if task_family(cur.kind) != FAMILY_EVAL:
                return self._take(sticky)
            # 本课在评估上：别的课的采集先跑（采集恒高于评估）
            for q in cands:
                other = q.current
                if other is not None and task_family(other.kind) == FAMILY_COLLECT:
                    return self._take(q)
            return self._take(sticky)
        for family in (FAMILY_COLLECT, FAMILY_STEP, FAMILY_EVAL):
            for q in cands:
                cur = q.current
                if cur is not None and task_family(cur.kind) == family:
                    return self._take(q)
        return None

    def step(self) -> StepTrace | None:
        """跑一步。返回 None = 没事可做（全 WAIT / 全暂停 / 全停腿 / **能跑的都被池挡住**）。

        「被池挡住」也算没事可做：否则调度器会在两个阻塞的课之间空转（单线程下纯烧 CPU，
        而且会把 traces 灌爆）。被挡住的课在 `_pick` 里就被跳过（读面仍可在 `blocked_courses`
        看到它们），全被挡住时返回 None 并**清空**挡住集合——下一拍重新问一遍（局面可能变了）。
        """
        now = self.now()
        q = self._pick(now)
        if q is None:
            self.blocked_courses.clear()
            return None
        task = q.current
        assert task is not None  # runnable 保证
        trace = self._run_one(q, task, now)
        if trace.action == "blocked_pool":
            self.blocked_courses.add(q.course)
        self.traces.append(trace)
        return trace

    def _run_one(self, q: CourseQueue, task: Task, now: float) -> StepTrace:
        pool = task.resource
        # 票可能**上一轮已持有**（后台仍在干活 ⇒ `waiting(hold=True)`），此时不重复领。
        held = q.holds.get(task.task_id)
        if pool is not None and held is None:
            if not self.pools.try_acquire(pool):
                # 资源被别人占着：本课让位（不占执行权），下一步去问别的课。
                return StepTrace(
                    course=q.course,
                    action="blocked_pool",
                    task_id=task.task_id,
                    kind=task.kind,
                    detail=f"{pool} 无空闲票（{self.pools.held(pool)}/{self.pools.capacity(pool)}）",
                )
            held = pool
            q.holds[task.task_id] = pool

        try:
            result = self.executor(task, q)
        except Exception as e:  # 执行体异常 = 一次失败（与主循环的 except 分支同语义）
            result = TaskResult(status=RETRY, reason=f"{type(e).__name__}: {e}")

        # 还票判定：WAIT/RETRY 且声明 hold ⇒ 留着（后台仍在跑）；其余（含终态）一律还。
        keep = result.hold and result.status in (WAIT, RETRY)
        if held is not None and not keep:
            self.pools.release(held)
            q.holds.pop(task.task_id, None)
            # 票回来了 ⇒ 局面变了：被挡住过的课值得再问一次（只影响探测次数，不影响收敛）。
            self.blocked_courses.clear()

        if result.status == DONE:
            q.mid_round = True  # 本轮已动手（若这是最后一步，_finish_round 立刻清 False）
            q.tasks.pop(0)
            q.inflight.pop(task.task_id, None)
            q.completed.append(task.task_id)
            q.attempts.pop(task.task_id, None)
            if not q.tasks:
                return self._finish_round(q)
            q.state = RUNNING
            q.reason = ""  # 已经往下走：上一次的「在等什么」不再是事实
            return StepTrace(
                course=q.course,
                action="ran",
                task_id=task.task_id,
                kind=task.kind,
                status=DONE,
                detail=result.reason,
            )

        if result.status == WAIT:
            # ★ 让位：不占执行权、不占资源票（票已在 finally 归还）；带上在飞事实。
            # `reason` 落在队列上（读面「在等什么」= 状态 + 原因 + 在飞 job_id，三者一起才
            # 够定位）；`_finish_round` / 恢复推进时清掉，避免读到一个过期的原因。
            q.mid_round = True  # 本轮已动手（等外部 = 轮内在飞）
            q.state = WAITING
            q.reason = result.reason
            q.resume_at = result.resume_at
            extra = {k: v for k, v in result.payload.items() if k != "task_id"}
            if extra:
                q.inflight[task.task_id] = dict(extra)
            return StepTrace(
                course=q.course,
                action="ran",
                task_id=task.task_id,
                kind=task.kind,
                status=WAIT,
                detail=result.reason or "等外部",
            )

        if result.status == RETRY:
            # 计数口径 = **已记录的失败次数**（不是 task.attempt + 1——后者在首次失败时
            # 会跳到 2，让 5 连击提前一轮变 4 连击停腿）。
            attempt = q.attempts.get(task.task_id, 0) + 1
            q.attempts[task.task_id] = attempt
            decided = resolve_failure(
                attempt, leg_abort=False, smoke_void=bool(result.payload.get("same_iter"))
            )
            if decided.status == ABORT_STATUS:
                q.tasks.pop(0)
                return self._abort(q, task, decided.reason)
            q.mid_round = True  # 本轮已动手（退避重试中，仍属轮内）
            q.tasks[0] = task.next_attempt()
            q.state = WAITING
            # 退避：下一轮不问它（与主循环 time.sleep(30) 同值；now 可注入 ⇒ 测试不真睡）。
            q.resume_at = now + (0.0 if result.payload.get("same_iter") else RETRY_BACKOFF_SEC)
            return StepTrace(
                course=q.course,
                action="ran",
                task_id=task.task_id,
                kind=task.kind,
                status=RETRY,
                detail=result.reason,
            )

        # ABORT：只脏本课（用户口径 §4.3）
        q.tasks.pop(0)
        return self._abort(q, task, result.reason)

    def _abort(self, q: CourseQueue, task: Task, reason: str) -> StepTrace:
        q.state = ABORTED
        q.reason = reason
        q.mid_round = False  # 停腿：不再有「轮内在用」的引擎需要护住
        self._last_course = None  # 停腿的课不再是「本批」，下一拍从轮转序挑新课
        return StepTrace(
            course=q.course,
            action="aborted",
            task_id=task.task_id,
            kind=task.kind,
            status=ABORT_STATUS,
            detail=reason,
        )

    def _finish_round(self, q: CourseQueue) -> StepTrace:
        """本轮任务清空 → 推进指针并按需准备下一轮（队列为空的课进入 `done`）。"""
        q.rounds_done += 1
        q.next_it += 1
        q.reason = ""
        q.mid_round = False  # 本轮已收尾；下一轮尚未动手 ⇒ 引擎此刻可安全驱逐
        # ★ 按课分批的边界（2026-10-22）：**本 it** 跑完就换课（不是一口气把下一轮也接着跑）。
        self._last_course = None
        q.tasks = list(self.planner(q.course, q.next_it, q))
        q.state = QUEUE_DONE if not q.tasks else READY
        return StepTrace(
            course=q.course,
            action="round_done",
            detail=f"next_it={q.next_it} rounds_done={q.rounds_done}",
        )

    def run_until_idle(self, max_steps: int = 1000) -> list[StepTrace]:
        """跑到没事可做（或步数上限）。**步数上限是防呆**：坏 planner 空转让它响亮停下。"""
        out: list[StepTrace] = []
        for _ in range(max_steps):
            trace = self.step()
            if trace is None:
                return out
            out.append(trace)
            if len(out) > max_steps:
                break
        raise RuntimeError(
            f"Supervisor.run_until_idle: {max_steps} 步仍未停——planner 可能在空转"
            "（每轮返回非空却不推进指针）"
        )

    # ------------------------------------------------------------ 读面

    def snapshot(self) -> dict[str, Any]:
        """控制台/CLI 的读面：每课状态 + 当前任务 + 在飞集 + 资源池占用。"""
        return {
            "pools": self.pools.snapshot(),
            "blocked": sorted(self.blocked_courses),
            "courses": {
                course: {
                    "state": q.state,
                    "next_it": q.next_it,
                    "current": q.current.kind if q.current else "",
                    "pending": [t.kind for t in q.tasks],
                    "inflight": dict(q.inflight),
                    "holds": dict(q.holds),
                    "rounds_done": q.rounds_done,
                    "resume_at": q.resume_at,
                    "reason": q.reason,
                }
                for course, q in self.courses.items()
            },
        }
