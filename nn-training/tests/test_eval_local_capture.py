"""test_eval_local_capture — 本机评估子进程的输出捕获契约（2026-09-22 回归测试）。

缺口：`run_local_eval_game` 的 `subprocess.run(..., text=True)` 没给 encoding —— win32 中文机
locale = gbk（cp936），而 bun 的输出带 UTF-8 字节 ⇒ 解码在 `subprocess` 的 **reader 线程**
里抛 UnicodeDecodeError：
  ① 父进程日志被 `Exception in thread ... _readerthread` traceback 刷屏（evalA 实测 65 次/100 局）；
  ② **captured stdout/stderr 直接丢成 None**（异常死在读线程，`communicate` 不重抛）
     ⇒ 失败时的 `RuntimeError(f"rc={rc} ({stderr[-160:]})")` 只剩 rc，诊断信息全没。

本文件用**真子进程**写不可解码字节复现（不是读源码断言）：解码必须成功、输出必须还在。
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

import rl.eval_local as eval_local
from platform_utils import KILL_REAP_SEC
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.eval_local import run_eval_runner_capture

#: 故意混字节：UTF-8 的中文 + 单独的 0xaf（在 gbk 与 utf-8 下都不是合法序列）。
_CHILD = (
    "import sys\n"
    "sys.stdout.buffer.write(b'ok-\\xe4\\xb8\\xad\\xe6\\x96\\x87-\\xaf\\n')\n"
    "sys.stderr.buffer.write(b'boom-\\xe4\\xb8\\xad\\xe6\\x96\\x87-\\xaf\\n')\n"
    "sys.exit(3)\n"
)


def test_capture_decodes_utf8_and_keeps_output() -> None:
    """回归：不可解码字节不得变成「输出丢成 None」+ 日志刷屏。"""
    proc = run_eval_runner_capture([sys.executable, "-c", _CHILD], 30.0)

    assert proc.returncode == 3
    # ① 输出必须还在（旧实现两条都是 None：异常死在 reader 线程）
    assert proc.stdout is not None and proc.stderr is not None
    assert "ok-" in proc.stdout
    assert "boom-" in proc.stderr
    # ② 坏字节走 errors="replace"，不抛（UTF-8 的部分正常解出中文）
    assert "中文" in proc.stdout
    assert "\ufffd" in proc.stderr  # 0xaf 变成替换符，而不是异常


def test_capture_helper_is_what_the_runner_uses() -> None:
    """接线：本机局的输出捕获必须走这个 helper（否则 encoding 又悄悄丢了）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "rl" / "eval_local.py").read_text(
        encoding="utf-8"
    )
    # 调用点带 cwd（云机离线评估要跑在 TS 树根上）+ 慢局告警的身份/落点（2026-09-22）
    assert "proc = run_eval_runner_capture(" in src
    assert "label=lab" in src
    assert "lab = game_watch.game_label(stage, seed)" in src
    # 长驻池优先、一次性兜底（`docs/nn/runtime-opt.md` §22.7）：顺序在源码里就得看得见
    # （池只可能更快：它拿不到就当场回退下面这条路，行为与池不存在时相同）。
    assert src.index("pool.try_capture(") < src.index("proc = run_eval_runner_capture(")
    # 看门狗口径一律走 game_watch 的模块属性（import 常量 = 第二份绑定，patch 不到）
    assert "from remote.game_watch import" not in src
    # 不许再有裸的 `subprocess.run(capture_output=True, text=True, timeout=...)`（那正是缺陷形态）
    assert "capture_output=True,\n        text=True,\n        timeout=timeout_sec" not in src
    assert "subprocess.run(\n        cmd," not in src


def test_capture_is_single_subprocess_invocation() -> None:
    """helper 本身只 spawn 一次（别把重试/缓冲塞进来）。"""
    proc = run_eval_runner_capture([sys.executable, "-c", "print('hi')"], 30.0)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.stdout.strip() == "hi"


def test_capture_watchdog_warns_slow_game_by_identity(monkeypatch) -> None:
    """慢局（卡住期间）就点名告警——不是等硬顶到了才知道某一局有问题（2026-09-22）。"""
    from remote import game_watch

    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    monkeypatch.setattr(game_watch, "SLOW_GAME_WARN_SEC", 0.05)
    msgs: list[str] = []
    # 只「慢」不「卡」：0.4s 就出结果，但已越过被 monkeypatch 成 0.05s 的软告警线
    child = (
        "import sys, time\n"
        "sys.stdout.write('partial'); sys.stdout.flush()\n"
        "time.sleep(0.4)\n"
    )
    out = run_eval_runner_capture(
        [sys.executable, "-c", child], 30.0, label="s2/d9", log_fn=msgs.append
    )
    assert out.returncode == 0 and "partial" in out.stdout
    assert any("异常慢" in m and "s2/d9" in m for m in msgs), msgs


def _python_launch_sec() -> float:
    """这台机器**此刻**起一个 python 子进程需要多久 —— 硬顶必须活得比它长。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：硬顶窗口里唯一不可控的一段就是子进程的
    「启动 → 写出第一行」——把它**测出来**再定硬顶，用例就不再依赖「机器必须够快」这个假设。
    （原来写死 0.3s：满载时单是 CPython 冷启动就能吃掉它 ⇒ 杀得太早 ⇒ 尾巴当然是空的，
    用例红在环境上而不是契约上。用与真实子进程同一组旗标（`-S`）量，才是同尺子。）
    """
    t0 = time.time()
    subprocess.run(
        [sys.executable, "-S", "-c", "pass"], timeout=120, check=False, **_POPEN_NO_WINDOW
    )
    return time.time() - t0


