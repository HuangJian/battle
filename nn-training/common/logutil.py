"""common/logutil.py —— 日志**行格式**的唯一来源。

本仓有两套日志出口，用途不同但**时间戳格式必须一致**（复盘时要能把两边按时间对齐）：

* `rl/log.py::log` —— 训练主通道，带**课程前缀路由**（`prefix_scope` 把同一行镜像进
  该课的日志文件）。它有模块级可变状态，属上层设施，留在 `rl/`。
* 带**固定 tag** 的轻量出口 —— `remote/run_loop.py` / `remote/push_dispatch.py` /
  `remote/offline_deliver.py` / `remote/notebook_runtime.py` 各自的 `_log_default`。
  它们刻意不依赖 `rl/log.py`（`remote/run_loop` 要在**没有 rl 包**的上下文里当自主循环
  的默认日志），于是「`[{时间}] [{tag}] {消息}` + flush」这段格式被抄了 4 遍。

本模块只收**格式**：tag 由调用方给，时钟由调用方传（关键——见下），不做路由、不留状态。
`rl/log.py::format_line` 也改用这里的 `stamp()`，两边的时间戳因此不可能再漂移。

**为什么 `clock` 是形参而不是模块级 `time`**：`tests/test_notebook_runtime.py` 用
`monkeypatch.setattr(nbr, "time", clock)` 注入假钟（只重绑该模块的引用、不污染全局
`time`）。若本模块自己抓 `time.strftime`，那次注入就失效了 ⇒ 调用方必须把自己的
`time` 传进来（`clock=time`），假钟才拦得到。
"""

from __future__ import annotations

import sys
import time
from typing import Any, Protocol

__all__ = ["Clock", "log_line", "stamp"]


class Clock(Protocol):
    """时钟接口（`time` 模块与测试假钟都满足）：只需要 `strftime`。"""

    def strftime(self, fmt: str) -> str: ...


#: 时间戳格式（全仓唯一）。改这里 = 改所有日志行，别在调用点写第二份字面量。
STAMP_FMT = "%H:%M:%S"


def stamp(clock: Clock = time) -> str:
    """`[HH:MM:SS]`（含方括号，与 `rl/log.py` 的历史输出逐字节一致）。"""
    return f"[{clock.strftime(STAMP_FMT)}]"


def log_line(tag: str, msg: str, *, clock: Clock = time, stream: Any = None) -> None:
    """`[HH:MM:SS] [tag] msg` 一行到 stdout（强制 flush：长训要能实时看到）。

    flush 不能省：`subprocess` 管道/重定向下 stdout 是块缓冲的，不 flush 的话
    云机会话里「进度行每分钟一句」会攒成一大块——那正是这些日志存在的意义。
    """
    print(f"{stamp(clock)} [{tag}] {msg}", flush=True, file=stream or sys.stdout)
