from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pid_probe import pid_alive as _pid_alive_impl
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from platform_utils import force_utf8_stdio
from remote.protocol import COURSE_ENABLE_MARKER, coef_active
from rl.archive import ensure_current_branch_pushed
from rl.cli import build_argparser
from rl.collect_only import run_collect_only
from rl.log import Tee as _Tee
from rl.log import log
from rl.modes import apply_mode_flags, merged_mode_args, resolve_mode
from rl.queue import (  # noqa: F401 — run_rollout_queue re-exported for tests
    REPO_ROOT,
    run_rollout_queue,
)
from rl.resume import (
    completed_pairs,  # noqa: F401 — re-exported for tests
    last_completed_iter,  # noqa: F401 — re-exported for tests
    last_rotate_seed,
    peak_entropy,  # noqa: F401 — F4 ENT 相对崩塌基线回读（§339），re-exported for tests
    resumed_manifests,  # noqa: F401 — re-exported for tests
)
from train.loop_util import (
    acquire_lock,
    cleanup_lock,
    course_key_from_path,
    course_lock_path,  # per-course 锁名（plan §1.2）
)


def update_kwargs(args, it: int, start_it: int, ref_model) -> dict:
    """intent/goal 模式的 PPO 更新参数：value 预热（前 warmup-iters 冻结主干只训
    value 头）+ kickstarting KL 惩罚（系数按策略迭代衰减）。自 run_rl_intent 原样
    迁入（数学不变）。纯函数，可单测。"""
    warmup_epochs = args.epochs if (it - start_it) < args.warmup_iters else 0
    policy_iter = (it - start_it) - args.warmup_iters + 1
    kl_coef = (
        args.kickstart_kl * (args.kickstart_decay ** max(0, policy_iter - 1))
        if args.kickstart_kl > 0 and policy_iter >= 1
        else 0.0
    )
    # 几何衰减永远到不了精确 0（实测 kl=2^-36=1.455e-11 时判据仍放行，白付 ref 权重
    # 传输 + worker 每轮 3 s 预计算，而数学贡献 ≈1.8e-12 可忽略）。低于阈值直接归零。
    # ⚠ 这一处是**唯一**的衰减源（`rl/loop_steps.kickstart_coef` 只是它的薄包装），
    #   故归零后：loop_steps 不再附 ref 字节 -> worker 不再加载 -> engine 的 `> 0` 自然为假。
    if not coef_active(kl_coef):
        kl_coef = 0.0
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
    """跨平台的进程存活探测（委托唯一实现 `pid_probe.pid_alive`）。

    保留本名字只为调用点稳定。历史教训（都写进 `pid_probe` 模块 docstring）：① Windows 侧
    `os.kill(pid, 0)` 是 `TerminateProcess`，会把锁持有人直接杀掉；② 2026-09-13 曾把 Windows
    分支写成无条件路径，Linux 上遇到**已存在**的锁文件就 AttributeError——陈旧锁永不清理、
    同课双开从「响亮拒启」退化成崩溃（P1 验收被 stale 锁连续打崩两门课）；③ `pid <= 0` 命中的
    是**进程组**，残缺锁里的 0/-1 会被当成「有人持有」⇒ 同名课永久拒启。
    """
    return _pid_alive_impl(pid)


def _acquire_run_rl_lock(lock_path: str, *, force: bool = False) -> bool:
    """PID 文件单实例锁（O_CREAT|O_EXCL 原子创建；holder 死亡 → stale 自动清理）。

    2026-09-06 事故：旧启动器 start-training --kill-previous 漏杀旧 trainer（msys pgrep 对
    Windows 原生进程不可靠）→ 两个 trainer 并行写同一 traj 7 分钟，it57-59 各被
    两遍训练/落账。锁在进入训练主循环前把关，双开=响亮拒启而非静默并行。"""
    if force:
        try:
            os.remove(lock_path)
        except OSError:
            pass
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, f"{os.getpid()}|{sys.executable}|{int(time.time())}".encode())
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        pass
    try:
        with open(lock_path) as f:
            holder = int(f.read().split("|")[0])
    except (OSError, ValueError):
        holder = None
    if holder is not None and _runrl_pid_alive(holder):
        return False
    try:
        os.remove(lock_path)
    except OSError:
        pass
    return _acquire_run_rl_lock(lock_path, force=True)


