"""run_bc.py — BC 训练编排器的**入口薄壳**（引擎在 `rl/bc_loop.py`；plan §3.4 / R3-4）。

把 BC（行为克隆）从本地手工流程升级为与 PPO 同构的分布式管线：
  每轮 it：
    1. 账本已有 `bc_round_completed(it)` → 跳过（断点续跑）；
    2. 语料缺额 → rl/bc_dispatch.py 派 LAN 节点采 God-AI 语料（mode=bc）；
    3. publish_job(kind=bc) → 云端 worker 跑 train/bc.py → 回传 BC 权重；
    4. verify_and_land_bc → `tmp/<course>/weights.json`；
    5. 归档 backup_weights → `nn-training/weights/<prefix>/…it<N>.<ts>.json` + WEIGHTS.md 行。

三种传输（--course 之外按环境自动选）：
  push   ：env REMOTE_PUSH_NODE（冒烟预演的本机伪 GPU 节点）→ push_client 直推
           worker_server（真实的 push 执行面走 hub 中介派发：课程与节点正交）；
  hub    ：--remote → 发布到共享 hub（云机 `remote_worker --poll` 领取）；
  local  ：--local → 语料就绪后本机子进程 train/bc.py（训练机 venv torch）。

--smoke：语料 1 局/max_ticks≤300/epochs=1 的真一轮（伪 GPU 节点可达）→ 落位即作废
（不覆盖 out、不归档），打 `BC SMOKE PASS`——控制台 smokeTrain 的三里程碑之一。

**本模块只做进程级一次性副作用 + 阻塞式驱动**（`force_utf8_stdio` / `chdir` / 单实例锁 /
启动前 `git push` / hub 停机态清零）——「一轮怎么跑」全部在 `rl/bc_loop.py`，因为单进程
supervisor（`run_rl_cluster.py --serve`）也要用**同一份**一轮实现（两套实现必然分叉：
BC 的续跑判据是 `bc_round_completed` / job 认领 / bc-resume，任何一处写成第二份就是重发布
= resume 失效 = 从头训）。

本模块 torch-free（local 模式经子进程消费 torch；云端训练在 worker 内）。
"""

from __future__ import annotations

import os
from pathlib import Path

from common.proc import run_capture
from platform_utils import force_utf8_stdio
from rl.archive import ensure_current_branch_pushed
from rl.bc_loop import BcLoop, bc_argparser, resolve_bc_runtime
from rl.log import log
from rl.queue import REPO_ROOT
from train.loop_util import acquire_lock, cleanup_lock, course_lock_path

#: nn-training 目录（锁文件与子进程 cwd 都相对它——与 `rl/loop_serve.py` 的 `NN_DIR` 同一个）。
NN_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    """进程级准备 → 解析 → **阻塞式**驱动（单课进程只服务这一门课，等外部时原地重问）。

    与改造前 `main()` 的顺序逐条同义，只有一处刻意调整：**课程/传输解析先于单实例锁**
    （配置错就不该占锁，也不该白推一次 `git push`；锁只保护「真的开始跑」之后的互斥）。
    """
    force_utf8_stdio()
    os.chdir(str(REPO_ROOT))
    args = bc_argparser().parse_args(argv)

    # ---- git push 串行化（节点代码同步依赖 origin；与 run_rl / serve 同规）----
    push_lock = str(REPO_ROOT / ".git_push.lock")
    if acquire_lock(push_lock, tag="git push"):
        try:
            ensure_current_branch_pushed(REPO_ROOT)
        finally:
            cleanup_lock(push_lock)
    import dist_common as dc

    current_branch = run_capture(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, timeout=30
    ).stdout.strip()
    # 升级分支必须在 load_dist_config 之前钉住（节点远端分支的选择依赖它）。
    if current_branch and current_branch != "HEAD":
        dc.set_upgrade_branch(current_branch)
    runtime = resolve_bc_runtime(args, cfg=dc.load_dist_config())

    # ---- 单实例锁（与 run_rl 同实现、按课命名；双开响亮拒启）----
    from run_rl import _acquire_run_rl_lock, _cleanup_run_rl_lock

    lock_path = course_lock_path(str(NN_ROOT), runtime.course_key, "run_bc")
    if not _acquire_run_rl_lock(lock_path):
        raise SystemExit(
            f"[run_bc] another run_bc is running for this course "
            f"(holder pid in {lock_path}) — refusing to start"
        )
    log(
        f"[run_bc] course={runtime.course.name} key={runtime.course_key} "
        f"iters={runtime.iters} traj={runtime.traj} out={runtime.out_weights} "
        f"corpus_fp={runtime.corpus_fp[:12]}… smoke={runtime.smoke} local={args.local}"
    )

    if runtime.transport == "hub":
        # 启动即清 hub 停机态（2026-09-12 it17 复盘同款；失败不阻断）
        from remote.hub_client import clear_halt_on_startup

        clear_halt_on_startup(runtime.hub_url, runtime.token, log=log, course=runtime.course_key)
    log(
        f"[run_bc] transport={runtime.transport} "
        f"push_url={runtime.push_url or '-'} hub_url={runtime.hub_url or '-'}"
    )
    try:
        BcLoop(runtime).run_blocking()
    finally:
        _cleanup_run_rl_lock(lock_path)


if __name__ == "__main__":
    main()
