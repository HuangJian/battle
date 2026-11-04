"""eval_gate —— 进程级 eval 优先级开关：rollout 活跃期冻结 eval **新投递**（2026-11-04）。

用户口径（plan/cloud-pool-cross-round.plan.md）：

* rollout 一起来，**在飞 eval 局不动**，只停止派发新的 eval 局，直到 rollout 收尾；
* rollout 之间，多轮 eval **平等**共享节点——不再排队、不再「一轮跑完才轮到下一轮」。

调度本体已下移到任务级仲裁（`worker/eval_arbiter.py` + `worker/eval_exec.py`）：
线程按节点住进程级池，下一局派给谁由 `EvalArbiter.take()` 按优先级决定。本模块只剩：

* `rollout()` / `begin_rollout()` / `end_rollout()`：引用计数的冻结/解冻（嵌套安全）；
* `submit(job, name)`：起一条后台线程跑整轮 `job()`，**立即开跑**（旧 FIFO 槽位已退役）。

`status()` 的键保持旧形状（`queued` 恒 0，`running` = 有整轮在跑）：`loop_eval` 的
`gate_idle` 补账判据不动——当前读数还在跑时不往池里再灌旧缺口。

为什么是**进程级**而不是按课程：生产是**一个** `run_rl_cluster --serve` 进程装所有课程
（`trainer/loop_serve.py` 的 Supervisor 单线程串行跑各课步骤）⇒ 全局一个冻结计数就够，
也不需要跨进程同步。手工 `evalA` / `dispatch_eval_round` 直调不过闸（人点名的一次性评估）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from worker.eval_arbiter import eval_arbiter


class _RolloutScope:
    """`with gate.rollout():` 的薄壳（只做计数 + 冻结，不吞异常）。"""

    def __init__(self, gate: EvalGate) -> None:
        self._gate = gate

    def __enter__(self) -> None:
        self._gate.begin_rollout()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._gate.end_rollout()


class EvalGate:
    """rollout 冻结计数 + 整轮后台执行（语义见模块 docstring）。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._running = 0
        self._rollouts = 0
        self._completed = 0

    # —— 调用面 ——

    def rollout(self) -> _RolloutScope:
        """标记一段 rollout 工作：期间 eval 不派**新**局（在飞局自然落账）。"""
        return _RolloutScope(self)

    def begin_rollout(self) -> None:
        with self._cond:
            self._rollouts += 1
        eval_arbiter().freeze()

    def end_rollout(self) -> None:
        with self._cond:
            if self._rollouts <= 0:
                # 计数已归零 ⇒ 本闸没有可 thaw 的冻结。再调 `thaw()` 会去减**别的**
                # 作用域的引用计数（多层 rollout 逐层解冻时不会发生；这里只防误用）。
                return
            self._rollouts -= 1
            self._cond.notify_all()
        eval_arbiter().thaw()

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
        """读面（日志/诊断）：`queued` 恒 0（无排队概念），`running`/`active` = 在跑轮数。"""
        with self._cond:
            return {
                "queued": 0,
                "running": self._running > 0,
                "active": self._running,
                "rollouts": self._rollouts,
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
