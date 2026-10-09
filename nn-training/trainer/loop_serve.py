"""loop_serve —— 单进程服务多门课的训练入口（R2d 的「写的一半」，plan/r2-loop-task-queue §8）。

**为什么需要它**：R2c-2/R2c-3 把「一轮」拆成了可让位的细粒度任务、给了调度器（`loop_scheduler`）
与任务体↔引擎的桥（`loop_runner`），但**没有任何东西真的驱动它**——今天仍是「一门课一个
`trainer/run_rl.py` 进程」。本模块就是那个驱动者：一个进程、一个 supervisor、N 门课，一门课等外部
（云端 PPO / 预采子进程）时**执行权交给别的课**。

三个层次，**每层各做一次**（越界做两次都会伤到既有护栏，所以按层显式分开）：

| 层 | 频率 | 内容 |
|---|---|---|
| 进程级 | 一次 | UTF-8 stdio / `faulthandler` / `chdir(repo)` / 启动前 `git push`（`.git_push.lock` 串行化）/ bun 存在性 → `prepare_process()` |
| 课程级 | 每课一次 | 解析课程配置（与 `trainer/run_rl.py --course` **逐字段一致**）→ `validate_args` → **按课程的单实例锁**（同课双开响亮拒启）→ 日志镜像 → 清本课 hub 停机态 → 引擎对象（`TrainingLoop`，torch 由引擎自己 `_setup()` 在首次执行时才拉起） |
| 一步级 | 每步 | `Supervisor.step()` → `EnginePool.get(课)` → `LoopRunner.executor(task, queue)` |

**刻意与单课程入口不同的两处**（都在文档里写死，防「统一」时被顺手改回去）：

1. **不收官停车**：`_park_after_completion` 的死循环语义前提是「这个进程就是这门课」——单进程
   多课程下停车会冻住所有课。所以这里对跑满的课只做 `TrainingLoop.finish_course()`（收敛预采 /
   云机 PAUSE / `run_complete` 落账，三件事与单课程路径**共用同一份实现**），随后调度器把该课
   队列置 `done`，进程继续服务别的课；全部课都收官才退出（重启交给控制台/启动器）。
2. **不换 `sys.stdout`**：单课程入口用 `Tee` 把 stdout 落到 `args.out_log`；一个进程里套两个
   Tee 会把每行复制进两份课日志。这里改成**行级路由**（`biz.log.open_course_sink` +
   `prefix_scope`）：每行带 `[课]` 前缀，并镜像到该课自己的 `out_log`。

**可测性**：`serve(..., prepare=False)` + 注入 `pool` / `supervisor` ⇒ 全流程不碰 torch、不碰
网络、不起进程（见 `tests/trainer/test_serve_wiring.py` 与 `e2e/test_loop_supervisor_integration.py`）。
"""

from __future__ import annotations

import atexit
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from biz.course_resolve import CURRICULA_DIR
from common.log import close_course_sinks, log, open_course_sink, prefix_scope
from common.platform_utils import force_utf8_stdio
from common.proc import run_capture
from trainer.loop_control import ControlApplier, read_control
from trainer.loop_plan import (
    COURSE_ENABLE_MARKER,
    course_enabled,
    course_facts,
    course_is_held,
    course_kind,
    course_traj,
    enabled_courses,
    round_tasks_for,
)
from trainer.loop_runner import ROUND_KIND, LoopRunner
from trainer.queue import REPO_ROOT
from worker.course_args import (
    COURSE_MACHINE_OVERRIDE_KEYS as COURSE_MACHINE_OVERRIDE_KEYS,
)
from worker.course_args import (
    _read_rl_config as _read_rl_config,
)
from worker.course_args import (
    apply_course_machine_overrides,
    course_args,
)
from worker.engine_pool import DEFAULT_CACHE_COURSES, DEFAULT_CACHE_MB, EnginePool
from worker.loop_scheduler import ABORTED, QUEUE_DONE, Supervisor
from worker.loop_tasks import RoundFacts, Task, abort, pending_tasks
from worker.train.loop_util import acquire_lock, cleanup_lock, course_lock_path

#: nn-training 目录（锁文件/课程文件都相对它——与 `trainer/run_rl.py` 的 `Path(__file__).parent` 同一个）。
NN_DIR = Path(__file__).resolve().parent.parent

#: 本机资源池默认容量（与 `trainer/run_rl_cluster.py` 的 CLI 默认值同一套；plan §6.2 定案 PPO/eval=1）。
DEFAULT_CAPACITIES: dict[str, int] = {"local_ppo": 1, "eval_local": 1, "local_rollout": 4}

#: `Supervisor` 的空转让位粒度（秒）：全部课都在等外部时按这个间隔再问一遍。
DEFAULT_POLL_SEC = 15.0


@dataclass
class CourseRuntime:
    """一门课在**本进程**里的运行时（args + 引擎 + 任务体，各自独立、互不共享）。

    `kind` ∈ {'rl', 'bc'}：决定引擎类型与任务粒度（BC ⇒ `BcLoop` + 单个轮任务）。BC 课时
    `args` 就是 `BcRuntime`（同样有 `.traj` / `.iters`，故所有「读 `rt.args.traj`」的路径
    一字不改），完整运行时另存 `bc` 供工厂取。
    """

    course: str
    args: Any
    lock_path: str = ""
    engine: Any = None
    runner: LoopRunner | None = None
    kind: str = "rl"
    #: BC 课的解析结果（`trainer.bc_loop.BcRuntime`）；RL 课为 None。
    bc: Any = None


@dataclass
class ServeReport:
    """一次 serve 的可断言结论（测试/控制台都用它，不再从日志里猜）。"""

    courses: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    #: 被跳过课的**判据指纹**（course → {"kind": 跳过来源, "fp": 判据快照}）。
    #: "跳过"不是终身黑名单：判据（课程文件/开课标记/锁/账本）变了就自动重试开课
    #: （2026-10-05 事故治本，plan/course-startup-recover §3.4）。
    skipped_at: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 一步级 SystemExit 按课下线的原因（2026-09-22 事故：一门课的配置错误曾弄崩整个共享
    #: trainer）——与 `skipped`（开课/入队阶段）分开，读面能区分「哪一步、为什么」。
    failures: dict[str, str] = field(default_factory=dict)
    engines: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    stop_reason: str = ""


# ---------------------------------------------------------------- 进程级一次


