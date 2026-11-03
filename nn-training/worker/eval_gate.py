"""eval_gate —— 进程级 eval 排队闸：**rollout 绝对优先，eval 只吃间隙**（2026-11-03 用户口径）。

调度规则（写死在本模块，派发器只是消费它）：

* **排队**：同一进程里同时只有一轮 eval 在跑；后来的 eval 取号排队，前一轮没结束就不启动
  （「不管多少课程，rollout 和 eval 都是排队」「前一个 eval 未完成，不启动后面的 eval」）。
* **rollout 优先**：`rollout()` 作用域一开，正在跑的 eval 立刻被插队（`Preempt` 置位 +
  钩子收手）；队列里的 eval 在 rollout 期间一律不启动。
* **被插队 ≠ 取消**：任务留在**队头**，下一个间隙按同一张票续跑（派发器靠已落账的
  `(wver, stage, seed)` 只补缺口），所以后一轮 eval 不会先跑。
* **不抢工作权**：本闸不决定节点份额（那是 `eval_yield.eval_slot_cap` 的事）；它只回答
  「现在能不能开跑」。

为什么是**进程级**而不是按课程：生产是**一个** `run_rl_cluster --serve` 进程装所有课程
（`trainer/loop_serve.py` 的 Supervisor 单线程串行跑各课步骤）⇒ 全局一张闸就够，也不需要
跨进程同步。手工 `evalA` / `dispatch_eval_round` 直调不过闸（人点名的一次性评估）。

为什么单独成模块：本模块只依赖 stdlib（`threading`/`collections`），是**零依赖叶子**；
派发器 `trainer/eval_dispatch.py` 贴 LOC 上限，闸的等待/唤醒状态机放回去会超预算。
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

#: 条件变量等待的单拍上限（秒）：只是「定时醒来重判」的兜底，正常路径靠 notify。
WAIT_TICK_SEC = 0.2


class Preempt:
    """一次 eval 执行期的让位令牌：`is_set()` = 有新 rollout 到了，立刻收手。

    派发器持有它并注册钩子（收工事件 / 断连在飞请求）；`EvalGate` 在 rollout 开始时
    `fire()`。令牌是**每执行一次一个**（被插队后续跑会拿到新令牌），所以钩子列表不必回收。
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._hooks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def add_hook(self, fn: Callable[[], None]) -> None:
        """注册让位钩子；令牌**已经置位**时立刻补 fire（派发器注册晚于插队的竞态）。

        「查已置位」与 `fire` 的「置位 + 取钩子」在同一把锁里 ⇒ 同一个钩子只会被叫一次
        （否则注册与插队撞车时钩子会跑两遍）。
        """
        with self._lock:
            self._hooks.append(fn)
            already = self._event.is_set()
        if already:
            _fire_safely(fn)

    def fire(self) -> None:
        """置位并叫醒钩子（幂等：重叠 rollout 的第二次 fire 不再重复叫）。"""
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            hooks = list(self._hooks)
        for fn in hooks:
            _fire_safely(fn)


def _fire_safely(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:  # 钩子是让位的尽力而为；它抛错不得挡住其它钩子/闸的唤醒
        pass


class _RolloutScope:
    """`with gate.rollout():` 的薄壳（只做计数 + 唤醒，不吞异常）。"""

    def __init__(self, gate: EvalGate) -> None:
        self._gate = gate

    def __enter__(self) -> None:
        self._gate.begin_rollout()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._gate.end_rollout()


class EvalGate:
    """FIFO 取号 + 单飞 + rollout 插队（语义见模块 docstring）。

    `submit(job)` 里的 `job(token)` 返回**真值 = 被插队、要续跑**（票留在队头），
    假值 = 已完成（出队、下一票开跑）。任务自身不得抛异常（派发器已顶层吞掉）。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._queue: deque[int] = deque()
        self._next_ticket = 0
        self._running: Preempt | None = None
        self._rollouts = 0
        self._completed = 0

    # —— 调用面 ——

    def rollout(self) -> _RolloutScope:
        """标记一段 rollout 工作：期间 eval 不启动，正在跑的 eval 被插队。"""
        return _RolloutScope(self)

    def begin_rollout(self) -> None:
        with self._cond:
            self._rollouts += 1
            token = self._running
        if token is not None:
            token.fire()

    def end_rollout(self) -> None:
        with self._cond:
            self._rollouts = max(0, self._rollouts - 1)
            self._cond.notify_all()

    def submit(self, job: Callable[[Preempt], bool], *, name: str = "") -> threading.Thread:
        """取号并起一条 daemon 线程执行 `job`；返回的线程 join 到**任务真正完成**为止。

        被插队时线程不退出，它等下一个间隙续跑同一张票——所以 join 能直接当
        「这一轮 eval 收工」的观测点（`_join_eval` / drain 的既有用法）。
        """
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._queue.append(ticket)
            self._cond.notify_all()
        thread = threading.Thread(
            target=self._serve,
            args=(ticket, job),
            daemon=True,
            name=name or f"eval-gate-{ticket}",
        )
        thread.start()
        return thread

    def status(self) -> dict:
        """读面（日志/诊断）：排队长、是否在跑、当前 rollout 数。"""
        with self._cond:
            return {
                "queued": len(self._queue),
                "running": self._running is not None,
                "rollouts": self._rollouts,
                "completed": self._completed,
            }

    # —— 票的执行体 ——

    def _serve(self, ticket: int, job: Callable[[Preempt], bool]) -> None:
        while True:
            token = Preempt()
            with self._cond:
                while self._rollouts > 0 or not self._queue or self._queue[0] != ticket:
                    self._cond.wait(WAIT_TICK_SEC)
                self._running = token
            preempted = False
            try:
                preempted = bool(job(token))
            except Exception:
                # 派发器已自带兜底；这里再守一道：任务异常不得变成**未捕获的线程异常**
                # （票按「已完成」出队，队列继续推进）。
                preempted = False
            finally:
                with self._cond:
                    self._running = None
                    if not preempted:
                        try:
                            self._queue.remove(ticket)
                        except ValueError:
                            pass
                        self._completed += 1
                    self._cond.notify_all()
            if not preempted:
                return


_GATE = EvalGate()


def eval_gate() -> EvalGate:
    """进程级单例（生产唯一入口；单测可自建 `EvalGate()` 或替换 `_GATE`）。"""
    return _GATE
