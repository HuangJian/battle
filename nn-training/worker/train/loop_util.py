"""loop_util —— 课程实例锁与课程键（唯一归宿；与 TS `slots.ts::lockName` 同构）。

纯 stdlib 工具（无 torch 依赖，可在任何 import 顺序下加载）。今天仍在用的是这两组：

  * **课程锁**：`course_lock_path` / `course_key_from_path` / `validate_course_name` 定名，
    `acquire_lock` / `cleanup_lock` 是 `common.instance_lock` 的薄壳（`trainer/run_bc` ·
    `trainer/loop_serve` · `trainer/once_shot` · `trainer/batch_store` 共用）；
  * **存活探测薄壳** `_pid_alive`：委托唯一实现 `common.pid_probe.pid_alive`（Windows 上裸
    `os.kill` 会**杀掉**锁持有者，见 `docs/nn/remote-transport.md`）。

★ 2026-11-14（`python.refactor.md` §P1-11 死代码清理）：本文件曾是「本地 BC 连续训练入口」的
循环工具集——状态行 `_emit` / epoch 进度 Tee `_TrainTee` / 心跳线程 `heartbeat` /
`auto_export_corpus` / `parse_val_loss_from_output`。那个入口随
`plan/single-resident-trainer` §1.4 于 2026-10-17 退役，这些符号此后**零调用**
（全仓 grep，`__pycache__` 与历史文档叙述之外无引用），本轮删除。它们还带走了 5 处
`except Exception: pass`——日志文件写失败被静默成「本来就没事」，正是 §P0-7 与 G9 门禁要清的那一类。
"""

from __future__ import annotations

import os
import re

from common.instance_lock import acquire_instance_lock, release_instance_lock
from common.pid_probe import pid_alive

# ────────────────────────── per-course 锁路径（plan multi-course-parallel-training §1.2/§3.1） ──
# 2026-09-12：单实例护栏本身不能删（2026-09-06 双 trainer 并行写同一 traj 的事故），
# 但必须按**课程**实例化——否则第二门课程永远启不来。本文件是课程名 → 锁文件名的
# 唯一归宿；TS 侧同构实现在 dashboard/src/core/slots.ts::lockName（两处必须同步）。

_COURSE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_course_name(course: str) -> str:
    """课程名合法性（运行时命名空间 + 锁文件名的安全前提）。空串 = 无课程（合法）。

    字符集与 `api.sanitizeViewCourse` 同约束，另禁 `..`——课程名会拼进文件名，
    越界字符/上跳会直接把锁写到目录外（plan §1.1）。
    """
    c = str(course or "")
    if not c:
        return ""
    if ".." in c or not _COURSE_NAME_RE.match(c):
        raise ValueError(f"课程名非法: {c!r}——只允许 [A-Za-z0-9._-] 且不含 '..'（plan §1.1）")
    return c


def course_lock_path(base_dir: str, course: str, kind: str) -> str:
    """课程 → 锁文件路径：``.<kind>.<course>.lock``（如 ``.run_rl.s-dodge.lock``）。

    course 为空时沿用旧全局文件名（``.run_rl.lock``）——无 ``--course`` 的老调用
    默认行为零变化（plan §0.5-4）。
    """
    c = validate_course_name(course)
    name = f".{kind}.{c}.lock" if c else f".{kind}.lock"
    return os.path.join(base_dir, name)


def course_key_from_path(course_path: str) -> str:
    """课程文件路径 → 命名空间键（stem）：``curricula/s-dodge.jsonc`` → ``s-dodge``。

    命名空间键是锁/账本/槽位/日志目录的统一键（plan §1.1：课程 = ``curricula/<name>.jsonc``；
    与 TS 侧 ``slots.ts::slotOf/peekCourse`` 用的短名同一键）。课程文件**内部**的
    ``name``（如 s-dodge.jsonc 里写 ``s-dodge-mix``）只是归属标注，不进命名空间。
    空串 → ``""``（无课程，老调用）；非法 stem → ``ValueError``（响亮拒启）。
    """
    if not course_path:
        return ""
    stem = os.path.splitext(os.path.basename(course_path))[0]
    return validate_course_name(stem)


def _pid_alive(pid: int) -> bool:
    """跨平台存活探测薄壳：委托唯一实现 `common.pid_probe.pid_alive`。

    2026-09-17：本处曾是裸 `os.kill(pid, 0)`，而 Windows 上那不是探测而是
    `TerminateProcess(handle, 0)`——**查询动作会杀死锁持有者**（最坏：正在训练的 trainer）。
    三个坑（Windows TerminateProcess / `pid<=0` 的进程组语义 / 宽捕获）现在只记在
    `common.pid_probe`，并由 `tests/common/test_pid_probe_windows_safe.py` 用注入的假
    kernel32 直接回归。保留本函数名只为调用点与测试稳定（该守卫按名枚举四份薄壳）；
    **新增探测请直接用 `common.pid_probe.pid_alive`，不要再复制实现**。
    """
    return pid_alive(pid)


def acquire_lock(lock_path: str, *, force: bool = False, tag: str = "lock") -> bool:
    """PID 文件单实例锁（**薄壳**——实现唯一住在 `common/instance_lock`；锁文件格式仍是
    ``PID|EXE_PATH|START_TIMESTAMP``，裸 PID 旧文件仍兼容）。

    本函数只提供历史调用面（`tag` 只用于争用/接管那几句话）：

      * 持有者活着 ⇒ False（同课/同 kind 双开响亮拒启）；
      * 持有者已死 / 够旧的残缺锁 ⇒ 接管；
      * 锁文件**残缺且新鲜**（别人刚 `O_EXCL` 创建还没写 pid）⇒ False **且不动那个文件**
        （2026-10-11：这个毫秒级窗口曾让两个 starter 各自自认唯一，见 `common.instance_lock`
        的「写入窗口」一节）；
      * `force=True` ⇒ 显式接管（操作员 `--force`，不问身份直接删了重取）。

    为什么要收敛（2026-10-11 用户指令）：本文件、`trainer/run_rl.py`、`remote/code_snapshot.py`
    三处各自写了同一套 `os.open(O_CREAT|O_EXCL)` + `os.write`，**三个写入窗口**；修一处只修一处。
    现在三处都是这里的薄壳，窗口与接管规则只有一份。
    """
    return acquire_instance_lock(lock_path, marker=(), tag=tag, force=force)


def cleanup_lock(lock_path: str) -> None:
    """Remove our lock file on exit (best-effort, only if we own it)."""
    release_instance_lock(lock_path)