def prepare_process(argv: list[str] | None = None) -> str:
    """进程级一次性准备，返回 bun 路径（rollout 需要它）。**副作用：启动前 push 当前分支。**

    与 `trainer/run_rl.py` 的对应片段同序同义（B7 之后 torch 仍不在启动路径上）：UTF-8 stdio →
    faulthandler → `chdir(REPO_ROOT)` → 启动前 `git push`（repo 级 O_EXCL 锁串行化——多课并发
    push 会顶成 non-fast-forward/锁竞争）→ 节点升级分支锁到训练机当前分支 → bun 存在性。

    **只做一次**：每课各做一次就等于每个课程都推一遍 git（这正是单进程入口要省掉的事）。
    """
    force_utf8_stdio()
    import faulthandler

    faulthandler.enable()
    os.chdir(str(REPO_ROOT))

    from worker.archive import ensure_current_branch_pushed

    push_lock = str(REPO_ROOT / ".git_push.lock")
    if acquire_lock(push_lock, tag="git push"):
        try:
            ensure_current_branch_pushed(REPO_ROOT)  # side-effect: push current branch
        finally:
            cleanup_lock(push_lock)
    else:
        log(
            "[serve] WARN: 另一进程正在 git push（.git_push.lock 被占）——跳过本次启动前推送，"
            "节点沿用远端已有分支；如远端长期无新提交请检查持锁进程"
        )
    branch = run_capture(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, timeout=30
    ).stdout.strip()
    if branch and branch != "HEAD":
        import common.distribution as _dc

        _dc.set_upgrade_branch(branch)
        log(f"[serve] node upgrade branch locked to training-machine branch: {branch}")

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[serve] bun not found on PATH — rollout needs it")
    return bun


#: 课程解析/校验链（白名单 + `_read_rl_config` + `apply_course_machine_overrides` +
#: `course_args`）2026-10-05 整块搬去 `worker/course_args.py`（训练栈家——`trainer/` 只许
#: 编排，见 `tests/test_layering.py::test_trainer_holds_only_orchestration_modules`）：serve 与只读视图
#: （`course_openable`）**同源复用**同一条链（plan/course-startup-recover §4.1）；
#: 本模块 import 后按旧名重导出，既有调用点/用例零改动。


def cluster_lock_path() -> str:
    """单进程服务器的锁文件：`nn-training/.run_cluster.lock`（`course=''` ⇒ 无课程名后缀）。"""
    from worker.train.loop_util import course_lock_path

    return course_lock_path(str(NN_DIR), "", "run_cluster")


def acquire_cluster_lock(lock_path: str, *, force: bool = False) -> bool:
    """进程级单实例锁（2026-09-19 / R3-5）：单进程服务**所有**课程 ⇒ 双开 = 两套调度器
    抢同一批 traj。按课锁拦不住这一类（两套调度器可以各跑一半课程，每门课都只有一个跑者）。

    实现复用 `run_rl._acquire_run_rl_lock`（O_CREAT|O_EXCL；holder 死了自动收回）——
    锁文件里写 `pid|python|ts`，与其它锁同一种形状（控制台按同一读法看它）。
    """
    from trainer.run_rl import _acquire_run_rl_lock

    return _acquire_run_rl_lock(str(lock_path), force=force)


def release_cluster_lock(lock_path: str) -> None:
    """释放自己持有的单实例锁（已易主则不删——与 `_cleanup_run_rl_lock` 同契约）。"""
    from trainer.run_rl import _cleanup_run_rl_lock

    _cleanup_run_rl_lock(str(lock_path))


def open_course(
    course: str, *, argv: list[str] | None = None, traj_root: str = "tmp"
) -> CourseRuntime:
    """开课（课程级一次性副作用）。**失败即抛**，由调用方按课隔离。

    ① args（`course_args`）；② 路径一致性核对（发现路径 vs 课程配置的 `traj`，不一致**响亮
    记录**但不自作主张改一边）；③ **按课程的单实例锁**（同课双开响亮拒启——2026-09-06 双 trainer
    并行写同一 traj 的护栏，按课程命名后对并行课程不误伤）；④ 每课日志镜像（行路由）；
    ⑤ 清本课 hub 停机态（残留 halt 会让首轮 PPO job 进无人区）。

    BC 课程走 `_open_bc_course`（**同一个函数名/同一份副作用**，只是解析链与锁名不同：
    `trainer/run_bc.py` 用的是 `run_bc` 锁——共用 `run_rl` 锁会让「控制台起 run_bc」与「serve 起同一个
    BC 课」互相看不见，两边同时开课）。

    **不在这里** `_setup()`：那会拉起 torch 并写 `run_start`——「扫到但本轮没在训」的课
    不该付这个代价。首次执行该课的一步时由 `ensure_ready` 做（见 `serve`）。
    """
    if course_kind(course) == "bc":
        return _open_bc_course(course, argv=argv, traj_root=traj_root)
    from trainer.run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    args = course_args(course, argv)
    # 死键点名（plan/course-workers-removal §3-S1.5）：**开课侧**一次，一行/门课。
    # 不放 `apply_course`：只读视图 `course_openable` 走同一条链，会被控制台每拍轮询重复打。
    from biz.course_spec import dead_key_warnings

    for _msg in dead_key_warnings(getattr(args, "course_obj", None)):
        log(_msg)
    traj = Path(args.traj)
    expected = Path(course_traj(traj_root, course))
    if str(traj) != str(expected):
        log(
            f"[serve] WARN 课程 {course} 的 traj = {traj}，与发现路径 {expected} 不一致——"
            f"按课程配置（{traj}）为准；若这是笔误请改课程 jsonc"
        )

    lock_path = course_lock_path(str(NN_DIR), course, "run_rl")
    if not _acquire_run_rl_lock(lock_path, force=bool(getattr(args, "force", False))):
        raise SystemExit(
            f"[serve] 课程 {course} 已有 run_rl 在跑（锁 {lock_path}）——拒绝双开；"
            "先停掉持有者或加 --force 接管"
        )
    atexit.register(_cleanup_run_rl_lock, lock_path)

    if getattr(args, "out_log", ""):
        open_course_sink(course, args.out_log)

    from remote.hub_client import clear_halt_on_startup

    clear_halt_on_startup(
        str(getattr(args, "remote_hub_url", "") or ""),
        str(getattr(args, "remote_token", "") or ""),
        log=log,
        course=course,
    )
    log(f"[serve] 开课 {course}：traj={traj} mode={getattr(args, 'mode', '?')} lock={lock_path}")
    return CourseRuntime(course=course, args=args, lock_path=lock_path)


