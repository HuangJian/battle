"""test_platform_utils_proc.py — 子进程回收原语（`platform_utils`: popen_own_group / kill_process_tree /
reap_bounded / keep_unreaped / sweep_unreaped）。

为什么单独测这一层（2026-09-25 云机二次取证「rollout 卡死机器半天」）：训练轮里 92 条线程同时在
跑，其中任一条卡在**无上限**的 syscall 上，整轮就再也收不齐（`as_completed` 永远等不到它），
而日志里只看得见一行「已结算 N 局」不再动。现场读数：两条线程各卡 ~890s（超时行里的 elapsed 是
kill **之前**算的，所以它照旧写着「5.0s」），同一时刻进度冻在 5s 的 270/336。

三件事必须钉死，每一条都对应一个真实能发生的坏结局：
  * **SIGKILL 要打整个进程组**：`bun` 自己还会带子进程（编译缓存 worker/子工具），只杀父进程会
    留下孤儿继续吃 CPU/内存 ⇒ 机器越跑越卡，而日志里只看得见「我们 kill 过它」；
  * **回收必须有上限**：子进程卡在不可中断的 IO 里（D 状态）时 SIGKILL 要等那个系统调用返回才
    生效，`wait()` 就跟着永远不返回；
  * **杀不掉/收不了要记账**：留着之后非阻塞地再碰一次（僵尸占着 pid/句柄，机器本来就被一堆
    进程压着）。

真进程优先：只有「不可中断的 IO」这一态没法在单测里造出来，它由 `test_remote_iter.py` 的
替身对象覆盖。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import platform_utils as plat


def _spawn(code: str) -> subprocess.Popen:
    """起一个 python 子进程（与生产同一条口径：`popen_own_group`）。"""
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **plat.popen_own_group(),
    )


# ------------------------------------------------------------------ popen_own_group


def test_popen_own_group_is_a_session_only_on_posix() -> None:
    """POSIX 上自带会话/进程组（才敢 killpg）；Windows 上没有这套语义（退回单进程 kill）。"""
    kw = plat.popen_own_group()
    if os.name == "posix":
        assert kw["start_new_session"] is True
    else:
        assert "start_new_session" not in kw
    # 无窗口 flags 必须原样保留（是合并，不是覆盖）
    assert kw == plat.popen_kwargs(**({"start_new_session": True} if os.name == "posix" else {}))


def test_popen_own_group_keeps_no_window_flags_on_this_platform() -> None:
    """无窗口 flags 不许被吞掉（Windows 上黑窗弹窗抢焦点是老问题）。"""
    assert plat.popen_own_group() == plat.popen_kwargs(
        **({"start_new_session": True} if os.name == "posix" else {})
    )
    assert plat.popen_own_group().get("creationflags") == plat.POPEN_NO_WINDOW.get("creationflags")


# ------------------------------------------------------------------ kill_process_tree


@pytest.mark.skipif(os.name != "posix", reason="进程组语义是 POSIX 的（Windows 退回单进程 kill）")
def test_kill_process_tree_takes_the_grandchild_with_it() -> None:
    """★ 杀人要杀全家：子进程自己带的子进程不得变成孤儿。

    判据用**事件**而不是轮询：孙进程继承父进程的 stdout 管道，所以那根管读到 EOF ⇔ 两个写者都
    没了。只杀父进程时管道永远有人握着 ⇒ EOF 不来 ⇒ 读线程还活着 ⇒ 用例红。
    """
    code = (
        "import subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])\n"
        "print(kid.pid, flush=True)\n"
        "time.sleep(3600)\n"
    )
    parent = _spawn(code)
    assert parent.stdout is not None
    try:
        kid_pid = int((parent.stdout.readline() or "0 0").split()[0])
        assert kid_pid > 0
        drained = threading.Event()

        def _drain() -> None:
            for _ in parent.stdout or ():
                pass
            drained.set()

        threading.Thread(target=_drain, daemon=True).start()
        plat.kill_process_tree(parent)
        assert plat.reap_bounded(parent, 10.0), "父进程必须能在预算内被回收"
        assert drained.wait(10.0), "孙进程还活着（管道没到 EOF）——killpg 没生效，孤儿会继续吃 CPU"
    finally:
        plat.kill_process_tree(parent)
        try:
            if parent.stdout is not None:
                parent.stdout.close()
        except OSError:
            pass


def test_kill_process_tree_falls_back_to_single_process_kill_when_no_group() -> None:
    """没自带进程组时（旧调用点/取不到 pid）退回单进程 kill —— 绝不能拿 killpg 去赌自己那一组。"""

    class _NoGroup:
        pid = None  # killpg(None) 会 TypeError ⇒ 助手必须自己走到 kill()

        def __init__(self) -> None:
            self.killed = 0

        def kill(self) -> None:
            self.killed += 1

    p = _NoGroup()
    plat.kill_process_tree(p)
    assert p.killed == 1


# ------------------------------------------------------------------ reap_bounded


def test_reap_bounded_gives_up_instead_of_waiting_forever() -> None:
    """★ 回收**有上限**：还活着的子进程 ⇒ False（不返回 = 那条线程被永远按住，就是本轮的事故）。"""
    p = _spawn("import time; time.sleep(3600)")
    try:
        assert plat.reap_bounded(p, 0.2) is False
        assert p.poll() is None
    finally:
        plat.kill_process_tree(p)
    assert plat.reap_bounded(p, 10.0) is True  # kill 之后立刻能收尸
    assert p.poll() is not None


def test_reap_bounded_returns_true_for_a_child_that_already_exited() -> None:
    """已经退出的子进程：立刻 True（这条路径不该出现任何等待）。"""
    p = _spawn("pass")
    assert plat.reap_bounded(p, 10.0) is True


# ------------------------------------------------------------------ 收不了尸的记账


def test_sweep_unreaped_collects_only_dead_ones() -> None:
    """`keep_unreaped` 记账 + `sweep_unreaped` 非阻塞收尸：死的收掉，活的留着。"""
    before = plat.unreaped_count()
    live = _spawn("import time; time.sleep(3600)")
    dead = _spawn("pass")
    try:
        plat.keep_unreaped(dead)
        assert plat.reap_bounded(dead, 10.0) is True  # 真收尸（外面那本账只是记账）
        assert plat.sweep_unreaped() >= 1
        plat.keep_unreaped(live)
        plat.sweep_unreaped()
        assert plat.unreaped_count() == before + 1, "还活着的不能被当成收掉了"
        plat.kill_process_tree(live)
        assert plat.reap_bounded(live, 10.0) is True
        assert plat.sweep_unreaped() >= 1
        assert plat.unreaped_count() == before, "这一趟必须把活着的那个收干净"
    finally:
        plat.kill_process_tree(live)
        plat.sweep_unreaped()


def test_kill_reap_sec_is_one_small_positive_number() -> None:
    """那个数字是**唯一**的一份（各条腿都 import 它），而且必须小:大数字只是把「挂住」改名。"""
    assert 0 < plat.KILL_REAP_SEC <= 30.0
    src = (ROOT / "platform_utils.py").read_text(encoding="utf-8")
    assert "KILL_REAP_SEC = " in src
    for leg in ("remote/iter_rollout.py", "rl/eval_local.py"):
        text = (ROOT / leg).read_text(encoding="utf-8")
        assert "KILL_REAP_SEC" in text, leg


def test_no_leftover_bookkeeping_from_this_file() -> None:
    """收尾：本文件不该给别的用例留下「僵尸候选」（那本账是模块级共享的）。"""
    plat.sweep_unreaped()
    assert plat.unreaped_count() == 0


def test_the_helpers_are_documented_in_the_module_map() -> None:
    """四个原语要在模块 docstring 的导出清单里（那一层的地图）。"""
    doc = plat.__doc__ or ""
    for name in ("popen_own_group", "kill_process_tree", "reap_bounded", "KILL_REAP_SEC"):
        assert name in doc, name