def _cleanup_run_rl_lock(lock_path: str) -> None:
    """仅清理自己持有的锁——锁已易主（--force 抢占）时不删别人的。"""
    try:
        with open(lock_path) as f:
            holder = int(f.read().split("|")[0])
        if holder != os.getpid():
            return
    except (OSError, ValueError):
        return
    try:
        os.remove(lock_path)
    except OSError:
        pass


def _require_course_open(args, traj_root: Path) -> None:
    """开课闸（纯逻辑，可单测）：训练循环只认控制台写的「开课」标记。

    ★ 2026-09-21（plan/accident.plan.md §1）：CLI detached 直启会绕过控制台开课流程
    （漏开课标记 / 暂停意图解禁 / hub 课程模式三件套），并让 agent 去手写控制台拥有的
    状态文件（`training-enabled.txt` / `loop-control.json`）与控制台写面打架。

    判据**同源调用** `rl.loop_plan.course_enabled`（= `enabled_courses` 的谓词；标记与
    traj 目录同址 `<traj>/training-enabled.txt`）——**禁**在这里另写第二份"标记存在性
    检查"（第二份判据 drift 就是这类事故的老 pattern）。

    ⚠ 落点：`--traj` 是**本课**的 traj 目录（run_rl 直接在那下面写 training_log.jsonl），
    所以用谓词 `course_enabled(traj)`；**不得** `enabled_courses(traj)`（那会去扫子课程，
    判据错位）。范围：只卡**训练循环** —— collect-only（调用点在其后）、`--smoke` 预演、
    无课程（legacy 非课程路径）都不受影响。
    """
    if getattr(args, "smoke", False):
        return
    if getattr(args, "course_obj", None) is None:
        return  # 非课程路径：老用法逐字节不变（开课标记只存在于课程 traj 目录下）
    from rl.loop_plan import course_enabled

    if course_enabled(traj_root):
        return
    name = str(getattr(args, "course_name", "") or "?")
    raise SystemExit(
        f"[run_rl] 课程 {name!r} 未开课：{Path(traj_root) / COURSE_ENABLE_MARKER} 不存在 ——\n"
        "  训练只从控制台开课（dashboard → 课程 → 开课），不接受 CLI 直启：\n"
        "  直启会漏掉开课标记 / 暂停意图解禁 / hub 课程模式三件套。"
    )