def _open_bc_course(
    course: str, *, argv: list[str] | None = None, traj_root: str = "tmp"
) -> CourseRuntime:
    """开一门 BC 课（R3-4）：与 `trainer/run_bc.py` 的启动段**同义**，只是不做进程级那几件（utf8 /
    chdir / 启动前 git push）——那些由 `prepare_process` 在进程级做过一次。

    锁用 `run_bc`（与单课程入口同名同路径）：控制台起的 `trainer/run_bc.py --course X` 与 serve 里的
    同一门 BC 课必须互相看得见（2026-09-06 双 trainer 写同一 traj 的护栏）。
    """
    from trainer.bc_loop import bc_course_args, resolve_bc_runtime
    from trainer.run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    # ★ 2026-09-28：serve = hub pull 模型，BC 课自己补 `--remote`（hub 地址/token 走
    #   rl-config，经 `resolve_bc_runtime` 自己的 dist_config 读入）。不补的话
    #   `resolve_transport(auto)` 响亮 SystemExit：课开了、标记落了，但每拍 skip、hub 队列
    #   恒空、worker 干等（bc-human-retrial 生产实测；R3-4 只接了引擎，没接传输）。
    #   已显式给传输（--remote/--local/--remote-transport，含测试）的不动。
    extra = list(argv or [])
    if not any(
        a in ("--remote", "--local") or a.startswith("--remote-transport") for a in extra
    ):
        extra.append("--remote")
    args = bc_course_args(course, extra)
    # 课程机器侧覆盖：与 RL 同源（同一份 rl-config 块、同一个白名单）——BC 解析器缺的键会被
    # 响亮跳过（不 setattr 造字段）。在 `resolve_bc_runtime` **之前**：它按 args 推传输。
    apply_course_machine_overrides(args, course)
    # 解析链只此一份（`resolve_bc_runtime` 同时给出 traj/传输/token；它自己读 rl-config）。
    runtime = resolve_bc_runtime(args)
    traj = Path(runtime.traj)
    expected = Path(course_traj(traj_root, course))
    if str(traj) != str(expected):
        log(
            f"[serve] WARN BC 课程 {course} 的 traj = {traj}，与发现路径 {expected} 不一致——"
            f"按课程配置（{traj}）为准；若这是笔误请改课程 jsonc"
        )

    lock_path = course_lock_path(str(NN_DIR), course, "run_bc")
    if not _acquire_run_rl_lock(lock_path):
        raise SystemExit(f"[serve] BC 课程 {course} 已有 run_bc 在跑（锁 {lock_path}）——拒绝双开")
    atexit.register(_cleanup_run_rl_lock, lock_path)

    if runtime.transport == "hub":
        from remote.hub_client import clear_halt_on_startup

        clear_halt_on_startup(runtime.hub_url, runtime.token, log=log, course=course)
    log(
        f"[serve] 开 BC 课 {course}：traj={traj} iters={runtime.iters} "
        f"transport={runtime.transport} smoke={runtime.smoke} lock={lock_path}"
    )
    return CourseRuntime(course=course, args=runtime, lock_path=lock_path, kind="bc", bc=runtime)


# ---------------------------------------------------------------- 一步级


def ensure_ready(rt: CourseRuntime, engine: Any) -> None:
    """首次执行前 `_setup()` 一次。

    「引擎对象换了」= 新建（首用 / 被池驱逐后重建）⇒ 走 `_setup()`（与一次进程重启同义：
    `run_start` 续写、账本指针仍是 SSOT）；「对象没换」⇒ 无事可做。

    ★ 2026-09-21（§3）：原先这里还要 `_ensure_local_ppo_stack()` 补齐 torch 栈（池
    `release_torch` 之后）。单一 PPO 路径下循环**不建** model/opt/ref ⇒ 没有栈要补，
    本机也不再付 torch 基线内存（D2）。
    """
    if rt.engine is not engine:
        engine._setup()
        rt.engine = engine


def build_factory(
    runtimes: dict[str, CourseRuntime],
    *,
    bun: str,
    iters: int = 0,
    step_mode: bool = True,
    poll_interval: float = DEFAULT_POLL_SEC,
    now: Callable[[], float] = time.time,
) -> Callable[[str], Any]:
    """课程 → 引擎的构建器（**廉价**：不碰 torch，RL 课的栈由 `_setup()` 稍后拉起）。

    构建时同时挂好该课的 `LoopRunner`（任务体↔引擎的唯一桥）：它是 `planner`/`executor` 的
    来源，也是「这一课跑到哪一步」的内存加速器（权威永远是账本）。

    **BC 课（R3-4）**：引擎是 `trainer.bc_loop.BcLoop`（同一套 `_setup`/`run_one_round`/
    `finish_course`/`release_torch` 子集），且**恒为轮粒度**（13 步表是 RL 的一轮，硬套会
    给 BC 发它不认识的待办）；指针走引擎自己的 `ledger_next_it`（`bc_round_completed`）。
    """
    from trainer.loop_core import TrainingLoop

    def factory(course: str) -> Any:
        rt = runtimes[course]
        if rt.kind == "bc":
            from trainer.bc_loop import BcLoop

            engine: Any = BcLoop(rt.bc)
            rt.runner = LoopRunner(
                loop=engine,
                course=course,
                iters=int(rt.bc.iters or 0),
                step_mode=False,
                poll_interval=poll_interval,
                now=now,
                facts_fn=facts_fn,
            )
            return engine
        from trainer.run_rl import update_kwargs

        # ★ §3：单一 PPO 路径 ⇒ 引擎不建本机 PPO 栈，后端传 None（hub 免 torch，D2）。
        engine = TrainingLoop(rt.args, None, bun, update_kwargs)
        rt.runner = LoopRunner(
            loop=engine,
            course=course,
            iters=int(iters or getattr(rt.args, "iters", 0) or 0),
            step_mode=step_mode,
            poll_interval=poll_interval,
            now=now,
            facts_fn=facts_fn,
        )
        return engine

    def facts_fn(course: str, it: int) -> RoundFacts:
        """盘上事实（账本 + shard 目录）——与只读计划视图**同一份实现**（`loop_plan`）。

        `course` 必须透传：BC 课的指针与 RL **不同源**（`bc_round_completed` vs `iteration`），
        少了它一门 BC 课会被按 RL 读账本、指针永远停在 it1。
        """
        rt = runtimes.get(course)
        try:
            facts, _v = course_facts(Path(rt.args.traj) if rt else Path("."), course=course)
            return facts
        except Exception as e:  # 读盘失败 ⇒ 判据未知（宁可重做，不可误跳）
            log(f"[serve] {course} 盘上事实读失败（判据退回未知）：{type(e).__name__}: {e}")
            return RoundFacts(it=it)

    return factory


