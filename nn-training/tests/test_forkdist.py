"""tools/forkdist.py（收集一次 + fork 的 pytest 分发器）的语义护栏。

引入背景（docs/nn/engineering.md §49）：`-n 12` 下每个 worker 都要收集全部 ~275 个测试模块，
12 份里 11 份纯冗（占 worker 自用 CPU 的 35%）。`--forkdist N` 让 master 收集一次再 fork，
16 核实测墙钟 22.69 → 19.81s、user 191.7 → 115.3s。**它已经是 Linux 门禁的默认分发器**
（Windows/macOS 继续走 xdist），所以它的失效模式全部要钉住——这类 runner 的典型事故是
「**静默少跑**」：worker 死了、用例丢了、报告丢了，而退出码照样是 0。

已发生过的两个坑（各一条断言）：

1. **重放阶段的异常会把 waitpid 变成死等**（2026-09-29 实测）。master 先在 `finally` 里
   `waitpid`、后关命令管道 ⇒ worker 永远读不到 EOF、守卫里也看不到那条异常（只有
   `kill -ABRT` + faulthandler 才把栈挖出来）。修法：**先关管道再 waitpid**
   （见 `_run_forked` 的 finally）。`pytest_warning_recorded` 是 historic hook，必须
   `call_historic`（直接调会被 pluggy 断言拒绝）也是那次挖出来的。
2. **子进程继承了 master 的全局捕获临时文件**（同一个 open file description = 共享文件
   偏移）⇒ 12 个子进程各自 `lseek(0)+read` 时会读到**别人写的半截 UTF-8 字符**
   （`UnicodeDecodeError: byte 0x96 in position 0`），而且会被 pytest 归因成「当前这条用例
   setup 失败」——真正出错的是前一条用例的夹具收尾。修法：子进程
   `stop_global_capturing()` + `start_global_capturing()` 重建自己的捕获
   （`_reset_global_capture`）。**丢掉这一步不会立刻红**，只会在满机时假红，故在此钉住调用点。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import platform_utils
from tests.test_githook_scripts import _bash_path, _bash_usable

REPO_ROOT = Path(__file__).resolve().parents[2]
NN_ROOT = REPO_ROOT / "nn-training"
PLUGIN = NN_ROOT / "tools" / "forkdist.py"
WRAPPER = REPO_ROOT / "tools" / "githook" / "nn-py-safe.sh"
MAKEFILE = NN_ROOT / "Makefile"
TASK_PY = NN_ROOT / "task.py"
CI = REPO_ROOT / ".github" / "workflows" / "nn-training.yml"

#: 覆盖五种结局的冒烟用例（判断「有没有少跑」用的就是它们的计数）。
_SMOKE = '''\
"""forkdist 冒烟（临时目录，用完不删——沙箱删除配额）：五种结局各一条。"""

from __future__ import annotations

import pytest


def test_ok_1():
    assert True


def test_ok_2():
    assert True


def test_skip():
    pytest.skip("intentional")


@pytest.mark.xfail(reason="intentional")
def test_xfail():
    raise AssertionError("expected")


def test_fail():
    assert 1 + 1 == 3, "intentional failure"
'''

_no_bash = pytest.mark.skipif(not _bash_usable(), reason="bash 不可用/不可启动")
_no_fork = pytest.mark.skipif(
    not hasattr(os, "fork"), reason="Windows 没有 os.fork（该分支走 xdist）"
)


class _StubConfig:
    """`pytest_configure` 只读这两样东西（选项 + numprocesses），并**写回**解析后的 worker 数。

    `forkdist_workers` 必须在类上声明：插件是 `setattr(config, "forkdist_workers", n)`，
    少了它 mypy 会判 `_StubConfig has no attribute`（"读回解析结果"那条用例只有运行期才摸到）。
    """

    def __init__(self, n: int | None, numprocesses: int | None = None) -> None:
        self._n = n
        self.option = types.SimpleNamespace(numprocesses=numprocesses)
        self.forkdist_workers: int | None = None

    def getoption(self, name: str) -> int | None:
        assert name == "forkdist", name
        return self._n


def _plugin():
    sys.path.insert(0, str(NN_ROOT))
    try:
        from tools import forkdist

        return forkdist
    finally:
        sys.path.pop(0)


def test_configure_is_a_noop_without_the_option() -> None:
    """不传 `--forkdist` 必须完全不动（缺省关；门禁以外的一切调用方零影响）。"""
    _plugin().pytest_configure(_StubConfig(None))  # 不抛就算过


def test_configure_rejects_windows_without_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 `os.fork`（Windows）⇒ 当场拒绝，而不是跑成一个半吊子 runner。"""
    monkeypatch.delattr(os, "fork", raising=False)
    with pytest.raises(Exception) as e:
        _plugin().pytest_configure(_StubConfig(4))
    assert "os.fork" in str(e.value), "拒绝理由必须点名 os.fork（Windows 的判据就是它）"


