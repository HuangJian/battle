"""test_child_proc_pump.py — 行泵的两种异常必须分开处理（2026-11-14，`python.refactor.md` §P0-7）。

`remote/child_proc.py::pump_lines` 是回传腿 / 下行腿共用的**唯一**行泵。它此前把两种完全不同的
异常揉进同一个 `except Exception: pass`：

  * **读流抛**（管道被子进程退出截断：`ValueError: I/O operation on closed file` / `OSError`）——
    这是**正常收线的回声**，真正的信号是 EOF，调用方据此判「子进程没了」。静默是对的。
  * **回调抛** —— 那是**我们自己**的行解析/记账代码有 bug。旧实现让这条例外把整个行泵带走：
    泵线程当场停摆，调用方从此再也看不到子进程的任何一行（`@@upload@@` / `@@pf@@` 这些**协议行**
    也在其中），只能干等到超时，现场零线索。

本文件钉住新契约：**读流异常照旧静默；回调异常逐行兜住、继续读、收尾报一次「丢了几行」**。
"""

from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import IO, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import child_proc


def test_callback_failure_keeps_pumping_and_is_reported() -> None:
    """回调对某些行抛 ⇒ 其余行**照读**（泵不停摆），收尾只报一行带计数的日志。"""
    seen: list[str] = []
    logs: list[str] = []

    def _on_line(s: str) -> None:
        if s.startswith("bad"):
            raise ValueError("解析不了这行")
        seen.append(s)

    stream = io.StringIO("好1\nbad1\n好2\nbad2\n好3\n")
    child_proc.pump_lines(stream, _on_line, log=logs.append)

    # ① 坏行不进账、好行一条不丢 —— 旧的「整段吞」在这里只会读出「好1」就沉默。
    assert seen == ["好1", "好2", "好3"]
    # ② 恰好一行读数，且带得上「丢了几行」（3 = 一个密码都没有，纯读数）。
    assert len(logs) == 1, logs
    head, _, cause = logs[0].partition("（")
    assert head.endswith("行泵：2 行被回调丢弃"), logs[0]
    assert cause.startswith("第一次 ValueError('解析不了这行')")


def test_truncated_stream_stays_silent() -> None:
    """子进程退出把管道截断 ⇒ 静默（这是 EOF 的回声，不是故障：调用方据此走降级）。"""

    class _TruncatingStream:
        """第一行正常、第二行迭代时抛（= 子进程退出把管道截断的形状）。"""

        def __iter__(self) -> Iterator[str]:
            yield "一行正常的日志\n"
            raise ValueError("I/O operation on closed file")

    logs: list[str] = []
    seen: list[str] = []
    child_proc.pump_lines(cast("IO[str]", _TruncatingStream()), seen.append, log=logs.append)

    assert seen == ["一行正常的日志"]
    assert logs == [], f"读流截断不该报错（那是 EOF 的回声）：{logs}"


def test_summary_log_that_itself_raises_does_not_escape() -> None:
    """喊话本身失败（stdout 关了 ⇒ `print` 抛 ValueError）⇒ 吞掉，**不把行泵也带走**。

    `pump_lines` 的契约是「不抛」：这里是它在生产里最可能被违反的一行（报数那行也是往 stdout 写）。
    冒出去的话，线程带着 traceback 死掉，调用方看到的仍是「腿卡住」——正是这次拆分要治的形状。
    """

    class _Stream:
        def __iter__(self) -> Iterator[str]:
            yield "一行\\n"

    def boom(_msg: str) -> None:
        raise ValueError("I/O operation on closed file")

    child_proc.pump_lines(cast("IO[str]", _Stream()), boom, log=boom)  # 不抛即通过
