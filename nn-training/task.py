#!/usr/bin/env python3
"""Cross-platform task runner for nn-training (equivalent to make).

Usage:
    python task.py <target>

Targets:
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


def run(cmd: list[str], *, check: bool = False) -> int:
    """Run a command, print it, return exit code."""
    print(f"[task] {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def target_check() -> int:
    lint()
    typecheck()
    return test_fast()


def target_test() -> int:
    test_fast()
    pytest_path = shutil.which("pytest") or "pytest"
    return run([PYTHON, "-m", pytest_path, "tests/", "-v", "--timeout=50000"])


def target_test_fast() -> int:
    return run([PYTHON, str(HERE / "test_run_rl.py")])


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


TARGETS = {
    "check": target_check,
    "test": target_test,
    "test-fast": target_test_fast,
    "smoke": target_smoke,
    "clean": target_clean,
    "format": target_format,
    "lint": target_lint,
    "typecheck": target_typecheck,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="nn-training task runner")
    parser.add_argument("target", choices=TARGETS.keys(), help="task to run")
    args = parser.parse_args()
    sys.exit(TARGETS[args.target]() or 0)


if __name__ == "__main__":
    main()
