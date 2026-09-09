"""test_local_slots.py — local_slots 语义单测（2026-09-09 统一）。

语义：rl.local_slots 的取值含义在各 rollout 路径必须一致——
  0        = 关闭本机直跑（全交给远端节点；远端失联仍自动兜底接管）
  > 0      = 显式槽位数
  None/负  = 未设置 → auto（stream: max(2, workers//4)；queue: workers 封顶）

历史事故：collect_only 两条调用漏传 local_slots_max ⇒ 配置恒按 workers 满额并发
（1 slot 变 8 slot，与 self 7 slot 局数相近）；rollout_phase 的 `or None` 把 0
吞成 None（想关关不掉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.queue import local_slots_max_of


class _Args:
    def __init__(self, v):
        self.local_slots = v


def test_positive_slot_count_passthrough() -> None:
    assert local_slots_max_of(_Args(3)) == 3


def test_zero_means_disabled_local() -> None:
    """0 = 显式关闭本机直跑（不得被吞成 None/auto）。"""
    assert local_slots_max_of(_Args(0)) == 0


def test_unset_means_auto() -> None:
    assert local_slots_max_of(_Args(None)) is None
    assert local_slots_max_of(_Args(-1)) is None  # 负 = 未设置


def test_string_input_tolerant() -> None:
    assert local_slots_max_of(_Args("2")) == 2
    assert local_slots_max_of(_Args("x")) is None  # 脏值退 auto 而非崩溃
