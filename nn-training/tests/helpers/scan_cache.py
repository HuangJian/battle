"""tests/helpers/scan_cache.py — 「文件内容哈希 → 派生结果」的跨会话磁盘缓存。

## 为什么

全仓只读扫描用例（锁重入守卫、文本断言计数）每遍都要 `ast.parse` 几百个文件。它们本身
没有可删的浪费（`docs/nn/engineering.md` §96/§77），但在门禁 8 worker 抢 CPU 时会撞上
「>5s 警告」那条**给空等用**的口径。源文件在一次 pytest 会话内不会变、跨会话也大多不变
⇒ 把「解析 + 派生」的结果按**内容哈希**缓存；命中时只付读盘 + sha256（~0.1s / 500 文件）。

## 失效（本文件唯一危险的地方，所以只有一条规则）

* 键 = `路径 + sha256(内容)` —— **不是** mtime/size。内容一改键就变，不可能读到旧结果。
* `version`（调用方传）：派生结构 / 口径变了就 +1，整库作废（调用方各自持一个常量，
  与缓存实现解耦 —— 见 `test_lock_reentrancy._INDEX_VERSION` / `text_asserts.CACHE_VERSION`）。
* 库损坏、版本不符、读不到、写不了 ⇒ 一律当作**空库**：缓存是加速器，不是判据，
  绝不让守卫生红。

## 存储

`<系统 temp>/bcity-nn-scan-cache/<scope>-<仓根哈希>.pkl`，一次读写整个 dict（几百条、几 MB）。
写入走 `tmp + os.replace`（门禁 8 worker 并发时不会撕文件；后写者赢，内容等价）。
不放仓库：不被 pytest 的 tmp 清理碰到，也不会被误提交。

**同一个文件改 N 次会留下 N 份旧内容的条目** ⇒ 扫完一遍完整扫描面时 `save(full_scan=True)`：
只留本轮真正读到的键（旧内容的键从此不会再有人问，不清就无限长）。局部扫描必须不传，
否则会误删没走到的条目。

库在**共享的系统 temp 里**、名字可预测 ⇒ 目录 0700 / 文件 0600，且只认自己的 uid：
别人放的 pickle 一律当空库（`pickle.load` 会执行任意代码，即使是测试缓存也不该给它机会）。
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any, Final

#: 缓存库的落点（系统 temp 下，按仓根分库）。
_CACHE_ROOT: Final = Path(tempfile.gettempdir()) / "bcity-nn-scan-cache"

#: `os.getuid` 只在 POSIX 存在（Windows 的 typeshed 没有这个属性，裸引用 mypy 红）；
#: Windows temp 本就按用户隔离，拿不到就不做归属校验。
_GETUID = getattr(os, "getuid", None)


def default_path(scope: str, root: Path) -> Path:
    """该仓库该用途的缓存文件（仓根进哈希 ⇒ 多个 checkout 互不串库）。"""
    tag = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return _CACHE_ROOT / f"{scope}-{tag}.pkl"


class Cache:
    """一个用途一份库。`get` / `put` 都以内容哈希为键，进程内只在 `save` 时落盘一次。"""

    def __init__(self, scope: str, *, version: int, root: Path, path: Path | None = None) -> None:
        self.path = path or default_path(scope, root)
        self.version = version
        self.hits = 0
        self.misses = 0
        self._entries: dict[str, Any] = self._load()
        #: 本轮真正用到的键（命中或写入）—— `save(full_scan=True)` 只留这些。
        self._live: set[str] = set()
        self._dirty = False

    def _load(self) -> dict[str, Any]:
        try:
            if _GETUID is not None and self.path.stat().st_uid != _GETUID():
                return {}  # 别人写的库：不反序列化（pickle = 执行代码）
            with self.path.open("rb") as fh:
                blob = pickle.load(fh)
        except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError, ValueError):
            return {}
        if not isinstance(blob, dict) or blob.get("version") != self.version:
            return {}
        entries = blob.get("entries")
        return entries if isinstance(entries, dict) else {}

    @staticmethod
    def _key(path: str | Path, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{Path(path).as_posix()}\0{digest}"

    def get(self, path: str | Path, text: str) -> Any | None:
        key = self._key(path, text)
        self._live.add(key)
        value = self._entries.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, path: str | Path, text: str, value: Any) -> None:
        key = self._key(path, text)
        self._live.add(key)
        self._entries[key] = value
        self._dirty = True

    def save(self, *, full_scan: bool = False) -> None:
        """落盘（原子替换）。写不了就算了 —— 缓存坏了也不能让守卫失败。

        `full_scan` = 本轮走完了**完整**扫描面 ⇒ 只留本轮读到的键。改过的文件会留下旧内容
        的条目（键含哈希），不清就随编辑次数无限长；局部扫描传了会把没走到的条目误删。
        """
        if full_scan:
            pruned = {k: v for k, v in self._entries.items() if k in self._live}
            if len(pruned) != len(self._entries):
                self._entries = pruned
                self._dirty = True
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            with tmp.open("wb") as fh:
                pickle.dump({"version": self.version, "entries": self._entries}, fh, protocol=5)
            os.chmod(tmp, 0o600)  # POSIX 生效；Windows 只落只读位，无害
            os.replace(tmp, self.path)
        except OSError:
            return
        self._dirty = False
