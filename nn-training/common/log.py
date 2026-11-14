"""统一日志基础设施 —— 训练日志必须可按时间轴复盘。

log():   带时间戳的 stdout 行（run_rl / queue / ppo 共用）。
Tee:     多流转发（控制台 + 文件）——run_rl._Tee 的通用化（P2-6d，2026-09-02）。
         带时间戳落盘 + [epoch N/M] 进度解析的那一份（曾住本地 BC 连续训练入口）
         已随该入口退役；语义特定、重复度低，如需再加可从本类继承。

**R2d 单进程多课程的行路由**（`prefix_scope` / `open_course_sink`）：一个进程服务 N 门课时
不能再换 `sys.stdout`（两个 Tee 套起来会把每行复制进两份课日志），所以课程归属改成
**行级前缀 + 行级镜像**：`log()` 在前缀作用域内把同一行同时写进该课的日志文件。
无前缀 = 与改造前逐字节相同（单课程入口 `out_log` 的 Tee 不受影响）。

**状态归属（2026-11-13，python.refactor.md §P0-5）**：前缀与课日志句柄不再是模块级
`_PREFIX` / `_COURSE_SINKS`，而是 `LogRouter` 的两个字段——一个进程一个默认实例
（`default_router()`），需要隔离的场景（测试、多课程宿主）可以自己 `LogRouter()` 并把
`router=` 传进来。前缀用 `contextvars.ContextVar` 存：后台线程读不到别的课程最后写的值
（旧实现按线程共享字符串，注释里已经踩过「4 条 it5 DONE 里 3 条前缀是错的」）。
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from common.logutil import log_line, stamp


class LogRouter:
    """课程前缀 + 课日志镜像的**显式所有者**（不含进程级可变态）。"""

    def __init__(self) -> None:
        self._prefix: contextvars.ContextVar[str] = contextvars.ContextVar(
            f"nn_log_prefix_{id(self)}", default=""
        )
        self._sinks: dict[str, Any] = {}
        #: 已经喊过的失败键（`mirror:<课>` / `close:<课>`）：同一种失败只喊一次，
        #: 否则日志自己坏掉时会每行刷一条 WARN。
        self._warned: set[str] = set()

    # ---- 前缀 ----
    def get_prefix(self) -> str:
        """当前课程前缀（诊断/测试用；读面上就是「这一行属于哪门课」）。"""
        return self._prefix.get()

    def set_prefix(self, prefix: str) -> str:
        """置课程前缀，返回**旧值**（调用方负责还原——`prefix_scope` 已封装）。"""
        prev = self._prefix.get()
        self._prefix.set(prefix)
        return prev

    @contextmanager
    def prefix_scope(self, prefix: str) -> Iterator[None]:
        """该作用域内所有 `log()` 行带 `[课]` 前缀并镜像到该课日志（异常也还原）。"""
        prev = self.set_prefix(prefix)
        try:
            yield
        finally:
            self.set_prefix(prev)

    # ---- 渲染 / 出口 ----
    def format_line(self, msg: str, *, course: str | None = None) -> str:
        """一行日志的**唯一**渲染（控制台与课日志逐字节同一行）。

        `course` 显式给出时优先于当前前缀（eval 后台线程的归属，见 `log`）。
        时间戳只走 `common.logutil.stamp()`：全仓只有一份 `%H:%M:%S`。
        """
        who = course or self.get_prefix()
        tag = f"[{who}] " if who else ""
        return f"{stamp()} {tag}{msg}"

    def log(self, msg: str, *, course: str | None = None) -> None:
        line = self.format_line(msg, course=course)
        print(line, flush=True)
        who = course or self.get_prefix()
        sink = self._sinks.get(who) if who else None
        if sink is not None:
            self._mirror(sink, line, who)

    def warn_once(self, key: str, msg: str) -> None:
        """把**日志自己的**失败喊一次（同一 key 只喊一次）；喊也不能抛。

        为什么不是 `pass`：镜像写失败以前完全静默 —— 该课日志在丢行，而控制台一切正常，
        只有事后比对才发现少了半天。`pass` 会让「日志坏了」和「没有日志」长得一模一样。
        """
        if key in self._warned:
            return
        self._warned.add(key)
        try:
            log_line("log", f"WARN {msg}")
        except (OSError, ValueError):  # 喊话本身失败就到此为止（不许递归、不许抛）
            pass

    def _mirror(self, sink: Any, line: str, who: str) -> None:
        """镜像一行到课日志；失败**不影响控制台**，但会喊一次（日志不得反向杀死训练）。"""
        try:
            sink.write(line + "\n")
            sink.flush()
        except Exception as e:  # 任何写失败都不许冒到训练循环
            self.warn_once(
                f"mirror:{who}",
                f"课 {who} 的日志镜像写失败（{type(e).__name__}: {e}）"
                "——控制台仍完整，该课日志文件在此刻丢行",
            )

    # ---- 课日志句柄 ----
    def open_sink(self, course: str, path: str | Path) -> Any:
        """注册某课的日志镜像目标（追加、utf-8）。失败**响亮告警但不抛**。

        返回句柄（未注册 = None）。同一课重复注册先关旧的，避免句柄泄漏。
        """
        if not str(path or ""):
            return None
        self.close_sink(course)
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            fh = open(p, "a", encoding="utf-8")  # noqa: SIM115
        except Exception as e:
            log_line("log", f"WARN 无法为课程 {course} 打开日志 {path}: {e}")
            return None
        self._sinks[course] = fh
        return fh

    @property
    def sinks(self) -> dict[str, Any]:
        """当前注册的课日志句柄（诊断/测试用；写面只有 `open_sink`/`close_sink`）。"""
        return self._sinks

    def close_sink(self, course: str) -> None:
        fh = self._sinks.pop(course, None)
        if fh is None:
            return
        try:
            fh.close()
        except Exception as e:  # 关闭失败只是句柄回收问题，不许冒
            self.warn_once(
                f"close:{course}",
                f"课 {course} 的日志句柄关闭失败（{type(e).__name__}: {e}）——忽略（退出时由 OS 回收）",
            )

    def close_sinks(self) -> None:
        """进程退出（或单课程入口收尾）时收敛全部课日志句柄。"""
        for course in list(self._sinks):
            self.close_sink(course)


_DEFAULT_ROUTER = LogRouter()


def default_router() -> LogRouter:
    """进程默认路由（需要隔离时自建 `LogRouter()` 并传 `router=`）。"""
    return _DEFAULT_ROUTER


def format_line(msg: str, *, course: str | None = None) -> str:
    """默认路由的一行渲染（兼容旧调用点）。"""
    return _DEFAULT_ROUTER.format_line(msg, course=course)


def log(msg: str, *, course: str | None = None, router: LogRouter | None = None) -> None:
    """Timestamped stdout line — the training log must be analyzable over time.

    在课程前缀作用域内（`prefix_scope`）额外镜像到该课的日志文件——单进程多课程下这
    替代了「每课自己的 stdout 重定向」（见模块 docstring）。镜像失败只吞（日志不得
    反向杀死训练）。
    `course`（2026-10-18，plan/eval-gap-honesty §6-1）：**显式**课程归属，不经前缀。
    为什么需要：eval 跑在 `dispatch_eval_bg` 的 daemon 线程里，活过 `prefix_scope` 的作用域
    ⇒ 它每行读到的是**别的课程线程**最后置的前缀，行标签与镜像目标都会张冠李戴
    （实测 4 条 `it5 DONE` 里 3 条前缀是错的）。缺省 = 旧行为逐字节不变。
    """
    (router or _DEFAULT_ROUTER).log(msg, course=course)


def say(msg: str, *, file: Any = None) -> None:
    """带 `[HH:MM:SS] ` 前缀的一行（训练器 stdout 日志；2026-11-12）。

    为什么要它：云端 BC 作业由 worker 在**本进程内**调 `train/bc.py::train`——训练器
    直接 `print` 的行没有时间戳，而它上下文的 `[worker]` 行都有，读日志时无法把
    “epoch 7 是什么时候打完的”与 hub 事件对齐。训练器改用本函数后每行都可对时；
    正文一字不改（`[train]` / `[epoch …]` 的前缀保持原位，本地 `trainer/bc_loop.py`
    解析前会剥掉这个时间戳）。
    与 `log()` 的区别：不参与课程前缀 / 镜像（那是 supervisor 的行路由，训练器的
    行由栈上下文决定去哪）。
    """
    print(f"{stamp()} {msg}", flush=True, file=file)


def get_prefix(*, router: LogRouter | None = None) -> str:
    return (router or _DEFAULT_ROUTER).get_prefix()


def set_prefix(prefix: str, *, router: LogRouter | None = None) -> str:
    return (router or _DEFAULT_ROUTER).set_prefix(prefix)


def prefix_scope(prefix: str, *, router: LogRouter | None = None) -> Any:
    """该作用域内所有 `log()` 行带 `[课]` 前缀并镜像到该课日志（异常也还原）。"""
    return (router or _DEFAULT_ROUTER).prefix_scope(prefix)


def open_course_sink(
    course: str, path: str | Path, *, router: LogRouter | None = None
) -> Any:
    return (router or _DEFAULT_ROUTER).open_sink(course, path)


def close_course_sink(course: str, *, router: LogRouter | None = None) -> None:
    (router or _DEFAULT_ROUTER).close_sink(course)


def close_course_sinks(*, router: LogRouter | None = None) -> None:
    (router or _DEFAULT_ROUTER).close_sinks()


class Tee:
    """同时写多个流（控制台 + 文件），供长训日志持久化且终端仍可见。

    源自 run_rl_intent（per-tick 模式借此获得 out_log/err_log 落盘能力）。
    on_line：可选逐行回调（逐行解析进度行、更新心跳/状态的那类用法）。
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
