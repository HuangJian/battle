"""worker/course_args.py — 课程解析与启动期校验链（**判据单点**）。

为什么要单独成家（2026-10-05，plan/course-startup-recover §4.1【评审补】）：这条链同时被
**两个面**消费——真开课的 `loop_serve.open_course`（第一段）与只读视图的 `course_openable`
（控制台红条 / `waiting.kind=blocked` 的判据）。放在 `loop_serve` 里会让 `loop_plan` 反向依赖它
（循环 import + 只读 `--json` 每轮拉起整个 serve 模块的导入代价）；搬到训练栈家（`worker/`）后
两边**同源 import**，不再有第二份判断。

★ 为什么住 `worker/` 而不是 `trainer/`：`trainer/` 只许编排（
`tests/test_layering.py::test_trainer_holds_only_orchestration_modules`）——本模块是训练栈的
解析/校验纯逻辑，就家庭在 `worker/`（L2，只向下用 `common/` 与 `worker/` 内部）。

链条语义一字未动（rl-config.json 默认 → argparse → `apply_course` 课程覆盖 → 冲突检测 →
显式 stream 标记 → `validate_args`）；对拍用例仍在
`tests/trainer/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`。

零副作用纪律：本模块只做解析/校验；锁、日志镜像、hub 停机态等副作用留在 `loop_serve.open_course`。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from common.log import log
from worker.modes import apply_mode_flags, merged_mode_args, resolve_mode

#: 课程**机器侧覆盖**的键白名单（rl-config `courses.<课>.<key>`，2026-09-19 / R3-5）。
#:
#: 为什么这些键住 rl-config 而**不能**住 `curricula/<课>.jsonc`：课程文件字节 = `course_fp`
#: （语料血缘 / 熔断口径，D14）——往课程文件里加一个旋钮，熔断会把同一份语料读成新语料。
#: 机器侧旋钮**永不进 curricula**。
#:
#: 为什么它今天**是空的**：单进程服务器（`--serve`）**无法**用进程级 CLI 表达「这门课怎么跑」
#: ——一个进程服务 N 门课，命令行只有一份。控制台过去往**每门课**的 trainer 命令行里塞
#: 那些旋钮，收敛成一个共享 trainer 后曾搬到这里（per-course，且随盘持久）。但两个成员先后
#: 退役：「PPO 跑在哪 / 怎么降级」2026-09-21 随单一 PPO 路径删除（§3），`gate_halt_mode`
#: 2026-10-01 随门禁停机**平台级化**摘除（`tmp/gate-halt.json` + 控制台开关，不再按课程）。
#: 结构保留（不是删掉这段）：下一条「单进程表达不了、又确实按课不同」的旋钮还往这里加，
#: 判据仍是「课程的机器侧配置，且不进 `curricula/*.jsonc`」。
#:
#: ★ **2026-09-19 删掉了两个键**（用户口径「课程任务与 worker 节点互相正交」）：
#: `remote_transport` 与 `remote_hub_url`。它们是「把**这门课**钉到某条传输路 / 某个 hub」的
#: 耦合旋钮：课程定义任务，worker 节点提供算力，谁接到活由**部署**（`rl.hub_push` + 登记节点）
#: 与 hub 的队列决定，不该由科目名决定。旧配置里若还留着这两个键，**不再被读**（不报错，
#: 静默失效）；控制台启动时会把它们连同 `push_node_url` / `hub_push` 一并清理（见
#: `dashboard/src/stack/course-knobs.ts::pruneLegacyCourseKnobs`）。
COURSE_MACHINE_OVERRIDE_KEYS: tuple[str, ...] = (
    # ★ 2026-10-01 删掉 `gate_halt_mode`（plan/gate-halt-platform-level）：门禁停机模式升成
    #   **平台级**（`tmp/gate-halt.json`，控制台一处切、全局生效、到点回落 halt）⇒ 不再按课程
    #   下发；旧配置里若还留着该键，**不再被读**（不报错，控制台开课时顺手清）。
    # ★ 2026-09-21 删掉 `remote_degrade_after`（plan/accident.plan.md §3）：单一 PPO 路径下
    #   没有"就地下沉到本机算"这回事；旧配置里若还留着该键，**不再被读**（不报错）。
)


#: 课程文件里课程名 → 路径约定下的 stem（读 courses.<课> 块用）。

def _read_rl_config() -> dict:
    """读 rl-config.json（读不到 / 形状不对 → 空 dict）。

    **读面只读一处**：路径走 `biz.config.rl_config_path()`（env `BCITY_RL_CONFIG` 可重定向，
    与 `trainer/run_rl.py` 完全同源）——否则「用例自带夹具」在 serve 侧做不到，读的还是本机那份
    未入库的配置。本函数仍是测试注入点（用例可以直接换掉它）。
    """
    from worker.config import read_rl_config_file

    return read_rl_config_file()


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
    """课程 stem → 生效 args（**与 `trainer/run_rl.py --course <stem>` 逐字段一致**）。

    解析链一字不差地复刻 `trainer/run_rl.py::main` 的启动段（rl-config.json 默认 → argparse →
    `apply_course` 课程覆盖 → 冲突检测 → 显式 stream 标记 → `validate_args`）。**不作弊**：
    参数语义没有第二份实现，`tests/trainer/test_serve_wiring.py::test_course_args_match_run_rl_echo_config`
    用 `trainer/run_rl.py --course X --echo-config` 对拍本函数的每一字段（漂移即红）。

    `argv` = serve 级附加参数（如 `--mode goal`）；课程由**课程列表**给出，故这里拒绝
    `--course`（避免「列表里的课」与「argv 里的课」两个来源）。
    """
    extra = list(argv or [])
    if any(a == "--course" or a.startswith("--course=") for a in extra):
        raise SystemExit("[serve] 课程由课程列表给出，不要在附加参数里再传 --course/--course-file")

    mode = resolve_mode(extra)
    cfg = _read_rl_config()  # 与 trainer/run_rl.py 同源（`BCITY_RL_CONFIG` 可重定向）
    rl_args, _src = merged_mode_args(cfg, mode)

    from worker.cli import build_argparser

    ap = build_argparser(mode, rl_args)
    args = ap.parse_args([*extra, "--course", course])
    apply_mode_flags(args)

    from worker.config import apply_course, course_cli_conflicts, course_from_args, validate_args

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




def course_openable(course: str) -> tuple[bool, str]:
    """只读判据：这门课能不能被 `open_course` 打开（**复用同一条校验链**，零副作用）。

    · 判据来源 = `course_args`（与 `open_course` 第一段逐字段同源）——不为只读视图另写判断；
    · 诚实性：读不到课程文件 / 解析失败 / 校验拒启 ⇒ `ok=false` + 原文 reason（不假设
      「读不到就没事」）；可运行 ⇒ `(True, "")`；
    · 零副作用：不建锁、不写账本、不推权重、不碰 runtimes（锁 / 日志镜像 / hub 停机态都在
      `open_course` 的后半段，不在本函数路径上）；
    · 覆盖边界：BC 课走 `_open_bc_course` 另一条链，**不在本函数覆盖面**（分流在
      `loop_plan.course_openable`）；serve 级 `--mode` 只读端拿不到 ⇒ 本判据对应
      「无 serve 级 argv」的校验结果。
    """
    try:
        course_args(course)
    except BaseException as e:  # SystemExit / FileNotFoundError / jsonc 解析错…全部翻成只读结论
        return False, f"{type(e).__name__}: {e}".strip()
    return True, ""
