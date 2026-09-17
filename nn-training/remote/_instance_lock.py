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

**锁文件写不下时 fail-open**（只读 FS / 权限不足）：守卫是**纵深防御的第二道闸**（第一道是
端口守卫），写不下锁不该变成启动拦路鬼 —— 响亮告警后本次退化为仅靠端口守卫；反之，「已有
活实例」的拒启仍是 fail-closed。

**陈旧锁接管规则**（判据只有两条，宁可 fail-closed）：

  * 持有者已死 → 陈旧锁，接管（响亮打印）；
  * 持有者活着但命令行**不含 marker** → PID 已被系统复用给别的程序，接管（响亮打印）；
  * 持有者活着、命令行读不到（无 `/proc`、命令行工具不可用）→ **拒启**并打印持有者 pid
    （fail-closed：宁可让操作员确认后删锁，也不静默双监听）。

**为什么不复用 `train/loop_util.py` 的 `acquire_lock`**（曾评估，此处否决）：它是
训练侧的流程级锁（面比实例锁大得多），反向依赖 `train/` 会把 hub 的启动链拖进训练侧依赖——
而 `remote/` 是要独立打进 code.zip 的包。**但存活探测本身现在只有一份**：
`nn-training/pid_probe.py`（stdlib-only 顶层模块，`remote/` 与 `train/` 都直接 import 它，
不经过对方的 `__init__`）。历史：此处曾自带一份，理由是当时 `train/loop_util._pid_alive`
在 Windows 上走 `os.kill(pid, 0)`＝`TerminateProcess`、**会把锁持有者直接杀掉**；2026-09-17
那份已改为委托同一实现，“三份同源”的漂移面随之归零。
"""

from __future__ import annotations

import os
import sys
import time

from pid_probe import pid_alive

__all__ = [
    "acquire_instance_lock",
    "default_instance_lock_path",
    "proc_cmdline",
    "read_lock",
    "release_instance_lock",
]

#: 命令行打印长度上限（死锁排障只需认得「这是哪个程序」，不需要整条 argv）。
_CMD_CLIP = 160


def _pid_alive(pid: int) -> bool:
    """跨平台进程存活探测（委托唯一实现 `pid_probe.pid_alive`）。

    本文件早期刻意**不**复用 `train/loop_util._pid_alive`（那一处当时是裸 `os.kill`，在
    Windows 上会杀死被探测进程）——保留本名字只为调用点稳定；实现与语义现在只有一份，
    见 `pid_probe` 模块 docstring（Windows `TerminateProcess` / `pid<=0` 进程组 / 宽捕获）。
    """
    return pid_alive(pid)


def _win_cmdline_wmic(pid: int) -> str | None:
    """Windows：`wmic ... get CommandLine /value`（OS 内置组件而非 shell 语法）。

    ⚠ **Win11 24H2 起 `wmic` 已被移除**（2026-09-17 本机实测：`which wmic` 找不到、
    `System32/wbem/WMIC.exe` 不存在）⇒ 本函数在新机上必然返回 None，真实现是
    `_win_cmdline_cim`。保留它是因为还有一批未打该补丁的机器/云机镜像只认 wmic。
    """
    import subprocess

    try:
        r = subprocess.run(
            ["wmic", "process", "where", f"processid={pid}", "get", "CommandLine", "/value"],
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


def _win_cmdline_cim(pid: int) -> str | None:
    """Windows：`Get-CimInstance Win32_Process` 读命令行（wmic 被移除后的唯一内置途径）。

    用 `pwsh`（PowerShell 7；**不**回落到 inbox 的 `powershell.exe` —— 仓库铁律
    §17.7，而这条路径只在「锁已存在且有活持有者」时才走，可接受 ~0.5s 的启动开销）。
    pwsh 不存在 / 超时 / 无输出 → None ⇒ 调用方按「身份未知」fail-closed（宁可让操作员
    确认后删锁，也不静默双监听）。

    与 `tools/sim/sim-pool.ts` 的 wmic→PowerShell 回落同一取舍（那边也是 Win11 被移除）。
    """
    import subprocess

    try:
        r = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip() or None


def proc_cmdline(pid: int) -> str | None:
    """进程命令行（身份核验用）；读不到 → None（调用方必须按「身份未知」fail-closed）。

    POSIX 读 `/proc/<pid>/cmdline`（macOS 无 `/proc` → None）；Windows 先用 wmic、
    被移除的新机上回落 PowerShell `Get-CimInstance`（见两个 `_win_cmdline_*` 的说明）。
    与 `dashboard/src/launch/cli.ts::listPythonProcesses` 同款（那边也走 wmic）。
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        return _win_cmdline_wmic(pid) or _win_cmdline_cim(pid)
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
    """`O_CREAT|O_EXCL` 原子创建（原子性正是本锁的全部价值）；已存在 → None。

    其它 `OSError`（只读 FS / 权限不足）**不在此吞**——由 `acquire_instance_lock` 判定
    是否 fail-open（与「已存在」是两回事，不能混为一谈）。
    """
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None


