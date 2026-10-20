"""remote/child_proc.py —— 子进程三件套的**唯一实现**：拉起 / 收线 / 行泵（2026-10-20）。

为什么要有它：仓库里现在有两个**同族的进程代理** —— 回传腿（`remote/upload_proc.py`）与
下行腿（`remote/payload_proc.py`）。它们的「`python -u -m <模块>` + stdin/stdout 管道」这一段
是同一件事：`Popen` 的参数（`cwd` / `PYTHONPATH` / 文本编码 / 不弹窗）、收线的三段式
（关 stdin → 等余量 → terminate → kill）、以及「把 stdout 当作行流读干净」。

第三个副本会立刻变成三份各自会漂的真相（仓库的复用账本 `tools/reuse_audit.py` 就是为了
抓这个）。所以这三件事只写在这里，两个代理都调它。

**边界**：本模块只管「进程与管道」，不管协议 —— 行前缀（`@@upload@@` / `@@pf@@`）、
降级策略、记账全在各自的代理里。也不碰 `deliver_proc`（它是上一代实现，语义已定，不为对称
去动它）。

**零仓内依赖**（stdlib + `common.platform_utils.POPEN_NO_WINDOW`）⇒ 账本里秩为 **L0**，
任何模块都能在它下面踩，不可能成环。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from typing import IO, Any

from common.platform_utils import POPEN_NO_WINDOW

#: 收线时给子进程自己的余量（秒）：关 stdin 之后它最多还需要把手上那一份做完。
REAP_GRACE_SEC = 5.0
#: terminate 之后的第二段余量（秒）；再不走就 kill。
REAP_KILL_SEC = 5.0


def repo_root() -> str:
    """`nn-training` 根（子进程 `python -m remote.<entry>` 的 cwd）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def spawn_module(
    module: str,
    *,
    python: str | None = None,
    cwd: str | None = None,
    spawn: Callable[[list[str]], Any] | None = None,
) -> tuple[subprocess.Popen | None, str]:
    """拉起 `python -u -m <module>`（管道全开）。返回 `(proc, why)`：`why` 非空 = 起不来。

    `spawn` 是测试注入点（假子进程只实现 Popen 的兼容面）；给了就完全走它。
    `PYTHONPATH` 必须继承当前 `sys.path`：离线包解压 / `notebook_runtime` 会改它，
    不继承就是 `No module named remote.<entry>`（而那正是「在飞的代码树里没有这个模块」的
    真实形状，降级兜得住，但没必要白撞一次）。
    """
    argv = [str(python or sys.executable), "-u", "-m", str(module)]
    try:
        if spawn is not None:
            return spawn(argv), ""
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd or repo_root()),
            env=env,
            stdin=subprocess.PIPE,
            # stderr 并入 stdout：两条流派生的行都只是「日志 or 协议行」，一个读者就够。
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **POPEN_NO_WINDOW,
        )
    except Exception as e:
        return None, f"Popen 失败（{type(e).__name__}: {e}）"
    return proc, ""


def reap_child(proc: subprocess.Popen | None, *, grace: float = REAP_GRACE_SEC) -> None:
    """收子进程：关 stdin（= 收线约定）→ 等 `grace` → terminate → 等 → kill。**不抛**。

    调用方（代理）自己负责自己的线程：本函数只碰进程，因为「join 自己」是调用点才看得见的
    死锁形状（读者线程里降级时 join 自己）。
    """
    if proc is None:
        return
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=float(grace))
    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=REAP_KILL_SEC)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def pump_lines(stream: IO[str] | None, on_line: Callable[[str], None]) -> None:
    """逐行读 `stream`（去行尾、跳过空行、整段吞异常），每行交给 `on_line`。EOF 即返回。

    「整段吞异常」是有意的：管道可能被子进程的退出截断（`ValueError: I/O operation on closed
    file` / `OSError`），那时**真正的信号是 EOF**（调用方据此判「子进程没了」），不是这条例外。
    """
    if stream is None:
        return
    try:
        for line in stream:
            s = str(line).rstrip("\r\n")
            if not s:
                continue
            on_line(s)
    except Exception:
        pass


def join_all(threads: Iterable[threading.Thread | None], *, timeout: float = 2.0) -> None:
    """收一批线程（跳过当前线程与 None）。"""
    me = threading.current_thread()
    for t in threads:
        if t is not None and t is not me:
            t.join(timeout=timeout)
