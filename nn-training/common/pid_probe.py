"""common.pid_probe —— 进程存活探测的**唯一实现**（stdlib-only；无 torch、无包依赖）。

放在 nn-training 根目录（与 `common/platform_utils.py` 同层）是为了让所有入口都能 `from common.pid_probe
import pid_alive`：`trainer/run_rl.py` / `trainer/run_bc.py` / `train/loop_util.py` / `remote/*`，而**不**引入
任何包依赖——`remote/` 要独立打进 code.zip、`train/loop_util` 刻意保持 torch-free，两者都不能
反向依赖对方的 `__init__`。

## 为什么必须唯一（三个坑，每一个都在本仓真实发生过）

1. **Windows 上 `os.kill(pid, 0)` 不是探测，是 `TerminateProcess(handle, 0)`** —— 会把被探测的
   进程**直接杀掉**。2026-09-17 实际隐患：`train/loop_util.py::_pid_alive` 是 `acquire_lock`
   判定陈旧锁的唯一依据，即一个**只读查询**——查询动作本身杀死了锁持有者（最坏：正在训练的
   trainer）。Windows 侧一律走 `GetExitCodeProcess == STILL_ACTIVE`。
2. **`pid <= 0` 在 POSIX 上是进程组语义**：`os.kill(0, 0)` / `os.kill(-1, 0)` 实测**成功**，
   于是残缺锁文件里的 `0`/`-1` 被当成「有人持有」⇒ 同名课永久拒启 / tmp-clean 永远不收敛。
3. **`except Exception → False` 会把「我杀了它」记成「它本来就是死的」**（坑 1 的连带），
   双开护栏静默失效。故探测失败一律按「不活」——副作用正确：陈旧锁总能被清理。

## 契约

* `pid_alive(pid)` → `True` 仅当该 PID 上确有一个存活进程；其余（`None` / `<=0` / 已退出 /
  打不开 / 任何异常）一律 `False`。**绝不产生信号、绝不改变任何进程状态。**
* 调用方若还需要「这个 PID 是不是我以为的那个程序」，用 `proc_cmdline_nt`（Windows 直读
  PEB，无子进程）/ `common.instance_lock.proc_cmdline` 的指纹核验（第二层，不属于 `pid_alive`）。
"""

from __future__ import annotations

import os

# Windows 常量（仅 Windows 分支使用；不导入 ctypes 到模块级以保持跨平台零成本加载）
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def proc_cmdline_nt(pid: int) -> str | None:
    """Windows 原生命令行：`NtQueryInformationProcess` → PEB → `RTL_USER_PROCESS_PARAMETERS`。

    旧路径（`wmic` / pwsh `Get-CimInstance`）每次**起一个子进程**，本机实测 0.38–0.42s/次
    （WSL 读 `/proc` 只要 0.0001s）。hub 启动的 code-snapshot 锚核验 + 陈旧锁接管每次读
    1–2 回 ⇒ 一条真 hub 的 e2e 白付 0.4–0.8s，Windows 门禁 78s vs WSL 52s 的主因之一。
    这里在进程内直读 Windows 结构，实测 0.0002s（首次加载 ctypes DLL ~0.01s）。

    失败一律返回 None ⇒ 调用方回落旧路径（身份读不到时 fail-closed，绝不猜）。
    跨架构（64 位 Python 读 32 位进程）拿不到正确 PEB ⇒ 也自然回落到旧路径。
    """
    import ctypes
    import struct
    from ctypes import wintypes

    # 经 __dict__ 取而非 ctypes.WinDLL：mypy 的跨平台 typeshed 不总暴露这个 Windows-only 属性。
    win_dll = ctypes.__dict__["WinDLL"]
    k32 = win_dll("kernel32", use_last_error=True)
    ntdll = win_dll("ntdll", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.LPVOID,
    ]
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG

    class _BasicInfo(ctypes.Structure):
        _fields_ = [
            ("exit_status", wintypes.LONG),
            ("peb", ctypes.c_void_p),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.LONG),
            ("pid", ctypes.c_size_t),
            ("parent", ctypes.c_size_t),
        ]

    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ：只读句柄，绝不碰目标进程。
    handle = k32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        info = _BasicInfo()
        if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        if not info.peb:
            return None
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        params = ctypes.c_void_p()
        if not k32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(info.peb + (0x20 if ptr_size == 8 else 0x10)),
            ctypes.byref(params),
            ptr_size,
            None,
        ) or not params.value:
            return None
        # UNICODE_STRING = {USHORT Length; USHORT Max; PAD; PWSTR Buffer}
        # 偏移指 **CommandLine** 这个 UNICODE_STRING 的起点（x64 0x70 / x86 0x40）。
        # 取 0x60 会读到前一个 ImagePathName —— 只有 exe 路径、没有 argv，实例锁的
        # marker 判据会把活持有者误判成 PID 复用（2026-11-27 全量门禁 7 红现场）。
        cmdline_off = 0x70 if ptr_size == 8 else 0x40
        head = ctypes.create_string_buffer(8 + ptr_size if ptr_size == 8 else 4 + ptr_size)
        if not k32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(params.value + cmdline_off),
            head,
            len(head),
            None,
        ):
            return None
        length = struct.unpack_from("<H", head.raw, 0)[0]
        if not length:
            return None
        buffer_addr = struct.unpack_from(
            "<Q" if ptr_size == 8 else "<I", head.raw, 8 if ptr_size == 8 else 4
        )[0]
        data = ctypes.create_string_buffer(length)
        if not k32.ReadProcessMemory(handle, ctypes.c_void_p(buffer_addr), data, length, None):
            return None
        return data.raw[:length].decode("utf-16-le", "replace").strip("\x00").strip() or None
    finally:
        k32.CloseHandle(handle)


def pid_alive(pid: int | None) -> bool:
    """跨平台进程存活探测。见模块 docstring 的契约与三个坑。"""
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        # pid 0/负数 = 进程组语义（POSIX 上 os.kill(0, 0) 会成功），绝不是「某个持有者」
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX：signal 0 = 只做存在性/权限探测，不发信号
        return True
    except Exception:
        # 宽捕获：Windows 残留路径、权限、PID 复用竞态……任何探测失败都按「不活」处理，
        # 让陈旧锁可清理（绝不因为探测本身失败把操作员锁死）。
        return False
