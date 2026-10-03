"""tools/forkdist.py — 「收集一次、fork 出 worker」的 pytest 分发器（POSIX-only，须显式开启）。

**为什么**（docs/nn/engineering.md §48 的账）：`pytest tests/ e2e/ -n 12` 下，**每个 worker 都
要收集全部 ~275 个测试模块**（实测 load / loadfile 两种分发模式都是 274 模块/worker）。12 份
收集里 11 份是纯冗的——它占 worker 自用 CPU 的 35%（57.8s），而收集相是**全核串行段**
（每个 worker 收完才开跑）。把它降到一份：

    收集一次（master，单进程）→ os.fork() 出 N 个 worker
    └ 子进程经 COW 继承 sys.modules 与已收集的 Item ⇒ 零重复 import 直接开跑

**与 xdist 的对应关系**（刻意保持逐条语义一致，避免「换 runner 换了测试语义」）：

* 派发 = **逐条动态**（xdist 的 `--dist=load`），不是按文件切块；本套用例是异质的（真 torch /
  真起进程 / 真 HTTP 都在长尾），动态补位是 §47 判定 `loadfile` 更慢的原因之一。
* `nextitem` 与 xdist 一样**预留**（把「跑完这条接下来轮到的那条」一并告诉 worker），
  module/class 级夹具才不会在连续两条之间被提前收掉。
* 报告回传走 pytest **核心 hook** `pytest_report_to_serializable` / `pytest_report_from_serializable`
  （就是 xdist 用的那对，2026-09 起已在 `_pytest/reports.py` 里），master 收到后原样
  `pytest_runtest_logreport` 重放 ⇒ 汇总、`-ra`、`--tb=short`、`-x`(maxfail) 全是 pytest 自己
  的语义，不是我们另写一套。
* 子进程里把 TerminalReporter 摘掉（否则 12 份进度点/自己的汇总会打到共享 stdout 上），
  并把 `config.workerinput` 填上（xdist 的既有约定：cacheprovider 的 `lastfailed` 写入、
  junitxml、stepwise 都会**自动跳过**，不再有 12 个进程抢写同一个缓存文件）。

**Windows**：没有 `os.fork` ⇒ 本插件直接拒绝（`pytest.UsageError`），门禁在 Windows 上仍走
xdist（见 tools/githook/nn-python-gate.sh 的分支）。判据是「有没有 os.fork」，不是 uname。

**用法**（显式开启，缺省不生效）：

    python -m pytest tests/ e2e/ -p tools.forkdist --forkdist 12 --timeout=60
    python -m pytest tests/ e2e/ -p tools.forkdist --forkdist auto --timeout=60   # = 物理核数

`auto` = `common.platform_utils.physical_cores()`（2026-10-03 起门禁口径 = **物理核数**，超线程
不计入；cgroup 配额/亲和掩码仍是硬信号）。`Makefile` 的 `NPROC` 与 `tools/task.py` 直接传
同一个口径算出的**数字**（不依赖这里的 `auto`），`auto` 只是给手工命令行留的同义入口。
"""

from __future__ import annotations

import argparse
import importlib
import os
import pickle
import select
import struct
import sys
import traceback
import warnings
from collections import deque
from typing import Any

import pytest

#: 帧头：4 字节大端长度。子↔父两个方向都用它（pickle 流）。
_HDR = struct.Struct("!I")
_CHUNK = 1 << 16

#: master 在 fork **之前**填好；子进程经 COW 继承后才可能读到（nodeid → Item）。
_ITEMS: dict[str, pytest.Item] = {}
#: 子进程内当前用例产生的报告（由本模块的 pytest_runtest_logreport 追加）。
_REPORTS: list[pytest.TestReport] = []
#: 仅子进程为真。让本模块的 hook 实现分流（并让 master 侧重放时不再收集）。
_IN_CHILD = False
#: master 自己的 pid（子进程用它做 PR_SET_PDEATHSIG 的竞态复核）。
_MASTER_PID = 0


# --------------------------------------------------------------------------------------
# 选项 / 配置校验
# --------------------------------------------------------------------------------------