def test_capture_hard_cap_kills_and_keeps_output(monkeypatch) -> None:
    """硬顶：kill 子进程、抛 TimeoutExpired，但**捕获到的尾巴要留着**（诊断不被超时吃掉）。

    硬顶 = 实测冷启动 ×3（下限 0.3s）：倍数只是护栏、不是同步手段 —— 要钉的契约是
    「写出来的尾巴必须留着」，不是「机器得多快」。×3 而非更大：16 核满载时实测单次冷启动
    ~1.9s ⇒ 硬顶 ~5.7s，仍在单测耗时预算（>10s 报错）以内。
    """
    from remote import game_watch

    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    launch = _python_launch_sec()
    cap = max(0.3, launch * 3)
    child = (
        "import sys, time\n"
        "sys.stdout.write('partial'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired) as e:
        run_eval_runner_capture([sys.executable, "-S", "-c", child], cap, label="s2/d9")
    assert "partial" in (e.value.output or ""), (e.value.output, f"cap={cap:.2f}s launch={launch:.2f}s")


class _UnreapablePopen:
    """SIGKILL 之后也回收不了的子进程替身（D 状态没法在单测里造出来，同 `test_remote_iter`）。

    契约只有「pid / communicate(timeout) / kill / poll」四条（`platform_utils` 就是按 Any 写的）。
    pid 取一个不可能存在的值 ⇒ `killpg` 走 ESRCH 分支，**绝不会**打到自己这个进程组。
    """

    #: 本用例造过的替身（收尾时统一「让它退出」，把那本账收干净）。
    instances: list[_UnreapablePopen] = []

    def __init__(self, cmd: list[str], **_kw: object) -> None:
        self.args = [str(a) for a in cmd]
        self.pid = 2**22 + 17
        self.returncode: int | None = None
        self.bounded_waits: list[float] = []
        self.unbounded_waits = 0
        self.killed = 0
        _UnreapablePopen.instances.append(self)

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if timeout is None:
            self.unbounded_waits += 1
            raise AssertionError("无上限的 communicate —— 那正是「卡死机器半天」的形态")
        self.bounded_waits.append(float(timeout))
        raise subprocess.TimeoutExpired(self.args, timeout)

    def kill(self) -> None:
        self.killed += 1

    def poll(self) -> int | None:
        return self.returncode


def test_capture_distinguishes_an_unreapable_child_from_a_plain_timeout(monkeypatch) -> None:
    """★ 收不了尸要**分类**：机器级停滞（`UnreapableChildError`）必须与普通超时分开。

    为什么这件事值一个用例（2026-09-25 用户口径：eval 腿的机器级停滞也要轮内重投、只补没评的局）：
      * 普通超时 = 这一局慢（内容/负载），它有它自己的重试与「这一轮先记一笔失败」的出路；
      * 收不了尸 = **机器**卡住（SIGKILL 之后进程还在 D 状态，旧写者可能仍活着）⇒ 不能原地
        重跑同一目录（半截/交错的 `_eval_report.json`），只能交回整轮重投。
    两者混成一个 `TimeoutExpired`，调用方就只能二选一：要么把机器问题记成内容失败，要么把
    内容失败当成机器问题无限重投 —— 两条都是错的。
    """
    import platform_utils as pu
    from remote import game_watch
    from remote.protocol import UnreapableChildError

    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    _UnreapablePopen.instances.clear()
    monkeypatch.setattr(eval_local.subprocess, "Popen", _UnreapablePopen)
    msgs: list[str] = []
    try:
        with pytest.raises(UnreapableChildError) as e:
            run_eval_runner_capture(
                [sys.executable, "-c", "pass"], 0.0, label="s5/d3", log_fn=msgs.append
            )
        p = _UnreapablePopen.instances[-1]
        # 回收必须有上限（旧形态是裸 `communicate()` = 线程永远不返回）：轮询那次 + 收尾那次
        assert p.unbounded_waits == 0
        assert p.bounded_waits == [game_watch.GAME_POLL_SEC, KILL_REAP_SEC], p.bounded_waits
        assert p.killed >= 1, "杀不掉也要先把信号递出去（进程组那一条）"
        assert "回收不了" in str(e.value) and "s5/d3" in str(e.value), e.value
        assert any("单局子进程杀不掉" in m and "s5/d3" in m for m in msgs), msgs
        assert pu.unreaped_count() == 1, "收不了的要记账（之后非阻塞地再碰一次）"
    finally:
        for inst in _UnreapablePopen.instances:
            inst.returncode = 1  # 它后来退出了
        pu.sweep_unreaped()
        assert pu.unreaped_count() == 0


def test_capture_plain_timeout_is_still_a_timeout(monkeypatch) -> None:
    """对照：**收得了尸**的超时仍然是 TimeoutExpired（分类不能把这一档也吞进来）。"""
    from remote import game_watch

    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    launch = _python_launch_sec()
    cap = max(0.3, launch * 3)
    child = "import time; time.sleep(30)\n"
    with pytest.raises(subprocess.TimeoutExpired):
        run_eval_runner_capture([sys.executable, "-S", "-c", child], cap, label="s2/d9")
