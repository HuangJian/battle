"""Shared pytest fixtures for nn-training pure-logic tests."""
from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def bp_args(sps: int = 3, rotate_stages: int = 35, total_stages: int = 35,
            seeds: str = "0-3", stages: str = "0-3") -> types.SimpleNamespace:
    """Minimal duck-typed args for build_pairs()."""
    return types.SimpleNamespace(
        rotate_stages=rotate_stages,
        seeds_per_stage=sps,
        total_stages=total_stages,
        stages=stages,
        seeds=seeds,
        curriculum_stages="",
        curriculum_start=4,
        curriculum_every=8,
        curriculum_grow=4,
        seed_rotate=0,
    )


@pytest.fixture
def tmp(tmp_path: Path) -> Iterator[Path]:
    """Legacy test_run_rl.py compat.

    2026-09-02 改为直接复用 pytest 内置 tmp_path：此前用
    `tempfile.TemporaryDirectory()` 在系统 %TEMP% 创建/删除临时目录，叠加 pytest
    启动时的 basetemp 垃圾回收，每次跑测试都触发沙箱删除权限请求。现在 basetemp
    已配置到项目内 tmp/pytest-tmp（pyproject.toml），一切临时文件都在工作区内。
    """
    yield tmp_path
