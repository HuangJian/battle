"""common/instance_lock.py — 启动守卫（第二道闸）：原子 PID 单实例锁。

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

**本模块是全仓唯一的「创建后写盘」锁实现**（2026-10-11 收敛）：hub/worker 启动守卫、
课程实例锁（`worker/train/loop_util`）、`trainer/run_rl` 的单实例锁、`remote/code_snapshot`
的抢锚锁全部走这里。四家各自写 `os.open(O_CREAT|O_EXCL)` + `os.write` 的版本曾**各自**带着
同一个写入窗口（见下），修一处只修一处，所以收敛到一个实现 = 四个窗口一起关。

**陈旧锁接管规则**（判据只有下列几条，宁可 fail-closed）：

  * 持有者已死 → 陈旧锁，接管（响亮打印）；
  * 持有者活着但命令行**不含 marker** → PID 已被系统复用给别的程序，接管（响亮打印）；
  * 锁文件**解析不出持有者**（空/残缺）且**新鲜**（`PARTIAL_LOCK_GRACE_SEC` 内）→ **拒启**，
    且（2026-10-11）**一个字节都不动**：它是 `O_CREAT|O_EXCL` 与随后写盘之间的窗口
    （见下「写入窗口」）；够久的残缺残留仍按第一条接管（崩溃残留不永久锁死启动）。
  * 持有者活着、命令行读不到（无 `/proc`、命令行工具不可用）→ **拒启**并打印持有者 pid
    （fail-closed：宁可让操作员确认后删锁，也不静默双监听）。

**写入窗口**（2026-10-11 事故）：`_create_exclusive` 的 `O_CREAT|O_EXCL` 与 `os.write` 是
两次系统调用，中间那个毫秒级窗口里文件是**空的**。满载下三个 starter 同时抢一个锁时，
后来的进程能完整穿过窗口；旧实现把「解析不出」一律读成「陈旧锁」⇒ **删掉先行者刚创建
的锁**再接管，两层后果：① 真持有者的锁文件被删/易主，「锁在 ∧ 端口答 200」的读数从此
撒谎（`tests/remote/test_worker_server_lock.py` 的 `_wait_ready` 等到超时 = 满载 flake）；
② 两个进程都以为自己是唯一实例。判据只能取「文件太新」（PID 都还没写上 ⇒ 没有身份可
核验），故**拒启**（fail-closed）而不是删别人可能正在写的锁。

**为什么不复用 `train/loop_util.py` 的 `acquire_lock`**（曾评估，此处否决）：它是
训练侧的流程级锁（面比实例锁大得多），反向依赖 `train/` 会把 hub 的启动链拖进训练侧依赖——
而 `remote/` 是要独立打进 code.zip 的包。**但存活探测本身现在只有一份**：
`nn-training/common/pid_probe.py`（stdlib-only 顶层模块，`remote/` 与 `train/` 都直接 import 它，
不经过对方的 `__init__`）。历史：此处曾自带一份，理由是当时 `train/loop_util._pid_alive`
在 Windows 上走 `os.kill(pid, 0)`＝`TerminateProcess`、**会把锁持有者直接杀掉**；2026-09-17
那份已改为委托同一实现，“三份同源”的漂移面随之归零。
"""

from __future__ import annotations

import os
import sys
import time

from common.pid_probe import pid_alive
from common.proc import run_capture

__all__ = [
    "acquire_instance_lock",
    "default_instance_lock_path",
    "proc_cmdline",
    "read_lock",
    "release_instance_lock",
]

#: 命令行打印长度上限（死锁排障只需认得「这是哪个程序」，不需要整条 argv）。
_CMD_CLIP = 160

#: 「残缺锁的写入窗口」宽限（秒）：锁文件解析不出持有者、且比这个还新 ⇒ 判为
#: **别的启动者正在写**（`O_CREAT|O_EXCL` 与 `os.write` 之间），拒启且不动那个文件。
#: 窗口本身是毫秒级，宽限取 30s 是给 NFS/慢盘与「stat 精度只有 1s」留余量；
#: 超过它仍按陈旧锁接管（崩溃残留不永久锁死启动）。定值不做旋钮：它与
#: `CLAIM_TTL_SEC` 同款——一个旋钮只是把现场调成另一种偶发。
PARTIAL_LOCK_GRACE_SEC = 30.0


def _pid_alive(pid: int) -> bool:
    """跨平台进程存活探测（委托唯一实现 `common.pid_probe.pid_alive`）。

    本文件早期刻意**不**复用 `train/loop_util._pid_alive`（那一处当时是裸 `os.kill`，在
    Windows 上会杀死被探测进程）——保留本名字只为调用点稳定；实现与语义现在只有一份，
    见 `common.pid_probe` 模块 docstring（Windows `TerminateProcess` / `pid<=0` 进程组 / 宽捕获）。
    """
    return pid_alive(pid)


