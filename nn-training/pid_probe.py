"""pid_probe —— 进程存活探测的**唯一实现**（stdlib-only；无 torch、无包依赖）。

放在 nn-training 根目录（与 `platform_utils.py` 同层）是为了让所有入口都能 `from pid_probe
import pid_alive`：`run_rl.py` / `run_bc.py` / `train/loop_util.py` / `remote/*`，而**不**引入
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
* 调用方若还需要「这个 PID 是不是我以为的那个程序」，用 `remote._instance_lock` 的命令行
  指纹核验（那是第二层，不属于本函数）。
"""

from __future__ import annotations

import os

# Windows 常量（仅 Windows 分支使用；不导入 ctypes 到模块级以保持跨平台零成本加载）
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


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