def main() -> None:
    # 子进程字节流恒 UTF-8（压过 PYTHONIOENCODING/PYTHONUTF8/代码页）——validate_args
    # 等的中文 SystemExit/日志对任何捕获方都是确定编码；配对消费方显式 utf-8 解码。
    force_utf8_stdio()
    # I1 快速缓解（hy E4/dsf）：fatal 信号（SIGSEGV/SIGABRT/SIGFPE…）时把 Python 栈
    # 倾倒到 stderr——随 §16.2 的 run.log 落文件，两起「无堆栈消失」事故不再完全盲区。
    # OOM killer（SIGKILL）不经过信号处理器——那种死法由 rl/forensics.py 的提交边界
    # 快照兜底取证（最后一条 forensics = 临终状态）。
    import faulthandler

    faulthandler.enable()
    # Anchor cwd to the repo root (parent of nn-training/): all default paths
    # (tmp/student-weights-dagger, tmp/rl-weights, tmp/rl-traj) are repo-root
    # relative. Required for the unified launcher's --detach (dashboard/src/launch/cli.ts), whose
    # is nn-training/ — same pattern as train_loop.py's REPO_ROOT.
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # ===== RL 入口整合（DECISIONS §307）：两阶段 argparse，先预解析 --mode/--goal =====
    mode = resolve_mode(sys.argv[1:])
    # 启动参数默认取自 rl-config.json（单一事实来源；CLI 显式传参覆盖 json 默认）。
    # 查找优先级 rl.<mode> → intent_rl 遗留块（intent/goal 迁移期）→ rl（D2）。
    try:
        _cfg = json.loads(
            (REPO_ROOT / "nn-training" / "rl-config.json").read_text(encoding="utf-8")
        )
    except Exception:
        _cfg = {}
    _rl_args, _rl_src = merged_mode_args(_cfg, mode)

    ap = build_argparser(mode, _rl_args)
    args = ap.parse_args()
    apply_mode_flags(args)
    # ===== 课程配置化（plan/rl-training-config.md §3）：唯一启动入口 =====
    # 优先级 课程 > rl-config.json > argparse 默认；无 CLI 逐参覆盖。课程自带
    # 关卡布局/奖励公式/超参 schedule，apply 后由各阶段消费。
    from rl.config import (
        apply_course,
        course_cli_conflicts,
        course_from_args,
        echo_config,
    )

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
    if getattr(args, "echo_config", False):
        echo_config(args, course)
        log("[run_rl] --echo-config done — exit")
        return
    # 课程名（与锁段共用一次推导；非法 stem 在此即响亮拒启）。
    #
    # ❌ 2026-09-18 删掉了“按课程回填 remote_hubs[<stem>]”那段：hub/隧道已收敛为**单**
    # 实例（一个进程服务所有并行课程、一条隧道指向它），URL 是全局事实 ⇒ 只认单键
    # `rl.remote_hub_url`（首参数 `--remote-hub-url` 仍可显式压过）。旧的 per-course 键如果
    # 还被读，就会把训练指向一个**已经不存在的每课隧道**（控制台写的是单键，两边不一致）。
    try:
        _hub_course_key = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except ValueError as e:
        raise SystemExit(f"[run_rl] {e}") from e
    # P1-3（2026-09-02）：启动期配置校验（互斥/范围 fail fast——此前这些错误
    # 要等训练中途才暴露）。课程覆盖后校验（课程值是单一事实来源）。
    # stream/double-buffer 的显式传参判定：单一 PPO 路径下两者恒置 0（本机没有 PPO 窗口，
    # 见 `rl/config.py` §3 块），但**显式** `--stream 1` / `--double-buffer 1` 仍要响亮报错
    # （用户要求的能力已退役 ≠ 参数可以静默失效）⇒ 需区分「显式传参」与「吃 config 默认」。
    # 存到 args 供 validate_args 消费。
    _defaults_ns2 = ap.parse_args([])
    args._explicit_stream = int(getattr(args, "stream", 0) or 0) != int(
        getattr(_defaults_ns2, "stream", 0) or 0
    )
    args._explicit_double_buffer = int(getattr(args, "double_buffer", 0) or 0) != int(
        getattr(_defaults_ns2, "double_buffer", 0) or 0
    )
    from rl.config import validate_args

    validate_args(args)

    # stdout/stderr 落盘（out_log/err_log；CLI 可覆盖调试；per-tick 默认空=仅控制台）。
    _setup_log_redirect(args)
    # 生效启动配置落地日志（trust-but-verify：核对 json 默认是否被正确读取）。
    _log_rl_args(_rl_src, _rl_args)

    # 启动前推送当前分支到 origin（远端 agent 靠 git pull 同步——§30 教训）。
    # 2026-08-30 事故修复（用户指令）：节点的远控升级分支**永远用训练机当前分支**，
    # 不再读 rl-config 的 upgradeBranch（残留旧战役分支名曾把全部节点 reset 回
    # 31 个提交前的 intent-ai）。config 键仅作 push 失败时的最后回退。
    # 并发 push 串行化（plan multi-course-parallel-training P1c F-C5）：.git 是双课
    # 共享的，双 trainer 同时 `git push` 会顶成 non-fast-forward/锁竞争。repo 级
    # O_EXCL 锁串行化（与单实例锁同一实现）；拿不到锁 → 响亮日志后跳过本次推送
    # （节点沿用远端已有分支继续——§30 同步靠"至少一课 push 成功"维持）。
    # 拒 --no-push 开关：静默不推比显式跳过更难排查。
    _push_lock = str(REPO_ROOT / ".git_push.lock")
    if acquire_lock(_push_lock, tag="git push"):
        try:
            ensure_current_branch_pushed(REPO_ROOT)  # side-effect: push current branch
        finally:
            cleanup_lock(_push_lock)
    else:
        log(
            "[run_rl] WARN: 另一进程正在 git push（.git_push.lock 被占）——跳过本次"
            "启动前推送，节点沿用远端已有分支；如远端长期无新提交请检查持锁进程"
        )
    _current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        **_POPEN_NO_WINDOW,
    ).stdout.strip()
    if _current_branch and _current_branch != "HEAD":
        import dist_common as _dc

        _dc.set_upgrade_branch(_current_branch)
        log(f"[run_rl] node upgrade branch locked to training-machine branch: {_current_branch}")

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl] bun not found on PATH — rollout needs it")

    # ===== 吞吐 T4：collect-only 分支必须在 build_model 之前（子进程无需 torch 模型/权重）=====
    _traj_root = Path(args.traj)
    _traj_root.mkdir(parents=True, exist_ok=True)
    _jpath = _traj_root / "training_log.jsonl"
    _prs = last_rotate_seed(_jpath)
    _rseed = _prs if _prs is not None else (args.seed * 1009 + 1 + int(time.time())) % (2**32)
    if getattr(args, "collect_only", 0):
        run_collect_only(args, _traj_root, _rseed, bun)
        log("[run_rl] collect-only done — exit")
        return
    # ===== 双缓冲：collect-only 分支结束 =====

    # ===== 开课闸（2026-09-21 事故，plan/accident.plan.md §1）=====
    # 位置：collect-only 之后（预采子进程不受影响）、单实例锁之前（不占锁就响亮退出）。
    _require_course_open(args, _traj_root)

    # ===== 单实例锁（2026-09-06 事故：--kill-previous 漏杀 → 双 trainer 并行写同一
    # traj 7 分钟，it57-59 各被两遍训练/落账）===== 仅训练主循环持锁；collect-only
    # 预采子进程（spawn_collect_next 的子代）不参与竞争。双开 = 响亮拒启。
    # 锁按课程实例化（plan multi-course-parallel-training §3.1）：双课各自持锁并行，
    # 同课双开仍响亮拒启（2026-09-06 双 trainer 并行写同一 traj 事故的护栏不删，
    # 只是文件按课程命名）；无 --course 的老调用沿用旧全局锁名（默认行为零变化）。
    # 命名空间键 = 课程文件 stem（`--course s-dodge` → `s-dodge`）：与控制台课程选择/
    # tmp/<course>/out 路径/TS launcher 的 peekCourse 同一键。课程文件内部的 `name`
    #（如 s-dodge-mix）只是归属标注（S9 attribution），不进命名空间——否则两边锁
    # 文件名对不上，preflight 与 kill 全部错位。键在 main 前部已推导（_hub_course_key，
    # 与 remote_hubs 回填共用一次推导）。
    try:
        lock_path = course_lock_path(
            str(Path(__file__).resolve().parent), _hub_course_key, "run_rl"
        )
    except ValueError as e:
        raise SystemExit(f"[run_rl] {e}") from e
    if getattr(args, "export_bundle", ""):
        # ★ 2026-09-22（§course-error-isolation-loud）：`--export-bundle` = **只读快照**
        # （打 zip：manifest/计划/代码/当前权重；不推进账本、不落新权重——见
        # `loop_steps._remote_ppo` 的 `export_path` 分支）。因此**不占 per-course 单实例锁**，
        # 允许「共享 trainer 正在服务该课时随时导出」——锁是「同课两个写者」的护栏，
        # 导出不是写者。与训练并行时以盘上当前权重/账本为快照（权重/账本落盘都是原子写）。
        log(
            f"[run_rl] --export-bundle：只读快照导出，不取 per-course 锁——若共享 trainer "
            f"正服务本课（{_hub_course_key}），以此为并行快照；导出的包是导出那一刻的进度"
        )
    elif not _acquire_run_rl_lock(lock_path, force=bool(getattr(args, "force", False))):
        raise SystemExit(
            f"[run_rl] another run_rl is running for this course "
            f"(holder pid in {lock_path}) — refusing to start; "
            "kill the holder or pass --force to take over"
        )
    else:
        atexit.register(_cleanup_run_rl_lock, lock_path)

    # ===== 启动即清空 hub 停机态（2026-09-12 it17 复盘）：上轮门判/人工停机残留的
    # halt 若带进新 run，首轮 PPO job 直接进无人区（训练机空等 30min 超时）。
    # 只读+幂等 resume，读回确认；失败只告警不阻断（local/push 无 hub 直接短路）。
    # 注意放锁后：只有真正持锁训练的进程才清，预采子进程/旁观者不动 hub。
    from remote.hub_client import clear_halt_on_startup

    clear_halt_on_startup(
        str(getattr(args, "remote_hub_url", "") or ""),
        str(getattr(args, "remote_token", "") or ""),
        log=log,
        # 共享 hub（2026-09-18）：只清**本课**的停机态（清全课程会把并行的其它课一起解停）
        course=_hub_course_key,
    )

    # ===== 主循环（rl/loop.py::run_training）=====
    from rl.loop import run_training

    # ★ 2026-09-21（§3 单一 PPO 路径）：训练进程不建本机 PPO 后端（hub 免 torch，D2）——
    # 传 None；PPO 由 hub 队列上认领到的 worker 执行（本机 worker = 控制台起的同一协议）。
    run_training(args, None, bun, update_kwargs)


if __name__ == "__main__":
    main()
