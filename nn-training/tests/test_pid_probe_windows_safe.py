"""存活探测的 Windows 安全不变量：**绝不用 `os.kill(pid, 0)`**（那是 TerminateProcess）。

2026-09-17 修复（`train/loop_util.py::_pid_alive`）：`acquire_lock` 用它判定「锁持有者还
活着吗」——这在语义上是**只读查询**；但在 Windows 上 `os.kill(pid, 0)` 不是探测，而是
`TerminateProcess(handle, 0)`：查询会**直接把锁持有者杀掉**（最坏情况：打死正在训练的
trainer），而且 `except Exception → False` 还会把「我把它杀了」记成「它本来就是死的」，
于是陈旧锁被「清理」、双开护栏静默失效。同口径的正确写法 = `GetExitCodeProcess ==
STILL_ACTIVE`（`run_rl._runrl_pid_alive` / `remote._instance_lock._pid_alive` 一直如此）。

**2026-09-17 收口后**：实现只有一份（`nn-training/pid_probe.py`），四份薄壳全部委托它——
`train.loop_util` / `run_rl` / `remote._instance_lock` / `remote.notebook_runtime`（后者原是
函数内的嵌套闭包，不可测，已提到模块层）；`tools/tmp-clean.py` 是唯一有意保留的副本
（仓根开发工具不能依赖 nn-training 的包布局），由源码门禁守契约。

本文件把不变量钉在**全部六处入口**上：

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
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers import source_scan

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


def _load_tmp_clean_probe() -> Callable[[int], bool]:
    """① `tools/tmp-clean.py` 的 `_pid_alive`（仓根脚本，按文件路径加载——不依赖包布局）。

    它是唯一**有意保留的副本**（仓根开发工具不能依赖 nn-training 的布局），一致性由下面的
    源码门禁守住。`if __name__ == "__main__"` 保护使加载无副作用。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_tmp_clean_under_test", NN_ROOT.parent / "tools" / "tmp-clean.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 禁止写字节码：否则每次跑测试都会在**仓根** `tools/` 下撒一个 `__pycache__/`
    # （未被 .gitignore 覆盖 ⇒ 变成脏工作区）。
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod._pid_alive  # type: ignore[no-any-return]


def _probes() -> list[tuple[str, Callable[[int], bool]]]:
    """全部存活探测入口（懒导入：run_rl 会拉起 torch）。

    四份薄壳（loop_util / run_rl / _instance_lock / notebook_runtime）现在**全部委托**
    `pid_probe.pid_alive`；额外把「唯一实现」本身与唯一的保留副本（`tools/tmp-clean.py`）
    一起纳入同一组断言——它们才是真正跑那段逻辑的地方。
    """
    from pid_probe import pid_alive as canonical_probe
    from remote._instance_lock import _pid_alive as instance_probe
    from remote.notebook_runtime import _pid_alive as notebook_probe
    from run_rl import _runrl_pid_alive as runrl_probe
    from train.loop_util import _pid_alive as loop_probe

    return [
        ("pid_probe.pid_alive（唯一实现）", canonical_probe),
        ("train.loop_util._pid_alive", loop_probe),
        ("run_rl._runrl_pid_alive", runrl_probe),
        ("remote._instance_lock._pid_alive", instance_probe),
        ("remote.notebook_runtime._pid_alive", notebook_probe),
        ("tools/tmp-clean.py::_pid_alive（保留副本）", _load_tmp_clean_probe()),
    ]


@contextmanager
def _windows_env(fake: _FakeK32, kills: list[tuple[int, int]]) -> Iterator[None]:
    """把当前进程伪装成 Windows（仅在 with 体内生效；退出前必定还原）。"""
    had_windll = hasattr(ctypes, "windll")
    prev_windll = getattr(ctypes, "windll", None)
    # 两种「我在 Windows 上吗」的写法都要翻：pid_probe 系列看 `os.name`，
    # tools/tmp-clean.py 看 `sys.platform` —— 只翻一个会让另一个副本走 POSIX 分支被漏测。
    real_name, real_kill, real_platform = os.name, os.kill, sys.platform

    def _spy(pid: int, sig: int) -> None:
        kills.append((pid, sig))
        # 修复前这里会真的 TerminateProcess；异常类型取 Exception 的父类之外无意义——
        # 用 AssertionError 即可：即便被 `except Exception` 吞掉，kills 也已记录了违规。
        raise AssertionError(f"Windows 侧禁止用 os.kill 做存活探测：pid={pid} sig={sig}")

    os.name = "nt"
    sys.platform = "win32"
    os.kill = _spy  # type: ignore[assignment]
    # 忽略码在两个平台不同：Linux/mypy 眼里 `ctypes.windll` 根本不存在（`attr-defined`），
    # Windows 上它被解析为 `LibraryLoader[WinDLL]` 因此是赋值不兼容（`assignment`）。
    # 两个码都列上（`warn_unused_ignores=false`，另一平台不会因多余码报错）。
    ctypes.windll = SimpleNamespace(kernel32=fake)  # type: ignore[attr-defined, assignment]
    try:
        yield
    finally:
        os.name = real_name
        sys.platform = real_platform
        os.kill = real_kill  # type: ignore[assignment]
        if had_windll:
            ctypes.windll = prev_windll  # type: ignore[attr-defined, assignment]
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


