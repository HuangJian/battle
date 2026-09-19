"""loop_serve —— 单进程服务多门课的训练入口（R2d 的「写的一半」，plan/r2-loop-task-queue §8）。

**为什么需要它**：R2c-2/R2c-3 把「一轮」拆成了可让位的细粒度任务、给了调度器（`loop_scheduler`）
与任务体↔引擎的桥（`loop_runner`），但**没有任何东西真的驱动它**——今天仍是「一门课一个
`run_rl.py` 进程」。本模块就是那个驱动者：一个进程、一个 supervisor、N 门课，一门课等外部
（云端 PPO / 预采子进程）时**执行权交给别的课**。

三个层次，**每层各做一次**（越界做两次都会伤到既有护栏，所以按层显式分开）：

| 层 | 频率 | 内容 |
|---|---|---|
| 进程级 | 一次 | UTF-8 stdio / `faulthandler` / `chdir(repo)` / 启动前 `git push`（`.git_push.lock` 串行化）/ bun 存在性 → `prepare_process()` |
| 课程级 | 每课一次 | 解析课程配置（与 `run_rl.py --course` **逐字段一致**）→ `validate_args` → **按课程的单实例锁**（同课双开响亮拒启）→ 日志镜像 → 清本课 hub 停机态 → 引擎对象（`TrainingLoop`，torch 由引擎自己 `_setup()` 在首次执行时才拉起） |
| 一步级 | 每步 | `Supervisor.step()` → `EnginePool.get(课)` → `LoopRunner.executor(task, queue)` |

**刻意与单课程入口不同的两处**（都在文档里写死，防「统一」时被顺手改回去）：

1. **不收官停车**：`_park_after_completion` 的死循环语义前提是「这个进程就是这门课」——单进程
   多课程下停车会冻住所有课。所以这里对跑满的课只做 `TrainingLoop.finish_course()`（收敛预采 /
   云机 PAUSE / `run_complete` 落账，三件事与单课程路径**共用同一份实现**），随后调度器把该课
   队列置 `done`，进程继续服务别的课；全部课都收官才退出（重启交给控制台/启动器）。
2. **不换 `sys.stdout`**：单课程入口用 `Tee` 把 stdout 落到 `args.out_log`；一个进程里套两个
   Tee 会把每行复制进两份课日志。这里改成**行级路由**（`rl.log.open_course_sink` +
   `prefix_scope`）：每行带 `[课]` 前缀，并镜像到该课自己的 `out_log`。

**可测性**：`serve(..., prepare=False)` + 注入 `pool` / `supervisor` ⇒ 全流程不碰 torch、不碰
网络、不起进程（见 `tests/test_serve_courses.py` 与 `e2e/test_serve_integration.py`）。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import force_utf8_stdio
from rl.engine_pool import DEFAULT_CACHE_COURSES, DEFAULT_CACHE_MB, EnginePool
from rl.log import close_course_sinks, log, open_course_sink, prefix_scope
from rl.loop_control import ControlApplier, read_control
from rl.loop_plan import course_facts, course_kind, course_traj, discover_courses, round_tasks_for
from rl.loop_runner import ROUND_KIND, LoopRunner
from rl.loop_scheduler import ABORTED, QUEUE_DONE, Supervisor
from rl.loop_tasks import RoundFacts, Task, pending_tasks
from rl.modes import apply_mode_flags, get_backend, merged_mode_args, resolve_mode
from rl.queue import REPO_ROOT
from train.loop_util import acquire_lock, cleanup_lock, course_lock_path

#: nn-training 目录（锁文件/课程文件都相对它——与 `run_rl.py` 的 `Path(__file__).parent` 同一个）。
NN_DIR = Path(__file__).resolve().parent.parent

#: 本机资源池默认容量（与 `run_rl_cluster.py` 的 CLI 默认值同一套；plan §6.2 定案 PPO/eval=1）。
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
    #: BC 课的解析结果（`rl.bc_loop.BcRuntime`）；RL 课为 None。
    bc: Any = None


@dataclass
class ServeReport:
    """一次 serve 的可断言结论（测试/控制台都用它，不再从日志里猜）。"""

    courses: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    engines: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    stop_reason: str = ""


# ---------------------------------------------------------------- 进程级一次


def prepare_process(argv: list[str] | None = None) -> str:
    """进程级一次性准备，返回 bun 路径（rollout 需要它）。**副作用：启动前 push 当前分支。**

    与 `run_rl.py` 的对应片段同序同义（B7 之后 torch 仍不在启动路径上）：UTF-8 stdio →
    faulthandler → `chdir(REPO_ROOT)` → 启动前 `git push`（repo 级 O_EXCL 锁串行化——多课并发
    push 会顶成 non-fast-forward/锁竞争）→ 节点升级分支锁到训练机当前分支 → bun 存在性。

    **只做一次**：每课各做一次就等于每个课程都推一遍 git（这正是单进程入口要省掉的事）。
    """
    force_utf8_stdio()
    import faulthandler

    faulthandler.enable()
    os.chdir(str(REPO_ROOT))

    from rl.archive import ensure_current_branch_pushed

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
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        **_POPEN_NO_WINDOW,
    ).stdout.strip()
    if branch and branch != "HEAD":
        import dist_common as _dc

        _dc.set_upgrade_branch(branch)
        log(f"[serve] node upgrade branch locked to training-machine branch: {branch}")

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[serve] bun not found on PATH — rollout needs it")
    return bun


#: 课程**机器侧覆盖**的键白名单（rl-config `courses.<课>.<key>`，2026-09-19 / R3-5）。
#:
#: 为什么这些键住 rl-config 而**不能**住 `curricula/<课>.jsonc`：课程文件字节 = `course_fp`
#: （语料血缘 / 熔断口径，D14）——往课程文件里加一个旋钮，熔断会把同一份语料读成新语料。
#: 机器侧旋钮**永不进 curricula**。
#:
#: 为什么现在需要它们：单进程服务器（`--serve`）**无法**用进程级 CLI 表达「这门课怎么跑」
#: ——一个进程服务 N 门课，命令行只有一份。控制台过去往**每门课**的 trainer 命令行里塞
#: `--remote-degrade-after` / `--gate-halt-mode`，收敛成一个共享 trainer 后那些旋钮搬到这个块
#: （per-course，且随盘持久——比一次性的 flag 耐久）。
#:
#: ★ **2026-09-19 删掉了两个键**（用户口径「课程任务与 worker 节点互相正交」）：
#: `remote_transport` 与 `remote_hub_url`。它们是「把**这门课**钉到某条传输路 / 某个 hub」的
#: 耦合旋钮：课程定义任务，worker 节点提供算力，谁接到活由**部署**（`rl.hub_push` + 登记节点）
#: 与 hub 的队列决定，不该由科目名决定。旧配置里若还留着这两个键，**不再被读**（不报错，
#: 静默失效）；控制台启动时会把它们连同 `push_node_url` / `hub_push` 一并清理（见
#: `dashboard/src/stack/course-knobs.ts::pruneLegacyCourseKnobs`）。
COURSE_MACHINE_OVERRIDE_KEYS: tuple[str, ...] = (
    "remote_degrade_after",  # T7 远端连败降级本机的阈值（控制台的 opt-in 开关）
    "gate_halt_mode",  # 门禁失败语义（halt/skip…）
)


def cluster_lock_path() -> str:
    """单进程服务器的锁文件：`nn-training/.run_cluster.lock`（`course=''` ⇒ 无课程名后缀）。"""
    from train.loop_util import course_lock_path

    return course_lock_path(str(NN_DIR), "", "run_cluster")


def acquire_cluster_lock(lock_path: str, *, force: bool = False) -> bool:
    """进程级单实例锁（2026-09-19 / R3-5）：单进程服务**所有**课程 ⇒ 双开 = 两套调度器
    抢同一批 traj。按课锁拦不住这一类（两套调度器可以各跑一半课程，每门课都只有一个跑者）。

    实现复用 `run_rl._acquire_run_rl_lock`（O_CREAT|O_EXCL；holder 死了自动收回）——
    锁文件里写 `pid|python|ts`，与其它锁同一种形状（控制台按同一读法看它）。
    """
    from run_rl import _acquire_run_rl_lock

    return _acquire_run_rl_lock(str(lock_path), force=force)


def release_cluster_lock(lock_path: str) -> None:
    """释放自己持有的单实例锁（已易主则不删——与 `_cleanup_run_rl_lock` 同契约）。"""
    from run_rl import _cleanup_run_rl_lock

    _cleanup_run_rl_lock(str(lock_path))


#: 课程文件里课程名 → 路径约定下的 stem（读 courses.<课> 块用）。

def _read_rl_config() -> dict:
    """读 rl-config.json（读不到 / 形状不对 → 空 dict）。**单独一个函数**：这是测试注入点
    （用例不碰仓根的真 rl-config），也是「读面只读一处」的写法。

    形状校验不是防御性装饰：读者按 `cfg.get("courses")` 取块，而一份顶层是数组/字符串的
    JSON（手改坏了）会让 `.get` 直接 AttributeError 落在**开课路径**上——一门课开不起来还
    看不出为什么。空 dict ⇒ 退化成「没配机器侧旋钮」，与文件不存在同一个结果。
    """
    try:
        data: Any = json.loads((NN_DIR / "rl-config.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_course_machine_overrides(
    args: Any, course: str, cfg: dict | None = None, *, log_fn: Callable[[str], None] | None = None
) -> list[str]:
    """把 rl-config `courses.<课>` 里的机器侧旋钮施加到 args；返回**已施加**的键（供测试/日志）。

    契约（两条都与既有写法同源，不发明新语义）：
      · 只认白名单 `COURSE_MACHINE_OVERRIDE_KEYS`——别的键（配额 / 隧道选项）各自有既有的
        读取点，本函数一个字都不碰；
      · 目标 args 没有这个字段（BC 解析器比 RL 少几个键）⇒ **响亮跳过**并记一行，不 setattr 造字段
        （造出来的字段没有任何读者，只会让人以为生效了）。

    施加时机 = `course_args`/`_open_bc_course` 的**课程覆盖之后、`validate_args` 之前**。
    优先顺序（高→低）：本覆盖（rl-config `courses.<课>`，**per-course 最具体**）→ serve 级 argv
    → 课程文件 → rl-config 默认。「谁赢」本身不是重点，重点是**逐键打印生效值**：静默改写
    执行面（该走 pull 却走 push）正是「看起来正常」那类事故的温床。
    """
    log_fn = log_fn or log
    if cfg is None:
        cfg = _read_rl_config()
    block = ((cfg.get("courses") or {}).get(course) or {}) if isinstance(cfg, dict) else {}
    if not isinstance(block, dict):
        return []
    applied: list[str] = []
    for key in COURSE_MACHINE_OVERRIDE_KEYS:
        if key not in block:
            continue
        val = block[key]
        if not hasattr(args, key):
            log_fn(f"[serve] 课程 {course} 的 courses.{course}.{key} 本课程种类没有该参数 ⇒ 跳过")
            continue
        setattr(args, key, val)
        applied.append(key)
    if applied:
        log_fn(
            f"[serve] 课程 {course} 机器侧覆盖（rl-config courses.{course}）: "
            + ", ".join(f"{k}={getattr(args, k)!r}" for k in applied)
        )
    return applied


# ---------------------------------------------------------------- 课程级一次


def course_args(course: str, argv: list[str] | None = None) -> Any:
    """课程 stem → 生效 args（**与 `run_rl.py --course <stem>` 逐字段一致**）。

    解析链一字不差地复刻 `run_rl.py::main` 的启动段（rl-config.json 默认 → argparse →
    `apply_course` 课程覆盖 → 冲突检测 → 显式 stream 标记 → `validate_args`）。**不作弊**：
    参数语义没有第二份实现，`tests/test_serve_args.py` 用 `run_rl.py --course X --echo-config`
    对拍本函数的每一字段（漂移即红）。

    `argv` = serve 级附加参数（如 `--mode goal`）；课程由**课程列表**给出，故这里拒绝
    `--course`（避免「列表里的课」与「argv 里的课」两个来源）。
    """
    extra = list(argv or [])
    if any(a == "--course" or a.startswith("--course=") for a in extra):
        raise SystemExit("[serve] 课程由课程列表给出，不要在附加参数里再传 --course/--course-file")

    mode = resolve_mode(extra)
    try:
        cfg = json.loads((NN_DIR / "rl-config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    rl_args, _src = merged_mode_args(cfg, mode)

    from rl.cli import build_argparser

    ap = build_argparser(mode, rl_args)
    args = ap.parse_args([*extra, "--course", course])
    apply_mode_flags(args)

    from rl.config import apply_course, course_cli_conflicts, course_from_args, validate_args

    # 课程配置化（plan/rl-training-config.md §3）：优先级 课程 > rl-config.json > argparse 默认。
    # `resolve_course` 找不到 `<stem>.jsonc` 时抛 FileNotFoundError（含可用课程列表）——
    # 由调用方按「故障域 = 单课」处理（响亮跳过这门课，不影响别的课）。
    co = course_from_args(args)
    if co is not None:
        cli_before = {k: v for k, v in vars(args).items()}
        defaults_ns = ap.parse_args([])
        apply_course(args, co)
        conflicts = course_cli_conflicts(cli_before, vars(defaults_ns), co)
        if conflicts:
            raise SystemExit(
                "[serve] 附加参数与课程配置冲突（课程是单一事实来源，无 CLI 逐参覆盖——"
                "plan §3）：\n  " + "\n  ".join(conflicts)
            )

    # 课程**机器侧覆盖**（rl-config `courses.<课>`）：优先级「argv > 机器侧覆盖 > 课程文件 >
    # rl-config 默认」（课程文件不该管机器侧；argv 是当前进程的明确意图）。放在
    # `validate_args` 之前——覆盖后的值同样要过一次启动期校验（P1-3 的口径）。
    apply_course_machine_overrides(args, course, cfg)

    defaults_ns2 = ap.parse_args([])
    args._explicit_stream = int(getattr(args, "stream", 0) or 0) != int(
        getattr(defaults_ns2, "stream", 0) or 0
    )
    args._explicit_double_buffer = int(getattr(args, "double_buffer", 0) or 0) != int(
        getattr(defaults_ns2, "double_buffer", 0) or 0
    )
    validate_args(args)
    return args


def open_course(
    course: str, *, argv: list[str] | None = None, traj_root: str = "tmp"
) -> CourseRuntime:
    """开课（课程级一次性副作用）。**失败即抛**，由调用方按课隔离。

    ① args（`course_args`）；② 路径一致性核对（发现路径 vs 课程配置的 `traj`，不一致**响亮
    记录**但不自作主张改一边）；③ **按课程的单实例锁**（同课双开响亮拒启——2026-09-06 双 trainer
    并行写同一 traj 的护栏，按课程命名后对并行课程不误伤）；④ 每课日志镜像（行路由）；
    ⑤ 清本课 hub 停机态（残留 halt 会让首轮 PPO job 进无人区）。

    BC 课程走 `_open_bc_course`（**同一个函数名/同一份副作用**，只是解析链与锁名不同：
    `run_bc.py` 用的是 `run_bc` 锁——共用 `run_rl` 锁会让「控制台起 run_bc」与「serve 起同一个
    BC 课」互相看不见，两边同时开课）。

    **不在这里** `_setup()`：那会拉起 torch 并写 `run_start`——「扫到但本轮没在训」的课
    不该付这个代价。首次执行该课的一步时由 `ensure_ready` 做（见 `serve`）。
    """
    if course_kind(course) == "bc":
        return _open_bc_course(course, argv=argv, traj_root=traj_root)
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    args = course_args(course, argv)
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
    """开一门 BC 课（R3-4）：与 `run_bc.py` 的启动段**同义**，只是不做进程级那几件（utf8 /
    chdir / 启动前 git push）——那些由 `prepare_process` 在进程级做过一次。

    锁用 `run_bc`（与单课程入口同名同路径）：控制台起的 `run_bc.py --course X` 与 serve 里的
    同一门 BC 课必须互相看得见（2026-09-06 双 trainer 写同一 traj 的护栏）。
    """
    from rl.bc_loop import bc_course_args, resolve_bc_runtime
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    args = bc_course_args(course, argv)
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
    """首次执行前 `_setup()` 一次；同一对象再进（可能刚被池 `release_torch` 过）补齐栈。

    「引擎对象换了」= 新建（首用 / 被池驱逐后重建）⇒ 走 `_setup()`（与一次进程重启同义：
    `run_start` 续写、账本指针仍是 SSOT）；「对象没换」⇒ `_ensure_local_ppo_stack()` 幂等补齐
    （未被释放时立即返回，零代价）。
    """
    if rt.engine is not engine:
        engine._setup()
        rt.engine = engine
        return
    engine._ensure_local_ppo_stack()


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

    **BC 课（R3-4）**：引擎是 `rl.bc_loop.BcLoop`（同一套 `_setup`/`run_one_round`/
    `finish_course`/`release_torch` 子集），且**恒为轮粒度**（13 步表是 RL 的一轮，硬套会
    给 BC 发它不认识的待办）；指针走引擎自己的 `ledger_next_it`（`bc_round_completed`）。
    """
    from rl.loop_core import TrainingLoop

    def factory(course: str) -> Any:
        rt = runtimes[course]
        if rt.kind == "bc":
            from rl.bc_loop import BcLoop

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
        from run_rl import update_kwargs

        engine = TrainingLoop(rt.args, get_backend(rt.args.mode), bun, update_kwargs)
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
    pool: EnginePool, runtimes: dict[str, CourseRuntime]
) -> Callable[[Any, Any], Any]:
    """调度器执行体：取引擎（可能刚重建）→ 补齐栈 → 交给该课的 `LoopRunner.executor`。

    `prefix_scope` 是**行级课程归属**的落点：这一步（及其内部全部日志）带 `[课]` 前缀并镜像
    到该课日志文件。放在这里而不是引擎里，是因为「谁在执行哪门课」只有调度器知道。
    """

    def execute(task: Any, queue: Any) -> Any:
        rt = runtimes[task.course]
        engine = pool.get(task.course)
        ensure_ready(rt, engine)
        with prefix_scope(task.course):
            assert rt.runner is not None  # factory 保证
            return rt.runner.executor(task, queue)

    return execute


