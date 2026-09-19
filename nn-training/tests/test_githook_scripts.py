"""tools/githook 脚本的可执行性 / 接线护栏。

已发生过的坑，各一条断言（条目 5 由 2026-09-17 门禁提速引入）：

1. **nn-py-safe.sh 在原生 Linux 上跑不了 pytest**（2026-09-15 实测）。它对 `-m pytest`
   分支无条件套 MSYS 盘符改写，`/home/<user>/battle/tools/githook` 被 `s|^/([a-z])|\\1:|`
   误伤成 `h:ome/...` ⇒ exit 2「can't open file '.../nn-wall.py'」。而 AGENTS §0.1-13
   钦定它是**唯一**合规的 pytest 入口 —— 入口坏掉时没有任何测试会红，只有人肉发现。
   修法：路径改写仅在 MSYS/MINGW 下生效。本测试真起一次子进程验证（`--version`
   不收集用例、秒级返回）。

2. **门禁的 pytest 目标必须同时含两层**（tests/ + e2e/）。e2e 曾在 60e5f69 后掉出所有
   自动化（门禁不跑、CI 那步指向不存在的文件），本断言把「e2e 在门禁里」钉成回归。

3. **pytest 的 `--timeout` 单位是秒，不是毫秒**（2026-09-15 发现）。门禁/task.py 曾
   写 `--timeout=50000`——那是从 **bun** 的 `--timeout=50000`（bun 才是毫秒）误搬的，
   等于把上限抬到 13.9 小时并**覆盖掉** pyproject addopts 的 `--timeout=60` ⇒ 所谓
   「>1 分钟即红旗」的护栏名存实亡，hang 又能无限挂。本测试把量级钉死。

4. **「bash 在 PATH 上」≠「bash 起得来」**（2026-09-16 发现）。受限宿主（Windows
   AppLocker / ASR、沙箱化终端）里 `shutil.which("bash")` 照常返回路径，但任何
   `subprocess.run(["bash", ...])` 都以 `Bash/CallMsi/E_ACCESSDENIED` 失败 ⇒ 只按
   which() 决定 skip 会让门禁在任何这类终端里**恒红**，把真回归淹掉。skip 条件改为
   **真起一次 `bash -c "exit 0"` 探测**。

5. **门禁的并行旋钮必须「worker 数 × CPU 内线程数」一起调**（2026-09-17 实测）。旧默认
   `-n 4` + torch 默认内线程（= 物理核）⇒ 4 worker × 16 线程抢 16 核，全量 36.3s；
   只加 worker 更慢（-n 12 默认线程 = 44.7s），封到 1 线程后 -n 12 = 24.7s（与 -n 8/16
   同水平）⇒ 全量 ~36s → ~23s。**静默退化风险**：谁把 OMP/MKL 的封顶 export 删掉，
   门禁立刻退回 ~36s，而所有用例仍然全绿——只有人肉计时才发现。故本文件把两个旋钮
   都钉成静态回归（见 test_gate_caps_intraop_threads / test_gate_worker_count_scales_with_cores）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NN_ROOT = REPO_ROOT / "nn-training"
WRAPPER = REPO_ROOT / "tools" / "githook" / "nn-py-safe.sh"
GATE = REPO_ROOT / "tools" / "githook" / "nn-python-gate.sh"
TASK_PY = NN_ROOT / "task.py"

#: `--timeout=<n>` 或 `NN_PYTEST_TIMEOUT_S:-<n>}` 里的数值（只看非注释行）。
_TIMEOUT_VALUE = re.compile(r"(?:--timeout|NN_PYTEST_TIMEOUT_S[:=])\D*(\d+)")
#: 合理上界（秒）：本仓最慢单测实测 22s；>10 分钟就不是「护栏」而是摆设。
_MAX_TIMEOUT_S = 600


def _bash_usable() -> bool:
    """bash 在 PATH 上**且真能启动**（见模块 docstring 第 4 条）。

    探测用 `bash -c "exit 0"`：不碰文件系统、不依赖 cwd、毫秒级返回。
    启动被宿主的执行策略拒绝时抛 `OSError`（E_ACCESSDENIED 等），归为「不可用」。
    """
    if shutil.which("bash") is None:
        return False
    try:
        probe = subprocess.run(["bash", "-c", "exit 0"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _bash_is_wsl() -> bool:
    """当前 bash 是不是 WSL（`uname -s` = Linux 且能调 wslpath）。

    WSL bash 与 MSYS git-bash 不同：不认 `D:\\...` 盘符路径（反斜杠被吞，子进程
    直接 ENOENT），也吃不下 `/d/...`；只认 `/mnt/d/...`。Windows 原生 python 的
    `Path(...)` 给的是 `D:\\...`，喂给 WSL bash 前必须经 wslpath -u 转 POSIX。
    """
    try:
        probe = subprocess.run(
            ["bash", "-c", "test -x /usr/bin/wslpath && uname -s"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and b"Linux" in probe.stdout


def _bash_path(p: Path) -> str:
    """把（可能 Windows 盘符形式的）路径转成当前 bash 可读形态。

    非 WSL（MSYS/native Linux）原样返回 —— MSYS 层自动映射 /d/ 风格路径，native
    Linux 本就是 POSIX。WSL 下经 wslpath -u 转 /mnt/<drive>/...；转换失败回退原样。
    """
    if not _BASH_IS_WSL:
        return str(p)
    try:
        r = subprocess.run(
            ["bash", "-lc", f"wslpath -u '{p}'"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return str(p)
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else str(p)


#: 模块级求值一次：探测本身要起子进程，不必每条用例重跑。
_BASH_USABLE = _bash_usable()
_BASH_IS_WSL = _BASH_USABLE and _bash_is_wsl()

no_bash = pytest.mark.skipif(
    not _BASH_USABLE, reason="bash 不可用/不可启动（宿主拦截或不在 PATH，无法验证 shell 脚本）"
)


@no_bash
def test_py_safe_wrapper_launches_pytest() -> None:
    """`nn-py-safe.sh -m pytest` 必须真能把 pytest 起起来（Linux/MSYS/WSL 都要成立）。"""
    proc = subprocess.run(
        ["bash", _bash_path(WRAPPER), "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,  # pytest 全局 per-test 上限 60s，这里必须更短
    )
    assert proc.returncode == 0, (
        "nn-py-safe.sh 起不了 pytest（AGENTS §0.1-13 的唯一合规入口）——"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "pytest" in (proc.stdout + proc.stderr).lower()


def test_gate_pytest_targets_cover_both_layers() -> None:
    """门禁的 pytest 目标集 = tests/（单测层）+ e2e/（集成层）。"""
    text = GATE.read_text(encoding="utf-8")
    assert 'PYTEST_TARGETS="tests/ e2e/"' in text, "门禁未同时覆盖 tests/ 与 e2e/"
    assert "$PYTEST_TARGETS" in text, "PYTEST_TARGETS 未被真正传给 pytest"
    assert 'NN_GATE_SKIP_E2E' in text, "缺少只退集成层的定向出口（flake 时要用）"
    # 历史坑：`-m "not heavy"` 式的标记分层（tests/ 里 heavy 标记实测 0 个 ⇒ 空转）。
    assert "not heavy" not in strip_comments(text), "层应由路径决定，不要回到标记过滤"


def strip_comments(text: str) -> str:
    """去掉整行注释——在注释里解释历史坑是合法的，不该被判红；只看真正生效的代码。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _gate_code() -> str:
    """门禁脚本里真正生效的行。"""
    return strip_comments(GATE.read_text(encoding="utf-8"))