#: `--forkdist` 的「机器规模」哨兵（解析后的值存在 config 上，见 `_resolve_workers`）。
_AUTO = 0
#: 解析后的 worker 数挂在 config 上的属性名（`pytest_runtestloop` 与测试都读它）。
_WORKERS_ATTR = "forkdist_workers"


def _worker_count_option(value: str) -> int:
    """`--forkdist` 的参数：正整数，或 `auto`（= 机器核数，同 xdist 的 `-n auto` 直觉）。

    `auto` 不是偷懒：`nn-training/Makefile` 的 `NPROC ?= auto` 与 `tools/task.py` 的 `-n auto`
    本来就是这个语义，换了分发器不该把这个旋钮的打法也换掉（`make test NPROC=8` 仍要能用）。
    """
    text = value.strip().lower()
    if text == "auto":
        return _AUTO
    try:
        n = int(text)
    except ValueError as e:  # argparse 会把它变成「invalid value」的干净报错
        raise argparse.ArgumentTypeError(f"--forkdist 只接受正整数或 auto，收到 {value!r}") from e
    if n < 1:
        raise argparse.ArgumentTypeError(f"--forkdist 的 worker 数必须 ≥ 1，收到 {value!r}")
    return n


def _resolve_workers(raw: int) -> int:
    """把 `auto` 解析成核数。**只看 `common.platform_utils.physical_cores()`**。

    为什么是 physical_cores（2026-10-03 决议）：门禁/本地 worker 池的口径统一按**物理核数**
    —— 它们开的是「每核一个重型进程」，超线程 sibling 共享执行单元与 L1/L2，加 worker 只涨
    内存与切换（实测 16 worker 把 Windows 提交上限顶穿 ⇒ 假红）。且**不能**用 `os.cpu_count()`：
    容器里它报的是**宿主机**核数（2026-09-25 云机卡死那笔账 —— Kaggle 224 vs cgroup 配额 96），
    而 `physical_cores()` 内部把 cgroup 配额/亲和掩码也取小。拿不到就不猜（拒绝而非静默退回错数）。
    """
    if raw != _AUTO:
        return raw
    try:
        from common.platform_utils import physical_cores
    except ImportError as e:  # 插件被脱离仓库使用（cwd 不在 nn-training）
        raise pytest.UsageError(
            "--forkdist auto 需要 nn-training/common/platform_utils.py::physical_cores()"
            "（请从 nn-training 目录运行 pytest，或直接给一个明确的正整数）"
        ) from e
    return int(physical_cores())


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("forkdist", "收集一次后 fork 出 worker（POSIX-only）")
    group.addoption(
        "--forkdist",
        action="store",
        type=_worker_count_option,
        default=None,
        metavar="N|auto",
        help="收集一次后 fork N 个 worker 并动态派发（auto = 机器核数；Windows 无 os.fork）",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _MASTER_PID
    raw = config.getoption("forkdist")
    if raw is None:
        return
    if not hasattr(os, "fork"):
        raise pytest.UsageError(
            "--forkdist 需要 os.fork（Windows 上不可用）——Windows 请继续用 xdist 的 -n"
        )
    if getattr(config.option, "numprocesses", None):
        raise pytest.UsageError(
            "--forkdist 与 xdist 的 -n 互斥：两者都接管 pytest_runtestloop，同时开会打起来"
        )
    n = _resolve_workers(int(raw))
    if n < 1:
        raise pytest.UsageError(f"--forkdist 的 worker 数必须 ≥ 1，收到 {n}")
    # 解析后的值（可能是 auto → 核数）挂上去，`pytest_runtestloop` 与测试都读它。
    setattr(config, _WORKERS_ATTR, n)
    _MASTER_PID = os.getpid()


# --------------------------------------------------------------------------------------
# master 侧：接管 runtestloop（收集已经发生过，这里只负责 fork + 派发 + 重放）
# --------------------------------------------------------------------------------------


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session) -> bool | None:
    """master 的接管点。返回 True = 吞掉 pytest 默认的串行循环。

    退化情形（没开 `--forkdist` / 只有一条用例 / `--collect-only`）返回 None，让默认循环跑
    ——「开了但不并行」不该有任何行为差异。
    """
    n = getattr(session.config, _WORKERS_ATTR, None)
    if not n or n < 1 or session.config.option.collectonly:
        return None
    return _run_forked(session, int(n))


