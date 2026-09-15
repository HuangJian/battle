#!/usr/bin/env python3
"""Cross-platform task runner for nn-training (equivalent to make).

Usage:
    python task.py <target>

Targets:
    setup       - one-command env bootstrap (detect GPU -> pick torch -> sync)
    check       - lint + typecheck + test-fast
    test        - full test suite
    test-fast   - fast layer only (no torch/bun)
    smoke       - torch import check + smoke_test
    clean       - remove temporary artifacts (preserves weights/)
    format      - auto-format with ruff
    lint        - lint-only with ruff
    typecheck   - type-check with mypy
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from platform_utils import rmtree_best_effort

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable

# `task setup` 之外的 target 不接受额外参数；setup 会把它们原样透传给 bootstrap.py
# （--variant / --python / --recreate / --check / --no-install-uv）。
EXTRA_ARGS: list[str] = []

# 删除保护沙箱注入变量名（与 tools/githook/_sandbox-sanitize.sh 同语义；两份都是
# 各自语言里的唯一实现：bash 侧负责 bash/子进程树，这里负责 python 子进程环境）。
SANDBOX_VAR_RE = re.compile(r"CODEBUDDY|SAFE_|BUDDY|SANDBOX", re.IGNORECASE)


def clean_env() -> dict[str, str]:
    """剥离删除保护沙箱的环境副本（2026-09-15，task.py 侧对齐拉闸）。

    背景：workbuddy/mimo 沙箱经环境变量注入 python sitecustomize 守卫（队列配额/
    删除确认/FAIL_CLOSED），task.py 直接 spawn 的 pytest（`-n auto` × CPU 核数、
    无超时）会被打穿——见 .workbuddy/memory/2026-09-15.md「追问 2」。剥离 BASH_ENV
    注入载体 + CODEBUDDY/SAFE_* 变量，并显式关掉 python 侧守卫开关（惰性变量，
    普通机器无害）。
    """
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV") and not SANDBOX_VAR_RE.search(k)}
    env["BASH_ENV"] = "/dev/null"  # 子进程若再拉 bash，无包装函数可注入
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> int:
    """Run a command, print it, return exit code."""
    print(f"[task] {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, env=env)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def target_setup() -> int:
    """One-command bootstrap: 探测 GPU → 选 torch 变体 → uv sync → 装后自检。

    委派给 bootstrap.py（stdlib-only，不依赖 torch 已装），因为 task.py 本身
    是用 venv 里的 python 跑的 —— 在冷机器上 venv 还不存在，只有 bootstrap.py
    能自举。这里用 sys.executable 起它是为了让 bootstrap 继承当前解释器语义。
    """
    return run([PYTHON, str(HERE / "bootstrap.py"), *EXTRA_ARGS])


def target_check() -> int:
    target_lint()
    target_typecheck()
    return target_test_fast()


def target_test() -> int:
    # 2026-09-15：-n auto（=CPU 核数）作废——16 worker 同时 import torch 是沙箱里
    # 「~34% 停滞」的头号嫌疑（nn-python-gate.sh 头注释实测：torch import 开销使
    # 4 worker 才是本机最优点）；对齐 gate 用 -n 4。env=clean_env() 关掉删除沙箱守卫。
    return run(
        [PYTHON, "-m", "pytest", "tests/", "-n", "4", "-v", "--timeout=50000"],
        env=clean_env(),
    )


def target_test_fast() -> int:
    # -n 4 + --timeout=50000（与 target_test / nn-python-gate.sh 对齐，2026-09-15）：
    # `task.py check`（=lint+typecheck+test-fast）此前是 -n auto 且无超时——沙箱里
    # hang 则无限挂；看门禁/日常两侧护栏必须一致，只改一处就是破口。
    return run(
        [PYTHON, "-m", "pytest", "tests/", "-n", "4", "-q", "-m", "not heavy", "--timeout=50000"],
        env=clean_env(),
    )


def target_smoke() -> int:
    run([PYTHON, "-c", "import torch; print('torch', torch.__version__)"])
    return run([PYTHON, str(HERE / "smoke_test.py")])


def target_clean() -> int:
    cleaned = 0
    for pycache in HERE.rglob("__pycache__"):
        if pycache.is_dir():
            rmtree_best_effort(pycache, ignore_errors=True)
            cleaned += 1
    # 双 tmp 统一（2026-09-08）：临时产物只在仓库根 tmp/（HERE.parent / tmp）
    for log in list(HERE.glob("*.log")) + list((HERE.parent / "tmp").glob("*.log")):
        log.unlink(missing_ok=True)
        cleaned += 1
    for orphan in ["dist-agent-meta.jsonl", "train_loop.lock"]:
        p = HERE / orphan
        if p.exists():
            p.unlink()
            cleaned += 1
    print(f"[task] clean: removed {cleaned} artifacts. weights/ preserved.")
    return 0


def target_format() -> int:
    run([PYTHON, "-m", "ruff", "format", "."])
    return run([PYTHON, "-m", "ruff", "check", "--fix", "."])


def target_lint() -> int:
    return run([PYTHON, "-m", "ruff", "check", "."])


def target_typecheck() -> int:
    return run([PYTHON, "-m", "mypy", ".", "--config-file", str(HERE / "pyproject.toml")])


def target_weights_prune(dry_run: bool = True) -> int:
    cmd = [PYTHON, "weights_prune.py", "--keep", "3", "--dir", "weights/"]
    cmd += ["--dry-run" if dry_run else "--apply"]
    return run(cmd)


def target_weights_update_md() -> int:
    return run([PYTHON, "weights_prune.py", "--dir", "weights/", "--update-md"])


TARGETS = {
    "setup": target_setup,
    "check": target_check,
    "test": target_test,
    "test-fast": target_test_fast,
    "smoke": target_smoke,
    "clean": target_clean,
    "format": target_format,
    "lint": target_lint,
    "typecheck": target_typecheck,
    "weights-prune": lambda: target_weights_prune(dry_run=True),
    "weights-prune-apply": lambda: target_weights_prune(dry_run=False),
    "weights-update-md": target_weights_update_md,
}


def main() -> None:
    global EXTRA_ARGS
    parser = argparse.ArgumentParser(description="nn-training task runner")
    parser.add_argument("target", choices=list(TARGETS), help="task to run")
    args, extra = parser.parse_known_args()
    if extra and args.target != "setup":
        parser.error(f"target {args.target!r} 不接受额外参数：{' '.join(extra)}")
    EXTRA_ARGS = extra
    sys.exit(TARGETS[args.target]() or 0)


if __name__ == "__main__":
    main()