def build_executor(
    pool: EnginePool,
    runtimes: dict[str, CourseRuntime],
    report: ServeReport | None = None,
    *,
    traj_root: str | Path = "tmp",
) -> Callable[[Any, Any], Any]:
    """调度器执行体：取引擎（可能刚重建）→ 补齐栈 → 交给该课的 `LoopRunner.executor`。

    `prefix_scope` 是**行级课程归属**的落点：这一步（及其内部全部日志）带 `[课]` 前缀并镜像
    到该课日志文件。放在这里而不是引擎里，是因为「谁在执行哪门课」只有调度器知道。

    **一步级 SystemExit 按课隔离**（2026-09-22 事故：run_rl 在 `--run-iters<0` 需要课程声明
    iters 这类**课程配置错误**上抛 SystemExit 是 BaseException，穿透调度器 `except Exception`
    的 RETRY 兜底，直接把整个共享 trainer 弄崩）。这里的 `except SystemExit` 把该课按
    「只脏本课」的 ABORT 语义下线（调度器 L313-315 既有裁决：队列 ABORTED、不再被 `_pick`
    选中、discover 因 `report.skipped` 不重试、`_settle_rounds` 不收官），**其余课程照跑**。
    只捕 SystemExit、不扩到其它 BaseException：KeyboardInterrupt 仍能干净停 serve。
    """

    def execute(task: Any, queue: Any) -> Any:
        rt = runtimes[task.course]
        engine = pool.get(task.course)
        ensure_ready(rt, engine)
        with prefix_scope(task.course):
            try:
                assert rt.runner is not None  # factory 保证
                return rt.runner.executor(task, queue)
            except SystemExit as e:
                code = e.code if isinstance(e.code, str) else ""
                detail = (code or str(e)).strip() or f"SystemExit({e.code!r})"
                log(
                    f"[serve] 课程 {task.course} 一步级 SystemExit——该课下线，其余课照跑："
                    f"{task.kind}@{task.task_id}：{detail}"
                    "（改好课程文件后会自动重试开跑，不需要重启 trainer）"
                )
                if report is not None:
                    _record_skip(report, task.course, SKIP_KIND_STEP, detail, traj_root)
                    report.failures[task.course] = detail
                return abort(detail)

    return execute


def _pinned_mid_round(sup: Supervisor) -> Callable[[str], bool]:
    """「这门课**本轮还没跑完**」——`EnginePool.pinned` 的 serve 侧判据（2026-10-02 事故）。

    判据 = `CourseQueue.mid_round`（本轮已经动过手）——**不能**用 `tasks` 非空：`_finish_round`
    会立刻为下一轮重新规划（tasks 又非空），于是「还没开跑的下一轮」与「跑了一半的本轮」形状相同，
    拿它当判据等于永久禁止驱逐。

    轮内抽走引擎的后果：重建出来的新引擎没有轮内属性（`_node_rollout` 等），而队列下一步就是
    `ppo`/`cleanup` ⇒ 该课一步一崩（h4-hurt-f75/f150：1076 次 run_start / 0 条 iteration）。
    轮间（`mid_round=False`）驱逐的代价才是 docstring 承诺的那一档（权重可从 `args.out` 重建、
    Adam 动量重置）。**全部课程都在轮内 ⇒ 池不再驱逐**（响亮记一行，宁可超预算也不中途抽栈）。
    """

    def pinned(course: str) -> bool:
        q = sup.courses.get(course)
        return bool(q is not None and q.mid_round and q.state != ABORTED)

    return pinned


def _all_settled(sup: Supervisor) -> bool:
    """全部课程**已收官 / 停腿**。

    **暂停不算收官**（用户口径「暂停 = 保留队列，恢复后接着跑」）：把 PAUSED 当收官会让
    「暂停一门课」顺手把整个进程退掉，于是恢复意图永远没人执行。暂停的课会让显式课程模式
    的进程一直等（用 `--max-seconds` 兜底）；发现模式本来就不退。
    """
    return all(q.state in (QUEUE_DONE, ABORTED) for q in sup.courses.values())


# ---------------------------------------------------------------- 主循环


def _marker_mtime_ns(traj_root: str | Path, course: str) -> int | None:
    """开课标记的 mtime（ns；缺失/不可读 ⇒ None）。只做存在性之外的第二事实：标记**何时**被写的。"""
    try:
        return (Path(traj_root) / course / COURSE_ENABLE_MARKER).stat().st_mtime_ns
    except OSError:
        return None


#: 跳过来源——决定「复活判据」看哪些盘上事实（plan §3.4 的分类表）。
SKIP_KIND_CONFIG = "config"  #: 开课校验失败（课程文件/环境配置）：判据 = 课程文件 + 开课标记
SKIP_KIND_LOCK = "lock"  #: 按课程锁被占：判据 = 锁签名（释放 / 换主 / 持有者死）
SKIP_KIND_ENQUEUE = "enqueue"  #: 入队失败（读盘）：判据 = 账本 + 课程文件 + 开课标记
SKIP_KIND_STEP = "step"  #: 一步级 SystemExit 下线：课仍在 runtimes ⇒ 走「重置队列」通道


def _course_file_identity(course: str) -> tuple[int, int] | None:
    """课程文件身份（`mtime_ns` + `size`；RL/BC 两个候选；缺失/不可读 ⇒ None）。

    与 `_marker_mtime_ns` 同款形状：只做「人动过这个文件没有」的第二事实。**不算 sha**
    （每拍算哈希太贵；mtime+size 足够判「文件变了」）。
    """
    for cand in (CURRICULA_DIR / f"{course}.jsonc", CURRICULA_DIR / f"{course}.bc.jsonc"):
        try:
            st = cand.stat()
        except OSError:
            continue
        return (int(st.st_mtime_ns), int(st.st_size))
    return None


def _ledger_mtime_ns(traj_root: str | Path, course: str) -> int | None:
    """该课账本的 mtime（ns；缺失/不可读 ⇒ None）——「入队失败」一类的复活判据。"""
    try:
        return int((Path(traj_root) / course / "training_log.jsonl").stat().st_mtime_ns)
    except OSError:
        return None


