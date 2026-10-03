"""test_nproc.py —— `tools/nproc.py`（CI 的核数入口）的契约（2026-10-03）。

为什么单列：CI 的并行度靠它现探（workflow 里 `uv run python tools/nproc.py`），而 CI 的路径
门禁（`tests/test_ci_workflow_paths.py`）只校验「文件存在」，**不校验它输出什么**。这条钉住
输出是「一行裸整数」（workflow 直接把它喂给 `-n`），且与 `physical_cores()` 同源而非第二份实现。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NPROC = ROOT / "tools" / "nproc.py"


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NPROC)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_nproc_cli_prints_a_bare_integer() -> None:
    """stdout 必须是纯整数（能被 workflow 的 `-n "$(...)"` 直接吃下）。"""
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out.isdigit(), f"nproc.py 应打印一行裸整数，实得 {out!r}"
    assert int(out) >= 1


def test_nproc_matches_physical_cores() -> None:
    """与 `common.platform_utils.physical_cores()` 同源（不是第二份实现）。"""
    sys.path.insert(0, str(ROOT))
    try:
        from common.platform_utils import physical_cores
    finally:
        sys.path.pop(0)
    assert int(_run().stdout.strip()) == physical_cores()
