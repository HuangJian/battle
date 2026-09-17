"""remote/_instance_lock.py — 启动守卫（第二道闸）：原子 PID 单实例锁。

为什么需要它（2026-09-17）：`_port_guard.ensure_port_free` 是「探测 → bind」的 TOCTOU
守卫——两个 starter **同时**探测可以双双通过；而 Windows 的 `SO_REUSEADDR` 允许两个
socket 同时 bind 同一端口（后绑定者不报错，静默变成「永远收不到连接的僵尸」，2026-09-09
的 8787 双实例事故就是这样）。端口守卫挡得住「已经有活监听者」的常见形态，挡不住
「同时启动」。本锁用 `O_CREAT|O_EXCL` 的原子性把启动**串行化**：

    拿锁（本模块）→ 端口探测（_port_guard）→ bind

三道任一道拦下都是**响亮拒启**，而不是静默共存。控制台的 `reclaimPort` 在启动前清掉
不可用的幸存者（2026-09-17），与本锁互补：它管「旧的不去」，本锁管「新的齐来」。

锁文件格式 `PID|EXE|START_TS`（与 `run_rl` / `train_loop` 的 PID 单实例锁同形），
兼容裸 PID 旧文件。

**陈旧锁接管规则**（判据只有两条，宁可 fail-closed）：

  * 持有者已死 → 陈旧锁，接管（响亮打印）；
  * 持有者活着但命令行**不含 marker** → PID 已被系统复用给别的程序，接管（响亮打印）；
  * 持有者活着、命令行读不到（无 `/proc`、`wmic` 不可用）→ **拒启**并打印持有者 pid
    （fail-closed：宁可让操作员确认后删锁，也不静默双监听）。

**为什么不复用 `train/loop_util.py` 的 `acquire_lock`**（曾评估，此处否决）：它的
`_pid_alive` 在 Windows 上走 `os.kill(pid, 0)` —— 这在 Windows 是
`TerminateProcess(handle, 0)`，**会把锁持有者直接杀掉**（`run_rl.py::_runrl_pid_alive`
的注释同样记录了这条不复用理由）。另外 `remote/` 是要打包进 code.zip 的独立包，反向
依赖 `train/` 会把 hub 的启动链拖进训练侧依赖。故本模块自带安全探测（Windows 走
`GetExitCodeProcess == STILL_ACTIVE`）。
"""

from __future__ import annotations

import os
import sys
import time

__all__ = [
    "acquire_instance_lock",
    "default_hub_lock_path",
    "proc_cmdline",
    "read_lock",
    "release_instance_lock",
]

#: 命令行打印长度上限（死锁排障只需认得「这是哪个程序」，不需要整条 argv）。
_CMD_CLIP = 160


def _pid_alive(pid: int) -> bool:
    """跨平台进程存活探测。

    Windows 走 `GetExitCodeProcess == STILL_ACTIVE`；POSIX 用 `signal 0`（只探测存在性）。
    任何异常一律按「不存活」处理——陈旧锁总能被清理，绝不因为探测本身失败把操作员锁死。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def proc_cmdline(pid: int) -> str | None:
    """进程命令行（身份核验用）；读不到 → None（调用方必须按「身份未知」fail-closed）。

    POSIX 读 `/proc/<pid>/cmdline`（macOS 无 `/proc` → None）；Windows 调
    `wmic process where processid=<pid> get CommandLine /value`（OS 内置组件而非 shell
    语法，与 `dashboard/src/launch/cli.ts::listPythonProcesses` 同款；不可用/超时 → None）。
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        import subprocess

        try:
            r = subprocess.run(
                [
                    "wmic",
                    "process",
                    "where",
                    f"processid={pid}",
                    "get",
                    "CommandLine",
                    "/value",
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("CommandLine="):
                return line[len("CommandLine=") :].strip() or None
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return raw.decode("utf-8", "replace").replace("\0", " ").strip() or None


def read_lock(lock_path: str) -> tuple[int | None, str | None, int | None]:
    """锁文件 → `(pid, exe, start_ts)`；残缺/不可读 → `(None, None, None)`。"""
    try:
        with open(lock_path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None, None, None
    parts = raw.split("|")
    if len(parts) >= 3:
        try:
            return int(parts[0]), parts[1], int(parts[2])
        except ValueError:
            return None, None, None
    try:  # 兼容裸 PID 旧文件
        return int(raw), None, None
    except ValueError:
        return None, None, None


def _create_exclusive(lock_path: str) -> int | None:
    """`O_CREAT|O_EXCL` 原子创建（原子性正是本锁的全部价值）；已存在 → None。"""
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None


def acquire_instance_lock(lock_path: str, *, marker: str, tag: str = "instance-lock") -> bool:
    """取锁：True = 本进程持有（锁文件已写入）；False = 已有活实例（已响亮打印，调用方应退出）。

    *marker* 是持有者身份指纹（如 `"hub_server"`）：只在持有者**活着**时用来区分
    「同一程序的第二个实例」（拒启）与「PID 被系统复用给了别的程序」（接管陈旧锁）。
    """
    fd = _create_exclusive(lock_path)
    if fd is None:  # 已存在锁文件：判定「真双开」还是「陈旧锁/ PID 复用」
        holder, holder_exe, holder_ts = read_lock(lock_path)
        if holder is not None and _pid_alive(holder):
            cmd = proc_cmdline(holder)
            if cmd is None or marker in cmd:
                why = (
                    "命令行不可读，身份未知（fail-closed）"
                    if cmd is None
                    else "命令行含同一程序指纹"
                )
                print(
                    f"[{tag}] ERROR: 已有实例在运行（PID {holder}，{why}）——拒绝启动。"
                    f"锁文件 {lock_path}；确认该进程已死后删除锁文件并重试。",
                    flush=True,
                )
                return False
            print(
                f"[{tag}] 锁持有人 PID {holder} 不是本程序（命令行: {cmd[:_CMD_CLIP]}）"
                f"——判为 PID 复用，接管陈旧锁 {lock_path}（原 start_ts={holder_ts}）",
                flush=True,
            )
        else:
            note = f"PID {holder}" if holder is not None else "已残缺不可解析"
            print(
                f"[{tag}] 陈旧锁（持有人 {note} 已不存在）——接管 {lock_path}"
                + (f"（原 exe={holder_exe}）" if holder_exe else ""),
                flush=True,
            )
        try:
            os.remove(lock_path)
        except OSError:
            pass
        fd = _create_exclusive(lock_path)
        if fd is None:
            print(
                f"[{tag}] ERROR: 锁 {lock_path} 被并发启动者抢占——拒绝启动（再试一次即可）",
                flush=True,
            )
            return False
    try:
        os.write(fd, f"{os.getpid()}|{sys.executable}|{int(time.time())}".encode())
    finally:
        os.close(fd)
    return True


def release_instance_lock(lock_path: str) -> None:
    """释放**自己持有**的锁（锁已易主则绝不动别人的；best-effort）。"""
    holder, _exe, _ts = read_lock(lock_path)
    if holder != os.getpid():
        return
    try:
        os.remove(lock_path)
    except OSError:
        pass


def default_hub_lock_path(port: int) -> str:
    """hub-server 默认锁路径：`nn-training/.hub_server.<port>.lock`。

    按**端口**键控（不是按课程或 job 目录）：不变量是「一个端口只允许一个 hub 实例」，
    跨课程槽位配错同样必须被挡住。与 trainer 的 `.run_rl.<课程>.lock` 同目录同风格。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, f".hub_server.{port}.lock")
