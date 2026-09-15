"""tools/githook 脚本的可执行性 / 接线护栏。

两个已发生过的坑，各一条断言：

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

no_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash 不在 PATH（无法验证 shell 脚本）")


@no_bash
def test_py_safe_wrapper_launches_pytest() -> None:
    """`nn-py-safe.sh -m pytest` 必须真能把 pytest 起起来（Linux/MSYS 都要成立）。"""
    proc = subprocess.run(
        ["bash", str(WRAPPER), "-m", "pytest", "--version"],
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
    # 只看非注释行 —— 在注释里解释这个坑是合法的，不该被判红。
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "not heavy" not in code, "层应由路径决定，不要回到标记过滤"


def _timeout_values(text: str) -> list[int]:
    """非注释行里的 pytest 超时数值（秒）。"""
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return [int(m.group(1)) for m in _TIMEOUT_VALUE.finditer(code)]


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