class _Worker:
    """一个 fork 出来的 worker 的 master 侧簿记。"""

    __slots__ = ("buf", "cmd_fd", "current", "index", "next_nodeid", "pid", "res_fd")

    def __init__(self, index: int, pid: int, cmd_fd: int, res_fd: int) -> None:
        self.index = index
        self.pid = pid
        self.cmd_fd = cmd_fd
        self.res_fd = res_fd
        self.buf = b""
        #: 正在跑的 nodeid（None = 空闲）；跑完还没上报就死了 = 丢了这条用例。
        self.current: str | None = None
        #: 已从队列里**预留**给它的下一条（xdist 的 nextitem 语义）。
        self.next_nodeid: str | None = None


def _run_forked(session: pytest.Session, n: int) -> bool | None:
    config = session.config
    items = list(session.items)
    if len(items) < 2:
        return None  # 没有可并行的东西，交给默认循环（也免得付 fork 的固定成本）

    _ITEMS.clear()
    _ITEMS.update((item.nodeid, item) for item in items)

    workers = _spawn(session, min(n, len(items)))
    pending: deque[str] = deque(item.nodeid for item in items)

    # 初次派发：每条 worker 给一条，并顺手预留它的下一条（见模块 docstring 的 nextitem 说明）。
    for w in workers:
        cur = pending.popleft() if pending else None
        if cur is None:
            _close_cmd(w)
            continue
        w.next_nodeid = pending.popleft() if pending else None
        _send(w.cmd_fd, {"nodeid": cur, "next": w.next_nodeid})
        w.current = cur

    stopping = False
    readers: dict[int, _Worker] = {w.res_fd: w for w in workers}
    try:
        while readers:
            ready, _, _ = select.select(list(readers), [], [], 1.0)
            for fd in ready:
                w = readers[fd]
                chunk = os.read(fd, _CHUNK)
                if not chunk:  # EOF：worker 收工（或死了）
                    del readers[fd]
                    continue
                for frame in _feed(w, chunk):
                    if frame["kind"] == "warning":
                        _replay_warning(config, frame)
                        continue
                    _replay_item(config, frame)
                    w.current = None
                    if stopping or session.shouldfail or session.shouldstop:
                        if not stopping:
                            stopping = True
                            print(
                                f"[forkdist] {session.shouldfail or session.shouldstop}"
                                " —— 停止派发（剩余用例未跑）",
                                flush=True,
                            )
                        _close_cmd(w)  # 收工：worker 读完 EOF 就退出
                        continue
                    if w.next_nodeid is None:
                        _close_cmd(w)
                        continue
                    cur, w.next_nodeid = w.next_nodeid, (pending.popleft() if pending else None)
                    _send(w.cmd_fd, {"nodeid": cur, "next": w.next_nodeid})
                    w.current = cur
    finally:
        # 先关命令管道：worker 读到 EOF 才会退出（否则 waitpid 会死等，且把
        # 重放阶段的异常一起吞掉——2026-09-29 实测踩到，见 §49）。
        for w in workers:
            _close_cmd(w)
        _reap(session, workers)
    return True


def _spawn(session: pytest.Session, n: int) -> list[_Worker]:
    workers: list[_Worker] = []
    try:
        for i in range(n):
            cmd_r, cmd_w = os.pipe()
            res_r, res_w = os.pipe()
            pid = os.fork()  # type: ignore[attr-defined]  # POSIX-only：win32 stubs 里没这个符号（真跑到这里前已在入口拒）
            if pid == 0:
                # ---- 子进程分支：这里没有返回值，_child_main 以 os._exit 结束 ----
                os.close(cmd_w)
                os.close(res_r)
                _child_main(session, cmd_r, res_w)
                os._exit(70)  # 防御性兜底（_child_main 正常路径不返回）
            os.close(cmd_r)
            os.close(res_w)
            workers.append(_Worker(i, pid, cmd_w, res_r))
    except BaseException:
        for w in workers:  # fork 中途失败：别把已经起来的 worker 留成孤儿
            _close_cmd(w)
            os.close(w.res_fd)
        raise
    return workers


