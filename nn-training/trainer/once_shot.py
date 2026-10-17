"""once_shot.py — 一次性腿的**唯一实现**：冒烟预演 / 导出快照 / 预采子进程（plan/single-resident-trainer §3，S3）。

## 为什么要有这个模块

S1 起常驻写者只剩一个：`trainer/run_rl_cluster.py --serve`（单进程服务所有课程）。而
`trainer/run_rl.py` 当时仍背四种角色（常驻训练 / 冒烟 / 导出 / 预采）——「一个文件背四种角色」
正是锁被滥用的温床。S3 把**不写账本、不推进指针**的三条腿搬到这里，入口退回「解析 → 分流」：

    trainer/run_rl_cluster.py --serve    常驻调度器（唯一常驻写者）
    trainer/run_rl.py                    CLI 壳：解析 + 分流（echo / 本模块 / 拒启）
    trainer/once_shot.py                 冒烟 / 导出 / 预采（本模块）
    trainer/collect_only.py              预采子进程的执行体（入口仍是 `run_rl.py`，见下）

## 三条腿各自的契约（与搬运前逐条同义；顺手改任何一条都是行为变更）

| 腿 | 取 `.run_rl.<课>.lock`？ | 过开课闸？ | 写什么 |
|---|---|---|---|
| `--smoke` | **取**（§3.1 选项 A：冒烟是**真跑一轮** collect → publish → echo → 作废，落位前作废点全在收官/权重层，**没有**「零写 traj」的判据 ⇒ 与 serve 抢同一批 traj 的面还在） | 不过（预演就在开课前跑） | tmp/ 下的临时物 |
| `--export-bundle` | **不取**（只读快照：不推进账本、不落新权重 ⇒ 与 serve 并行是设计目标；与「随时导出」同一口径） | **过**（停课的课程不许导出——这条行为由本闸守着，见 §3.3） | 一个包 |
| `--collect-only` | 不取（预采子进程不参与竞争） | 不过（它在开课前采） | `it{N}/` shard |

## 边界

- **`--echo-config` 不在这里**：它是**解析面**的 dump（对拍 oracle 读 stdout），留在壳里。
- **常驻训练不在这里**：唯一入口是 `run_rl_cluster.py --serve`；壳对「没有任何一次性动作」的
  调用**拒启**（`worker/trainer_gate.py` 的拒启消息）。本模块里没有任何常驻循环。
- **预采子进程的入口仍是 `run_rl.py`**（`collect_only.RUN_RL_ENTRY` 钉住它）：子进程重新解析
  课程命名空间，再分流回本模块的 collect 腿——壳是唯一的解析面，不另立第二份 argparse。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from common.log import log
from common.proc import run_capture
from trainer.dispatch_queue import REPO_ROOT
from worker.archive import ensure_current_branch_pushed
from worker.train.loop_util import (
    acquire_lock,
    cleanup_lock,
    course_lock_path,
)


def require_course_open(args, traj_root: Path) -> None:
    """开课闸（纯逻辑，可单测）：只认控制台写的「开课」标记。

    ★ 2026-09-21（plan/accident.plan.md §1）：CLI 直启会绕过控制台开课流程（漏开课标记 /
    暂停意图解禁 / hub 课程模式三件套），并让 agent 去手写控制台拥有的状态文件
    （`training-enabled.txt` / `loop-control.json`）与控制台写面打架。

    判据**同源调用** `trainer.loop_plan.course_enabled`（= `enabled_courses` 的谓词；标记与
    traj 目录同址 `<traj>/training-enabled.txt`）——**禁**在这里另写第二份"标记存在性
    检查"（第二份判据 drift 就是这类事故的老 pattern）。

    ⚠ 落点：`--traj` 是**本课**的 traj 目录（run_rl 直接在那下面写 training_log.jsonl），
    所以用谓词 `course_enabled(traj)`；**不得** `enabled_courses(traj)`（那会去扫子课程，
    判据错位）。

    范围（S3 后：只剩导出腿还过它）：冒烟预演（在开课前跑）、预采子进程（调用点更早）、
    无课程（legacy 非课程路径）都不受影响。**导出腿保留本判据**——「停课的课程不能导出」
    这条行为此前由常驻分支顺带守着，迁移时它是唯一不能丢的那一条（§3.3）。
    """
    if getattr(args, "smoke", False):
        return
    if getattr(args, "course_obj", None) is None:
        return  # 非课程路径：老用法逐字节不变（开课标记只存在于课程 traj 目录下）
    from common.protocol import COURSE_ENABLE_MARKER
    from trainer.loop_plan import course_enabled

    if course_enabled(traj_root):
        return
    name = str(getattr(args, "course_name", "") or "?")
    raise SystemExit(
        f"[run_rl] 课程 {name!r} 未开课：{Path(traj_root) / COURSE_ENABLE_MARKER} 不存在 ——\n"
        "  训练只从控制台开课（dashboard → 课程 → 开课），不接受 CLI 直启：\n"
        "  直启会漏掉开课标记 / 暂停意图解禁 / hub 课程模式三件套。"
    )


def takes_course_lock(args) -> bool:
    """这条腿取 `.run_rl.<课>.lock` 吗？（纯判据，可单测；契约表见模块头）

    ★ 2026-10-17（S3，plan §3.1 选项 A）：**只有导出腿不取锁**（只读快照，与 serve 并行是
    设计目标）；**冒烟保留取锁** —— 它是真跑一轮（collect → publish → echo → 作废），
    作废点全在收官/权重层，**没有**「零写 traj」的判据 ⇒ 与 serve 抢同一批 traj 的面还在
    （2026-09-06 双 trainer 写同一 traj 的护栏同口径）。预采子进程在更早的分支返回，不问本判据。
    """
    return not bool(str(getattr(args, "export_bundle", "") or ""))


def _require_bun() -> str:
    """冒烟/预采都要 bun（rollout 跑在它上面）；缺了当场响亮拒启。"""
    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("[run_rl] bun not found on PATH — rollout needs it")
    return bun


def _ensure_cluster_snapshot(args) -> None:
    """集群代码快照（2026-10-10 用户指令，plan/cluster-code-snapshot）。

    锚点口径（S3 后）：`--export-bundle` 仍是「会话」的候选锚点 ⇒ 复用集群快照；其余一次性腿
    是短命进程，不拿它们当锚（启动 `run_rl_cluster.py` / serve 才是锚的正主）。
    警告不致命（同 hub）：publish 有 per-course 兜底，起不来比一份旧代码严重得多。
    """
    try:
        from remote.code_snapshot import ensure_cluster_snapshot

        ensure_cluster_snapshot(
            anchor_kind="export" if getattr(args, "export_bundle", "") else "oneshot", log=log
        )
    except OSError as e:
        log(f"[run_rl] WARN: 集群代码快照未能建立（{e}）——publish 将回落 per-course 打包")


def _push_current_branch() -> None:
    """启动前推送当前分支到 origin（远端 agent 靠 git pull 同步——§30 教训）。

    2026-08-30 事故修复（用户指令）：节点的远控升级分支**永远用训练机当前分支**，不再读
    rl-config 的 upgradeBranch（残留旧战役分支名曾把全部节点 reset 回 31 个提交前的 intent-ai）。
    config 键仅作 push 失败时的最后回退。

    并发 push 串行化（plan multi-course-parallel-training P1c F-C5）：`.git` 是双课共享的，
    双 trainer 同时 `git push` 会顶成 non-fast-forward/锁竞争。repo 级 O_EXCL 锁串行化
    （与单实例锁同一实现）；拿不到锁 → 响亮日志后跳过本次推送（节点沿用远端已有分支继续）。
    拒 `--no-push` 开关：静默不推比显式跳过更难排查。
    """
    push_lock = str(REPO_ROOT / ".git_push.lock")
    if acquire_lock(push_lock, tag="git push"):
        try:
            ensure_current_branch_pushed(REPO_ROOT)  # side-effect: push current branch
        finally:
            cleanup_lock(push_lock)
    else:
        log(
            "[run_rl] WARN: 另一进程正在 git push（.git_push.lock 被占）——跳过本次"
            "启动前推送，节点沿用远端已有分支；如远端长期无新提交请检查持锁进程"
        )
    current_branch = run_capture(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, timeout=30
    ).stdout.strip()
    if current_branch and current_branch != "HEAD":
        import common.distribution as dc

        dc.set_upgrade_branch(current_branch)
        log(f"[run_rl] node upgrade branch locked to training-machine branch: {current_branch}")


def _run_collect_only(args, bun: str) -> None:
    """预采腿（T4 双缓冲的子进程侧）：只采一轮落盘，随后退出。

    `run_collect_only` 住 `trainer/collect_only.py`（与入口 `RUN_RL_ENTRY` 同址）；这里只做
    「它要的那几个入参」的推导 —— 轮换 seed 的定义与主循环同源（`last_rotate_seed`）。
    """
    from trainer.collect_only import run_collect_only
    from worker.resume import last_rotate_seed

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    jpath = traj_root / "training_log.jsonl"
    prs = last_rotate_seed(jpath)
    rseed = prs if prs is not None else (args.seed * 1009 + 1 + int(time.time())) % (2**32)
    run_collect_only(args, traj_root, rseed, bun)
    log("[run_rl] collect-only done — exit")


def run_once_shot(args, *, course_key: str) -> None:
    """执行一次性腿（调用方 = `run_rl.py` 的壳：参数已解析、课程已施加、闸已放行）。

    `course_key` 由壳推导（课程文件 stem），用于锁名与清理 —— 与 serve/控制台的键同源。
    顺序与搬运前逐条同义（快照 → 推送当前分支 → bun → 各腿）：预采子进程同样会推一次
    （`set_upgrade_branch` 是**子进程内**的生效值，节点同步靠它——不是可省的副作用）。
    """
    _ensure_cluster_snapshot(args)
    _push_current_branch()
    bun = _require_bun()
    if getattr(args, "collect_only", 0):
        _run_collect_only(args, bun)
        return

    traj_root = Path(args.traj)
    traj_root.mkdir(parents=True, exist_ok=True)
    # 开课闸（导出腿）：停课的课程不许导出——§3.3 的迁移判据，调用点必须在 export 分支之前。
    require_course_open(args, traj_root)

    # ===== 单实例锁（2026-09-06 事故：--kill-previous 漏杀 → 双 trainer 并行写同一 traj
    # 7 分钟，it57-59 各被两遍训练/落账）=====
    # 锁按课程实例化（plan multi-course-parallel-training §3.1）；无 --course 的老调用沿用旧
    # 全局锁名（默认行为零变化）。命名空间键 = 课程文件 stem（与控制台课程选择 / tmp/<course>/out
    # 路径 / TS launcher 的 peekCourse 同一键）。
    try:
        lock_path = course_lock_path(str(Path(__file__).resolve().parents[1]), course_key, "run_rl")
    except ValueError as e:
        raise SystemExit(f"[run_rl] {e}") from e
    from trainer.run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    if not takes_course_lock(args):
        # ★ 2026-09-22（§course-error-isolation-loud）：`--export-bundle` = **只读快照**
        # （打 zip：manifest/计划/代码/当前权重；不推进账本、不落新权重——见
        # `loop_steps._remote_ppo` 的 `export_path` 分支）。因此**不占 per-course 单实例锁**，
        # 允许「共享 trainer 正在服务该课时随时导出」——锁是「同课两个写者」的护栏，
        # 导出不是写者。与训练并行时以盘上当前权重/账本为快照（权重/账本落盘都是原子写）。
        log(
            f"[run_rl] --export-bundle：只读快照导出，不取 per-course 锁——若共享 trainer "
            f"正服务本课（{course_key}），以此为并行快照；导出的包是导出那一刻的进度"
        )
    elif not _acquire_run_rl_lock(lock_path, force=bool(getattr(args, "force", False))):
        raise SystemExit(
            f"[run_rl] another run_rl is running for this course "
            f"(holder pid in {lock_path}) — refusing to start; "
            "kill the holder or pass --force to take over"
        )
    else:
        import atexit

        atexit.register(_cleanup_run_rl_lock, lock_path)

    # ===== 启动即清空 hub 停机态（2026-09-12 it17 复盘）：上轮门判/人工停机残留的
    # halt 若带进新 run，首轮 PPO job 直接进无人区（训练机空等 30min 超时）。
    # 只读+幂等 resume，读回确认；失败只告警不阻断（local/push 无 hub 直接短路）。
    # 注意放锁后：只有真正持锁的进程才清，预采子进程/旁观者不动 hub。
    from remote.hub_client import clear_halt_on_startup

    clear_halt_on_startup(
        str(getattr(args, "remote_hub_url", "") or ""),
        str(getattr(args, "remote_token", "") or ""),
        log=log,
        # 共享 hub（2026-09-18）：只清**本课**的停机态（清全课程会把并行的其它课一起解停）
        course=course_key,
    )

    # ===== 真跑一轮（trainer/loop.py::run_training）=====
    # ★ 2026-10-15（J3，plan/offline-open-ended-iters-and-loud-export §3.3）：导出腿的**成败**
    #   在**进程退出层**落一个权威点（`tmp/<课>/export-status.json`）：控制台起的是
    #   detached 子进程（拿不到退出码），且控制台重启后它仍在跑 ⇒ 控制台的内存状态不可靠，
    #   落盘是唯一跨重启的锚。非导出腿一字不写；**成功也写**。
    #   包住 `run_training` 才盖得住三条真实路径：启动期 `export_refusal`（SystemExit）·
    #   轮内 `build_plan` 的 `ProtocolError`（走 `round_failure` 同轮重试 5 次后退）· 成功。
    #   （更早的启动期拒因——课程未开课 / `--course` 与 CLI 冲突——在它之前，不进这个文件。）
    from remote.bundle import export_status_guard
    from trainer.loop import run_training
    from trainer.run_rl import update_kwargs

    # ★ 2026-09-21（§3 单一 PPO 路径）：训练进程不建本机 PPO 后端（hub 免 torch，D2）——
    # 传 None；PPO 由 hub 队列上认领到的 worker 执行（本机 worker = 控制台起的同一协议）。
    with export_status_guard(args):
        run_training(args, None, bun, update_kwargs)


__all__ = ["require_course_open", "run_once_shot", "takes_course_lock"]
