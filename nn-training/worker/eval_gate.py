"""eval_gate —— 进程级 eval 优先级开关：rollout 活跃期冻结 eval **新投递**（2026-11-04）。

用户口径（plan/cloud-pool-cross-round.plan.md）：

* rollout 一起来，**在飞 eval 局不动**，只停止派发新的 eval 局，直到 rollout 收尾；
* rollout 之间，多轮 eval **平等**共享节点——不再排队、不再「一轮跑完才轮到下一轮」。

调度本体已下移到任务级仲裁（`worker/eval_arbiter.py` + `worker/eval_exec.py`）：
线程按节点住进程级池，下一局派给谁由 `EvalArbiter.take()` 按优先级决定。本模块只剩：

* `rollout(course)` / `begin_rollout(course)` / `end_rollout(course)`：引用计数的
  冻结/解冻（嵌套安全）；
* `submit(job, name)`：起一条后台线程跑整轮 `job()`，**立即开跑**（旧 FIFO 槽位已退役）。

`status()` 的键保持旧形状（`queued` 恒 0，`running` = 有整轮在跑，`rollouts` = 进程级
+ 各课作用域计数之和）。

为什么闸是**进程级单例**：生产是**一个** `run_rl_cluster --serve` 进程装所有课程
（`trainer/loop_serve.py` 的 Supervisor 单线程串行跑各课步骤）⇒ 一个闸对象即可，
不需要跨进程同步。但**冻结作用域按课程**（2026-11-04 §113）：两门课交替采集时，
A 课采集只停 A 课的 eval——旧进程级冻结把 B 课的 eval 也一起停掉，实测覆盖 75% 墙钟。
`course=None` 保留进程级冻结（手工 `evalA` / 无课程身份的旧路径）。
手工 `evalA` / `dispatch_eval_round` 直调不过闸（人点名的一次性评估）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from worker.eval_arbiter import eval_arbiter


class _RolloutScope:
    """`with gate.rollout():` 的薄壳（只做计数 + 冻结，不吞异常）。"""

    def __init__(self, gate: EvalGate, course: str | None) -> None:
        self._gate = gate
        self._course = course

    def __enter__(self) -> None:
        self._gate.begin_rollout(self._course)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._gate.end_rollout(self._course)


class EvalGate:
    """rollout 冻结计数 + 整轮后台执行（语义见模块 docstring）。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running = 0
        self._rollouts = 0
        self._course_rollouts: dict[str, int] = {}
        self._completed = 0

    # —— 调用面 ——

    def rollout(self, course: str | None = None) -> _RolloutScope:
        """标记一段 rollout 工作：期间该课 eval 不派**新**局（在飞局自然落账）。

        `course=None` = 进程级冻结（手工/旧调用方）；生产 in-loop 传课程指纹（§113）。
        """
        return _RolloutScope(self, course)

    def begin_rollout(self, course: str | None = None) -> None:
        # 冻结与计数在同一把闸锁里完成：解冻/再冻结交错时不会把别人的引用计数
        # 记到自己头上（仲裁器只拿自己的锁，不会再回调本闸）。
        with self._cond:
            if course is None:
                self._rollouts += 1
            else:
                self._course_rollouts[course] = self._course_rollouts.get(course, 0) + 1
            eval_arbiter().freeze(course)

    def end_rollout(self, course: str | None = None) -> None:
        with self._cond:
            if course is None:
                if self._rollouts <= 0:
                    # 计数已归零 ⇒ 本闸没有可 thaw 的进程级冻结。再调 `thaw()` 会去减
                    # **别的**作用域的引用计数（这里只防误用）。
                    return
                self._rollouts -= 1
            else:
                n = self._course_rollouts.get(course, 0)
                if n <= 0:
                    return
                if n == 1:
                    self._course_rollouts.pop(course, None)
                else:
                    self._course_rollouts[course] = n - 1
            self._cond.notify_all()
            eval_arbiter().thaw(course)

    def submit(self, job: Callable[[], object], *, name: str = "") -> threading.Thread:
        """起一条 daemon 线程执行整轮 `job()`；返回的线程 join 到整轮真正收工为止。"""
        thread = threading.Thread(
            target=self._serve, args=(job,), daemon=True, name=name or "eval-gate"
        )
        with self._cond:
            self._running += 1
        thread.start()
        return thread

    def status(self) -> dict:
        """读面（日志/诊断）：`queued` 恒 0（无排队概念），`running`/`active` = 在跑轮数，
        `rollouts` = 进程级 + 各课作用域计数之和。"""
        with self._cond:
            return {
                "queued": 0,
                "running": self._running > 0,
                "active": self._running,
                "rollouts": self._rollouts + sum(self._course_rollouts.values()),
                "completed": self._completed,
            }

    # —— 整轮执行体 ——

    def _serve(self, job: Callable[[], object]) -> None:
        try:
            job()
        except Exception:
            # 派发器已自带兜底；这里再守一道：任务异常不得变成未捕获的线程异常。
            pass
        finally:
            with self._cond:
                self._running = max(0, self._running - 1)
                self._completed += 1
                self._cond.notify_all()


_GATE = EvalGate()


def eval_gate() -> EvalGate:
    """进程级单例（生产唯一入口；单测可自建 `EvalGate()` 或替换 `_GATE`）。"""
    return _GATE