def _feed(w: _Worker, chunk: bytes) -> list[dict[str, Any]]:
    """按 4 字节长度前缀切帧（一个 read 可能拿到半帧或好几帧）。"""
    w.buf += chunk
    out: list[dict[str, Any]] = []
    while len(w.buf) >= _HDR.size:
        (length,) = _HDR.unpack_from(w.buf, 0)
        if len(w.buf) < _HDR.size + length:
            break
        blob = w.buf[_HDR.size : _HDR.size + length]
        w.buf = w.buf[_HDR.size + length :]
        out.append(pickle.loads(blob))
    return out


def _send(fd: int, payload: Any) -> None:
    blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    blob = _HDR.pack(len(blob)) + blob
    view = memoryview(blob)
    while view:  # 管道不保证一次写完
        view = view[os.write(fd, view) :]


def _close_cmd(w: _Worker) -> None:
    if w.cmd_fd >= 0:
        try:
            os.close(w.cmd_fd)
        except OSError:
            pass
        w.cmd_fd = -1


def _reap(session: pytest.Session, workers: list[_Worker]) -> None:
    """等 worker 全退，把「派了但没上报」的用例按失败补上（不许静默少跑）。"""
    for w in workers:
        try:
            _, status = os.waitpid(w.pid, 0)
        except ChildProcessError:
            status = 0
        try:
            os.close(w.res_fd)
        except OSError:
            pass
        if w.current is not None:
            _replay_item(
                session.config,
                {
                    "nodeid": w.current,
                    "reports": [],
                    "lost": f"worker {w.index} (pid {w.pid}) 在跑这条时退出（exit={status >> 8}）",
                },
            )
        elif status != 0:
            print(
                f"[forkdist] worker {w.index} (pid {w.pid}) 非正常退出：exit={status >> 8}",
                file=sys.stderr,
                flush=True,
            )


# --------------------------------------------------------------------------------------
# 结果重放（master）：把子进程的报告还原成 master 自己的报告事件
# --------------------------------------------------------------------------------------


def _replay_item(config: pytest.Config, frame: dict[str, Any]) -> None:
    nodeid = frame["nodeid"]
    lost = frame.get("lost")
    if lost:  # 用例被吞：合成一条失败报告，绝不让它静默消失
        location = _location_of(nodeid)
        config.hook.pytest_runtest_logstart(nodeid=nodeid, location=location)
        config.hook.pytest_runtest_logreport(
            report=pytest.TestReport(
                nodeid=nodeid,
                location=location,
                keywords={},
                outcome="failed",
                longrepr=f"[forkdist] {lost}",
                when="call",
                duration=0.0,
            )
        )
        config.hook.pytest_runtest_logfinish(nodeid=nodeid, location=location)
        return
    for data in frame["reports"]:
        report = config.hook.pytest_report_from_serializable(config=config, data=data)
        if report.when == "setup":
            config.hook.pytest_runtest_logstart(nodeid=report.nodeid, location=report.location)
        config.hook.pytest_runtest_logreport(report=report)
        if report.when == "teardown":
            config.hook.pytest_runtest_logfinish(nodeid=report.nodeid, location=report.location)


def _location_of(nodeid: str) -> tuple[str, int | None, str]:
    item = _ITEMS.get(nodeid)
    if item is not None:
        return tuple(item.location)  # type: ignore[return-value]
    return (nodeid.split("::")[0], None, "")


def _replay_warning(config: pytest.Config, frame: dict[str, Any]) -> None:
    """转发子进程的告警给 master 的 reporter（否则 warnings summary 会整个消失）。

    `pytest_warning_recorded` 是 historic hook；master 侧重建一个等价的 `WarningMessage`
    再调一次即可（`TerminalReporter` 只读 message/category/filename/lineno/line）。
    """
    category: type[Warning] = Warning
    mod, name = frame.get("category") or (None, None)
    if mod and name:
        try:
            category = getattr(importlib.import_module(mod), name)
        except (ImportError, AttributeError):
            category = Warning
    try:
        message = warnings.WarningMessage(
            frame["message"], category, frame["filename"], frame["lineno"], None, None
        )
    except (TypeError, ValueError):  # 构造失败也不该把门禁弄红
        return
    # ⚠ `pytest_warning_recorded` 是 **historic** hook：直接调用会被 pluggy 断言拒绝
    #   （"Cannot directly call a historic hook"），必须走 call_historic。
    config.hook.pytest_warning_recorded.call_historic(
        kwargs={
            "warning_message": message,
            "when": frame["when"],
            "nodeid": frame["nodeid"],
            "location": frame.get("location"),
        }
    )


