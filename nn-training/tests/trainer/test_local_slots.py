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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trainer.dispatch_queue import local_slots_max_of
from trainer.queue_local import split_local_head


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


def test_local_head_rotates_with_remote_and_keeps_quota() -> None:
    tasks = [(2000, i) for i in range(1, 7)]
    head, rest = split_local_head(tasks, 2)
    assert head == [(2000, 2), (2000, 4)], head
    assert rest == [(2000, 1), (2000, 3), (2000, 5), (2000, 6)], rest
    assert sorted(head + rest) == tasks


def test_local_head_zero_and_full_are_degenerate() -> None:
    tasks = [(2000, 1), (2000, 2)]
    assert split_local_head(tasks, 0) == ([], tasks)
    assert split_local_head(tasks, 2) == (tasks, [])
    assert split_local_head(tasks, 9) == (tasks, [])


def test_local_head_keeps_exact_share_when_remote_quota_is_small() -> None:
    tasks = [(2000, i) for i in range(1, 5)]
    head, rest = split_local_head(tasks, 3)
    assert len(head) == 3 and len(rest) == 1, (head, rest)
    assert sorted(head + rest) == tasks
