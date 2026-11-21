"""`common/warn_once.py` —— 「同一个 key 只告警一次」的去重桶（P0-5，2026-11-21）。

三份同形状的模块级 `set`（`trainer/loop_transport` 两份 + `worker/gate_halt` 一份）收成一个类。
判据有四条：`mark()` 只在第一次为真、`seen()` 只查不记、两份桶互不相见、`clear()` 能重来。
"""

from __future__ import annotations

from common.warn_once import WarnOnce


def test_mark_is_true_only_the_first_time() -> None:
    bucket = WarnOnce()
    assert bucket.mark(("c5", "run")) is True
    assert bucket.mark(("c5", "run")) is False
    assert bucket.mark(("c5", "node")) is True, "不同的 key 各自第一次"
    assert len(bucket) == 2


def test_seen_queries_without_recording() -> None:
    """`seen()` 只查不记 —— `gate_halt` 靠它做到「文件不存在就不烧 key」。"""
    bucket = WarnOnce()
    assert bucket.seen("k") is False
    assert bucket.seen("k") is False
    assert len(bucket) == 0, "查询不得写进桶里"
    bucket.mark("k")
    assert bucket.seen("k") is True
    assert len(bucket) == 1


def test_buckets_are_independent() -> None:
    """要重来就换一份新桶（没有 `clear()`）：整份换新比逐个清表少一种漏清形态。"""
    a, b = WarnOnce(), WarnOnce()
    a.mark("k")
    assert b.seen("k") is False, "两份桶互不相见"
    fresh = WarnOnce()
    assert fresh.mark("k") is True, "新桶里同一个 key 能重新告警"
    assert len(a) == 1 and len(fresh) == 1