# --------------------------------------------------------------------------------------
# 子进程侧
# --------------------------------------------------------------------------------------


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """只在子进程里干活：把本进程当前用例的报告攒起来，供 master 重放。"""
    if _IN_CHILD:
        _REPORTS.append(report)


def pytest_warning_recorded(
    warning_message: warnings.WarningMessage, when: str, nodeid: str, location: Any
) -> None:
    if not _IN_CHILD:
        return
    try:
        category: tuple[str, str] | None = (
            type(warning_message.message).__module__,
            type(warning_message.message).__name__,
        )
        _send(
            _CHILD_OUT,
            {
                "kind": "warning",
                "message": str(warning_message.message),
                "category": category,
                "filename": warning_message.filename,
                "lineno": warning_message.lineno,
                "when": when,
                "nodeid": nodeid,
                "location": location,
            },
        )
    except (OSError, pickle.PicklingError, TypeError, ValueError):
        pass  # 告警转发失败不算测试失败


#: 子进程侧的结果管道 fd（fork 后由 _child_main 赋值）。
_CHILD_OUT = -1


def _child_main(session: pytest.Session, cmd_r: int, res_w: int) -> None:
    """子进程主函数：**不返回**（以 os._exit 结束，不跑 pytest 自己的收尾路径）。"""
    global _IN_CHILD, _CHILD_OUT
    _IN_CHILD = True
    _CHILD_OUT = res_w
    config = session.config
    _adopt_worker_identity(config)
    _mute_terminal(config)
    _reset_global_capture(config)
    _die_with_parent()
    status = 0
    try:
        _child_loop(session, cmd_r, res_w)
    except BaseException:  # 子进程的异常必须变成退出码 + 栈
        traceback.print_exc()
        status = 3
    finally:
        # ⚠ 必须跑：session 级夹具的 finalizer 在这里收（关 FakeServer / 清临时目录）；
        # tests/conftest.py 的 pytest_sessionfinish 也把**本进程**跑过的通过用例的临时目录
        # 入队清理——不调它 = 每轮全量多堆几百个临时目录（2026-09-14 那笔账）。
        try:
            config.hook.pytest_sessionfinish(session=session, exitstatus=status)
        except BaseException:
            traceback.print_exc()
            status = 3
        try:
            os.close(res_w)
        except OSError:
            pass
    os._exit(status)


def _reset_global_capture(config: pytest.Config) -> None:
    """子进程重建自己的全局捕获（**不做就会读到半个 UTF-8 字符**）。

    pytest 在 session 开始（= fork 之前）就打开了 fd 捕获用的临时文件。`os.fork()` 让子进程
    与 master、以及彼此**共享同一个 open file description** —— 而「文件偏移」就住在那份描述里。
    `_pytest/capture.py` 每次出报告都会 `lseek(tmpfile, 0)` + read 一遍全局捕获（就是报告里那个
    "Captured stdout" 段落），于是 12 个子进程互相把对方的偏移挪走 ⇒ 谁都会读到**别人写的半截
    字符**（实测：`UnicodeDecodeError: byte 0x96 in position 0`，并且被 pytest 归因到「当前这条
    用例 setup 失败」——真正出错的是前一条用例的夹具收尾，极难从栈上看出来，2026-09-29 §49）。

    修法：停掉继承来的那个，在子进程里重开一份（临时文件全新、偏移独立）。语义上也是对的：
    子进程本来就该像独立 worker 那样拥有自己的捕获。
    """
    capturemanager = config.pluginmanager.getplugin("capturemanager")
    if capturemanager is None:
        return
    try:
        capturemanager.stop_global_capturing()
        capturemanager.start_global_capturing()
    except (AssertionError, OSError, ValueError):  # 捕获没开（-s）等：保持现状即可
        pass