def _all_settled(sup: Supervisor) -> bool:
    """全部课程**已收官 / 停腿**。

    **暂停不算收官**（用户口径「暂停 = 保留队列，恢复后接着跑」）：把 PAUSED 当收官会让
    「暂停一门课」顺手把整个进程退掉，于是恢复意图永远没人执行。暂停的课会让显式课程模式
    的进程一直等（用 `--max-seconds` 兜底）；发现模式本来就不退。
    """
    return all(q.state in (QUEUE_DONE, ABORTED) for q in sup.courses.values())


# ---------------------------------------------------------------- 主循环


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
            report.skipped[course] = f"{type(e).__name__}: {e}"
            log(f"[serve] 跳过课程 {course}：{type(e).__name__}: {e}")
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
) -> None:
    """给**刚开的**课挂队列——单课失败隔离（一门课读盘/算判据失败不该带走整个进程）。

    失败时把该课从 `runtimes` 里摘掉并记进 `skipped`：没有队列的课不会被调度器选中，留在
    `runtimes` 里只会变成一个「看着开着、实际没人跑」的幽灵（且下次扫到它也不会重试）。
    """
    for course in names:
        try:
            _enqueue(sup, runtimes, course, step_mode=step_mode)
        except BaseException as e:
            report.skipped[course] = f"入队失败 {type(e).__name__}: {e}"
            runtimes.pop(course, None)
            log(f"[serve] 课程 {course} 入队失败，本次不服务：{type(e).__name__}: {e}")


