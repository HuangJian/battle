"""G15：生产面每个 `threading.Thread(...)` 必须带 `name=`（`python.refactor.md` §P1-6）。

## 为什么

线程名是**线程转储里唯一的身份**：`faulthandler.dump_traceback()`、`py-spy`、调试器、
以及 OOM 时人肉看 `top -H` 全都只给名字或 tid。没名字的线程一律显示成 `Thread-7` ——
现场看到「某个线程卡在 `queue.get`」，却分不清它是本机队列的采样线程、某节点的监管线程，
还是日志写者，等于把诊断成本推给了下一次事故。

本仓本来就有这个约定（`jsonl-writer:<文件>`、`hub-queue` 一类），只是没有闸：2026-11-16 量到
**55 处 `Thread(` 里 45 处有名**，剩下 10 处正好分布在最需要现场读数的几处（撤单/派发/监管/
采样/流式收集）。补齐之后本闸**零容忍**，不需要基线 —— 新建线程一律带名字。

## 口径

* AST 扫 `Thread(...)` 调用（`threading.Thread` / `Thread` 两种写法），要求存在 `name=` 关键字。
  位置参数不算：`Thread` 的签名里 `name` 是第四个关键字位，写死位置等于把可读性赌在别人不改签名。
* 扫描面 = 生产包 `biz common hub remote train trainer worker`；`tools/` 与 `worker/scripts/`
  与 G3/G4/G14 同款豁免（一次性脚本）。
* 只判**构造**：`concurrent.futures` 的池子靠 `thread_name_prefix=`，那一半还没搬（见 §P1-6），
  不在本闸口径内。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 生产包（`tools/` 与 `worker/scripts/` 不数 —— 与 G3/G4/G14 同款）。
FACE = ("biz", "common", "hub", "remote", "train", "trainer", "worker")


def _iter_face_py() -> list[Path]:
    out: list[Path] = []
    for pkg in FACE:
        for p in sorted((ROOT / pkg).rglob("*.py")):
            if "scripts" in p.parts:
                continue
            out.append(p)
    return out


def _thread_calls(src: str) -> list[tuple[int, bool]]:
    """`(行号, 是否有 name=)` —— 只认 `Thread(...)` 这两种写法。"""
    out: list[tuple[int, bool]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if called != "Thread":
            continue
        out.append((node.lineno, any(k.arg == "name" for k in node.keywords)))
    return out


def test_every_thread_has_a_name() -> None:
    offenders: list[str] = []
    total = 0
    for p in _iter_face_py():
        rel = p.relative_to(ROOT).as_posix()
        for line, has_name in _thread_calls(p.read_text(encoding="utf-8", errors="replace")):
            total += 1
            if not has_name:
                offenders.append(f"  {rel}:{line}")
    assert total >= 40, f"只扫到 {total} 处 `Thread(`，扫描入口可能坏了"
    assert not offenders, (
        "这些线程没有名字（线程转储里只显示 `Thread-N`，出事时认不出是谁）：\n"
        + "\n".join(offenders)
        + '\n  加上 `name="<谁>-<哪个>"`（如 `f"batch-worker:{node}"`）。'
    )


def test_scan_counts_both_spellings_and_ignores_other_calls() -> None:
    """口径自检：`threading.Thread` 与 `Thread` 都算，`Executor`/同名变量不算。"""
    src = (
        "import threading\n"
        "threading.Thread(target=f, name='a')\n"
        "Thread(target=g)\n"
        "Pool(target=h, name='c')\n"
        "t = threading.Thread(target=i, args=(1,), daemon=True)\n"
    )
    assert _thread_calls(src) == [(2, True), (3, False), (5, False)]