def test_configure_rejects_mixing_with_xdist() -> None:
    """`--forkdist` 与 `-n` 互斥：两者都接管 pytest_runtestloop，同时给必须响。"""
    if not hasattr(os, "fork"):
        pytest.skip("Windows")
    with pytest.raises(Exception) as e:
        _plugin().pytest_configure(_StubConfig(4, numprocesses=12))
    assert "xdist" in str(e.value) or "-n" in str(e.value)


def test_worker_count_option_accepts_ints_and_auto() -> None:
    """`--forkdist auto` 的解析（`make test NPROC=auto` / `task.py` 的 `-n auto` 靠它）。"""
    fc = _plugin()
    assert fc._worker_count_option("12") == 12
    assert fc._worker_count_option(" auto ") == fc._AUTO
    assert fc._worker_count_option("AUTO") == fc._AUTO
    for bad in ("x", "0", "-3", ""):
        with pytest.raises(Exception) as e:
            fc._worker_count_option(bad)
        assert "forkdist" in str(e.value), f"{bad!r} 的报错必须点名 --forkdist"


def test_configure_resolves_auto_from_platform_utils(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`auto` 必须解析成 `platform_utils.effective_cores()`（容器里比 `os.cpu_count()` 准）。"""
    if not hasattr(os, "fork"):
        pytest.skip("Windows")
    fc = _plugin()
    monkeypatch.setattr(platform_utils, "effective_cores", lambda: 7)
    cfg = _StubConfig(fc._AUTO)
    fc.pytest_configure(cfg)
    assert cfg.forkdist_workers == 7, (
        "auto 走了别的核数来源——本仓「本机几核」只允许一个答案（effective_cores）"
    )


def test_configure_rejects_nonsense_worker_count() -> None:
    """负数在 configure 里也要拒（选项转换器是第一道，这里是第二道）。"""
    if not hasattr(os, "fork"):
        pytest.skip("Windows")
    with pytest.raises(Exception) as e:
        _plugin().pytest_configure(_StubConfig(-5))
    assert "worker" in str(e.value)


def test_child_rebuilds_its_own_global_capture() -> None:
    """根因护栏（见模块 docstring 第 2 条）：子进程必须重建全局捕获。

    丢了它不会立刻红——只在满载时以「半个 UTF-8 字符 + 归因到错误用例」的形式假红。
    这里钉调用点（`_child_main` 里必须有 `_reset_global_capture(config)`），并在下方
    用源码断言把「真的重建了」也钉住。
    """
    src = PLUGIN.read_text(encoding="utf-8")
    assert re.search(r"_reset_global_capture\(config\)", src), "子进程没有重建全局捕获"
    assert "stop_global_capturing()" in src and "start_global_capturing()" in src


# ----------------------------------------------------------------------------------------
# 三个入口的接线（task.py / Makefile / CI）：判据都是「Linux ⇒ forkdist，其余 ⇒ xdist -n」
# ----------------------------------------------------------------------------------------


def test_task_py_dispatches_per_platform() -> None:
    """`python task.py <target>` 在 Linux 上走 forkdist，在其它平台保留 xdist。

    真 import 一次 task.py（它模块级没有副作用）并看它拼出来的 argv —— 比 grep 文本强。
    """
    sys.path.insert(0, str(NN_ROOT))
    try:
        import task

        got = task.pytest_dispatch()
    finally:
        sys.path.pop(0)
    src = TASK_PY.read_text(encoding="utf-8")
    assert 'hasattr(os, "fork")' in src, "task.py 的分发判据里丢了「有没有 os.fork」"
    if sys.platform.startswith("linux"):
        assert got == ["-p", "tools.forkdist", "--forkdist", "auto"], got
    else:
        assert got == ["-n", "auto"], got


def test_makefile_dispatches_per_kernel() -> None:
    """`make test*` 三个目标：Linux → `--forkdist $(NPROC)`，其余 → `-n $(NPROC)`。

    有 make 就**真跑 `make -n`** 看展开结果（文本里有分支不等于 make 真走对）。

    子进程的 env 必须**清干净**：`NPROC` / `PYTEST_DISPATCH` 是 Makefile 公开的覆盖旋钮，
    而 make 会把命令行变量（`NPROC=8 make test`、`PYTEST_DISPATCH='-n auto' make test` ——
    都是文档里的打法）经 **MAKEFLAGS** 传给子 make；哪怕只靠 env 传，make 也认。不清就等于
    让判据变成「看调用方的环境」而不是「看缺省行为」，而缺省值才是这条用例的对象
    （实测：`make test-fast PYTEST_DISPATCH='-n auto'` 下不清 ⇒ 子 make 打出 `-n auto`、用例假红）。
    """
    src = MAKEFILE.read_text(encoding="utf-8")
    assert "ifeq ($(UNAME_S),Linux)" in src, "Makefile 的分发器没有按内核分流"
    if shutil.which("make"):
        env = {k: v for k, v in os.environ.items() if k not in {"NPROC", "PYTEST_DISPATCH"}}
        env["MAKEFLAGS"] = ""
        env["MFLAGS"] = ""
        proc = subprocess.run(
            ["make", "-n", "test", "test-fast", "test-e2e"],
            cwd=NN_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
        assert proc.returncode == 0, f"make -n 失败：{proc.stdout}{proc.stderr}"
        recipes = [ln for ln in proc.stdout.splitlines() if "-m pytest" in ln]
        assert len(recipes) == 3, recipes
        for line in recipes:
            if sys.platform.startswith("linux"):
                assert "--forkdist auto" in line, f"{line}"
                assert not re.search(r"\s-n\s", line), f"forkdist 分支还搭了 -n：{line}"
            else:
                assert "--forkdist" not in line, f"{line}"
    else:
        assert "-n $(NPROC)" in src, "非 Linux 分支丢了 xdist"


def test_ci_keeps_xdist_with_the_measured_reason_in_the_file() -> None:
    """CI **不**换 forkdist（实测无收益），但那份实测依据必须留在文件里。

    本仓的 runner 规模（2 worker）下 forkdist 与 xdist 墙钟同价（§50 实测：45.03 vs 44.95s
    @2 vCPU、40.93 vs 41.51s @4 vCPU）——收益来自「多 worker 重复收集」的争用缓解，
    2 个 worker 没什么可缓解的。这条用例钉的不是「永远不许换」（换 runner 规模就得重测），
    而是「换之前必须先有实测，且测量记录不能丢」：注释里的依据一旦被删，下一个读文件的人
    就会把它当成「没试过」而重跑一遍。
    """
    src = CI.read_text(encoding="utf-8")
    assert "--forkdist" in src, "CI 里应留下「为什么不用 forkdist」的记录/反例"
    assert re.search(r"不要给 CI 换", src), "缺少「别换」的显式警告"
    layers = [ln for ln in src.splitlines() if "uv run pytest" in ln]
    assert len(layers) == 2, layers
    for line in layers:
        assert "-n 2" in line, f"CI 两层应继续用 xdist -n 2：{line}"


def test_reap_closes_command_pipes_before_waiting() -> None:
    """根因护栏（见模块 docstring 第 1 条）：`finally` 里必须先关命令管道再 waitpid。"""
    src = PLUGIN.read_text(encoding="utf-8")
    tail = src.split("    finally:\n", 1)[1]
    body = tail.split("    return True", 1)[0]
    assert body.index("_close_cmd") < body.index("_reap"), (
        "先 waitpid 后关管道 ⇒ worker 读不到 EOF、主进程死等（2026-09-29 实测）"
    )


@_no_bash
@_no_fork
def test_forkdist_runs_everything_and_reports_honestly(tmp_path: Path) -> None:
    """端到端：五种结局全跑到、计数与退出码都对（防「静默少跑」）。"""
    scratch = tmp_path / "smoke"
    scratch.mkdir()
    (scratch / "test_smoke.py").write_text(_SMOKE, encoding="utf-8", newline="\n")

    proc = subprocess.run(
        [
            "bash",
            _bash_path(WRAPPER),
            "-m",
            "pytest",
            str(scratch),
            "-p",
            "tools.forkdist",
            "--forkdist",
            "3",
            "--maxfail=0",  # 万一 ini 的 addopts 带 -x：别让首败把计数截短
        ],
        cwd=NN_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,  # 用例内上限 60s/项也够，这里给子进程留足
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"有失败用例时退出码必须是 1\n{out}"
    for fragment in ("2 passed", "1 failed", "1 skipped", "1 xfailed"):
        assert fragment in out, f"结果计数少了 {fragment!r}（静默少跑？）\n{out}"
    assert "[forkdist]" not in out, f"runner 自己报了丢用例/提前收工\n{out}"


#: **在 worker 里把自己弄死**的用例：worker 不会上报任何报告（模拟段错误/OOM/被外部杀掉）。
#: 旁边放一条正常用例——runner 在「只有一条用例」时会主动退回串行循环（没东西可并行）。
_DIES = '''\
"""worker 死于半途（模拟非法指令/OOM）：master 必须把这条判失败，不得静默跳过。"""

from __future__ import annotations

import os


def test_survives():
    assert True


def test_worker_dies_mid_run():
    os._exit(9)
'''


@_no_bash
@_no_fork
def test_forkdist_reports_a_worker_that_dies_instead_of_dropping_the_item(tmp_path: Path) -> None:
    """worker 半途死掉时，那条用例必须变成**响亮失败**（否则整批少跑也能“全绿”）。"""
    scratch = tmp_path / "dies"
    scratch.mkdir()
    (scratch / "test_dies.py").write_text(_DIES, encoding="utf-8", newline="\n")

    proc = subprocess.run(
        [
            "bash",
            _bash_path(WRAPPER),
            "-m",
            "pytest",
            str(scratch),
            "-p",
            "tools.forkdist",
            "--forkdist",
            "2",
            "--maxfail=0",
        ],
        cwd=NN_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"worker 死了却判成功（就是这类 runner 最危险的失效模式）\n{out}"
    assert "[forkdist]" in out, f"没有点名「worker 丢了用例」\n{out}"
    assert "1 failed" in out, f"丢掉的用例没计成失败\n{out}"
