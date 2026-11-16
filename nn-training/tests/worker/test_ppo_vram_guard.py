"""eager 全量上卡前的显存预检（用户 2026-11-16：搬语料前先查够不够）。

被测物是 `worker/ppo/vram.py` 的**纯判定**部分；后端探测（torch/paddle）在 CPU-only 机器上
一律返回 None = 放行，所以本文件三条路径全都能在没 GPU 的机器上钉住：
  ① 探测不到 ⇒ 不拦（CPU 训练不能因为加了保护就跑不起来）；
  ② 显存够 ⇒ 不拦；
  ③ **明确测到不够 ⇒ 响亮拒绝**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from worker.ppo import vram


def test_corpus_bytes_sums_eager_chunks() -> None:
    chunks = [
        {'obs': np.zeros((4, 2), dtype=np.uint8), 'scalars': np.zeros((4, 3), dtype=np.float32)},
        {'obs': np.zeros((6, 2), dtype=np.uint8)},
    ]
    # 4*2 + 4*3*4 + 6*2 = 8 + 48 + 12
    assert vram.corpus_bytes(chunks) == 68
    assert vram.corpus_bytes([]) == 0


def test_unknown_free_does_not_block() -> None:
    """CPU / 探测失败 ⇒ None ⇒ 放行（保护不能把能跑的训练拦下来）。"""
    vram.require_bytes(10 * 1024**3, None)
    vram.require_corpus([{'obs': np.zeros((10,), dtype=np.uint8)}], None)


def test_enough_free_does_not_block() -> None:
    vram.require_bytes(1000, 1200)  # 含 6/5 余量 = 1200，刚好够
    vram.require_bytes(1000, 10_000)


def test_insufficient_free_raises_loudly() -> None:
    with pytest.raises(ProtocolError):
        vram.require_bytes(1000, 1199)  # 差 1 字节也不许静默继续
    with pytest.raises(ProtocolError):
        vram.require_corpus([{'obs': np.zeros((1000,), dtype=np.uint8)}], 1)


def test_free_probes_are_wired_for_both_backends() -> None:
    """两个后端必须同改同挂（Paddle 双生纪律），少一个就是静默失效的承诺。"""
    assert set(vram.FREE_PROBES) == {'torch', 'paddle'}
    for probe in vram.FREE_PROBES.values():
        # CPU-only 机器上都应返回 None；GPU 机器上返回 int —— 只钉类型，不钉具体值。
        got = probe('cpu')
        assert got is None or isinstance(got, int)