def _win_cmdline_wmic(pid: int) -> str | None:
    """Windows：`wmic ... get CommandLine /value`（OS 内置组件而非 shell 语法）。

    ⚠ **Win11 24H2 起 `wmic` 已被移除**（2026-09-17 本机实测：`which wmic` 找不到、
    `System32/wbem/WMIC.exe` 不存在）⇒ 本函数在新机上必然返回 None，真实现是
    `_win_cmdline_cim`。保留它是因为还有一批未打该补丁的机器/云机镜像只认 wmic。

    ⚠ 走 `common.proc.run_capture` 而不是裸 `subprocess.run`（2026-10-13 用户报障「启动
    trainer/hub 闪黑窗」）：本函数只被**无控制台**的父进程调用（trainer / hub / 云机 worker
    都是 `spawnBg` / `CREATE_NO_WINDOW` 出来的），而 Windows 会给「无控制台的父进程 + 未带
    旗标的 console 子进程」**新建一块可见控制台窗口** ⇒ 每次启动都闪一下。`run_capture`
    自带 `POPEN_NO_WINDOW`（且返回值类型钉死为 `CompletedProcess[str]`，避开
    `warn_return_any`）。
    """
    import subprocess

    try:
        r = run_capture(
            ["wmic", "process", "where", f"processid={pid}", "get", "CommandLine", "/value"],
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (r.stdout or "").splitlines():
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

    ⚠ 同 `_win_cmdline_wmic`：走 `common.proc.run_capture`（自带 `POPEN_NO_WINDOW`）——本机实测
    这一发 pwsh 是「启动 trainer/hub 闪黑窗」里最显眼的一个（`ensure_cluster_snapshot` 的锚
    核验会调它两次：先比对指纹、再写元数据）。
    """
    import subprocess

    try:
        r = run_capture(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


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


def _lock_age_sec(lock_path: str) -> float | None:
    """锁文件的年龄（秒）；stat 失败/时钟倒着走 → None 或 0（调用方按「太新」fail-closed）。

    mtime 是「这个残缺文件是不是刚从别人手里生出来」唯一可用的信号（空文件里没有 pid
    可核验）。用 `max(0.0, …)`：粗粒度时间戳/时钟跳变下的负年龄必须读成「刚创建」。
    """
    try:
        return max(0.0, time.time() - os.stat(lock_path).st_mtime)
    except OSError:
        return None


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
    lock_path: str,
    *,
    marker: str | tuple[str, ...] = (),
    tag: str = "instance-lock",
    force: bool = False,
    announce: bool = True,
) -> bool:
    """取锁：True = 本进程持有（锁文件已写入）；False = 已有活实例（默认已响亮打印）。

    *marker* 是持有者身份指纹（如 `"hub_server"`）：只在持有者**活着**时用来区分
    「同一程序的第二个实例」（拒启）与「PID 被系统复用给了别的程序」（接管陈旧锁）。
    可给多个指纹（元组）——同一个服务常有多个合法入口（如 worker_server 既走
    `-m remote_worker_serve` 也可能被以模块名拉起），**任一命中即认作同一程序**。
    **空（缺省）= 不做身份核验**：持有者活着就拒启（fail-closed）、死了/够旧的残缺才接管
    ——互斥型锁（课程实例 / run_rl / push / claim / 快照）要的就是这个语义，它们没有
    稳定的「同程序指纹」（同一个锁的持有者可以是 run_rl / loop_serve / bc / 控制台）。

    *force*=True：**显式接管**（操作员 `--force` / 运维重启）——不问身份直接删掉现有锁再重取，
    调用方已确认「该跑的那个不在」。

    *announce*（2026-10-11）：**只管「拒启」那两句**（`partial` / 已有活实例 / 抢锁失败）。
    缺省 True（启动守卫该响）；**False 给「抢不到就改走别的路」的调用方**（如
    `remote/code_snapshot` 的抢锚：它抢不到就有界等待后自打，打印一句「拒绝启动」是误导）。
    **接管陈旧锁 / 放掉 PID 复用 / 创建失败的 WARN 不受它管辖**——那是「我拿到了锁」与
    「本次没做串行化」的事实，什么时候都该看得见（消音会把现场的可观测性一并拿掉）。
    """
    markers = (marker,) if isinstance(marker, str) else tuple(marker)

    def _say(msg: str) -> None:
        if announce:
            print(msg, flush=True)

    if force:
        try:
            os.remove(lock_path)
        except OSError:
            pass
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
        if holder is None:
            # 解析不出持有者：要么是**写入窗口**（别人刚创建还没写 pid），要么是崩溃残留。
            # 只能按年龄分流（空文件里没有身份可核验）；新鲜的必须 fail-closed——删掉它就是把
            # 先行者的锁抢走（2026-10-11：三启同时只该活一个，旧行为会让两个都自认唯一）。
            age = _lock_age_sec(lock_path)
            if age is None or age < PARTIAL_LOCK_GRACE_SEC:
                shown = "年龄未知" if age is None else f"{age:.2f}s 前"
                _say(
                    f"[{tag}] ERROR: 锁文件 {lock_path} 残缺（{shown}创建，里面没有持有者身份）"
                    f"——另一个启动者可能正在写入——拒绝启动（fail-closed）。"
                    f"若确认它只是崩溃残留，删除它后重试。"
                )
                return False
        if holder is not None and _pid_alive(holder):
            cmd = proc_cmdline(holder)
            if cmd is None or not markers or any(m in cmd for m in markers):
                if cmd is None:
                    why = "命令行不可读，身份未知（fail-closed）"
                elif not markers:
                    why = "未给身份指纹（无从核验）"
                else:
                    why = "命令行含同一程序指纹"
                _say(
                    f"[{tag}] ERROR: 已有实例在运行（PID {holder}，{why}）——拒绝启动。"
                    f"锁文件 {lock_path}；确认该进程已死后删除锁文件并重试。"
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
            _say(f"[{tag}] ERROR: 锁 {lock_path} 被并发启动者抢占——拒绝启动（再试一次即可）")
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
