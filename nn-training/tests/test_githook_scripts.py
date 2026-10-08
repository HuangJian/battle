"""tools/githook 脚本的可执行性 / 接线护栏。

已发生过的坑，各一条断言（条目 5 由 2026-09-17 门禁提速引入）：

1. **nn-py-safe.sh 在原生 Linux 上跑不了 pytest**（2026-09-15 实测）。它对 `-m pytest`
   分支无条件套 MSYS 盘符改写，`/home/<user>/battle/tools/githook` 被 `s|^/([a-z])|\\1:|`
   误伤成 `h:ome/...` ⇒ exit 2「can't open file '.../nn-wall.py'」。而 AGENTS §5
   钦定它是**唯一**合规的 pytest 入口 —— 入口坏掉时没有任何测试会红，只有人肉发现。
   修法：路径改写仅在 MSYS/MINGW 下生效。本测试真起一次子进程验证（`--version`
   不收集用例、秒级返回）。

2. **门禁的 pytest 目标必须同时含两层**（tests/ + e2e/）。e2e 曾在 60e5f69 后掉出所有
   自动化（门禁不跑、CI 那步指向不存在的文件），本断言把「e2e 在门禁里」钉成回归。

3. **pytest 的 `--timeout` 单位是秒，不是毫秒**（2026-09-15 发现）。门禁/tools/task.py 曾
   写 `--timeout=50000`——那是从 **bun** 的 `--timeout=50000`（bun 才是毫秒）误搬的，
   等于把上限抬到 13.9 小时并**覆盖掉** pyproject addopts 的 `--timeout=60` ⇒ 所谓
   「>1 分钟即红旗」的护栏名存实亡，hang 又能无限挂。本测试把量级钉死。

4. **「bash 在 PATH 上」≠「bash 起得来」**（2026-09-16 发现）。受限宿主（Windows
   AppLocker / ASR、沙箱化终端）里 `shutil.which("bash")` 照常返回路径，但任何
   `subprocess.run(["bash", ...])` 都以 `Bash/CallMsi/E_ACCESSDENIED` 失败 ⇒ 只按
   which() 决定 skip 会让门禁在任何这类终端里**恒红**，把真回归淹掉。skip 条件改为
   **真起一次 `bash -c "exit 0"` 探测**。

6. **双向路径的转换判据是「python 是不是 Windows 二进制」，不是「wslpath 存不存在」**
   （2026-09-20 事故）。写好 WSL 支持的那次只问了「`command -v wslpath` 通不通」，
   而容器里恰好是 **WSL2 内核 + 原生 Linux venv + 一个把仓库路径映射到
   `//wsl.localhost/<distro>/…` 的 wslpath** —— 那是个**只有 Windows 侧**能读的 UNC
   命名空间；原生 python 拿到它当场 `can't open file '//wsl.localhost/.../detach-run.py'`，
   门禁 **0s 假红**（`✗ nn-python-gate 失败且无法按文件归因`）。判据同 `nn-py-safe.sh`
   的 `case "$PY_BIN" in *.exe)`；本文件静态钉住转换的所在分支，并用
   `test_gate_keeps_native_paths_when_wslpath_maps_elsewhere` 真起一次假仓库骨架复现事故。

7. **分发器选择只看「选中的 python 是什么」，不看 uname 存不存在**（2026-09-29，v3.19）。
   Linux 上 pytest 改走 `--forkdist`（收集一次 + fork，见 tests/tools/test_forkdist.py 与
   docs/nn/engineering.md §49），Windows/macOS 继续 `-n`。掉进旧陷阱的写法是
   「问 uname/有没有 os.fork」这种间接判据——本仓已有一次同型事故（第 6 条）。
   另钉一条：`--forkdist` 与 `-n` 互斥，任何一行命令行不得同时出现（同时给会让两个
   插件抢 `pytest_runtestloop`）。

5. **门禁的并行旋钮必须「worker 数 × CPU 内线程数」一起调**（2026-09-17 实测）。旧默认
   `-n 4` + torch 默认内线程（= 物理核）⇒ 4 worker × 16 线程抢 16 核，全量 36.3s；
   只加 worker 更慢（-n 12 默认线程 = 44.7s），封到 1 线程后 -n 12 = 24.7s（与 -n 8/16
   同水平）⇒ 全量 ~36s → ~23s。**静默退化风险**：谁把 OMP/MKL 的封顶 export 删掉，
   门禁立刻退回 ~36s，而所有用例仍然全绿——只有人肉计时才发现。故本文件把两个旋钮
   都钉成静态回归（见 test_gate_caps_intraop_threads / test_gate_worker_count_scales_with_cores）。

8. **门禁任一腿先红就必须停掉其余腿**（fail-fast，2026-10-05，v3.20）。现场：ruff 1s 就红
   （一次 import 排序），门禁却陪跑完 23s 的 pytest 才报错；而 `tools/task.py check` 自
   2026-09-15 起就写着「与 nn-python-gate.sh 并行语义同构」——门禁一直只并行、没 fail-fast。
   行为档 = `test_gate_fails_fast_when_a_static_tool_is_red`（真跑门禁：假 pytest 睡 20s，
   红腿一响它必须被停、**进程真的死**——hook 模式下 detach-run 若还是 Popen 壳，杀到的
   只是壳，真 pytest 会变孤儿继续烧 CPU）；Windows 连树杀（taskkill /T）由静态钉子看住。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NN_ROOT = REPO_ROOT / "nn-training"
WRAPPER = REPO_ROOT / "tools" / "githook" / "nn-py-safe.sh"
GATE = REPO_ROOT / "tools" / "githook" / "nn-python-gate.sh"
TASK_PY = NN_ROOT / "tools/task.py"

#: `--timeout=<n>` 或 `NN_PYTEST_TIMEOUT_S:-<n>}` 里的数值（只看非注释行）。
_TIMEOUT_VALUE = re.compile(r"(?:--timeout|NN_PYTEST_TIMEOUT_S[:=])\D*(\d+)")
#: 合理上界（秒）：本仓最慢单测实测 22s；>10 分钟就不是「护栏」而是摆设。
_MAX_TIMEOUT_S = 600


def _msys(p: Path) -> str:
    """MSYS bash 里的路径形态：`D:/github/battle2` → `/d/github/battle2`。

    门禁/包装器都跑在 MSYS bash 下，脚本里 `pwd`/`dirname` 给出的就是这种形式；而
    `str(Path(...))` 在 Windows 上是反斜杠 + 盘符（`D:\\github\\...`）⇒ 直接拿它去
    bash 的输出里比对**永远找不到**（2026-09-20）。原生 Linux 上没有盘符，原样返回。
    """
    s = p.as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


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

    WSL 优先 `wslpath -u`（→ `/mnt/d/...`）；失败或非 WSL 时，盘符路径一律归一成
    MSYS `/d/...`（WSL 再套 `/mnt`）。**绝不**把 `D:\\...` 直接喂给 bash——反斜杠
    会被当转义吞掉（门禁 xdist 下 `_BASH_IS_WSL` 探测偶发假阴性时实测
    `bash: D:githubbattle2... No such file`，rc=127，2026-09-20）。
    """
    msys_form = _msys(p)
    if _BASH_IS_WSL:
        try:
            r = subprocess.run(
                ["bash", "-lc", f"wslpath -u '{p}'"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            out = r.stdout.strip()
            if r.returncode == 0 and out and ":" not in out.split("/", 1)[0]:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
        # wslpath 不可用/失败：盘符 → `/mnt/d/...`（WSL 只认这一种）。
        if re.match(r"^/[a-z]/", msys_form):
            return "/mnt" + msys_form
    return msys_form


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
        "nn-py-safe.sh 起不了 pytest（AGENTS §5 的唯一合规入口）——"
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


def test_py_safe_wrapper_pins_blas_threads() -> None:
    """`nn-py-safe.sh -m pytest …` 必须把 BLAS 内线程封到 1（docs/nn/engineering.md §68）。

    不封的代价实测是**每进程 +483MB 私有提交**（import torch 后 642MB → 封顶 159MB），
    而工作集两条路径只差 1MB —— 工作集/任务管理器口径根本看不见这笔账，但它顶的是
    Windows 的 commit limit（「内存紧张杀进程」的直接成因）。门禁脚本（GATE_THREADS）
    早就封了，**单跑入口没有** ⇒ 日常单跑才是内存暴涨的那条路。
    """
    text = strip_comments(WRAPPER.read_text(encoding="utf-8"))
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        assert f"{key}=" in text, (
            f"nn-py-safe.sh 未封 {key}（§68：不封则按物理核开满 BLAS 线程，每线程一份缓冲）"
        )
    assert "export OMP_NUM_THREADS" in text, "变量设了但没 export ⇒ 内层 pytest 进程看不到"


def test_root_conftest_pins_blas_threads_before_heavy_imports() -> None:
    """根 conftest 必须在 numpy/torch 被 import **之前** setdefault 三个 BLAS 变量。

    「之前」是语义的一部分：BLAS 在**载入瞬间**按环境变量决定开几条线程、各留一份缓冲，
    事后 `torch.set_num_threads(1)` 只收 PyTorch 自己的线程池，收不掉 numpy 侧已经 commit
    出去的那部分（§68 实测：`set_num_threads(1)` 那一步的工作集/私有提交**一行没动**）。
    用 `setdefault` 而非直接赋值，是为了不覆盖门禁脚本 / CI 已经 export 的值。
    """
    text = strip_comments((NN_ROOT / "conftest.py").read_text(encoding="utf-8"))
    hits = [
        text.find(f'"{key}"')
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    ]
    found = [i for i in hits if i != -1]
    assert len(found) == 3, f"根 conftest 没封全三个 BLAS 变量（§68）：命中 {len(found)}/3"
    assert "setdefault" in text, "必须 setdefault（覆盖式赋值会顶掉门禁/CI 显式设的线程数）"
    first = min(found)
    for mod in ("import torch", "import numpy"):
        idx = text.find(mod)
        assert idx == -1 or idx > first, (
            f"{mod} 早于 BLAS 封顶 ⇒ 载入时缓冲已按默认线程数 commit 出去，封顶失效"
        )


def strip_comments(text: str) -> str:
    """去掉整行注释——在注释里解释历史坑是合法的，不该被判红；只看真正生效的代码。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _gate_code() -> str:
    """门禁脚本里真正生效的行。"""
    return strip_comments(GATE.read_text(encoding="utf-8"))


def _timeout_values(text: str) -> list[int]:
    """非注释行里的 pytest 超时数值（秒）。"""
    return [int(m.group(1)) for m in _TIMEOUT_VALUE.finditer(strip_comments(text))]


@pytest.mark.parametrize("script", [GATE, TASK_PY], ids=["nn-python-gate.sh", "tools/task.py"])
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


def test_gate_only_converts_paths_for_windows_python() -> None:
    """路径转换必须**限定在「选中 Windows python」的分支内**（模块 docstring 第 6 条）。

    转换本身要留着（Windows python 在 WSL 下确实需要 `D:/...`），但它不能出现在分支外
    ——分支外无条件转换 = 给原生 python 喂 UNC/盘符形态，直接开不了文件（2026-09-20 事故）。
    """
    code = _gate_code()
    idx = code.find("wslpath -m")
    assert idx != -1, "Windows python 在 WSL 下的 wslpath 转换被删了？"
    assert "Scripts/python.exe" in code[:idx], (
        "wslpath 转换必须在「选中 .venv/Scripts/python.exe」的分支内——放在分支外无条件转换，"
        "原生 Linux venv 的 argv 也会被换成 Win32/UNC 形态（2026-09-20 门禁 0s 假红）"
    )
    assert re.search(r"NN_PY_WIN=\$NN_PY\b", code), (
        "原生 python 分支必须直通（NN_PY_WIN=$NN_PY）——POSIX 路径本就是它认的形态"
    )


@no_bash
def test_gate_keeps_native_paths_when_wslpath_maps_elsewhere(tmp_path: Path) -> None:
    """回归：wslpath 映射到「Linux 侧读不到」的命名空间时，原生 python 必须拿到 POSIX 路径。

    复现 2026-09-20 事故的**最小骨架**：假仓库（只有 `nn-training/.venv/bin/python`，
    没有 `Scripts/python.exe`）+ PATH 前置一个模拟本容器行为的假 wslpath（输出
    `//wsl.localhost/<distro>/…`）。旧版门禁把 detach-run.py 的路径换成该 UNC 路径，
    原生 python 开不了 ⇒ 假 `python` 记下的 argv 里就能看到证据；修正后应全程 POSIX。
    """
    skel = tmp_path / "skel"
    hook_dir = skel / "tools" / "githook"
    hook_dir.mkdir(parents=True)
    shutil.copy(GATE, hook_dir / GATE.name)

    # 假 python：把每次调用的 argv 记下来再 exit 0（门禁的三路工具、核数探测、detach 全走它）。
    # newline="\n" 必须写死：Windows 上 write_text 默认把 \n 译成 \r\n，shebang 变成
    # `#!/bin/sh\r` ⇒ WSL/bash 报「cannot execute: required file not found」（2026-09-20）。
    argv_log = tmp_path / "argv.log"
    argv_log_posix = _bash_path(argv_log)
    fake_py = skel / "nn-training" / ".venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$ARGV_LOG\"\nexit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_py.chmod(0o755)

    # 假 wslpath：模仿本容器 /usr/bin/wslpath 的映射（只有 Windows 侧能读的 UNC）。
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub_wslpath = stub_bin / "wslpath"
    stub_wslpath.write_text(
        "#!/bin/sh\nlast=\nfor a in \"$@\"; do last=$a; done\n"
        "printf '//wsl.localhost/opencode%s\\n' \"$last\"\n",
        encoding="utf-8",
        newline="\n",
    )
    stub_wslpath.chmod(0o755)

    # Windows Python → WSL bash 的 subprocess env **不可靠**（实测自定义变量一律空，
    # 2026-09-20）：PATH/ARGV_LOG 必须在 bash 进程内 export，否则假 python 的
    # `>> "$ARGV_LOG"` 变成 `>> ""`（Directory nonexistent）且 stub wslpath 不在 PATH。
    wrapper = tmp_path / "run-gate.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export PATH='{_bash_path(stub_bin)}':\"$PATH\"\n"
        f"export ARGV_LOG='{argv_log_posix}'\n"
        "export NN_GATE_SKIP=ruff,mypy\n"
        "export PYTHONUTF8=1\n"
        f"cd '{_bash_path(skel)}'\n"
        f"exec bash '{_bash_path(hook_dir / GATE.name)}'\n",
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o755)

    proc = subprocess.run(
        ["bash", _bash_path(wrapper)],
        cwd=skel,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"门禁骨架应跑通（rc=0）\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    detach = next((ln for ln in calls if "detach-run.py" in ln), None)
    assert detach is not None, f"没看到 detach 启动（python argv 记录：{calls}）"
    assert "wsl.localhost" not in detach, (
        "原生 python 的 argv 里出现了只读得到 Windows 侧的 UNC 路径——路径转换没有按"
        f"「python 是不是 Windows 二进制」判定（2026-09-20 事故回归）\n{detach}"
    )
    # WSL 下 `pwd` 是 /mnt/d/...；MSYS 下是 /d/...。`/d/...` 是 `/mnt/d/...` 的子串，
    # 故 _msys() 形态在两种 bash 下都能命中（见 _msys docstring）。
    assert _msys(hook_dir / "detach-run.py") in detach, (
        f"detach 脚本路径应是骨架内的 POSIX 绝对路径\n{_msys(hook_dir / 'detach-run.py')}\n{detach}"
    )
    assert _msys(fake_py) in detach, f"detach 内层 argv[0] 应是原生 python 的 POSIX 路径\n{detach}"


def test_gate_worker_count_scales_with_cores() -> None:
    """worker 数默认派生自**物理核数**（不再写死 4），且必须有上界（内存封顶）。"""
    code = _gate_code()
    assert "NN_GATE_NPROC" in code, "缺少 NN_GATE_NPROC 逃生口"
    assert re.search(r"CORES=\$\(.*physical_cores", code), (
        "worker 数应从核数派生，且核数走 common.platform_utils.physical_cores（真物理核 ∧ "
        "cgroup 配额 ∧ 亲和掩码取小）—— 2026-10-03 决议：门禁按**物理核**（超线程 worker "
        "不涨吞吐只涨内存，16 worker 的 bun test 把 Windows 提交上限顶穿 ⇒ 一片假红）；"
        "`os.cpu_count()` 在容器里报的是宿主机核数（Kaggle 224 vs 配额 96，2026-09-25 云机"
        "卡死那笔账），故配额/掩码仍是硬信号；写死 4 在 16 核上白白浪费并行度（2026-09-17 "
        "实测 n=4 → n=12 提速 1/3）"
    )
    assert "cpu_count()" not in code, (
        "门禁里不许再拿 os.cpu_count() 定并行度——「本机几核」只允许一个答案，"
        "就是 common.platform_utils.physical_cores()（注释里的事故说明不算，_gate_code 已去注释）"
    )
    assert re.search(r"NPROC=\$\{NN_GATE_NPROC:-\$CORES\}", code), (
        "NPROC 默认值应 = min(物理核数, 上界)，且由 NN_GATE_NPROC 覆盖"
    )
    cap = re.search(r'NPROC" -gt (\d+)', code)
    assert cap, "NPROC 缺上界——worker 无上限会按核数放大内存（-n 12 峰值 RSS 实测 ≈ 3.9GB）"
    assert int(cap.group(1)) == 32, (
        f"NPROC 上界应 = 32（2026-10-03 决议：从 12 提到 32，只给大机器兜内存；实得 "
        f"{cap.group(1)}）"
    )


def test_gate_picks_forkdist_by_python_and_kernel() -> None:
    """分发器选择（模块 docstring 第 7 条）：Windows python 一律 xdist，Linux 才 forkdist。"""
    code = _gate_code()
    assert "NN_GATE_FORKDIST" in code, "缺少强制选择分发器的逃生口（NN_GATE_FORKDIST=0/1）"
    assert re.search(r"case \"\$NN_PY\" in", code), (
        "分发器选择应由「选中的 python 是不是 Windows 二进制」判定（case $NN_PY in *.exe），"
        "不要问 uname/wslpath 存不存在（同 2026-09-20 那次事故的判据）"
    )
    assert re.search(r"\*\.exe\) : ;;", code), "少了「Windows python ⇒ 不选 forkdist」的分支"
    assert re.search(r"uname -s[^\n]*Linux", code), (
        "非 Linux（macOS）应继续用 xdist：master 在 fork 前已经 import torch，"
        "libgomp/dyld 与 fork 的组合本仓没验证过"
    )
    assert '-n "$NPROC"' in code, "Windows 分支必须保留 xdist 的 -n（那是它的唯一分发器）"
    assert "-p tools.forkdist" in code, "forkdist 分支没把插件挂上"


@no_bash
@pytest.mark.parametrize(
    ("py_rel", "expect_forkdist"),
    [
        # Windows 二进制 python（MSYS/WSL 两种 bash 都长这样）：唯一没 os.fork 的情形
        (Path("nn-training/.venv/Scripts/python.exe"), False),
        # 原生 POSIX python：Linux 上应选 forkdist（macOS 见下方 skip 说明）
        (Path("nn-training/.venv/bin/python"), True),
    ],
    ids=["windows-python", "posix-python"],
)
def test_gate_dispatcher_branch_is_pinned_by_python_flavor(
    tmp_path: Path, py_rel: Path, expect_forkdist: bool
) -> None:
    """真跑一次门禁骨架，看它到底给 pytest 发了哪个分发器（模块 docstring 第 7 条）。

    用一个只记 argv 的假 python（同 `test_gate_keeps_native_paths_when_wslpath_maps_elsewhere`
    的手法）——比 grep 脚本文本强：文本里有分支不等于分支真的走对。
    macOS（uname=Darwin）上 POSIX python 也**应该**走 xdist（fork 与 libgomp 未验证），
    故那一半在非 Linux 上跳过。
    """
    if expect_forkdist and not sys.platform.startswith("linux"):
        pytest.skip("非 Linux：POSIX python 也走 xdist（保守选择，见门禁「pytest 分发器」一节）")

    skel = tmp_path / "skel"
    hook_dir = skel / "tools" / "githook"
    hook_dir.mkdir(parents=True)
    shutil.copy(GATE, hook_dir / GATE.name)

    argv_log = tmp_path / "argv.log"
    fake_py = skel / py_rel
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$ARGV_LOG\"\nexit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_py.chmod(0o755)

    wrapper = tmp_path / "run-gate.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export ARGV_LOG='{_bash_path(argv_log)}'\n"
        "export NN_GATE_SKIP=ruff,mypy\n"
        "export PYTHONUTF8=1\n"
        f"cd '{_bash_path(skel)}'\n"
        f"exec bash '{_bash_path(hook_dir / GATE.name)}'\n",
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o755)

    proc = subprocess.run(
        ["bash", _bash_path(wrapper)],
        cwd=skel,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"骨架应跑通（rc=0）\n{proc.stdout}\n{proc.stderr}"
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    pytest_call = next((ln for ln in calls if "-m pytest" in ln), "")
    assert pytest_call, f"没看到 pytest 调用（argv 记录：{calls}）"
    if expect_forkdist:
        assert "--forkdist" in pytest_call, f"POSIX python 没走 forkdist：{pytest_call}"
        assert not re.search(r"\s-n\s", pytest_call), f"forkdist 分支还搭了 -n：{pytest_call}"
    else:
        assert "--forkdist" not in pytest_call, (
            f"Windows python 走了 forkdist（没有 os.fork，必然失败）：{pytest_call}"
        )
        assert re.search(r"\s-n\s", pytest_call), f"Windows 分支丢了 xdist 的 -n：{pytest_call}"


def test_gate_never_passes_n_and_forkdist_together() -> None:
    """`-n` 与 `--forkdist` 互斥（模块 docstring 第 7 条）：同一行同时给会抢 runtestloop。"""
    offenders = [
        ln.strip()
        for ln in _gate_code().splitlines()
        if "-m pytest" in ln and "--forkdist" in ln and re.search(r"\s-n\s", ln)
    ]
    assert not offenders, f"同一行既给 -n 又给 --forkdist：{offenders}"


def _pid_alive(pid: int) -> bool:
    """pid 是否还在（同 pid 未被回收时 kill 0 无副作用）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@no_bash
@pytest.mark.skipif(
    os.name != "posix",
    reason="假 python 是 POSIX sh 脚本；Windows 的 taskkill 连树无法用它复现，"
    "改由 test_gate_stops_other_legs_for_windows_python_with_taskkill 静态钉住",
)
@pytest.mark.parametrize("red", ["ruff", "mypy"])
def test_gate_fails_fast_when_a_static_tool_is_red(tmp_path: Path, red: str) -> None:
    """任一腿先红 ⇒ 停掉其余腿、不等 pytest 跑完（模块 docstring 第 8 条）。

    真跑一次门禁（不是 grep 文本）：假 python 记账 argv；`red` 腿睡 1s 再非零退出（给
    pytest 足够启动窗口 ⇒ 无竞态）；pytest 腿写 pid 后睡 20s，睡够才写完成标记。断言：
    ① 门禁 rc≠0；② 墙钟 <10s（诚实 fail-fast ≈1s；没停腿会陪跑 ~21s）；③ pytest 起过；
    ④ 没跑完（完成标记缺席）；⑤ **pytest 进程真的死了**——hook 模式下 detach-run 若不 exec
    目标，杀到的只是壳，真 pytest 会变孤儿继续烧 CPU（这正是要抓的回归）。
    """
    skel = tmp_path / "skel"
    hook_dir = skel / "tools" / "githook"
    hook_dir.mkdir(parents=True)
    shutil.copy(GATE, hook_dir / GATE.name)
    # detach-run.py 必须真在场：假 python 对它的调用是 exec 真解释器（见下），跑不到文件
    # 的话三道腿会以「can't open file」秒红——"fail-fast 生效"的假绿。
    shutil.copy(
        REPO_ROOT / "tools" / "githook" / "detach-run.py", hook_dir / "detach-run.py"
    )

    argv_log = tmp_path / "argv.log"
    pid_log = tmp_path / "pytest.pid"
    done_log = tmp_path / "pytest.done"
    real_py = _bash_path(Path(sys.executable))
    fake_py = skel / "nn-training" / ".venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    # 假 python 三分支：① 记账；② 被 detach-run 调用 ⇒ **exec 真解释器**跑 detach-run.py
    # （不真跑它就测不到「pid 是否贯穿到工具本体」，见断言 ⑤）；③ 各工具腿的行为。
    fake_py.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$ARGV_LOG\"\n"
        "case \"$1\" in\n"
        f"  *detach-run.py) exec '{real_py}' \"$@\" ;;\n"
        "esac\n"
        "case \"$*\" in\n"
        f"  *\"-m {red}\"*) sleep 1; exit 7 ;;\n"
        "  *\"-m pytest\"*)\n"
        "    printf '%s' \"$$\" > \"$PID_LOG\"\n"
        "    sleep 20\n"
        "    : > \"$DONE_LOG\" ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_py.chmod(0o755)

    wrapper = tmp_path / "run-gate.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export ARGV_LOG='{_bash_path(argv_log)}'\n"
        f"export PID_LOG='{_bash_path(pid_log)}'\n"
        f"export DONE_LOG='{_bash_path(done_log)}'\n"
        "export PYTHONUTF8=1\n"
        # 清空继承旋钮（用例必须让指定的静态腿真跑）：pre-commit 钩子会给门禁灌
        # NN_GATE_SKIP=ruff（它自己先跑 staged ruff）——不清零时 ruff 腿被跳过，
        # 「ruff 红 ⇒ 停其余腿」这条断言就永远不成立（2026-10-05 首次提交现场）。
        "export NN_GATE_SKIP=''\n"
        f"cd '{_bash_path(skel)}'\n"
        f"exec bash '{_bash_path(hook_dir / GATE.name)}'\n",
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o755)

    t0 = time.monotonic()
    proc = subprocess.run(
        ["bash", _bash_path(wrapper)],
        cwd=skel,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    elapsed = time.monotonic() - t0
    assert proc.returncode != 0, (
        f"{red} 应让门禁红（rc={proc.returncode}）\n{proc.stdout}\n{proc.stderr}"
    )
    # timing-ok: 相对判据（阈值 = 假 pytest 腿 20s 的一半；真 fail-fast ≈1s，陪跑才会 ~21s）
    assert elapsed < 10.0, (
        f"{red} 已红，门禁却等了 {elapsed:.1f}s——fail-fast 没发生（假 pytest 要睡 20s）\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert any("-m pytest" in ln for ln in calls), f"pytest 没被启动（argv：{calls}）"
    assert not done_log.exists(), "pytest 跑完了——它没有被中止？"
    # ⑤ 收尸窗口 ≤5s（kill 是异步的）；还活着就先清理再判红，别把孤儿留在测试机上。
    pid = int(pid_log.read_text(encoding="utf-8"))
    for _ in range(50):
        if not _pid_alive(pid):
            break
        # sleep-ok: 轮询步长（等的是「进程已死」这个谓词，超时只当挂起兜底）
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        pytest.fail(
            f"pytest（pid {pid}）还活着——fail-fast 杀到的只是 detach-run 壳，真 pytest 成了孤儿"
        )


def test_gate_stops_other_legs_for_windows_python_with_taskkill() -> None:
    """fail-fast 的 Windows 路径：`.exe` python 必须 taskkill /T 连树停腿（docstring 第 8 条）。

    Windows 上 detach-run 是 Popen+wait 的壳（DETACHED_PROCESS 靠壳成立），kill 壳会把内层
    pytest 与它的 xdist worker 留成孤儿 ⇒ 停腿必须连树杀。判据同全脚本其它平台分支：看选中的
    python 是不是 .exe，不看 uname（POSIX 侧由上面那条真跑门禁的行为档验证）。
    """
    code = _gate_code()
    assert re.search(r"\*\.exe\)\s*taskkill[^\n]*//F //T //PID", code), (
        "缺少「Windows python ⇒ taskkill //F //T //PID」的 fail-fast 停腿路径"
    )