def serve(
    courses: list[str] | None = None,
    *,
    argv: list[str] | None = None,
    traj_root: str = "tmp",
    control_file: str | None = None,
    iters: int = 0,
    poll_sec: float = DEFAULT_POLL_SEC,
    capacities: dict[str, int] | None = None,
    cache_courses: int = DEFAULT_CACHE_COURSES,
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
    `--traj-root` 下所有有账本的课程，之后每个空转拍再扫一次（新课程自动入队）；一门课都
    没有也能起，队列就空着等（**不退出**：空队列是合法稳态，不是结束条件）。显式给
    `courses` 时退化为「只看这几门」，全收官即退出（e2e/单课调试用）。

    每拍的**控制面**（`rl/loop_control.py`）：读 `tmp/loop-control.json` 的暂停意图 →
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
    explicit = list(courses or [])
    if discover:
        explicit = discover_courses(traj_root)
        if explicit:
            log(f"[serve] 发现 {len(explicit)} 门课程：{', '.join(explicit)}")
        else:
            log(
                f"[serve] {traj_root} 下暂无课程账本——进程照常运行，队列空着等"
                "（有新课程账本出现即自动入队）"
            )
    _open_courses(explicit, runtimes, report, argv=argv, traj_root=traj_root)
    if not runtimes and not discover:
        report.stop_reason = "no_courses"
        log("[serve] 显式课程表里没有能开的课——退出")
        return report

    own_pool = pool is None
    pool = pool or EnginePool(
        factory=build_factory(runtimes, bun=bun, iters=iters, step_mode=step_mode, now=now),
        courses=cache_courses,
        mb=cache_mb,
    )
    sup = supervisor or Supervisor(
        executor=build_executor(pool, runtimes),
        planner=_planner(runtimes),
        capacities=dict(capacities or DEFAULT_CAPACITIES),
        now=now,
    )

    # 初始队列内容用**盘上事实**算（不构建引擎：扫到但没在训的课不该拉起 torch）。
    _enqueue_opened(sup, runtimes, report, list(runtimes), step_mode=step_mode)

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
                fresh = [c for c in discover_courses(traj_root) if c not in runtimes]
                # 被跳过过的课不再重试（课程文件缺失 = 这一轮修不好；避免每秒刷日志）
                fresh = [c for c in fresh if c not in report.skipped]
                if fresh:
                    log(f"[serve] 发现新课程：{', '.join(fresh)}")
                    _enqueue_opened(
                        sup,
                        runtimes,
                        report,
                        _open_courses(fresh, runtimes, report, argv=argv, traj_root=traj_root),
                        step_mode=step_mode,
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


def _settle_rounds(
    sup: Supervisor, runtimes: dict[str, CourseRuntime], done_hooked: set[str]
) -> str:
    """给**刚**收官的课程做收官副作用（每课一次），返回本轮的整体结论。

    跑满的课：`TrainingLoop.finish_course()`（收敛预采 / 云机 PAUSE / `run_complete` 落账，
    与单课程入口共用同一份实现）——**只做这三件事，不停车**（停车会冻住其余课，见模块
    docstring）。停腿（ABORTED）的课不做收官副作用（它不是正常跑满）。
    """
    for course, q in sup.courses.items():
        if q.state != QUEUE_DONE or course in done_hooked:
            continue
        done_hooked.add(course)
        rt = runtimes.get(course)
        if rt is None or rt.engine is None:
            continue  # 一步都没跑过：没有预采子进程/云机态可收
        with prefix_scope(course):
            rt.engine.finish_course(max(int(q.next_it) - 1, 0))
        log(
            f"[serve] 课程 {course} 已收官（{q.rounds_done} 轮，指针 it{q.next_it}）——"
            "本进程不再为它接新轮（重启由控制台/启动器负责）"
        )
    if _all_settled(sup):
        return "all_settled"
    return "waiting"  # 还在等外部（远端 PPO / 预采）：让位后稍后再问
