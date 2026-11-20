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
import time
from collections.abc import Callable, Iterable
from typing import IO, Any

from common.logutil import tag_logger
from common.platform_utils import POPEN_NO_WINDOW

_log = tag_logger("child", lambda: time)

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
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        # Popen 的真失败面（同 `deliver_proc._spawn`）。`env`/`sys.path` 拼错是我们自己的 bug
        # （`AttributeError` / `TypeError`）⇒ 穿出去，而不是报成「Popen 失败」去查系统。
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
        # 契约静默（G9 冻结，非旧债）：收尾级联的第一步 —— 管道已被对端关掉时 close 会抛，
        # 而「父进程收线了」的结论已由读流 EOF 拿到，不靠这里。
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
                # 契约静默（G9 冻结，非旧债）：级联最后一级 —— terminate+wait 都失败才走到 kill，
                # kill 再失败 = 进程已不受控；没有可做的事，也不该把收尾异常抛回调用方。
                pass


def pump_lines(
    stream: IO[str] | None,
    on_line: Callable[[str], None],
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """逐行读 `stream`（去行尾、跳过空行），每行交给 `on_line`。EOF 即返回。**不抛**。

    两种异常必须分开处理（2026-11-14）：

    * **读流抛**（管道被子进程的退出截断：`ValueError: I/O operation on closed file` / `OSError`）
      ⇒ 静默。那时**真正的信号是 EOF**（调用方据此判「子进程没了」），这条例外只是它的回声。
    * **回调抛** ⇒ 那是**我们自己**的解析/记账代码出了 bug。旧实现让它把整个行泵一起带走：
      线程当场停摆，调用方从此再也看不到子进程的任何一行（协议行也在其中），只能等到超时，
      现场零线索。现在逐行兜住、继续读，收尾时一次性报「丢了几行 + 第一次的异常」——
      坏行不进账，好行照走，**且这件事不再是静音的**。
    """
    if stream is None:
        return
    dropped = 0
    first: BaseException | None = None
    try:
        for line in stream:
            s = str(line).rstrip("\r\n")
            if not s:
                continue
            try:
                on_line(s)
            except Exception as e:
                dropped += 1
                if first is None:
                    first = e
    except Exception:
        # 契约静默（G9 冻结，非旧债）：读流被截断 =「管道关了」这条正常收线（EOF 的回声）；
        # 回调自己抛的异常上面已单独兜住并报数（dropped / first）。
        pass
    if dropped:
        try:
            (log or _log)(
                f"行泵：{dropped} 行被回调丢弃（第一次 {first!r}）—— 子进程仍在跑，其余行照读"
            )
        except (OSError, ValueError):
            # 喊话本身失败 = 出口（stdout/管道）已经关了，到此为止。不吞的话这行会把整条行泵
            # 带走：线程带着 traceback 死掉，调用方看到的还是「腿卡住」——正是本函数要治的形状。
            pass


def join_all(threads: Iterable[threading.Thread | None], *, timeout: float = 2.0) -> None:
    """收一批线程（跳过当前线程与 None）。"""
    me = threading.current_thread()
    for t in threads:
        if t is not None and t is not me:
            t.join(timeout=timeout)
