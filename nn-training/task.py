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
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable

# `task setup` 之外的 target 不接受额外参数；setup 会把它们原样透传给 bootstrap.py
# （--variant / --python / --recreate / --check / --no-install-uv）。
EXTRA_ARGS: list[str] = []


def run(cmd: list[str], *, check: bool = False) -> int:
    """Run a command, print it, return exit code."""
    print(f"[task] {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
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
    return run([PYTHON, "-m", "pytest", "tests/", "-n", "auto", "-v", "--timeout=50000"])


def target_test_fast() -> int:
    # -n auto：按 CPU 核数自适应（旧版硬编码依赖 Makefile，这里原本连 xdist 都没启用）
    return run([PYTHON, "-m", "pytest", "tests/", "-n", "auto", "-q", "-m", "not heavy"])


def target_smoke() -> int:
    run([PYTHON, "-c", "import torch; print('torch', torch.__version__)"])
    return run([PYTHON, str(HERE / "smoke_test.py")])


def target_clean() -> int:
    cleaned = 0
    for pycache in HERE.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
            cleaned += 1
    for log in list(HERE.glob("*.log")) + list((HERE / "tmp").glob("*.log")):
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
