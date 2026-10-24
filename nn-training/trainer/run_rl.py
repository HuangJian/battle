"""trainer/run_rl.py — RL 入口 = **CLI 壳**（解析 → 分流）+ 共享训练原语（plan/single-resident-trainer §3，S3）。

## 四种角色现在各在哪

| 角色 | 归宿 |
|---|---|
| 常驻训练循环（唯一写者） | `trainer/run_rl_cluster.py --serve` —— **本文件没有常驻分支** |
| 一次性腿（冒烟 / 导出 / 预采） | `trainer/once_shot.py` |
| 参数解析 · `--echo-config` dump · 准入拒启 | 本文件（**唯一的解析面**） |
| 共享原语（单实例锁 / PPO 更新参数 / 日志重定向） | 本文件（serve 与 run_bc 都在用，P1-F4：不许当残留清掉） |

`main()` = 解析 → `--echo-config` 短路 → 一次性腿分流进 `once_shot`；**没有**「什么都不带」的
活路 —— 那正是常驻训练，S3 后只有 `run_rl_cluster.py --serve` 能跑（拒启消息见
`worker/trainer_gate.py`）。**唯一保留的训练调用点在 `once_shot.py`**（冒烟是真跑一轮）。

## 为什么锁助手还住这里

`_acquire_run_rl_lock` / `_cleanup_run_rl_lock` / `_runrl_pid_alive` 是**共享原语**而非本入口的
私货：serve 的集群锁（`trainer/loop_serve.py::acquire_cluster_lock`）、`run_bc.py` 的单实例锁、
以及一串护栏用例都 import 这三个名字（评审 P1-F4）。S3 拆入口时它们**原地不动**。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 2026-09-30（刀 7）：本入口从 nn-training/ 顶层搬进 trainer/。既有前提随之失效，这里是
# 唯一的修复点：把 nn-training 根（= 脚本目录的父目录）放回 sys.path，仓内顶层导入语义不变。
# `Path(__file__).parent` 的语义（原来是 nn-training 根）改成 `parents[1]`——只在下文两处
# （chdir 到仓根 / 锁目录），各自写明为什么。
# 2026-10-11（S1）：原先还要摘掉脚本目录项，防脚本目录里的 `queue.py` 遮蔽 stdlib `queue`；该模块
# 已改名 `trainer/dispatch_queue.py`（plan/nn-training-reuse-and-deps.plan.md §S1），scrub 删除。
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from common.cli import run_main
from common.instance_lock import acquire_instance_lock, release_instance_lock
from common.log import Tee as _Tee
from common.log import log
from common.pid_probe import pid_alive as _pid_alive_impl
from common.platform_utils import force_utf8_stdio
from trainer.dispatch_queue import (  # noqa: F401 — run_rollout_queue re-exported for tests
    REPO_ROOT,
    run_rollout_queue,
)
from worker.cli import build_argparser
from worker.modes import apply_mode_flags, merged_mode_args, resolve_mode
from worker.resume import (
    completed_pairs,  # noqa: F401 — re-exported for tests
    last_completed_iter,  # noqa: F401 — re-exported for tests
    last_rotate_seed,  # noqa: F401 — 轮换 seed 的唯一实现（预采腿用它；e2e/test_run_rl 直读）
    peak_entropy,  # noqa: F401 — F4 ENT 相对崩塌基线回读（§339），re-exported for tests
    resumed_manifests,  # noqa: F401 — re-exported for tests
)
from worker.rl_config_schema import check_rl_config
from worker.schedule import kickstart_coef
from worker.train.loop_util import course_key_from_path


def update_kwargs(args, it: int, start_it: int, ref_model) -> dict:
    """intent/goal 模式的 PPO 更新参数：value 预热（前 warmup-iters 冻结主干只训
    value 头）+ kickstarting KL 惩罚（系数按策略迭代衰减）。自 run_rl_intent 原样
    迁入（数学不变）。纯函数，可单测。"""
    warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
    # 系数公式的**唯一**实现已下沉 `worker/schedule.kickstart_coef`（含阈值归零；
    # worker 侧守卫也要用它）。这里只接结果，不重算。
    kl_coef = kickstart_coef(args, it, start_it)
    return {
        "value_warmup_epochs": warmup_epochs,
        "ref_model": ref_model,
        "kl_coef": kl_coef,
        "seed": args.seed,
    }


def _setup_log_redirect(args) -> None:
    """stdout/stderr 重定向到 out_log/err_log（Tee 控制台+文件）。空字符串=仅控制台
    （per-tick 默认 → 行为不变）。落盘追加模式 + 启动横幅，多次启动日志累积。"""
    if getattr(args, "out_log", ""):
        try:
            p = Path(args.out_log)
            p.parent.mkdir(parents=True, exist_ok=True)
            sys.stdout = _Tee(sys.stdout, open(p, "a", encoding="utf-8"))
            log(f"[launch] stdout -> {p} (tee console+file, append)")
        except Exception as e:
            log(f"WARN cannot redirect stdout to {args.out_log}: {e}")
    if getattr(args, "err_log", ""):
        try:
            pe = Path(args.err_log)
            pe.parent.mkdir(parents=True, exist_ok=True)
            sys.stderr = _Tee(sys.stderr, open(pe, "a", encoding="utf-8"))
            log(f"[launch] stderr -> {pe} (tee console+file, append)")
        except Exception as e:
            log(f"WARN cannot redirect stderr to {args.err_log}: {e}")


def _log_rl_args(src: dict, merged: dict) -> None:
    """生效启动配置落地日志：标注每个键来源（rl.<mode> / intent_rl(legacy) / rl /
    fallback），便于核对单一事实来源（trust-but-verify）。"""
    log(
        "[launch] rl args source: "
        + " ".join(f"{k}={src.get(k, 'fallback')}" for k in sorted(merged))
    )


def _runrl_pid_alive(pid: int) -> bool:
    """跨平台的进程存活探测（委托唯一实现 `common.pid_probe.pid_alive`）。

    保留本名字只为调用点稳定。历史教训（都写进 `common.pid_probe` 模块 docstring）：① Windows 侧
    `os.kill(pid, 0)` 是 `TerminateProcess`，会把锁持有人直接杀掉；② 2026-09-13 曾把 Windows
    分支写成无条件路径，Linux 上遇到**已存在**的锁文件就 AttributeError——陈旧锁永不清理、
    同课双开从「响亮拒启」退化成崩溃（P1 验收被 stale 锁连续打崩两门课）；③ `pid <= 0` 命中的
    是**进程组**，残缺锁里的 0/-1 会被当成「有人持有」⇒ 同名课永久拒启。
    """
    return _pid_alive_impl(pid)


def _acquire_run_rl_lock(lock_path: str, *, force: bool = False) -> bool:
    """PID 文件单实例锁（**薄壳**：实现唯一住在 `common/instance_lock`）。

    2026-09-06 事故：旧启动器 start-training --kill-previous 漏杀旧 trainer（msys pgrep 对
    Windows 原生进程不可靠）→ 两个 trainer 并行写同一 traj 7 分钟，it57-59 各被
    两遍训练/落账。锁在进入训练主循环前把关，双开=响亮拒启而非静默并行。

    2026-10-11 收敛：本函数曾自带一套 `os.open(O_CREAT|O_EXCL)` + `os.write`（与
    `worker/train/loop_util`、`remote/code_snapshot` 各自一套 ⇒ 三个写入窗口），现在一律走
    `common/instance_lock`：持有者活着 → False；已死 / 够旧的残缺锁 → 接管；**残缺且新鲜**
    （别人刚创建还没写 pid）→ False **且不动那个文件**（那个毫秒级窗口曾让两个 starter
    各自自认唯一）。`force=True` = 显式接管（控制台/运维重启）。
    """
    return acquire_instance_lock(lock_path, marker=(), tag="run_rl", force=force)


def _cleanup_run_rl_lock(lock_path: str) -> None:
    """仅清理自己持有的锁——锁已易主（--force 抢占）时不删别人的。"""
    release_instance_lock(lock_path)


def _parse(args_argv: list[str]):
    """两阶段解析（阶段一 `--mode`，阶段二 rl-config 默认 + argparse）+ 课程施加。

    返回 `(ap, args, course, rl_src, rl_args)`。**只有一份**（旧版把这段摊在 main 里，
    预采子进程的命名空间重建靠的就是「同一个解析面」）。
    """
    mode = resolve_mode(args_argv)
    # 启动参数默认取自 rl-config.json（单一事实来源；CLI 显式传参覆盖 json 默认）。
    # 查找优先级 rl.<mode> → intent_rl 遗留块（intent/goal 迁移期）→ rl（D2）。
    # 路径的唯一来源是 `biz.config.rl_config_path()`（`BCITY_RL_CONFIG` 可重定向）——与
    # `trainer/loop_serve.py::_read_rl_config` 同源，用例才能用自带夹具做「解析链对拍」。
    from worker.config import read_rl_config_file

    _cfg = read_rl_config_file()
    # rl-config 键白名单校验（plan/rl-config-cleanup.plan.md §3.4）：**只告警不拒** ——
    # 拼错/退役的键此前会静默沉睡（`intent_rl` 沉睡到 2026-09-26）；白名单数据住
    # `rl_config.schema.json`（今天只有这里读它）。**只打日志**：告警不得变成拒启（用户裁决 O3）。
    # 注：`check_rl_config` 必须在**模块顶层** import——main() 里已经 `os.chdir` 到仓根，
    # 此时 `sys.path[0]=''` 已指向仓根，惰性 import 会找不到 `worker.rl_config_schema`。
    for _adv in check_rl_config(_cfg):
        log(f"[run_rl] rl-config 告警：{_adv}")
    _rl_args, _rl_src = merged_mode_args(_cfg, mode)

    ap = build_argparser(mode, _rl_args)
    args = ap.parse_args()
    apply_mode_flags(args)
    # ===== 课程配置化（plan/rl-training-config.md §3）：唯一启动入口 =====
    # 优先级 课程 > rl-config.json > argparse 默认；无 CLI 逐参覆盖。课程自带
    # 关卡布局/奖励公式/超参 schedule，apply 后由各阶段消费。
    from worker.config import apply_course, course_cli_conflicts, course_from_args

    course = course_from_args(args)
    if course is not None:
        # CLI 快照（apply 前）+ 纯默认基线（parse_args([])）→ 冲突检测
        _cli_before = {k: v for k, v in vars(args).items()}
        _defaults_ns = ap.parse_args([])
        apply_course(args, course)
        conflicts = course_cli_conflicts(_cli_before, vars(_defaults_ns), course)
        if conflicts:
            raise SystemExit(
                "[run_rl] --course 与显式 CLI 参数冲突（课程是单一事实来源，无 CLI 逐参"
                "覆盖——plan §3）：\n  "
                + "\n  ".join(conflicts)
                + "\n删掉冲突 flag 或改课程配置后重试"
            )
    # 内部预采子进程（T4 双缓冲）的动作覆盖：**必须在 apply_course 之后**（见
    # `worker/course_args.apply_collect_only_config` 的 docstring：bc/iters 这些键由课程定义，
    # CLI 传会被拒启、课程值又会覆盖 CLI）。放在 echo 短路之前 —— `--echo-config` 对拍
    # 要能看见覆盖后的生效值（用例用它验收）。
    if getattr(args, "collect_only_config", ""):
        from worker.course_args import apply_collect_only_config

        apply_collect_only_config(args, args.collect_only_config, log_fn=log)
    return ap, args, course, _rl_src, _rl_args


def main() -> None:
    # 子进程字节流恒 UTF-8（压过 PYTHONIOENCODING/PYTHONUTF8/代码页）——validate_args
    # 等的中文 SystemExit/日志对任何捕获方都是确定编码；配对消费方显式 utf-8 解码。
    force_utf8_stdio()
    # I1 快速缓解（hy E4/dsf）：fatal 信号（SIGSEGV/SIGABRT/SIGFPE…）时把 Python 栈
    # 倾倒到 stderr——随 §16.2 的 run.log 落文件，两起「无堆栈消失」事故不再完全盲区。
    # OOM killer（SIGKILL）不经过信号处理器——那种死法由 biz/forensics.py 的提交边界
    # 快照兜底取证（最后一条 forensics = 临终状态）。
    import faulthandler

    faulthandler.enable()
    # Anchor cwd to the repo root (parent of nn-training/): all default paths
    # (tmp/student-weights-dagger, tmp/rl-weights, tmp/rl-traj) are repo-root
    # relative. Required for the unified launcher's --detach (dashboard/src/launch/cli.ts), whose
    # is nn-training/ — same pattern as every other entry script's REPO_ROOT.
    # 2026-09-30（刀 7）：入口从 nn-training/ 顶层搬进 trainer/ ⇒ 上溯层数 2 → 3
    # （仓根仍是父目录的父目录的父目录）。层数写错 = cwd 停在 nn-training/ ⇒ 上面那些
    # tmp/ 默认路径全部错位。
    os.chdir(str(Path(__file__).resolve().parents[2]))
    ap, args, course, _rl_src, _rl_args = _parse(sys.argv[1:])
    if getattr(args, "echo_config", False):
        # 纯 dump 面（对拍 oracle 读它的 stdout）：**在分流之前**短路——`--echo-config`
        # 不是一条腿，它就是「把解析结果打印出来」。
        from worker.config import echo_config

        echo_config(args, course)
        log("[run_rl] --echo-config done — exit")
        return
    # 死键点名（plan/course-workers-removal §3-S1.5）：**开课侧**一次/门课。放在 echo 短路
    # **之后**——`--echo-config` 是纯 dump 面，不该多出别的行。
    if course is not None:
        from biz.course_spec import dead_key_warnings

        for _msg in dead_key_warnings(course):
            log(_msg)
    # 课程名（与分流/锁段共用一次推导；非法 stem 在此即响亮拒启）。
    #
    # ❌ 2026-09-18 删掉了“按课程回填 remote_hubs[<stem>]”那段：hub/隧道已收敛为**单**
    # 实例（一个进程服务所有并行课程、一条隧道指向它），URL 是全局事实 ⇒ 只认单键
    # `rl.remote_hub_url`（首参数 `--remote-hub-url` 仍可显式压过）。
    try:
        _hub_course_key = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except ValueError as e:
        raise SystemExit(f"[run_rl] {e}") from e

    # ===== 分流（S3，plan/single-resident-trainer §3）：一次性腿 → once_shot；其余 = 拒启 =====
    # 判据（纯函数，住 `worker/trainer_gate.py`）：**一个一次性动作都没有** = 本进程想跑常驻
    # 训练循环 ⇒ 拒启。`--force` 过不了这一关（它是**锁抢先**，与准入是两件事）；也**没有**
    # 逃生舱——S3 之后常驻训练的唯一入口是 `run_rl_cluster.py --serve`（用户裁决 2026-10-17）。
    from worker.trainer_gate import REFUSE_MESSAGE, oneshot_action_present

    if not oneshot_action_present(args):
        raise SystemExit(REFUSE_MESSAGE)

    # P1-3（2026-09-02）：启动期配置校验（互斥/范围 fail fast——此前这些错误
    # 要等训练中途才暴露）。课程覆盖后校验（课程值是单一事实来源）。
    # stream/double-buffer 的显式传参判定：单一 PPO 路径下两者恒置 0（本机没有 PPO 窗口），
    # 但**显式** `--stream 1` / `--double-buffer 1` 仍要响亮报错（用户要求的能力已退役 ≠ 参数
    # 可以静默失效）⇒ 需区分「显式传参」与「吃 config 默认」。存到 args 供 validate_args 消费。
    _defaults_ns2 = ap.parse_args([])
    args._explicit_stream = int(getattr(args, "stream", 0) or 0) != int(
        getattr(_defaults_ns2, "stream", 0) or 0
    )
    args._explicit_double_buffer = int(getattr(args, "double_buffer", 0) or 0) != int(
        getattr(_defaults_ns2, "double_buffer", 0) or 0
    )
    from worker.config import validate_args

    validate_args(args)

    # stdout/stderr 落盘（out_log/err_log；CLI 可覆盖调试；per-tick 默认空=仅控制台）。
    _setup_log_redirect(args)
    # 生效启动配置落地日志（trust-but-verify：核对 json 默认是否被正确读取）。
    _log_rl_args(_rl_src, _rl_args)

    from trainer.once_shot import run_once_shot

    run_once_shot(args, course_key=_hub_course_key)


if __name__ == "__main__":
    run_main(main)