def _lock_signature(course: str) -> tuple[Any, ...]:
    """按课程锁的身份（缺失 ⇒ `(False,)`；在 ⇒ `(True, mtime_ns, holder, holder_alive)`）。

    持有者活着时签名稳定（不重试、不刷日志）；锁释放 / 换主 / 持有者死 ⇒ 签名变化 ⇒ 重试
    （死锁由 `_acquire_run_rl_lock` 的自动收回接手，不需要重启整个 trainer）。
    """
    from common.pid_probe import pid_alive
    from worker.train.loop_util import course_lock_path

    kind = "bc" if course_kind(course) == "bc" else "rl"
    lock = Path(course_lock_path(str(NN_DIR), course, "run_bc" if kind == "bc" else "run_rl"))
    try:
        st = lock.stat()
    except OSError:
        return (False,)
    holder: int | None = None
    try:
        holder = int(lock.read_text(encoding="utf-8").split("|", 1)[0])
    except (OSError, ValueError):
        holder = None
    return (True, int(st.st_mtime_ns), holder, pid_alive(holder))


def _skip_fingerprint(course: str, kind: str, traj_root: str | Path) -> dict[str, Any]:
    """跳过时 / 重试前**同一取法**的判据快照（按来源取事实；全是 O(1) stat）。"""
    fp: dict[str, Any] = {
        "file": _course_file_identity(course),
        "marker": _marker_mtime_ns(traj_root, course),
    }
    if kind == SKIP_KIND_LOCK:
        fp["lock"] = _lock_signature(course)
    elif kind == SKIP_KIND_ENQUEUE:
        fp["ledger"] = _ledger_mtime_ns(traj_root, course)
    return fp


def _skip_kind_of(detail: str) -> str:
    """按失败原文归类跳过来源（锁被占是开课期唯一需要与「配置错」分开的族）。"""
    if "拒绝双开" in detail:
        return SKIP_KIND_LOCK
    return SKIP_KIND_CONFIG


def _record_skip(
    report: ServeReport, course: str, kind: str, detail: str, traj_root: str | Path
) -> None:
    """记一次跳过（原因 + 当时判据）。复活 = 判据变了；没变 ⇒ 不重试、不刷日志。"""
    report.skipped[course] = detail
    report.skipped_at[course] = {"kind": kind, "fp": _skip_fingerprint(course, kind, traj_root)}


def reopenable_skipped(
    skipped_at: dict[str, dict[str, Any]], traj_root: str | Path
) -> list[str]:
    """被跳过的课里，**判据已变**、值得重试的课（2026-10-05 事故治本）。

    只读 stat（课程文件 / 开课标记 / 锁 / 账本），不建 runtime、不写任何状态——与
    `reopened_parked` 同款纯函数（可单测）。判据没变 ⇒ 返回空（`:851` 注释的原意：不刷日志）；
    变了（含「文件从缺到有」「停→开后标记 mtime 更新」「锁释放/持有者死」「账本更新」）⇒ 返回。
    """
    out: list[str] = []
    for course, rec in skipped_at.items():
        kind = str(rec.get("kind") or SKIP_KIND_CONFIG)
        if _skip_fingerprint(course, kind, traj_root) != rec.get("fp"):
            out.append(course)
    return out


def reopened_parked(
    states: dict[str, str],
    done: set[str],
    seen_marker: dict[str, int],
    traj_root: str | Path,
) -> list[str]:
    """收官后被用户重开的课（2026-09-25 C-0 复活事故）。

    条件三合一，缺一不可：① 队列已终（`done`/`aborted`）② 已落过收官副作用（`done` 集 =
    `_settle_rounds` 处理过）③ 开课标记 mtime **新于入队时记录值**（控制台停→开的唯一机器
    含义：停课删标记、开课重写，mtime 必变；只点"开课"而标记本来就在 ⇒ mtime 不变 ⇒ 不算重开）。

    调用方摘名后（`runtimes`/`done`/`seen` 三处同删），`fresh` 通道按新课重入队、指针续跑。
    纯函数（只读标记 mtime），可单测。
    """
    out: list[str] = []
    for course, state in states.items():
        if state not in (QUEUE_DONE, ABORTED) or course not in done:
            continue
        prev = seen_marker.get(course)
        if prev is None:
            continue
        cur = _marker_mtime_ns(traj_root, course)
        if cur is not None and cur > prev:
            out.append(course)
    return out


def _open_courses(
    names: list[str],
    runtimes: dict[str, CourseRuntime],
    report: ServeReport,
    *,
    argv: list[str] | None,
    traj_root: str,
) -> list[str]:
    """开课并登记到 `runtimes`（单课失败隔离；返回本次**新开**的课程名）。"""
    opened: list[str] = []
    for course in names:
        if course in runtimes:
            continue
        try:
            runtimes[course] = open_course(course, argv=argv, traj_root=traj_root)
            opened.append(course)
        except BaseException as e:  # 单课故障隔离：不因一门课配错/被占就停掉别的课
            detail = f"{type(e).__name__}: {e}"
            # ★ 跳过不是终身黑名单（2026-10-05 事故）：记下判据指纹，变了就自动重试。
            _record_skip(report, course, _skip_kind_of(detail), detail, traj_root)
            log(f"[serve] 跳过课程 {course}：{detail}")
    return opened


def _enqueue(
    sup: Supervisor, runtimes: dict[str, CourseRuntime], course: str, *, step_mode: bool
) -> None:
    """把一门课挂上调度器（初始队列 = 盘上事实算出的待办表）。

    判据原语与只读计划视图同源（`course_facts` + `pending_tasks(round_tasks_for(...))`）——
    不调 `plan_course` 是因为它连展示面字段（累计量/verdict）一起算，那是给 CLI 表/控制台看的。
    粒度必须匹配：细粒度用 13 步任务表，轮粒度用**单个**轮任务——混了就会把 13 个步骤 kind
    塞进只认 `round` 的执行体（响亮 ABORT，不是静默跳步）。BC 课的轮任务由
    `round_tasks_for` 给出（它按课程种类选表），故这里的折叠分支对它幂等。
    """
    rt = runtimes[course]
    facts, _view = course_facts(Path(rt.args.traj), course=course)
    it = int(facts.it)
    tasks = pending_tasks(round_tasks_for(course, it), facts)
    if not step_mode and tasks:
        tasks = [Task(course, it, ROUND_KIND)]
    q = sup.add_course(course, it, tasks)
    log(f"[serve] 入队 {course}（{rt.kind}）：it{it} 待办 {len(tasks)} 步（队列状态 {q.state}）")


