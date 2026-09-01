"""Shared pytest fixtures for nn-training pure-logic tests."""
from __future__ import annotations

import sys
import tempfile
import types
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
def tmp() -> Path:
    """Legacy test_run_rl.py compat: mirrors pytest's tmp_path API used across tests."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