def _os_kill_zero_lines(path: Path) -> list[int]:
    """源码里真调用 `os.kill(<pid>, 0)`（= 存活探测）的行号——用 AST 而非字符串匹配。

    两个理由：① 新写的 docstring 里到处在讨论 `os.kill(pid, 0)` 这个坑，字符串匹配会把
    注释/文档算成违规；② **只抓信号 0**：`os.kill(pid, 15)`（SIGTERM）是**故意发的信号**
    （`notebook_runtime` 关闭 push 服务时就是这么干的），那是正常用法，不属本不变量。
    """
    import ast

    tree = source_scan.parse(str(path))
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        fn, sig = node.func, node.args[1]
        if not (
            isinstance(fn, ast.Attribute)
            and fn.attr == "kill"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in {"os", "_os", "posix"}
        ):
            continue
        if isinstance(sig, ast.Constant) and sig.value == 0:
            out.append(node.lineno)
    return out


def _production_py_files() -> list[Path]:
    """nn-training 的生产 .py（排除点目录/.venv、__pycache__、tests/、e2e/）。"""
    out: list[Path] = []
    for path in sorted(NN_ROOT.rglob("*.py")):
        parts = path.relative_to(NN_ROOT).parts
        if any(p.startswith(".") for p in parts) or "__pycache__" in parts:
            continue
        if parts[0] in {"tests", "e2e"}:
            continue
        out.append(path)
    return out


def test_os_kill_probe_exists_in_exactly_one_place() -> None:
    """行程门禁（唯一实现不变量）：`nn-training/` 里真调用 `os.kill` 的文件**只能有一个**。

    这就是这类隐患反复出现的根因面：从前是「三处同源」——每加一个调用点就多一份可漂移的
    实现（2026-09-17 一天内就在 `loop_util` / `notebook_runtime` 两处踩到不同的坑）。
    现在所有入口都委托 `pid_probe.py`，于是「Windows 安全」只需在一个地方成立。
    """
    offenders = [
        p.relative_to(NN_ROOT).as_posix() for p in _production_py_files() if _os_kill_zero_lines(p)
    ]
    assert offenders == ["pid_probe.py"], (
        f"`os.kill(pid, 0)` 存活探测只允许出现在 pid_probe.py（唯一实现），实际: {offenders} —— "
        "新增调用点请 `from pid_probe import pid_alive`，不要再复制实现"
    )
    # 守住“唯一”的另一面：pid_probe 的 POSIX 分支必须真的存在（别为了过门禁把它删了，
    # 那会让 Linux/本机全部判「不活」）
    assert any(
        "os.kill" in line and "pid, 0" in line
        for line in (NN_ROOT / "pid_probe.py").read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ), "pid_probe 的 POSIX 分支不得缺失"
    # 且它必须靠 `os.name == "nt"` 分流（Windows 侧禁 os.kill）
    assert 'os.name == "nt"' in (NN_ROOT / "pid_probe.py").read_text(encoding="utf-8")


def test_delegation_shells_do_not_reimplement() -> None:
    """四份薄壳必须**委托**而不是复制：源码里不得再出现 `ctypes.windll`（Windows 分支的标志）。"""
    for rel in (
        "train/loop_util.py",
        "run_rl.py",
        "remote/_instance_lock.py",
        "remote/notebook_runtime.py",
    ):
        src = (NN_ROOT / rel).read_text(encoding="utf-8")
        assert "from pid_probe import" in src, f"{rel}: 必须从 pid_probe 导入唯一实现"
        assert re.search(r"return\s+\w*pid_alive\w*\(\s*pid\s*\)", src), (
            f"{rel}: 探测函数体必须是 `return ...pid_alive(pid)`（不得再自己写实现）"
        )
        assert "import ctypes" not in src, f"{rel}: 不得再自带 Windows 分支（应委托唯一实现）"


def test_tmp_clean_copy_keeps_guard_and_windows_branch() -> None:
    """仓根工具的唯一保留副本：`pid <= 0` 护栏与 Windows 分支都必须**同时**在。"""
    src = (NN_ROOT.parent / "tools" / "tmp-clean.py").read_text(encoding="utf-8")
    assert "if pid <= 0:" in src, "缺少 `pid <= 0` 护栏：残锁里的 0/-1 会让运行目录永不收敛"
    assert 'sys.platform != "win32"' in src, "缺少 Windows 安全分支（os.kill 在 Windows 会杀进程）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
