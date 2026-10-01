"""统一日志基础设施 —— 训练日志必须可按时间轴复盘。

log():   带时间戳的 stdout 行（run_rl / queue / ppo 共用）。
Tee:     多流转发（控制台 + 文件）——run_rl._Tee 的通用化（P2-6d，2026-09-02）。
         train_loop._TrainTee 因带时间戳落盘 + [epoch N/M] 进度解析保留在本地
         （语义特定，重复度低），如需迁移可从本类继承。

**R2d 单进程多课程的行路由**（`prefix_scope` / `open_course_sink`）：一个进程服务 N 门课时
不能再换 `sys.stdout`（两个 Tee 套起来会把每行复制进两份课日志），所以课程归属改成
**行级前缀 + 行级镜像**：`log()` 在前缀作用域内把同一行同时写进该课的日志文件。
无前缀 = 与改造前逐字节相同（单课程入口 `out_log` 的 Tee 不受影响）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: 当前日志行的课程前缀（空 = 进程级/无课程归属）。只在**执行某课的一步**时置位。
_PREFIX = ""

#: 课程 → 该课日志文件句柄（追加、utf-8）。`log()` 在前缀命中时镜像同一行。
_COURSE_SINKS: dict[str, Any] = {}


def format_line(msg: str) -> str:
    """一行日志的**唯一**渲染（控制台与课日志逐字节同一行）。"""
    tag = f"[{_PREFIX}] " if _PREFIX else ""
    return f"[{time.strftime('%H:%M:%S')}] {tag}{msg}"


def log(msg: str) -> None:
    """Timestamped stdout line — the training log must be analyzable over time.

    在课程前缀作用域内（`prefix_scope`）额外镜像到该课的日志文件——单进程多课程下这
    替代了「每课自己的 stdout 重定向」（见模块 docstring）。镜像失败只吞（日志不得
    反向杀死训练）。
    """
    line = format_line(msg)
    print(line, flush=True)
    sink = _COURSE_SINKS.get(_PREFIX) if _PREFIX else None
    if sink is not None:
        try:
            sink.write(line + "\n")
            sink.flush()
        except Exception:
            pass


def get_prefix() -> str:
    """当前课程前缀（诊断/测试用；读面上就是「这一行属于哪门课」）。"""
    return _PREFIX


def set_prefix(prefix: str) -> str:
    """置课程前缀，返回**旧值**（调用方负责还原——`prefix_scope` 已封装）。"""
    global _PREFIX
    prev = _PREFIX
    _PREFIX = prefix
    return prev


@contextmanager
def prefix_scope(prefix: str) -> Iterator[None]:
    """该作用域内所有 `log()` 行带 `[课]` 前缀并镜像到该课日志（异常也还原）。"""
    prev = set_prefix(prefix)
    try:
        yield
    finally:
        set_prefix(prev)


def open_course_sink(course: str, path: str | Path) -> Any:
    """注册某课的日志镜像目标（追加、utf-8）。失败**响亮告警但不抛**。

    返回句柄（未注册 = None）。同一课重复注册先关旧的，避免句柄泄漏。
    """
    if not str(path or ""):
        return None
    close_course_sink(course)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = open(p, "a", encoding="utf-8")
    except Exception as e:
        print(f"[log] WARN 无法为课程 {course} 打开日志 {path}: {e}", flush=True)
        return None
    _COURSE_SINKS[course] = fh
    return fh


def close_course_sink(course: str) -> None:
    fh = _COURSE_SINKS.pop(course, None)
    if fh is None:
        return
    try:
        fh.close()
    except Exception:
        pass


def close_course_sinks() -> None:
    """进程退出（或单课程入口收尾）时收敛全部课日志句柄。"""
    for course in list(_COURSE_SINKS):
        close_course_sink(course)


class Tee:
    """同时写多个流（控制台 + 文件），供长训日志持久化且终端仍可见。

    源自 run_rl_intent（per-tick 模式借此获得 out_log/err_log 落盘能力）。
    on_line：可选逐行回调（train_loop 用它解析 [epoch N/M] 更新心跳进度）。
    """

    def __init__(self, *streams, on_line: Callable[[str], None] | None = None):
        self._streams = streams
        self._on_line = on_line

    def write(self, s: str) -> None:
        for st in self._streams:
            try:
                st.write(s)
            except Exception:
                pass
        if self._on_line is not None:
            try:
                self._on_line(s)
            except Exception:
                pass

    def flush(self) -> None:
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False
