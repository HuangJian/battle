"""覆盖率报表的硬闸语义：SHORT 是读数，不是失败；重复 pair / 空关才是失败。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from common.errors import ProtocolError
from tools.coverage_report import coverage_rows, render


def _shard(root: Path, stage: int, seed: int, n: int) -> None:
    d = root / f'rl_s{stage}_seed{seed}'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'manifest.json').write_text(
        json.dumps({'stage': stage, 'seed': seed, 'nSamples': n}), encoding='utf-8'
    )


def test_short_is_a_readout_not_a_failure(tmp_path: Path) -> None:
    """供给不足照样能训 ⇒ 不进 problems；只有空关/重复 pair 才让工具返回非 0。"""
    root = tmp_path / 'shards'
    for s in (0, 1):
        _shard(root, s, 1, 10)
    rows, problems = coverage_rows([p for p in root.iterdir() if p.is_dir()],
                                   target_transitions=40, expect_stages=2)
    assert problems == []
    assert all(r.short > 0 for r in rows)
    text = render(rows, target_transitions=40, problems=problems, source=str(root))
    assert sum(r.short for r in rows) == 20  # 两关各缺 10（quota=ceil(40/2)=20）
    assert len(text.splitlines()) > 10  # 报表把 SHORT 段落打出来了


def test_duplicate_pair_and_empty_stage_are_hard_failures(tmp_path: Path) -> None:
    root = tmp_path / 'shards'
    _shard(root, 0, 1, 10)
    # 目录名不同但 manifest 的 (stage,seed) 相同 ⇒ 真重复 pair
    d = root / 'rl_s0_seed_dup'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'manifest.json').write_text(
        json.dumps({'stage': 0, 'seed': 1, 'nSamples': 10}), encoding='utf-8'
    )
    _shard(root, 3, 9, 0)   # 有效样本为 0
    rows, problems = coverage_rows([p for p in root.iterdir() if p.is_dir()],
                                   target_transitions=40, expect_stages=None)
    assert len(problems) == 2, '重复 pair + 空关各一条硬闸问题'
    assert rows[0].valid is True  # stage 0 仍有样本
    assert rows[1].valid is False  # stage 3 的 nSamples=0 ⇒ 无有效样本


def test_expect_stages_ledger_is_enforced(tmp_path: Path) -> None:
    root = tmp_path / 'shards'
    _shard(root, 0, 1, 10)
    with pytest.raises(ProtocolError):
        coverage_rows([p for p in root.iterdir() if p.is_dir()],
                      target_transitions=10, expect_stages=35)
