"""loop_tasks —— 训练循环的任务模型（R2b，plan/r2-loop-task-queue §3）。

**为什么需要它**：今天的一轮是 `run()` 里的 190 行顺序脚本（rollout → 派 eval → PPO →
导权重 → join eval → 落账 → 门 → 轮转 → 预采），跨轮状态全在进程内存 ⇒ ① 一步阻塞整条腿
阻塞；② 进程一死，「这一轮跑到哪一步、已发布哪些 job」无从得知。任务模型把一轮拆成
**可重入的细粒度步骤**，每步自带执行所需的一切、且**先有盘上判据**（幂等 guard）——
这条同时让「重放安全」与「扫账本」变成同一件事（用户 2026-09-18 定案）。

**四态执行器契约**（`TaskResult`）：

```
DONE   落账完成 → 出队，推进本课队列
WAIT   需要等外部（远程 PPO / eval 尾巴 / 预采子进程）→ 带 resume_at 归队，**不占执行权**
RETRY  本轮失败 → attempt+1（≤5）+ 退避，it 不前跳（沿用现语义）
ABORT  腿停（R9 停腿 / I2 停腿）→ 只脏**本课**（与停机按课程 §3.13 一致）
```

`WAIT` 不占执行权是**单进程服务多课程**的关键：一门课等云机时，另一门课的 rollout 照跑
（今天「it5 的 eval 藏在 it6 的 PPO 空隙里」在队列语义下是免费得到的性质）。

**本模块只放纯逻辑**（无 torch / 无网络 / 无文件 IO）：队列推进、幂等判据、任务表。
执行与调度在 R2c 的 supervisor；本模块的函数可被单测直接喂事实（`RoundFacts`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- 四态 ------------------------------------------------------------------

DONE = "done"
WAIT = "wait"
RETRY = "retry"
ABORT = "abort"


@dataclass(frozen=True)
class TaskResult:
    """任务的终态（四态之一）。构造走下面四个工厂，避免各处手拼 status。

    `hold`（R2c）：**WAIT/RETRY 时是否继续持有资源票**。默认 False = 让位即还票，也就是
    「我这一步在等外部，没占用本机资源」。置 True 的场景是**后台仍在干活**：例如本机
    eval 的局还在子进程/线程里跑（`eval_join` 先回来等它），此时票必须留着，否则另一门课
    的本地重资源会插进来把机器压爆。
    """

    status: str
    resume_at: float | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    hold: bool = False

    @property
    def is_terminal(self) -> bool:
        """DONE / ABORT = 出队；WAIT / RETRY = 归队（带各自的唤醒条件）。"""
        return self.status in (DONE, ABORT)

    def __post_init__(self) -> None:
        if self.status not in (DONE, WAIT, RETRY, ABORT):
            raise ValueError(f"TaskResult: 未知状态 {self.status!r}")


def done(**payload: Any) -> TaskResult:
    return TaskResult(status=DONE, payload=dict(payload))


def waiting(
    resume_at: float | None, reason: str = "", *, hold: bool = False, **payload: Any
) -> TaskResult:
    """等外部（远程结果 / eval 尾巴 / 子进程）——`resume_at` = 下次可再问的时刻。

    `hold=True` = 后台工作仍在跑，**不要把资源票还掉**（见 `TaskResult.hold`）。
    """
    return TaskResult(
        status=WAIT, resume_at=resume_at, reason=reason, payload=dict(payload), hold=hold
    )


def retry(reason: str, *, hold: bool = False, **payload: Any) -> TaskResult:
    return TaskResult(status=RETRY, reason=reason, payload=dict(payload), hold=hold)


def abort(reason: str, **payload: Any) -> TaskResult:
    return TaskResult(status=ABORT, reason=reason, payload=dict(payload))


# ---- 任务 ------------------------------------------------------------------

#: 一轮的标准步骤（顺序即依赖顺序；plan/r2-loop-task-queue §3.1 的 13 项）。
#: 前 6 项属**采集**，其后是**训练/结算**——R2c 的调度器按资源类给它们分池。
ROUND_TASKS: tuple[str, ...] = (
    "prepare_iter",  # 清场/建目录（幂等：目录已备即跳过）
    "hot_reload",  # 课程热加载（纯函数：文件指纹）
    "course_iter",  # 本轮课程上下文（holder + ppo_schedule）
    "precollect_join",  # 收上一轮预采子进程（子进程已消失 ⇒ 无产出，继续）
    "rollout",  # 采集（本机槽位 / 云）+ 动态补波
    "volume_topup",  # 配额补波（v1 串行路径）
    "eval_dispatch",  # 为上一轮已完成权重派发干净评估
    "ppo",  # 本地梯度步 / 远端发布 + 等待
    "export_weights",  # 权重导出 + 归档（幂等：已归档即跳过）
    "eval_join",  # 收评估（软等窗口内）
    "record_iteration",  # 落 `iteration` 事件（★ 幂等铁律：账本已有该 it 的行 ⇒ 绝不重写）
    "gate",  # 门禁求值（读 LedgerView）
    "cleanup",  # 目录轮转 + 自动巡检 + 下一轮预采
)

#: 需要**独占资源**的步骤 → 资源池名（R2c 的容量控制；其余步骤不占池）。
RESOURCE_OF: dict[str, str] = {
    "rollout": "local_rollout",
    "volume_topup": "local_rollout",
    "ppo": "local_ppo",
    "eval_join": "eval_local",
}


@dataclass(frozen=True)
class Task:
    """一个可重入的步骤实例。

    `params` 自带执行所需的一切（不得回头读进程内跨轮状态——否则单进程多课程必然串课）；
    `task_id` 是幂等键：(course, it, kind) 唯一确定一件事，重试时 `attempt` 才变。
    """

    course: str
    it: int
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1

    @property
    def task_id(self) -> str:
        return f"{self.course}:it{self.it}:{self.kind}"

    @property
    def resource(self) -> str | None:
        return RESOURCE_OF.get(self.kind)

    def next_attempt(self) -> Task:
        return Task(
            course=self.course, it=self.it, kind=self.kind, params=self.params, attempt=self.attempt + 1
        )


def round_tasks(course: str, it: int, params: dict[str, Any] | None = None) -> list[Task]:
    """一轮的完整任务序列（顺序即依赖顺序）。"""
    base = dict(params or {})
    return [Task(course=course, it=it, kind=kind, params=dict(base)) for kind in ROUND_TASKS]


# ---- 幂等判据（每步的「盘上事实」） -----------------------------------------


@dataclass(frozen=True)
class RoundFacts:
    """本轮**盘上事实**（由调用方从账本 / 目录 / 权重文件算出；本模块不碰 IO）。

    每个字段都必须是「可重算的真事实」，而不是「上一轮记住的东西」——否则重启后
    判据就不可信了（这正是 R2a 把门禁迁到账本的同一条理由）。
    """

    it: int
    #: 账本里已有本 it 的 `iteration` 行（⇒ 这一轮已经结算过，任何重放都必须停在它之前）。
    iteration_recorded: bool = False
    #: 本轮采集已结算的局数 / 计划局数（配额未满 = 还需补波，不是「已完成」）。
    games_settled: int = 0
    games_planned: int = 0
    #: 本轮权重的指纹已落到 `args.out`（对比发布时的指纹）。
    weights_landed: bool = False
    #: 本轮权重的干净评估 summary 已落账（无需重派）。
    eval_landed: bool = False
    #: 权重已归档（backup_weights 的产物存在）。
    weights_archived: bool = False
    #: 上一轮预采子进程句柄是否已消费（持久层面：本轮的预采 shard 是否已在盘上）。
    precollect_consumed: bool = False

    @property
    def collection_complete(self) -> bool:
        return self.games_planned <= 0 or self.games_settled >= self.games_planned


def already_done(kind: str, facts: RoundFacts) -> bool:
    """该步骤是否**已经完成**（幂等 guard：完成即可跳过，重放安全）。

    只有「能从盘上事实判定完成」的步骤才有判据；判据未知的步骤一律返回 False
    （宁可重做，不可误跳——误跳会丢一轮语料，这是本仓库最贵的一类 bug）。
    """
    if facts.iteration_recorded and kind in ("record_iteration", "eval_join", "gate", "cleanup"):
        # 本轮已结算 ⇒ 结算及其之后的步骏没有重放的意义（账本是唯一真相）。
        # 含 `record_iteration` 本身：★ 幂等铁律——重写 `iteration` 行会让门禁累计量翻倍。
        return True
    if kind == "rollout":
        return facts.collection_complete and facts.games_planned > 0
    if kind == "volume_topup":
        return facts.collection_complete and facts.games_planned > 0
    if kind == "export_weights":
        return facts.weights_landed or facts.weights_archived
    if kind == "eval_dispatch":
        return facts.eval_landed
    if kind == "precollect_join":
        return facts.precollect_consumed
    return False


def pending_tasks(tasks: list[Task], facts: RoundFacts) -> list[Task]:
    """本轮**尚未完成**的任务（R2c 的队列内容；顺序保持）。

    `iteration_recorded` 时整轮视为已结算 ⇒ 返回空（重放不得重写账本、不得重发 job）。
    """
    if facts.iteration_recorded:
        return []
    return [t for t in tasks if not already_done(t.kind, facts)]


# ---- 失败语义（与现主循环逐条对齐） -----------------------------------------


def resolve_failure(attempt: int, *, leg_abort: bool = False, smoke_void: bool = False) -> TaskResult:
    """把一次失败映射成四态之一——**与现主循环的重试语义逐条一致**。

    · 冒烟回显（`SmokeVoidRoundError`）⇒ 本轮作废、it 原地重试（不计失败连击）；
    · 已判死腿（`_leg_abort`）⇒ 立刻 ABORT（不再 5×30s 空转，x3-step 事故的教训）；
    · 其余 ⇒ `attempt` 用尽（≥5）才 ABORT，否则 RETRY（it 不前跳，杜绝静默跳轮丢语料）。
    """
    if smoke_void:
        return retry("smoke-void: 本轮作废，it 原地重试", same_iter=True)
    if leg_abort:
        return abort("leg ABORTED（远端不可用且禁用降级）")
    if attempt >= 5:
        return abort(f"连续失败 {attempt} 次（≥5）")
    return retry(f"失败第 {attempt} 次 —— 原地重试")
