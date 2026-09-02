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
    """Legacy test_run_rl.py compat：直接复用（被覆盖的）tmp_path。"""
    yield tmp_path


@pytest.fixture
def tmp_path(request) -> Path:
    """覆盖内置 tmp_path：项目内唯一目录、**零删除**（沙箱批量删除保护适配）。

    内置 tmp_path 依赖 pytest 的 basetemp——pytest 每次启动会**清空** basetemp
    （一次性删除全部累积文件，>50 触发沙箱 SAFE_DELETE_BULK_CONFIRM_REQUIRED：
    交互式弹确认、pre-commit hook 等无交互场景直接 SystemExit 失败）。

    本实现：
      * 目录落在 `nn-training/tmp/pytest-tmp/<nodeid>-<id>`（已 gitignore）；
      * 每个测试唯一目录、**从不删除**（磁盘增长可接受，手动清理一次即可）；
      * pytest 的 basetemp 不再被创建/清空 → 全程零删除、零弹窗。
    """
    root = Path(__file__).resolve().parent.parent / "tmp" / "pytest-tmp"
    root.mkdir(parents=True, exist_ok=True)
    safe = request.node.nodeid.replace("/", "__").replace("::", "__")
    d = root / f"{safe}-{id(request.node) & 0xFFFF}"
    d.mkdir(parents=True, exist_ok=True)
    return d