def acquire_instance_lock(
    lock_path: str, *, marker: str | tuple[str, ...], tag: str = "instance-lock"
) -> bool:
    """取锁：True = 本进程持有（锁文件已写入）；False = 已有活实例（已响亮打印，调用方应退出）。

    *marker* 是持有者身份指纹（如 `"hub_server"`）：只在持有者**活着**时用来区分
    「同一程序的第二个实例」（拒启）与「PID 被系统复用给了别的程序」（接管陈旧锁）。
    可给多个指纹（元组）——同一个服务常有多个合法入口（如 worker_server 既走
    `-m remote_worker_serve` 也可能被以模块名拉起），**任一命中即认作同一程序**。
    """
    markers = (marker,) if isinstance(marker, str) else tuple(marker)
    try:
        fd = _create_exclusive(lock_path)
    except OSError as e:
        # 只读 FS / 权限不足：写不下锁 ≠ 有实例在跑。守卫是纵深防御的第二道闸（第一道是
        # 端口守卫），此处**fail-open**并响亮告警——不能让「写不下锁」变成启动拦路鬼。
        print(
            f"[{tag}] WARN: 锁文件 {lock_path} 无法创建（{e}）——本次退化为仅靠端口守卫；"
            f"请检查目录权限（若两个实例同时启动，此环境双监听窗口未被堵塞）",
            flush=True,
        )
        return True
    if fd is None:  # 已存在锁文件：判定「真双开」还是「陈旧锁/ PID 复用」
        holder, holder_exe, holder_ts = read_lock(lock_path)
        if holder is not None and _pid_alive(holder):
            cmd = proc_cmdline(holder)
            if cmd is None or any(m in cmd for m in markers):
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
                f"[{tag}] 锁持有人 PID {holder} 不是本程序（命令行: {cmd[:_CMD_CLIP]}；"
                f"指纹 {list(markers)}）——判为 PID 复用，接管陈旧锁 {lock_path}"
                f"（原 start_ts={holder_ts}）",
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


def default_instance_lock_path(kind: str, port: int) -> str:
    """服务的默认锁路径：`nn-training/.<kind>.<port>.lock`（如 `.hub_server.8787.lock`、
    `.worker_server.8790.lock`）。

    按**端口**键控（不是按课程或 job 目录）：不变量是「一个端口只允许一个该服务实例」，
    跨课程槽位配错、不同入口重复拉起同样必须被挡住。与 trainer 的 `.run_rl.<课程>.lock`
    同目录同风格（同是「启动守卫」）。路径锚在包目录（`remote/` 的上一级 = `nn-training/`），
    与调用方的 cwd 无关——云端 code.zip 里它就是那份代码副本的目录。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, f".{kind}.{port}.lock")
