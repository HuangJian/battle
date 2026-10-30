"""common/logutil.py —— 日志**行格式**的唯一来源。

本仓有两套日志出口，用途不同但**时间戳格式必须一致**（复盘时要能把两边按时间对齐）：

* `biz/log.py::log` —— 训练主通道，带**课程前缀路由**（`prefix_scope` 把同一行镜像进
  该课的日志文件）。它有模块级可变状态，属上层设施，留在 `trainer/`。
* 带**固定 tag** 的轻量出口 —— `remote/run_loop.py` / `remote/push_dispatch.py` /
  `remote/offline_deliver.py` / `remote/notebook_runtime.py` 各自的 `_log_default`。
  它们刻意不依赖 `biz/log.py`（`remote/run_loop` 要在**没有 rl 包**的上下文里当自主循环
  的默认日志），于是「`[{时间}] [{tag}] {消息}` + flush」这段格式被抄了 4 遍。

本模块只收**格式**：tag 由调用方给，时钟由调用方传（关键——见下），不做路由、不留状态。
`biz/log.py::format_line` 也改用这里的 `stamp()`，两边的时间戳因此不可能再漂移。

**为什么 `clock` 是形参而不是模块级 `time`**：`tests/remote/test_notebook_runtime.py` 用
`monkeypatch.setattr(nbr, "time", clock)` 注入假钟（只重绑该模块的引用、不污染全局
`time`）。若本模块自己抓 `time.strftime`，那次注入就失效了 ⇒ 调用方必须把自己的
`time` 传进来（`clock=time`），假钟才拦得到。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

__all__ = ["Clock", "kb", "log_line", "secs", "stamp", "tag_logger"]


class Clock(Protocol):
    """时钟接口（`time` 模块与测试假钟都满足）：只需要 `strftime`。"""

    def strftime(self, fmt: str) -> str: ...


#: 时间戳格式（全仓唯一）。改这里 = 改所有日志行，别在调用点写第二份字面量。
STAMP_FMT = "%H:%M:%S"


def stamp(clock: Clock = time) -> str:
    """`[HH:MM:SS]`（含方括号，与 `biz/log.py` 的历史输出逐字节一致）。"""
    return f"[{clock.strftime(STAMP_FMT)}]"


def secs(v: float) -> str:
    """秒数的**紧凑**口径：整数秒不带 `.0`（`0.0` → `0`、`6.4` → `6.4`）。

    为什么：准备段那一行的读数多半是 `0.0s`（快路径命中）——小数位是纯噪声；
    而 `6.4` 这类**真读数**必须留住（判「慢在哪一段」靠它）。全仓统一走这里，
    免得同一栏在不同调用点各写一种小数位。
    """
    s = f"{float(v):.1f}"
    return s[:-2] if s.endswith(".0") else s


def kb(n: int) -> str:
    """字节 → KB（**十进制**；`< 1000` 给字节本身）。

    为什么用 1000 而不是 1024：这一栏是给人**粗读量级**的（payload 多大），
    十进制 KB 与云控制台的账单口径一致，不会出现「305 KB 却对不上 298 KiB」。
    """
    n = int(n)
    if n < 1000:
        return f"{n} B"
    return f"{round(n / 1000)} KB"


def log_line(tag: str, msg: str, *, clock: Clock = time, stream: Any = None) -> None:
    """`[HH:MM:SS] [tag] msg` 一行到 stdout（强制 flush：长训要能实时看到）。

    flush 不能省：`subprocess` 管道/重定向下 stdout 是块缓冲的，不 flush 的话
    云机会话里「进度行每分钟一句」会攒成一大块——那正是这些日志存在的意义。
    """
    print(f"{stamp(clock)} [{tag}] {msg}", flush=True, file=stream or sys.stdout)


def tag_logger(tag: str, clock: Callable[[], Clock]) -> Callable[[str], None]:
    """固定 tag 的日志出口工厂（`remote/*` 的 `_log_default` 共 4 份）。

    ⚠ `clock` 是**取值函数**（调用点写 `lambda: time`），不是时钟对象：每次调用都重读
    **该模块自己的** `time` 引用，`monkeypatch.setattr(mod, "time", 假钟)` 才拦得到。
    若这里收 `Clock` 对象（import 期绑死），那些注入会**静默失效**——假钟一失效，
    断言时间戳的用例会变成「一直在看真钟」，却仍然绿。
    """

    def _emit(msg: str) -> None:
        log_line(tag, msg, clock=clock())

    return _emit
