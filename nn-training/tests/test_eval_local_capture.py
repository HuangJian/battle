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

import pytest

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


def test_capture_hard_cap_kills_and_keeps_output(monkeypatch) -> None:
    """硬顶：kill 子进程、抛 TimeoutExpired，但**捕获到的尾巴要留着**（诊断不被超时吃掉）。"""
    from remote import game_watch

    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    child = (
        "import sys, time\n"
        "sys.stdout.write('partial'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired) as e:
        run_eval_runner_capture([sys.executable, "-c", child], 0.3, label="s2/d9")
    assert "partial" in (e.value.output or ""), e.value.output