def _enqueue_opened(
    sup: Supervisor,
    runtimes: dict[str, CourseRuntime],
    report: ServeReport,
    names: list[str],
    *,
    step_mode: bool,
    traj_root: str = "tmp",
) -> None:
    """给**刚开的**课挂队列——单课失败隔离（一门课读盘/算判据失败不该带走整个进程）。

    失败时把该课从 `runtimes` 里摘掉并记进 `skipped`：没有队列的课不会被调度器选中，留在
    `runtimes` 里只会变成一个「看着开着、实际没人跑」的幽灵（且下次扫到它也不会重试）。
    """
    for course in names:
        try:
            _enqueue(sup, runtimes, course, step_mode=step_mode)
            # 成功入队 = 这门课回到正常服务面：清掉历史跳过记账（复活路径的收尾）。
            report.skipped.pop(course, None)
            report.skipped_at.pop(course, None)
        except BaseException as e:
            detail = f"入队失败 {type(e).__name__}: {e}"
            _record_skip(report, course, SKIP_KIND_ENQUEUE, detail, traj_root)
            runtimes.pop(course, None)
            log(f"[serve] 课程 {course} 入队失败，本次不服务：{detail}")


def _revive_skipped(
    sup: Supervisor,
    runtimes: dict[str, CourseRuntime],
    report: ServeReport,
    course: str,
    *,
    argv: list[str] | None,
    traj_root: str,
    step_mode: bool,
) -> list[str]:
    """判据已变的「开课阶段跳过」课：撤销记账 → 重走开课 + 入队（失败则按新判据重记 skip）。"""
    report.skipped.pop(course, None)
    report.skipped_at.pop(course, None)
    log(f"[serve] 课程 {course} 跳过判据已变化——重新尝试开课")
    opened = _open_courses([course], runtimes, report, argv=argv, traj_root=traj_root)
    _enqueue_opened(sup, runtimes, report, opened, step_mode=step_mode, traj_root=traj_root)
    return opened


def _revive_aborted(
    sup: Supervisor,
    runtimes: dict[str, CourseRuntime],
    report: ServeReport,
    course: str,
    *,
    step_mode: bool,
    traj_root: str,
) -> bool:
    """判据已变的「一步级失败」课：重置队列、**复用原 runtime 与热引擎**重新入队。

    C-0 前科（DECISIONS §2026-09-25-clutch-null-kill）：重走 `_open_courses` 会建一个
    `runner=None` 的新 runtime，而池里还是旧引擎 ⇒ `ensure_ready` 对不上 ⇒ 每轮断言失败进
    无限 RETRY。故这里只重置队列（`_enqueue`），runtime/引擎原样复用。
    """
    try:
        old_rounds = int(sup.courses[course].rounds_done) if course in sup.courses else 0
        _enqueue(sup, runtimes, course, step_mode=step_mode)
        sup.courses[course].rounds_done += old_rounds
    except BaseException as e:  # 复活失败：刷新判据（下次只在**再变**时重试，不刷日志）
        detail = f"复活入队失败 {type(e).__name__}: {e}"
        _record_skip(report, course, SKIP_KIND_STEP, detail, traj_root)
        log(f"[serve] 课程 {course} 复活入队失败，本次仍停车：{detail}")
        return False
    report.skipped.pop(course, None)
    report.skipped_at.pop(course, None)
    report.failures.pop(course, None)
    log(f"[serve] 课程 {course} 一步级失败后判据已变化——重置队列，复用原 runtime/引擎重新入队")
    return True


