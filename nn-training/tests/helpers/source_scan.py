"""tests/helpers/source_scan.py — 源码扫描用的**进程内缓存**（读盘 + `ast.parse` 各一次）。

## 为什么

一批「分层 / 单一来源 / 注入点」守卫用例各自 `rglob("*.py")` → `read_text` → `ast.parse`
全仓生产代码（~220 文件）。这些只读检查**同一个 pytest 进程里会重复很多遍**（每个文件被好几个
用例、甚至同一用例的多条判据各读各解析一次），单次解析不贵、重复起来就是秒级墙钟。
2026-09-26 墙钟收敛：把「读盘 + 解析」收成一个 lru_cache。

## 安全性

* 仓库源码在一次 pytest 进程里**不会变**（用例不写生产文件），所以缓存内容与直接从盘读一致。
* xdist 每个 worker 是独立进程 ⇒ 各自一份缓存，不跨进程共享，没有并发写问题（lru_cache 线程安全）。
* **只用于仓库内的只读源码**；用例自己写进 `tmp_path` 又改写的文件不要走这里（会拿到旧内容）。

## 用法

    from tests.helpers import source_scan

    src = source_scan.read_text(str(path))          # 只读字符串
    tree = source_scan.parse(str(path))             # AST（同一路径只解析一次）
    tree = source_scan.parse(str(path), errors="replace")  # 与 read_text 同参数
"""

from __future__ import annotations

import ast
from functools import cache, lru_cache
from pathlib import Path


@cache
def read_text(path: str, errors: str = "strict") -> str:
    """`Path(path).read_text(encoding="utf-8")` 的缓存版（`errors` 同 `Path.read_text`）。"""
    return Path(path).read_text(encoding="utf-8", errors=errors)


@cache
def parse(path: str, errors: str = "strict") -> ast.Module:
    """`ast.parse(read_text(path))` 的缓存版；同一 `(path, errors)` 只解析一次。"""
    return ast.parse(read_text(path, errors), filename=path)