def _child_loop(session: pytest.Session, cmd_r: int, res_w: int) -> None:
    config = session.config
    buf = b""
    while True:
        chunk = os.read(cmd_r, _CHUNK)
        if not chunk:  # master 关掉了命令管道 = 收工（或 master 死了 → 不留孤儿）
            break
        buf += chunk
        while len(buf) >= _HDR.size:
            (length,) = _HDR.unpack_from(buf, 0)
            if len(buf) < _HDR.size + length:
                break
            cmd = pickle.loads(buf[_HDR.size : _HDR.size + length])
            buf = buf[_HDR.size + length :]
            _run_one(session, config, res_w, cmd)
            if session.shouldfail or session.shouldstop:  # -x / --maxfail
                return


def _run_one(
    session: pytest.Session, config: pytest.Config, res_w: int, cmd: dict[str, Any]
) -> None:
    nodeid = cmd["nodeid"]
    item = _ITEMS.get(nodeid)
    _REPORTS.clear()
    if item is None:
        _send(
            res_w,
            {"kind": "item", "nodeid": nodeid, "reports": [], "lost": "子进程找不到这条用例"},
        )
        return
    nextitem = _ITEMS.get(cmd["next"]) if cmd.get("next") else None
    # 与 pytest 默认循环 / xdist 的 worker 同一入口：pytest 自己派发 setup/call/teardown ，
    # 本模块的 pytest_runtest_logreport 把它们攒进 _REPORTS。
    config.hook.pytest_runtest_protocol(item=item, nextitem=nextitem)
    _send(
        res_w,
        {
            "kind": "item",
            "nodeid": nodeid,
            "reports": [
                config.hook.pytest_report_to_serializable(config=config, report=r) for r in _REPORTS
            ],
        },
    )


def _adopt_worker_identity(config: pytest.Config) -> None:
    """把子进程伪装成 xdist 语义下的 worker（pytest 生态的既有约定）。

    `_pytest/cacheprovider.py` / `junitxml.py` / `stepwise.py` / `xdist/plugin.py` 都用
    `hasattr(config, "workerinput")` 判定「我是不是 worker」，并据此跳过「只能由 master 做」
    的收尾（如写 `lastfailed` 缓存）——12 个进程抢写同一个文件显然不该发生。
    xdist 的 `worker_id` 类夹具也因此拿到 `forkdist-N` 而不是 `master`。
    """
    config.workerinput = {  # type: ignore[attr-defined]
        "workerid": f"forkdist-{os.getpid()}",
        "testrunuid": os.environ.get("PYTEST_XDIST_TESTRUNUID", ""),
        "workercount": 0,
        "mainargv": [],
    }


def _mute_terminal(config: pytest.Config) -> None:
    """摘掉子进程里的 TerminalReporter。

    报告经管道回 master 重放，子进程若自己再打一遍，12 份进度点/12 个汇总会交错打在共享
    stdout 上。摘掉它不影响测试本身（capsys/capfd 由 capture 插件负责，与 reporter 无关），
    也不影响失败信息的完整性（失败细节在报告里，由 master 打印）。
    """
    pluginmanager = config.pluginmanager
    reporter = pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        pluginmanager.unregister(reporter)


def _die_with_parent() -> None:
    """Linux：master 一死就把自己 SIGKILL。

    `nn-wall.py` 在 POSIX 上只能杀进程树的**根**（`p.kill()`；连树杀是 Windows 的
    taskkill /T）——没有这一步，master 被墙钟杀掉后，卡在测试里的 worker 会变成孤儿继续烧
    CPU。prctl 失败（macOS / 无 libc）时静默跳过：这只是止损，不是正确性依赖。
    """
    try:
        import ctypes
        import signal

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.prctl(1, signal.SIGKILL) != 0:  # type: ignore[attr-defined]  # PR_SET_PDEATHSIG = 1；SIGKILL 同理只在 POSIX stubs 里
            return
        if os.getppid() != _MASTER_PID:  # fork 与 prctl 之间父进程已经退出的竞态
            os._exit(70)
    except (OSError, AttributeError, ValueError):
        pass