def serve(
    courses: list[str] | None = None,
    *,
    argv: list[str] | None = None,
    traj_root: str = "tmp",
    control_file: str | None = None,
    iters: int = 0,
    poll_sec: float = DEFAULT_POLL_SEC,
    capacities: dict[str, int] | None = None,
    cache_courses: int = 0,
    cache_mb: float = DEFAULT_CACHE_MB,
    step_mode: bool = True,
    max_seconds: float = 0.0,
    max_steps: int = 0,
    prepare: bool = True,
    bun: str | None = None,
    pool: EnginePool | None = None,
    supervisor: Supervisor | None = None,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> ServeReport:
    """单进程服务 N 门课。

    **进程不绑课程**（用户 2026-09-18 口径）：`courses=None`/空 ⇒ 发现模式——启动时扫
    `--traj-root` 下**已开课**的课程（账本 ∧ `training-enabled.txt`，见 `enabled_courses`），
    之后每个空转拍再扫一次（新开课的课自动入队）；一门课都没有也能起，队列就空着等
    （**不退出**：空队列是合法稳态，不是结束条件）。显式给 `courses` 时退化为「只看这几门」，
    全收官即退出（e2e/单课调试用）。

    ★ **发现判据从「有账本」改成「有开课标记」**（2026-09-20 用户口径：「课程开训需要用户
    手动开启」）：旧判据下 tmp/ 里的历史课会被一起拉起来跑（实测一启动就 21 门）。

    每拍的**控制面**（`trainer/loop_control.py`）：读 `tmp/loop-control.json` 的暂停意图 →
    `Supervisor.pause/resume`。暂停只影响调度，队列/账本不动（用户口径「保留队列，不删」）。

    退出条件（`ServeReport.stop_reason` 如实记录是哪一条——运维要能一眼分辨「跑完」与「被停」）：
    `all_settled`（仅显式课程模式；**暂停不算收官**——进程等着恢复）/ `max_steps` /
    `max_seconds` / `interrupted`(Ctrl-C)。
    课程级失败（课程文件缺失、锁被占）**不**终止进程：该课进 `skipped`，其余课照跑
    （故障域 = 单课，plan §4.3）。
    """
    report = ServeReport()
    if prepare:
        bun = prepare_process(argv)
    bun = bun or "bun"
    t0 = now()
    discover = not courses  # 发现模式：进程独立于课程（没课在训也照常起）

    runtimes: dict[str, CourseRuntime] = {}
    # 入队时见到的开课标记 mtime（`reopened_parked` 的比较基准：只有"停→开"能让它变大）。
    seen_marker: dict[str, int] = {}
    explicit = list(courses or [])
    if discover:
        explicit = enabled_courses(traj_root)
        if explicit:
            log(f"[serve] 发现 {len(explicit)} 门已开课的课程：{', '.join(explicit)}")
        else:
            log(
                f"[serve] {traj_root} 下暂无已开课的课程——进程照常运行，队列空着等"
                "（先在控制台点「开课」：写 training-enabled.txt + 账本即自动入队）"
            )
    for _c in _open_courses(explicit, runtimes, report, argv=argv, traj_root=traj_root):
        _m = _marker_mtime_ns(traj_root, _c)
        if _m is not None:
            seen_marker[_c] = _m
    if not runtimes and not discover:
        report.stop_reason = "no_courses"
        log("[serve] 显式课程表里没有能开的课——退出")
        return report

    # ★ P2（2026-10-02 事故）：上限默认**自动 = 并行课程上限**，不是死值 5。
    # 「5」是 R2c 定案时的并行课程上限估计，而开课是人手点的操作面事实 —— 一旦开课数越过它，
    # 引擎按 LRU 互逐。真实约束是**字节顶**（每课栈 MB 级；256MB ≈ 130 课，
    # 见 `scripts/measure_checkpoint_rss.py`），数量顶在这里没有保护价值。
    # 显式传正值 ⇒ 原样尊重（e2e/单测用它构造驱逐），但越界时响亮告警。
    if cache_courses <= 0:
        cache_courses = max(DEFAULT_CACHE_COURSES, len(runtimes))
        if len(runtimes) > DEFAULT_CACHE_COURSES:
            log(
                f"[serve] 开课 {len(runtimes)} 门 > 引擎缓存默认上限 {DEFAULT_CACHE_COURSES}"
                f"——上限自动抬到 {cache_courses}（真约束是字节顶 {cache_mb:.0f}MB，"
                "每课栈 MB 级：worker/scripts/measure_checkpoint_rss.py）"
            )
    elif len(runtimes) > cache_courses:
        log(
            f"[serve] ⚠ 显式 cache_courses={cache_courses} < 开课 {len(runtimes)} 门："
            "轮间会被驱逐（权重可重建，但 **Adam 动量重置**）；"
            "**轮内**（队列还有待办步骤）的课不驱逐"
        )

    own_pool = pool is None
    pool = pool or EnginePool(
        factory=build_factory(runtimes, bun=bun, iters=iters, step_mode=step_mode, now=now),
        courses=cache_courses,
        mb=cache_mb,
    )
    sup = supervisor or Supervisor(
        executor=build_executor(pool, runtimes, report, traj_root=traj_root),
        planner=_planner(runtimes),
        capacities=dict(capacities or DEFAULT_CAPACITIES),
        now=now,
    )
    # ★ P1（2026-10-02 事故）：把 `EnginePool` docstring 那句「绝不驱逐正在用的引擎」补全 ——
    # 此前 `keep` 只护住「本次 `get` 的那一门」，其余**轮内**的课照样被抽走引擎：重建后的
    # 引擎没有轮内属性（`_node_rollout` 等），而调度器队列仍停在该轮之后的步骤 ⇒ 一步一崩
    # （h4-hurt-f75/f150：1076 次 run_start / 0 条 iteration）。判据挂在**队列**上：
    # `tasks` 非空 = 这一轮还没走完（含 WAIT 等外部）⇒ 此刻不许抽栈。
    pool.pinned = _pinned_mid_round(sup)

    # 初始队列内容用**盘上事实**算（不构建引擎：扫到但没在训的课不该拉起 torch）。
    _enqueue_opened(sup, runtimes, report, list(runtimes), step_mode=step_mode, traj_root=traj_root)

    log(
        f"[serve] 单进程 supervisor 启动：{len(runtimes)} 课（{'发现模式：不绑课程' if discover else '显式课程表'}）/ 池容量 "
        + " ".join(f"{k}={v}" for k, v in (capacities or DEFAULT_CAPACITIES).items())
        + f" / 粒度={'13 步' if step_mode else '轮'}"
    )

    done_hooked: set[str] = set()
    control = ControlApplier()
    try:
        while True:
            # 控制面先于推进：暂停的那门课本拍就不会被选到（意图 → 调度，无中间态）。
            control.apply(sup, read_control(control_file), path=control_file)
            trace = sup.step()
            if trace is not None:
                report.steps += 1
                if trace.action in ("aborted", "round_done", "blocked_pool"):
                    log(f"[serve] {trace.course}: {trace.action} {trace.kind} {trace.detail}")
                if max_steps and report.steps >= max_steps:
                    report.stop_reason = "max_steps"
                    break
                continue
            # 一步都没能跑：要么全在等外部，要么全收官/停腿。
            report.stop_reason = _settle_rounds(sup, runtimes, done_hooked)
            # 发现模式：**空队列不是结束**（进程独立于课程，队列空着等新课程）。
            if report.stop_reason == "all_settled" and not discover:
                break
            if discover:
                # 收官后重开的课：只重置队列、复用原 runtime 与热引擎（2026-09-25）——
                # 重走 `_open_courses` 会建一个 runner=None 的新 runtime，而池里还是旧引擎，
                # `ensure_ready` 只对引擎对象不对 runner，之后每轮断言失败进无限 RETRY。
                # 复活本来就是"指针续跑"，引擎本来就是热的。
                for _c in reopened_parked(
                    {c: q.state for c, q in sup.courses.items()},
                    done_hooked,
                    seen_marker,
                    traj_root,
                ):
                    try:
                        _old_rounds = (
                            sup.courses[_c].rounds_done if _c in sup.courses else 0
                        )
                        _enqueue(sup, runtimes, _c, step_mode=step_mode)
                        # 新队列的 rounds_done 从 0 起：把收官前的计数带过去（状态面不断档）。
                        sup.courses[_c].rounds_done += _old_rounds
                    except BaseException as _e:
                        log(f"[serve] 课程 {_c} 复活入队失败，本次仍停车：{type(_e).__name__}: {_e}")
                        continue
                    done_hooked.discard(_c)
                    _m = _marker_mtime_ns(traj_root, _c)
                    if _m is not None:
                        seen_marker[_c] = _m
                    log(f"[serve] 课程 {_c} 收官后被重开——重新入队，指针续跑（引擎热复用）")
                # ★ 被跳过的课**不是终身黑名单**（2026-10-05 事故，plan §3.4）：判据
                # （课程文件/开课标记/锁/账本）变了 ⇒ 重试；没变 ⇒ 不重试、不刷日志。
                for _c in reopenable_skipped(report.skipped_at, traj_root):
                    if not course_enabled(course_traj(traj_root, _c)):
                        continue  # 用户已停课：不重开（再开课时标记变 ⇒ 下一拍自然复活）
                    if _c in runtimes:
                        _revive_aborted(
                            sup, runtimes, report, _c, step_mode=step_mode, traj_root=traj_root
                        )
                    else:
                        _revive_skipped(
                            sup,
                            runtimes,
                            report,
                            _c,
                            argv=argv,
                            traj_root=traj_root,
                            step_mode=step_mode,
                        )
                    _m = _marker_mtime_ns(traj_root, _c)
                    if _m is not None:
                        seen_marker[_c] = _m
                fresh = [c for c in enabled_courses(traj_root) if c not in runtimes]
                fresh = [c for c in fresh if c not in report.skipped]
                if fresh:
                    log(f"[serve] 发现新课程：{', '.join(fresh)}")
                    _newly = _open_courses(
                        fresh, runtimes, report, argv=argv, traj_root=traj_root
                    )
                    for _c in _newly:
                        _m = _marker_mtime_ns(traj_root, _c)
                        if _m is not None:
                            seen_marker[_c] = _m
                    _enqueue_opened(
                        sup, runtimes, report, _newly, step_mode=step_mode, traj_root=traj_root
                    )
            if max_seconds and (now() - t0) >= max_seconds:
                report.stop_reason = "max_seconds"
                break
            sleep(poll_sec)
    except KeyboardInterrupt:
        report.stop_reason = "interrupted"
        log("[serve] 收到中断——干净退出（各课账本已落盘，指针续跑可用）")

    report.courses = {
        course: {
            "state": q.state,
            "next_it": q.next_it,
            "rounds_done": q.rounds_done,
            "current": q.current.kind if q.current else "",
            "reason": q.reason,
            "engine": "loaded" if pool.peek(course) is not None else "cold",
        }
        for course, q in sup.courses.items()
    }
    if own_pool:
        pool.close()  # 自己建的池：退出前释放全部 torch 栈（注入的池归调用方管）
    report.engines = pool.snapshot()  # 快照在 close 之后：`loaded` 为空、计数器保留
    close_course_sinks()
    log(
        f"[serve] 退出（{report.stop_reason}）：步数={report.steps} 课程="
        + ", ".join(f"{c}:{v['state']}/it{v['next_it']}" for c, v in report.courses.items())
    )
    return report


def _planner(runtimes: dict[str, CourseRuntime]) -> Callable[[str, int, Any], list[Any]]:
    """调度器的 planner：委托该课的 `LoopRunner.planner`（步骤表 = 幂等判据的唯一来源）。"""

    def planner(course: str, it: int, queue: Any) -> list[Any]:
        rt = runtimes[course]
        if rt.runner is None:  # 引擎还没建（初始入队走 plan_course，不经这里）
            return []
        return rt.runner.planner(course, it, queue)

    return planner


def maybe_auto_stop_course(*, kind: str, auto_stop: bool, traj: str | Path) -> bool:
    """BC 课程 `auto_stop`：自然收官后删开课标记（训练侧不再认领；控制台读标记即停显）。

    hub 模式/暂停意图不动——自然收官不是用户停课，重开走正常开课流程。
    RL 课无此键（收官不清标记，重开即续跑，2026-09-25 C-0 语义）。"""
    if kind != "bc" or not auto_stop:
        return False
    marker = Path(traj) / COURSE_ENABLE_MARKER
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        return False
    log("[serve] auto-stop：已删开课标记（预定轮跑完，课程不再入队）")
    return True


def _held_waiting(rt: CourseRuntime | None) -> bool:
    """这门课现在是不是「被接管、在等云机」（★P1-3 / ★M2）——判据住 `loop_plan.course_is_held`
    （与 `step_course_iter` 同一处裁决；这里只做 `rt` → `args` 的适配）。"""
    args = getattr(rt, "args", None)
    return False if args is None else course_is_held(args)


def _settle_rounds(
    sup: Supervisor, runtimes: dict[str, CourseRuntime], done_hooked: set[str]
) -> str:
    """给**刚**收官的课程做收官副作用（每课一次），返回本轮的整体结论。

    跑满的课：`TrainingLoop.finish_course()`（收敛预采 / 云机 PAUSE / 收官 eval drain /
    `run_complete` 落账，与单课程入口共用同一份实现）——**不停车**（停车会冻住其余课，见
    模块 docstring）。RL 的 drain 传 `block=False`（派发即返回，G2）；BC 是另一份实现
    （`BcLoop.finish_course(self, it)`），按 kind 分派、**不带 kwargs**（P0-2）。停腿
    （ABORTED）的课不做收官副作用（它不是正常跑满）。
    """
    for course, q in sup.courses.items():
        if q.state != QUEUE_DONE or course in done_hooked:
            continue
        rt = runtimes.get(course)
        if rt is None or rt.engine is None:
            continue  # 一步都没跑过：没有预采子进程/云机态可收
        # ★P1-3：「离线 = 等待，不是收官」。P1-1 已把离线轮映射成 WAIT（队列因此不会走到
        # QUEUE_DONE）；这里是第二道闸：哪怕它真因预算/硬边界走到 DONE，也**不得**在这条
        # 腿上写 `run_complete` / 发云机 PAUSE——云机那边还在跑（R1-e）。
        if _held_waiting(rt):
            continue
        done_hooked.add(course)
        with prefix_scope(course):
            # kind 分派（评审 P0-2）：BC 的 `finish_course(self, it)` 是另一份实现
            # （trainer/bc_loop.py），**传 kwargs 会 TypeError 带崩 serve**；RL 传
            # drain=True, block=False——派发即返回，不冻其它课（G2/N4）。
            if str(getattr(rt, "kind", "") or "") == "bc":
                rt.engine.finish_course(max(int(q.next_it) - 1, 0))
            else:
                rt.engine.finish_course(
                    max(int(q.next_it) - 1, 0), drain=True, block=False
                )
        log(
            f"[serve] 课程 {course} 已收官（{q.rounds_done} 轮，指针 it{q.next_it}）——"
            "控制台停→开后自动重新入队（开课标记 mtime 更新即重开信号）"
        )
        bc_rt = getattr(rt, "bc", None)
        bc_course = getattr(bc_rt, "course", None)
        maybe_auto_stop_course(
            kind=str(getattr(rt, "kind", "") or ""),
            auto_stop=bool(getattr(bc_course, "auto_stop", False)),
            traj=str(getattr(bc_rt, "traj", "") or ""),
        )
    if _all_settled(sup):
        return "all_settled"
    return "waiting"  # 还在等外部（远端 PPO / 预采）：让位后稍后再问
