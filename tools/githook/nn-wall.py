"""nn-wall.py — 进程外**墙钟 watchdog**：给任意命令一个墙钟硬上限，超限连进程树强杀。

2026-09-15（为什么必须有它）：pytest-timeout（--timeout，线程型）只能兜 **Python
字节码级**挂起——无法打断 C 层原生阻塞（subprocess.wait / 文件锁 / socket recv 等）。
实测：`test_rollout_volume.py` 在编码 agent 沙箱下卡在 #42-43 数分钟、全局 60s 超时
**没有触发**（进程树浅、CPU 近零 = 真·原生等待）。真正可靠的墙钟保护 = 进程外进程：
超时即 `taskkill /T` 连树杀（Windows）/ SIGKILL（POSIX），子进程无论卡在哪层都能被
终结，且能打印"被墙钟杀掉"的判定。

用法（cwd 自定；`--wall` 缺省读 NN_WALL_S，默认 480s）：
  python -S tools/githook/nn-wall.py --wall 480 -- <cmd...>
  NN_WALL_S=480 python -S tools/githook/nn-wall.py -- <cmd...>
正常结束透传退出码；超时返回 **124**（timeout 惯例）并在 stderr 打 marker。
调用方（nn-py-safe.sh / 后续 task/gate）据此可区分「测试失败」与「墙钟止损」。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

DEFAULT_WALL = float(os.environ.get("NN_WALL_S", "480"))


def kill_tree(p: subprocess.Popen) -> None:
    """连进程树强杀（Windows taskkill /T；POSIX kill 组主进程）。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.kill()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="墙钟 watchdog（超限连树杀）")
    ap.add_argument("--wall", type=float, default=DEFAULT_WALL, help="墙钟上限(秒)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的命令与参数")
    a = ap.parse_args(argv)
    # 已知坑：argparse 的 REMAINDER 会把分隔符 `--` 本身留在 cmd[0] ⇒ Popen 找不到
    # 可执行文件（WinError 2）。标准做法是显式剥掉前导 `--`。
    if a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    if not a.cmd:
        print("✗ nn-wall: no command after --", file=sys.stderr)
        return 2
    p = subprocess.Popen(a.cmd)
    # 2026-11-02：原来是「`poll()` + `sleep(0.25)`」轮询 —— 子进程跑过 250ms 线的那一次，
    # 退出后还要白等到下一个 tick 才发现（平均 125ms、最坏 250ms），而 nn-py-safe.sh 的**每一次**
    # `-m pytest` 都包在本进程里（门禁 master 一发、每个子进程测试各一发）。`wait(timeout=)`
    # 内部就是 `waitpid` 循环（次次 tick 25ms）⇒ 子进程一退就返，墙钟语义一字未变。
    try:
        rc = p.wait(timeout=a.wall)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            p.wait(timeout=10)  # 收尸：不让被杀的子进程变僵尸（原实现直接 return）
        except subprocess.TimeoutExpired:
            pass
        print(
            f"✗ nn-wall: wall {a.wall:g}s exceeded — killed pid {p.pid} tree (exit 124)",
            file=sys.stderr,
        )
        return 124
    return rc or 0


if __name__ == "__main__":
    raise SystemExit(main())
