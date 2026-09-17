"""存活探测的 Windows 安全不变量：**绝不用 `os.kill(pid, 0)`**（那是 TerminateProcess）。

2026-09-17 修复（`train/loop_util.py::_pid_alive`）：`acquire_lock` 用它判定「锁持有者还
活着吗」——这在语义上是**只读查询**；但在 Windows 上 `os.kill(pid, 0)` 不是探测，而是
`TerminateProcess(handle, 0)`：查询会**直接把锁持有者杀掉**（最坏情况：打死正在训练的
trainer），而且 `except Exception → False` 还会把「我把它杀了」记成「它本来就是死的」，
于是陈旧锁被「清理」、双开护栏静默失效。同口径的正确写法 = `GetExitCodeProcess ==
STILL_ACTIVE`（`run_rl._runrl_pid_alive` / `remote._instance_lock._pid_alive` 一直如此）。

本文件把不变量钉在三处同源的探测上（`train.loop_util` / `run_rl` / `remote._instance_lock`）：

  A. **Windows 分支只读退出码，绝不调用 `os.kill`**（注入假 kernel32 + 监视 `os.kill`）；
  B. 语义：`STILL_ACTIVE(259)` → 活；其他退出码 / 打不开进程 / GetExitCode 失败 → 不活，
     且句柄必须 CloseHandle（不泄漏）；
  C. POSIX 分支仍走 `signal 0`（存在性探测，安全），非法 pid 与异常一律判「不活」。

Windows 分支在 Linux 上跑不了，但 `os.name` 与 `ctypes.windll` 都是**调用时**读取/取属性的，
故可在探针调用期间注入假 kernel32，把该分支跑成真实代码路径。⚠️ `os.name` 只在
`_probe_on_windows` 内部临时改、**异常也在 finally 里先还原**：否则 Python 会把
`pathlib.Path` 解析成 `WindowsPath`，pytest 自己在报错/cache 阶段就崩（实测 INTERNALERROR）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NN_ROOT))

STILL_ACTIVE = 259


# ────────────────────────── 夹具 ──────────────────────────


class _FakeK32:
    """假 kernel32：只实现被测代码真正调用的三个入口。"""

    def __init__(self, exit_code: int | None, open_ok: bool = True) -> None:
        self.exit_code = exit_code
        self.open_ok = open_ok
        self.closed: list[int] = []

    def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:  # noqa: N802
        return 1 if self.open_ok else 0

    def GetExitCodeProcess(self, _handle: int, ref: Any) -> bool:  # noqa: N802
        if self.exit_code is None:
            return False
        ref._obj.value = self.exit_code
        return True

    def CloseHandle(self, handle: int) -> None:  # noqa: N802
        self.closed.append(handle)


def _probes() -> list[tuple[str, Callable[[int], bool]]]:
    """三处同源的存活探测（懒导入：run_rl 会拉起 torch）。"""
    from remote._instance_lock import _pid_alive as instance_probe
    from run_rl import _runrl_pid_alive as runrl_probe
    from train.loop_util import _pid_alive as loop_probe

    return [
        ("train.loop_util._pid_alive", loop_probe),
        ("run_rl._runrl_pid_alive", runrl_probe),
        ("remote._instance_lock._pid_alive", instance_probe),
    ]


@contextmanager
def _windows_env(fake: _FakeK32, kills: list[tuple[int, int]]) -> Iterator[None]:
    """把当前进程伪装成 Windows（仅在 with 体内生效；退出前必定还原）。"""
    had_windll = hasattr(ctypes, "windll")
    prev_windll = getattr(ctypes, "windll", None)
    real_name, real_kill = os.name, os.kill

    def _spy(pid: int, sig: int) -> None:
        kills.append((pid, sig))
        # 修复前这里会真的 TerminateProcess；异常类型取 Exception 的父类之外无意义——
        # 用 AssertionError 即可：即便被 `except Exception` 吞掉，kills 也已记录了违规。
        raise AssertionError(f"Windows 侧禁止用 os.kill 做存活探测：pid={pid} sig={sig}")

    os.name = "nt"
    os.kill = _spy  # type: ignore[assignment]
    ctypes.windll = SimpleNamespace(kernel32=fake)  # type: ignore[attr-defined]
    try:
        yield
    finally:
        os.name = real_name
        os.kill = real_kill  # type: ignore[assignment]
        if had_windll:
            ctypes.windll = prev_windll  # type: ignore[attr-defined]
        else:
            try:
                del ctypes.windll  # type: ignore[attr-defined]
            except AttributeError:
                pass


def _probe_on_windows(
    probe: Callable[[int], bool], pid: int, fake: _FakeK32, kills: list[tuple[int, int]]
) -> tuple[bool | None, BaseException | None]:
    """在「伪 Windows」下跑一次探针，异常**在这里被吸收**（os.name 已先还原）。"""
    try:
        with _windows_env(fake, kills):
            return probe(pid), None
    except BaseException as e:
        return None, e


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=30)
    return p.pid


# ────────────────────────── A/B：Windows 分支只读退出码 ──────────────────────────


def test_windows_branch_reads_exit_code_and_never_kills() -> None:
    """STILL_ACTIVE → 活；且**一次 os.kill 都不许发**（发一发就把持有者杀了）。"""
    kills: list[tuple[int, int]] = []
    fake = _FakeK32(STILL_ACTIVE)
    for name, probe in _probes():
        value, err = _probe_on_windows(probe, os.getpid(), fake, kills)
        assert kills == [], f"{name}: Windows 分支不得调用 os.kill，实际: {kills}"
        assert err is None, f"{name}: Windows 分支不应抛异常 —— {err!r}"
        assert value is True, f"{name}: STILL_ACTIVE 应判为活着"
    assert fake.closed, "OpenProcess 的句柄必须 CloseHandle（否则句柄泄漏）"


def test_windows_branch_dead_and_error_codes() -> None:
    """已退出码 / 打不开进程 / GetExitCodeProcess 失败 → 一律判「不活」（陈旧锁总可清理）。"""
    kills: list[tuple[int, int]] = []
    for exit_code, open_ok, why in (
        (0, True, "进程已退出（exit code 0）"),
        (1, True, "进程异常退出（exit code 1）"),
        (STILL_ACTIVE, False, "打不开进程（权限/不存在）"),
        (None, True, "GetExitCodeProcess 失败"),
    ):
        for name, probe in _probes():
            value, err = _probe_on_windows(probe, 4242, _FakeK32(exit_code, open_ok=open_ok), kills)
            assert err is None, f"{name}: Windows 分支不应抛异常 —— {err!r}"
            assert value is False, f"{name}: {why} 应判为不活"
    assert kills == [], f"Windows 分支不得调用 os.kill，实际: {kills}"


# ────────────────────────── C：POSIX 分支语义不变 ──────────────────────────


def test_posix_branch_still_uses_signal_zero() -> None:
    """POSIX 上仍是 `signal 0` 存在性探测：活进程 True、已死进程/非法 pid False。"""
    real = os.name
    for name, probe in _probes():
        assert os.name == real  # 前置：上一条用例没把 os.name 留在 "nt"
        assert probe(os.getpid()) is True, f"{name}: 本进程应判活"
        assert probe(_dead_pid()) is False, f"{name}: 已退出进程应判不活"
        assert probe(0) is False, f"{name}: pid=0 应判不活（os.kill(0,0) 会误判进程组存在）"
        assert probe(-1) is False, f"{name}: 非法 pid 应判不活"


def test_stale_lock_still_cleaned_after_windows_fix(tmp_path: Path) -> None:
    """锁语义不受影响：持有者已死 → 陈旧锁被接管（修复不得把清理能力弄丢）。"""
    from train.loop_util import acquire_lock, cleanup_lock

    lock = str(tmp_path / ".train_loop.course-a.lock")
    Path(lock).write_text(f"{_dead_pid()}|python|0", encoding="utf-8")
    try:
        assert acquire_lock(lock) is True
    finally:
        cleanup_lock(lock)


def test_pid_zero_lock_is_not_treated_as_live(tmp_path: Path) -> None:
    """残缺锁文件（PID 0 / 负数）不得被当成「有人持有」——否则同名课永久拒启。"""
    from train.loop_util import acquire_lock

    for bogus in ("0", "-1"):
        lock = str(tmp_path / f".train_loop.bogus{bogus.replace('-', 'm')}.lock")
        Path(lock).write_text(f"{bogus}|python|0", encoding="utf-8")
        assert acquire_lock(lock) is True, f"PID {bogus} 的残锁应被清理后接管"


# ────────────────────────── 源码门禁：安全分支不得被删回去 ──────────────────────────


def test_windows_guard_present_in_all_three_probes() -> None:
    """行程门禁：这三处探测必须保留 `os.name == "nt"` 分支（防回退成裸 os.kill）。"""
    for rel in ("train/loop_util.py", "run_rl.py", "remote/_instance_lock.py"):
        src = (NN_ROOT / rel).read_text(encoding="utf-8")
        assert 'os.name == "nt"' in src, f"{rel}: 缺少 Windows 安全分支（os.kill 会杀进程）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
