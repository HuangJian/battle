"""`worker/resume.ScanCache` —— 目录签名缓存的对象化（P0-5，2026-11-21）。

原来它是模块级 `_SCAN_CACHE: dict[tuple, ...]` + 调用点里手写的「满了先 clear」。收成对象后
两条**失效时机**必须钉住，否则它退化成一个永不失效的字典：

1. **签名变 ⇒ 新 key**：`completed_pairs` 把 `_dir_signature()` 放进 key（新 shard / shard 写完
   manifest 都会改签名）⇒ 命中只发生在「磁盘这 2 秒没变」时。这条由 `completed_pairs` 的行为
   用例（`tests/worker/test_rl_resume.py` 与 e2e）覆盖，这里只钉缓存本身。
2. **条目满 ⇒ 整表清空**（不是 LRU）：这是原实现语义，给「条目上限」一个可断言的结果。
"""

from __future__ import annotations

from pathlib import Path

from worker.resume import SCAN_CACHE_MAX, ScanCache


def test_lookup_misses_until_remembered() -> None:
    cache = ScanCache()
    assert cache.lookup(("k",)) is None
    assert len(cache) == 0
    cache.remember(("k",), [((0, 111), Path("/tmp/x"))])
    got = cache.lookup(("k",))
    assert got is not None and got[0][0] == (0, 111)
    assert cache.lookup(("other",)) is None, "不同的 key 各自独立"


def test_full_cache_is_cleared_wholesale_not_lru() -> None:
    """满了**整表清空**（原语义）：留下的是新那一条，旧 key 全部落空。"""
    cache = ScanCache(max_entries=2)
    cache.remember(("a",), [])
    cache.remember(("b",), [])
    assert len(cache) == 2
    cache.remember(("c",), [])
    assert len(cache) == 1, "满了应当整表清空再写"
    assert cache.lookup(("c",)) == []
    assert cache.lookup(("a",)) is None and cache.lookup(("b",)) is None


def test_forget_all_and_default_size() -> None:
    cache = ScanCache()
    cache.remember(("a",), [])
    cache.forget_all()
    assert len(cache) == 0, "forget_all() 之后必须重扫"
    assert SCAN_CACHE_MAX >= 1, "上限必须是正数（`max(1, …)` 的兜底不掩盖配置错）"


def test_caches_are_independent() -> None:
    """用例自建一份就隔离：不再靠改模块变量（那份仍由 `scan_cache()` 只读暴露）。"""
    a, b = ScanCache(), ScanCache()
    a.remember(("k",), [])
    assert b.lookup(("k",)) is None
    assert len(a) == 1 and len(b) == 0