def _timeout_values(text: str) -> list[int]:
    """非注释行里的 pytest 超时数值（秒）。"""
    return [int(m.group(1)) for m in _TIMEOUT_VALUE.finditer(strip_comments(text))]


@pytest.mark.parametrize("script", [GATE, TASK_PY], ids=["nn-python-gate.sh", "task.py"])
def test_pytest_timeout_is_seconds_not_milliseconds(script: Path) -> None:
    """超时必须按**秒**给，且不能是 0/天量值（0 = 关掉护栏）。"""
    values = _timeout_values(script.read_text(encoding="utf-8"))
    assert values, f"{script.name} 里找不到 pytest --timeout —— 护栏被删了？"
    bad = [v for v in values if v == 0 or v > _MAX_TIMEOUT_S]
    assert not bad, (
        f"{script.name} 的 pytest --timeout={bad} 不合规：pytest-timeout 的单位是**秒**，"
        f"ms 量级的值（如 50000，从 bun 误搬）会覆盖 addopts 的 60s 并把护栏变成 13.9 小时。"
    )


def test_gate_runs_pytest_for_both_dirs(tmp_path: Path) -> None:
    """冒烟：把门禁的 pytest 目标行抽出来，确认两个目录都在同一次调用里。"""
    # tmp_path 仅为与 tests/conftest.py 的清理语义一致（未使用其内容）。
    assert (NN_ROOT / "tests").is_dir()
    assert (NN_ROOT / "e2e").is_dir()
    text = GATE.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "$PYTEST_TARGETS" in ln and "-m pytest" in ln)
    assert line.count("$PYTEST_TARGETS") == 1


def test_gate_caps_intraop_threads() -> None:
    """门禁必须把 pytest 的 CPU 内线程数封顶（默认 1）——否则超订，全量 ~36s。

    见模块 docstring 第 5 条：删掉这组 export 会让门禁静默退回 ~36s（测试照旧全绿）。
    """
    code = _gate_code()
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        assert f"{var}=$GATE_THREADS" in code, (
            f"门禁未把 {var} 封到 GATE_THREADS —— 每个 xdist worker 都会继承 torch 的默认"
            "内线程数（= 物理核），worker 一多就超订（2026-09-17 实测 36.3s → 24.7s）"
        )
    m = re.search(r"\$\{NN_GATE_THREADS:-(\d+)\}", code)
    assert m, "找不到 NN_GATE_THREADS 的默认值（0 = 退回 torch 默认的逃生口须保留）"
    assert 1 <= int(m.group(1)) <= 4, (
        f"NN_GATE_THREADS 默认 {m.group(1)} 不合理：实测 1 最优（0 才是「不设」，别用大数做默认）"
    )


def test_gate_worker_count_scales_with_cores() -> None:
    """worker 数默认派生自核数（不再写死 4），且必须有上界（内存封顶）。"""
    code = _gate_code()
    assert "NN_GATE_NPROC" in code, "缺少 NN_GATE_NPROC 逃生口"
    assert "os.cpu_count()" in code, (
        "worker 数应从核数派生——写死 4 在 16 核上白白浪费并行度（2026-09-17 实测 n=4 → n=12 提速 1/3）"
    )
    assert re.search(r"NPROC=\$\{NN_GATE_NPROC:-\$CORES\}", code), (
        "NPROC 默认值应 = min(核数, 上界)，且由 NN_GATE_NPROC 覆盖"
    )
    cap = re.search(r'NPROC" -gt (\d+)', code)
    assert cap, "NPROC 缺上界——worker 无上限会按核数放大内存（-n 12 峰值 RSS 实测 ≈ 3.9GB）"
    assert int(cap.group(1)) <= 32, f"NPROC 上界 {cap.group(1)} 过大（内存封顶形同虚设）"
