"""扫描缓存的口径守卫（`tests/helpers/scan_cache.py`，2026-11-01）。

它只许「快」，不许「错」：键是内容哈希（内容一改必换键）、版本不符整库作废、库坏/写不了
退化成「没命中」。两个大厅级守卫（锁重入扫描、文本断言计数）都把它当加速器 ⇒ 缓存错了
就是判据错了，所以这几条比「命中率」重要得多。
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import scan_cache


def _cache(tmp_path: Path, *, version: int = 1) -> scan_cache.Cache:
    return scan_cache.Cache("t", version=version, root=tmp_path, path=tmp_path / "c.pkl")


def test_miss_then_hit_after_save(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.get("a.py", "x = 1\n") is None
    cache.put("a.py", "x = 1\n", (3, 1))
    cache.save()
    again = _cache(tmp_path)
    assert again.get("a.py", "x = 1\n") == (3, 1)
    assert again.get("a.py", "x = 2\n") is None, "内容变了还命中 ⇒ 读到的是旧结果"


def test_version_bump_invalidates_everything(tmp_path: Path) -> None:
    cache = _cache(tmp_path, version=1)
    cache.put("a.py", "x = 1\n", 7)
    cache.save()
    assert _cache(tmp_path, version=2).get("a.py", "x = 1\n") is None


def test_same_content_under_another_path_is_another_entry(tmp_path: Path) -> None:
    """`_Module.path` 会进锁身份（`("local", 路径, 函数, 名字)`）⇒ 键必须含路径。"""
    cache = _cache(tmp_path)
    cache.put("a/p.py", "x = 1\n", "A")
    assert cache.get("a/p.py", "x = 1\n") == "A"
    assert cache.get("b/p.py", "x = 1\n") is None


def test_full_scan_save_drops_versions_nobody_asks_for_again(tmp_path: Path) -> None:
    """改过的文件会留下旧内容的条目；不清就随编辑次数无限长。"""
    cache = _cache(tmp_path)
    cache.put("a.py", "old\n", 1)
    cache.save()
    again = _cache(tmp_path)
    assert again.get("a.py", "new\n") is None  # 内容变了 ⇒ 冷
    again.put("a.py", "new\n", 2)
    again.save(full_scan=True)
    third = _cache(tmp_path)
    assert third.get("a.py", "new\n") == 2
    assert third.get("a.py", "old\n") is None, "旧内容的键没人再问，留着只会越长越大"


def test_partial_save_keeps_the_entries_it_did_not_walk(tmp_path: Path) -> None:
    """局部扫描（不传 `full_scan`）不许顺手删掉没走到的文件。"""
    cache = _cache(tmp_path)
    cache.put("a.py", "x\n", 1)
    cache.put("b.py", "y\n", 2)
    cache.save()
    partial = _cache(tmp_path)
    assert partial.get("a.py", "x\n") == 1
    partial.save()
    assert _cache(tmp_path).get("b.py", "y\n") == 2


def test_corrupt_library_is_an_empty_library_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "c.pkl"
    path.write_bytes(b"not a pickle at all")
    cache = scan_cache.Cache("t", version=1, root=tmp_path, path=path)
    assert cache.get("a.py", "x\n") is None
    cache.put("a.py", "x\n", 1)
    cache.save()
    assert scan_cache.Cache("t", version=1, root=tmp_path, path=path).get("a.py", "x\n") == 1


def test_unwritable_library_degrades_to_no_hits(tmp_path: Path) -> None:
    """写不了就只当没缓存：守卫不能因为缓存目录的状态变红。"""
    path = tmp_path / "is-a-dir"
    path.mkdir()
    cache = scan_cache.Cache("t", version=1, root=tmp_path, path=path)
    cache.put("a.py", "x\n", 1)
    cache.save()  # IsADirectoryError → 吞掉
    assert cache.get("a.py", "x\n") == 1, "内存里那一份仍然可用"
